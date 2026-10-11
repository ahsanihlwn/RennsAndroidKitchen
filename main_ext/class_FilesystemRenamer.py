# SPDX-License-Identifier: MPL-2.0
import os
import re
import shutil

class FilesystemRenamer:

    def __init__(self, project_base, engine):
        self.base = os.path.normpath(project_base)
        self.engine = engine
        self.config_dir = os.path.join(self.base, "config")


    def rename(self, abs_old_path, abs_new_path):
        rel_old = os.path.relpath(abs_old_path, self.base).replace("\\", "/")
        rel_new = os.path.relpath(abs_new_path, self.base).replace("\\", "/")

        old_parts = rel_old.split("/")
        new_parts = rel_new.split("/")

        old_root = old_parts[0]
        new_root = new_parts[0]


        if rel_old == old_root and new_root != old_root:
            self._rename_root_fs(old_root, new_root)
            return True


        if old_root in self.engine.root_map_ctx and \
           old_root in self.engine.root_map_fs:

            self._rename_subpath(rel_old, rel_new, old_root)
            return True


        return False


    def _rename_root_fs(self, old_root, new_root):
        for suffix in ("_file_contexts", "_fs_config", "_fs_options", "_info"):
            old_file = os.path.join(self.config_dir, old_root + suffix)
            new_file = os.path.join(self.config_dir, new_root + suffix)
            if os.path.isfile(old_file) and os.path.exists(new_file):
                raise FileExistsError(f"metadata target already exists: {new_file}")

        fc = os.path.join(self.config_dir, f"{old_root}_file_contexts")
        fs = os.path.join(self.config_dir, f"{old_root}_fs_config")
        opt = os.path.join(self.config_dir, f"{old_root}_fs_options")
        info = os.path.join(self.config_dir, f"{old_root}_info")

        # update file_contexts
        if os.path.isfile(fc):
            self._replace_prefix(fc, f"/{old_root}", f"/{new_root}")

        # update fs_config
        if os.path.isfile(fs):
            self._replace_prefix(fs, old_root, new_root)

        # EROFS
        if os.path.isfile(opt):
            self._rename_fs_options(opt, old_root, new_root)

        # EXT
        if os.path.isfile(info):
            self._rename_fs_info(info, old_root, new_root)

        # rename config filenames
        for suffix in (
            "_file_contexts",
            "_fs_config",
            "_fs_options",
            "_info",
        ):
            old_file = os.path.join(self.config_dir, old_root + suffix)
            if os.path.isfile(old_file):
                new_file = os.path.join(self.config_dir, new_root + suffix)
                import time

                for _ in range(5):
                    try:
                        shutil.move(old_file, new_file)
                        break
                    except PermissionError:
                        time.sleep(0.2)
                else:
                    raise


    def _rename_subpath(self, rel_old, rel_new, root):

        fc = os.path.join(self.config_dir, f"{root}_file_contexts")
        fs = os.path.join(self.config_dir, f"{root}_fs_config")

        old_prefix = "/" + rel_old
        new_prefix = "/" + rel_new

        if os.path.isfile(fc):
            self._replace_prefix(fc, old_prefix, new_prefix)

        if os.path.isfile(fs):
            self._replace_prefix(fs, rel_old, rel_new)


    def _replace_prefix(self, filepath, old_prefix, new_prefix):
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()

        out = []
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                out.append(line)
                continue

            parts = stripped.split()
            key = parts[0]

            if (key == old_prefix or key.startswith(old_prefix + "/")
                    or key.startswith(old_prefix + "(")):
                key = new_prefix + key[len(old_prefix):]
                parts[0] = key
                line = " ".join(parts) + "\n"

            out.append(line)

        with open(filepath, "w", encoding="utf-8", errors="ignore") as f:
            f.writelines(out)

    def _rename_fs_info(self, filepath, old_root, new_root):

        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        out = []
        for line in lines:
            key, sep, value = line.partition(":")
            if sep and key.strip() in ("PartitionName", "VolumeName"):
                if value.strip() == old_root:
                    line = f"{key}:{value[:len(value) - len(value.lstrip())]}{new_root}\n"
            elif sep and key.strip() == "MountPoint":
                mount = value.strip()
                if mount == f"/{old_root}" or mount.startswith(f"/{old_root}/"):
                    line = f"{key}: {('/' + new_root + mount[len(old_root) + 1:])}\n"
            out.append(line)
        with open(filepath, "w", encoding="utf-8", errors="ignore") as f:
            f.writelines(out)

    def _rename_fs_options(self, filepath, old_root, new_root):
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()

        out = []

        for line in lines:

            # mount-point
            line = re.sub(
                rf"--mount-point=/{re.escape(old_root)}\b",
                f"--mount-point=/{new_root}",
                line
            )

            # fs-config-file
            line = re.sub(
                rf"{re.escape(old_root)}_fs_config\b",
                f"{new_root}_fs_config",
                line
            )

            # file-contexts
            line = re.sub(
                rf"{re.escape(old_root)}_file_contexts\b",
                f"{new_root}_file_contexts",
                line
            )

            # repack image name
            line = re.sub(
                rf"\b{re.escape(old_root)}_repack\.img\b",
                f"{new_root}_repack.img",
                line
            )

            # last argument (filesystem path)
            line = re.sub(
                rf"({re.escape(self.base.replace('\\','/'))}/){re.escape(old_root)}\b",
                rf"\1{new_root}",
                line
            )

            out.append(line)

        with open(filepath, "w", encoding="utf-8", errors="ignore") as f:
            f.writelines(out)
