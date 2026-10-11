# SPDX-License-Identifier: MPL-2.0
import os
import uuid
import math
from collections import Counter

DEBUG_FS_BUILDER = True


def dbg(*args):
    if DEBUG_FS_BUILDER:
        print("[FS-BUILDER]", *args)


class FilesystemBuilder:
    def __init__(self, project_base, partition_path, forced_fs_type=None, source_fs_root=None, source_rel_prefix=None):

        self.project_base = os.path.normpath(project_base)
        self.partition_path = os.path.normpath(partition_path)
        self.partition = os.path.basename(self.partition_path.rstrip("/\\"))
        self.config_dir = os.path.join(self.project_base, "config")
        self.forced_fs_type = forced_fs_type
        self.source_fs_root = source_fs_root
        self.source_rel_prefix = source_rel_prefix


    def create_filesystem(self):
        dbg("===================================================")
        dbg("CREATE FILESYSTEM CALLED")
        dbg("project_base:", self.project_base)
        dbg("partition_path:", self.partition_path)
        dbg("partition:", self.partition)
        dbg("config_dir:", self.config_dir)

        if not os.path.isdir(self.partition_path):
            dbg("ERROR: partition_path is not a directory!")
            return False

        os.makedirs(self.config_dir, exist_ok=True)

        # cek apakah sudah ada
        if self._filesystem_exists():
            dbg("Filesystem already exists, skipping build.")
            return True

        parent_partition = self._detect_parent_partition()
        dbg("parent_partition:", parent_partition)

        fs_type = self._detect_parent_fs_type(parent_partition)
        dbg("detected fs_type:", fs_type)

        dominant_context = self._detect_dominant_context()
        dbg("dominant_context:", dominant_context)

        remapped = self._remap_from_parent_config()
        dbg("remapped entries:", len(remapped["ctx"]), "ctx,", len(remapped["fs"]), "fs")

        self._create_file_contexts(dominant_context, remapped["ctx"])
        self._create_fs_config(remapped["fs"])

        if fs_type == "EROFS":
            self._create_fs_options()
        else:
            self._create_fs_info()

        dbg("FILESYSTEM CREATED FOR:", self.partition)
        dbg("===================================================")
        return True


    def _filesystem_exists(self):
        fc = os.path.join(self.config_dir, f"{self.partition}_file_contexts")
        fs = os.path.join(self.config_dir, f"{self.partition}_fs_config")
        opt = os.path.join(self.config_dir, f"{self.partition}_fs_options")
        info = os.path.join(self.config_dir, f"{self.partition}_info")

        exists = os.path.isfile(fc) and os.path.isfile(fs) and (
            os.path.isfile(opt) or os.path.isfile(info)
        )

        dbg("filesystem_exists:", exists)
        return exists


    def _detect_parent_partition(self):
        rel = os.path.relpath(self.partition_path, self.project_base)
        rel = rel.replace("\\", "/")
        dbg("relative path:", rel)

        parts = rel.split("/")
        dbg("split parts:", parts)

        if len(parts) > 1:
            return parts[0]

        return None

    def _detect_parent_fs_type(self, parent):
        dbg("checking parent fs type:", parent)

        # 🔥 PRIORITY: forced from dropEvent
        if self.forced_fs_type:
            dbg("forced fs_type:", self.forced_fs_type)
            return self.forced_fs_type

        # 1️⃣ CEK CONFIG EXISTING
        existing_opt = os.path.join(self.config_dir, self.partition + "_fs_options")
        existing_info = os.path.join(self.config_dir, self.partition + "_info")

        if os.path.isfile(existing_opt):
            dbg("existing config says EROFS")
            return "EROFS"

        if os.path.isfile(existing_info):
            dbg("existing config says EXT")
            return "EXT"

        # 2️⃣ CEK PARENT
        if parent:
            parent_opt = os.path.join(self.config_dir, parent + "_fs_options")
            parent_info = os.path.join(self.config_dir, parent + "_info")

            if os.path.isfile(parent_opt):
                dbg("parent detected as EROFS")
                return "EROFS"

            if os.path.isfile(parent_info):
                dbg("parent detected as EXT")
                return "EXT"

        dbg("fallback EXT")
        return "EXT"


    def _remap_from_parent_config(self):

        result = {"ctx": {}, "fs": {}}

        if not self.source_fs_root or not self.source_rel_prefix:
            dbg("no source_fs_root/source_rel_prefix, skip remap")
            return result

        src_prefix = self.source_rel_prefix.replace("\\", "/").rstrip("/")
        new_partition = self.partition

        # FILE_CONTEXTS remap
        ctx_path = os.path.join(self.config_dir, self.source_fs_root + "_file_contexts")
        if os.path.isfile(ctx_path):
            try:
                with open(ctx_path, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        parts = line.split(None, 1)
                        if len(parts) != 2:
                            continue
                        pattern, label = parts


                        ctx_src_prefix = "/" + src_prefix.replace(".", "\\.")
                        if pattern == ctx_src_prefix or pattern.startswith(ctx_src_prefix + "/") or pattern.startswith(ctx_src_prefix + "("):
                            new_pattern = "/" + new_partition + pattern[len(ctx_src_prefix):]
                            result["ctx"][new_pattern] = label
            except Exception as e:
                dbg("ctx remap error:", e)

        # FS_CONFIG remap
        fs_path = os.path.join(self.config_dir, self.source_fs_root + "_fs_config")
        if os.path.isfile(fs_path):
            try:
                with open(fs_path, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        parts = line.split(None, 3)
                        if len(parts) < 4:
                            continue
                        rel, uid, gid, mode = parts[0], parts[1], parts[2], parts[3]
                        # rel di fs_config: src_prefix atau src_prefix/...
                        if rel == src_prefix or rel.startswith(src_prefix + "/"):
                            new_rel = new_partition + rel[len(src_prefix):]
                            result["fs"][new_rel] = f"{uid} {gid} {mode}"
            except Exception as e:
                dbg("fs remap error:", e)

        dbg("remap ctx keys:", list(result["ctx"].keys())[:5])
        dbg("remap fs keys:", list(result["fs"].keys())[:5])
        return result


    def _detect_dominant_context(self):
        try:
            from main_ext.class_ConfigEngine import load_file_context
            ctx = load_file_context(self.project_base)
        except Exception as e:
            dbg("context load failed:", e)
            return "u:object_r:system_file:s0"

        counter = Counter()
        src_prefix = (self.source_rel_prefix or "").replace("\\", "/").rstrip("/")

        for root, dirs, files in os.walk(self.partition_path):
            for name in files:
                full = os.path.join(root, name)
                rel = os.path.relpath(full, self.project_base).replace("\\", "/")

                # lookup pakai path saat ini (setelah move)
                if rel in ctx:
                    counter[ctx[rel]] += 1
                    continue

                # fallback: lookup pakai path lama (sebelum move, di parent fs_root)
                if src_prefix:
                    rel_in_partition = os.path.relpath(full, self.partition_path).replace("\\", "/")
                    old_rel = src_prefix + "/" + rel_in_partition
                    if old_rel in ctx:
                        counter[ctx[old_rel]] += 1

        dbg("context counter:", counter)

        if not counter:
            dbg("no context found → fallback system_file")
            return "u:object_r:system_file:s0"

        dominant = counter.most_common(1)[0][0]
        dbg("dominant context selected:", dominant)
        return dominant


    def _create_file_contexts(self, context, remapped_ctx=None):
        path = os.path.join(self.config_dir, f"{self.partition}_file_contexts")
        dbg("creating:", path)

        # newline="\n" WAJIB: mkfs.erofs/make_ext4fs (tool native Linux) parse
        # strict, CRLF dari text-mode default Windows bikin "Ill-formed line".
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            if remapped_ctx:
                lines = sorted(f"{pat} {lbl}" for pat, lbl in remapped_ctx.items())
                f.write("\n".join(lines) + "\n")
            else:
                f.write(f"/ {context}\n")


    def _create_fs_config(self, remapped_fs=None):
        path = os.path.join(self.config_dir, f"{self.partition}_fs_config")
        dbg("creating:", path)

        with open(path, "w", encoding="utf-8", newline="\n") as f:
            if remapped_fs:
                lines = sorted(f"{rel} {perm}" for rel, perm in remapped_fs.items())
                f.write("\n".join(lines) + "\n")
            else:
                f.write("/ 0 0 0755\n")


    def _create_fs_options(self):
        path = os.path.join(self.config_dir, f"{self.partition}_fs_options")
        fs_uuid = uuid.uuid4()

        dbg("creating:", path)

        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write("Filesystem created:        Thu Jan  1 07:00:00 1970\n")
            f.write(f"Filesystem UUID:           {fs_uuid}\n")
            f.write(
                f"mkfs.erofs options:        "
                f"-zlz4hc -C 16384 -T 0 "
                f"-U {fs_uuid} "
                f"--mount-point=/{self.partition} "
                f"--fs-config-file={self.partition}_file_contexts "
                f"{self.partition}_repack.img "
                f"{self.partition_path}\n"
            )


    def _create_fs_info(self):
        path = os.path.join(self.config_dir, f"{self.partition}_info")
        size = self._get_folder_size()
        block_size = 4096

        used_blocks = math.ceil(size / block_size)
        buffer_blocks = max(32, int(used_blocks * 0.05))
        block_count = used_blocks + buffer_blocks
        free_blocks = block_count - used_blocks

        fs_uuid = uuid.uuid4()

        dbg("creating:", path)
        dbg("size:", size)
        dbg("block_count:", block_count)
        dbg("free_blocks:", free_blocks)

        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(f"PartitionName: {self.partition}\n")
            f.write("Format: EXT4\n")
            f.write(f"Size: {size}\n")
            f.write("BlockSize: 4096\n")
            f.write(f"BlockCount: {block_count}\n")
            f.write(f"FreeBlocks: {free_blocks}\n")
            f.write(f"MountPoint: /mnt/{self.partition}\n")
            f.write("VolumeName: \n")
            f.write(f"UUID: {fs_uuid}\n")


    def _get_folder_size(self):
        total = 0
        for root, dirs, files in os.walk(self.partition_path):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(root, name))
                except Exception:
                    pass
        return total
