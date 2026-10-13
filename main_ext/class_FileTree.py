# SPDX-License-Identifier: MPL-2.0
import os,shutil,stat
from core.header_magic import is_fs_image as detect_fs_type
import json
from core.fsdetect import detect_fs_root
import subprocess
from core.header_magic import is_super_or_sparse as detect_super_sparse
from core.fs_verify import FSVerifier
import sys
import weakref
from shiboken6 import isValid
from PySide6.QtWidgets import (
    QTreeWidget,
    QApplication,
    QTreeWidgetItem,
    QHeaderView,
    QMenu,
    QMessageBox
)
from main_ext.class_AppDialog import AppDialog
from main_ext.class_ThemeUi import theme as _th
from core.symlink import (
    resolve_symlink,
    batch_resolve_symlinks,
    is_symlink_placeholder,
    is_candidate,
    clear_symlink_cache,
    invalidate_symlink_cache,
    _symlink_cache,
)

from PySide6.QtCore import QTimer, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import (
    QIcon,
    QPainter,
    QColor,
    QAction,
    QPixmap,
    QTransform,
    QPen,
    QDrag,
)
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QCheckBox
)

from PySide6.QtCore import (
    Qt,
    QFileSystemWatcher,
    QModelIndex,
    QPersistentModelIndex,
    QSignalBlocker,
    QCoreApplication,
    QSize,
)
from core.threader import run_in_thread
from core.ui_lang import t
from core.app_paths import (
    APP_ROOT, USER_STATE_DIR, migrate_legacy_file, neocat_cache_dir,
)
from core import apktool_tool
from main_ext.class_NameBadgeDelegate import (
    NameBadgeDelegate, SIZE_LABEL_ROLE, SIZE_PENDING, SizeProbeService, IS_FOLDER_ROLE,
    _fmt_size as fmt_size, is_folder_item,
)
from main_ext.class_PermDialog import PermDialog
from main_ext.class_ConfigEngine import ConfigEngine,prescan_task,load_file_context,load_fs_config,perm_to_rwx
from services.unpack_watcher import UnpackWatcher


_FILETREE_PROTECTED_FILENAMES = frozenset({
    "agents.md",
    "claude.md",
    "codex.md",
})


def is_filetree_entry_hidden(
    name: str,
    *,
    is_dir: bool,
    is_root_level: bool,
    show_apktool_dec: bool,
) -> bool:

    normalized_name = str(name).casefold()
    if normalized_name in _FILETREE_PROTECTED_FILENAMES:
        return True
    return (
        is_dir
        and is_root_level
        and normalized_name == apktool_tool.APKTOOL_DEC_DIR.casefold()
        and not show_apktool_dec
    )


def _load_ctx_from_dir(cfg_dir: str) -> dict:

    ctx = {}
    if not os.path.isdir(cfg_dir):
        return ctx
    for fname in os.listdir(cfg_dir):
        if not fname.endswith("_file_contexts"):
            continue
        try:
            with open(os.path.join(cfg_dir, fname), "r", encoding="utf-8", errors="ignore") as f:
                for ln in f:
                    s = ln.strip()
                    if not s or s.startswith("#"):
                        continue
                    parts = s.split()
                    if len(parts) >= 2:
                        ctx[parts[0]] = parts[1]
        except Exception:
            pass
    return ctx


def _load_fs_from_dir(cfg_dir: str) -> dict:

    fs = {}
    if not os.path.isdir(cfg_dir):
        return fs
    for fname in os.listdir(cfg_dir):
        if not fname.endswith("_fs_config"):
            continue
        try:
            with open(os.path.join(cfg_dir, fname), "r", encoding="utf-8", errors="ignore") as f:
                for ln in f:
                    s = ln.strip()
                    if not s or s.startswith("#"):
                        continue
                    parts = s.split()
                    if len(parts) >= 4:
                        fs[parts[0]] = parts[3]
        except Exception:
            pass
    return fs


def _load_fs_full_from_dir(cfg_dir: str) -> dict:

    fs = {}
    if not os.path.isdir(cfg_dir):
        return fs
    for fname in os.listdir(cfg_dir):
        if not fname.endswith("_fs_config"):
            continue
        try:
            with open(os.path.join(cfg_dir, fname), "r", encoding="utf-8", errors="ignore") as f:
                for ln in f:
                    s = ln.strip()
                    if not s or s.startswith("#"):
                        continue
                    parts = s.split()
                    if len(parts) >= 4:
                        fs[parts[0]] = " ".join(parts[1:4])
        except Exception:
            pass
    return fs


_DEFAULT_CTX = "u:object_r:system_file:s0"


def _nb_append_ctx_entry(branch_cfg_dir: str, fs_root: str, raw_key: str):

    ctx_fname = None
    for fname in os.listdir(branch_cfg_dir):
        if fname == fs_root + "_file_contexts":
            ctx_fname = fname
            break
    if not ctx_fname:
        return  # no file_contexts for this fs_root in branch

    ctx_file = os.path.join(branch_cfg_dir, ctx_fname)
    try:
        with open(ctx_file, "r", encoding="utf-8", errors="ignore") as f:
            lines = [ln.rstrip("\n") for ln in f]
    except Exception:
        lines = []

    if any(ln.startswith(raw_key + " ") for ln in lines):
        return  # already exists

    lines.append(f"{raw_key} {_DEFAULT_CTX}")
    lines.sort()
    with open(ctx_file, "w", encoding="utf-8", errors="ignore") as f:
        f.write("\n".join(lines) + "\n")
def _nb_append_fs_entry(branch_cfg_dir: str, fs_root: str, rel: str, engine):

    fs_fname = None
    for fname in os.listdir(branch_cfg_dir):
        if fname == fs_root + "_fs_config":
            fs_fname = fname
            break
    if not fs_fname:
        return  # no _fs_config for this fs_root in branch

    fs_file = os.path.join(branch_cfg_dir, fs_fname)
    try:
        with open(fs_file, "r", encoding="utf-8", errors="ignore") as f:
            lines = [ln.rstrip("\n") for ln in f]
    except Exception:
        lines = []

    # already exists → nothing to do
    if any(ln.split()[0] == rel for ln in lines if ln.strip() and not ln.startswith("#")):
        return

    inferred = engine.infer_permission(rel)
    if len(inferred) == 4:
        uid, gid, mode, *_ = inferred
        new_line = f"{rel} {uid} {gid} {mode}"
    else:
        uid, gid, mode = inferred
        new_line = f"{rel} {uid} {gid} {mode}"

    lines.append(new_line)
    lines.sort()
    with open(fs_file, "w", encoding="utf-8", errors="ignore") as f:
        f.write("\n".join(lines) + "\n")


from main_ext.class_FileConflictDialog import (
    FileConflictDialog,
    ask_file_conflict,
    ask_multi_file_conflict,
)
from icons.handler import icon, icon_pixmap, icon_resource_key
from main_ext.FileTree_ext.RightClick import FileTreeContextMixin
from main_ext.FileTree_ext.MountDisk import MountDiskMixin, MOUNT_ROLE
from main_ext.FileTree_ext.Clipboard import FileTreeClipboardMixin
from main_ext.FileTree_ext.Thumbnails import ThumbnailMixin
from main_ext.FileTree_ext.DirectoryLoading import DirectoryLoadingMixin
from main_ext.class_NeoBranchInterceptor import NeoBranchInterceptorMixin
from main_ext.class_NeoBranchManager import NB_STORE_DIR
import sys, os

# Internal sidecars (e.g. boot unpack's <name>/.rak_boot) are never shown in
# the tree. Any entry whose name starts with this prefix is skipped on listing.
RAK_HIDDEN_PREFIX = ".rak_"
print("[PRIORITY_STAGE 1] FILETREE LOAD")
print("[PRIORITY_STAGE 1] FILETREE LOAD - LOCAL CACHE")


UI_STABLE_ROLE = Qt.UserRole + 9


APKTOOL_LINK_ROLE = Qt.UserRole + 71

class _TreeIconBundle:
    def __init__(self, tree):
        self.icons = {name: value for name, value in vars(tree).items() if name.startswith("icon_")}
        self.extensions = dict(tree._ext_icon_map)


# Entries live only while a view owns the bundle. No global QPixmap ownership
# outlives all the views or crosses application instances.
_TREE_ICON_BUNDLES = weakref.WeakKeyDictionary()


class FileTree(QTreeWidget, FileTreeContextMixin, MountDiskMixin, FileTreeClipboardMixin, ThumbnailMixin, NeoBranchInterceptorMixin, DirectoryLoadingMixin):
    ARCHIVE_EXT = [
        ".zip", ".jar", ".rar", ".7z", ".tar", ".gz", ".tgz",
        ".bz2", ".xz", ".lz", ".lz4", ".iso", ".zst", ".pac"
    ]

    TEXT_EXT = [
        # Plain text & config
        ".txt", ".text", ".log", ".ini", ".cfg", ".conf", ".rc", ".prop", ".properties",
        ".env", ".env.example", ".editorconfig", ".gitignore", ".gitattributes", ".gitmodules",
        ".dockerignore", ".htaccess", ".htpasswd", ".toml", ".yaml", ".yml", ".json",
        ".json5", ".ndjson", ".jsonl", ".lock",
        ".npmrc", ".yarnrc", ".nvmrc", ".node-version",
        ".pylintrc", ".flake8", ".bandit", ".mypy.ini",
        ".eslintrc", ".eslintignore", ".prettierrc", ".prettierignore",
        ".stylelintrc", ".browserslistrc", ".babelrc",
        ".clang-format", ".clang-tidy",
        ".rubocop.yml", ".rubocop_todo.yml",
        ".scalafmt.conf", ".scalariform.conf",
        ".terraform.lock.hcl",
        ".hadolint.yaml",
        ".codecov.yml", ".coveragerc",
        ".semgrepignore",
        ".mailmap",
        ".authors", ".contributors",
        ".version", ".changelog", ".changes", ".history",
        ".readme", ".todo", ".fixme", ".notes",
        ".license", ".copying", ".notice",

        # Markup & documentation
        ".xml", ".xsl", ".xslt", ".xsd", ".dtd", ".rss", ".atom", ".svg",
        ".html", ".htm", ".xhtml", ".html5", ".mhtml",
        ".md", ".markdown", ".mdown", ".mkd", ".mkdn", ".mdx",
        ".rst", ".rest", ".adoc", ".asciidoc", ".asc", ".tex", ".ltx", ".latex", ".bib",
        ".textile", ".wiki", ".mediawiki", ".org", ".pod", ".creole", ".rdoc",
        ".nfo", ".diz",  # scene/release info files
        ".man", ".roff", ".groff", ".troff",  # man pages
        ".info",  # GNU info docs
        ".docbook", ".dbk",

        # Stylesheet
        ".css", ".scss", ".sass", ".less", ".styl", ".stylus", ".pcss", ".postcss",

        # Web / frontend
        ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".mts", ".cts",
        ".vue", ".svelte", ".astro", ".marko",
        ".ejs", ".hbs", ".handlebars", ".mustache", ".pug", ".jade", ".haml", ".slim",
        ".twig", ".njk", ".nunjucks", ".liquid", ".jinja", ".jinja2", ".j2",
        ".erb", ".rhtml",
        ".wasm",  # wat source (text format)
        ".wat",   # WebAssembly text format

        # PHP (dedicated icon, handled above)
        ".php7", ".php8", ".phps",

        # Python
        ".py", ".pyw", ".pyi", ".pyx", ".pxd", ".pxi", ".rpy",

        # Ruby
        ".rb", ".rbw", ".rake", ".gemspec", ".ru", ".thor",
        ".rbs",  # Ruby type signatures
        ".rbi",  # Sorbet type signatures

        # Perl
        ".pl", ".pm", ".t", ".pod", ".p6", ".pl6", ".pm6",  # Perl 6 / Raku
        ".raku", ".rakumod", ".rakutest",

        # Lua
        ".lua", ".luau",  # Luau (Roblox)

        # R
        ".r", ".R", ".rmd", ".Rmd", ".rnw", ".Rnw",

        # Tcl
        ".tcl", ".tk", ".itcl", ".itk", ".exp",  # expect scripts

        # JVM languages (kt/kts and smali have dedicated icons)
        ".java", ".groovy", ".gvy", ".gy", ".gradle",
        ".scala", ".sc", ".sbt",
        ".clj", ".cljs", ".cljc", ".edn",
        ".ceylon",
        ".x10",

        # Elixir / Erlang
        ".ex", ".exs", ".heex", ".eex", ".leex",
        ".erl", ".hrl", ".app", ".app.src",
        ".beam",  # sometimes needed

        # Haskell / Purescript / Elm
        ".hs", ".lhs", ".hsc",
        ".purs",  # PureScript
        ".elm",

        # OCaml / SML / ReasonML
        ".ml", ".mli", ".mll", ".mly",
        ".sml", ".sig", ".fun",
        ".re", ".rei",  # ReasonML

        # F# / VB.NET
        ".fs", ".fsi", ".fsx", ".fsscript",
        ".vb",  # VB.NET

        # Dart / Nim / Julia / Crystal / Haxe
        ".dart", ".nim", ".nimble", ".jl", ".cr",
        ".hx", ".hxml",  # Haxe

        # Go
        ".go", ".mod", ".sum",  # go.sum

        # Rust (dedicated icon, handled above)

        # Zig
        ".zig", ".zon",  # Zig build manifest

        # Odin
        ".odin",

        # D lang
        ".d", ".di",

        # V lang
        ".v", ".vh",

        # Pascal / Delphi
        ".pas", ".pp", ".inc", ".dpr", ".dpk", ".lpr", ".dfm", ".fmx",

        # Ada
        ".ada", ".adb", ".ads",

        # COBOL
        ".cob", ".cbl", ".cobol", ".cpy",

        # Fortran
        ".f", ".f90", ".f95", ".f03", ".f08", ".for", ".ftn", ".f77",

        # C / C++
        ".c", ".cc", ".cpp", ".cxx", ".c++",
        ".h++",
        ".inl", ".ipp", ".tcc", ".tpp",
        ".pch",  # precompiled header source

        # C#
        ".cs", ".csx", ".cshtml", ".razor",
        ".csproj", ".vbproj", ".fsproj", ".sln",  # project files (XML/text)
        ".props", ".targets",  # MSBuild

        # Swift
        ".swift",

        # Objective-C
        ".m", ".mm",

        # Assembly
        ".asm", ".s", ".S", ".nasm", ".masm", ".gas", ".a51",
        ".inc",  # include files for asm

        # Shell / scripting (sh/zsh/fish have dedicated icons, handled above)
        ".bat", ".cmd", ".ps1", ".psm1", ".psd1", ".ps1xml",
        ".vbs", ".vba", ".vb",
        ".awk", ".sed",
        ".m4",  # autoconf
        ".ac", ".am",  # automake

        # Data & serialization
        ".csv", ".tsv", ".psv", ".ssv",
        ".sql", ".pgsql", ".mysql", ".sqlite", ".ddl", ".dml",
        ".graphql", ".gql",
        ".proto",
        ".thrift",
        ".avro",
        ".capnp",
        ".fbs",   # FlatBuffers
        ".idl",   # Interface Definition Language
        ".wsdl",  # Web Services Description Language
        ".wadl",
        ".raml",  # REST API Markup Language

        # Build / project / CI
        ".cmake", ".make", ".mk", ".mak",
        ".dockerfile", ".containerfile",
        ".nix", ".nixpkgs",
        ".bazel", ".bzl", ".star",
        ".buck",
        ".gradle", ".maven",
        ".tf", ".tfvars", ".hcl",
        ".jenkinsfile",
        ".travis.yml",
        ".gitlab-ci.yml",
        ".circleci",
        ".github",
        ".gn", ".gni",  # GN build system (Chromium)
        ".podspec",     # CocoaPods
        ".pbxproj",     # Xcode project (text-based plist)
        ".xcconfig",    # Xcode config

        # Notebook (text-based)
        ".ipynb",
        ".Rmd",
        ".qmd",   # Quarto

        # Subtitle / caption
        ".srt", ".vtt", ".sub", ".ass", ".ssa", ".sbv", ".lrc",

        # Calendar / contact
        ".ics", ".vcf",

        # Certificate / key (text/PEM form)
        ".pem", ".crt", ".cer", ".csr", ".key", ".pub",
        ".asc",  # GPG armored

        # Windows specific
        ".inf", ".reg", ".manifest",
        ".wxs", ".wxi", ".wxl",  # WiX installer

        # Apple / macOS
        ".plist", ".xcconfig", ".entitlements",

        # Linux desktop / systemd
        ".desktop", ".service", ".socket", ".timer", ".mount",
        ".target", ".slice", ".path",  # systemd units
        ".rules",  # udev rules
        ".pkla",   # polkit

        # Game / engine scripts
        ".gd", ".gdscript",
        ".rpy",
        ".ink",
        ".twee", ".tw",
        ".unity",   # Unity YAML scenes
        ".prefab",  # Unity prefabs (YAML)
        ".meta",    # Unity meta files
        ".asset",   # Unity assets (YAML)
        ".anim",    # Unity animations (YAML)
        ".lua",
        ".cs",
        ".angelscript", ".as",  # AngelScript
        ".wren",    # Wren scripting
        ".squirrel", ".nut",  # Squirrel

        # Shaders
        ".glsl", ".hlsl", ".vert", ".frag", ".geom", ".comp", ".metal",
        ".wgsl",
        ".shader",  # Unity ShaderLab
        ".cg", ".cgfx",  # Nvidia Cg
        ".usf", ".ush",  # Unreal Engine shaders
        ".fx",  # DirectX effect

        # Configuration DSL / misc languages
        ".dhall",
        ".cue",    # CUE lang
        ".pkl",    # Apple Pkl config
        ".nickel", # Nickel lang
        ".jsonnet", ".libsonnet",  # Jsonnet
        ".starlark",
        ".sky",
        ".rego",   # Open Policy Agent
        ".cel",    # Common Expression Language

        # IDE / editor specific
        ".iml",    # IntelliJ module
        ".ipr", ".iws",
        ".project", ".classpath",  # Eclipse
        ".vscode",
        ".code-workspace",
        ".sublime-project", ".sublime-workspace",
        ".editorconfig",

        # Other misc
        ".diff", ".patch",
        ".po", ".pot",
        ".feature",
        ".robot",
        ".spec",
        ".test", ".tests",
        ".bak", ".orig",
        ".sample", ".example", ".stub",
        ".tmpl", ".template", ".tpl",
        ".linuxmeta", ".rensh",
    ]

    AUDIO_EXT = [
        # Lossy
        ".mp3", ".aac", ".ogg", ".oga", ".opus", ".m4a", ".m4b", ".m4p",
        ".wma", ".amr", ".awb", ".3gp", ".3g2",
        ".ra", ".rm", ".rmvb",  # RealAudio
        ".voc",  # Creative Voice
        ".gsm",

        # Lossless
        ".wav", ".flac", ".alac", ".aiff", ".aif", ".aifc",
        ".ape",   # Monkey's Audio
        ".wv",    # WavPack
        ".tta",   # True Audio
        ".shn",   # Shorten
        ".tak",   # Tom's lossless Audio Kompressor
        ".dsd", ".dsf", ".dff",  # DSD (hi-res audio)
        ".pcm", ".raw",

        # MIDI / tracker / module
        ".mid", ".midi", ".rmi",
        ".mod", ".xm", ".it", ".s3m", ".669", ".mtm",  # tracker modules
        ".nsf", ".nsfe",  # NES Sound Format
        ".spc",  # SNES
        ".gbs",  # Game Boy
        ".gym",  # Sega Genesis
        ".vgm", ".vgz",  # Video Game Music

        # Playlist (text-based tapi sering dipakai bareng audio)
        ".m3u", ".m3u8", ".pls", ".xspf", ".asx", ".wpl", ".cue",

        # Other
        ".mka",  # Matroska audio
        ".spx",  # Speex
        ".caf",  # Apple Core Audio
        ".sd2",  # Sound Designer II
        ".au",   # Sun/NeXT audio
        ".pvf",  # Portable Voice Format
        ".8svx", ".16sv",  # Amiga IFF
    ]

    VIDEO_EXT = [
        # Common
        ".mp4", ".m4v", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".f4v",
        ".webm", ".ogv", ".ogg",
        ".3gp", ".3g2",  # mobile
        ".ts", ".mts", ".m2ts",  # MPEG transport stream
        ".vob",  # DVD
        ".ifo", ".bup",  # DVD info
        ".mpg", ".mpeg", ".mp2", ".mpe", ".mpv", ".m2v", ".m4p",
        ".divx", ".xvid",
        ".rm", ".rmvb",  # RealVideo
        ".asf", ".asx",  # Advanced Streaming Format
        ".dv",   # Digital Video
        ".mxf",  # Material Exchange Format (broadcast)
        ".roq",  # id Software
        ".nsv",  # Nullsoft Streaming Video
        ".amv",  # Anime Music Video / Action Camera
        ".svi",  # Samsung Video
        ".yuv",  # Raw YUV

        # High-end / professional
        ".r3d",  # RED camera
        ".braw", # Blackmagic RAW
        ".mxf",
        ".gxf",  # General Exchange Format
        ".lxf",  # Leitch
        ".nut",
        ".wtv", ".dvr-ms",  # Windows TV recordings

        # Animation / web
        ".gif", ".gifv", ".apng", ".avif",  # animated
        ".swf",  # Flash (legacy)

        # Apple
        ".mov", ".qt",
    ]

    IMAGE_EXT = [
        # Common raster
        ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tiff", ".tif",
        ".avif", ".heic", ".heif",  # modern formats
        ".jxl",  # JPEG XL

        # RAW camera formats
        ".raw", ".arw",  # Sony
        ".cr2", ".cr3",  # Canon
        ".nef", ".nrw",  # Nikon
        ".orf",  # Olympus
        ".rw2",  # Panasonic
        ".pef",  # Pentax
        ".srw",  # Samsung
        ".raf",  # Fujifilm
        ".dng",  # Adobe DNG (universal RAW)
        ".3fr",  # Hasselblad
        ".mef",  # Mamiya
        ".erf",  # Epson
        ".mrw",  # Minolta
        ".x3f",  # Sigma
        ".rwl",  # Leica

        # Vector
        ".svg", ".svgz",
        ".ai",   # Adobe Illustrator
        ".eps", ".ps",
        ".emf", ".wmf",  # Windows Metafile
        ".cdr",  # CorelDRAW
        ".afdesign",  # Affinity Designer
        ".sketch",    # Sketch

        # Professional / HDR
        ".exr",  # OpenEXR (VFX/HDR)
        ".hdr",  # Radiance HDR
        ".pfm",  # Portable Float Map
        ".dpx",  # Digital Picture Exchange (film)
        ".cin",  # Cineon

        # Adobe / design
        ".psd",  # Photoshop
        ".psb",  # Photoshop Large
        ".xcf",  # GIMP
        ".kra",  # Krita
        ".cpt",  # Corel Photo-Paint
        ".clip", ".csp",  # Clip Studio Paint

        # Icon / UI
        ".ico", ".icns",  # Windows & macOS icons
        ".cur",  # Windows cursor
        ".ani",  # Animated cursor

        # Old / misc raster
        ".pcx",  # ZSoft
        ".tga",  # Targa
        ".ppm", ".pgm", ".pbm", ".pnm",  # Netpbm
        ".xbm", ".xpm",  # X11 bitmaps
        ".sgi", ".rgb", ".rgba", ".bw",  # SGI
        ".dib",  # Device Independent Bitmap
        ".wdp", ".jxr", ".hdp",  # JPEG XR / HD Photo
        ".jp2", ".j2k", ".jpf", ".jpx", ".jpm",  # JPEG 2000
        ".mng",  # Multiple-image Network Graphics
        ".apng",
        ".flif",  # Free Lossless Image Format (deprecated tapi ada)
        ".qoi",   # Quite OK Image

        # 3D / specialized
        ".hdr", ".latlong",
        ".iff",  # IFF image (Amiga / Maya)
        ".vtf",  # Valve Texture Format
        ".dds",  # DirectDraw Surface (game textures)
        ".ktx", ".ktx2",  # Khronos Texture
        ".astc",  # Adaptive Scalable Texture Compression
        ".pvr",   # PowerVR texture
        ".pkm",   # ETC compressed texture
    ]

    NON_FS_BADGE = ("SUPER", "SPARSE", "BOOT", "VNDRBOOT")

    def __init__(self):
        super().__init__()
        self.setFrameShape(QTreeWidget.NoFrame)
        self.setFrameShadow(QTreeWidget.Plain)
        self.viewport().setAutoFillBackground(False)
        self.viewport().setAttribute(Qt.WA_OpaquePaintEvent)
        self.setVerticalScrollMode(QTreeWidget.ScrollPerPixel)
        self._wheel_target = 0
        self._wheel_animation = QPropertyAnimation(self.verticalScrollBar(), b"value", self)
        self._wheel_animation.setDuration(120)
        self._wheel_animation.setEasingCurve(QEasingCurve.OutCubic)
        self.verticalScrollBar().sliderPressed.connect(self._stop_wheel_scroll)
        self.verticalScrollBar().rangeChanged.connect(self._stop_wheel_scroll)


        self.watcher = QFileSystemWatcher(self)
        self.child_snapshot = {}
        self._fs_event_guard = set()
        self._pending_directory_changes = set()
        self._directory_change_timer = QTimer(self)
        self._directory_change_timer.setSingleShot(True)
        self._directory_change_timer.setInterval(200)
        self._directory_change_timer.timeout.connect(self._flush_directory_changes)
        self._background_stopped = False
        self._init_directory_loading()
        self.drop_conflict_policy = None
        # None | "replace_all" | "skip_all"
        self.watcher.directoryChanged.connect(self.on_directory_changed)


        self._unpack_watcher = UnpackWatcher(self)
        self.setStyleSheet(f"""
            /* ---- BASE ---- */
            QTreeWidget {{
                font-size: 13px;
                color: {_th.FG};
                background: {_th.BG3};
                border: none;
                padding: 0px;
            }}

            /* ---- HEADER (rata, ga ada corner jelek) ---- */
            QHeaderView::section {{
                background: {_th.BG3};
                color: {_th.FG2};
                padding: 7px 6px;
                font-size: 11px;
                font-weight: 600;
                border: none;
                border-bottom: 1px solid {_th.border_color(0.08)};
            }}

            QHeaderView::section:hover {{
                /* hover halus biar keliatan bisa di-drag */
                background-color: {_th.border_color(0.06)};
            }}

            QHeaderView {{
                /* garis halus di bawah seluruh header */
                border-bottom: 1px solid {_th.border_color(0.12)};
            }}


            /* ---- ITEM ---- */
            QTreeWidget::item {{
                height: 20px;
                padding: 4px;
                border: none;
                color: {_th.FG};
            }}

            /* HOVER (soft, ga lebay) */
            QTreeWidget::item:hover {{
                background-color: transparent;
                color: {_th.FG};
            }}

            /* SELECTED: modern blue transparent */
            QTreeWidget::item:selected {{
                background-color: transparent;
                color: {_th.FG};
            }}

            /* =========================
            SCROLLBAR VERTICAL (HILANG TOTAL)
            ========================= */
            QScrollBar:vertical {{
                background: transparent;
                width: 4px;
                margin: 0px;
            }}

            QScrollBar::handle:vertical {{
                background: {_th.border_color(0.18)};
                border-radius: 2px;
                min-height: 20px;
            }}

            QScrollBar::handle:vertical:hover {{
                background: {_th.border_color(0.35)};
            }}

            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {{
                height: 0px;
            }}

            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical {{
                background: transparent;
            }}


            /* =========================
            SCROLLBAR HORIZONTAL (CUSTOM TIPIS)
            ========================= */
            QScrollBar:horizontal {{
                background: transparent;
                height: 4px;
                margin: 0px;
            }}

            QScrollBar::handle:horizontal {{
                background: transparent;
                border-radius: 2px;
                min-width: 20px;
            }}

            QScrollBar::handle:horizontal:hover {{
                background: {_th.border_color(0.35)};
            }}

            QScrollBar::add-line:horizontal,
            QScrollBar::sub-line:horizontal {{
                width: 0px;
            }}

            QScrollBar::add-page:horizontal,
            QScrollBar::sub-page:horizontal {{
                background: transparent;
            }}


            /* ---- DIALOG ---- */
            QDialog {{
                background-color: {_th.BG_TREE};
                border: 1px solid {_th.border_color(0.18)};
            }}
            QPushButton {{
                background-color: {_th.border_color(0.18)};
                padding: 6px 10px;
                border-radius: 4px;
                color: {_th.FG};
            }}
            QPushButton:hover {{
                background-color: {_th.border_color(0.25)};
            }}
            QLabel {{
                color: {_th.FG};
            }}
            QTreeWidget::item:focus {{
                outline: none;
                border: none;
            }}

            QTreeView::branch:focus {{
                outline: none;
                border: none;
            }}

            QTreeWidget:focus {{
                outline: none;
                border: none;
            }}
            QTreeView::dropIndicator {{
                background-color: {_th.accent(0.35)};
                height: 3px;
            }}
            QTreeWidget::item:hover:!selected {{
                background-color: transparent;
                color: {_th.FG};
            }}
            """)


        self._sb_hide_timer = QTimer(self)
        self._sb_hide_timer.setSingleShot(True)
        self._sb_hide_timer.setInterval(1000)
        self._sb_hide_timer.timeout.connect(self._hide_scrollbar)
        self._sb_hovered = False
        self._sb_visible = False
        # horizontal
        self._sbh_hide_timer = QTimer(self)
        self._sbh_hide_timer.setSingleShot(True)
        self._sbh_hide_timer.setInterval(1000)
        self._sbh_hide_timer.timeout.connect(self._hide_scrollbar_h)
        self._sbh_hovered = False
        self._sbh_visible = False
        QTimer.singleShot(0, self, self._setup_scrollbar_hover)
        QTimer.singleShot(0, self, self._setup_scrollbar_hover_h)
        QTimer.singleShot(0, self, self._hide_scrollbar)
        QTimer.singleShot(0, self, self._hide_scrollbar_h)


        self._drag_scroll_timer = QTimer(self)
        self._drag_scroll_timer.setInterval(30)  # ~33fps, smooth tanpa bikin CPU sibuk
        self._drag_scroll_timer.timeout.connect(self._drag_scroll_tick)
        self._drag_scroll_speed = 0.0  # pixel per tick (float, bisa <1 utk pelan banget)
        self._drag_scroll_accum = 0.0  # akumulator subpixel

        self.menu_style = f"""
            QMenu {{
                background-color: {_th.BG_DARK};
                border: 1px solid {_th.border_color(0.18)};
                padding: 6px;
            }}

            QMenu::item {{
                padding: 6px 28px 6px 24px;
                color: {_th.FG_LABEL};
                background: transparent;
            }}

            QMenu::item:selected {{
                background-color: {_th.accent(0.25)};
                color: {_th.FG_BRIGHT};
            }}

            QMenu::separator {{
                height: 1px;
                background: {_th.border_color(0.15)};
                margin: 6px 6px;
            }}
            """

        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setSelectionMode(QTreeWidget.ExtendedSelection)


        self.setAutoScroll(False)

        # MATIKAN internal move (INI YANG BENER)
        self.setDragDropMode(QTreeWidget.DragDrop)

        self.setDefaultDropAction(Qt.MoveAction)
        self.drag_hover_item = None   # legacy, tetap ada
        self.drag_hover_items = set()  # set item yg di-highlight
        self._drag_hover_position = None
        self._drag_hover_mounted = False
        self._hovered_item = None      # hover manual biar bisa rounded
        self._multi_anchor_parent = None  # parent constraint for multi-select
        self._multi_selecting     = False  # True while Ctrl/Shift held
        self.selectionModel().selectionChanged.connect(self._on_selection_changed)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.update()

        # BRANCH ARROW
        # indentation diperkecil supaya arrow-folder gap minimal
        self.setIndentation(20)
        self.setIconSize(QSize(20, 20))


        self.setUniformRowHeights(True)
        _ap = icon_pixmap("arrow_right")
        _size = 12
        self._arrow_closed = _ap.scaled(_size, _size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self._arrow_open = self._arrow_closed.transformed(QTransform().rotate(90), Qt.SmoothTransformation)

        _badge_delegate = NameBadgeDelegate(self, {})
        _badge_delegate.badge_keys = {
            "EROFS", "EXT4", "EXT3", "EXT2", "SUPER", "SPARSE", "BOOT", "VNDRBOOT", "BR",
            "DAT", "DAT.BR", "PAYLOAD",
        }
        self._badge_delegate = _badge_delegate
        self._cell_warm_timer = QTimer(self)
        self._cell_warm_timer.setSingleShot(True)
        self._cell_warm_timer.setInterval(120)
        self._cell_warm_timer.timeout.connect(lambda: setattr(self._badge_delegate, "_cache_new_cells", True))
        self.setItemDelegateForColumn(0, _badge_delegate)
        self.setItemDelegateForColumn(1, _badge_delegate)
        self._load_icons()
        self._thumb_init()

        self._creating_item = None
        self._editing_item = None
        self._renaming = False
        # guard re-entrant commit context (lihat on_item_edited)
        self._in_ctx_edit = False

        # ── Size & Symlink probe service: hitung size dan probe symlink di background, update non-blok.
        # Folder buka INSTAN, size dan ikon/dot ijo symlink nyusul di background.
        self._size_probe = SizeProbeService(self)


        self._size_probe_alias = {}
        self._size_probe_items = {}
        self._size_probe.resolved.connect(
            self._on_size_resolved, Qt.QueuedConnection
        )


        self.setColumnCount(3)
        self.setHeaderLabels(["Name", "Selinux", "Permission"])
        self.setColumnWidth(0, 270)
        self.setColumnWidth(1, 160)
        self.setColumnWidth(2, 95)
        header = self.header()
        header.setStretchLastSection(False)

        # Name column (kiri)
        header.setSectionResizeMode(0, QHeaderView.Interactive)  # bisa di-resize manual
        header.resizeSection(0, 270)  # room for filename and size badge

        # Context column (tengah, harus luas + fleksibel)
        header.setSectionResizeMode(1, QHeaderView.Stretch)    # ikut melar pas window di-resize
        header.resizeSection(1, 160)

        # Perm column (kanan, kecil tapi nempel kanan)
        header.setSectionResizeMode(2, QHeaderView.Interactive)
        header.resizeSection(2, 95)


        self._column_sync_peers = []
        self._syncing_columns = False
        header.sectionResized.connect(self._on_header_section_resized)


        self.base = None
        # apktool_dec is generated decompile workspace. It stays invisible by
        # default, but Main can enable it live from Project Settings.
        self.show_apktool_dec = False


        self.display_mode = "tree"
        self._sd_stack = []  # stack path/state ancestor untuk navigasi "up" single_dir

        self.ctx = {}
        self.fs  = {}
        self.engine = None
        self.ctx_neocat = {}

        # SATU PINTU: cache magic detection — entry: {mtime, size, magic}
        # magic = "EROFS"|"EXT4"|"EXT3"|"EXT2"|"SUPER"|"SPARSE"|"PAYLOAD"|""
        self.magic_cache = {}
        # Backward-compat aliases — keep berfungsi untuk kode yang masih
        # akses langsung. Isi-nya derivasi dari magic_cache via property.
        self.fsdetect_cache = {}     # EXT / EROFS ONLY (legacy view)
        self.superdetect_cache = {}  # SUPER / SPARSE ONLY (legacy view)
        # cache untuk info folder mana yang punya anak (hasil prescan)
        self.folder_has_child = {}
        self.prescan_tree = {}

        # NeoBranch
        self._nb_manager = None

        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.open_context_menu)

        self.itemChanged.connect(self.on_item_edited)

    def delete_selected(self):

        if getattr(self, "_renaming", False):
            return

        import os as _os
        selected = self.selectedItems()
        if not selected:
            cur = self.currentItem()
            if cur:
                selected = [cur]
        if selected:
            if len(selected) == 1:
                self.delete_item(selected[0])
            else:
                items_info = []
                for it in selected:
                    full       = it.data(0, Qt.UserRole)
                    branch_abs = it.data(0, Qt.UserRole + 20)
                    phys_full  = branch_abs if branch_abs and _os.path.exists(branch_abs) else full
                    if not full:
                        continue
                    items_info.append({
                        "item": it, "full": full, "phys_full": phys_full,
                        "branch_abs": branch_abs,
                        "fs_badge": it.data(0, Qt.UserRole + 1),
                        "is_file": _os.path.isfile(phys_full) if phys_full else False,
                        "is_dir":  _os.path.isdir(phys_full)  if phys_full else False,
                    })
                if items_info:
                    self._batch_delete(items_info)

    def drawBranches(self, painter: QPainter, rect, index):

        item = self.itemFromIndex(index)
        if not item:
            return


        if self.display_mode == "single_dir":
            bg = self.palette().color(self.backgroundRole())
            painter.fillRect(rect, bg)
            return

        indent = self.indentation()

        # Slot arrow item ini (paling kanan dari branch area)
        slot_left  = rect.right() - indent
        slot_width = indent

        has_children = (
            item.childCount() > 0 or
            item.childIndicatorPolicy() == QTreeWidgetItem.ShowIndicator
        )

        # Timpa area branch dengan warna bg asli tree (bukan transparent)
        bg = self.palette().color(self.backgroundRole())
        painter.save()
        painter.fillRect(rect, bg)
        painter.restore()


        try:
            br, bg_, bb = _th._palette["_border_rgb"]
        except Exception:
            br, bg_, bb = 255, 255, 255
        line_color = QColor(br, bg_, bb)
        line_color.setAlphaF(0.18)

        painter.save()
        pen = QPen(line_color)
        pen.setWidth(1)
        pen.setCosmetic(True)
        painter.setPen(pen)


        ancestors = []
        p = item.parent()
        while p is not None:
            ancestors.append(p)
            p = p.parent()
        ancestors.reverse()  # dari root ke parent terdekat

        # Gambar vertical line full-height utk tiap ancestor yg punya next-sibling
        for idx, anc in enumerate(ancestors):
            anc_parent = anc.parent()
            if anc_parent is None:
                # top-level ancestor
                anc_idx = self.indexOfTopLevelItem(anc)
                anc_total = self.topLevelItemCount()
            else:
                anc_idx = anc_parent.indexOfChild(anc)
                anc_total = anc_parent.childCount()
            if anc_idx < anc_total - 1:
                # ancestor ini masih punya sibling di bawah → garis nerusin
                depth_offset = len(ancestors) - idx  # berapa slot ke kiri dari slot item
                col_x = slot_left - depth_offset * indent + slot_width // 2
                painter.drawLine(col_x, rect.top(), col_x, rect.bottom())

        # Gambar garis untuk item sendiri (slot parent)
        # Slot parent = slot kiri 1 indent dari slot item
        if ancestors:  # cuma kalau item ini bukan top-level
            parent_item = item.parent()
            if parent_item is not None:
                parent_idx = parent_item.indexOfChild(item)
                parent_total = parent_item.childCount()
            else:
                parent_idx = self.indexOfTopLevelItem(item)
                parent_total = self.topLevelItemCount()

            parent_col_x = slot_left - indent + slot_width // 2
            mid_y = rect.y() + rect.height() // 2

            is_last = parent_idx >= parent_total - 1
            if is_last:
                # └─ : garis dari atas rect ke tengah, lalu horizontal ke arrow slot
                painter.drawLine(parent_col_x, rect.top(), parent_col_x, mid_y)
            else:
                # ├─ : garis full atas-bawah, plus horizontal stub ke arrow slot
                painter.drawLine(parent_col_x, rect.top(), parent_col_x, rect.bottom())

            # Horizontal stub: dari parent_col_x ke awal slot item (slot_left)
            # Stop sebelum arrow slot biar ga numpuk dgn panah folder
            painter.drawLine(parent_col_x, mid_y, slot_left, mid_y)

        painter.restore()


        if not has_children:
            return

        pix = self._arrow_open if item.isExpanded() else self._arrow_closed

        # Tengahin arrow di slot terakhir
        x = slot_left + (slot_width - pix.width()) // 2
        y = rect.y() + (rect.height() - pix.height()) // 2


        painter.save()
        painter.fillRect(x, y, pix.width(), pix.height(), bg)
        painter.restore()

        painter.save()
        painter.setOpacity(0.8)
        painter.drawPixmap(x, y, pix)
        painter.restore()

    def _load_icons(self):
        app = QCoreApplication.instance()
        cache = _TREE_ICON_BUNDLES.setdefault(app, weakref.WeakValueDictionary())
        key = (type(self), icon_resource_key())
        bundle = cache.get(key)
        if bundle is None:
            self._create_icons()
            bundle = _TreeIconBundle(self)
            cache[key] = bundle
        else:
            for name, value in bundle.icons.items():
                setattr(self, name, value)
            self._ext_icon_map = dict(bundle.extensions)
            if hasattr(self, "_badge_delegate"):
                self._badge_delegate.badge_keys = {
                    "EROFS", "EXT4", "EXT3", "EXT2", "SUPER", "SPARSE", "BOOT", "VNDRBOOT", "BR",
                    "DAT", "DAT.BR", "PAYLOAD",
                }
        self._icon_bundle = bundle

    def _create_icons(self):
        self.icon_folder        = QIcon(icon("folder"))
        self.icon_folder_neocat = QIcon(icon("folder_neocat"))
        self.icon_symlink       = QIcon(icon("symlink"))
        self.icon_symlink_file  = QIcon(icon("symlink_file"))
        self.icon_fs_folder     = QIcon(icon("fs_folder"))
        self.icon_file         = QIcon(icon("file"))
        self.icon_drive        = QIcon(icon("drive"))
        self.icon_drive_mounted = QIcon(icon("drive_mounted"))
        self.icon_image        = QIcon(icon("image"))
        self.icon_apk          = QIcon(icon("apk"))
        self.icon_apk_linked   = QIcon(icon("apk_linked"))
        self.icon_archive      = QIcon(icon("archive"))
        self.icon_archive_jar  = QIcon(icon("archive_jar"))
        self.icon_archive_7z   = QIcon(icon("archive_7z"))
        self.icon_archive_tar  = QIcon(icon("archive_tar"))
        self.icon_archive_rar  = QIcon(icon("archive_rar"))
        self.icon_archive_gz   = QIcon(icon("archive_gz"))
        self.icon_archive_iso  = QIcon(icon("archive_iso"))
        self.icon_txt          = QIcon(icon("txt"))
        self.icon_linux_meta   = QIcon(icon("linux_metadata"))
        self.icon_special_folder = QIcon(icon("special_folder"))
        self.icon_audio        = QIcon(icon("icon_audio"))
        self.icon_video        = QIcon(icon("icon_video"))
        self.icon_json         = QIcon(icon("icon_json"))
        self.icon_root         = QIcon(icon("project_ic"))
        self.icon_asm          = QIcon(icon("icon_asm"))
        self.icon_py           = QIcon(icon("icon_py"))
        self.icon_cpp          = QIcon(icon("icon_cpp"))
        self.icon_c            = QIcon(icon("icon_c"))
        self.icon_cs           = QIcon(icon("icon_cs"))
        self.icon_java         = QIcon(icon("icon_java"))
        self.icon_js           = QIcon(icon("icon_js"))
        self.icon_html         = QIcon(icon("icon_html"))
        self.icon_css          = QIcon(icon("icon_css"))
        self.icon_swift        = QIcon(icon("icon_swift"))
        self.icon_cobol        = QIcon(icon("icon_cobol"))
        self.icon_qss          = QIcon(icon("icon_qss"))
        self.icon_bin          = QIcon(icon("icon_bin"))
        self.icon_so           = QIcon(icon("icon_so"))
        self.icon_brotli       = QIcon(icon("icon_brotli"))
        self.icon_xml          = QIcon(icon("icon_xml"))
        self.icon_cfg          = QIcon(icon("icon_cfg"))
        self.icon_csv          = QIcon(icon("icon_csv"))
        self.icon_rc           = QIcon(icon("icon_rc"))
        self.icon_prop         = QIcon(icon("icon_prop"))
        self.icon_h            = QIcon(icon("icon_h"))
        self.icon_hpp          = QIcon(icon("icon_hpp"))
        self.icon_rust         = QIcon(icon("icon_rust"))
        self.icon_php          = QIcon(icon("icon_php"))
        self.icon_kt           = QIcon(icon("icon_kt"))
        self.icon_smali        = QIcon(icon("icon_smali"))
        self.icon_sh           = QIcon(icon("icon_sh"))
        self.icon_zsh          = QIcon(icon("icon_zsh"))
        self.icon_fish         = QIcon(icon("icon_fish"))
        self.icon_erofs_label  = QIcon(icon_pixmap("erofs_label"))
        self.icon_ext4_label   = QIcon(icon_pixmap("ext4_label"))
        self.icon_ext4l_label  = QIcon(icon_pixmap("ext4l_label"))
        self.icon_ext3_label   = QIcon(icon_pixmap("ext3_label"))
        self.icon_ext2_label   = QIcon(icon_pixmap("ext2_label"))
        self.icon_sparse_label = QIcon(icon_pixmap("sparse_label"))
        self.icon_super_label  = QIcon(icon_pixmap("super_label"))

        if hasattr(self, "_badge_delegate"):
            self._badge_delegate.badge_keys = {
                "EROFS", "EXT4", "EXT3", "EXT2", "SUPER", "SPARSE", "BOOT", "VNDRBOOT", "BR",
                "DAT", "DAT.BR", "PAYLOAD",
            }

        # Fast O(1) hash map untuk resolusi icon ekstensi (hindari 50 if/elif per file)
        self._ext_icon_map = {}

        # ── 1. Default fallback per kelompok (dijalankan DULUAN) ──
        for e in getattr(self, "TEXT_EXT", ()): self._ext_icon_map[e] = self.icon_txt
        for e in getattr(self, "ARCHIVE_EXT", ()): self._ext_icon_map[e] = self.icon_archive
        for e in getattr(self, "IMAGE_EXT", ()): self._ext_icon_map[e] = self.icon_image
        for e in getattr(self, "AUDIO_EXT", ()): self._ext_icon_map[e] = self.icon_audio
        for e in getattr(self, "VIDEO_EXT", ()): self._ext_icon_map[e] = self.icon_video

        # ── 2. Spesifik icon (meng-override fallback di atas) ──
        self._ext_icon_map[".img"] = self.icon_drive
        self._ext_icon_map[".br"] = self.icon_brotli
        self._ext_icon_map[".brotli"] = self.icon_brotli
        self._ext_icon_map[".bin"] = self.icon_bin
        self._ext_icon_map[".so"] = self.icon_so
        self._ext_icon_map[".apk"] = self.icon_apk
        self._ext_icon_map[".jar"] = self.icon_archive_jar
        self._ext_icon_map[".7z"] = self.icon_archive_7z
        for e in (".tar", ".tgz", ".bz2", ".xz", ".lz", ".lz4", ".zst"): self._ext_icon_map[e] = self.icon_archive_tar
        self._ext_icon_map[".rar"] = self.icon_archive_rar
        self._ext_icon_map[".gz"] = self.icon_archive_gz
        self._ext_icon_map[".iso"] = self.icon_archive_iso
        for e in (".json", ".jsonc"): self._ext_icon_map[e] = self.icon_json
        for e in (".xml", ".xsl", ".xslt", ".xsd", ".dtd", ".rss", ".atom"): self._ext_icon_map[e] = self.icon_xml
        for e in (".cfg", ".conf", ".ini"): self._ext_icon_map[e] = self.icon_cfg
        self._ext_icon_map[".csv"] = self.icon_csv
        for e in (".py", ".pyw"): self._ext_icon_map[e] = self.icon_py
        for e in (".asm", ".s", ".nasm", ".masm", ".gas", ".a51"): self._ext_icon_map[e] = self.icon_asm
        for e in (".hpp", ".hxx", ".hh"): self._ext_icon_map[e] = self.icon_hpp
        self._ext_icon_map[".h"] = self.icon_h
        for e in (".cpp", ".cc", ".cxx"): self._ext_icon_map[e] = self.icon_cpp
        self._ext_icon_map[".c"] = self.icon_c
        self._ext_icon_map[".cs"] = self.icon_cs
        self._ext_icon_map[".java"] = self.icon_java
        for e in (".js", ".mjs", ".cjs", ".ts", ".jsx", ".tsx"): self._ext_icon_map[e] = self.icon_js
        for e in (".html", ".htm"): self._ext_icon_map[e] = self.icon_html
        self._ext_icon_map[".css"] = self.icon_css
        self._ext_icon_map[".qss"] = self.icon_qss
        self._ext_icon_map[".rc"] = self.icon_rc
        for e in (".prop", ".properties"): self._ext_icon_map[e] = self.icon_prop
        self._ext_icon_map[".swift"] = self.icon_swift
        for e in (".cob", ".cbl", ".cobol", ".cpy"): self._ext_icon_map[e] = self.icon_cobol
        self._ext_icon_map[".rs"] = self.icon_rust
        for e in (".php", ".php3", ".php4", ".php5", ".phtml"): self._ext_icon_map[e] = self.icon_php
        for e in (".kt", ".kts"): self._ext_icon_map[e] = self.icon_kt
        self._ext_icon_map[".smali"] = self.icon_smali
        for e in (".sh", ".bash", ".ksh", ".csh", ".tcsh", ".dash"): self._ext_icon_map[e] = self.icon_sh
        self._ext_icon_map[".zsh"] = self.icon_zsh
        self._ext_icon_map[".fish"] = self.icon_fish

    def reload_icons(self):
        self._load_icons()
        self.viewport().update()

    def _resolve_file_icon(self, full):
        name = os.path.basename(full)
        # FS METADATA FILE
        if (
            name.endswith("_file_contexts") or
            name.endswith("_fs_config") or
            name.endswith("_fs_options") or
            name.endswith("_info")
        ):
            return self.icon_linux_meta

        if ".so." in name:
            return self.icon_so

        ext = os.path.splitext(full)[1].lower()
        if ext.startswith(".so"):
            return self.icon_so

        map_dict = getattr(self, "_ext_icon_map", None)
        if map_dict is not None:
            return map_dict.get(ext, self.icon_file)
        return self.icon_file

    def _dtgen_collapse_target(self, full):

        if os.path.basename(full).lower() != "recovery":
            return None
        try:
            marker = os.path.join(full, ".rak_dtgen.json")
            if not os.path.isfile(marker):
                return None
            import json
            with open(marker, "r", encoding="utf-8") as f:
                data = json.load(f)
            rel = (data.get("collapse_to") or "").replace("/", os.sep)
            if not rel:
                return None
            target = os.path.normpath(os.path.join(full, rel))
            return target if os.path.isdir(target) else None
        except Exception:
            return None

    def _resolve_folder_icon(self, full, fs_type):
        if fs_type:
            return self.icon_fs_folder

        base_norm = getattr(self, "_base_norm", None)
        if base_norm is None:
            base_norm = self._base_norm = os.path.normpath(self.base)
        full_norm = os.path.normpath(full)

        if full_norm == base_norm:
            return self.icon_root

        # special config folder (ONLY ROOT LEVEL)
        if (
            os.path.basename(full_norm) == "config" and
            os.path.dirname(full_norm) == base_norm
        ):
            return self.icon_special_folder

        # apktool_dec folder (ONLY ROOT LEVEL)
        if (
            os.path.basename(full_norm) == apktool_tool.APKTOOL_DEC_DIR and
            os.path.dirname(full_norm) == base_norm
        ):
            return self.icon_special_folder

        # neocat_config folder (ONLY ROOT LEVEL)
        if (
            os.path.basename(full_norm) == "neocat_config" and
            os.path.dirname(full_norm) == base_norm
        ):
            return self.icon_folder_neocat

        return self.icon_folder

    def on_repack_finished(self, img_path):

        if not img_path or not os.path.isfile(img_path):
            return

        # 🔥 INVALIDATE CACHE LAMA (1 pintu)
        self.magic_cache.pop(img_path, None)
        self.fsdetect_cache.pop(img_path, None)
        self.superdetect_cache.pop(img_path, None)

        # 🔥 DETECT ULANG via 1 pintu (file sudah stabil)
        fs_type = self._detect_magic_once(img_path)
        if not fs_type:
            return

        # 🔥 UPDATE UI BADGE
        item = self.find_item_by_path(self.topLevelItem(0), img_path)
        if item:
            item.setData(0, Qt.UserRole + 1, fs_type)
        if item:
            self.viewport().update()

    def reload_config_cache(self, with_neocat: bool = True):

        # A newer reload supersedes any queued metadata update from unpack.
        self._metadata_refresh = None
        # 1. Load source config dulu sebagai baseline
        self.ctx = load_file_context(self.base)
        self.fs  = load_fs_config(self.base)
        if with_neocat:
            self.ctx_neocat = self._load_neocat_ctx(self.base)


        try:
            import json as _json
            nb_root = os.path.join(self.base, "neobranch")
            if os.path.isdir(nb_root):
                for fs_root_name in os.listdir(nb_root):
                    fs_root_dir = os.path.join(nb_root, fs_root_name)
                    if not os.path.isdir(fs_root_dir):
                        continue
                    state_path = os.path.join(fs_root_dir, "neobranch_state.json")
                    if not os.path.isfile(state_path):
                        continue
                    try:
                        with open(state_path, "r", encoding="utf-8") as _f:
                            state = _json.load(_f)
                    except Exception:
                        continue
                    active_branch = state.get("active_branch")
                    if not active_branch:
                        continue  # null / None = source, skip
                    # Punya branch aktif — overlay config-nya
                    branch_cfg = os.path.join(
                        fs_root_dir, f"branch_{active_branch}", "config"
                    )
                    branch_neocat = neocat_cache_dir(
                        self.base, fs_root_name, active_branch
                    )
                    if os.path.isdir(branch_cfg):
                        self.ctx.update(_load_ctx_from_dir(branch_cfg))
                        self.fs.update(_load_fs_from_dir(branch_cfg))
                    if with_neocat and os.path.isdir(branch_neocat):
                        self.ctx_neocat.update(self._load_neocat_ctx_dir(branch_neocat))
                    print(f"[NeoBranch] overlay applied: fs_root={fs_root_name!r} branch={active_branch!r}")
        except Exception as _e:
            print(f"[NeoBranch] reload_config_cache scan error: {_e}")

        # 3. ── Sync engine cache
        if hasattr(self, "engine") and self.engine:
            if hasattr(self.engine, "rebuild_root_map"):
                try:
                    self.engine.rebuild_root_map()
                except Exception:
                    pass
            self.engine.ctx = self.ctx
            self.engine.fs  = self.fs
            if hasattr(self.engine, "tree_ref"):
                self.engine.tree_ref = self

        # 4. ── Refresh dot markers
        try:
            self.nb_refresh_all_dots()
        except Exception:
            pass

        # 5. ── Refresh SELinux/perm columns pada semua visible items
        try:
            root = self.topLevelItem(0)
            if root and hasattr(self, "engine") and self.engine:
                if with_neocat:
                    self._update_ctx_perm_recursive(root)
                else:
                    self._schedule_metadata_refresh(root)
        except Exception as _e:
            print(f"[NeoBranch] _update_ctx_perm_recursive error: {_e}")

        # 6. ── Repaint umum (ctx/perm). Size update sendiri lewat probe async
        #       (_on_size_resolved repaint per-item begitu hasilnya beda).
        self.viewport().update()

    def is_fs_root(self, rel):

        root = rel.split("/")[0]
        return root in self.engine.root_map_ctx and root in self.engine.root_map_fs


    def _detach_watchers_for(self, path):

        try:
            norm = os.path.normpath(path)
            watched = list(self.watcher.directories())
            for w in watched:
                wn = os.path.normpath(w)
                if wn == norm or wn.startswith(norm + os.sep):
                    try:
                        self.watcher.removePath(w)
                    except Exception:
                        pass
        except Exception:
            pass

    def _set_bulk_watcher_quiet(self, path, recursive, enabled):

        key = (os.path.normcase(os.path.normpath(path)), bool(recursive))
        counts = getattr(self, "_bulk_watcher_quiet", None)
        if counts is None:
            counts = self._bulk_watcher_quiet = {}
        count = counts.get(key, 0) + (1 if enabled else -1)
        if count > 0:
            counts[key] = count
        else:
            counts.pop(key, None)

    def _retry_fs_op(self, op_fn, attempts=5):

        import time as _t
        from PySide6.QtWidgets import QApplication

        last_err = None
        for attempt in range(attempts):
            try:
                op_fn()
                return None
            except FileExistsError as e:
                return ("exists", e)
            except FileNotFoundError as e:
                return ("notfound", e)
            except PermissionError as e:
                import gc
                gc.collect()
                QApplication.processEvents()
                _t.sleep(0.08 * (attempt + 1))
                last_err = ("perm", e)
            except OSError as e:
                if getattr(e, "winerror", None) in (32, 33):
                    import gc
                    gc.collect()
                    QApplication.processEvents()
                    _t.sleep(0.08 * (attempt + 1))
                    last_err = ("perm", e)
                else:
                    return ("other", e)
        return last_err

    def _offer_force_unlock_and_retry(self, op_fn) -> bool:

        if not AppDialog.confirm(
            self.window(),
            t("lock_force_title"),
            t("lock_force_msg"),
            yes_label=t("lock_force_yes"),
            no_label=t("lock_force_no"),
        ):
            return False

        from core.lock_recovery import force_release_managed_tools
        force_release_managed_tools(only_children=True)

        err = self._retry_fs_op(op_fn, attempts=3)
        if err is None:
            return True

        AppDialog.warning(self.window(), t("lock_force_failed_title"), t("lock_force_failed_msg"))
        return False

    def _setup_scrollbar_hover(self):
        from PySide6.QtCore import QObject, QEvent as _QEv
        sb = self.verticalScrollBar()

        class _SBHoverFilter(QObject):
            def __init__(self, tree):
                super().__init__(tree)
                self._tree = tree
            def eventFilter(self, obj, ev):
                if ev.type() == _QEv.Enter:
                    self._tree._sb_hovered = True
                    self._tree._sb_hide_timer.stop()
                    if not self._tree._sb_visible:
                        self._tree._show_scrollbar()
                elif ev.type() == _QEv.Leave:
                    self._tree._sb_hovered = False
                    if self._tree._sb_visible:
                        self._tree._sb_hide_timer.start()
                return False

        self._sb_hover_filter = _SBHoverFilter(self)
        sb.installEventFilter(self._sb_hover_filter)

    def _setup_scrollbar_hover_h(self):
        from PySide6.QtCore import QObject, QEvent as _QEv
        sb = self.horizontalScrollBar()

        class _SBHoverFilterH(QObject):
            def __init__(self, tree):
                super().__init__(tree)
                self._tree = tree
            def eventFilter(self, obj, ev):
                if ev.type() == _QEv.Enter:
                    self._tree._sbh_hovered = True
                    self._tree._sbh_hide_timer.stop()
                    if not self._tree._sbh_visible:
                        self._tree._show_scrollbar_h()
                elif ev.type() == _QEv.Leave:
                    self._tree._sbh_hovered = False
                    if self._tree._sbh_visible:
                        self._tree._sbh_hide_timer.start()
                return False

        self._sbh_hover_filter = _SBHoverFilterH(self)
        sb.installEventFilter(self._sbh_hover_filter)

    def _show_scrollbar(self):


        if not self._sb_visible:
            sb = self.verticalScrollBar()
            sb.setStyleSheet(f"""
                QScrollBar:vertical {{
                    background: transparent; width: 4px; margin: 0;
                }}
                QScrollBar::handle:vertical {{
                    background: {_th.border_color(0.35)};
                    border-radius: 2px; min-height: 20px;
                }}
                QScrollBar::handle:vertical:hover {{
                    background: {_th.border_color(0.55)};
                }}
                QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
                QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
            """)
            self._sb_visible = True
        if not self._sb_hovered:
            self._sb_hide_timer.start()

    def _hide_scrollbar(self):
        sb = self.verticalScrollBar()
        sb.setStyleSheet("""
            QScrollBar:vertical { background: transparent; width: 4px; margin: 0; }
            QScrollBar::handle:vertical { background: transparent; min-height: 20px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
        """)
        self._sb_visible = False

    def _show_scrollbar_h(self):
        # Sama seperti _show_scrollbar(): skip re-polish kalau udah visible,
        # timer tetap di-reset tiap panggilan.
        if not self._sbh_visible:
            sb = self.horizontalScrollBar()
            sb.setStyleSheet(f"""
                QScrollBar:horizontal {{
                    background: transparent; height: 4px; margin: 0;
                }}
                QScrollBar::handle:horizontal {{
                    background: {_th.border_color(0.35)};
                    border-radius: 2px; min-width: 20px;
                }}
                QScrollBar::handle:horizontal:hover {{
                    background: {_th.border_color(0.55)};
                }}
                QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
                QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}
            """)
            self._sbh_visible = True
        if not self._sbh_hovered:
            self._sbh_hide_timer.start()

    def _hide_scrollbar_h(self):
        sb = self.horizontalScrollBar()
        sb.setStyleSheet("""
            QScrollBar:horizontal { background: transparent; height: 4px; margin: 0; }
            QScrollBar::handle:horizontal { background: transparent; min-width: 20px; }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
            QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: transparent; }
        """)
        self._sbh_visible = False

    def scrollContentsBy(self, dx, dy):
        if (hasattr(self, "_badge_delegate") and abs(dy) > max(1, self.viewport().height() // 4)):
            # Large jumps mostly expose unseen cells. Avoid allocating/rasterizing
            # cache tiles that would be evicted immediately; keep existing hits.
            self._badge_delegate._cache_new_cells = False
            self._cell_warm_timer.start()
        super().scrollContentsBy(dx, dy)
        if dy != 0:
            self._show_scrollbar()
        if dx != 0:
            self._show_scrollbar_h()

    def _stop_wheel_scroll(self, *_args):
        self._wheel_animation.stop()
        self._wheel_target = self.verticalScrollBar().value()

    def wheelEvent(self, event):
        # Trackpads already provide continuous pixel motion. Native horizontal,
        # modified and animation-disabled wheel handling retains Qt semantics.
        if (not event.pixelDelta().isNull() or event.angleDelta().x()
                or event.modifiers() != Qt.NoModifier or not _th.animations_enabled):
            self._stop_wheel_scroll()
            return super().wheelEvent(event)
        delta = event.angleDelta().y()
        if not delta:
            return super().wheelEvent(event)
        bar = self.verticalScrollBar()
        if self._wheel_animation.state() != QPropertyAnimation.Running:
            self._wheel_target = bar.value()
        rows = QApplication.wheelScrollLines()
        if rows <= 0:
            return super().wheelEvent(event)
        target = round(self._wheel_target - delta / 120 * rows * max(1, bar.singleStep()))
        self._wheel_target = max(bar.minimum(), min(bar.maximum(), target))
        self._wheel_animation.stop()
        self._wheel_animation.setStartValue(bar.value())
        self._wheel_animation.setEndValue(self._wheel_target)
        if self._wheel_target != bar.value():
            self._wheel_animation.start()
        else:
            return super().wheelEvent(event)
        event.accept()

    def populate_root(self):
        if not self.base:
            return
        self._reset_directory_loading()
        self.setRootIndex(QModelIndex())
        self.clear()
        self._has_apktool_dec_dir = None


        try:
            self._ensure_mounts().clear()
            self._ensure_mounts_meta().clear()
        except Exception:
            pass

        def _after_render():


            if getattr(self, "_skip_initial_reload", False):
                self._skip_initial_reload = False


                try:
                    self.nb_refresh_all_dots()
                except Exception:
                    pass
                return
            try:
                self.reload_config_cache()
            except Exception:
                pass


        if self.display_mode == "single_dir":


            self.setIndentation(8)
            self._sd_reset_to(self.base)
            QTimer.singleShot(50, self, _after_render)
            return

        self.setIndentation(20)

        # ROOT ITEM
        root_name = os.path.basename(os.path.normpath(self.base))

        item = QTreeWidgetItem([root_name, "", ""])
        item.setData(0, Qt.UserRole, self.base)
        item.setData(0, IS_FOLDER_ROLE, True)
        item.setData(0, SIZE_LABEL_ROLE, "")

        item.setIcon(0, self.icon_root)

        # pastikan root selalu tampilkan indikator (panah)
        item.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)

        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
        self.addTopLevelItem(item)
        self.setRootIndex(self.indexFromItem(item))

        # root expandable
        self.folder_has_child[os.path.normpath(self.base)] = True

        if self.folder_has_child.get(os.path.normpath(self.base), False):
            self.add_loading(item)

        # AUTO-EXPAND ROOT
        def _after_expand():
            self.expandItem(item)
            self.on_expand(item)

        QTimer.singleShot(0, self, _after_expand)
        QTimer.singleShot(50, self, _after_render)


    _SD_ROLES = (
        Qt.UserRole,        # path fisik (atau "mount://..." virtual)
        Qt.UserRole + 20,   # branch_abs (NeoBranch branch-only overlay)
        Qt.UserRole + 50,   # symlink target
        Qt.UserRole + 51,   # symlink exists
        Qt.UserRole + 52,   # mount: symlink nembus ke path fisik disk
        Qt.UserRole + 53,   # mount: raw symlink target
        Qt.UserRole + 54,   # mount: (img_path, internal) asal symlink
        Qt.UserRole + 60,   # MOUNT_ROLE — (img_path, internal) mount aktif
        Qt.UserRole + 61,   # mount: cache flag editable
        Qt.UserRole + 71,   # APKTOOL_LINK_ROLE — mirror dir apk ter-decompile
    )
    _SD_UP_ROLE = Qt.UserRole + 70  # marker baris ".." (naik satu level)

    def _sd_state_from_item(self, item):
        return {role: item.data(0, role) for role in self._SD_ROLES}

    def _sd_apply_state(self, item, state):
        for role in self._SD_ROLES:
            item.setData(0, role, state.get(role))

    def _sd_render(self, state):

        root_item = self.invisibleRootItem()
        self._reset_directory_loading()
        root_item.takeChildren()
        self._sd_apply_state(root_item, state)
        self.on_expand(root_item)
        if self._sd_stack:
            up_item = QTreeWidgetItem(["..", "", ""])
            up_item.setData(0, self._SD_UP_ROLE, True)
            up_item.setFlags(up_item.flags() & ~Qt.ItemIsEditable)
            up_item.setChildIndicatorPolicy(QTreeWidgetItem.DontShowIndicator)
            up_item.setIcon(0, self.icon_root)
            root_item.insertChild(0, up_item)

    def _sd_reset_to(self, path):

        self._sd_stack = []
        self._sd_render({Qt.UserRole: path})

    def _sd_navigate_into(self, item):

        self._sd_stack.append(self._sd_state_from_item(self.invisibleRootItem()))
        self._sd_render(self._sd_state_from_item(item))

    def _sd_navigate_up(self):

        if not self._sd_stack:
            return
        self._sd_render(self._sd_stack.pop())

    @staticmethod
    def link_columns(*trees):

        trees = [t for t in trees if t is not None]
        for t in trees:
            t._column_sync_peers = [x for x in trees if x is not t]
        if trees:
            first = trees[0]
            w0 = first.header().sectionSize(0)
            w2 = first.header().sectionSize(2)
            for t in trees[1:]:
                t._syncing_columns = True
                try:
                    t.header().resizeSection(0, w0)
                    t.header().resizeSection(2, w2)
                finally:
                    t._syncing_columns = False

    def _on_header_section_resized(self, logical_index, old_size, new_size):
        # Cuma kolom Interactive (0=Name, 2=Permission) yang relevan — kolom 1
        # (Selinux) Stretch, lebarnya ngikut sisa ruang tiap widget sendiri.
        if logical_index not in (0, 2) or self._syncing_columns:
            return
        for peer in self._column_sync_peers:
            try:
                if peer.header().sectionSize(logical_index) == new_size:
                    continue
                peer._syncing_columns = True
                peer.header().resizeSection(logical_index, new_size)
            except Exception:
                pass
            finally:
                try:
                    peer._syncing_columns = False
                except Exception:
                    pass

    def set_display_mode(self, mode: str):

        mode = "single_dir" if mode == "single_dir" else "tree"
        if self.display_mode == mode:
            return
        self.display_mode = mode
        self._sd_stack = []
        if self.base:
            self.populate_root()

    def set_show_apktool_dec(self, enabled: bool, *, refresh: bool = True):

        enabled = bool(enabled)
        if self.show_apktool_dec == enabled:
            return
        self.show_apktool_dec = enabled
        if refresh and self.base:
            self.populate_root()

    def should_hide_entry(self, name: str, *, is_dir: bool, is_root_level: bool) -> bool:
        return is_filetree_entry_hidden(
            name,
            is_dir=is_dir,
            is_root_level=is_root_level,
            show_apktool_dec=self.show_apktool_dec,
        )


    def add_loading(self, item):
        dummy = QTreeWidgetItem(["Empty", "", ""])
        dummy.setFlags(dummy.flags() & ~Qt.ItemIsEditable)

        # italic + 50% transparan
        font = dummy.font(0)
        font.setItalic(True)
        dummy.setFont(0, font)

        dummy.setForeground(0, QColor(200, 200, 200, 130))  # abu-abu soft transparan
        dummy.setData(0, Qt.UserRole + 121, True)

        item.addChild(dummy)

    def _nb_merged_entries(self, source_path: str) -> list:

        try:
            nb = self._get_nb_manager()
            if not nb:
                return []
            if os.path.normcase(os.path.normpath(source_path)) == os.path.normcase(os.path.normpath(self.base)):
                # A partition can exist only in its active branch. Surface its
                # logical root in the GUI just like ActiveProjectView.list_dir.
                extras = []
                store = os.path.join(self.base, NB_STORE_DIR)
                if os.path.isdir(store):
                    for fs_root in sorted(os.listdir(store)):
                        logical = os.path.join(self.base, fs_root)
                        if os.path.lexists(logical):
                            continue
                        active = nb.get_active_branch(fs_root)
                        if active:
                            physical = nb._branch_fs_dir(fs_root, active["name"])
                            if os.path.isdir(physical):
                                extras.append((fs_root, logical, True, physical))
                return extras

            rel_dir = os.path.relpath(source_path, self.base).replace("\\", "/")
            fs_root = rel_dir.split("/")[0]
            active = nb.get_active_branch(fs_root)
            if not active:
                return []

            branch_name = active["name"]
            # branch fs dir: neobranch/<fs_root>/branch_<n>/<fs_root>/
            branch_fs_dir = nb._branch_fs_dir(fs_root, branch_name)
            # path equivalen di branch untuk folder ini
            # rel_dir = "vendor/bin" → branch path = branch_fs_dir/../bin
            rel_inside_fs = "/".join(rel_dir.split("/")[1:])  # strip fs_root prefix
            branch_dir_path = os.path.join(branch_fs_dir, rel_inside_fs) if rel_inside_fs else branch_fs_dir

            if not os.path.isdir(branch_dir_path):
                return []

            # Nama file yang ada di source
            try:
                source_names = set(os.listdir(source_path))
            except Exception:
                source_names = set()

            # Nama file yang ada di branch dir
            try:
                branch_names = set(os.listdir(branch_dir_path))
            except Exception:
                branch_names = set()


            branch_only = set(branch_names - source_names)
            for name in branch_names & source_names:
                source_equiv = os.path.join(source_path, name)
                try:
                    if nb.is_deleted_in_branch(
                        fs_root,
                        branch_name,
                        os.path.relpath(source_equiv, self.base).replace("\\", "/"),
                    ):
                        branch_only.add(name)
                except Exception:
                    pass

            extra = []
            for name in branch_only:
                # Source path equivalent (belum tentu ada di disk, tapi representasi logisnya)
                source_equiv = os.path.join(source_path, name)
                branch_abs   = os.path.join(branch_dir_path, name)
                is_dir = os.path.isdir(branch_abs)
                extra.append((name, source_equiv, is_dir, branch_abs))

            return extra

        except Exception as _e:
            print(f"[NeoBranch] _nb_merged_entries error: {_e}")
            return []

    def on_expand(self, item):
        if self._background_stopped:
            return


        if self.is_mount_node(item):
            # Dynamic re-resolution in case target partition image was mounted later
            raw_target = item.data(0, Qt.UserRole + 53)
            orig_ref = item.data(0, Qt.UserRole + 54) or self.mount_ref(item)
            if raw_target and orig_ref and hasattr(self, "_resolve_symlink_target_for_mount"):
                orig_img, orig_internal = orig_ref
                sym_virt, sym_phys, is_dir_sym, sym_exists = self._resolve_symlink_target_for_mount(orig_img, orig_internal, raw_target)
                if sym_virt:
                    item.setData(0, Qt.UserRole + 50, sym_virt)
                    item.setData(0, Qt.UserRole + 51, sym_exists)
                    item.setData(0, Qt.UserRole + 52, False)
                    item.setData(0, MOUNT_ROLE, (sym_virt[0], sym_virt[1]))
                elif sym_phys:
                    item.setData(0, Qt.UserRole + 50, sym_phys)
                    item.setData(0, Qt.UserRole + 51, sym_exists)
                    item.setData(0, Qt.UserRole + 52, True)

            phys_target = item.data(0, Qt.UserRole + 52)
            if not phys_target:
                self.expand_mount_node(item)
                return

        self._request_directory(item)

    def _build_single_tree_item(self, entry_tuple, is_root_parent, is_branch_only_dir, _nb_is_deleted, size_labels=None, *, entry_info=None, defer_file_icon=False):
        priority, _, full, is_dir_entry, symlink_data, source_full, branch_only_abs = entry_tuple

        # NeoBranch: skip items deleted in active branch
        if _nb_is_deleted and not branch_only_abs:
            try:
                if _nb_is_deleted(source_full):
                    return None
            except Exception:
                pass


        if is_dir_entry or (entry_info is None and branch_only_abs and os.path.isdir(branch_only_abs)):
            base_name, ctx, perm, ctx_src = self.get_meta(source_full)

            if entry_info is not None:
                fs_type = entry_info["fs_type"] or self._unpack_watcher.get_fs_type(source_full)
            elif is_root_parent:
                rel = os.path.relpath(source_full, self.base).replace("\\", "/")
                root_name = rel.split("/")[0]
                expected_root_path = os.path.join(self.base, root_name)
                if os.path.normpath(source_full) == os.path.normpath(expected_root_path):
                    fs_type = detect_fs_root(self.base, root_name)
                    if not fs_type and hasattr(self, "_unpack_watcher"):
                        fs_type = self._unpack_watcher.get_fs_type(source_full)
                else:
                    fs_type = ""
            else:
                fs_type = ""

            dt_target = entry_info["collapse"] if entry_info is not None else self._dtgen_collapse_target(full)
            if dt_target:
                child = QTreeWidgetItem([base_name, ctx, perm])
                child.setData(0, IS_FOLDER_ROLE, True)
                child.setData(0, Qt.UserRole, dt_target)
                child.setData(0, SIZE_LABEL_ROLE, "")
                child.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
                child.setIcon(0, self._resolve_folder_icon(dt_target, ""))
                child.setFlags(child.flags() & ~Qt.ItemIsEditable)
                if entry_info is None:
                    self.add_loading(child)
                return child

            child = QTreeWidgetItem([base_name, ctx, perm])
            child.setData(0, IS_FOLDER_ROLE, True)
            child.setData(0, Qt.UserRole, source_full)
            child.setData(0, SIZE_LABEL_ROLE, "")
            if branch_only_abs or is_branch_only_dir:
                child.setData(0, Qt.UserRole + 20, branch_only_abs or full)

            if source_full in self.superdetect_cache:
                badge = self.superdetect_cache.get(source_full)
                if badge in self.NON_FS_BADGE:
                    child.setData(0, UI_STABLE_ROLE, True)

            child.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
            phys_dir = branch_only_abs if branch_only_abs else (full if not is_branch_only_dir else full)
            child.setIcon(0, self._resolve_folder_icon(phys_dir, fs_type))

            if fs_type in ("EROFS", "EXT4", "EXT3", "EXT2") or fs_type in self.NON_FS_BADGE:
                child.setData(0, Qt.UserRole + 1, fs_type)
                if fs_type in self.NON_FS_BADGE:
                    child.setData(0, UI_STABLE_ROLE, True)

            self._apply_ctx_color(child, ctx_src)
            child.setFlags(child.flags() & ~Qt.ItemIsEditable)
            for col in (1, 2):
                child.setFlags(child.flags() | Qt.ItemIsEditable)

            if entry_info is None:
                self.add_loading(child)
            return child


        if symlink_data:
            target_path = symlink_data.get("resolved", "")
            is_dir_symlink = symlink_data.get("is_dir", False)
            symlink_exists = symlink_data.get("exists", False)
            base_name, ctx, perm, ctx_src = self.get_meta(source_full)

            child = QTreeWidgetItem([base_name, ctx, perm])
            child.setData(0, IS_FOLDER_ROLE, is_dir_symlink)
            child.setData(0, Qt.UserRole, source_full)
            child.setData(0, Qt.UserRole + 50, target_path)
            child.setData(0, Qt.UserRole + 51, symlink_exists)
            if branch_only_abs or is_branch_only_dir:
                child.setData(0, Qt.UserRole + 20, branch_only_abs or full)

            if is_dir_symlink:
                child.setData(0, SIZE_LABEL_ROLE, "")
                child.setIcon(0, self.icon_symlink)
                child.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
                if entry_info is None:
                    self.add_loading(child)
            else:
                if defer_file_icon:
                    child.setIcon(0, self.icon_file)
                    child.setData(0, Qt.UserRole + 120, True)
                else:
                    child.setIcon(0, self.icon_symlink_file)
                    child.setData(0, Qt.UserRole + 120, False)
                if size_labels and source_full in size_labels:
                    child.setData(0, SIZE_LABEL_ROLE, size_labels[source_full])

            self._apply_ctx_color(child, ctx_src)
            child.setFlags(child.flags() & ~Qt.ItemIsEditable)
            for col in (1, 2):
                child.setFlags(child.flags() | Qt.ItemIsEditable)
            return child


        base_name, ctx, perm, ctx_src = self.get_meta(source_full)

        fs_info = ""
        phys = branch_only_abs if branch_only_abs else full
        _low_phys = phys.lower()
        _is_badgeable = (
            _low_phys.endswith(".img") or _low_phys.endswith(".img.br")
            or _low_phys.endswith(".new.dat") or _low_phys.endswith(".new.dat.br")
        )
        _dropped_paths = getattr(self, "_dropped_badge_paths", None)
        _dropped_badge = bool(_dropped_paths) and os.path.normpath(phys) in _dropped_paths
        if entry_info is not None:
            fs_info = entry_info["badge"]
        elif _low_phys.endswith(".bin") and self._detect_magic_once(phys) == "PAYLOAD":
            fs_info = "PAYLOAD"
        elif _is_badgeable or _dropped_badge:
            if source_full in self.superdetect_cache:
                badge = self.superdetect_cache[source_full]
            elif phys in self.superdetect_cache:
                badge = self.superdetect_cache[phys]
            elif _is_badgeable:
                badge = self.get_image_badge(phys)
            else:
                badge = self._detect_magic_once(phys)

            if badge in self.NON_FS_BADGE:
                fs_info = badge
            elif badge in ("BR", "DAT", "DAT.BR"):
                fs_info = badge
            else:
                fs_type = self._check_img_cache(phys)
                if fs_type:
                    fs_info = fs_type

        child = QTreeWidgetItem([base_name, ctx, perm])
        child.setData(0, IS_FOLDER_ROLE, False)
        child.setData(0, Qt.UserRole, source_full)
        if branch_only_abs or is_branch_only_dir:
            child.setData(0, Qt.UserRole + 20, branch_only_abs or full)


        _size_lbl = size_labels.get(source_full) if size_labels else None
        if _size_lbl is None and branch_only_abs and entry_info is None:
            try:
                _size_lbl = fmt_size(os.path.getsize(branch_only_abs))
            except Exception:
                _size_lbl = None
        if _size_lbl is not None:
            child.setData(0, SIZE_LABEL_ROLE, _size_lbl)

        if defer_file_icon:
            child.setIcon(0, self.icon_file)
            child.setData(0, Qt.UserRole + 120, True)
        else:
            child.setIcon(0, self._resolve_file_icon(phys))
            self._schedule_file_thumbnail(child, phys, entry_info=entry_info)
            child.setData(0, Qt.UserRole + 120, False)

        if fs_info in ("EROFS", "EXT4", "EXT3", "EXT2", "PAYLOAD"):
            child.setData(0, Qt.UserRole + 1, fs_info)
        elif fs_info in self.NON_FS_BADGE or fs_info in ("BR", "DAT", "DAT.BR"):
            child.setData(0, Qt.UserRole + 1, fs_info)
            child.setData(0, UI_STABLE_ROLE, True)

        self._apply_ctx_color(child, ctx_src)
        child.setFlags(child.flags() & ~Qt.ItemIsEditable)
        for col in (1, 2):
            child.setFlags(child.flags() | Qt.ItemIsEditable)

        if getattr(self, "_mounts", None):
            self.reattach_mount_item(child, source_full)
        if child.data(0, Qt.UserRole + 60):
            child.setData(0, Qt.UserRole + 120, False)

        # 🔥 APK sudah di-decompile → tampil kayak folder symlink jika mode "folder",
        # atau tetap file biasa jika user switch ke mode "apk".
        if source_full.lower().endswith(".apk"):
            if entry_info is not None:
                mirror = entry_info["apk_mirror"]
            else:
                mirror = apktool_tool.mirror_dir_for(self.base, source_full) if apktool_tool.is_decompiled(self.base, source_full) else None
            if mirror:
                if self.is_apk_folder_mode(source_full):
                    child.setData(0, APKTOOL_LINK_ROLE, mirror)
                    child.setIcon(0, self.icon_apk_linked)
                    child.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
                    if entry_info is None:
                        self.add_loading(child)
                    child.setData(0, Qt.UserRole + 120, False)
                else:
                    child.setData(0, APKTOOL_LINK_ROLE, None)
                    child.setIcon(0, self.icon_apk)
                    child.setChildIndicatorPolicy(QTreeWidgetItem.DontShowIndicator)
                    child.setData(0, Qt.UserRole + 120, False)

        return child

    def _get_apk_view_modes(self):
        if not hasattr(self, "_apk_view_modes_cache"):
            self._apk_view_modes_cache = {}
            if self.base:
                cfg_path = os.path.join(self.base, apktool_tool.APKTOOL_DEC_DIR, ".view_modes.json")
                if os.path.isfile(cfg_path):
                    try:
                        with open(cfg_path, "r", encoding="utf-8") as f:
                            self._apk_view_modes_cache = json.load(f)
                    except Exception:
                        pass
        return self._apk_view_modes_cache

    def is_apk_folder_mode(self, apk_path):

        modes = self._get_apk_view_modes()
        if not self.base:
            return True
        try:
            rel = os.path.relpath(os.path.normpath(apk_path), os.path.normpath(self.base)).replace("\\", "/")
        except Exception:
            rel = apk_path
        return modes.get(rel, "folder") != "apk"

    def set_apk_view_mode(self, apk_path, mode: str):
        modes = self._get_apk_view_modes()
        if not self.base:
            return
        try:
            rel = os.path.relpath(os.path.normpath(apk_path), os.path.normpath(self.base)).replace("\\", "/")
        except Exception:
            rel = apk_path
        modes[rel] = mode
        dec_dir = os.path.join(self.base, apktool_tool.APKTOOL_DEC_DIR)
        if os.path.isdir(dec_dir):
            cfg_path = os.path.join(dec_dir, ".view_modes.json")
            try:
                with open(cfg_path, "w", encoding="utf-8") as f:
                    json.dump(modes, f, indent=2)
            except Exception:
                pass

    def mark_apk_decompiled(self, apk_path, item=None):

        self._has_apktool_dec_dir = True
        try:
            if (item is None or item.treeWidget() is not self or
                    os.path.normpath(item.data(0, Qt.UserRole)) != os.path.normpath(apk_path)):
                item = None
        except (RuntimeError, TypeError):
            item = None
        if item is None:
            item = self.find_item_by_path(self.invisibleRootItem(), apk_path)
        if item is None:
            return False
        if self.is_apk_folder_mode(apk_path):
            item.setData(0, APKTOOL_LINK_ROLE,
                         apktool_tool.mirror_dir_for(self.base, apk_path))
            item.setIcon(0, self.icon_apk_linked)
            item.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
            if item.childCount() == 0:
                self.add_loading(item)
        else:
            item.setData(0, APKTOOL_LINK_ROLE, None)
            item.setIcon(0, self.icon_apk)
            item.setChildIndicatorPolicy(QTreeWidgetItem.DontShowIndicator)
        self.viewport().update(self.visualItemRect(item))
        return True

    @staticmethod
    def _load_neocat_ctx_dir(cache_dir):
        neocat = {}
        if not os.path.isdir(cache_dir):
            return neocat
        for fname in os.listdir(cache_dir):
            if not fname.endswith("_file_contexts"):
                continue
            fpath = os.path.join(cache_dir, fname)
            with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                for ln in f:
                    s = ln.strip()
                    if not s or s.startswith("#"):
                        continue
                    parts = s.split()
                    if len(parts) < 2:
                        continue
                    neocat[parts[0]] = parts[1]
        return neocat

    @staticmethod
    def _load_neocat_ctx(base):

        cache_dir = neocat_cache_dir(base)
        neocat = FileTree._load_neocat_ctx_dir(cache_dir)
        print(f"[NEOCAT] loaded {len(neocat)} AppData cache entries for {os.path.basename(base)}")
        return neocat

    @staticmethod
    def _apply_ctx_color(child, ctx_src):

        if ctx_src == "neocat":
            child.setForeground(1, QColor(140, 220, 140))

    def get_meta(self, full):
        if full.startswith(self.base):
            rel = full[len(self.base):].lstrip("\\/").replace("\\", "/")
        else:
            rel = os.path.relpath(full, self.base).replace("\\", "/")

        from core.boot_image import is_boot_ramdisk_rel
        boot_ramdisk = is_boot_ramdisk_rel(self.base, rel)
        raw_ctx_key = self.engine.escaped_ctx_key(rel)

        # config menang kalau keduanya punya slot di titik yang sama
        # neocat hanya isi slot yang kosong di config
        if boot_ramdisk:
            # CPIO has no security.selinux field. A kitchen-side context entry
            # is not an on-device label and must never be displayed as one.
            ctx_val = ""
            ctx_src = ""
        elif raw_ctx_key in self.ctx:
            ctx_val = self.ctx[raw_ctx_key]
            ctx_src = "config"
        elif raw_ctx_key in self.ctx_neocat:
            ctx_val = self.ctx_neocat[raw_ctx_key]
            ctx_src = "neocat"
        else:
            ctx_val = ""
            ctx_src = ""

        raw_fs_key = rel
        perm_raw = self.fs.get(raw_fs_key, "")
        if perm_raw == "":
            perm_raw = ""

        perm = perm_to_rwx(perm_raw) if perm_raw else ""

        return os.path.basename(full), ctx_val, perm, ctx_src

    @staticmethod
    def _has_transfer_list(filepath):
        low = filepath.lower()
        if low.endswith(".new.dat.br"):
            prefix = filepath[:-len(".new.dat.br")]
        elif low.endswith(".new.dat"):
            prefix = filepath[:-len(".new.dat")]
        else:
            return False
        tl = prefix + ".transfer.list"
        return os.path.isfile(tl)

    def get_image_badge(self, full):
        low = full.lower()
        if low.endswith(".img.br"):
            try:
                if os.path.getsize(full) == 0:
                    return ""
            except OSError:
                return ""
            return "BR"
        if low.endswith(".new.dat.br"):
            if self._has_transfer_list(full):
                return "DAT.BR"
            return ""
        if low.endswith(".new.dat"):
            if self._has_transfer_list(full):
                return "DAT"
            return ""
        if not low.endswith(".img"):
            return ""
        magic = self._detect_magic_once(full)
        if magic in self.NON_FS_BADGE:
            return magic
        return ""

    def _mount_edit_selinux(self, item):

        if getattr(self, "_in_mount_edit", False):
            return
        ref = self.mount_ref(item)
        if not ref:
            return
        img_path, internal = ref
        new_ctx = item.text(1)
        from core import imgfs
        from main_ext.class_AppDialog import AppDialog
        self._in_mount_edit = True
        try:
            imgfs.set_selinux(img_path, internal, new_ctx)
            self.patch_mount_entry(img_path, internal, selinux=new_ctx)
        except (ValueError, NotImplementedError, Exception) as ex:
            # revert tampilan ke nilai lama dari index
            old = self._mount_field(img_path, internal, "selinux")
            if old is not None:
                item.setText(1, old)
            try:
                AppDialog.warning(self.window(), t("rc_invalid_name_title") if False else "Tidak bisa simpan SELinux", str(ex))
            except Exception:
                print("[MOUNT] set_selinux gagal:", ex)
        finally:
            self._in_mount_edit = False

    def _mount_edit_perm(self, item, ref):

        from main_ext.class_PermDialog import PermDialog
        from main_ext.class_ConfigEngine import perm_to_rwx
        from core import imgfs
        from main_ext.class_AppDialog import AppDialog
        img_path, internal = ref
        from app_ext.class_BlurOverlay import BlurOverlay as _BlurOverlay, install_window_fade
        main_win = self.window()
        dlg = PermDialog(item.text(2), parent=main_win)
        ov = _BlurOverlay(main_win) if main_win else None
        if ov:
            dlg._overlay = ov
        install_window_fade(dlg)
        accepted = dlg.exec()
        if ov:
            ov.close_overlay()
        if not accepted:
            return
        octal = dlg.get_octal()
        try:
            imgfs.set_perm(img_path, internal, octal)
            item.setText(2, perm_to_rwx(octal))
            self.patch_mount_entry(img_path, internal, perm=perm_to_rwx(octal))
        except (ValueError, NotImplementedError, Exception) as ex:
            try:
                AppDialog.warning(self.window(), "Tidak bisa simpan Permission", str(ex))
            except Exception:
                print("[MOUNT] set_perm gagal:", ex)

    def _mount_field(self, img_path, internal, field):

        try:
            key = os.path.normpath(img_path)
            index = self._mounts.get(key, {})
            internal = internal.strip("/").replace("\\", "/")
            parent = internal.rsplit("/", 1)[0] if "/" in internal else ""
            name = internal.rsplit("/", 1)[-1]
            for c in index.get(parent, []):
                if c["name"] == name:
                    return c.get(field)
        except Exception:
            pass
        return None

    def on_item_edited(self, item, col):
        if self._background_stopped:
            return
        # ⛔ JANGAN GANGGU RENAME
        if self._renaming:
            return

        # ── Mount node (virtual): tulis selinux ke IMAGE, bukan config disk ──
        if self.is_mount_node(item):
            if col == 1:
                self._mount_edit_selinux(item)
            return

        full = item.data(0, Qt.UserRole)
        if not full:
            return

        if col != 1:
            # kolom 2 (perm) di-handle terpisah; kolom lain diabaikan.
            return

        # itemChanged also fires for programmatic text/colour updates during
        # config reload. An unchanged cached value is not a user edit.
        if self.engine is None:
            return
        rel = os.path.relpath(full, self.base).replace("\\", "/")
        raw_key = self.engine.escaped_ctx_key(rel)
        expected = self.ctx.get(raw_key, self.ctx_neocat.get(raw_key, ""))
        if item.text(1) == expected:
            return

        rel = os.path.relpath(full, self.base).replace("\\", "/")
        from core.boot_image import is_boot_ramdisk_rel
        if is_boot_ramdisk_rel(self.base, rel):
            previous = self.blockSignals(True)
            try:
                item.setText(1, "")
            finally:
                self.blockSignals(previous)
            QMessageBox.information(
                self, "Boot ramdisk SELinux",
                "Label SELinux tidak disimpan per-file di CPIO boot/vendor_boot. "
                "Cek file_contexts yang benar-benar dimuat: bisa ada di ramdisk "
                "(recovery) atau partisi system/vendor. Jika file masih ada "
                "saat SELinux aktif, pastikan restorecon mencakup path itu."
            )
            return


        if getattr(self, "_in_ctx_edit", False):
            return

        val = item.text(col)
        rel = os.path.relpath(full, self.base).replace("\\", "/")
        QTimer.singleShot(0, self, lambda: self._commit_ctx_edit(rel, val))

    def _commit_ctx_edit(self, rel, val):
        if self._background_stopped or getattr(self, "_in_ctx_edit", False):
            return
        self._in_ctx_edit = True
        try:
            fs_root = rel.split("/")[0]
            raw_key = self.engine.escaped_ctx_key(rel)
            in_config = raw_key in self.ctx
            in_neocat   = raw_key in self.ctx_neocat

            if in_config:
                # KEY ADA DI CTX (bisa source atau branch, karena ctx sudah di-overlay)
                self.ctx[raw_key] = val
                self.edit_context_line(rel, val)

            elif in_neocat:
                # SLOT ADA DI neocat → edit neocat_config
                self.ctx_neocat[raw_key] = val
                self.edit_neocat_context_line(rel, val)

            else:
                # BELUM ADA DI MANAPUN → buat entry baru
                if fs_root and self.nb_is_active(fs_root):
                    # ── NeoBranch: buat di branch config, bukan source ──
                    branch_cfg = self.nb_get_live_config_dir(fs_root)
                    _nb_append_ctx_entry(branch_cfg, fs_root, raw_key)
                    # Reload hanya ctx dari branch agar tidak pollute source
                    self.ctx.update(_load_ctx_from_dir(branch_cfg))
                else:
                    # Source normal: pakai engine (tulis ke tree_base/config)
                    self.engine.apply_context_only(rel)
                    self.ctx = load_file_context(self.base)
                self.ctx[raw_key] = val
                self.edit_context_line(rel, val)
        finally:
            self._in_ctx_edit = False

    def edit_context_line(self, rel_path, new_ctx):
        # ── NeoBranch: route to branch config dir when branch is active ──
        fs_root = rel_path.split("/")[0] if rel_path else ""
        if fs_root and self.nb_is_active(fs_root):
            cfgdir = self.nb_get_live_config_dir(fs_root)
        else:
            cfgdir = os.path.join(self.base, "config")

        # raw escaped key
        raw_key = self.engine.escaped_ctx_key(rel_path)
        for fname in os.listdir(cfgdir):
            if not fname.endswith("_file_contexts"):
                continue

            full = os.path.join(cfgdir, fname)
            with open(full, "r", encoding="utf-8", errors="ignore") as f:
                lines = [ln.rstrip("\n") for ln in f]

            changed = False
            out = []

            for ln in lines:
                s = ln.strip()
                if not s or s.startswith("#"):
                    out.append(ln)
                    continue

                parts = s.split()
                key = parts[0]

                if key == raw_key:
                    if len(parts) >= 2:
                        parts[1] = new_ctx
                    else:
                        parts.append(new_ctx)
                    ln = " ".join(parts)
                    changed = True

                out.append(ln)

            if changed:
                with open(full, "w", encoding="utf-8", errors="ignore") as f:
                    f.write("\n".join(out) + "\n")

    def edit_neocat_context_line(self, rel_path, new_ctx):

        fs_root = rel_path.split("/")[0] if rel_path else ""
        neocat_dir = neocat_cache_dir(self.base)
        if fs_root and self.nb_is_active(fs_root):
            nb = self._get_nb_manager()
            active = nb.get_active_branch(fs_root) if nb else None
            if active:
                neocat_dir = nb._branch_neocat_config_dir(fs_root, active["name"])
        if not os.path.isdir(neocat_dir):
            return

        raw_key = self.engine.escaped_ctx_key(rel_path)
        for fname in os.listdir(neocat_dir):
            if not fname.endswith("_file_contexts"):
                continue

            full = os.path.join(neocat_dir, fname)
            with open(full, "r", encoding="utf-8", errors="ignore") as f:
                lines = [ln.rstrip("\n") for ln in f]

            changed = False
            out = []

            for ln in lines:
                s = ln.strip()
                if not s or s.startswith("#"):
                    out.append(ln)
                    continue

                parts = s.split()
                key = parts[0]

                if key == raw_key:
                    if len(parts) >= 2:
                        parts[1] = new_ctx
                    else:
                        parts.append(new_ctx)
                    ln = " ".join(parts)
                    changed = True

                out.append(ln)

            if changed:
                with open(full, "w", encoding="utf-8", errors="ignore") as f:
                    f.write("\n".join(out) + "\n")

    def edit_fs_line(self, rel_path, new_perm):
        # ── NeoBranch: route to branch config dir when branch is active ──
        fs_root = rel_path.split("/")[0] if rel_path else ""
        if fs_root and self.nb_is_active(fs_root):
            cfgdir = self.nb_get_live_config_dir(fs_root)
        else:
            cfgdir = os.path.join(self.base, "config")
        raw_key = rel_path

        for fname in os.listdir(cfgdir):
            if not fname.endswith("_fs_config"):
                continue

            full = os.path.join(cfgdir, fname)
            with open(full, "r", encoding="utf-8", errors="ignore") as f:
                lines = [ln.rstrip("\n") for ln in f]

            changed = False
            out = []

            for ln in lines:
                s = ln.strip()
                if not s or s.startswith("#"):
                    out.append(ln)
                    continue

                parts = s.split()
                if parts[0] == raw_key:
                    parts[-1] = new_perm
                    ln = " ".join(parts)
                    changed = True

                out.append(ln)

            if changed:
                with open(full, "w", encoding="utf-8", errors="ignore") as f:
                    f.write("\n".join(out) + "\n")

    def _save_selection(self):

        cur = self.currentItem()
        if not cur:
            return None
        return cur.data(0, Qt.UserRole)

    def _restore_selection(self, target_path):

        if not target_path:
            return
        root = self.topLevelItem(0)
        item = self.find_item_by_path(root, target_path)
        if item:
            self.setCurrentItem(item)

    def _safe_write(self, path, lines):
        bak = path + ".bak"
        try:
            shutil.copyfile(path, bak)
        except Exception:
            pass
        with open(path, "w", encoding="utf-8", errors="ignore") as f:
            f.write("\n".join(lines) + "\n")


    def mouseDoubleClickEvent(self, e):
        # Cuma tombol KIRI yang buka file/viewer. Klik kanan (termasuk 2x)
        # tidak buka apa-apa — context menu cukup lewat 1x klik kanan.
        if e.button() != Qt.LeftButton:
            return super().mouseDoubleClickEvent(e)

        item = self.itemAt(e.pos())
        if not item:
            return super().mouseDoubleClickEvent(e)

        # Single Dir mode: baris ".." (naik satu level)
        if item.data(0, self._SD_UP_ROLE):
            self._sd_navigate_up()
            return

        # Mount node (virtual)
        if self.is_mount_node(item):
            ref = self.mount_ref(item)
            col_m = self.indexAt(e.pos()).column()
            writable = self.mount_writable(ref[0]) if ref else False


            is_root = bool(ref and ref[1] == ""
                           and not item.data(0, Qt.UserRole + 53))
            is_folder = item.childIndicatorPolicy() == QTreeWidgetItem.ShowIndicator

            # Kolom 2 (Permission) editable → PermDialog (file & folder).
            if col_m == 2 and writable and not is_root:
                self._mount_edit_perm(item, ref)
                return


            if col_m == 1 and writable and not is_root:
                return super().mouseDoubleClickEvent(e)


            if is_root or is_folder:
                if self.display_mode == "single_dir":
                    self._sd_navigate_into(item)
                else:
                    item.setExpanded(not item.isExpanded())
                return

            # FILE, kolom 0 (atau read-only) → buka isi teks
            img_path, internal = ref
            from core.imgfs import is_text_filename
            if not is_text_filename(internal):
                win = self.window()
                if hasattr(win, "_toast_or_print"):
                    win._toast_or_print(f"'{os.path.basename(internal)}' bukan file teks — tidak dibuka")
                return

            ro = not writable
            win = self.window()
            if hasattr(win, "open_mount_file_in_editor"):
                win.open_mount_file_in_editor(img_path, internal, read_only=ro)
            return

        col = self.indexAt(e.pos()).column()
        full = item.data(0, Qt.UserRole)

        if not full:
            return super().mouseDoubleClickEvent(e)

        if col == 1:
            from core.boot_image import is_boot_ramdisk_rel
            rel = os.path.relpath(full, self.base).replace("\\", "/")
            if is_boot_ramdisk_rel(self.base, rel):
                QMessageBox.information(
                    self, "Boot ramdisk SELinux",
                    "Label SELinux tidak disimpan per-file di CPIO boot/vendor_boot. "
                    "Cek file_contexts yang benar-benar dimuat: bisa ada di ramdisk "
                    "(recovery) atau partisi system/vendor. Jika file masih ada "
                    "saat SELinux aktif, pastikan restorecon mencakup path itu."
                )
                return

        # ── NeoBranch: branch-only file tidak exist di source path.
        # Resolve ke branch_abs (UserRole+20) kalau source tidak ada.
        branch_abs = item.data(0, Qt.UserRole + 20)
        _phys = full  # path fisik yang benar-benar exist
        if not os.path.exists(full) and branch_abs and os.path.exists(branch_abs):
            _phys = branch_abs
        elif not os.path.exists(full):
            return super().mouseDoubleClickEvent(e)

        # Ambil ext dari nama tampilan item (bukan dari path fisik) —
        # supaya rename nama file (misal xx → xx.init) langsung kelihatan efeknya.
        ext = os.path.splitext(item.text(0))[1].lower()


        name_only = os.path.basename(full)
        if (
            name_only.endswith("_file_contexts") or
            name_only.endswith("_fs_config") or
            name_only.endswith("_fs_options") or
            name_only.endswith("_info")
        ):
            ext = ".linuxmeta"


        symlink_target = item.data(0, Qt.UserRole + 50) or item.data(0, APKTOOL_LINK_ROLE)

        # Redirect _phys to target if the symlink is pointing to a file
        if symlink_target and os.path.isfile(symlink_target):
            _phys = symlink_target
            ext = os.path.splitext(symlink_target)[1].lower()


        is_expandable_symlink = symlink_target and item.childIndicatorPolicy() == QTreeWidgetItem.ShowIndicator

        if (os.path.isdir(_phys) or is_expandable_symlink) and col == 0:
            if self.display_mode == "single_dir":
                self._sd_navigate_into(item)
            else:
                item.setExpanded(not item.isExpanded())
            return


        # 1. PAYLOAD .BIN (OTA payload b'CrAU')
        if os.path.isfile(_phys) and col == 0 and ext == ".bin":
            _magic = self._detect_magic_once(_phys)
            if _magic == "PAYLOAD":
                self.window().open_payload_tab(_phys)
                return

        # 1b. SHARED OBJECT (.SO / ELF)
        _fname_lower = os.path.basename(_phys).lower()
        if os.path.isfile(_phys) and col == 0 and (ext == ".so" or ".so." in _fname_lower or ext.startswith(".so")):
            self.window().open_so_tab(_phys)
            return

        # 1c. BINARY / FIRMWARE / HEX INSPECTOR (.bin, .fsm, .ccu, .blob)
        if os.path.isfile(_phys) and col == 0 and ext in (".bin", ".fsm", ".ccu", ".blob"):
            self.window().open_bin_tab(_phys)
            return

        # 2. ARCHIVE
        if os.path.isfile(_phys) and col == 0 and ext in FileTree.ARCHIVE_EXT:
            self.window().open_archive_tab(_phys)
            return

        # 3. IMAGE (png/jpg/svg/dll)
        from main_ext.class_ImageViewer import IMAGE_EXT
        if os.path.isfile(_phys) and col == 0 and ext in IMAGE_EXT:
            self.window().open_image_tab(_phys)
            return

        # 3b. AUDIO (mp3/wav/flac/dll)
        # Cek AUDIO dulu: ext yg overlap (.ogg/.3gp) diperlakukan sbg audio.
        if os.path.isfile(_phys) and col == 0 and ext in FileTree.AUDIO_EXT:
            self.window().open_audio_tab(_phys)
            return

        # 3c. VIDEO (mp4/mkv/avi/mov/webm/dll)
        if os.path.isfile(_phys) and col == 0 and ext in FileTree.VIDEO_EXT:
            self.window().open_video_tab(_phys)
            return

        # 4. TEXT FILE (editor)
        if os.path.isfile(_phys) and col == 0:

            # is_meta pakai nama item (yang sudah ke-rename) bukan path fisik
            name_only = item.text(0)

            is_meta = (
                name_only.endswith("_file_contexts") or
                name_only.endswith("_fs_config") or
                name_only.endswith("_fs_options") or
                name_only.endswith("_info")
            )

            if (
                ext in FileTree.TEXT_EXT or
                ext in (".json", ".jsonc") or
                ext in (".kt", ".kts") or
                ext == ".smali" or
                ext == ".rs" or
                ext in (".php", ".php3", ".php4", ".php5", ".phtml") or
                ext == ".qss" or
                ext in (".h", ".hpp", ".hxx", ".hh") or
                ext in (".sh", ".bash", ".zsh", ".fish", ".ksh", ".csh", ".tcsh", ".dash") or
                is_meta
            ):
                print(">>> OPENING TEXT:", _phys)
                # ── NeoBranch branch-only: buka langsung dari _phys (branch path).
                # ── Source file: resolve via nb_resolve_open seperti biasa.
                if _phys != full:
                    resolved = _phys
                else:
                    resolved = self.nb_resolve_open(full)
                self.window().open_file_in_editor(resolved, source_path=full, source_tree=self)
                return


            self._open_with_dialog(_phys, full)
            return


        # COLUMN PERMISSION
        if col == 2:
            rel = os.path.relpath(full, self.base).replace("\\", "/")

            from app_ext.class_BlurOverlay import BlurOverlay as _BlurOverlay, install_window_fade
            _win = self.window()
            dlg = PermDialog(item.text(2), parent=_win)
            _ov = _BlurOverlay(_win)
            dlg._overlay = _ov
            install_window_fade(dlg)
            result = dlg.exec()
            _ov.close_overlay()
            if result:
                new_perm = dlg.get_octal()

                # ENTRY BELUM ADA → PAKSA BUAT di tempat yang bener
                if rel not in self.fs:
                    fs_root = rel.split("/")[0]
                    if fs_root and self.nb_is_active(fs_root):
                        # Branch aktif → buat entry di branch config, BUKAN source
                        branch_cfgdir = self.nb_get_live_config_dir(fs_root)
                        _nb_append_fs_entry(branch_cfgdir, fs_root, rel, self.engine)
                        # Reload cache dari branch dir saja (overlay)
                        self.fs.update(_load_fs_from_dir(branch_cfgdir))
                    else:
                        # No branch → buat di source config seperti biasa
                        self.engine.apply_permission_only(rel)
                        self.fs = load_fs_config(self.base)

                self.fs[rel] = new_perm
                self.edit_fs_line(rel, new_perm)
                item.setText(2, perm_to_rwx(new_perm))
            return


        # 5. Default behaviour
        return super().mouseDoubleClickEvent(e)


    def _open_with_dialog(self, phys_path, source_path):

        def _get_assoc_path():
            path = os.path.join(USER_STATE_DIR, "file_associations.json")
            migrate_legacy_file(
                os.path.join(APP_ROOT, "file_associations.json"), path
            )
            os.makedirs(USER_STATE_DIR, exist_ok=True)
            return path

        def _load_assoc():
            p = _get_assoc_path()
            if os.path.exists(p):
                try:
                    with open(p, "r", encoding="utf-8") as f: return json.load(f)
                except: pass
            return {}

        _, ext = os.path.splitext(phys_path)
        ext = ext.lower()
        assoc = _load_assoc()
        choice = None

        if ext and ext in assoc:
            choice = assoc[ext]
        else:
            from main_ext.class_OpenWithDialog import OpenWithDialog
            res = OpenWithDialog.pick(self.window(), file_name=os.path.basename(phys_path))
            if not res or not res[0]:
                return
            choice, always = res
            if always and ext:
                assoc[ext] = choice
                try:
                    with open(_get_assoc_path(), "w", encoding="utf-8") as f: json.dump(assoc, f)
                except: pass

        if not choice:
            return
        main = self.window()
        if choice == "editor":
            if phys_path != source_path:
                resolved = phys_path
            else:
                resolved = self.nb_resolve_open(source_path)
            # force=True: user eksplisit milih editor untuk file tak dikenal,
            # bypass whitelist ext di open_file_in_editor.
            main.open_file_in_editor(resolved, force=True, source_path=source_path, source_tree=self)
        elif choice == "so":
            main.open_so_tab(phys_path)
        elif choice == "bin":
            main.open_bin_tab(phys_path)
        elif choice == "image":
            main.open_image_tab(phys_path)
        elif choice == "archive":
            main.open_archive_tab(phys_path)
        elif choice == "payload":
            main.open_payload_tab(phys_path)
        elif choice == "super":
            main.open_super_tab(phys_path)


    def _cancel_dummy(self):

        item = self._creating_item
        if item is None:
            return

        try:
            self.closePersistentEditor(item, 0)
        except Exception:
            pass

        parent_item = item.parent()
        if parent_item is not None:
            try:
                parent_item.removeChild(item)
            except Exception:
                pass

        try:
            self.itemChanged.disconnect(self._finalize_new)
        except Exception:
            pass

        self._creating_item = None
        self._editing_item = None

    def add_new(self, mode):
        item = self.currentItem()
        if not item:
            return

        # Kalau ada dummy lama, cancel dulu
        if self._creating_item:
            self._cancel_dummy()


        full = item.data(0, Qt.UserRole)

        # kalau yang di-select FILE → dummy masuk tepat di bawah file itu (sibling)
        # kalau folder → dummy masuk sebagai child pertama
        if os.path.isfile(full):
            parent_dir = os.path.dirname(full)
            parent_item = item.parent()
            # posisi: tepat setelah file yang di-select
            insert_idx = parent_item.indexOfChild(item) + 1
        else:
            parent_dir = full
            parent_item = item
            parent_item.setExpanded(True)
            # posisi: paling atas child
            insert_idx = 0

        if not parent_item:
            return


        dummy = QTreeWidgetItem(["", "", ""])
        dummy.setData(0, Qt.UserRole, parent_dir)

        if mode == "folder":
            dummy.setIcon(0, self.icon_folder)
            dummy.setData(0, SIZE_LABEL_ROLE, "")
        else:
            dummy.setIcon(0, self.icon_file)

        flags = dummy.flags()
        dummy.setFlags(flags | Qt.ItemIsEditable)

        parent_item.insertChild(insert_idx, dummy)

        self.setCurrentItem(dummy)
        self.scrollToItem(dummy)

        self._editing_item = dummy
        dummy._creating_mode = mode
        self._creating_item = dummy

        self.itemChanged.connect(self._finalize_new)

        # Langsung buka editor tanpa perlu hover
        from PySide6.QtCore import QTimer
        def _open_and_focus():
            if self._creating_item is None:
                return
            self.openPersistentEditor(dummy, 0)
            # Grab focus langsung ke persistent editor widget
            from PySide6.QtWidgets import QLineEdit, QApplication
            def _grab():
                if self._creating_item is None:
                    return
                # cara paling reliable: cek semua child QLineEdit di viewport
                for w in self.viewport().findChildren(QLineEdit):
                    w.setFocus(Qt.OtherFocusReason)
                    w.deselect()
                    w.end(False)  # cursor di akhir, bukan select all
                    break
            QTimer.singleShot(30, _grab)

        QTimer.singleShot(0, _open_and_focus)

    def mouseMoveEvent(self, event):
        item = self.itemAt(event.pos())
        if item is not self._hovered_item:
            old = self._hovered_item
            self._hovered_item = item
            self._update_hover_rows(old, item)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        if self._hovered_item is not None:
            old = self._hovered_item
            self._hovered_item = None
            self._update_hover_rows(old, None)
        super().leaveEvent(event)

    def _update_hover_rows(self, old, current):
        viewport = self.viewport()
        for item in (old, current):
            if item is not None:
                rect = self.visualItemRect(item)
                if rect.isValid():
                    # The highlight spans the row, including all columns and
                    # indentation. Repaint both old/new rows to erase it fully.
                    rect.setLeft(0)
                    rect.setRight(viewport.width() - 1)
                    viewport.update(rect.intersected(viewport.rect()))

    def mousePressEvent(self, event):
        self._stop_wheel_scroll()
        # Klik di luar dummy saat creating → cancel
        if self._creating_item and not self._renaming:
            cur = self.itemAt(event.pos())
            if cur is not self._creating_item:
                self._cancel_dummy()


        if event.button() == Qt.RightButton:
            clicked = self.itemAt(event.pos())
            if clicked is not None and clicked not in self.selectedItems():
                # klik kanan di item yg belum ke-select → pindahin selection
                # ke item itu (single), tanpa scroll-into-view.
                self.blockSignals(True)
                self.clearSelection()
                clicked.setSelected(True)
                self.setCurrentItem(clicked)
                self.blockSignals(False)
                self._on_selection_changed(None, None)
            event.accept()
            return

        # single-parent enforcement untuk multi-select
        ctrl  = bool(event.modifiers() & Qt.ControlModifier)
        shift = bool(event.modifiers() & Qt.ShiftModifier)
        clicked = self.itemAt(event.pos())

        if ctrl or shift:
            self._multi_selecting = True
            if clicked:
                clicked_parent = clicked.parent()
                if self._multi_anchor_parent is None:
                    # anchor belum ada — set dari item yang di-click ini
                    self._multi_anchor_parent = clicked_parent
                elif clicked_parent is not self._multi_anchor_parent:
                    # beda parent — block
                    event.accept()
                    return
        else:
            # klik normal — reset anchor, Qt akan clear selection via super()
            self._multi_selecting = False
            self._multi_anchor_parent = None

        super().mousePressEvent(event)

    def _on_selection_changed(self, selected, deselected):
        # Kalau selection jadi kosong dan tidak sedang multi-select → reset anchor
        if not self._multi_selecting and not self.selectedItems():
            self._multi_anchor_parent = None


    def focusOutEvent(self, event):
        if self._editing_item and not self._renaming:
            self.closePersistentEditor(self._editing_item, 0)
        super().focusOutEvent(event)

    def show_search(self):
        if not hasattr(self, "_search_ui") or self._search_ui is None:
            from main_ext.class_FileSearchUI import FileSearchUI
            self._search_ui = FileSearchUI(self)
        self._search_ui.show_search()

    def keyPressEvent(self, event):
        self._stop_wheel_scroll()
        # ESC saat creating dummy → cancel langsung
        if event.key() == Qt.Key_Escape and self._creating_item:
            self._cancel_dummy()
            event.accept()
            return

        # Backspace — naik satu level (Single Dir mode saja, kaya Explorer)
        if (event.key() == Qt.Key_Backspace and self.display_mode == "single_dir"
                and not self._editing_item and not self._creating_item
                and not getattr(self, "_renaming", False)):
            self._sd_navigate_up()
            event.accept()
            return

        # DEL — hapus item terpilih
        if event.key() == Qt.Key_Delete and not self._editing_item and not self._creating_item and not getattr(self, "_renaming", False):
            self.delete_selected()
            event.accept()
            return


        if (event.modifiers() & Qt.ControlModifier) and not self._editing_item and not self._creating_item:
            key = event.key()
            if key == Qt.Key_F:
                self.show_search()
                event.accept()
                return
            if key == Qt.Key_C:
                self.clip_copy()
                event.accept()
                return
            if key == Qt.Key_X:
                self.clip_cut()
                event.accept()
                return
            if key == Qt.Key_V:
                if self.can_paste():
                    self.clip_paste(self.currentItem())
                event.accept()
                return

        super().keyPressEvent(event)

    def _generate_unique_name(self, parent_dir, base_name):

        name = base_name
        i = 1
        while True:
            full_path = os.path.join(parent_dir, name)
            if not os.path.exists(full_path):
                return name
            name = f"{base_name} ({i})"
            i += 1

    def _finalize_new(self, item, col):
        # Run ONLY for the dummy being created
        if self._creating_item is None or item is not self._creating_item:
            return

        parent_dir = item.data(0, Qt.UserRole)
        parent_item = item.parent()
        name = item.text(0).strip()
        mode = item._creating_mode


        if not name:
            if parent_item:
                parent_item.removeChild(item)

                # refresh parent untuk pastikan UI tidak freeze
                self.refresh_folder(parent_item)

            self._creating_item = None
            del item._creating_mode
            self.itemChanged.disconnect(self._finalize_new)
            return


        new_path = os.path.join(parent_dir, name)


        rel_new = os.path.relpath(new_path, self.base).replace("\\", "/")
        fs_root_new = rel_new.split("/")[0]
        _branch_physical_path = None  # path fisik di branch, None = source
        if fs_root_new and self.nb_is_active(fs_root_new):
            nb = self._get_nb_manager()
            active = nb.get_active_branch(fs_root_new) if nb else None
            if active and nb:
                branch_fs_dir = nb._branch_fs_dir(fs_root_new, active["name"])
                # rel_inside_fs = strip fs_root prefix dari rel_new
                rel_inside = "/".join(rel_new.split("/")[1:])
                _branch_physical_path = os.path.join(branch_fs_dir, rel_inside.replace("/", os.sep))

        # path yang benar-benar dibuat di disk
        create_path = _branch_physical_path if _branch_physical_path else new_path


        if os.path.exists(create_path):
            AppDialog.warning(self.window(), t("ft_name_exists_title"), t("ft_name_exists_msg"))
            if parent_item:
                parent_item.removeChild(item)

            self._creating_item = None
            del item._creating_mode
            self.itemChanged.disconnect(self._finalize_new)
            return


        try:
            if mode == "folder":
                os.makedirs(create_path, exist_ok=True)
            else:
                os.makedirs(os.path.dirname(create_path), exist_ok=True)
                open(create_path, "w").close()
        except Exception as e:
            AppDialog.warning(self.window(), "Error", str(e))
            if parent_item:
                parent_item.removeChild(item)

            self._creating_item = None
            del item._creating_mode
            self.itemChanged.disconnect(self._finalize_new)
            return


        if parent_item:
            parent_item.removeChild(item)

        rel = os.path.relpath(new_path, self.base).replace("\\", "/")

        # hanya inject kalau belum ada di config maupun neocat_config
        raw_ctx_key = self.engine.escaped_ctx_key(rel)
        if raw_ctx_key not in self.ctx and raw_ctx_key not in self.ctx_neocat:
            self._nb_apply(rel)
        self._nb_record_output_tree(new_path, create_path)

        # Select the created row after asynchronous reconciliation completes.
        self.refresh_folder(parent_item, focus_path=new_path)


        self._creating_item = None
        del item._creating_mode
        self.itemChanged.disconnect(self._finalize_new)

    def _detect_magic_once(self, full):

        from core.header_magic import check_magic
        try:
            stat = os.stat(full)
            mtime = int(stat.st_mtime)
            size  = stat.st_size
        except:
            return ""

        entry = self.magic_cache.get(full)
        if entry and entry["mtime"] == mtime and entry["size"] == size:
            return entry["magic"]

        magic = check_magic(full)
        self.magic_cache[full] = {"mtime": mtime, "size": size, "magic": magic}

        # Sync ke legacy sub-cache supaya kode lama nggak break
        if magic in ("EROFS", "EXT4", "EXT3", "EXT2"):
            self.fsdetect_cache[full] = {"mtime": mtime, "size": size, "fs_type": magic}
            self.superdetect_cache[full] = ""
        elif magic in self.NON_FS_BADGE:
            self.superdetect_cache[full] = magic
            self.fsdetect_cache[full] = {"mtime": mtime, "size": size, "fs_type": ""}
        else:
            self.fsdetect_cache[full] = {"mtime": mtime, "size": size, "fs_type": ""}
            self.superdetect_cache[full] = ""

        return magic

    def _check_img_cache(self, full):

        magic = self._detect_magic_once(full)
        if magic in ("EROFS", "EXT4", "EXT3", "EXT2"):
            return magic
        return ""

    # Tipe magic yang bikin file dapat label di tree (FS + container image).
    _BADGEABLE_MAGIC = ("EROFS", "EXT4", "EXT3", "EXT2") + NON_FS_BADGE

    def _sniff_dropped_magic(self, phys):

        reg = getattr(self, "_dropped_badge_paths", None)
        if reg is None:
            reg = self._dropped_badge_paths = set()

        def _sniff_one(p):
            try:
                if not os.path.isfile(p):
                    return
                magic = self._detect_magic_once(p)
                if magic in self._BADGEABLE_MAGIC:
                    reg.add(os.path.normpath(p))
            except Exception:
                pass

        try:
            if os.path.isdir(phys):
                for name in os.listdir(phys):
                    _sniff_one(os.path.join(phys, name))
            else:
                _sniff_one(phys)
        except Exception:
            pass

    def refresh_folder(self, parent_item, *, focus_path=None):
        if parent_item is not None and not self._background_stopped:
            if self.is_mount_node(parent_item) and not parent_item.data(0, Qt.UserRole + 52):
                self.expand_mount_node(parent_item)
            else:
                self._request_directory(parent_item, refresh=True, focus_path=focus_path)

    def find_item_by_path(self, item, path):

        if item is None or not path:
            return None

        ipath = item.data(0, Qt.UserRole)
        if ipath:
            if ipath == path or os.path.normcase(os.path.normpath(ipath)) == os.path.normcase(os.path.normpath(path)):
                return item

        for i in range(item.childCount()):
            child = item.child(i)
            found = self.find_item_by_path(child, path)
            if found:
                return found

        return None

    def save_expanded_paths(self):

        expanded = set()

        def walk(item):
            if not item:
                return
            p = item.data(0, Qt.UserRole)
            if p and item.isExpanded():
                expanded.add(os.path.normpath(p))
            for i in range(item.childCount()):
                walk(item.child(i))

        root = self.topLevelItem(0)
        walk(root)
        return expanded

    def restore_expanded_paths(self, expanded_set):

        expanded_set = set(os.path.normpath(p) for p in expanded_set)

        def walk(item):
            if not item:
                return
            p = item.data(0, Qt.UserRole)
            if p and os.path.normpath(p) in expanded_set:
                item.setExpanded(True)
            for i in range(item.childCount()):
                walk(item.child(i))

        root = self.topLevelItem(0)
        walk(root)

    def apply_fs_badge_after_finished(self, full):
        if not full.lower().endswith(".img"):
            return

        fs_type = self._detect_magic_once(full)
        if fs_type not in ("EROFS", "EXT4", "EXT3", "EXT2", "SUPER", "SPARSE", "BOOT", "VNDRBOOT"):
            return

        item = self.find_item_by_path(self.topLevelItem(0), full)
        if item:
            item.setData(0, Qt.UserRole + 1, fs_type)
            if fs_type in self.NON_FS_BADGE:
                item.setData(0, UI_STABLE_ROLE, True)
            self.viewport().update()

    def refresh_single_dir(self, path):


        path = os.path.normpath(path)

        # pastikan folder ada
        if not os.path.isdir(path):
            return

        # tambahkan watcher untuk folder baru
        if path not in self.watcher.directories() and not self._unpack_watcher.is_watched(path):
            try:
                self.watcher.addPath(path)
            except:
                pass

        # SCAN SUBTREE PAKAI prescan_task
        from core.fsdetect import prescan_task

        def after(ok, payload):
            if not ok:
                return

            try:
                data = json.loads(payload)
                folders = data.get("folders", {})
                fscache = data.get("fscache", {})
                # P3 optimisasi: prescan udah precompute child folder list per
                # folder. Pake langsung — hindari os.listdir di main thread.
                snap_from_prescan = data.get("child_snapshot", {})
            except:
                return

            # update caches
            for k, v in folders.items():
                self.folder_has_child[os.path.normpath(k)] = v


            for folder in folders.keys():
                norm = os.path.normpath(folder)

                # DAFTARIN WATCHER
                if norm not in self.watcher.directories() and not self._unpack_watcher.is_watched(norm):
                    try:
                        self.watcher.addPath(norm)
                    except:
                        pass


                snap_list = snap_from_prescan.get(norm)
                if snap_list is None:
                    # Coba juga key non-normalized — prescan kirim normpath,
                    # tapi defensive untuk variasi separator
                    snap_list = snap_from_prescan.get(folder)

                if snap_list is not None:
                    self.child_snapshot[norm] = set(snap_list)
                else:
                    # Fallback in-line (jarang ke-hit kalau prescan jalan benar)
                    try:
                        subs = set(
                            os.path.normpath(os.path.join(norm, n))
                            for n in os.listdir(norm)
                            if os.path.isdir(os.path.join(norm, n))
                        )
                        self.child_snapshot[norm] = subs
                    except:
                        self.child_snapshot[norm] = set()

            for k, v in fscache.items():
                self.fsdetect_cache[os.path.normpath(k)] = v

            # REFRESH PARENT ITEM SAJA
            root = self.topLevelItem(0)
            target_item = self.find_item_by_path(root, path)
            parent = target_item.parent() if target_item else None

            if not parent:
                parent = self.topLevelItem(0)

            state = self.save_full_expanded_state()
            self.refresh_folder(parent)
            self.restore_full_expanded_state(state)

        run_in_thread(prescan_task, path, callback=after)

    def is_inside_fs_root(self, rel):
        root = rel.split("/")[0]

        # ⛔ IMAGE CONTAINER BUKAN FILESYSTEM
        if root.upper() in self.NON_FS_BADGE:
            return False

        return root in self.engine.root_map_ctx and root in self.engine.root_map_fs

    def is_image_container(self, full):
        if not full.lower().endswith(".img"):
            return False

        fs = self.fsdetect_cache.get(full)
        if not fs:
            return False

        return fs.get("fs_type") in self.NON_FS_BADGE

    def _watcher_peers(self):

        peers = [self]
        base = os.path.normcase(os.path.normpath(self.base)) if self.base else None
        window = self.window()
        for name in ("tree", "tree2", "tree_mirror"):
            tree = getattr(window, name, None)
            if tree is not None and tree not in peers and tree.base and base:
                if os.path.normcase(os.path.normpath(tree.base)) == base:
                    peers.append(tree)
        return peers

    def stop_background_work(self):

        self._stop_wheel_scroll()
        self._background_stopped = True
        if hasattr(self, "_reset_icon_work"):
            self._reset_icon_work()
        self._metadata_refresh = None
        self._directory_jobs.stop()
        self._directory_tokens.clear()
        self._directory_handles.clear()
        self._directory_rendering.clear()
        self._directory_last.clear()
        self._directory_focus.clear()
        self._directory_change_timer.stop()
        self._pending_directory_changes.clear()
        self.watcher.blockSignals(True)
        paths = self.watcher.directories() + self.watcher.files()
        if paths:
            self.watcher.removePaths(paths)
        self._unpack_watcher.stop()
        self._size_probe.stop()
        self._size_probe_items.clear()
        self._size_probe_alias.clear()
        for timer in self.findChildren(QTimer):
            timer.stop()

    def pause_watcher(self, path=None, fs_type: str = "", *, refresh_ui: bool = True):
        if not path:
            return
        for tree in self._watcher_peers():
            tree._pause_watcher_local(path, fs_type, refresh_ui=refresh_ui)
            # Extractors also stream fs_config/file_contexts outside the output
            # directory. Those notifications must not rebuild the config row.
            config = os.path.join(tree.base, "config") if tree.base else None
            if config and os.path.normpath(config) != os.path.normpath(path):
                tree._pause_watcher_local(config, refresh_ui=False)

    def _pause_watcher_local(self, path=None, fs_type: str = "", *, refresh_ui: bool = True):

        if not path:
            return

        norm = os.path.normpath(path)

        # 1) Remove path + semua subdirektori dari QFileSystemWatcher
        try:
            watched = list(self.watcher.directories())
            for w in watched:
                w_norm = os.path.normpath(w)
                if w_norm == norm or w_norm.startswith(norm + os.sep):
                    try:
                        self.watcher.removePath(w)
                    except Exception:
                        pass
        except Exception as e:
            print(f"[pause_watcher] error removing paths: {e}")

        # 2) Register ke UnpackWatcher — polling 5 detik
        self._unpack_watcher.register(norm, self, fs_type=fs_type,
                                      refresh_ui=refresh_ui)

    def resume_watcher(self, path=None):
        if not path:
            return
        for tree in self._watcher_peers():
            tree._resume_watcher_local(path)
            config = os.path.join(tree.base, "config") if tree.base else None
            if config and os.path.normpath(config) != os.path.normpath(path):
                tree._resume_watcher_local(config)

    def _resume_watcher_local(self, path=None):

        if not path or self._background_stopped:
            return

        norm = os.path.normpath(path)

        # 1) Unregister dari UnpackWatcher (includes final refresh)
        self._unpack_watcher.unregister(norm)
        if self._unpack_watcher.is_watched(norm):
            return


        try:
            if os.path.isdir(norm) and norm not in self.watcher.directories():
                self.watcher.addPath(norm)
        except Exception:
            pass

        # Re-add subdirektori yang expanded
        try:
            root = self.topLevelItem(0)
            if root:
                item = self.find_item_by_path(root, norm)
                if item and item.isExpanded():
                    self._readd_expanded_watchers(item)
        except Exception:
            pass

    def _readd_expanded_watchers(self, item):

        for i in range(item.childCount()):
            child = item.child(i)
            if child is None or not child.isExpanded():
                continue
            child_path = child.data(0, Qt.UserRole)
            # Expanded rows have already been classified by the directory
            # worker. Do not stat every file when resuming an unpack watcher.
            if child_path and is_folder_item(child):
                try:
                    norm_child = os.path.normpath(child_path)
                    if norm_child not in self.watcher.directories():
                        self.watcher.addPath(norm_child)
                except Exception:
                    pass
                self._readd_expanded_watchers(child)


    def on_directory_changed(self, path):
        if self._background_stopped:
            return
        path = os.path.normpath(path)
        if self._unpack_watcher.is_watched(path):
            return
        comparable_path = os.path.normcase(path)
        for (quiet_path, recursive), count in getattr(self, "_bulk_watcher_quiet", {}).items():
            if count and (comparable_path == quiet_path or
                          (recursive and comparable_path.startswith(quiet_path + os.sep))):
                return
        self._pending_directory_changes.add(path)
        # A fixed batching window also handles a continuous stream of writes;
        # restarting the timer for each event could postpone refresh forever.
        if not self._directory_change_timer.isActive():
            self._directory_change_timer.start()

    def _flush_directory_changes(self):
        paths = self._pending_directory_changes
        self._pending_directory_changes = set()
        for path in sorted(paths):
            if self._background_stopped:
                break
            self._process_directory_changed(path)

    def _process_directory_changed(self, path):
        if self._background_stopped or self._renaming or self._unpack_watcher.is_watched(path):
            return
        comparable = os.path.normcase(os.path.normpath(path))
        for (quiet_path, recursive), count in getattr(self, "_bulk_watcher_quiet", {}).items():
            if count and (comparable == quiet_path or (recursive and comparable.startswith(quiet_path + os.sep))):
                return
        root = self.invisibleRootItem() if self.display_mode == "single_dir" else self.topLevelItem(0)
        item = self.find_item_by_path(root, path)
        if item is not None:
            if item.data(0, UI_STABLE_ROLE):
                return
            if item.isExpanded() or item is self.invisibleRootItem():
                self.refresh_folder(item)
            elif item.parent() is not None:
                self.refresh_folder(item.parent())
            return
        parent_path = os.path.dirname(os.path.normpath(path))
        while parent_path:
            parent = self.find_item_by_path(root, parent_path)
            if parent is not None and (parent.isExpanded() or parent is self.invisibleRootItem()):
                self.refresh_folder(parent)
                return
            next_path = os.path.dirname(parent_path)
            if next_path == parent_path:
                break
            parent_path = next_path

    def get_expanded_state(self, item):
        state = { item.data(0, Qt.UserRole): item.isExpanded() }
        for i in range(item.childCount()):
            state.update(self.get_expanded_state(item.child(i)))
        return state

    def apply_expanded_state(self, item, state):
        key = item.data(0, Qt.UserRole)
        if key in state:
            item.setExpanded(state[key])
        for i in range(item.childCount()):
            self.apply_expanded_state(item.child(i), state)

    def save_full_expanded_state(self):
        root = self.topLevelItem(0)
        return self._save_state_recursive(root)

    def _save_state_recursive(self, item):
        state = { item.data(0, Qt.UserRole): item.isExpanded() }
        for i in range(item.childCount()):
            child = item.child(i)
            state.update(self._save_state_recursive(child))
        return state

    def restore_full_expanded_state(self, state):
        root = self.topLevelItem(0)
        self._restore_state_recursive(root, state)

    def _restore_state_recursive(self, item, state):
        key = item.data(0, Qt.UserRole)
        if key in state:
            item.setExpanded(state[key])
        for i in range(item.childCount()):
            self._restore_state_recursive(item.child(i), state)

    def _enqueue_size_probe(self, logical_path, phys_path=None, item=None):

        if not logical_path or self._background_stopped:
            return
        target = phys_path or logical_path
        if not target or target.startswith("mount://"):
            return


        if item is not None and item.data(0, IS_FOLDER_ROLE) is True:
            return
        if target != logical_path:
            self._size_probe_alias[target] = logical_path
        if item is not None:


            self._size_probe_items[logical_path] = QPersistentModelIndex(self.indexFromItem(item))
        self._size_probe.enqueue(target)

    @staticmethod
    def _item_phys_path(item):

        try:
            return item.data(0, Qt.UserRole + 20)
        except Exception:
            return None

    def invalidate_size(self, path):

        root = self.topLevelItem(0)
        if root is None or not path:
            return
        item = self.find_item_by_path(root, path)
        if item is None or item.data(0, IS_FOLDER_ROLE) is True:
            return
        self._enqueue_size_probe(
            item.data(0, Qt.UserRole), self._item_phys_path(item), item
        )

    def request_size_probe(self, item):

        if item.data(0, IS_FOLDER_ROLE) is True:
            item.setData(0, SIZE_LABEL_ROLE, "")
            return
        path = item.data(0, Qt.UserRole)
        if path:
            self._enqueue_size_probe(path, self._item_phys_path(item), item)

    def pause_size_probe(self):

        pass

    def resume_size_probe(self):

        pass

    def _on_size_resolved(self, path, size_str):

        if self._background_stopped:
            return
        # Hasil balik pakai path yang di-stat; kalau itu path fisik branch,
        # balikin ke path logis biar ketemu item-nya.
        path = self._size_probe_alias.get(path, path)
        index = self._size_probe_items.get(path)
        if index is None or not index.isValid():
            self._size_probe_items.pop(path, None)
            return
        item = self.itemFromIndex(QModelIndex(index))
        if item is None:
            return
        # The worker already returns an empty size for folders/missing files.
        # Never stat an unknown row on the GUI thread when accepting results.
        if item.data(0, IS_FOLDER_ROLE) is True:
            item.setData(0, SIZE_LABEL_ROLE, "")
            return
        cur = item.data(0, SIZE_LABEL_ROLE)
        # cur biasanya SIZE_PENDING ("0 B") atau nilai lama. Update kalau diff.
        if cur == size_str:
            return
        item.setData(0, SIZE_LABEL_ROLE, size_str)
        try:
            self.viewport().update(self.visualItemRect(item))
        except Exception:
            self.viewport().update()

    def _update_ctx_perm_recursive(self, item):
        if not self._update_ctx_perm_item(item):
            return
        for i in range(item.childCount()):
            self._update_ctx_perm_recursive(item.child(i))

    def _update_ctx_perm_item(self, item):

        # Mount node (virtual): ctx/perm udah dari image, jangan diutak-atik
        # lewat config cache disk. Skip total subtree-nya.
        if self.is_mount_node(item):
            return False


        if is_folder_item(item):
            item.setData(0, SIZE_LABEL_ROLE, "")
        elif item.data(0, SIZE_LABEL_ROLE) is not None:
            p = item.data(0, Qt.UserRole)
            if p:
                self._enqueue_size_probe(p, self._item_phys_path(item), item)

        full = item.data(0, Qt.UserRole)
        if full and self.base:
            try:
                rel = os.path.relpath(full, self.base).replace("\\", "/")
                # Process all items that are inside or at fs_root level
                # (rel == "vendor" is the fs_root itself, also needs update)
                if rel and rel != ".":
                    raw_ctx_key = self.engine.escaped_ctx_key(rel)
                    if raw_ctx_key in self.ctx:
                        ctx_val = self.ctx[raw_ctx_key]
                    elif raw_ctx_key in self.ctx_neocat:
                        ctx_val = self.ctx_neocat[raw_ctx_key]
                    else:
                        ctx_val = ""

                    perm_raw = self.fs.get(rel, "")
                    perm = perm_to_rwx(perm_raw) if perm_raw else ""

                    # Rendering fresh metadata must not enqueue config writes
                    # through the user-edit handler for every row.
                    with QSignalBlocker(self):
                        item.setText(1, ctx_val)
                        item.setText(2, perm)
                        ctx_src = "config" if raw_ctx_key in self.ctx else (
                            "neocat" if raw_ctx_key in self.ctx_neocat else "")
                        self._apply_ctx_color(item, ctx_src)
            except Exception:
                pass

        return True

    def dragEnterEvent(self, event):
        data = event.mimeData()

        # drag dari dalam image yang di-mount (comot keluar)
        if data.hasFormat("application/x-rdt-mount"):
            event.acceptProposedAction()
            return

        if data.hasUrls():
            event.acceptProposedAction()
            return

        super().dragEnterEvent(event)

    def _update_drag_hover(self, pos, mounted=False):
        item = self.itemAt(pos)
        if item is None:
            item = (self.invisibleRootItem() if self.display_mode == "single_dir"
                    else self.topLevelItem(0))


        accepted = item is not None
        if mounted:
            full = (item.data(0, Qt.UserRole) or "") if item is not None else ""
            accepted = item is not None and not self.is_mount_node(item) and os.path.isdir(full)
            if not accepted:
                item = None

        self.drag_hover_item = item

        # MULTI-HIGHLIGHT LOGIC
        new_set = set()
        # Cek apakah item yg di-drag ada di dir yang sama dengan target
        main = self.window()
        src_path = main.drag_src_path if main and hasattr(main, "drag_src_path") else None
        src_dir = os.path.dirname(src_path) if src_path else None
        if item is not None:
            full = item.data(0, Qt.UserRole) or ""
            link_dir = item.data(0, APKTOOL_LINK_ROLE)
            if link_dir:
                # Drag ke APK yang lagi di mode FOLDER → highlight folder itu sendiri (+ anak kalau expanded)
                new_set.add(item)
                if item.isExpanded():
                    for i in range(item.childCount()):
                        new_set.add(item.child(i))
            else:
                target_dir = os.path.dirname(full) if os.path.isfile(full) else full
                # Kalau src dan target ada di dir yang sama → skip highlight (TAPI scroll tetap jalan)
                if src_dir and os.path.normpath(src_dir) == os.path.normpath(target_dir):
                    pass  # new_set tetep kosong, no highlight
                elif os.path.isfile(full):
                    # Drag ke FILE biasa / APK mode → highlight semua sibling (1 dir yang sama)
                    parent_item = item.parent()
                    if parent_item:
                        for i in range(parent_item.childCount()):
                            new_set.add(parent_item.child(i))
                    else:
                        for i in range(self.topLevelItemCount()):
                            new_set.add(self.topLevelItem(i))
                elif os.path.isdir(full):
                    # Drag ke FOLDER → highlight folder itu sendiri
                    new_set.add(item)
                    # Kalau expanded, child-nya juga ikut nyala
                    if item.isExpanded():
                        for i in range(item.childCount()):
                            new_set.add(item.child(i))
                else:
                    new_set.add(item)

        self.drag_hover_items = new_set
        # The opaque viewport owns its backing store; updating only the outer
        # QTreeWidget no longer repaints these destination hints.
        self.viewport().update()
        return accepted

    def dragMoveEvent(self, event):
        pos = event.position().toPoint()
        self._drag_hover_position = pos
        self._drag_hover_mounted = event.mimeData().hasFormat("application/x-rdt-mount")
        if self._update_drag_hover(pos, self._drag_hover_mounted):
            event.acceptProposedAction()
        else:
            event.ignore()

        y = pos.y()
        h = self.viewport().height()

        # 30% dari atas/bawah = zona scroll
        zone_size = h * 0.30
        max_speed = 8.0   # px/tick di TEPI banget viewport (~265 px/s)
        min_speed = 0.15  # px/tick di BATAS zona dalam (~5 px/s, ngeraba)
        exponent = 2.5    # >1 = pelan dominan, cepet cuma di ujung

        new_speed = 0.0
        if y < zone_size:
            # t=0 di batas zona dalam, t=1 di tepi atas
            t = 1.0 - (y / zone_size)
            t = max(0.0, min(1.0, t))
            new_speed = -(min_speed + (max_speed - min_speed) * (t ** exponent))
        elif y > h - zone_size:
            # t=0 di batas zona dalam, t=1 di tepi bawah
            t = (y - (h - zone_size)) / zone_size
            t = max(0.0, min(1.0, t))
            new_speed = min_speed + (max_speed - min_speed) * (t ** exponent)

        # Kalau ganti arah, reset accumulator biar ga ada residual sisa
        if (new_speed > 0) != (self._drag_scroll_speed > 0) or \
           (new_speed < 0) != (self._drag_scroll_speed < 0):
            self._drag_scroll_accum = 0.0

        self._drag_scroll_speed = new_speed
        if new_speed != 0.0:
            if not self._drag_scroll_timer.isActive():
                self._drag_scroll_timer.start()
        else:
            if self._drag_scroll_timer.isActive():
                self._drag_scroll_timer.stop()

    def _drag_scroll_tick(self):
        speed = self._drag_scroll_speed
        if speed == 0.0:
            self._drag_scroll_timer.stop()
            self._drag_scroll_accum = 0.0
            return

        # Akumulasi subpixel — biar speed float <1 tetap bisa scroll
        # (cuma lebih jarang). Contoh: speed=0.3 → tiap ~3 tick gerak 1px.
        self._drag_scroll_accum += speed
        step = int(self._drag_scroll_accum)
        if step == 0:
            return  # belum cukup akumulasi, tunggu tick berikutnya
        self._drag_scroll_accum -= step

        bar = self.verticalScrollBar()
        bar.setValue(bar.value() + step)
        if self._drag_hover_position is not None:
            self._update_drag_hover(self._drag_hover_position, self._drag_hover_mounted)
        # Kalau udah mentok atas/bawah, stop biar ga sia-sia
        if (speed < 0 and bar.value() <= bar.minimum()) or \
           (speed > 0 and bar.value() >= bar.maximum()):
            self._drag_scroll_timer.stop()
            self._drag_scroll_accum = 0.0

    def apply_fs_from_config(self, config_dir, fs_root_dir):


        for fname in os.listdir(config_dir):
            if not fname.endswith("_fs_config"):
                continue

            part = fname.replace("_fs_config", "")
            ctx_file = os.path.join(config_dir, f"{part}_file_context")
            fs_file  = os.path.join(config_dir, f"{part}_fs_config")

            if not os.path.isfile(ctx_file) or not os.path.isfile(fs_file):
                continue

            fs_target = os.path.join(fs_root_dir, part)
            if not os.path.isdir(fs_target):
                continue

            # 🔥 apply ke subtree filesystem
            for root, dirs, files in os.walk(fs_target):
                rel_root = os.path.relpath(root, self.base).replace("\\", "/")
                raw_ctx_r = self.engine.escaped_ctx_key(rel_root)
                if raw_ctx_r not in self.ctx and raw_ctx_r not in self.ctx_neocat:
                    self.engine.apply(rel_root)

                for f in files:
                    full = os.path.join(root, f)
                    rel = os.path.relpath(full, self.base).replace("\\", "/")
                    raw_ctx_f = self.engine.escaped_ctx_key(rel)
                    if raw_ctx_f not in self.ctx and raw_ctx_f not in self.ctx_neocat:
                        self.engine.apply(rel)

    def ask_drop_conflict_policy(self, filename, src_path=None, dst_path=None,
                                  folder_context=False, remaining=0,
                                  show_bulk=False) -> str:

        if self.drop_conflict_policy == "replace_all":
            return "replace_all"
        if self.drop_conflict_policy == "skip_all":
            return "skip_all"

        policy = ask_file_conflict(
            self, filename,
            src_path=src_path, dst_path=dst_path,
            show_bulk=show_bulk,
            folder_context=folder_context,
            remaining=remaining,
        )

        if policy in ("replace_all", "skip_all"):
            self.drop_conflict_policy = policy

        return policy

    def _needs_auto_apply(self, rel):

        raw_ctx_key = self.engine.escaped_ctx_key(rel)
        raw_fs_key  = rel

        has_ctx  = (raw_ctx_key in self.ctx) or (raw_ctx_key in self.ctx_neocat)
        has_perm = raw_fs_key in self.fs

        # kalau dua-duanya ada → JANGAN APPLY
        if has_ctx and has_perm:
            return False

        return True

    def _nb_apply(self, rel):

        rel = rel.replace("\\", "/").strip("/")
        fs_root = rel.split("/")[0] if "/" in rel else rel

        if not fs_root or not self.nb_is_active(fs_root):
            # Source normal
            self.engine.apply(rel)
            return

        # Branch aktif: tulis langsung ke branch config
        branch_cfg = self.nb_get_live_config_dir(fs_root)

        # Pastikan file ctx dan fs ada di branch config dir
        # (namanya harus sama dengan yang di source config)
        import re as _re
        ctx_fname = f"{fs_root}_file_contexts"
        fs_fname  = f"{fs_root}_fs_config"
        ctx_file  = os.path.join(branch_cfg, ctx_fname)
        fs_file   = os.path.join(branch_cfg, fs_fname)

        if not os.path.isfile(ctx_file) or not os.path.isfile(fs_file):
            # Branch config belum ada file ini — fallback ke source
            self.engine.apply(rel)
            return

        ctx_key = self.engine.escaped_ctx_key(rel)
        fs_key  = rel

        # file_contexts
        with open(ctx_file, "r", encoding="utf-8", errors="ignore") as f:
            lines = [ln.rstrip("\n") for ln in f]

        if not any(ln.startswith(ctx_key + " ") for ln in lines):
            final = []
            inserted = False
            for ln in lines:
                s = ln.strip()
                if s and not s.startswith("#"):
                    key = s.split()[0]
                    if not inserted and ctx_key < key:
                        final.append(f"{ctx_key} {_DEFAULT_CTX}")
                        inserted = True
                final.append(ln)
            if not inserted:
                final.append(f"{ctx_key} {_DEFAULT_CTX}")
            with open(ctx_file, "w", encoding="utf-8", errors="ignore") as f:
                f.write("\n".join(final) + "\n")

        # fs_config
        with open(fs_file, "r", encoding="utf-8", errors="ignore") as f:
            lines = [ln.rstrip("\n") for ln in f]

        if not any(ln.split()[0] == fs_key
                   for ln in lines if ln.strip() and not ln.strip().startswith("#")):
            uid, gid, mode = self.engine.infer_permission(fs_key)[:3]
            new_line = f"{fs_key} {uid} {gid} {mode}"
            final = []
            inserted = False
            for ln in lines:
                s = ln.strip()
                if s and not s.startswith("#"):
                    key = s.split()[0]
                    if not inserted and fs_key < key:
                        final.append(new_line)
                        inserted = True
                final.append(ln)
            if not inserted:
                final.append(new_line)
            with open(fs_file, "w", encoding="utf-8") as f:
                f.write("\n".join(final) + "\n")

        # Reload ctx/fs dari branch overlay
        self.ctx.update(_load_ctx_from_dir(branch_cfg))
        self.fs.update(_load_fs_from_dir(branch_cfg))
        if hasattr(self, "engine") and self.engine:
            self.engine.ctx = self.ctx
            self.engine.fs  = self.fs

    def _nb_active_state_for_logical(self, logical_path: str):

        try:
            rel = os.path.relpath(logical_path, self.base).replace("\\", "/").strip("/")
        except Exception:
            return "", "", None, None
        if not rel or rel == ".":
            return rel, "", None, None
        fs_root = rel.split("/")[0]
        if not fs_root or not self.nb_is_active(fs_root):
            return rel, fs_root, None, None
        nb = self._get_nb_manager()
        active = nb.get_active_branch(fs_root) if nb else None
        return rel, fs_root, nb, active

    def _nb_resolve_write_path(self, logical_path: str):

        rel, fs_root, nb, active = self._nb_active_state_for_logical(logical_path)
        if nb and active and rel:
            return os.path.join(
                nb._branch_dir(fs_root, active["name"]),
                rel.replace("/", os.sep),
            ), nb, active
        return logical_path, None, None

    def _nb_record_output_tree(self, logical_path: str, physical_path: str):

        rel, fs_root, nb, active = self._nb_active_state_for_logical(logical_path)
        if not (rel and fs_root and nb and active):
            return False

        if os.path.isdir(physical_path):
            self._nb_apply(rel)
            for root, dirs, files in os.walk(physical_path):
                for d in dirs:
                    p = os.path.join(root, d)
                    child_rel = rel + "/" + os.path.relpath(p, physical_path).replace("\\", "/")
                    self._nb_apply(child_rel)
                for fn in files:
                    p = os.path.join(root, fn)
                    child_rel = rel + "/" + os.path.relpath(p, physical_path).replace("\\", "/")
                    self._nb_apply(child_rel)
        elif os.path.isfile(physical_path):
            self._nb_apply(rel)

        nb.record_tree_edit(fs_root, active["name"], rel, physical_path)
        try:
            self.reload_config_cache()
        except Exception:
            pass
        return True

    def _nb_remap_config(self, src_cfg_dir: str, dst_cfg_dir: str, old_rel: str, new_rel: str):

        old_rel = old_rel.replace("\\", "/").strip("/")
        new_rel = new_rel.replace("\\", "/").strip("/")
        if not old_rel or not new_rel:
            return
        old_root = old_rel.split("/")[0]
        new_root = new_rel.split("/")[0]
        os.makedirs(dst_cfg_dir, exist_ok=True)

        ctx_raw = _load_ctx_from_dir(src_cfg_dir)
        fs_raw = _load_fs_full_from_dir(src_cfg_dir)

        def _esc(r):
            return "/" + "/".join(p.replace(".", "\\.") for p in r.split("/"))

        old_ctx_prefix = _esc(old_rel)
        new_ctx_prefix = _esc(new_rel)

        def _remove_prefix(path, prefix, is_ctx):
            if not os.path.isfile(path):
                return
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                lines = [ln.rstrip("\n") for ln in f]
            out = []
            for ln in lines:
                s = ln.strip()
                if not s or s.startswith("#"):
                    out.append(ln)
                    continue
                key = s.split()[0]
                if key == prefix or key.startswith(prefix + "/"):
                    continue
                out.append(ln)
            with open(path, "w", encoding="utf-8", errors="ignore") as f:
                f.write("\n".join(out) + ("\n" if out else ""))

        _remove_prefix(os.path.join(src_cfg_dir, old_root + "_file_contexts"), old_ctx_prefix, True)
        _remove_prefix(os.path.join(src_cfg_dir, old_root + "_fs_config"), old_rel, False)

        def _append_entries(path, entries):
            if not entries:
                return
            if not os.path.isfile(path):
                open(path, "w", encoding="utf-8").close()
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                lines = [ln.rstrip("\n") for ln in f]
            existing = {
                ln.strip().split()[0]
                for ln in lines
                if ln.strip() and not ln.strip().startswith("#")
            }
            out = list(lines)
            for key, rest in entries:
                if key not in existing:
                    out.append(f"{key} {rest}")
            out.sort()
            with open(path, "w", encoding="utf-8", errors="ignore") as f:
                f.write("\n".join(out) + ("\n" if out else ""))

        ctx_entries = []
        for key, val in ctx_raw.items():
            if key == old_ctx_prefix or key.startswith(old_ctx_prefix + "/"):
                ctx_entries.append((new_ctx_prefix + key[len(old_ctx_prefix):], val))

        fs_entries = []
        for key, val in fs_raw.items():
            if key == old_rel or key.startswith(old_rel + "/"):
                fs_entries.append((new_rel + key[len(old_rel):], val))

        _append_entries(os.path.join(dst_cfg_dir, new_root + "_file_contexts"), ctx_entries)
        _append_entries(os.path.join(dst_cfg_dir, new_root + "_fs_config"), fs_entries)

    def dropEvent(self, event):
        # Reset highlight saat drop terjadi
        self._clear_drag_hover()

        data = event.mimeData()
        main = self.window()

        target_item = self.itemAt(event.position().toPoint())
        if not target_item:
            target_item = (self.invisibleRootItem() if self.display_mode == "single_dir"
                           else self.topLevelItem(0))
            if not target_item or not target_item.data(0, Qt.UserRole):
                event.ignore()
                return

        target_link = target_item.data(0, APKTOOL_LINK_ROLE)
        if target_link:
            target_path = target_link
        else:
            target_path = target_item.data(0, Qt.UserRole)
            if os.path.isfile(target_path):
                target_path = os.path.dirname(target_path)

        drag_paths = getattr(main, "drag_src_paths", None) if main else None
        paired_cross_apk_drop = (
            isinstance(event.source(), FileTree)
            and event.source() is not self
            and bool(drag_paths)
            and all(isinstance(path, str) and path.lower().endswith(".apk")
                    for path in drag_paths)
        )
        # The target poller's refresh_folder already preserves expanded rows.
        # Avoid walking a potentially huge visible tree on APK drop.
        expanded = set() if paired_cross_apk_drop else self.save_expanded_paths()

        # Reset session policy tiap kali ada drop baru
        self.drop_conflict_policy = None


        if data.hasFormat("application/x-rdt-archive"):
            raw = bytes(data.data("application/x-rdt-archive")).decode("utf-8")
            internal_paths = [p for p in raw.split("\n") if p]

            archive_tab = main.tab.currentWidget()
            if not hasattr(archive_tab, "filepath"):
                event.ignore()
                return

            archive_path = archive_tab.filepath
            target_phys, target_nb, target_active = self._nb_resolve_write_path(target_path)
            branch_target = bool(target_nb and target_active)

            from subprocess import run

            def worker():
                os.makedirs(target_phys, exist_ok=True)
                tool_path = os.path.normpath(
                    os.path.join(
                        os.path.dirname(__file__),
                        "..",
                        "core",
                        "extract_archive.py"
                    )
                )

                cmd = [
                    sys.executable,
                    tool_path,
                    "extract",
                    archive_path,
                    *internal_paths,
                    "to",
                    target_phys
                ]
                p = run(cmd, capture_output=True, text=True)
                return p.returncode == 0, p.stderr

            def after(ok, msg):
                if main:
                    main.pop_task("extracting-archive")
                if ok:
                    if branch_target:
                        for name in os.listdir(target_phys):
                            logical = os.path.join(target_path, name)
                            physical = os.path.join(target_phys, name)
                            rel, fs_root, nb, active = self._nb_active_state_for_logical(logical)
                            if nb and active and os.path.exists(logical):
                                nb.record_delete(fs_root, active["name"], rel, prune_overlay=False)
                            self._nb_record_output_tree(logical, physical)
                    parent = self.find_item_by_path(self.topLevelItem(0), target_path)
                    if parent:
                        self.refresh_folder(parent)
                    self.restore_expanded_paths(expanded)
                    if main:
                        main.set_loading_finished("Archive extracted")
                else:
                    if main:
                        main.set_loading_failed("Archive extract failed")

            if main:
                main.push_task("extracting-archive")

            run_in_thread(worker, None, callback=after)
            event.acceptProposedAction()
            return


        if data.hasFormat("application/x-rdt-mount"):
            raw = bytes(data.data("application/x-rdt-mount")).decode("utf-8")
            import json
            try:
                items_info = json.loads(raw)
            except Exception:
                lines = [l for l in raw.split("\n") if l]
                if len(lines) < 2:
                    event.ignore()
                    return
                img_path, internals = lines[0], lines[1:]
                items_info = [(img_path, i, os.path.basename(i)) for i in internals]

            # jangan menjatuhkan isi image ke dalam mount itu sendiri
            if self.is_mount_node(target_item):
                event.ignore()
                return
            if not os.path.isdir(target_path):
                event.ignore()
                return

            target_phys, target_nb, target_active = \
                self._nb_resolve_write_path(target_path)
            branch_target = bool(target_nb and target_active)

            event.acceptProposedAction()
            self.drop_mount_extract(items_info, target_path,
                                    target_phys=target_phys,
                                    branch_target=branch_target,
                                    expanded=expanded)
            return


        if data.hasUrls() and not isinstance(event.source(), FileTree):
            urls = [u.toLocalFile() for u in data.urls() if u.toLocalFile()]
            if not urls:
                event.ignore()
                return

            # Validasi: semua path harus benar-benar ada di disk
            urls = [p for p in urls if os.path.exists(p)]
            if not urls:
                event.ignore()
                return

            # Terima event DULU, baru push task — bukan saat hover
            event.acceptProposedAction()

            task_label = f"moving-external-{id(urls)}"
            if main:
                main.push_task(task_label)

            target_phys, target_nb, target_active = self._nb_resolve_write_path(target_path)
            branch_target = bool(target_nb and target_active)

            def worker(_urls=urls, _target=target_path, _target_phys=target_phys, _branch_target=branch_target):
                try:
                    os.makedirs(_target_phys, exist_ok=True)
                    # Kumpulkan konflik dulu untuk multi-file dialog
                    file_conflicts = []
                    for src in _urls:
                        dst = os.path.join(_target_phys, os.path.basename(src))
                        logical_dst = os.path.join(_target, os.path.basename(src))
                        if not os.path.isdir(src) and (os.path.exists(dst) or (_branch_target and os.path.exists(logical_dst))):
                            file_conflicts.append((os.path.basename(src), src, dst))

                    # Resolusi konflik sebelum eksekusi
                    per_file = {}
                    if file_conflicts:
                        if len(file_conflicts) == 1:
                            fname, src_fc, dst_fc = file_conflicts[0]
                            policy = ask_file_conflict(
                                self, fname,
                                src_path=src_fc, dst_path=dst_fc,
                                show_bulk=False,
                            )
                            if policy == "cancel":
                                return False, "Cancelled"
                            per_file[fname] = "skip" if policy in ("skip", "skip_all") else "replace"
                        else:
                            policy, per_file = ask_multi_file_conflict(self, file_conflicts)
                            if policy == "cancel":
                                return False, "Cancelled"

                    # Eksekusi semua URL
                    for src in _urls:
                        dst = os.path.join(_target_phys, os.path.basename(src))
                        fname = os.path.basename(src)

                        if os.path.isdir(src):
                            if os.path.exists(dst):
                                self.drop_conflict_policy = None
                                try:
                                    self._merge_internal_folder(src, dst, move=False)
                                except RuntimeError:
                                    return False, "Cancelled"
                                finally:
                                    self.drop_conflict_policy = None
                            else:
                                shutil.copytree(src, dst)
                        else:
                            if os.path.exists(dst):
                                action = per_file.get(fname, "skip")
                                if action == "skip":
                                    continue
                                os.remove(dst)
                            shutil.copy2(src, dst)

                    return True, None
                except Exception as e:
                    return False, str(e)

            def after(ok, msg, _urls=urls, _target=target_path, _target_phys=target_phys, _label=task_label, _branch_target=branch_target):
                self.resume_size_probe()
                if main:
                    main.pop_task(_label)

                if not ok:
                    if main:
                        main.set_loading_failed("External drop failed")
                    return

                for src in _urls:
                    new_full = os.path.join(_target, os.path.basename(src))
                    new_phys = os.path.join(_target_phys, os.path.basename(src))
                    rel_new = os.path.relpath(new_full, self.base).replace("\\", "/")


                    self._sniff_dropped_magic(new_phys)

                    if _branch_target:
                        rel, fs_root, nb, active = self._nb_active_state_for_logical(new_full)
                        if nb and active and os.path.exists(new_full):
                            nb.record_delete(fs_root, active["name"], rel, prune_overlay=False)
                        self._nb_record_output_tree(new_full, new_phys)
                    elif self.is_inside_fs_root(rel_new):
                        self.engine.handle_fs_transfer(
                            old_rel=None,
                            new_rel=rel_new,
                            old_base=None,
                            new_base=self.base
                        )

                parent = self.find_item_by_path(self.topLevelItem(0), _target)
                if parent:
                    self.refresh_folder(parent)

                self.restore_expanded_paths(expanded)

                if main:
                    n = len(_urls)
                    main.set_loading_finished(f"Copied {n} item{'s' if n != 1 else ''}")

            self.pause_size_probe()
            run_in_thread(worker, None, callback=after)
            return


        src_paths = getattr(main, "drag_src_paths", None) if main else None
        src_phys_paths = getattr(main, "drag_src_phys_paths", None) if main else None
        src_path  = main.drag_src_path if main else None
        if not src_path:
            return


        _source_tree = event.source() if isinstance(event.source(), FileTree) else None
        if src_paths and (len(src_paths) > 1 or (
            _source_tree is not None and _source_tree is not self
            and src_path.lower().endswith(".apk")
        )):
            self._drop_multi_internal(src_paths, target_path, target_item, expanded, event, main, src_phys_paths)
            return


        src_tree = None
        dst_tree = self

        if main:
            if hasattr(main, "tree") and src_path.startswith(main.tree.base):
                src_tree = main.tree
            elif hasattr(main, "tree2") and src_path.startswith(main.tree2.base):
                src_tree = main.tree2

        if not src_tree:
            print("[DROP] src_tree not found")
            return

        src_base = src_tree.base
        is_cross_tree = src_tree is not dst_tree


        if main:
            main.push_task("moving-internal")

        if src_base and os.path.isdir(src_path):
            _rel_src_guard = os.path.relpath(src_path, src_base).replace("\\", "/")
            if _rel_src_guard == "config":
                if main:
                    main.pop_task("moving-internal", False)
                    main.set_loading_failed("config folder cannot be moved")
                AppDialog.warning(
                    self, t("rc_not_allowed_title"),
                    "The <b>config</b> folder cannot be moved.<br>"
                    "You can move individual files inside it."
                )
                event.ignore()
                return


        if is_cross_tree:
            dst_base = dst_tree.base

            rel_src = os.path.relpath(src_path, src_base).replace("\\", "/")
            part = rel_src.split("/")[0]

            is_root_folder = (
                os.path.isdir(src_path) and
                rel_src == part
            )

            # is_root_folder hanya berlaku kalau user drop langsung ke root dst_tree
            # kalau drop ke subfolder, perlakukan sebagai subfolder biasa (else branch)
            target_is_dst_root = os.path.normpath(target_path) == os.path.normpath(dst_base)
            if not target_is_dst_root:
                is_root_folder = False

            if is_root_folder:
                # ROOT FOLDER: conflict dialog dulu di main thread sebelum ke worker
                src_cfg  = os.path.join(src_base, "config")
                root_dest = os.path.join(dst_base, part)


                if dst_tree.nb_is_active(part):
                    if main: main.pop_task("moving-internal", False)
                    main.set_loading_failed("Cannot move a filesystem folder into a tree where that filesystem has an active branch")
                    event.ignore()
                    return

                # dst_cfg: kalau dst fs_root ini punya branch aktif → branch config dir
                # (di sini part tidak aktif karena sudah diblok di atas, jadi ini fallback normal)
                dst_cfg  = os.path.join(dst_base, "config")

                def _move_fs_identity_files():
                    os.makedirs(dst_cfg, exist_ok=True)
                    for suffix in ("_fs_options", "_info"):
                        s = os.path.join(src_cfg, part + suffix)
                        d = os.path.join(dst_cfg, part + suffix)
                        if os.path.isfile(s) and not os.path.exists(d):
                            shutil.move(s, d)

                def _move_root_config_entries():
                    dst_tree.engine.move_paths_cross(src_tree.engine, part, part)
                    dst_tree.ctx = dst_tree.engine.ctx
                    dst_tree.fs = dst_tree.engine.fs
                    src_tree.ctx = src_tree.engine.ctx
                    src_tree.fs = src_tree.engine.fs

                # Kalau ada konflik, resolve dulu di main thread
                if os.path.exists(root_dest):
                    print("[MERGE MODE] root already exists, merging instead")
                    self.drop_conflict_policy = None
                    try:
                        self._merge_internal_folder(src_path, root_dest)
                    except RuntimeError:
                        self.drop_conflict_policy = None
                        if main: main.cancel_task("moving-internal")
                        event.ignore()
                        return
                    finally:
                        self.drop_conflict_policy = None

                    # Dialog selesai, jalankan I/O berat di background
                    def _worker_root_merge():
                        _move_fs_identity_files()
                        dst_tree.ctx = load_file_context(dst_base)
                        dst_tree.fs  = load_fs_config(dst_base)
                        src_tree.ctx = load_file_context(src_base)
                        src_tree.fs  = load_fs_config(src_base)
                        return True, ""

                    def _after_root_merge(ok=True, msg=""):
                        src_tree.resume_size_probe()
                        dst_tree.resume_size_probe()
                        try:
                            _move_root_config_entries()
                        except Exception as e:
                            if main:
                                main.pop_task("moving-internal", False)
                                main.set_loading_failed(f"Config move failed: {e}")
                            return

                        dst_tree.engine.rebuild_root_map()
                        src_tree.engine.rebuild_root_map()

                        si = src_tree.find_item_by_path(src_tree.topLevelItem(0), src_path)
                        if si:
                            src_tree.refresh_folder(si.parent() or src_tree.topLevelItem(0))
                        else:
                            src_tree.refresh_folder(src_tree.topLevelItem(0))

                        di = dst_tree.find_item_by_path(dst_tree.topLevelItem(0), root_dest)
                        if di:
                            dst_tree.refresh_folder(di)
                        else:
                            dst_tree.refresh_folder(dst_tree.topLevelItem(0))

                        src_tree.restore_expanded_paths(expanded)
                        if main:
                            main.pop_task("moving-internal", True)
                            main.maybe_finish_loading()

                    src_tree.pause_size_probe()
                    dst_tree.pause_size_probe()
                    run_in_thread(_worker_root_merge, None, callback=_after_root_merge)
                    event.acceptProposedAction()
                    return

                # Tidak ada konflik — jalankan semua di background
                def _worker_root_move():
                    try:
                        _move_fs_identity_files()
                        shutil.move(src_path, root_dest)
                        dst_tree.ctx = load_file_context(dst_base)
                        dst_tree.fs  = load_fs_config(dst_base)
                        src_tree.ctx = load_file_context(src_base)
                        src_tree.fs  = load_fs_config(src_base)
                        return True, ""
                    except Exception as e:
                        return False, str(e)

                def _after_root_move(ok=True, msg=""):
                    src_tree.resume_size_probe()
                    dst_tree.resume_size_probe()
                    if not ok:
                        if main:
                            main.pop_task("moving-internal", False)
                            main.set_loading_failed(f"Cross-tree move failed: {msg}")
                        return

                    try:
                        _move_root_config_entries()
                    except Exception as e:
                        if main:
                            main.pop_task("moving-internal", False)
                            main.set_loading_failed(f"Config move failed: {e}")
                        return

                    dst_tree.engine.rebuild_root_map()
                    src_tree.engine.rebuild_root_map()

                    si = src_tree.find_item_by_path(src_tree.topLevelItem(0), src_path)
                    if si:
                        src_tree.refresh_folder(si.parent() or src_tree.topLevelItem(0))
                    else:
                        src_tree.refresh_folder(src_tree.topLevelItem(0))

                    di = dst_tree.find_item_by_path(dst_tree.topLevelItem(0), root_dest)
                    if di:
                        dst_tree.refresh_folder(di)
                    else:
                        dst_tree.refresh_folder(dst_tree.topLevelItem(0))

                    src_tree.restore_expanded_paths(expanded)
                    if main:
                        main.pop_task("moving-internal", True)
                        main.maybe_finish_loading()

                src_tree.pause_size_probe()
                dst_tree.pause_size_probe()
                run_in_thread(_worker_root_move, None, callback=_after_root_move)
                event.acceptProposedAction()
                return

            else:
                # FILE atau SUBFOLDER: pindah ke target_path
                cross_dest = os.path.join(target_path, os.path.basename(src_path))


                _ct_rel_dst = os.path.relpath(cross_dest, dst_base).replace("\\", "/")
                _ct_dst_root = _ct_rel_dst.split("/")[0]
                _ct_dst_in_branch = dst_tree.nb_is_active(_ct_dst_root)

                # Juga cek apakah src dari branch aktif (cross-tree branch→branch / branch→folder)
                _ct_rel_src = os.path.relpath(src_path, src_base).replace("\\", "/")
                _ct_src_root = _ct_rel_src.split("/")[0]
                _ct_src_in_branch = src_tree.nb_is_active(_ct_src_root)

                if _ct_dst_in_branch:
                    # Dst di dalam branch: redirect physical path ke branch fs dir
                    _ct_nb_dst      = dst_tree._get_nb_manager()
                    _ct_nb_dst_act  = _ct_nb_dst.get_active_branch(_ct_dst_root) if _ct_nb_dst else None
                    _ct_nb_dst_name = _ct_nb_dst_act["name"] if _ct_nb_dst_act else None

                    # branch fs dir untuk dst — ini yang akan menerima file/folder
                    _ct_branch_fs_dir = _ct_nb_dst._branch_fs_dir(_ct_dst_root, _ct_nb_dst_name)
                    # rel dari dst_root ke target_path, untuk menentukan subpath di dalam branch fs dir
                    _ct_rel_target_inside = os.path.relpath(target_path, dst_base).replace("\\", "/")
                    # path fisik di branch fs dir yang sesuai dengan target_path
                    _ct_branch_target = os.path.join(
                        _ct_nb_dst._branch_dir(_ct_dst_root, _ct_nb_dst_name),
                        _ct_rel_target_inside.replace("/", os.sep)
                    )
                    _ct_branch_dest = os.path.join(_ct_branch_target, os.path.basename(src_path))

                    # Kalau src juga dari branch aktif, ambil source + overlay branch edits
                    _ct_nb_src      = src_tree._get_nb_manager() if _ct_src_in_branch else None
                    _ct_nb_src_act  = _ct_nb_src.get_active_branch(_ct_src_root) if _ct_nb_src else None
                    _ct_nb_src_name = _ct_nb_src_act["name"] if _ct_nb_src_act else None
                    _ct_src_branch_json = (
                        _ct_nb_src._read_branch_json(
                            _ct_nb_src._branch_json_path(_ct_src_root, _ct_nb_src_name)
                        ) if _ct_nb_src and _ct_nb_src_name else {"edited_files": [], "deleted_paths": []}
                    )
                    _ct_src_branch_fs_dir = (
                        _ct_nb_src._branch_fs_dir(_ct_src_root, _ct_nb_src_name)
                        if _ct_nb_src and _ct_nb_src_name else None
                    )

                    # Conflict check tetap berlaku — tapi cek di branch fs dir dst
                    if os.path.exists(_ct_branch_dest):
                        if os.path.isfile(src_path):
                            policy = self.ask_drop_conflict_policy(
                                os.path.basename(src_path),
                                src_path=src_path,
                                dst_path=_ct_branch_dest,
                                folder_context=False,
                            )
                            if policy in ("cancel", "skip", "skip_all"):
                                if main: main.cancel_task("moving-internal")
                                event.ignore()
                                return
                            try:
                                os.remove(_ct_branch_dest)
                            except Exception:
                                pass
                        else:
                            self.drop_conflict_policy = None
                            try:
                                self._merge_internal_folder(src_path, _ct_branch_dest)
                            except RuntimeError:
                                self.drop_conflict_policy = None
                                if main: main.cancel_task("moving-internal")
                                event.ignore()
                                return
                            finally:
                                self.drop_conflict_policy = None

                            def _worker_ct_branch_merge():
                                try:
                                    # Src dari branch: record_delete di src branch
                                    if _ct_src_in_branch and _ct_nb_src and _ct_nb_src_name:
                                        _ct_nb_src.record_delete(
                                            _ct_src_root, _ct_nb_src_name, _ct_rel_src)
                                    # Record semua file hasil merge di branch dst sebagai edited
                                    for _r, _d, _fs in os.walk(_ct_branch_dest):
                                        for _fn in _fs:
                                            _fp = os.path.join(_r, _fn)
                                            _frel = os.path.relpath(_fp, dst_base).replace("\\", "/")
                                            _ct_nb_dst.record_edit(
                                                _ct_dst_root, _ct_nb_dst_name,
                                                os.path.join(dst_base, _frel.replace("/", os.sep))
                                            )
                                    dst_tree.ctx = load_file_context(dst_base)
                                    dst_tree.fs  = load_fs_config(dst_base)
                                    return True, ""
                                except Exception as e:
                                    return False, str(e)

                            def _after_ct_branch_merge(ok=True, msg=""):
                                src_tree.resume_size_probe()
                                dst_tree.resume_size_probe()
                                dst_tree.engine.ctx = dst_tree.ctx
                                dst_tree.engine.fs  = dst_tree.fs
                                dst_tree.engine.rebuild_root_map()

                                si = src_tree.find_item_by_path(src_tree.topLevelItem(0), src_path)
                                if si:
                                    src_tree.refresh_folder(si.parent() or src_tree.topLevelItem(0))
                                else:
                                    src_tree.refresh_folder(src_tree.topLevelItem(0))

                                di = dst_tree.find_item_by_path(dst_tree.topLevelItem(0), target_path)
                                if di:
                                    dst_tree.refresh_folder(di)
                                else:
                                    dst_tree.refresh_folder(dst_tree.topLevelItem(0))

                                src_tree.restore_expanded_paths(expanded)
                                if main:
                                    main.pop_task("moving-internal", True)
                                    main.maybe_finish_loading()

                            src_tree.pause_size_probe()
                            dst_tree.pause_size_probe()
                            run_in_thread(_worker_ct_branch_merge, None, callback=_after_ct_branch_merge)
                            event.acceptProposedAction()
                            return

                    def _worker_ct_branch_move():
                        try:
                            os.makedirs(_ct_branch_target, exist_ok=True)
                            if os.path.isfile(src_path):
                                # File tunggal
                                if _ct_src_in_branch and _ct_src_branch_fs_dir:
                                    # Ambil versi branch kalau ada
                                    _src_rel_inside = "/".join(_ct_rel_src.split("/")[1:])
                                    _src_phys = os.path.join(
                                        _ct_src_branch_fs_dir,
                                        _src_rel_inside.replace("/", os.sep)
                                    )
                                    if os.path.isfile(_src_phys):
                                        shutil.copy2(_src_phys, _ct_branch_dest)
                                    else:
                                        shutil.copy2(src_path, _ct_branch_dest)
                                else:
                                    shutil.copy2(src_path, _ct_branch_dest)
                            else:
                                # Folder: copy source dulu
                                if os.path.exists(_ct_branch_dest):
                                    shutil.rmtree(_ct_branch_dest)
                                if os.path.isdir(src_path):
                                    shutil.copytree(src_path, _ct_branch_dest, symlinks=True)
                                else:
                                    os.makedirs(_ct_branch_dest, exist_ok=True)

                                if _ct_src_in_branch:
                                    # Overlay edited_files dari src branch
                                    for _entry in _ct_src_branch_json.get("edited_files", []):
                                        _erel  = _entry["rel_path"].replace("\\", "/")
                                        _ephys = _entry["abs_branch_path"]
                                        if not (_erel == _ct_rel_src or _erel.startswith(_ct_rel_src + "/")):
                                            continue
                                        _erel_inside = _erel[len(_ct_rel_src):].lstrip("/")
                                        _edst = os.path.join(_ct_branch_dest, _erel_inside.replace("/", os.sep))
                                        if os.path.isfile(_ephys):
                                            os.makedirs(os.path.dirname(_edst), exist_ok=True)
                                            shutil.copy2(_ephys, _edst)

                                    # Hapus deleted_paths dari copy di branch dst
                                    for _drel in _ct_src_branch_json.get("deleted_paths", []):
                                        _drel = _drel.replace("\\", "/")
                                        if not (_drel == _ct_rel_src or _drel.startswith(_ct_rel_src + "/")):
                                            continue
                                        _drel_inside = _drel[len(_ct_rel_src):].lstrip("/")
                                        _ddst = os.path.join(_ct_branch_dest, _drel_inside.replace("/", os.sep))
                                        if os.path.isdir(_ddst):
                                            shutil.rmtree(_ddst)
                                        elif os.path.isfile(_ddst):
                                            os.remove(_ddst)

                            # Src dari branch → record_delete di src branch
                            # Src bukan branch → hapus fisik (ini move, bukan copy)
                            if _ct_src_in_branch and _ct_nb_src and _ct_nb_src_name:
                                _ct_nb_src.record_delete(
                                    _ct_src_root, _ct_nb_src_name, _ct_rel_src)
                            else:
                                try:
                                    if os.path.isdir(src_path):
                                        shutil.rmtree(src_path)
                                    else:
                                        os.remove(src_path)
                                except Exception:
                                    pass

                            # Record semua yang masuk ke branch dst sebagai edited_files
                            _ct_rel_dst_full = os.path.relpath(_ct_branch_dest, dst_base).replace("\\", "/")
                            if os.path.exists(os.path.join(dst_base, _ct_rel_dst_full.replace("/", os.sep))):
                                _ct_nb_dst.record_delete(
                                    _ct_dst_root, _ct_nb_dst_name,
                                    _ct_rel_dst_full, prune_overlay=False)
                            if os.path.isfile(_ct_branch_dest):
                                _ct_nb_dst.record_edit(
                                    _ct_dst_root, _ct_nb_dst_name,
                                    os.path.join(dst_base,
                                        os.path.relpath(_ct_branch_dest, dst_base).replace("/", os.sep))
                                )
                            else:
                                for _r, _d, _fs in os.walk(_ct_branch_dest):
                                    for _fn in _fs:
                                        _fp = os.path.join(_r, _fn)
                                        _frel_abs = os.path.join(
                                            dst_base,
                                            os.path.relpath(_fp, dst_base).replace("/", os.sep)
                                        )
                                        _ct_nb_dst.record_edit(_ct_dst_root, _ct_nb_dst_name, _frel_abs)

                            # Config: remap ctx/fs entries dari src ke branch config dst
                            _ct_nb_dst_cfg_dir = _ct_nb_dst._branch_config_dir(_ct_dst_root, _ct_nb_dst_name)

                            if _ct_src_in_branch and _ct_nb_src and _ct_nb_src_name:
                                _bctx_raw = _load_ctx_from_dir(
                                    _ct_nb_src._branch_config_dir(_ct_src_root, _ct_nb_src_name))
                                _bfs_raw  = _load_fs_full_from_dir(
                                    _ct_nb_src._branch_config_dir(_ct_src_root, _ct_nb_src_name))
                            else:
                                _bctx_raw = _load_ctx_from_dir(os.path.join(src_base, "config"))
                                _bfs_raw  = _load_fs_full_from_dir(os.path.join(src_base, "config"))

                            def _esc_ct(r):
                                return "/" + "/".join(p.replace(".", "\\.") for p in r.split("/"))

                            _src_ctx_pfx = _esc_ct(_ct_rel_src)
                            _dst_ctx_pfx = _esc_ct(_ct_rel_dst_full)

                            _ctx_target = os.path.join(_ct_nb_dst_cfg_dir, _ct_dst_root + "_file_contexts")
                            _fs_target  = os.path.join(_ct_nb_dst_cfg_dir, _ct_dst_root + "_fs_config")

                            # ctx: hapus entry lama di src config, tulis entry baru di dst config
                            # (MOVE bukan copy — src config harus dibersihkan)
                            if _ct_src_in_branch and _ct_nb_src and _ct_nb_src_name:
                                _src_ctx_cfg = os.path.join(
                                    _ct_nb_src._branch_config_dir(_ct_src_root, _ct_nb_src_name),
                                    _ct_src_root + "_file_contexts")
                                _src_fs_cfg  = os.path.join(
                                    _ct_nb_src._branch_config_dir(_ct_src_root, _ct_nb_src_name),
                                    _ct_src_root + "_fs_config")
                            else:
                                _src_ctx_cfg = os.path.join(src_base, "config", _ct_src_root + "_file_contexts")
                                _src_fs_cfg  = os.path.join(src_base, "config", _ct_src_root + "_fs_config")

                            # Hapus entry lama dari src ctx
                            if os.path.isfile(_src_ctx_cfg):
                                with open(_src_ctx_cfg, "r", encoding="utf-8", errors="ignore") as _f:
                                    _src_ctx_lines = [ln.rstrip("\n") for ln in _f]
                                _src_ctx_lines = [
                                    ln for ln in _src_ctx_lines
                                    if not (ln.strip() and not ln.strip().startswith("#")
                                            and (ln.strip().split()[0] == _src_ctx_pfx
                                                 or ln.strip().split()[0].startswith(_src_ctx_pfx + "/")))
                                ]
                                with open(_src_ctx_cfg, "w", encoding="utf-8", errors="ignore") as _f:
                                    _f.write("\n".join(_src_ctx_lines) + "\n")

                            # Hapus entry lama dari src fs
                            if os.path.isfile(_src_fs_cfg):
                                with open(_src_fs_cfg, "r", encoding="utf-8", errors="ignore") as _f:
                                    _src_fs_lines = [ln.rstrip("\n") for ln in _f]
                                _src_fs_lines = [
                                    ln for ln in _src_fs_lines
                                    if not (ln.strip() and not ln.strip().startswith("#")
                                            and (ln.strip().split()[0] == _ct_rel_src
                                                 or ln.strip().split()[0].startswith(_ct_rel_src + "/")))
                                ]
                                with open(_src_fs_cfg, "w", encoding="utf-8", errors="ignore") as _f:
                                    _f.write("\n".join(_src_fs_lines) + "\n")

                            # Tulis entry baru ke dst ctx
                            if os.path.isfile(_ctx_target):
                                with open(_ctx_target, "r", encoding="utf-8", errors="ignore") as _f:
                                    _ctx_lines = [ln.rstrip("\n") for ln in _f]
                                _existing_ctx_keys = {
                                    ln.strip().split()[0]
                                    for ln in _ctx_lines
                                    if ln.strip() and not ln.strip().startswith("#")
                                }
                                _ctx_appended = list(_ctx_lines)
                                for _k, _v in _bctx_raw.items():
                                    if _k == _src_ctx_pfx or _k.startswith(_src_ctx_pfx + "/"):
                                        _new_k = _dst_ctx_pfx + _k[len(_src_ctx_pfx):]
                                    else:
                                        continue
                                    if _new_k not in _existing_ctx_keys:
                                        _ctx_appended.append(f"{_new_k} {_v}")
                                _ctx_appended.sort()
                                with open(_ctx_target, "w", encoding="utf-8", errors="ignore") as _f:
                                    _f.write("\n".join(_ctx_appended) + "\n")

                            # Tulis entry baru ke dst fs
                            if os.path.isfile(_fs_target):
                                with open(_fs_target, "r", encoding="utf-8", errors="ignore") as _f:
                                    _fs_lines = [ln.rstrip("\n") for ln in _f]
                                _existing_fs_keys = {
                                    ln.strip().split()[0]
                                    for ln in _fs_lines
                                    if ln.strip() and not ln.strip().startswith("#")
                                }
                                _fs_appended = list(_fs_lines)
                                for _k, _v in _bfs_raw.items():
                                    if _k == _ct_rel_src or _k.startswith(_ct_rel_src + "/"):
                                        _new_k = _ct_rel_dst_full + _k[len(_ct_rel_src):]
                                    else:
                                        continue
                                    if _new_k not in _existing_fs_keys:
                                        _fs_appended.append(f"{_new_k} {_v}")
                                _fs_appended.sort()
                                with open(_fs_target, "w", encoding="utf-8", errors="ignore") as _f:
                                    _f.write("\n".join(_fs_appended) + "\n")

                            dst_tree.ctx = load_file_context(dst_base)
                            dst_tree.fs  = load_fs_config(dst_base)
                            return True, ""
                        except Exception as e:
                            return False, str(e)

                    def _after_ct_branch_move(ok, msg):
                        src_tree.resume_size_probe()
                        dst_tree.resume_size_probe()
                        if not ok:
                            if main:
                                main.pop_task("moving-internal", False)
                                main.set_loading_failed(f"Branch-aware cross-tree move failed: {msg}")
                            return

                        try:
                            dst_tree.reload_config_cache()
                        except Exception:
                            dst_tree.ctx = load_file_context(dst_base)
                            dst_tree.fs  = load_fs_config(dst_base)
                            if hasattr(dst_tree, "engine") and dst_tree.engine:
                                dst_tree.engine.ctx = dst_tree.ctx
                                dst_tree.engine.fs  = dst_tree.fs
                                dst_tree.engine.rebuild_root_map()

                        si = src_tree.find_item_by_path(src_tree.topLevelItem(0), src_path)
                        if si:
                            src_tree.refresh_folder(si.parent() or src_tree.topLevelItem(0))
                        else:
                            src_tree.refresh_folder(src_tree.topLevelItem(0))

                        di = dst_tree.find_item_by_path(dst_tree.topLevelItem(0), target_path)
                        if di:
                            dst_tree.refresh_folder(di)
                        else:
                            dst_tree.refresh_folder(dst_tree.topLevelItem(0))

                        src_tree.restore_expanded_paths(expanded)
                        if main:
                            main.pop_task("moving-internal", True)
                            main.maybe_finish_loading()

                    src_tree.pause_size_probe()
                    dst_tree.pause_size_probe()
                    run_in_thread(_worker_ct_branch_move, None, callback=_after_ct_branch_move)
                    event.acceptProposedAction()
                    return

                # dst bukan branch, jalur normal cross-tree

                # Konflik: resolve di main thread dulu
                if os.path.exists(cross_dest):
                    if os.path.isfile(src_path):
                        policy = self.ask_drop_conflict_policy(
                            os.path.basename(src_path),
                            src_path=src_path,
                            dst_path=cross_dest,
                            folder_context=False,
                        )
                        if policy in ("cancel", "skip", "skip_all"):
                            if main: main.cancel_task("moving-internal")
                            event.ignore()
                            return
                        # replace — hapus dulu di main thread (cepat)
                        try:
                            os.remove(cross_dest)
                        except Exception:
                            pass

                    else:
                        # Folder konflik — merge dialog di main thread
                        self.drop_conflict_policy = None
                        try:
                            self._merge_internal_folder(src_path, cross_dest)
                        except RuntimeError:
                            self.drop_conflict_policy = None
                            if main: main.cancel_task("moving-internal")
                            event.ignore()
                            return
                        finally:
                            self.drop_conflict_policy = None

                        def _worker_cross_merge():
                            dst_tree.ctx = load_file_context(dst_base)
                            dst_tree.fs  = load_fs_config(dst_base)
                            return True, ""

                        def _after_cross_merge(ok=True, msg=""):
                            src_tree.resume_size_probe()
                            dst_tree.resume_size_probe()
                            dst_tree.engine.ctx = dst_tree.ctx
                            dst_tree.engine.fs  = dst_tree.fs
                            dst_tree.engine.rebuild_root_map()

                            si = src_tree.find_item_by_path(src_tree.topLevelItem(0), src_path)
                            if si:
                                src_tree.refresh_folder(si.parent() or src_tree.topLevelItem(0))
                            else:
                                src_tree.refresh_folder(src_tree.topLevelItem(0))

                            di = dst_tree.find_item_by_path(dst_tree.topLevelItem(0), target_path)
                            if di:
                                dst_tree.refresh_folder(di)
                            else:
                                dst_tree.refresh_folder(dst_tree.topLevelItem(0))

                            src_tree.restore_expanded_paths(expanded)
                            if main:
                                main.pop_task("moving-internal", True)
                                main.maybe_finish_loading()

                        src_tree.pause_size_probe()
                        dst_tree.pause_size_probe()
                        run_in_thread(_worker_cross_merge, None, callback=_after_cross_merge)
                        event.acceptProposedAction()
                        return

                # Tidak ada konflik (atau file sudah dihapus) — move di background
                # Release watcher dulu biar Win nggak hold handle saat move
                self._detach_watchers_for(src_path)
                def _worker_cross_move():
                    shutil.move(src_path, cross_dest)
                    dst_tree.ctx = load_file_context(dst_base)
                    dst_tree.fs  = load_fs_config(dst_base)
                    return True, ""

                def _after_cross_move(ok=True, msg=""):
                    src_tree.resume_size_probe()
                    dst_tree.resume_size_probe()
                    if not ok:
                        if main:
                            main.pop_task("moving-internal", False)
                            main.set_loading_failed(msg)
                        return

                    rel_old = os.path.relpath(src_path, src_base).replace("\\", "/")
                    rel_new = os.path.relpath(cross_dest, dst_base).replace("\\", "/")
                    if dst_tree.is_inside_fs_root(rel_new):
                        dst_tree.engine.move_paths_cross(src_tree.engine, rel_old, rel_new)
                        # move_paths_cross sudah reload ctx/fs kedua engine — sync ke tree
                        src_tree.ctx = src_tree.engine.ctx
                        src_tree.fs  = src_tree.engine.fs
                        dst_tree.ctx = dst_tree.engine.ctx
                        dst_tree.fs  = dst_tree.engine.fs
                    else:
                        src_tree.engine.ctx = src_tree.ctx
                        src_tree.engine.fs  = src_tree.fs
                        dst_tree.engine.ctx = dst_tree.ctx
                        dst_tree.engine.fs  = dst_tree.fs

                    src_tree.engine.rebuild_root_map()
                    dst_tree.engine.rebuild_root_map()

                    si = src_tree.find_item_by_path(src_tree.topLevelItem(0), src_path)
                    if si:
                        src_tree.refresh_folder(si.parent() or src_tree.topLevelItem(0))
                    else:
                        src_tree.refresh_folder(src_tree.topLevelItem(0))

                    di = dst_tree.find_item_by_path(dst_tree.topLevelItem(0), target_path)
                    if di:
                        dst_tree.refresh_folder(di)
                    else:
                        dst_tree.refresh_folder(dst_tree.topLevelItem(0))

                    src_tree.restore_expanded_paths(expanded)
                    if main:
                        main.pop_task("moving-internal", True)
                        main.maybe_finish_loading()

                def _worker_cross_move_safe():
                    try:
                        _worker_cross_move()
                        return True, ""
                    except Exception as e:
                        return False, str(e)

                src_tree.pause_size_probe()
                dst_tree.pause_size_probe()
                run_in_thread(_worker_cross_move_safe, None, callback=_after_cross_move)
                event.acceptProposedAction()
                return


        new_full = os.path.join(target_path, os.path.basename(src_path))

        old_rel = os.path.relpath(src_path, src_base).replace("\\", "/")
        new_rel_preview = os.path.relpath(new_full, dst_tree.base).replace("\\", "/")

        old_root = old_rel.split("/")[0]
        new_root = new_rel_preview.split("/")[0]
        root_changed = (old_root != new_root)


        _nb_old_depth = len([p for p in old_rel.split("/") if p])
        if os.path.isdir(src_path) and _nb_old_depth == 1 and self.nb_is_active(old_root):
            if main: main.pop_task("moving-internal", False)
            main.set_loading_failed("Cannot move a filesystem folder that has an active branch")
            event.ignore()
            return

        print("[ROOT CHECK]")
        print("old_root:", old_root)
        print("new_root:", new_root)
        print("root_changed:", root_changed)


        # Case 1: drop tepat ke diri sendiri
        if os.path.normpath(new_full) == os.path.normpath(src_path):
            if main:
                main.cancel_task("moving-internal")
            event.ignore()
            return

        # Case 2: drop ke parent yang sudah jadi tempat asal (no-op move)
        if os.path.normpath(os.path.dirname(src_path)) == os.path.normpath(target_path):
            if main:
                main.cancel_task("moving-internal")
            event.ignore()
            return

        # Case 3: drop folder ke dalam dirinya sendiri (circular)
        if os.path.isdir(src_path):
            src_norm = os.path.normpath(os.path.realpath(src_path))
            dst_norm = os.path.normpath(os.path.realpath(new_full))
            if dst_norm.startswith(src_norm + os.sep) or dst_norm == src_norm:
                print("[DROP] Circular move blocked silently:", src_path, "->", new_full)
                if main:
                    main.cancel_task("moving-internal")
                event.ignore()
                return

        if os.path.exists(new_full):

            # FILE CONFLICT — dialog di main thread (cepat)
            if os.path.isfile(src_path):
                policy = self.ask_drop_conflict_policy(
                    os.path.basename(src_path),
                    src_path=src_path,
                    dst_path=new_full,
                    folder_context=False,
                )
                if policy == "cancel":
                    if main:
                        main.pop_task("moving-internal", False)
                        main.set_loading_failed("Move cancelled")
                    event.ignore()
                    return
                if policy in ("skip", "skip_all"):
                    if main:
                        main.pop_task("moving-internal", False)
                        main.set_loading_failed("Move skipped")
                    event.ignore()
                    return
                if policy in ("replace_all",):
                    self.drop_conflict_policy = "replace_all"
                try:
                    os.remove(new_full)
                except Exception:
                    pass

            # FOLDER CONFLICT → MERGE dialog di main thread, I/O berat di background
            else:
                self.drop_conflict_policy = None
                try:
                    self._merge_internal_folder(src_path, new_full)
                except RuntimeError:
                    if main:
                        main.pop_task("moving-internal", False)
                        main.set_loading_failed("Merge cancelled")
                    event.ignore()
                    return
                finally:
                    self.drop_conflict_policy = None

                rel_old = os.path.relpath(src_path, self.base).replace("\\", "/")
                rel_new = os.path.relpath(new_full, self.base).replace("\\", "/")

                _merge_new_root = rel_new.split("/")[0]
                _merge_nb       = self._get_nb_manager()
                _merge_nb_act   = _merge_nb.get_active_branch(_merge_new_root) if _merge_nb else None

                def _worker_merge_same():
                    try:
                        # Src dari branch aktif → record_delete, jangan hapus fisik source
                        _merge_src_root = rel_old.split("/")[0]
                        _merge_src_act  = _merge_nb.get_active_branch(_merge_src_root) if _merge_nb else None
                        if _merge_src_act:
                            _merge_nb.record_delete(_merge_src_root, _merge_src_act["name"], rel_old)
                    except Exception:
                        pass

                    # ── NeoBranch: kalau dst di branch, copy hasil merge ke branch fs dir
                    # dan record semua file yang berubah sebagai edited_files
                    if _merge_nb and _merge_nb_act:
                        _merge_branch_name = _merge_nb_act["name"]
                        _merge_branch_dir  = _merge_nb._branch_dir(_merge_new_root, _merge_branch_name)
                        # Walk new_full (source tree) dan copy ke branch fs dir
                        for _mr, _md, _mfs in os.walk(new_full):
                            for _mfn in _mfs:
                                _mfp     = os.path.join(_mr, _mfn)
                                _mfrel   = os.path.relpath(_mfp, self.base).replace("\\", "/")
                                _mf_dst  = os.path.join(_merge_branch_dir, _mfrel.replace("/", os.sep))
                                os.makedirs(os.path.dirname(_mf_dst), exist_ok=True)
                                shutil.copy2(_mfp, _mf_dst)
                                # record_edit butuh abs source path (tree_base path)
                                _merge_nb.record_edit(
                                    _merge_new_root, _merge_branch_name,
                                    os.path.join(self.base, _mfrel.replace("/", os.sep))
                                )

                    return True, ""

                def _after_merge_same(ok=True, msg=""):
                    self.resume_size_probe()
                    if _merge_nb and _merge_nb_act:
                        # Branch aktif di dst: reload config cache supaya overlay branch terpakai
                        try:
                            self.reload_config_cache()
                        except Exception:
                            self.ctx = load_file_context(self.base)
                            self.fs  = load_fs_config(self.base)
                            if hasattr(self, "engine") and self.engine:
                                self.engine.ctx = self.ctx
                                self.engine.fs  = self.fs
                                self.engine.rebuild_root_map()
                    elif self.is_inside_fs_root(rel_new):
                        self.engine.handle_fs_transfer(
                            old_rel=rel_old,
                            new_rel=rel_new,
                            old_base=self.base,
                            new_base=self.base
                        )
                    parent = self.find_item_by_path(self.topLevelItem(0), target_path)
                    if parent:
                        self.refresh_folder(parent)
                    self.restore_expanded_paths(expanded)
                    if main:
                        main.pop_task("moving-internal", True)
                        main.maybe_finish_loading()

                self.pause_size_probe()
                run_in_thread(_worker_merge_same, None, callback=_after_merge_same)
                event.acceptProposedAction()
                return


        _nb_src_in_branch = (
            os.path.isdir(src_path)
            and _nb_old_depth > 1
            and self.nb_is_active(old_root)
        )
        # File tunggal juga perlu branch-aware kalau branch aktif
        _nb_file_in_branch = (
            os.path.isfile(src_path)
            and self.nb_is_active(old_root)
        )


        _nb_dst_in_branch = (
            not (_nb_src_in_branch or _nb_file_in_branch)
            and self.nb_is_active(new_root)
        )

        if _nb_src_in_branch or _nb_file_in_branch:
            _nb2      = self._get_nb_manager()
            _nb2_act  = _nb2.get_active_branch(old_root) if _nb2 else None
            if _nb2 and _nb2_act:
                _nb2_branch_name    = _nb2_act["name"]
                _nb2_branch_fs_dir  = _nb2._branch_fs_dir(old_root, _nb2_branch_name)
                _nb2_branch_cfg_dir = _nb2._branch_config_dir(old_root, _nb2_branch_name)
                _nb2_branch_json    = _nb2._read_branch_json(
                    _nb2._branch_json_path(old_root, _nb2_branch_name))

                # path fisik item ini di dalam branch fs dir
                # old_rel = "vendor/a/b/c" → rel_inside = "a/b/c"
                _nb2_rel_inside = "/".join(old_rel.split("/")[1:])
                _nb2_phys = os.path.join(
                    _nb2_branch_fs_dir,
                    _nb2_rel_inside.replace("/", os.sep)
                ) if _nb2_rel_inside else _nb2_branch_fs_dir

                def _worker_nb_item_move_safe():
                    try:
                        # 1. Copy/overlay ke target — SEMUA I/O berat di sini

                        if os.path.isfile(src_path):
                            # File tunggal: ambil versi branch kalau ada, otherwise source
                            os.makedirs(os.path.dirname(new_full), exist_ok=True)
                            if os.path.isfile(_nb2_phys):
                                shutil.copy2(_nb2_phys, new_full)
                            else:
                                shutil.copy2(src_path, new_full)
                        else:
                            # Folder: copy source dulu
                            if os.path.exists(new_full):
                                shutil.rmtree(new_full)
                            if os.path.isdir(src_path):
                                shutil.copytree(src_path, new_full, symlinks=True)
                            else:
                                os.makedirs(new_full, exist_ok=True)

                            # Overlay edited_files yang scope-nya di bawah folder ini
                            for _entry in _nb2_branch_json.get("edited_files", []):
                                _erel  = _entry["rel_path"].replace("\\", "/")
                                _ephys = _entry["abs_branch_path"]
                                if not (_erel == old_rel or _erel.startswith(old_rel + "/")):
                                    continue
                                _erel_inside = _erel[len(old_rel):].lstrip("/")
                                _edst = os.path.join(new_full, _erel_inside.replace("/", os.sep))
                                if os.path.isfile(_ephys):
                                    os.makedirs(os.path.dirname(_edst), exist_ok=True)
                                    shutil.copy2(_ephys, _edst)

                            # Hapus deleted_paths dari copy di target (source aman)
                            for _drel in _nb2_branch_json.get("deleted_paths", []):
                                _drel = _drel.replace("\\", "/")
                                if not (_drel == old_rel or _drel.startswith(old_rel + "/")):
                                    continue
                                _drel_inside = _drel[len(old_rel):].lstrip("/")
                                _ddst = os.path.join(new_full, _drel_inside.replace("/", os.sep))
                                if os.path.isdir(_ddst):
                                    shutil.rmtree(_ddst)
                                elif os.path.isfile(_ddst):
                                    os.remove(_ddst)

                            # Cleanup folder branch fisik (source tetap utuh)
                            if os.path.isdir(_nb2_phys):
                                shutil.rmtree(_nb2_phys)

                        # 2. Catat sebagai deleted di branch (logically moved)
                        _nb2.record_delete(old_root, _nb2_branch_name, old_rel)


                        _new_rel_nb_pre = os.path.relpath(new_full, src_base).replace('\\', '/')
                        _new_root_nb_pre = _new_rel_nb_pre.split('/')[0]
                        if root_changed and os.path.isdir(new_full):
                            _nb2_cfg_dir = os.path.join(src_base, 'config')
                            # Cek fs_type dari branch config dulu, fallback ke source config
                            _nb2_old_opt  = os.path.join(_nb2_branch_cfg_dir, old_root + '_fs_options')
                            _nb2_old_info = os.path.join(_nb2_branch_cfg_dir, old_root + '_info')
                            _nb2_src_opt  = os.path.join(src_base, 'config', old_root + '_fs_options')
                            _nb2_src_info = os.path.join(src_base, 'config', old_root + '_info')
                            if os.path.isfile(_nb2_old_opt) or os.path.isfile(_nb2_src_opt):
                                _nb2_fs_type = 'EROFS'
                            elif os.path.isfile(_nb2_old_info) or os.path.isfile(_nb2_src_info):
                                _nb2_fs_type = 'EXT'
                            else:
                                _nb2_fs_type = None
                            _nb2_fc  = os.path.join(_nb2_cfg_dir, _new_root_nb_pre + '_file_contexts')
                            _nb2_fs2 = os.path.join(_nb2_cfg_dir, _new_root_nb_pre + '_fs_config')
                            _nb2_opt = os.path.join(_nb2_cfg_dir, _new_root_nb_pre + '_fs_options')
                            _nb2_inf = os.path.join(_nb2_cfg_dir, _new_root_nb_pre + '_info')
                            if not ((os.path.isfile(_nb2_fc) and os.path.isfile(_nb2_fs2))
                                    and (os.path.isfile(_nb2_opt) or os.path.isfile(_nb2_inf))):
                                try:
                                    from main_ext.class_FilesystemBuilder import FilesystemBuilder
                                    _nb2_builder = FilesystemBuilder(
                                        src_base, new_full,
                                        forced_fs_type=_nb2_fs_type,
                                        source_fs_root=old_root,
                                        source_rel_prefix=old_rel,
                                    )
                                    _nb2_builder.create_filesystem()
                                    print(f'[NeoBranch] FilesystemBuilder: {_new_root_nb_pre}')
                                except Exception as _nb2_e:
                                    print(f'[NeoBranch] FilesystemBuilder failed: {_nb2_e}')


                        _new_rel_nb = os.path.relpath(new_full, src_base).replace("\\", "/")
                        _new_root_nb = _new_rel_nb.split("/")[0]

                        # Cek apakah dst masuk ke branch aktif
                        _nb3_act = None
                        if _new_root_nb != old_root:
                            # Pindah ke fs_root berbeda — cek branch di new_root
                            _nb3_mgr = self._get_nb_manager()
                            if _nb3_mgr:
                                _nb3_act = _nb3_mgr.get_active_branch(_new_root_nb)
                        else:
                            # Tetap di fs_root yang sama — branch yang sama (_nb2_act) masih aktif
                            _nb3_act = _nb2_act

                        if _nb3_act:
                            _nb3_mgr2 = self._get_nb_manager()
                            _dst_cfg_dir = _nb3_mgr2._branch_config_dir(_new_root_nb, _nb3_act["name"])
                        else:
                            _dst_cfg_dir = os.path.join(src_base, "config")

                        _bctx_raw = _load_ctx_from_dir(_nb2_branch_cfg_dir)
                        _bfs_raw  = _load_fs_full_from_dir(_nb2_branch_cfg_dir)  # full "uid gid mode [sym]"

                        def _esc(r):
                            return "/" + "/".join(p.replace(".", "\\.") for p in r.split("/"))

                        _src_ctx_pfx = _esc(old_rel)
                        _dst_ctx_pfx = _esc(_new_rel_nb)

                        # Tentukan file ctx dan fs target
                        _ctx_target = os.path.join(_dst_cfg_dir, _new_root_nb + "_file_contexts")
                        _fs_target  = os.path.join(_dst_cfg_dir, _new_root_nb + "_fs_config")

                        # Hapus entry lama dari src branch config (MOVE bukan copy)
                        _src_ctx_file2 = os.path.join(_nb2_branch_cfg_dir, old_root + "_file_contexts")
                        _src_fs_file2  = os.path.join(_nb2_branch_cfg_dir, old_root + "_fs_config")

                        if os.path.isfile(_src_ctx_file2):
                            with open(_src_ctx_file2, "r", encoding="utf-8", errors="ignore") as _f:
                                _del_ctx_lines = [ln.rstrip("\n") for ln in _f]
                            _del_ctx_lines = [
                                ln for ln in _del_ctx_lines
                                if not (ln.strip() and not ln.strip().startswith("#")
                                        and (ln.strip().split()[0] == _src_ctx_pfx
                                             or ln.strip().split()[0].startswith(_src_ctx_pfx + "/")))
                            ]
                            with open(_src_ctx_file2, "w", encoding="utf-8", errors="ignore") as _f:
                                _f.write("\n".join(_del_ctx_lines) + "\n")

                        if os.path.isfile(_src_fs_file2):
                            with open(_src_fs_file2, "r", encoding="utf-8", errors="ignore") as _f:
                                _del_fs_lines = [ln.rstrip("\n") for ln in _f]
                            _del_fs_lines = [
                                ln for ln in _del_fs_lines
                                if not (ln.strip() and not ln.strip().startswith("#")
                                        and (ln.strip().split()[0] == old_rel
                                             or ln.strip().split()[0].startswith(old_rel + "/")))
                            ]
                            with open(_src_fs_file2, "w", encoding="utf-8", errors="ignore") as _f:
                                _f.write("\n".join(_del_fs_lines) + "\n")

                        # Tulis entry baru ke dst config
                        if os.path.isfile(_ctx_target):
                            with open(_ctx_target, "r", encoding="utf-8", errors="ignore") as _f:
                                _ctx_lines = [ln.rstrip("\n") for ln in _f]
                            _existing_ctx_keys = set()
                            for _ln in _ctx_lines:
                                _s = _ln.strip()
                                if _s and not _s.startswith("#"):
                                    _existing_ctx_keys.add(_s.split()[0])
                            _ctx_appended = list(_ctx_lines)
                            for _k, _v in _bctx_raw.items():
                                # remap key
                                if _k == _src_ctx_pfx or _k.startswith(_src_ctx_pfx + "/"):
                                    _new_k = _dst_ctx_pfx + _k[len(_src_ctx_pfx):]
                                else:
                                    continue  # hanya tulis entry yang relevan
                                if _new_k not in _existing_ctx_keys:
                                    _ctx_appended.append(f"{_new_k} {_v}")
                            _ctx_appended.sort()
                            with open(_ctx_target, "w", encoding="utf-8", errors="ignore") as _f:
                                _f.write("\n".join(_ctx_appended) + "\n")

                        if os.path.isfile(_fs_target):
                            with open(_fs_target, "r", encoding="utf-8", errors="ignore") as _f:
                                _fs_lines = [ln.rstrip("\n") for ln in _f]
                            _existing_fs_keys = set()
                            for _ln in _fs_lines:
                                _s = _ln.strip()
                                if _s and not _s.startswith("#"):
                                    _existing_fs_keys.add(_s.split()[0])
                            _fs_appended = list(_fs_lines)
                            for _k, _v in _bfs_raw.items():
                                if _k == old_rel or _k.startswith(old_rel + "/"):
                                    _new_k = _new_rel_nb + _k[len(old_rel):]
                                else:
                                    continue
                                if _new_k not in _existing_fs_keys:
                                    _fs_appended.append(f"{_new_k} {_v}")
                            _fs_appended.sort()
                            with open(_fs_target, "w", encoding="utf-8", errors="ignore") as _f:
                                _f.write("\n".join(_fs_appended) + "\n")

                        return True, ""
                    except Exception as e:
                        return False, str(e)

                def _after_nb_item_move(ok, msg):
                    self.resume_size_probe()
                    if not ok:
                        if main:
                            main.pop_task("moving-internal", False)
                            main.set_loading_failed(f"Branch-aware move failed: {msg}")
                        return

                    # Reload engine setelah worker nulis config baru
                    try:
                        self.reload_config_cache()
                    except Exception:
                        self.ctx = load_file_context(self.base)
                        self.fs  = load_fs_config(self.base)
                        if hasattr(self, "engine") and self.engine:
                            self.engine.ctx = self.ctx
                            self.engine.fs  = self.fs
                            self.engine.rebuild_root_map()

                    parent = self.find_item_by_path(self.topLevelItem(0), target_path)
                    if parent:
                        self.refresh_folder(parent)

                    _sp = self.find_item_by_path(
                        self.topLevelItem(0), os.path.dirname(src_path))
                    if _sp:
                        self.refresh_folder(_sp)

                    self.restore_expanded_paths(expanded)
                    if main:
                        main.pop_task("moving-internal", True)
                        main.maybe_finish_loading()

                self.pause_size_probe()
                run_in_thread(_worker_nb_item_move_safe, None, callback=_after_nb_item_move)
                event.acceptProposedAction()
                return

        # end NeoBranch subfolder/file move


        if _nb_dst_in_branch:
            _nb4      = self._get_nb_manager()
            _nb4_act  = _nb4.get_active_branch(new_root) if _nb4 else None
            if _nb4 and _nb4_act:
                _nb4_branch_name    = _nb4_act["name"]
                _nb4_branch_dir     = _nb4._branch_dir(new_root, _nb4_branch_name)
                _nb4_branch_cfg_dir = _nb4._branch_config_dir(new_root, _nb4_branch_name)

                # Physical dst di branch fs dir
                _nb4_rel_new_inside = "/".join(new_rel_preview.split("/")[1:])
                _nb4_branch_dest = os.path.join(
                    _nb4._branch_dir(new_root, _nb4_branch_name),
                    new_rel_preview.replace("/", os.sep)
                )

                def _worker_nb_dst_move_safe():
                    try:
                        os.makedirs(os.path.dirname(_nb4_branch_dest), exist_ok=True)
                        if os.path.isfile(src_path):
                            shutil.copy2(src_path, _nb4_branch_dest)
                        else:
                            if os.path.exists(_nb4_branch_dest):
                                shutil.rmtree(_nb4_branch_dest)
                            shutil.copytree(src_path, _nb4_branch_dest, symlinks=True)

                        # Hapus src fisik (ini move)
                        try:
                            if os.path.isdir(src_path):
                                shutil.rmtree(src_path)
                            else:
                                os.remove(src_path)
                        except Exception:
                            pass

                        # Record semua yang masuk sebagai edited_files
                        if os.path.exists(os.path.join(self.base, new_rel_preview.replace("/", os.sep))):
                            _nb4.record_delete(
                                new_root, _nb4_branch_name,
                                new_rel_preview, prune_overlay=False)
                        if os.path.isfile(_nb4_branch_dest):
                            _nb4.record_edit(
                                new_root, _nb4_branch_name,
                                os.path.join(self.base, new_rel_preview.replace("/", os.sep))
                            )
                        else:
                            for _r, _d, _fs in os.walk(_nb4_branch_dest):
                                for _fn in _fs:
                                    _fp = os.path.join(_r, _fn)
                                    _frel = os.path.relpath(_fp, _nb4_branch_dir).replace("\\", "/")
                                    # _frel adalah rel dari branch_dir, tapi record_edit minta abs source path
                                    # reconstruct abs source path dari self.base + _frel
                                    _abs_src_equiv = os.path.join(self.base, _frel.replace("/", os.sep))
                                    _nb4.record_edit(new_root, _nb4_branch_name, _abs_src_equiv)

                        # Config remap: src config → branch config dir dst
                        # MOVE bukan copy: hapus entry lama dari src config dulu
                        _src_cfg_raw  = _load_ctx_from_dir(os.path.join(src_base, "config"))
                        _src_fs_raw   = _load_fs_full_from_dir(os.path.join(src_base, "config"))

                        def _esc_nb4(r):
                            return "/" + "/".join(p.replace(".", "\\.") for p in r.split("/"))

                        _src_ctx_pfx4 = _esc_nb4(old_rel)
                        _dst_ctx_pfx4 = _esc_nb4(new_rel_preview)

                        # Hapus entry lama dari src config (source tree config)
                        _src_ctx_file4 = os.path.join(src_base, "config", old_root + "_file_contexts")
                        _src_fs_file4  = os.path.join(src_base, "config", old_root + "_fs_config")

                        if os.path.isfile(_src_ctx_file4):
                            with open(_src_ctx_file4, "r", encoding="utf-8", errors="ignore") as _f:
                                _del4_ctx = [ln.rstrip("\n") for ln in _f]
                            _del4_ctx = [
                                ln for ln in _del4_ctx
                                if not (ln.strip() and not ln.strip().startswith("#")
                                        and (ln.strip().split()[0] == _src_ctx_pfx4
                                             or ln.strip().split()[0].startswith(_src_ctx_pfx4 + "/")))
                            ]
                            with open(_src_ctx_file4, "w", encoding="utf-8", errors="ignore") as _f:
                                _f.write("\n".join(_del4_ctx) + "\n")

                        if os.path.isfile(_src_fs_file4):
                            with open(_src_fs_file4, "r", encoding="utf-8", errors="ignore") as _f:
                                _del4_fs = [ln.rstrip("\n") for ln in _f]
                            _del4_fs = [
                                ln for ln in _del4_fs
                                if not (ln.strip() and not ln.strip().startswith("#")
                                        and (ln.strip().split()[0] == old_rel
                                             or ln.strip().split()[0].startswith(old_rel + "/")))
                            ]
                            with open(_src_fs_file4, "w", encoding="utf-8", errors="ignore") as _f:
                                _f.write("\n".join(_del4_fs) + "\n")

                        _ctx_target4 = os.path.join(_nb4_branch_cfg_dir, new_root + "_file_contexts")
                        _fs_target4  = os.path.join(_nb4_branch_cfg_dir, new_root + "_fs_config")

                        # Tulis entry baru ke dst branch config
                        if os.path.isfile(_ctx_target4):
                            with open(_ctx_target4, "r", encoding="utf-8", errors="ignore") as _f:
                                _ctx_lines4 = [ln.rstrip("\n") for ln in _f]
                            _existing_ctx4 = {
                                ln.strip().split()[0]
                                for ln in _ctx_lines4
                                if ln.strip() and not ln.strip().startswith("#")
                            }
                            _ctx_app4 = list(_ctx_lines4)
                            for _k, _v in _src_cfg_raw.items():
                                if _k == _src_ctx_pfx4 or _k.startswith(_src_ctx_pfx4 + "/"):
                                    _new_k4 = _dst_ctx_pfx4 + _k[len(_src_ctx_pfx4):]
                                else:
                                    continue
                                if _new_k4 not in _existing_ctx4:
                                    _ctx_app4.append(f"{_new_k4} {_v}")
                            _ctx_app4.sort()
                            with open(_ctx_target4, "w", encoding="utf-8", errors="ignore") as _f:
                                _f.write("\n".join(_ctx_app4) + "\n")

                        if os.path.isfile(_fs_target4):
                            with open(_fs_target4, "r", encoding="utf-8", errors="ignore") as _f:
                                _fs_lines4 = [ln.rstrip("\n") for ln in _f]
                            _existing_fs4 = {
                                ln.strip().split()[0]
                                for ln in _fs_lines4
                                if ln.strip() and not ln.strip().startswith("#")
                            }
                            _fs_app4 = list(_fs_lines4)
                            for _k, _v in _src_fs_raw.items():
                                if _k == old_rel or _k.startswith(old_rel + "/"):
                                    _new_k4 = new_rel_preview + _k[len(old_rel):]
                                else:
                                    continue
                                if _new_k4 not in _existing_fs4:
                                    _fs_app4.append(f"{_new_k4} {_v}")
                            _fs_app4.sort()
                            with open(_fs_target4, "w", encoding="utf-8", errors="ignore") as _f:
                                _f.write("\n".join(_fs_app4) + "\n")

                        return True, ""
                    except Exception as e:
                        return False, str(e)

                def _after_nb_dst_move(ok, msg):
                    self.resume_size_probe()
                    if not ok:
                        if main:
                            main.pop_task("moving-internal", False)
                            main.set_loading_failed(f"Branch-aware dst move failed: {msg}")
                        return

                    try:
                        self.reload_config_cache()
                    except Exception:
                        self.ctx = load_file_context(self.base)
                        self.fs  = load_fs_config(self.base)
                        if hasattr(self, "engine") and self.engine:
                            self.engine.ctx = self.ctx
                            self.engine.fs  = self.fs
                            self.engine.rebuild_root_map()

                    parent = self.find_item_by_path(self.topLevelItem(0), target_path)
                    if parent:
                        self.refresh_folder(parent)

                    _sp = self.find_item_by_path(
                        self.topLevelItem(0), os.path.dirname(src_path))
                    if _sp:
                        self.refresh_folder(_sp)

                    self.restore_expanded_paths(expanded)
                    if main:
                        main.pop_task("moving-internal", True)
                        main.maybe_finish_loading()

                self.pause_size_probe()
                run_in_thread(_worker_nb_dst_move_safe, None, callback=_after_nb_dst_move)
                event.acceptProposedAction()
                return

        # end NeoBranch dst-in-branch move

        import shiboken6
        from services.protocol import OP_CONFIG_HANDLE_TRANSFER, OP_CONFIG_REBUILD_ROOT_MAP
        from main_ext.config_engine_client import TIMEOUT_HEAVY

        captured_src_base = str(src_base or self.base)
        captured_dst_base = str(dst_tree.base if dst_tree else self.base)
        captured_src_path = str(src_path)
        captured_new_full = str(new_full)
        captured_target_path = str(target_path)
        captured_old_root = str(old_root) if old_root is not None else ""
        captured_old_rel = str(old_rel) if old_rel is not None else ""
        captured_root_changed = bool(root_changed)

        rel_old_f = os.path.relpath(captured_src_path, captured_src_base).replace("\\", "/")
        rel_new_f = os.path.relpath(captured_new_full, captured_dst_base).replace("\\", "/")

        dest_is_inside_fs = bool(self.is_inside_fs_root(rel_new_f))

        engine = getattr(self, "engine", None)
        captured_conn = getattr(engine, "_conn", None) if engine else None

        peers = self._watcher_peers()
        src_parent_dir = os.path.dirname(captured_src_path)
        dst_parent_dir = os.path.dirname(captured_new_full)
        config_dir_path = os.path.join(captured_dst_base, "config")

        quiet_targets = [
            (captured_src_path, True),
            (captured_new_full, True),
            (src_parent_dir, False),
            (dst_parent_dir, False),
            (config_dir_path, True),
        ]

        for peer in peers:
            if hasattr(peer, "_detach_watchers_for"):
                try:
                    peer._detach_watchers_for(captured_src_path)
                except Exception:
                    pass

        for peer in peers:
            if hasattr(peer, "_set_bulk_watcher_quiet"):
                for q_path, q_rec in quiet_targets:
                    try:
                        peer._set_bulk_watcher_quiet(q_path, q_rec, True)
                    except Exception:
                        pass

        def _purge_pending_dir_events(peer):
            pending = getattr(peer, "_pending_directory_changes", None)
            if pending is None:
                return
            def _matches_quiet(p):
                try:
                    norm_p = os.path.normcase(os.path.normpath(str(p)))
                    for qp, qr in quiet_targets:
                        norm_q = os.path.normcase(os.path.normpath(str(qp)))
                        if qr:
                            if norm_p == norm_q or norm_p.startswith(norm_q + os.sep):
                                return True
                        else:
                            if norm_p == norm_q:
                                return True
                except Exception:
                    pass
                return False

            if isinstance(pending, set):
                to_del = [p for p in pending if _matches_quiet(p)]
                for p in to_del:
                    pending.discard(p)
            elif isinstance(pending, dict):
                to_del = [p for p in pending if _matches_quiet(p)]
                for p in to_del:
                    pending.pop(p, None)
            elif isinstance(pending, list):
                pending[:] = [p for p in pending if not _matches_quiet(p)]

        for peer in peers:
            _purge_pending_dir_events(peer)

        def _worker_same_move():
            shutil.move(captured_src_path, captured_new_full)

            builder_ran = False
            if captured_root_changed:
                part = os.path.basename(captured_new_full.rstrip("/\\"))
                config_dir = os.path.join(captured_dst_base, "config")

                old_cfg  = os.path.join(captured_src_base, "config")
                old_opt  = os.path.join(old_cfg, captured_old_root + "_fs_options")
                old_info = os.path.join(old_cfg, captured_old_root + "_info")

                if os.path.isfile(old_opt):
                    old_fs_type = "EROFS"
                elif os.path.isfile(old_info):
                    old_fs_type = "EXT"
                else:
                    old_fs_type = None

                fc  = os.path.join(config_dir, part + "_file_contexts")
                fs  = os.path.join(config_dir, part + "_fs_config")
                opt = os.path.join(config_dir, part + "_fs_options")
                inf = os.path.join(config_dir, part + "_info")

                has_required = os.path.isfile(fc) and os.path.isfile(fs)
                has_identity = os.path.isfile(opt) or os.path.isfile(inf)

                if not (has_required and has_identity):
                    try:
                        from main_ext.class_FilesystemBuilder import FilesystemBuilder
                        builder = FilesystemBuilder(
                            captured_dst_base,
                            captured_new_full,
                            forced_fs_type=old_fs_type,
                            source_fs_root=captured_old_root,
                            source_rel_prefix=captured_old_rel,
                        )
                        builder.create_filesystem()
                        builder_ran = True
                    except Exception as e:
                        print("FilesystemBuilder failed:", e)

            state = None
            if captured_conn is not None:
                if callable(getattr(captured_conn, "is_connected", None)):
                    try:
                        if not captured_conn.is_connected():
                            raise RuntimeError("Config service is disconnected")
                    except Exception as e:
                        if isinstance(e, RuntimeError):
                            raise
                        raise RuntimeError(f"Config service connection check failed: {e}")

                if builder_ran:
                    captured_conn.call_sync(OP_CONFIG_REBUILD_ROOT_MAP, {"base": captured_dst_base})

                if dest_is_inside_fs:
                    state = captured_conn.call_sync(
                        OP_CONFIG_HANDLE_TRANSFER,
                        {
                            "base": captured_dst_base,
                            "old_rel": rel_old_f,
                            "new_rel": rel_new_f,
                            "old_base": captured_src_base,
                            "new_base": captured_dst_base,
                        },
                        timeout_ms=TIMEOUT_HEAVY,
                    )
                else:
                    state = captured_conn.call_sync(
                        OP_CONFIG_REBUILD_ROOT_MAP,
                        {"base": captured_dst_base},
                    )

                if not isinstance(state, dict):
                    raise RuntimeError("Config service returned non-dict response")
            else:
                from core.config_engine_pure import ConfigEngine
                pure_engine = ConfigEngine(captured_dst_base)

                if builder_ran:
                    pure_engine.rebuild_root_map()

                if dest_is_inside_fs:
                    pure_engine.handle_fs_transfer(
                        old_rel=rel_old_f,
                        new_rel=rel_new_f,
                        old_base=captured_src_base,
                        new_base=captured_dst_base,
                    )
                else:
                    pure_engine.rebuild_root_map()

                state = pure_engine.load_full_state()

            return state

        def _worker_same_move_safe():
            try:
                state = _worker_same_move()
                return True, state
            except Exception as e:
                return False, str(e)

        def _balance_quiet():
            for peer in peers:
                try:
                    if not shiboken6.isValid(peer):
                        continue
                    if hasattr(peer, "_set_bulk_watcher_quiet"):
                        for q_path, q_rec in quiet_targets:
                            peer._set_bulk_watcher_quiet(q_path, q_rec, False)
                except Exception:
                    pass

        def _after_same_move(ok=True, payload=None):
            settled = False
            try:
                if not shiboken6.isValid(self) or getattr(self, "_background_stopped", False) or getattr(self, "base", None) != captured_src_base:
                    return

                valid_trees = []
                for t in [self] + list(peers or []):
                    if t not in valid_trees and shiboken6.isValid(t) and not getattr(t, "_background_stopped", False) and getattr(t, "base", None) == captured_src_base:
                        valid_trees.append(t)

                refresh_paths = []
                for p in (src_parent_dir, captured_target_path):
                    if p and p not in refresh_paths:
                        refresh_paths.append(p)

                if not ok:
                    msg = str(payload or "")
                    if os.path.exists(captured_src_path):
                        for tree in valid_trees:
                            watcher = getattr(tree, "watcher", None)
                            if watcher:
                                try:
                                    if os.path.isdir(captured_src_path):
                                        watcher.addPath(captured_src_path)
                                        for ep in expanded:
                                            if isinstance(ep, str) and (ep == captured_src_path or ep.startswith(captured_src_path + os.sep)) and os.path.isdir(ep):
                                                watcher.addPath(ep)
                                    elif os.path.isfile(captured_src_path):
                                        watcher.addPath(captured_src_path)
                                except Exception:
                                    pass
                else:
                    state = payload if isinstance(payload, dict) else {}
                    for tree in valid_trees:
                        tree.ctx = state.get("ctx", getattr(tree, "ctx", {}))
                        tree.fs  = state.get("fs", getattr(tree, "fs", {}))
                        engine = getattr(tree, "engine", None)
                        if engine:
                            if hasattr(engine, "_apply_state"):
                                engine._apply_state(state)
                            else:
                                engine.ctx = tree.ctx
                                engine.fs  = tree.fs
                                if "root_map_ctx" in state:
                                    engine.root_map_ctx = state["root_map_ctx"]
                                if "root_map_fs" in state:
                                    engine.root_map_fs = state["root_map_fs"]

                for tree in valid_trees:
                    root_item = tree.topLevelItem(0)
                    for rp in refresh_paths:
                        parent_item = tree.find_item_by_path(root_item, rp)
                        if parent_item:
                            tree.refresh_folder(parent_item)
                    if ok and root_item:
                        tree._schedule_metadata_refresh(root_item)

                if ok:
                    self.restore_expanded_paths(expanded)
                    if main and shiboken6.isValid(main):
                        settled = True
                        try:
                            main.pop_task("moving-internal", True)
                            main.maybe_finish_loading()
                        except Exception:
                            pass
                else:
                    if main and shiboken6.isValid(main):
                        settled = True
                        try:
                            main.pop_task("moving-internal", False)
                            main.set_loading_failed(msg)
                        except Exception:
                            pass
            except Exception as e:
                if not settled and main and shiboken6.isValid(main):
                    settled = True
                    try:
                        main.pop_task("moving-internal", False)
                        main.set_loading_failed(str(e))
                    except Exception:
                        pass
            finally:
                targets = []
                for t in [self] + list(peers or []):
                    if t not in targets and shiboken6.isValid(t) and not getattr(t, "_background_stopped", False) and getattr(t, "base", None) == captured_src_base:
                        targets.append(t)
                for t in targets:
                    try:
                        t.resume_size_probe()
                    except Exception:
                        pass
                _balance_quiet()

        self.pause_size_probe()
        run_in_thread(_worker_same_move_safe, None, callback=_after_same_move)
        event.acceptProposedAction()

    def _nb_append_remapped_entries(self, src_cfg_dir, dst_cfg_dir, old_root, new_root, rel_src, rel_dst):

        os.makedirs(dst_cfg_dir, exist_ok=True)
        ctx_raw = _load_ctx_from_dir(src_cfg_dir)
        fs_raw = _load_fs_full_from_dir(src_cfg_dir)

        def _esc(r):
            return "/" + "/".join(p.replace(".", "\\.") for p in r.split("/"))

        src_ctx_pfx = _esc(rel_src)
        dst_ctx_pfx = _esc(rel_dst)
        ctx_target = os.path.join(dst_cfg_dir, new_root + "_file_contexts")
        fs_target = os.path.join(dst_cfg_dir, new_root + "_fs_config")

        if not os.path.isfile(ctx_target):
            open(ctx_target, "w", encoding="utf-8").close()
        if not os.path.isfile(fs_target):
            open(fs_target, "w", encoding="utf-8").close()

        if os.path.isfile(ctx_target):
            with open(ctx_target, "r", encoding="utf-8", errors="ignore") as f:
                lines = [ln.rstrip("\n") for ln in f]
            existing = {
                ln.strip().split()[0]
                for ln in lines
                if ln.strip() and not ln.strip().startswith("#")
            }
            out = list(lines)
            for key, val in ctx_raw.items():
                if key == src_ctx_pfx or key.startswith(src_ctx_pfx + "/"):
                    new_key = dst_ctx_pfx + key[len(src_ctx_pfx):]
                    if new_key not in existing:
                        out.append(f"{new_key} {val}")
            out.sort()
            with open(ctx_target, "w", encoding="utf-8", errors="ignore") as f:
                f.write("\n".join(out) + ("\n" if out else ""))

        if os.path.isfile(fs_target):
            with open(fs_target, "r", encoding="utf-8", errors="ignore") as f:
                lines = [ln.rstrip("\n") for ln in f]
            existing = {
                ln.strip().split()[0]
                for ln in lines
                if ln.strip() and not ln.strip().startswith("#")
            }
            out = list(lines)
            for key, val in fs_raw.items():
                if key == rel_src or key.startswith(rel_src + "/"):
                    new_key = rel_dst + key[len(rel_src):]
                    if new_key not in existing:
                        out.append(f"{new_key} {val}")
            out.sort()
            with open(fs_target, "w", encoding="utf-8", errors="ignore") as f:
                f.write("\n".join(out) + ("\n" if out else ""))

    def _nb_copy_branch_aware_source(self, src_logical, src_phys, dst_phys, rel_src, src_root, src_nb, src_active):

        merged_existing_dir = False
        if os.path.exists(dst_phys):
            src_dir = src_phys if os.path.isdir(src_phys) else (
                src_logical if os.path.isdir(src_logical) else None
            )
            if src_dir and os.path.isdir(dst_phys):
                self.drop_conflict_policy = None
                try:
                    self._merge_internal_folder(src_dir, dst_phys, move=False)
                finally:
                    self.drop_conflict_policy = None
                merged_existing_dir = True
            else:
                policy = self.ask_drop_conflict_policy(
                    os.path.basename(src_logical),
                    src_path=src_phys,
                    dst_path=dst_phys,
                    folder_context=False,
                    remaining=1,
                    show_bulk=True,
                )
                if policy == "cancel":
                    raise RuntimeError("User cancelled")
                if policy in ("skip", "skip_all"):
                    raise RuntimeError("Skipped by user")
                if os.path.isdir(dst_phys):
                    shutil.rmtree(dst_phys)
                else:
                    os.remove(dst_phys)

        if os.path.isfile(src_phys):
            os.makedirs(os.path.dirname(dst_phys), exist_ok=True)
            shutil.copy2(src_phys, dst_phys)
            return

        os.makedirs(os.path.dirname(dst_phys), exist_ok=True)
        if merged_existing_dir:
            pass
        elif os.path.isdir(src_phys):
            shutil.copytree(src_phys, dst_phys, symlinks=True)
        elif os.path.isdir(src_logical):
            shutil.copytree(src_logical, dst_phys, symlinks=True)
        else:
            os.makedirs(dst_phys, exist_ok=True)

        if not (src_nb and src_active):
            return

        branch_json = src_nb._read_branch_json(src_nb._branch_json_path(src_root, src_active["name"]))
        for entry in branch_json.get("edited_files", []):
            erel = entry.get("rel_path", "").replace("\\", "/")
            ephys = entry.get("abs_branch_path", "")
            if not (erel == rel_src or erel.startswith(rel_src + "/")):
                continue
            inside = erel[len(rel_src):].lstrip("/")
            out = os.path.join(dst_phys, inside.replace("/", os.sep))
            if os.path.isfile(ephys):
                os.makedirs(os.path.dirname(out), exist_ok=True)
                shutil.copy2(ephys, out)

        for drel in branch_json.get("deleted_paths", []):
            drel = drel.replace("\\", "/")
            if not (drel == rel_src or drel.startswith(rel_src + "/")):
                continue
            inside = drel[len(rel_src):].lstrip("/")
            victim = os.path.join(dst_phys, inside.replace("/", os.sep))
            if os.path.isdir(victim):
                shutil.rmtree(victim)
            elif os.path.isfile(victim):
                os.remove(victim)

    def _drop_multi_internal(self, src_paths, target_path, target_item, expanded, event, main, src_phys_paths=None):

        event.acceptProposedAction()


        self.drop_conflict_policy = None

        # The drag source identifies the owning tree even when project roots
        # share a path prefix. Fall back to paths for older callers.
        dst_tree = self
        dst_base = self.base
        drag_source = event.source() if hasattr(event, "source") else None
        src_tree = drag_source if isinstance(drag_source, FileTree) else None
        if src_tree is None and src_paths:
            _first = src_paths[0]
            def _inside_base(path, base):
                if not base:
                    return False
                try:
                    return os.path.normcase(os.path.commonpath((path, base))) == os.path.normcase(os.path.normpath(base))
                except ValueError:
                    return False
            if main:
                if hasattr(main, "tree") and _inside_base(_first, main.tree.base):
                    src_tree = main.tree
                elif hasattr(main, "tree2") and _inside_base(_first, main.tree2.base):
                    src_tree = main.tree2
        if not src_tree:
            src_tree = dst_tree
        src_base  = src_tree.base
        is_cross  = src_tree is not dst_tree
        source_rows = {
            item.data(0, Qt.UserRole): item
            for item in src_tree.selectedItems()
            if item.data(0, Qt.UserRole) in src_paths
        }

        from core.task_queue import TaskItem
        from main_ext.class_ConfigEngine import load_file_context, load_fs_config

        total   = len(src_paths)
        done    = [0]
        failed  = [0]
        errors  = []
        target_poll_active = [False]


        batch_id = None
        if main and hasattr(main, "_ensure_loading_manager"):
            main._ensure_loading_manager()
            batch_id = main.loading_manager.start_batch(
                "moving", total, label="Moving", region="light"
            )

        phys_by_src = {}
        if src_phys_paths:
            for logical, physical in zip(src_paths, src_phys_paths):
                phys_by_src[logical] = physical or logical

        def _branch_state_for(tree, base, logical_path):
            try:
                rel = os.path.relpath(logical_path, base).replace("\\", "/")
            except Exception:
                return None, None, None, None
            root = rel.split("/")[0] if rel and rel != "." else ""
            if not root or not tree.nb_is_active(root):
                return rel, root, None, None
            nb = tree._get_nb_manager()
            active = nb.get_active_branch(root) if nb else None
            return rel, root, nb, active

        _append_remapped_entries = self._nb_append_remapped_entries

        def _remove_config_prefix(cfg_dir, root, rel_src):
            def _esc(r):
                return "/" + "/".join(p.replace(".", "\\.") for p in r.split("/"))

            src_ctx_pfx = _esc(rel_src)
            ctx_file = os.path.join(cfg_dir, root + "_file_contexts")
            fs_file = os.path.join(cfg_dir, root + "_fs_config")

            if os.path.isfile(ctx_file):
                with open(ctx_file, "r", encoding="utf-8", errors="ignore") as f:
                    lines = [ln.rstrip("\n") for ln in f]
                lines = [
                    ln for ln in lines
                    if not (
                        ln.strip() and not ln.strip().startswith("#")
                        and (
                            ln.strip().split()[0] == src_ctx_pfx
                            or ln.strip().split()[0].startswith(src_ctx_pfx + "/")
                        )
                    )
                ]
                with open(ctx_file, "w", encoding="utf-8", errors="ignore") as f:
                    f.write("\n".join(lines) + ("\n" if lines else ""))

            if os.path.isfile(fs_file):
                with open(fs_file, "r", encoding="utf-8", errors="ignore") as f:
                    lines = [ln.rstrip("\n") for ln in f]
                lines = [
                    ln for ln in lines
                    if not (
                        ln.strip() and not ln.strip().startswith("#")
                        and (
                            ln.strip().split()[0] == rel_src
                            or ln.strip().split()[0].startswith(rel_src + "/")
                        )
                    )
                ]
                with open(fs_file, "w", encoding="utf-8", errors="ignore") as f:
                    f.write("\n".join(lines) + ("\n" if lines else ""))

        def _record_branch_tree(nb, root, branch_name, branch_dir, base, logical_root):
            if os.path.isfile(branch_dir):
                nb.record_edit(root, branch_name, logical_root)
                return
            for walk_root, _, files in os.walk(branch_dir):
                for fn in files:
                    phys = os.path.join(walk_root, fn)
                    logical = os.path.join(
                        base,
                        os.path.relpath(phys, nb._branch_dir(root, branch_name)).replace("/", os.sep),
                    )
                    nb.record_edit(root, branch_name, logical)

        _copy_branch_aware_source = self._nb_copy_branch_aware_source

        def _classify(src):

            if not os.path.isdir(src):
                return "normal"
            rel = os.path.relpath(src, src_base).replace("\\", "/")
            if rel == "config":
                return "config_blocked"
            # fs_root = tepat satu level di bawah src_base, ada di src engine root_map,
            # DAN target adalah base dst_tree langsung (bukan subfolder fs_root lain).
            rel_target = os.path.relpath(target_path, dst_base).replace("\\", "/")
            target_is_base = rel_target == "."
            parts = rel.split("/")
            if len(parts) == 1 and rel in src_tree.engine.root_map_ctx and target_is_base:
                return "fs_root"
            return "normal"

        def _make_fs_root_worker(src, target):

            def worker(s=src, t=target):
                try:
                    part     = os.path.basename(s)
                    _src_cfg = os.path.join(src_base, "config")
                    _dst_cfg = os.path.join(dst_base, "config")
                    dst_root = os.path.join(t, part)

                    os.makedirs(_dst_cfg, exist_ok=True)
                    for suffix in ("_fs_options", "_info"):
                        sf = os.path.join(_src_cfg, part + suffix)
                        df = os.path.join(_dst_cfg, part + suffix)
                        if os.path.isfile(sf) and not os.path.exists(df):
                            shutil.move(sf, os.path.join(_dst_cfg, part + suffix))

                    if os.path.exists(dst_root):
                        self._merge_internal_folder(s, dst_root, move=True)
                    else:
                        shutil.move(s, dst_root)

                    dst_tree.ctx = load_file_context(dst_base)
                    dst_tree.fs  = load_fs_config(dst_base)
                    if is_cross:
                        src_tree.ctx = load_file_context(src_base)
                        src_tree.fs  = load_fs_config(src_base)
                    return True, ""
                except Exception as e:
                    return False, str(e)
            return worker

        def _make_normal_worker(src, target):

            def worker(s=src, t=target):
                try:
                    dst_full = os.path.join(t, os.path.basename(s))
                    rel_src  = os.path.relpath(s, src_base).replace("\\", "/")
                    old_root = rel_src.split("/")[0]

                    # NeoBranch redirect: cek apakah DST ada di dalam branch aktif
                    rel_target   = os.path.relpath(t, dst_base).replace("\\", "/")
                    _dst_nb_root = rel_target.split("/")[0] if rel_target != "." else ""
                    if _dst_nb_root and dst_tree.nb_is_active(_dst_nb_root):
                        nb  = dst_tree._get_nb_manager()
                        act = nb.get_active_branch(_dst_nb_root) if nb else None
                        if act and nb:
                            branch_fs    = nb._branch_fs_dir(_dst_nb_root, act["name"])
                            parts_inside = rel_target.split("/")[1:]
                            phys_target  = os.path.join(branch_fs, *parts_inside) if parts_inside else branch_fs
                            os.makedirs(phys_target, exist_ok=True)
                            dst_full = os.path.join(phys_target, os.path.basename(s))

                    if os.path.normpath(s) == os.path.normpath(dst_full):
                        return True, ""
                    if os.path.exists(dst_full):
                        if os.path.isdir(s) and os.path.isdir(dst_full):
                            # Folder-merge dapat scope replace_all/skip_all sendiri,
                            # supaya tidak bocor ke item lain di batch ini.
                            _outer_policy = self.drop_conflict_policy
                            self.drop_conflict_policy = None
                            try:
                                self._merge_internal_folder(s, dst_full, move=True)
                            finally:
                                self.drop_conflict_policy = _outer_policy
                        else:


                            policy = self.ask_drop_conflict_policy(
                                os.path.basename(s),
                                src_path=s,
                                dst_path=dst_full,
                                folder_context=False,
                                remaining=1,
                                show_bulk=True,
                            )
                            if policy == "cancel":
                                return False, "Cancelled"
                            if policy in ("skip", "skip_all"):
                                return True, ""


                            if os.path.isdir(s) and os.path.isdir(dst_full):
                                self._merge_internal_folder(s, dst_full, move=True)
                            else:
                                if os.path.isdir(dst_full):
                                    shutil.rmtree(dst_full)
                                elif os.path.isfile(dst_full):
                                    os.remove(dst_full)
                                shutil.move(s, dst_full)
                    else:
                        shutil.move(s, dst_full)

                    # Kalau pindah ke fs_root berbeda (root_changed), build filesystem baru
                    rel_new  = os.path.relpath(dst_full, dst_base).replace("\\", "/")
                    new_root = rel_new.split("/")[0]
                    if os.path.isdir(dst_full) and (is_cross or new_root != old_root):
                        _old_cfg  = os.path.join(src_base, "config")
                        _old_opt  = os.path.join(_old_cfg, old_root + "_fs_options")
                        _old_info = os.path.join(_old_cfg, old_root + "_info")
                        if os.path.isfile(_old_opt):
                            old_fs_type = "EROFS"
                        elif os.path.isfile(_old_info):
                            old_fs_type = "EXT"
                        else:
                            old_fs_type = None

                        _dst_cfg = os.path.join(dst_base, "config")
                        fc  = os.path.join(_dst_cfg, new_root + "_file_contexts")
                        fs2 = os.path.join(_dst_cfg, new_root + "_fs_config")
                        opt = os.path.join(_dst_cfg, new_root + "_fs_options")
                        inf = os.path.join(_dst_cfg, new_root + "_info")
                        has_required = os.path.isfile(fc) and os.path.isfile(fs2)
                        has_identity = os.path.isfile(opt) or os.path.isfile(inf)
                        if not (has_required and has_identity):
                            try:
                                from main_ext.class_FilesystemBuilder import FilesystemBuilder
                                FilesystemBuilder(
                                    dst_base, dst_full,
                                    forced_fs_type=old_fs_type,
                                    source_fs_root=old_root,
                                    source_rel_prefix=rel_src,
                                ).create_filesystem()
                            except Exception as e:
                                print(f"[multi] FilesystemBuilder failed: {e}")

                    dst_tree.ctx = load_file_context(dst_base)
                    dst_tree.fs  = load_fs_config(dst_base)
                    if is_cross:
                        src_tree.ctx = load_file_context(src_base)
                        src_tree.fs  = load_fs_config(src_base)
                    return True, ""
                except Exception as e:
                    return False, str(e)
            return worker

        def _make_branch_worker(src, target):

            def worker(s=src, t=target):
                try:
                    src_phys = phys_by_src.get(s) or s
                    rel_src, old_root, src_nb, src_active = _branch_state_for(src_tree, src_base, s)
                    dst_logical = os.path.join(t, os.path.basename(s))
                    rel_dst, new_root, dst_nb, dst_active = _branch_state_for(dst_tree, dst_base, dst_logical)

                    src_branch_active = bool(src_nb and src_active)
                    dst_branch_active = bool(dst_nb and dst_active)
                    print(f"[MV-BR] worker | src={os.path.basename(s)!r} | rel_src={rel_src!r}->rel_dst={rel_dst!r} | old_root={old_root!r}->new_root={new_root!r} | src_branch={src_branch_active} dst_branch={dst_branch_active}")

                    if not rel_src or not rel_dst:
                        print("[MV-BR]   -> ABORT: invalid branch move path")
                        return False, "Invalid branch move path"

                    if dst_branch_active:
                        dst_phys = os.path.join(
                            dst_nb._branch_dir(new_root, dst_active["name"]),
                            rel_dst.replace("/", os.sep),
                        )
                    else:
                        dst_phys = dst_logical


                    _copy_branch_aware_source(
                        s, src_phys, dst_phys,
                        rel_src, old_root, src_nb, src_active,
                    )

                    # Non-branch src = MOVE beneran → hapus fisik source sekarang.
                    # Branch src = COPY → jangan sentuh; record_delete di akhir.
                    if not src_branch_active:
                        print(f"[MV-BR]   non-branch src → MOVE (rmtree/remove): {os.path.basename(s)}")
                        try:
                            if os.path.isdir(s):
                                shutil.rmtree(s)
                            elif os.path.isfile(s):
                                os.remove(s)
                        except Exception:
                            pass
                    else:
                        print("[MV-BR]   branch src → COPY (source kept, record_delete ditunda ke akhir)")

                    if dst_branch_active:
                        if os.path.exists(dst_logical):
                            dst_nb.record_delete(new_root, dst_active["name"], rel_dst, prune_overlay=False)
                        _record_branch_tree(
                            dst_nb,
                            new_root,
                            dst_active["name"],
                            dst_phys,
                            dst_base,
                            os.path.join(dst_base, rel_dst.replace("/", os.sep)),
                        )

                    src_cfg_dir = (
                        src_nb._branch_config_dir(old_root, src_active["name"])
                        if src_branch_active
                        else os.path.join(src_base, "config")
                    )
                    dst_cfg_dir = (
                        dst_nb._branch_config_dir(new_root, dst_active["name"])
                        if dst_branch_active
                        else os.path.join(dst_base, "config")
                    )


                    root_changed_branch = (new_root != old_root)
                    if root_changed_branch and os.path.isdir(dst_phys):
                        _src_opt  = os.path.join(src_cfg_dir, old_root + "_fs_options")
                        _src_info = os.path.join(src_cfg_dir, old_root + "_info")
                        _src_opt2  = os.path.join(src_base, "config", old_root + "_fs_options")
                        _src_info2 = os.path.join(src_base, "config", old_root + "_info")
                        if os.path.isfile(_src_opt) or os.path.isfile(_src_opt2):
                            _b_fs_type = "EROFS"
                        elif os.path.isfile(_src_info) or os.path.isfile(_src_info2):
                            _b_fs_type = "EXT"
                        else:
                            _b_fs_type = None
                        _b_fc  = os.path.join(dst_cfg_dir, new_root + "_file_contexts")
                        _b_fs2 = os.path.join(dst_cfg_dir, new_root + "_fs_config")
                        _b_opt = os.path.join(dst_cfg_dir, new_root + "_fs_options")
                        _b_inf = os.path.join(dst_cfg_dir, new_root + "_info")
                        _b_has_required = os.path.isfile(_b_fc) and os.path.isfile(_b_fs2)
                        _b_has_identity = os.path.isfile(_b_opt) or os.path.isfile(_b_inf)
                        print(f"[MV-BR]   root_changed → fs build check | fs_type={_b_fs_type} | has_required={_b_has_required} has_identity={_b_has_identity}")
                        if not (_b_has_required and _b_has_identity):
                            try:
                                from main_ext.class_FilesystemBuilder import FilesystemBuilder


                                FilesystemBuilder(
                                    dst_base, dst_phys,
                                    forced_fs_type=_b_fs_type,
                                    source_fs_root=old_root,
                                    source_rel_prefix=rel_src,
                                ).create_filesystem()
                                print(f"[MV-BR]   FilesystemBuilder OK: new fs_root={new_root}")
                            except Exception as _be:
                                print(f"[MV-BR]   FilesystemBuilder FAILED: {_be}")
                        else:
                            print(f"[MV-BR]   skip fs build: config sudah lengkap untuk {new_root}")

                    _append_remapped_entries(src_cfg_dir, dst_cfg_dir, old_root, new_root, rel_src, rel_dst)
                    _remove_config_prefix(src_cfg_dir, old_root, rel_src)


                    if src_branch_active:
                        src_nb.record_delete(old_root, src_active["name"], rel_src)
                        print(f"[MV-BR]   record_delete src branch (prune edited+overlay): {rel_src}")

                    try:
                        dst_tree.reload_config_cache()
                    except Exception:
                        dst_tree.ctx = load_file_context(dst_base)
                        dst_tree.fs = load_fs_config(dst_base)
                    if is_cross:
                        try:
                            src_tree.reload_config_cache()
                        except Exception:
                            src_tree.ctx = load_file_context(src_base)
                            src_tree.fs = load_fs_config(src_base)
                    print("[MV-BR]   worker DONE OK")
                    return True, ""
                except Exception as e:
                    return False, str(e)
            return worker

        def _with_apk_mirror(src, worker):

            if not is_cross or not src.lower().endswith(".apk"):
                return worker

            def paired_worker():
                dst_apk = os.path.join(target_path, os.path.basename(src))
                return apktool_tool.move_apk_with_mirror(
                    src_base, dst_base, src, dst_apk, worker)

            return paired_worker

        def _make_callback(src, kind, task_id):
            def cb(ok, msg, s=src, k=kind, tid=task_id):
                if main:
                    if batch_id is not None:
                        main.loading_manager.report_done(batch_id, bool(ok), member_name=os.path.basename(s))
                    else:
                        main.pop_task(tid)

                if ok:
                    dst_tree.engine.ctx = dst_tree.ctx
                    dst_tree.engine.fs  = dst_tree.fs
                    if is_cross:
                        src_tree.engine.ctx = src_tree.ctx
                        src_tree.engine.fs  = src_tree.fs

                    if k == "fs_root":
                        rel_src = os.path.relpath(s, src_base).replace("\\", "/")
                        rel_dst = os.path.relpath(
                            os.path.join(target_path, os.path.basename(s)), dst_base
                        ).replace("\\", "/")
                        try:
                            if is_cross:
                                dst_tree.engine.move_paths_cross(src_tree.engine, rel_src, rel_dst)
                                src_tree.ctx = src_tree.engine.ctx
                                src_tree.fs = src_tree.engine.fs
                                dst_tree.ctx = dst_tree.engine.ctx
                                dst_tree.fs = dst_tree.engine.fs
                            else:
                                dst_tree.engine.handle_fs_transfer(
                                    old_rel=rel_src, new_rel=rel_dst,
                                    old_base=src_base, new_base=dst_base,
                                )
                        except Exception as e:
                            print(f"[multi] fs_root config move failed: {e}")
                        dst_tree.engine.rebuild_root_map()
                        if is_cross:
                            src_tree.engine.rebuild_root_map()
                    elif k == "branch_normal":
                        if hasattr(dst_tree, "engine") and dst_tree.engine:
                            dst_tree.engine.ctx = dst_tree.ctx
                            dst_tree.engine.fs = dst_tree.fs
                            dst_tree.engine.rebuild_root_map()
                        if is_cross and hasattr(src_tree, "engine") and src_tree.engine:
                            src_tree.engine.ctx = src_tree.ctx
                            src_tree.engine.fs = src_tree.fs
                            src_tree.engine.rebuild_root_map()
                    else:
                        rel_src = os.path.relpath(s, src_base).replace("\\", "/")
                        rel_dst = os.path.relpath(
                            os.path.join(target_path, os.path.basename(s)), dst_base
                        ).replace("\\", "/")
                        if dst_tree.is_inside_fs_root(rel_dst):
                            try:
                                if is_cross:
                                    dst_tree.engine.move_paths_cross(src_tree.engine, rel_src, rel_dst)
                                    src_tree.ctx = src_tree.engine.ctx
                                    src_tree.fs  = src_tree.engine.fs
                                    dst_tree.ctx = dst_tree.engine.ctx
                                    dst_tree.fs  = dst_tree.engine.fs
                                else:
                                    dst_tree.engine.handle_fs_transfer(
                                        old_rel=rel_src, new_rel=rel_dst,
                                        old_base=src_base, new_base=dst_base,
                                    )
                            except Exception:
                                pass
                        else:
                            dst_tree.engine.rebuild_root_map()
                        if is_cross:
                            src_tree.engine.rebuild_root_map()
                    done[0] += 1
                else:
                    failed[0] += 1
                    errors.append(str(msg))

                # refresh dst tree
                if ok and not target_poll_active[0]:
                    p = dst_tree.find_item_by_path(dst_tree.topLevelItem(0), target_path)
                    if p:
                        dst_tree.refresh_folder(p)

                # refresh src tree (folder parent dari item yang dipindah)
                dst_apk = os.path.join(target_path, os.path.basename(s))
                paired_apk = (is_cross and s.lower().endswith(".apk")
                              and apktool_tool.is_decompiled(dst_base, dst_apk))
                if ok and paired_apk:


                    si = source_rows.get(s)
                    try:
                        if (si is None or si.treeWidget() is not src_tree
                                or si.data(0, Qt.UserRole) != s):
                            si = None
                    except RuntimeError:
                        si = None
                    if si is None:
                        si = src_tree.find_item_by_path(src_tree.topLevelItem(0), s)
                    if si:
                        if si.parent():
                            si.parent().removeChild(si)
                        else:
                            src_tree.takeTopLevelItem(src_tree.indexOfTopLevelItem(si))
                elif ok or "restore failed" in str(msg):
                    sp = src_tree.find_item_by_path(src_tree.topLevelItem(0), os.path.dirname(s))
                    if sp:
                        src_tree.refresh_folder(sp)

                # summary saat semua done
                if done[0] + failed[0] >= total:
                    dst_tree.resume_size_probe()
                    if is_cross:
                        src_tree.resume_size_probe()
                    if target_poll_active[0]:
                        # Same final refresh as EROFS/EXT unpack. The poller
                        # touches the target only when it is expanded.
                        dst_tree.resume_watcher(target_path)
                        target_poll_active[0] = False
                    if expanded:
                        self.restore_expanded_paths(expanded)
                    if main:
                        if failed[0] == 0:
                            main.set_loading_finished(f"Moved {done[0]} item{'s' if done[0] != 1 else ''}")
                        else:
                            main.set_loading_failed(
                                f"{failed[0]} item(s) failed to move: {errors[0] if errors else ''}")
            return cb

        dst_tree.pause_size_probe()
        if is_cross:
            src_tree.pause_size_probe()

        for src in src_paths:
            kind = _classify(src)

            if kind == "config_blocked":
                if main:
                    from main_ext.class_AppDialog import AppDialog
                    AppDialog.warning(
                        main, t("rc_not_allowed_title"),
                        "The <b>config</b> folder cannot be moved."
                    )


                    if batch_id is not None:
                        main.loading_manager.report_done(batch_id, False, member_name=os.path.basename(src))
                total -= 1
                continue

            # NeoBranch guard untuk fs_root yang punya branch aktif
            if kind == "fs_root":
                part = os.path.basename(src)
                if src_tree.nb_is_active(part):
                    if main:
                        main.set_loading_failed(f"Cannot move fs_root with active branch: {part}")
                        if batch_id is not None:
                            main.loading_manager.report_done(batch_id, False, member_name=part)
                    total -= 1
                    continue

            rel_src_for_branch, old_root_for_branch, _, src_active_for_branch = _branch_state_for(src_tree, src_base, src)
            dst_logical_for_branch = os.path.join(target_path, os.path.basename(src))
            rel_dst_for_branch, new_root_for_branch, _, dst_active_for_branch = _branch_state_for(dst_tree, dst_base, dst_logical_for_branch)
            branch_involved = bool(src_active_for_branch or dst_active_for_branch)
            if kind == "normal" and branch_involved:
                kind = "branch_normal"

            task_id = f"moving-internal:{src}"
            worker = (
                _make_fs_root_worker(src, target_path)
                if kind == "fs_root"
                else (_make_branch_worker(src, target_path) if kind == "branch_normal" else _make_normal_worker(src, target_path))
            )
            quiet_tokens = []
            paired_active = [False]
            src_mirror = apktool_tool.mirror_dir_for(src_base, src) if is_cross and src.lower().endswith(".apk") else None
            if is_cross and src.lower().endswith(".apk"):
                worker = _with_apk_mirror(src, worker)

            def _on_promote_apk(s=src, mirror=src_mirror, tokens=quiet_tokens,
                                active=paired_active):


                if mirror:
                    active[0] = True
                    for tree, path, recursive in (
                        (src_tree, os.path.dirname(s), True),
                        (src_tree, os.path.join(src_base, apktool_tool.APKTOOL_DEC_DIR), True),
                        (dst_tree, target_path, True),
                        (dst_tree, os.path.join(dst_base, apktool_tool.APKTOOL_DEC_DIR), True),
                        (dst_tree, dst_base, False),
                    ):
                        tree._set_bulk_watcher_quiet(path, recursive, True)
                        tokens.append((tree, path, recursive))
                    # Source virtual folder is hidden while its physical mirror
                    # moves. The destination folder gets unpack-style 5s polls.
                    src_tree.pause_watcher(mirror, refresh_ui=False)
                    if not target_poll_active[0]:
                        dst_tree.pause_watcher(target_path)
                        target_poll_active[0] = True
            cb      = _make_callback(src, kind, task_id)

            def _release_after(callback, tokens, active, mirror, source):
                def finished(ok, msg):
                    try:
                        callback(ok, msg)
                    finally:
                        if active[0]:
                            src_tree.resume_watcher(mirror)
                            if not ok:
                                si = src_tree.find_item_by_path(src_tree.topLevelItem(0), source)
                                if si and si.isExpanded():
                                    src_tree._readd_expanded_watchers(si)
                        for tree, path, recursive in tokens:
                            tree._set_bulk_watcher_quiet(path, recursive, False)
                        if (target_poll_active[0] and main
                                and not main.task_queue.is_busy()):
                            dst_tree.resume_watcher(target_path)
                            target_poll_active[0] = False
                return finished

            cb = _release_after(cb, quiet_tokens, paired_active, src_mirror, src)

            if main:
                try:
                    main.task_queue.enqueue(TaskItem(
                        task_id=task_id,
                        worker=worker,
                        callback=cb,
                        on_promote=_on_promote_apk,
                    ))
                except Exception:
                    for tree, path, recursive in quiet_tokens:
                        tree._set_bulk_watcher_quiet(path, recursive, False)
                    raise

        if total == 0:
            dst_tree.resume_size_probe()
            if is_cross:
                src_tree.resume_size_probe()
            if main:
                main.set_loading_finished("Nothing to move")

    def _merge_internal_folder(self, src, dst, move=True):

        # Kumpulkan semua file yang akan konflik dulu untuk hitung remaining
        conflicts = []
        for root, dirs, files in os.walk(src):
            # Cegah circular (symlink)
            real_root = os.path.realpath(root)
            real_dst  = os.path.realpath(dst)
            if real_root.startswith(real_dst + os.sep) or real_root == real_dst:
                dirs.clear()
                continue

            rel = os.path.relpath(root, src)
            target_root = dst if rel == "." else os.path.join(dst, rel)

            for fname in files:
                d = os.path.join(target_root, fname)
                if os.path.exists(d):
                    s = os.path.join(root, fname)
                    conflicts.append((root, target_root, fname, s, d))

        remaining_count = len(conflicts)
        # Walk dan eksekusi
        for root, dirs, files in os.walk(src):
            # Cegah circular
            real_root = os.path.realpath(root)
            real_dst  = os.path.realpath(dst)
            if real_root.startswith(real_dst + os.sep) or real_root == real_dst:
                dirs.clear()
                continue

            rel = os.path.relpath(root, src)
            target_root = dst if rel == "." else os.path.join(dst, rel)
            target_root = os.path.normpath(target_root)
            if not os.path.isdir(target_root):
                os.makedirs(target_root, exist_ok=True)

            for fname in files:
                s = os.path.join(root, fname)
                d = os.path.join(target_root, fname)

                if os.path.exists(d):
                    # Cek global policy dulu
                    if self.drop_conflict_policy == "replace_all":
                        try:
                            os.remove(d)
                        except Exception:
                            pass
                    elif self.drop_conflict_policy == "skip_all":
                        # Kurangi counter
                        remaining_count -= 1
                        continue
                    else:
                        # Tanya user
                        policy = self.ask_drop_conflict_policy(
                            fname,
                            src_path=s,
                            dst_path=d,
                            folder_context=True,
                            remaining=remaining_count,
                        )
                        remaining_count -= 1

                        if policy == "cancel":
                            raise RuntimeError("User cancelled")
                        if policy in ("skip", "skip_all"):
                            if policy == "skip_all":
                                self.drop_conflict_policy = "skip_all"
                            continue
                        # replace / replace_all
                        try:
                            os.remove(d)
                        except Exception:
                            pass

                try:
                    if move:
                        shutil.move(s, d)
                    else:
                        shutil.copy2(s, d)
                except Exception as e:
                    op = "move" if move else "copy"
                    print(f"[MERGE] {op} error {s} -> {d}: {e}")

        if move:
            # Hapus hanya direktori yang sudah kosong. Kalau user memilih Skip,
            # file sumber tetap aman dan folder asal dibiarkan ada.
            for root, dirs, files in os.walk(src, topdown=False):
                try:
                    os.rmdir(root)
                except OSError:
                    pass

    def paintEvent(self, event):
        # An opaque viewport can scroll its backing pixels without repainting
        # the parent/editor. Explicitly clear damaged/empty areas so removed
        # rows and partial scrolls cannot leave old glyphs behind.
        background = QPainter(self.viewport())
        background.fillRect(event.rect(), self.palette().color(self.backgroundRole()))
        background.end()
        super().paintEvent(event)
        self._acknowledge_paint_barrier()

        from PySide6.QtCore import QRectF
        RADIUS = 6.0
        MARGIN = 2

        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)

        # Theme accent for selection/drag highlights
        _ar, _ag, _ab = _th._palette["_accent_rgb"]
        _br, _bg, _bb = _th._palette["_border_rgb"]

        # SELECTED (rounded, theme accent)
        for sel_item in self.selectedItems():
            s_rect = self.visualItemRect(sel_item)
            if s_rect.isValid():
                painter.setBrush(QColor(_ar, _ag, _ab, 41))  # accent @ 0.16
                r = QRectF(s_rect.adjusted(MARGIN, 1, -MARGIN, -1))
                painter.drawRoundedRect(r, RADIUS, RADIUS)

        # ── HOVER (rounded, soft theme border tint, skip saat drag) ──
        hovered = getattr(self, "_hovered_item", None)
        drag_items = getattr(self, "drag_hover_items", set())
        is_dragging = bool(drag_items) or bool(self.drag_hover_item)
        is_right_clicking = getattr(self, "_right_click_active", False) or getattr(FileTreeContextMixin, "_global_right_click_active", False)
        if hovered and not is_dragging and not hovered.isSelected() and not is_right_clicking:
            h_rect = self.visualItemRect(hovered)
            if h_rect.isValid():
                painter.setBrush(QColor(_br, _bg, _bb, 18))  # border @ 0.07
                r = QRectF(h_rect.adjusted(MARGIN, 1, -MARGIN, -1))
                painter.drawRoundedRect(r, RADIUS, RADIUS)

        # DRAG-DROP (rounded, 1 bounding box besar)
        items = drag_items
        if not items:
            if self.drag_hover_item:
                items = {self.drag_hover_item}
            else:
                return

        painter.setBrush(QColor(_ar, _ag, _ab, 56))  # accent @ 0.22
        combined = None
        for hi_item in items:
            rect = self.visualItemRect(hi_item)
            if rect.isValid():
                combined = rect if combined is None else combined.united(rect)

        if combined and combined.isValid():
            r = QRectF(combined.adjusted(MARGIN, 0, -MARGIN, 0))
            painter.drawRoundedRect(r, RADIUS, RADIUS)


    def mimeData(self, items):
        mime = super().mimeData(items)
        from PySide6.QtCore import QUrl
        urls = []
        for i in items:
            p = i.data(0, Qt.UserRole + 20) or i.data(0, Qt.UserRole)
            if p and isinstance(p, str) and not p.startswith("mount://") and os.path.exists(p):
                urls.append(QUrl.fromLocalFile(os.path.normpath(p)))
        if urls:
            mime.setUrls(urls)
            mime.setText(" ".join(f'"{u.toLocalFile()}"' if ' ' in u.toLocalFile() else u.toLocalFile() for u in urls))
        return mime

    def startDrag(self, supportedActions):
        self.dragged_item = self.currentItem()
        if not self.dragged_item:
            return

        main = self.window()
        if not main:
            return

        # collect all selected items (multi-select drag)
        selected = self.selectedItems()
        if not selected:
            selected = [self.dragged_item]


        if self.is_mount_node(self.dragged_item):
            mount_sel = [i for i in selected if self.is_mount_node(i)
                         and (self.mount_ref(i) or ("", ""))[1]]
            if mount_sel:
                self._start_mount_drag(mount_sel, supportedActions)
                return

        # enforce single-parent: filter to items sharing parent with dragged_item
        anchor_parent = self.dragged_item.parent()
        selected = [i for i in selected if i.parent() is anchor_parent]

        # store as list on main for dropEvent to consume
        main.drag_src_paths = [
            i.data(0, Qt.UserRole)
            for i in selected
        ]
        main.drag_src_phys_paths = [
            (i.data(0, Qt.UserRole + 20) or i.data(0, Qt.UserRole))
            for i in selected
        ]
        main.drag_src_path   = main.drag_src_paths[0]  # backward compat
        main.drag_src_phys_path = main.drag_src_phys_paths[0]

        parent_item = self.dragged_item.parent()
        if parent_item:
            main.drag_src_parent = parent_item.data(0, Qt.UserRole)
        else:
            main.drag_src_parent = self.base

        drag = QDrag(self)
        drag.setMimeData(self.mimeData(selected))
        preview = self._mount_drag_pixmap(selected)
        if preview is not None:
            from PySide6.QtCore import QPoint
            drag.setPixmap(preview)
            drag.setHotSpot(QPoint(min(12, preview.width()-1), min(10, preview.height()-1)))
        # Qt draws just the selected icons/names beside the action cursor.
        # The tree row, columns, highlights and viewport are never captured.
        try:
            drag.exec(supportedActions, self.defaultDropAction())
        finally:
            if isValid(self):
                self.dragged_item = None
                self._clear_drag_hover()
            if isValid(main):
                for name in ("drag_src_paths", "drag_src_phys_paths", "drag_src_path",
                             "drag_src_phys_path", "drag_src_parent"):
                    setattr(main, name, None)


    def rebuild_path(self, path):
        # bangun ulang folder path di dalam tree
        parts = path.replace(self.base, "").strip("\\/").split("\\")
        node = self.topLevelItem(0)

        cur_path = self.base
        for part in parts:
            cur_path = os.path.join(cur_path, part)
            found = self.find_item_by_path(node, cur_path)
            if not found:
                name, ctx, perm, ctx_src = self.get_meta(cur_path)
                item = QTreeWidgetItem([name, ctx, perm])
                item.setData(0, Qt.UserRole, cur_path)
                item.setData(0, SIZE_LABEL_ROLE, "")
                item.setIcon(0, self.icon_folder)
                item.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
                self._apply_ctx_color(item, ctx_src)
                node.addChild(item)
                node = item
            else:
                node = found

        self.refresh_folder(node)

    def dragLeaveEvent(self, event):
        self._clear_drag_hover()
        event.accept()

    def _clear_drag_hover(self):
        self.drag_hover_item = None   # legacy, tetap ada
        self.drag_hover_items = set()  # set item yg di-highlight
        self._drag_hover_position = None
        self._hovered_item = None      # hover manual biar bisa rounded
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        # stop auto-scroll timer biar ga lanjut jalan setelah cursor keluar
        self._drag_scroll_speed = 0.0
        self._drag_scroll_accum = 0.0
        if self._drag_scroll_timer.isActive():
            self._drag_scroll_timer.stop()
        self.viewport().update()

    def apply_subtree(self, abs_path):
        if not abs_path.startswith(self.base):
            return

        for root, dirs, files in os.walk(abs_path):
            rel_root = os.path.relpath(root, self.base).replace("\\", "/")

            # apply folder — skip kalau udah ada di config atau neocat
            raw_ctx = self.engine.escaped_ctx_key(rel_root)
            if (raw_ctx not in self.ctx and raw_ctx not in self.ctx_neocat) or rel_root not in self.fs:
                self._nb_apply(rel_root)

            # apply files
            for f in files:
                full = os.path.join(root, f)
                rel = os.path.relpath(full, self.base).replace("\\", "/")

                raw_ctx_f = self.engine.escaped_ctx_key(rel)
                if (raw_ctx_f not in self.ctx and raw_ctx_f not in self.ctx_neocat) or rel not in self.fs:
                    self._nb_apply(rel)

    def open_context_menu(self, pos):
        return self.RightClick(pos)

    def is_payload_img_ready(self, img_path):
        cfg_dir = os.path.join(
            os.path.dirname(img_path),
            "config",
            ".payload_ready"
        )
        flag = os.path.join(cfg_dir, os.path.basename(img_path) + ".ready")
        return os.path.isfile(flag)

    def on_payload_extract_finished(self, img_path):
        if not self.is_payload_img_ready(img_path):
            return

        parent_dir = os.path.dirname(img_path)
        parent_item = self.find_item_by_path(
            self.topLevelItem(0),
            parent_dir
        )
        if parent_item:
            self.refresh_folder(parent_item)

        self.apply_fs_badge_after_finished(img_path)

        # 🧹 CLEANUP READY FLAG
        try:
            cfg_dir = os.path.join(parent_dir, "config", ".payload_ready")
            flag = os.path.join(cfg_dir, os.path.basename(img_path) + ".ready")
            if os.path.isfile(flag):
                os.remove(flag)
        except Exception:
            pass

    def rename_item(self, item):
        if self._creating_item or not item:
            return

        try:
            old_name = item.text(0)
            old_path = item.data(0, Qt.UserRole)


            if item.data(0, Qt.UserRole + 50):
                AppDialog.info(self.window(), t("rc_not_allowed_title"), t("rc_symlink_no_rename"))
                return

        except RuntimeError:
            return

        if not old_path:
            return


        branch_abs = item.data(0, Qt.UserRole + 20)
        if not os.path.exists(old_path) and branch_abs and os.path.exists(branch_abs):
            # File hanya ada di branch — rename di branch filesystem
            _actual_path = branch_abs
        elif os.path.exists(old_path):
            _actual_path = old_path
        else:
            return

        parent_dir = os.path.dirname(_actual_path)

        self._editing_item = item
        self._renaming = True
        finished = False

        # ⛔ BLOCK WATCHER
        try:
            self.watcher.blockSignals(True)
        except:
            pass

        def cleanup():
            nonlocal finished
            if finished:
                return
            finished = True

            try:
                self.itemChanged.disconnect(finish)
            except:
                pass

            try:
                self.closePersistentEditor(item, 0)
            except:
                pass

            try:
                self.watcher.blockSignals(False)
            except:
                pass

            self._editing_item = None
            self._renaming = False


        item.setFlags(item.flags() | Qt.ItemIsEditable)


        item.setFlags(item.flags() | Qt.ItemIsEditable)

        self.setCurrentItem(item)
        self.editItem(item, 0)

        # ⛔ PAKSA CURSOR DI AKHIR & MATIIN SELECT-ALL
        editor = self.itemWidget(item, 0)
        if editor is None:
            editor = self.focusWidget()

        if editor:
            try:
                editor.setCursorPosition(len(editor.text()))
                editor.deselect()
            except:
                pass

        def finish(item_changed, col):
            if finished:
                return
            if item_changed is not item or col != 0:
                return

            typed_name = item.text(0).strip()

            # kosong
            if not typed_name:
                AppDialog.warning(self.window(), t("rc_invalid_name_title"), t("rc_name_empty"))
                item.setText(0, old_name)
                cleanup()
                return

            logical_parent_dir = os.path.dirname(old_path)
            logical_new_path = os.path.join(logical_parent_dir, typed_name)
            new_path = os.path.join(parent_dir, typed_name)

            # NO-OP
            if os.path.normpath(logical_new_path) == os.path.normpath(old_path):
                item.setText(0, old_name)
                cleanup()
                return

            old_rel, old_root, nb, active = self._nb_active_state_for_logical(old_path)
            if nb and active and old_rel:
                try:
                    new_rel = os.path.relpath(logical_new_path, self.base).replace("\\", "/")
                    new_phys, _, _ = self._nb_resolve_write_path(logical_new_path)
                    old_branch_phys = branch_abs if branch_abs and os.path.exists(branch_abs) else os.path.join(
                        nb._branch_dir(old_root, active["name"]),
                        old_rel.replace("/", os.sep),
                    )

                    if os.path.exists(new_phys):
                        item.setText(0, old_name)
                        cleanup()
                        AppDialog.warning(self.window(), t("lbl_error"), t("rc_name_exists_msg"))
                        return

                    os.makedirs(os.path.dirname(new_phys), exist_ok=True)
                    self._detach_watchers_for(old_branch_phys if os.path.exists(old_branch_phys) else old_path)

                    if os.path.exists(old_branch_phys):
                        os.rename(old_branch_phys, new_phys)
                    elif os.path.isdir(old_path):
                        shutil.copytree(old_path, new_phys, symlinks=True)
                    elif os.path.isfile(old_path):
                        shutil.copy2(old_path, new_phys)
                    else:
                        cleanup()
                        return

                    branch_cfg = nb._branch_config_dir(old_root, active["name"])
                    self._nb_remap_config(branch_cfg, branch_cfg, old_rel, new_rel)
                    nb.record_delete(old_root, active["name"], old_rel)
                    nb.record_tree_edit(old_root, active["name"], new_rel, new_phys)

                    try:
                        self.reload_config_cache()
                    except Exception:
                        pass

                    item.setData(0, Qt.UserRole, logical_new_path)
                    item.setData(0, Qt.UserRole + 20, new_phys)
                    item.setText(0, typed_name)
                    if os.path.isfile(new_phys):
                        item.setIcon(0, self._resolve_file_icon(new_phys))
                    parent_item = item.parent()
                    cleanup()
                    if parent_item:
                        self.refresh_folder(parent_item)
                    return
                except Exception as e:
                    item.setText(0, old_name)
                    cleanup()
                    AppDialog.warning(self.window(), t("rc_rename_error_title"), str(e))
                    return


            self._detach_watchers_for(_actual_path)


            rename_err = self._retry_fs_op(lambda: os.rename(_actual_path, new_path))

            if rename_err is not None:
                kind, e = rename_err
                if kind == "exists":
                    item.setText(0, old_name)
                    cleanup()
                    AppDialog.warning(self.window(), t("lbl_error"), t("rc_name_exists_msg"))
                    return
                if kind == "notfound":
                    cleanup()
                    return
                if kind == "perm":
                    # Masih tertahan setelah retry normal — tawarkan force-unlock
                    # (kill tool managed app yang masih hidup) lalu coba sekali lagi.
                    if self._offer_force_unlock_and_retry(
                        lambda: os.rename(_actual_path, new_path)
                    ):
                        pass  # berhasil setelah force — lanjut ke bawah
                    else:
                        item.setText(0, old_name)
                        cleanup()
                        return
                else:
                    # other
                    item.setText(0, old_name)
                    cleanup()
                    AppDialog.warning(self.window(), t("rc_rename_error_title"), str(e))
                    return

            # UPDATE ENGINE (hanya untuk file di source tree, bukan branch-only)
            _is_branch_only = (_actual_path != old_path)
            if not _is_branch_only:
                from main_ext.class_FilesystemRenamer import FilesystemRenamer
                renamer = FilesystemRenamer(self.base, self.engine)
                try:
                    rel_old = os.path.relpath(old_path, self.base).replace("\\", "/")
                    rel_new = os.path.relpath(new_path, self.base).replace("\\", "/")
                    handled = renamer.rename(old_path, new_path)
                    if not handled:
                        self.engine.move_paths(rel_old, rel_new)
                    if handled:
                        self.reload_config_cache()
                        self.engine.rebuild_root_map()
                except Exception as e:
                    try:
                        os.rename(new_path, _actual_path)
                    except:
                        pass
                    item.setText(0, old_name)
                    cleanup()
                    AppDialog.warning(self.window(), t("rc_rename_error_title"), str(e))
                    return

            # COMMIT UI
            # Update branch_abs kalau branch-only item
            if _is_branch_only:
                item.setData(0, Qt.UserRole + 20, new_path)
            else:
                item.setData(0, Qt.UserRole, new_path)
            item.setText(0, typed_name)

            # Update icon sesuai extension baru
            if os.path.isfile(new_path):
                item.setIcon(0, self._resolve_file_icon(new_path))

            cleanup()

        self.itemChanged.connect(finish)


    def delete_item(self, item):

        self._enqueue_delete_task(item, skip_confirm=False)

    def _enqueue_delete_task(self, item, *, skip_confirm=False):

        from core.task_queue import TaskItem

        full = item.data(0, Qt.UserRole)
        print(f"[DEL] enqueue_delete ENTER | skip_confirm={skip_confirm} | full={full!r}")
        if not full:
            print("[DEL]   -> ABORT: no full path")
            return False


        _del_nb_active = self.nb_is_active_for_path(full)
        print(f"[DEL]   nb_active_for_path={_del_nb_active} | exists={os.path.exists(full)}")
        if not _del_nb_active and not os.path.exists(full):
            print("[DEL]   -> ABORT: non-branch & path not exist")
            return False

        _win = self.window()


        if self.base and os.path.isdir(full):
            rel = os.path.relpath(full, self.base).replace("\\", "/")
            if rel == "config":
                print("[DEL]   -> BLOCK: config folder")
                AppDialog.warning(
                    self, t("rc_not_allowed_title"),
                    "The <b>config</b> folder cannot be deleted.<br>"
                    "You can delete individual files inside it."
                )
                return False


        if item.data(0, Qt.UserRole + 50):
            print("[DEL]   -> SYMLINK placeholder, remove instan (no ring)")
            try:
                os.remove(full)  # hapus file symlink placeholder saja
            except Exception as e:
                print("Delete symlink error:", e)
                return False

            parent = item.parent()
            if parent:
                self.refresh_folder(parent)
            return True

        is_folder = os.path.isdir(full)
        name = os.path.basename(full)
        kind = "folder and all its contents" if is_folder else "file"
        print(f"[DEL]   is_folder={is_folder} | name={name!r}")


        if _del_nb_active and is_folder:
            try:
                rel_root = os.path.relpath(full, self.base).replace("\\", "/")
            except Exception:
                rel_root = ""
            if rel_root and "/" not in rel_root:
                nb = self._get_nb_manager()
                active = nb.get_active_branch(rel_root) if nb else None
                if active:
                    print(f"[DEL]   -> BRANCH-ROOT delete | root={rel_root!r} | branch={active['name']!r} (instan, no ring)")
                    if not skip_confirm:
                        confirmed = AppDialog.confirm(
                            _win,
                            "Delete Branch",
                            f"Delete active branch <b>{active['name']}</b> for <b>{name}</b>?<br>"
                            "The source filesystem is not affected and will be shown again.",
                            yes_label="Delete Branch",
                            no_label="Cancel",
                        )
                        if not confirmed:
                            print("[DEL]   -> CANCELLED branch-root delete")
                            return False
                    try:
                        nb.delete_branch(rel_root, active["name"])
                        print(f"[DEL]   branch-root deleted OK: {rel_root}")
                    except Exception as e:
                        print(f"[DEL]   branch-root delete FAILED: {e}")
                        AppDialog.warning(self.window(), "Delete Branch Failed", str(e))
                        return False
                    try:
                        self._neobranch_update_dot(item, rel_root, nb)
                        self._neobranch_on_switch(item, rel_root)
                    except Exception:
                        parent = item.parent()
                        if parent:
                            self.refresh_folder(parent)
                    return True


        if _del_nb_active:
            print("[DEL]   -> BRANCH logical delete (mode=branch, lewat ring)")
            branch_kind = "folder" if is_folder else "file"
            if not skip_confirm:
                confirmed = AppDialog.confirm(
                    _win,
                    "Delete in Branch",
                    f"Delete {branch_kind} <b>{name}</b> from this branch?<br>"
                    "The source filesystem is not affected — "
                    "this entry will be hidden while this branch is active.",
                    yes_label="Delete",
                    no_label="Cancel",
                )
                if not confirmed:
                    print("[DEL]   -> CANCELLED branch logical delete")
                    return False
            return self._submit_delete_task(
                item, full, is_folder=is_folder, mode="branch",
            )


        print("[DEL]   -> PHYSICAL delete (mode=physical, lewat ring)")
        if not skip_confirm:
            confirmed = AppDialog.confirm(
                _win,
                "Delete",
                f"Are you sure you want to delete the {kind} <b>{name}</b>?<br>"
                "This action cannot be undone.",
                yes_label="Delete",
                no_label="Cancel",
            )
            if not confirmed:
                print("[DEL]   -> CANCELLED physical delete")
                return False

        return self._submit_delete_task(
            item, full, is_folder=is_folder, mode="physical",
        )

    def _submit_delete_task(self, item, full, *, is_folder, mode):

        from core.task_queue import TaskItem

        main = self.window()

        rel  = os.path.relpath(full, self.base).replace("\\", "/")
        root = rel.split("/")[0]
        is_fs_root = (
            mode == "physical" and is_folder and rel == root
            and hasattr(self, "engine") and self.engine is not None
            and root in self.engine.root_map_ctx
            and root in self.engine.root_map_fs
        )

        print(f"[DEL] submit_delete | mode={mode} | is_fs_root={is_fs_root} | rel={rel!r} | task_id=deleting:{os.path.basename(full)}")


        parent = item.parent()
        parent_index = parent.indexOfChild(item) if parent else -1
        if parent:
            parent.removeChild(item)
        print(f"[DEL]   optimistic removal done (parent_index={parent_index})")

        task_id = f"deleting:{full}"

        def worker(_full=full, _is_folder=is_folder, _mode=mode):
            print(f"[DEL][worker] START | mode={_mode} | {os.path.basename(_full)}")
            try:
                if _mode == "branch":
                    # Logical delete only — source tree NEVER touched.
                    # record_delete juga prune overlay store secara terkontrol.
                    rec = self.nb_record_delete(_full)
                    print(f"[DEL][worker] branch record_delete -> {rec} (source NOT touched)")
                    return True, ""

                def _do_delete():
                    if _is_folder:


                        def _rm_readonly(func, path, _exc):
                            try:
                                os.chmod(path, stat.S_IWRITE)
                                func(path)
                            except Exception as e:
                                print(f"[DEL][worker] rm readonly retry failed: {path}: {e}")
                                raise
                        shutil.rmtree(_full, onerror=_rm_readonly)
                    else:
                        os.remove(_full)


                import time as _t
                last_exc = None
                for attempt in range(5):
                    try:
                        _do_delete()
                        print(f"[DEL][worker] delete OK: {_full}")
                        return True, ""
                    except (PermissionError, OSError) as e:
                        winerror = getattr(e, "winerror", None)
                        if isinstance(e, PermissionError) or winerror in (5, 32, 33):
                            last_exc = e
                            _t.sleep(0.08 * (attempt + 1))
                            continue
                        raise
                print(f"[DEL][worker] still locked after retry: {last_exc}")
                return False, f"PERMLOCK:{last_exc}"
            except Exception as e:
                print(f"[DEL][worker] EXCEPTION: {e}")
                return False, str(e)

        def after(ok, msg,
                  _parent=parent, _parent_index=parent_index, _item=item,
                  _full=full, _rel=rel, _root=root, _is_fs_root=is_fs_root,
                  _mode=mode, _tid=task_id):
            print(f"[DEL][after] ok={ok} | mode={_mode} | msg={msg!r}")

            if not ok and isinstance(msg, str) and msg.startswith("PERMLOCK:"):


                def _force_delete_op(_p=_full):
                    if os.path.isdir(_p):
                        def _rm_readonly(func, path, _exc):
                            os.chmod(path, stat.S_IWRITE)
                            func(path)
                        shutil.rmtree(_p, onerror=_rm_readonly)
                    else:
                        os.remove(_p)

                if self._offer_force_unlock_and_retry(_force_delete_op):
                    print(f"[DEL][after] force-unlock retry OK: {_full}")
                    ok = True
                else:
                    print(f"[DELETE] failed after force-unlock attempt: {msg}")

            if main:
                main.pop_task(_tid, ok)

            if not ok:
                # Gagal — restore item ke posisi semula
                print(f"[DELETE] failed: {msg}")
                if _parent is not None:
                    if _parent_index >= 0:
                        _parent.insertChild(_parent_index, _item)
                    else:
                        _parent.addChild(_item)
                    self.refresh_folder(_parent)
                if main:
                    main.set_loading_failed("Delete failed")
                return

            # Berhasil
            if _mode == "branch":
                # Item sudah lepas dari tree; refresh parent supaya state branch
                # (dot/hidden) konsisten.
                if _parent is not None:
                    self.refresh_folder(_parent)
                if main:
                    main.maybe_finish_loading()
                return

            # physical: cleanup post-delete
            if _is_fs_root:
                config_dir = os.path.join(self.base, "config")
                for suffix in ("_file_contexts", "_fs_config", "_fs_options", "_info"):
                    cfg_file = os.path.join(config_dir, _root + suffix)
                    if os.path.isfile(cfg_file):
                        try:
                            os.remove(cfg_file)
                            print("[DELETE] removed config:", os.path.basename(cfg_file))
                        except Exception as e:
                            print("Config delete error:", e)
                self.reload_config_cache()
                self.engine.rebuild_root_map()

            if hasattr(self.engine, "delete_paths"):
                try:
                    self.engine.delete_paths(_rel)
                except Exception as e:
                    print("WARN delete_paths:", e)

            if _full in self.fsdetect_cache:
                del self.fsdetect_cache[_full]

            if main:
                main.maybe_finish_loading()

        task = TaskItem(task_id=task_id, worker=worker, callback=after)
        if main:
            main.push_task(task_id)
            print(f"[DEL]   -> ENQUEUE ke main.task_queue (ring) | id={task_id}")
            main.task_queue.enqueue(task)
        else:
            # Tanpa main window: fallback langsung (test/headless).
            print("[DEL]   -> no main window, run_in_thread fallback")
            run_in_thread(worker, None, callback=after)
        return True
