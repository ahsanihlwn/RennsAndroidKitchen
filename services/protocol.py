# SPDX-License-Identifier: MPL-2.0
import hashlib
import hmac


PROTOCOL_VERSION = 1

# OP NAMES
# System
OP_PING                    = "system.ping"
OP_SHUTDOWN                = "system.shutdown"

# ConfigEngine
OP_CONFIG_INIT             = "config.init"
OP_CONFIG_APPLY            = "config.apply"
OP_CONFIG_MOVE_PATHS       = "config.move_paths"
OP_CONFIG_MOVE_PATHS_CROSS = "config.move_paths_cross"
OP_CONFIG_DELETE_PATHS     = "config.delete_paths"
OP_CONFIG_APPLY_CTX_ONLY   = "config.apply_context_only"
OP_CONFIG_APPLY_PERM_ONLY  = "config.apply_permission_only"
OP_CONFIG_APPLY_REPACK     = "config.apply_perm_on_repack"
OP_CONFIG_PREDICTED_CTX    = "config.apply_predicted_context"
OP_CONFIG_VALIDATE_FS      = "config.validate_fs_config_for_mkfs"
OP_CONFIG_HANDLE_TRANSFER  = "config.handle_fs_transfer"
OP_CONFIG_REBUILD_ROOT_MAP = "config.rebuild_root_map"
OP_CONFIG_GET_STATE        = "config.get_state"
OP_CONFIG_LOAD_FULL_STATE  = "config.load_full_state"  # ctx + fs + neocat + nb_overlay sekaligus

# Standalone helpers
OP_PRESCAN_FULL            = "prescan.run_full"   # dict shape (mtime/size/fs_type) — versi class_ConfigEngine
OP_PRESCAN_FAST            = "prescan.run_fast"   # string shape (fs_type only)    — versi fsdetect
OP_LOAD_FILE_CONTEXT       = "config.load_file_context"
OP_LOAD_FS_CONFIG          = "config.load_fs_config"

# Payload tools
OP_PAYLOAD_EXTRACT_PARTITION = "payload.extract_partition"

# ERROR CODES
ERR_INVALID_OP   = "invalid_op"
ERR_BAD_ARGS     = "bad_args"
ERR_NOT_INIT     = "engine_not_initialized"
ERR_INTERNAL     = "internal_error"

# EVENTS
EVT_PROGRESS = "progress"
EVT_LOG      = "log"
EVT_READY    = "ready"

# HANDSHAKE
# Env var berisi path file tempat service nulis {port, pid} saat startup
HANDSHAKE_FILE_ENV = "RAK_SERVICE_HANDSHAKE"


HANDSHAKE_TOKEN_ENV = "RAK_SERVICE_TOKEN"


def handshake_mac(token: str, port: int, pid: int, version: int = PROTOCOL_VERSION) -> str:

    payload = f"{int(port)}:{int(pid)}:{int(version)}".encode("ascii")
    return hmac.new(token.encode("utf-8"), payload, hashlib.sha256).hexdigest()

# Env var dipakai untuk menandai bahwa proses ini adalah child service
# (dipakai oleh entry point untuk routing antara UI mode dan service mode)
SERVICE_MODE_ENV = "RAK_SERVICE_MODE"
