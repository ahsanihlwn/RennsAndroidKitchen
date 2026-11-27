# SPDX-License-Identifier: MPL-2.0
"""Resolve public GKI release manifests and sync their pinned Repo checkouts."""
from collections import deque
from contextlib import nullcontext
from dataclasses import dataclass
import hashlib
from html import unescape
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
from urllib.error import HTTPError, URLError
from urllib.request import urlopen
from xml.etree import ElementTree

from core import kernel_version, kitchen_process, wsl_manager
from core.app_paths import CACHE_DIR
from core.command_context import OperationError


_CATALOG = "https://source.android.com/docs/core/architecture/kernel"
_CI = "https://ci.android.com/builds/submitted"
_SHA1 = re.compile(r"[0-9a-f]{40}")
_PATH = re.compile(r"[a-zA-Z0-9_.+-]+(?:/[a-zA-Z0-9_.+-]+)*")
_CLANG_PROJECT = "platform/prebuilts/clang/host/linux-x86"
_CLANG_PATHS = ("prebuilts/clang/host/linux-x86", "prebuilts-master/clang/host/linux-x86")
_RELEASE_TAG = re.compile(r"[a-zA-Z0-9.-]+(?:_[a-zA-Z0-9.-]+)*")
_MAX_DOCUMENT = 1024 * 1024
_MAX_SYSTEM_MAP = 32 * 1024 * 1024
GKI_CACHE_INDEX = Path(CACHE_DIR) / "gki"


@dataclass(frozen=True)
class GkiRelease:
    build_id: str
    tag: str
    commit: str
    manifest_url: str
    manifest: bytes
    project_count: int
    branch: str = ""
    kernel_version: str = ""
    clang_path: str = _CLANG_PATHS[0]

    @property
    def display_name(self):
        return self.tag or self.branch


def _manifest_url(build_id):
    return _artifact_url(build_id, f"manifest_{build_id}.xml")


def _artifact_url(build_id, name):
    return f"{_CI}/{build_id}/kernel_aarch64/latest/raw/{name}"


def _remember_release(release):
    if not (release.branch and release.kernel_version):
        return
    GKI_CACHE_INDEX.mkdir(parents=True, exist_ok=True)
    destination = GKI_CACHE_INDEX / f"{release.build_id}-{release.commit}.json"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=destination.parent, delete=False) as stream:
        temporary = Path(stream.name)
        json.dump({
            "branch": release.branch,
            "version": release.kernel_version,
            "tag": release.tag,
            "manifest": release.manifest.decode("utf-8"),
        }, stream)
    try:
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def _remembered_release(info):
    for entry in sorted(GKI_CACHE_INDEX.glob(f"{info.android_build_id}-{info.git_commit}*.json")):
        commit = entry.stem.partition("-")[2]
        if not _SHA1.fullmatch(commit):
            continue
        try:
            cached = json.loads(entry.read_text(encoding="utf-8"))
            if cached["branch"] != info.aosp_branch or cached["version"] != info.base_version:
                continue
            manifest = cached["manifest"].encode("utf-8")
            tag = cached["tag"]
            if not isinstance(tag, str) or (tag and not _RELEASE_TAG.fullmatch(tag)):
                continue
            _, count, clang = _validate_manifest(manifest, commit)
        except (AttributeError, KeyError, OSError, TypeError, ValueError, OperationError):
            continue
        return GkiRelease(info.android_build_id, tag, commit, _manifest_url(info.android_build_id),
                          manifest, count, info.aosp_branch, info.base_version, clang)
    return None


def _download(url, max_bytes=_MAX_DOCUMENT):
    try:
        with urlopen(url, timeout=20) as response:
            data = response.read(max_bytes + 1)
    except HTTPError as error:
        if error.code == 404:
            return None
        raise OperationError(f"Could not read AOSP release data from {url}: {error}") from error
    except (URLError, OSError) as error:
        raise OperationError(f"Could not read AOSP release data from {url}: {error}") from error
    if len(data) > max_bytes:
        raise OperationError(f"AOSP release document is too large: {url}")
    return data


def _catalog(document):
    try:
        content = document.decode("utf-8").lstrip()
        if content.startswith("{"):
            return json.loads(content)
        for code in re.findall(r"<pre\b[^>]*>\s*<code\b[^>]*>(.*?)</code>", content, re.S | re.I):
            candidate = unescape(code).strip()
            if candidate.startswith("{"):
                return json.loads(candidate)
    except (ValueError, UnicodeError) as error:
        raise OperationError("Could not read the AOSP GKI release catalog.") from error
    raise OperationError("The AOSP GKI release catalog did not contain its JSON release data.")


def _relative_path(value):
    return bool(_PATH.fullmatch(value)) and not {".", "..", ".repo"}.intersection(value.split("/"))


def _validate_manifest(document, commit, *, abbreviated=False):
    try:
        content = document.decode("utf-8")
        if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", content, re.I):
            raise ValueError("XML declarations may not define entities")
        manifest = ElementTree.fromstring(content)
        if manifest.tag != "manifest" or any(
            row.tag not in {"default", "remote", "superproject", "project", "notice", "contactinfo"}
            for row in manifest
        ):
            raise ValueError("unexpected manifest elements")
        remotes = {row.get("name"): row.get("fetch") for row in manifest.findall("remote")}
        default = manifest.find("default")
        default_remote = default.get("remote") if default is not None else None
        if not remotes or any(
            fetch not in {"https://android.googlesource.com/", "https://android.googlesource.com"}
            for fetch in remotes.values()
        ):
            raise ValueError("unexpected Git remote")
        projects = {}
        for project in manifest.findall("project"):
            name = project.get("name", "")
            path = project.get("path", name)
            revision = project.get("revision", "")
            if (not _relative_path(name) or not _relative_path(path) or path in projects
                    or project.get("remote", default_remote) not in remotes
                    or not _SHA1.fullmatch(revision)):
                raise ValueError(f"unverified project path or revision for {name}")
            for row in project:
                if row.tag == "annotation":
                    continue
                if (row.tag not in {"linkfile", "copyfile"} or not _relative_path(row.get("src", ""))
                        or not _relative_path(row.get("dest", "")) or len(row)):
                    raise ValueError(f"unexpected checkout instruction for {name}")
            projects[path] = (name, revision)
        origin, pinned_commit = projects.get("common", ("", ""))
        if origin != "kernel/common":
            raise ValueError("the manifest does not specify kernel/common")
        clang = next((path for path in _CLANG_PATHS if projects.get(path, ("", ""))[0] == _CLANG_PROJECT), "")
        if not clang:
            raise ValueError("the manifest does not specify the Linux x86-64 Clang prebuilt")
        if abbreviated and not pinned_commit.startswith(commit):
            return None
        if not abbreviated and pinned_commit != commit:
            raise ValueError("kernel/common differs from the GKI release commit")
    except (ValueError, UnicodeError, ElementTree.ParseError) as error:
        raise OperationError(f"Could not verify the pinned GKI manifest: {error}") from error
    return pinned_commit, len(projects), clang


def resolve_release(info):
    """Return a validated public arm64 release, or a reason to use vendor inputs."""
    if not (info.arch == "arm64" and re.fullmatch(r"android\d{2}-\d+\.\d+", info.aosp_branch)
            and re.fullmatch(r"[0-9]{1,12}", info.android_build_id)
            and re.fullmatch(r"[a-f0-9]{7,40}", info.git_commit)):
        return None, "The kernel banner does not identify an Android arm64 GKI release."

    cached = _remembered_release(info)
    if cached:
        return cached, ""
    branch = info.aosp_branch.replace(".", "_")
    document = _download(f"{_CATALOG}/gki-{branch}-release-builds.json?hl=en")
    catalog = _catalog(document) if document is not None else {"name": info.aosp_branch, "branches": []}
    if not isinstance(catalog, dict) or catalog.get("name") != info.aosp_branch or not isinstance(catalog.get("branches"), list):
        raise OperationError("The AOSP GKI release catalog has an unexpected branch or format.")

    for series in catalog["branches"]:
        if not isinstance(series, dict) or series.get("kernel_version") != info.base_version:
            continue
        for release in series.get("releases") or ():
            if not isinstance(release, dict) or str(release.get("kernel_bid")) != info.android_build_id:
                continue
            commit, tag = release.get("sha1"), release.get("tag")
            if (not isinstance(commit, str) or not _SHA1.fullmatch(commit) or not commit.startswith(info.git_commit)
                    or not isinstance(tag, str) or not _RELEASE_TAG.fullmatch(tag)):
                continue
            build_id = info.android_build_id
            url = _manifest_url(build_id)
            manifest = _download(url)
            if manifest is None:
                return None, f"The public CI manifest for GKI build {build_id} is unavailable."
            _, count, clang = _validate_manifest(manifest, commit)
            return GkiRelease(build_id, tag, commit, url, manifest, count, info.aosp_branch, info.base_version, clang), ""

    # Older releases can be removed from the catalog while their pinned CI artifacts remain public.
    build_id = info.android_build_id
    url = _manifest_url(build_id)
    manifest = _download(url)
    verified = _validate_manifest(manifest, info.git_commit, abbreviated=True) if manifest is not None else None
    if verified is None:
        return None, "The kernel build ID and commit did not match a published AOSP GKI build."
    commit, count, clang = verified
    return GkiRelease(build_id, "", commit, url, manifest, count, info.aosp_branch, info.base_version, clang), ""


def inspect_release(path):
    info = kernel_version.inspect_kernel(path)
    if not info.is_kernel:
        raise OperationError(info.error or "No kernel detected. Select boot.img, Image or vmlinux.")
    release, reason = resolve_release(info)
    return info, release, reason


def analysis_system_map(info, image_digest, image_size, *, offline=False):
    """Reuse a CI System.map only after matching the extracted Image byte for byte."""
    if not (re.fullmatch(r"[0-9]{1,12}", info.android_build_id)
            and re.fullmatch(r"[a-f0-9]{7,40}", info.git_commit)
            and re.fullmatch(r"[a-f0-9]{64}", image_digest)):
        return None
    directory = GKI_CACHE_INDEX / "symbols" / f"{info.android_build_id}-{image_digest}"
    path = directory / "System.map"
    if path.is_file() or offline:
        return path if path.is_file() else None
    release, _ = resolve_release(info)
    if release is None:
        return None
    url = _artifact_url(release.build_id, "Image")
    try:
        with urlopen(url, timeout=20) as image:
            digest = hashlib.sha256()
            remaining = image_size
            while remaining:
                chunk = image.read(min(remaining, 1024 * 1024))
                if not chunk:
                    return None
                digest.update(chunk)
                remaining -= len(chunk)
            if digest.hexdigest() != image_digest or image.read(1):
                return None
    except HTTPError as error:
        if error.code == 404:
            return None
        raise OperationError(f"Could not verify AOSP Image from {url}: {error}") from error
    except OSError as error:
        raise OperationError(f"Could not verify AOSP Image from {url}: {error}") from error
    symbols = _download(_artifact_url(release.build_id, "System.map"), _MAX_SYSTEM_MAP)
    if not symbols:
        return None
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=directory, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(symbols)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    _remember_release(release)
    return path


def vendor_requirements(info, reason):
    embedded = (
        "The detected IKCONFIG can be exported as kernel.config with Decompile."
        if info.config_sha256 else "Get the original .config or vendor defconfig and build fragments."
    )
    return "\n\n".join((
        f"{info.version_string or info.arch or 'Kernel'}\n{reason}",
        "Prepare these from the same vendor build:\n"
        "• Full kernel source checkout at the matching commit, including vendor patches and modules.\n"
        "• Original build recipe and toolchain. RAK's C compile step needs a Clang compilation database.\n"
        "• Kbuild output with .config, include/generated/autoconf.h and compile_commands.json.\n"
        "• Optional System.map, vmlinux or BTF for more accurate reconstruction.\n"
        "• C2Rust in the build environment if you want Rust source export.",
        embedded,
    ))


# Shared by sync and cache management so deletion and fetching use the same locks.
_CACHE_WORKER = r"""
from contextlib import contextmanager, ExitStack
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from xml.etree import ElementTree

cache = Path.home() / ".cache" / "renn-android-kitchen" / "gki"

def checkout_path(key):
    root = cache / key
    if root.is_symlink() or (root.exists() and root.resolve().parent != cache.resolve()):
        raise ValueError("The checkout must be a directory inside the GKI cache.")
    return root

@contextmanager
def lock_checkout(root, create=False, wait_message=""):
    with ExitStack() as resources:
        paths = [cache / (".lock-" + root.name), root / ".rak-sync.lock"]
        for path in paths:
            if create and path.parent == root:
                root.mkdir(exist_ok=True)
            fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            stream = resources.enter_context(os.fdopen(fd, "a+"))
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                if not wait_message:
                    raise
                print(wait_message, flush=True)
                fcntl.flock(stream, fcntl.LOCK_EX)
        yield

def incomplete_project(root, manifest):
    clang = None
    for project in ElementTree.fromstring(manifest).findall("project"):
        path = project.get("path", project.get("name"))
        if not path or any(part in ("", ".", "..", ".repo") for part in path.split("/")):
            return "invalid manifest path"
        location = root / path
        if project.get("name") == "platform/prebuilts/clang/host/linux-x86":
            clang = location
        gitdir = root / ".repo" / "projects" / (path + ".git")
        if (not location.resolve().is_relative_to(root.resolve()) or not (location / ".git").exists()
                or not (gitdir / "index").is_file()):
            return path
        git = ["git", "-C", str(location), "rev-parse", "HEAD"]
        result = subprocess.run(git, capture_output=True, text=True)
        if result.returncode or result.stdout.strip() != project.get("revision"):
            return path
    if clang is None or not any(path.is_file() and os.access(path, os.X_OK) for path in clang.glob("*/bin/clang")):
        return str(clang.relative_to(root)) if clang else "Clang prebuilt"
    return ""
"""


# Runs inside Linux so Repo uses native symlinks and executable permissions even on Windows.
_SYNC = _CACHE_WORKER + r"""
import platform
import shlex
import tempfile

def fail(message):
    sys.exit(message)

def run(*args, input_data=None):
    print(shlex.join(args), flush=True)
    inputs = {"stdin": subprocess.DEVNULL} if input_data is None else {"input": input_data}
    # The checkout root is private. Keep Git's usual file modes for Kbuild archives.
    subprocess.run(args, cwd=root, check=True, env=env, umask=0o022, **inputs)

def sync_output(args):
    print(shlex.join(args), flush=True)
    lines = []
    with subprocess.Popen(args, cwd=root, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True, errors="replace", umask=0o022) as process:
        for line in process.stdout:
            print(line, end="", flush=True)
            if any(token in line for token in ("couldn't find remote ref", "Cannot fetch ", "Cannot initialize work tree for ")):
                lines.append(line)
        return process.wait(), "".join(lines)

def sync_projects():
    base = (repo, "sync", "--no-manifest-update", "--no-use-superproject", "--no-tags",
            "--optimized-fetch", "--no-interleaved", "--no-auto-gc", "-c", "-j4")
    projects = {project.get("name"): project for project in ElementTree.fromstring(manifest).findall("project")}
    project_dirs = root / ".repo" / "projects"
    object_dirs = root / ".repo" / "project-objects"
    for name, project in projects.items():
        path = project.get("path", name)
        objects = object_dirs / (name + ".git")
        if objects.is_dir() and not (objects / "HEAD").is_file():
            if objects.is_symlink() or not objects.resolve().is_relative_to(object_dirs.resolve()):
                fail("Could not safely repair this interrupted Repo object store: " + name)
            run("git", "--git-dir=" + str(objects), "init", "--bare", "--quiet")
        gitdir = project_dirs / (path + ".git")
        if not gitdir.is_dir() or (gitdir / "HEAD").is_file():
            continue
        location = root / path
        if (gitdir.is_symlink() or not gitdir.resolve().is_relative_to(project_dirs.resolve())
                or location.is_symlink() or (location.exists() and any(entry.name != ".git" for entry in location.iterdir()))):
            fail("Could not safely repair this interrupted Repo metadata: " + path)
        # Only incomplete worktree metadata moves. Repo keeps the fetched objects in project-objects.
        preserved = Path(tempfile.mkdtemp(prefix=".rak-interrupted-gitdir-", dir=root))
        gitdir.rename(preserved / gitdir.name)
        print("Preserved incomplete Repo metadata for " + path + " at " + str(preserved), flush=True)
    network = (*base, "--network-only", "--retry-fetches=2")
    code, output = sync_output(network)
    if code:
        missing_refs = set(re.findall(r"couldn't find remote ref ([^\s]+)", output))
        failures = set(re.findall(r"Cannot fetch ([a-zA-Z0-9_.+/\-]+) from https://android.googlesource.com/", output))
        recovered = False
        for name in sorted(failures & projects.keys()):
            project = projects[name]
            upstream = project.get("upstream", "")
            branch = upstream if upstream.startswith("refs/") else "refs/heads/" + upstream
            if not upstream or not {upstream, branch} & missing_refs:
                continue
            gitdir = project_dirs / (project.get("path", name) + ".git")
            revision = project.get("revision")
            print(f"Upstream {upstream} is no longer available. Fetching pinned commit {revision} for {name}.", flush=True)
            run("git", "--git-dir=" + str(gitdir), "-c", "http.version=HTTP/1.1",
                "fetch", "--depth=1", "--filter=blob:none", "--no-tags", "--no-recurse-submodules",
                "https://android.googlesource.com/" + name, revision)
            recovered = True
        if not recovered:
            fail("Repo network sync failed. See Git and Repo output above.")
        run(*network)

    def hydrate(project):
        path = project.get("path", project.get("name"))
        revision = project.get("revision")
        gitdir = root / ".repo" / "projects" / (path + ".git")
        if not gitdir.is_dir() or not re.fullmatch("[a-f0-9]{40}", revision or ""):
            fail("An incomplete project could not be matched to its pinned Git revision.")
        git = ["git", "--git-dir=" + str(gitdir)]
        remotes = subprocess.check_output([*git, "remote"], cwd=root, text=True).split()
        if len(remotes) != 1:
            fail("The incomplete project does not have a unique Git remote: " + path)
        print("Fetching missing objects for the pinned commit in " + path, flush=True)
        read_env = dict(env, GIT_NO_LAZY_FETCH="1")
        missing = subprocess.check_output(
            [*git, "rev-list", "--objects", "--no-object-names", "--missing=print", "--max-count=1", revision],
            cwd=root, env=read_env, text=True,
        )
        objects = [line[1:] for line in missing.splitlines() if re.fullmatch(r"\?[a-f0-9]{40}", line)]
        http = ("-c", "http.version=HTTP/1.1", "-c", "http.lowSpeedLimit=1024", "-c", "http.lowSpeedTime=120")
        if len(objects) > 1000:
            run(*git, *http, "fetch", "--progress", "--no-filter", "--refetch", "--depth=1",
                "--no-tags", "--no-write-fetch-head", "--no-recurse-submodules", remotes[0], revision)
            return
        for start in range(0, len(objects), 48):
            batch = objects[start:start+48]
            print(f"{path}: fetching objects {start + 1}–{start + len(batch)} of {len(objects)}", flush=True)
            run(*git, *http,
                "fetch", "--filter=blob:none", "--no-tags", "--no-write-fetch-head", "--no-recurse-submodules",
                remotes[0], "--stdin", input_data=("\n".join(batch) + "\n").encode("ascii"))

    sync = (*base, "--local-only", "--fail-fast")
    recovered = set()
    while True:
        code, output = sync_output(sync)
        if code == 0:
            break
        failures = re.findall(r"Cannot initialize work tree for ([a-zA-Z0-9_.+/\-]+)", output)
        pending = next((name for name in failures if name in projects and name not in recovered), "")
        if not pending:
            fail("Repo sync failed. See Git and Repo output above.")
        hydrate(projects[pending])
        recovered.add(pending)
    for name, project in projects.items():
        path = project.get("path", name)
        location = root / path
        gitdir = root / ".repo" / "projects" / (path + ".git")
        if (gitdir / "index").is_file() or not (location / ".git").exists():
            continue
        git = ["git", "-C", str(location)]
        revision = subprocess.check_output([*git, "rev-parse", "HEAD"], text=True).strip()
        if revision != project.get("revision"):
            fail("Could not safely finish this interrupted checkout: " + path)
        if any(entry.name != ".git" for entry in location.iterdir()):
            with tempfile.TemporaryDirectory(prefix=".rak-verify-worktree-", dir=root) as temporary:
                check_env = dict(env, GIT_INDEX_FILE=str(Path(temporary) / "index"))
                subprocess.run([*git, "read-tree", revision], check=True, env=check_env)
                subprocess.run([*git, "update-index", "-q", "--refresh"], check=True, env=check_env)
                changes = subprocess.check_output([*git, "diff-files", "--name-status", "-z"], env=check_env)
                others = subprocess.check_output([*git, "ls-files", "--others", "-z"], env=check_env)
            if others or any(status != b"D" for status in changes.split(b"\0")[0:-1:2]):
                fail("This interrupted checkout contains local files or edits: " + path)
        hydrate(project)
        run(*git, "read-tree", "--reset", "-u", revision)

build_id, commit, digest = sys.argv[1:]
repo = shutil.which("repo")
if platform.system() != "Linux" or platform.machine() != "x86_64":
    fail("The published GKI host toolchain requires Linux x86-64 (native or WSL).")
missing = [tool for tool in ("git", "repo") if not shutil.which(tool)]
if missing:
    fail("Install these in the selected Linux or WSL environment and retry: " + ", ".join(missing))
env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
manifest = sys.stdin.buffer.read()
if hashlib.sha256(manifest).hexdigest() != digest:
    fail("The received GKI manifest did not match the verified download.")
root = checkout_path(build_id + "-" + commit)
os.umask(0o077)
cache.mkdir(parents=True, exist_ok=True)
with lock_checkout(root, create=True):
    selected = root / ".rak-manifest.xml"
    if selected.exists() and selected.read_bytes() != manifest:
        fail("This checkout already contains a different verified manifest.")
    if (root / ".repo/local_manifests").exists() or (root / ".repo/local_manifest.xml").exists():
        fail("This GKI cache contains local Repo manifests. Use an unmodified RAK checkout cache.")
    if not selected.exists():
        with tempfile.NamedTemporaryFile(dir=root, delete=False) as staging:
            staging.write(manifest)
        Path(staging.name).replace(selected)
    installed = root / ".repo/manifests/default.xml"
    if not installed.is_file():
        run(repo, "init", "--standalone-manifest", "-u", selected.as_uri(), "--groups=all",
            "--partial-clone", "--no-clone-bundle", "--no-use-superproject")
    if not installed.is_file() or installed.read_bytes() != manifest:
        fail("Repo did not load the verified GKI manifest.")
    if incomplete_project(root, manifest):
        sync_projects()
        missing = incomplete_project(root, manifest)
        if missing:
            fail("The synced project or toolchain does not match the manifest: " + missing)
    print("RAK_GKI_CHECKOUT=" + str(root), flush=True)
"""


_CACHE_STATUS = _CACHE_WORKER + r"""
action, build_id, prefix, digest = sys.argv[1:]
matches = [
    path for path in cache.glob(build_id + "-" + prefix + "*")
    if re.fullmatch(r"[0-9]{1,12}-[a-f0-9]{40}", path.name) and path.is_dir() and not path.is_symlink()
]
if len(matches) > 1:
    sys.exit("This abbreviated commit matches multiple GKI cache entries.")
result = {}
if matches:
    root = checkout_path(matches[0].name)
    selected = root / ".rak-manifest.xml"
    if selected.is_file() and not selected.is_symlink():
        result = {"path": str(root), "commit": root.name.partition("-")[2],
                  "distro": os.environ.get("WSL_DISTRO_NAME", ""), "state": "incomplete"}
        try:
            with lock_checkout(root):
                if action == "remove":
                    shutil.rmtree(root)
                    result["state"] = "removed"
                elif (installed := root / ".repo/manifests/default.xml").is_file():
                    manifest = selected.read_bytes()
                    pinned = not digest or hashlib.sha256(manifest).hexdigest() == digest
                    if pinned and installed.read_bytes() == manifest and not incomplete_project(root, manifest):
                        result["state"] = "ready"
                elif (root / ".rak-source-files").is_dir():
                    result["state"] = "source"
        except BlockingIOError:
            result["state"] = "syncing"
print(json.dumps(result))
"""


def _linux_command(distro):
    if sys.platform == "win32":
        wsl = wsl_manager._wsl_path()
        if not wsl:
            raise OperationError("Install WSL with a Linux distribution, Python 3, Git and Repo to fetch this GKI build.")
        command = [wsl, *(["--distribution", distro] if distro else []), "--exec"]
    elif sys.platform.startswith("linux"):
        command = []
    else:
        raise OperationError("Fetching GKI build inputs requires Linux or Windows with WSL.")
    return [*command, "python3", "-u", "-c"]


_WORKER_STDIN = "import sys; exec(sys.stdin.buffer.readline())"


def _worker_input(script):
    return f"exec({ascii(script)})\n"


def cache_checkout(build_id, commit, *, release=None, distro=None, remove=False):
    if not re.fullmatch(r"[0-9]{1,12}", build_id) or not re.fullmatch(r"[a-f0-9]{7,40}", commit):
        return {}
    if remove and not _SHA1.fullmatch(commit):
        raise OperationError("Removal requires the full GKI cache commit.")
    try:
        command = _linux_command(distro)
        digest = hashlib.sha256(release.manifest).hexdigest() if release else ""
        with kitchen_process.background_probe() if not remove else nullcontext():
            result = kitchen_process.run(
                [*command, _WORKER_STDIN, "remove" if remove else "status", build_id, commit, digest],
                input=_worker_input(_CACHE_STATUS),
                capture_output=True, encoding="utf-8", errors="replace",
                timeout=90 if not remove else None, creationflags=wsl_manager._CREATE_NO_WINDOW,
            )
    except (OSError, OperationError, kitchen_process.TimeoutExpired):
        if remove:
            raise
        return {}
    if result.returncode:
        if remove:
            raise OperationError(f"Could not remove the GKI checkout.\n{result.stdout}\n{result.stderr}")
        return {}
    try:
        entry = json.loads(result.stdout)
    except ValueError as error:
        if remove:
            raise OperationError(f"Could not verify the cache removal.\n{result.stdout}\n{result.stderr}") from error
        return {}
    if remove and entry.get("state") != "removed":
        raise OperationError("The checkout is currently syncing or is no longer in the cache.")
    return entry


def sync_release(release, *, distro=None):
    _remember_release(release)
    command = _linux_command(distro)
    command += [_WORKER_STDIN, release.build_id, release.commit, hashlib.sha256(release.manifest).hexdigest()]
    process = kitchen_process.run(command, input=_worker_input(_SYNC).encode("ascii") + release.manifest, capture_output=True,
                                  creationflags=wsl_manager._CREATE_NO_WINDOW)
    stdout, stderr = (stream.decode("utf-8", errors="replace") for stream in (process.stdout, process.stderr))
    location = next((line.partition("=")[2] for line in reversed(stdout.splitlines()) if line.startswith("RAK_GKI_CHECKOUT=")), "")
    if process.returncode or not location.startswith("/"):
        raise OperationError(f"GKI Repo sync failed (exit {process.returncode}).\n{stdout}\n{stderr}")
    return location


_FETCH_SOURCES = _CACHE_WORKER + r"""
import shlex
import tempfile
import time

build_id, revision = sys.argv[1:]
request = json.load(sys.stdin)
manifest = request["manifest"].encode()
paths = {path for path in request["paths"] if re.fullmatch(r"[\w.+/-]+", path, re.ASCII)
         and all(part not in ("", ".", "..", ".git") for part in path.split("/"))}
root = checkout_path(build_id + "-" + revision)
env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_NO_LAZY_FETCH="1", LC_ALL="C")
remote = "https://android.googlesource.com/kernel/common"
os.umask(0o077)
cache.mkdir(parents=True, exist_ok=True)

def run(command, *, data=None, check=True):
    return subprocess.run(command, input=data, capture_output=True, check=check, env=env)

def fetch(git, args, *, data=None):
    command = [*git, "-c", "http.version=HTTP/1.1", "-c", "http.lowSpeedLimit=1024",
               "-c", "http.lowSpeedTime=30", "fetch", "--progress", "--no-tags", *args]
    transient = re.compile(
        r"timed? out|timeout was reached|connection (?:reset|refused|aborted|closed)|could not resolve host|"
        r"couldn't connect|failed to connect|early EOF|curl (?:6|7|18|28|35|52|55|56)\b|"
        r"error: (?:429|50[234])\b|hung up unexpectedly", re.I,
    )
    for attempt in range(1, 4):
        print(shlex.join(command), flush=True)
        with subprocess.Popen(command, env=env, stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf-8", errors="replace") as process:
            if data is not None:
                try:
                    process.stdin.write(data.decode("ascii"))
                    process.stdin.close()
                except BrokenPipeError:
                    pass
            output = []
            for line in process.stdout:
                line = line.rstrip()
                output.append(line)
                print(line, flush=True)
            if process.wait() == 0:
                return
        if attempt == 3 or not transient.search("\n".join(output)):
            sys.exit("Git could not fetch the verified kernel source.")
        print(f"RAK_SOURCE_PROGRESS=Connection interrupted. Retrying Git ({attempt + 1}/3)...", flush=True)
        time.sleep(attempt * 2)

def has_commit(git):
    return run([*git, "cat-file", "-e", revision + "^{commit}"], check=False).returncode == 0

try:
    with lock_checkout(root, create=True,
                       wait_message="RAK_SOURCE_PROGRESS=Waiting for another task to finish using this GKI cache..."):
        selected = root / ".rak-manifest.xml"
        if selected.exists() and selected.read_bytes() != manifest:
            sys.exit("This cached build contains a different manifest.")
        if not selected.exists():
            with tempfile.NamedTemporaryFile(dir=root, delete=False) as staging:
                staging.write(manifest)
            Path(staging.name).replace(selected)
        repository = root / ".repo/projects/common.git"
        git = ["git", "--git-dir=" + str(repository)]
        if not (repository / "HEAD").is_file() or not has_commit(git):
            repository = root / ".rak-source.git"
            if repository.is_symlink():
                sys.exit("The kernel source cache must be a local directory.")
            git = ["git", "--git-dir=" + str(repository)]
            if not (repository / "HEAD").is_file():
                run([*git, "init", "--bare", "--quiet"])
            run([*git, "config", "remote.origin.url", remote])
            run([*git, "config", "remote.origin.promisor", "true"])
            run([*git, "config", "remote.origin.partialclonefilter", "blob:none"])
            if not has_commit(git):
                print("RAK_SOURCE_PROGRESS=Fetching the pinned kernel commit...", flush=True)
                fetch(git, ["--depth=1", "--filter=blob:none", remote, revision])
                run([*git, "update-ref", "refs/rak/source", revision])
        source = root / ".rak-source-files"
        if source.is_symlink():
            sys.exit("The source export cache must be a local directory.")
        source.mkdir(exist_ok=True)
        tree = run([*git, "ls-tree", "-rz", revision]).stdout.split(b"\0")
        rows = []
        for item in filter(None, tree):
            header, path_bytes = item.split(b"\t", 1)
            mode, kind, oid = header.split()
            path = os.fsdecode(path_bytes)
            if path not in paths or kind != b"blob" or mode not in (b"100644", b"100755"):
                continue
            target = source / path
            if target.resolve() != target or not target.is_relative_to(source):
                sys.exit("Unsafe path in the source export cache: " + path)
            rows.append((oid, target))
        for start in range(0, len(rows), 128):
            batch = rows[start:start + 128]
            print(f"RAK_SOURCE_PROGRESS=Loading kernel source files {start + len(batch)}/{len(rows)}...", flush=True)
            oids = b"\n".join(oid for oid, _ in batch) + b"\n"
            metadata = run([*git, "cat-file", "--batch-check"], data=oids).stdout.splitlines()
            missing = [line.split()[0] for line in metadata if line.endswith(b" missing")]
            if missing:
                fetch(git, ["--filter=blob:none", "--no-write-fetch-head", remote, "--stdin"],
                      data=b"\n".join(dict.fromkeys(missing)) + b"\n")
            result = run([*git, "cat-file", "--batch"], data=oids).stdout
            cursor = 0
            for oid, target in batch:
                end = result.index(b"\n", cursor)
                blob, kind, size = result[cursor:end].split()
                if blob != oid or kind != b"blob":
                    sys.exit("Git returned an unexpected source object.")
                cursor = end + 1 + int(size)
                content = result[end + 1:cursor]
                if not target.is_file() or target.read_bytes() != content:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as staging:
                        staging.write(content)
                    Path(staging.name).replace(target)
                cursor += 1
        print("RAK_SOURCE_ROOT=" + json.dumps({"path": str(source), "distro": os.environ.get("WSL_DISTRO_NAME", "")}), flush=True)
except BlockingIOError:
    sys.exit("This GKI cache is in use. Retry when its download is finished.")
except subprocess.CalledProcessError as error:
    sys.exit(shlex.join(error.cmd) + "\n" + (error.stdout or b"").decode(errors="replace")
             + (error.stderr or b"").decode(errors="replace"))
"""


def require_linux_tools(distro=None, *tools):
    if sys.platform == "win32":
        distros = wsl_manager.list_distros()
        if not distros or (distro and distro not in distros):
            raise OperationError(
                "Activate WSL and install a Linux distribution such as Ubuntu, then retry. "
                "Open PowerShell as administrator and run wsl --install -d Ubuntu. Restart Windows if requested."
            )
        prefix = _linux_command(distro)[:-3]
        with kitchen_process.background_probe():
            for tool in tools:
                try:
                    probe = kitchen_process.run([*prefix, "/bin/sh", "-c", 'command -v "$1"', "sh", tool],
                                                capture_output=True, timeout=20, creationflags=wsl_manager._CREATE_NO_WINDOW)
                except (OSError, kitchen_process.TimeoutExpired) as error:
                    raise OperationError(f"WSL did not respond while checking {tool}: {error}") from error
                if probe.returncode:
                    raise OperationError(f"Install {tool} in your WSL Linux distribution, then retry.")
    else:
        missing = [tool for tool in tools if not shutil.which(tool)]
        if missing:
            raise OperationError("Install these Linux tools and retry: " + ", ".join(missing))


def fetch_source_files(release, paths, *, distro=None, progress=None):
    """Cache only the Git blobs used as references by this reconstruction."""
    request = json.dumps({"manifest": release.manifest.decode(), "paths": paths})
    command = [*_linux_command(distro), _WORKER_STDIN, release.build_id, release.commit]
    log = deque(maxlen=40)
    location = None
    with kitchen_process.Popen(command, stdin=kitchen_process.PIPE, stdout=kitchen_process.PIPE,
                               stderr=kitchen_process.STDOUT, text=True, encoding="utf-8", errors="replace",
                               creationflags=wsl_manager._CREATE_NO_WINDOW) as process:
        process.stdin.write(_worker_input(_FETCH_SOURCES))
        process.stdin.write(request)
        process.stdin.close()
        for line in process.stdout:
            log.append(line)
            if line.startswith("RAK_SOURCE_ROOT="):
                location = json.loads(line.partition("=")[2])
            elif progress and (line.startswith("RAK_SOURCE_PROGRESS=") or "Receiving objects:" in line):
                progress(line.partition("=")[2].strip() if line.startswith("RAK_SOURCE_PROGRESS=") else line.strip())
        if process.wait() or not location:
            raise OperationError("Could not fetch the matching kernel source. See Kitchen Terminal.\n" + "".join(log))
    if sys.platform == "win32":
        return f"//wsl.localhost/{location['distro']}{location['path']}"
    return location["path"]
