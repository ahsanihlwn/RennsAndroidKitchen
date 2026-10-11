# SPDX-License-Identifier: MPL-2.0
from core.config_engine_pure import (
    ConfigEngine as _PureConfigEngine,
    prescan_task as _pure_prescan_task,
    load_file_context as _pure_load_file_context,
    load_fs_config as _pure_load_fs_config,
    perm_to_rwx,
    DEFAULT_CTX,
    DEFAULT_PERM,
)


def _service_available() -> bool:

    try:
        from main_ext.config_engine_client import _ServiceConnection
        # This is only a status query, including from background/pure CLI
        # workers. Bootstrap owns creating the Qt socket on the GUI thread.
        conn = _ServiceConnection._instance
        return conn is not None and conn.is_connected()
    except Exception:
        return False


def ConfigEngine(base):  # noqa: N802 (compat name, harus tetap PascalCase)
    if _service_available():
        from main_ext.config_engine_client import ConfigEngineClient
        return ConfigEngineClient(base)
    return _PureConfigEngine(base)


def prescan_task(base):
    if _service_available():
        from main_ext.config_engine_client import prescan_task as _svc_prescan
        return _svc_prescan(base)
    return _pure_prescan_task(base)


def load_file_context(basepath):
    if _service_available():
        from main_ext.config_engine_client import load_file_context as _svc_lfc
        return _svc_lfc(basepath)
    return _pure_load_file_context(basepath)


def load_fs_config(basepath):
    if _service_available():
        from main_ext.config_engine_client import load_fs_config as _svc_lfs
        return _svc_lfs(basepath)
    return _pure_load_fs_config(basepath)


__all__ = [
    "ConfigEngine",
    "prescan_task",
    "load_file_context",
    "load_fs_config",
    "perm_to_rwx",
    "DEFAULT_CTX",
    "DEFAULT_PERM",
]
