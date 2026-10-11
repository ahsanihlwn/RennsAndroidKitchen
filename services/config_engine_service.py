# SPDX-License-Identifier: MPL-2.0
import os
import sys
import json
import asyncio
import traceback
import hmac

import websockets

from services.protocol import (
    PROTOCOL_VERSION,
    OP_PING, OP_SHUTDOWN,
    OP_CONFIG_INIT, OP_CONFIG_APPLY, OP_CONFIG_MOVE_PATHS,
    OP_CONFIG_MOVE_PATHS_CROSS, OP_CONFIG_DELETE_PATHS,
    OP_CONFIG_APPLY_CTX_ONLY, OP_CONFIG_APPLY_PERM_ONLY,
    OP_CONFIG_APPLY_REPACK, OP_CONFIG_PREDICTED_CTX,
    OP_CONFIG_VALIDATE_FS, OP_CONFIG_HANDLE_TRANSFER,
    OP_CONFIG_REBUILD_ROOT_MAP, OP_CONFIG_GET_STATE,
    OP_CONFIG_LOAD_FULL_STATE,
    OP_PRESCAN_FULL, OP_PRESCAN_FAST,
    OP_LOAD_FILE_CONTEXT, OP_LOAD_FS_CONFIG,
    OP_PAYLOAD_EXTRACT_PARTITION,
    ERR_INVALID_OP, ERR_BAD_ARGS, ERR_INTERNAL,
    EVT_PROGRESS, EVT_READY,
    HANDSHAKE_FILE_ENV, HANDSHAKE_TOKEN_ENV, handshake_mac,
)

from core.config_engine_pure import (
    ConfigEngine,
    prescan_task as prescan_task_full,
    load_file_context,
    load_fs_config,
    load_full_state as _load_full_state_pure,
)


_engines: dict[str, ConfigEngine] = {}


def _get_engine(base: str) -> ConfigEngine:
    if base not in _engines:
        _engines[base] = ConfigEngine(base)
    return _engines[base]


def _engine_state(eng: ConfigEngine) -> dict:

    return {
        "ctx": eng.ctx if hasattr(eng, "ctx") else load_file_context(eng.base),
        "fs": eng.fs if hasattr(eng, "fs") else load_fs_config(eng.base),
        "root_map_ctx": dict(eng.root_map_ctx),
        "root_map_fs": dict(eng.root_map_fs),
    }


# DISPATCH

async def _dispatch(req: dict, ws) -> dict:
    op = req.get("op")
    args = req.get("args") or {}
    req_id = req.get("id")

    def reply_ok(result):
        return {"id": req_id, "ok": True, "result": result}

    def reply_err(code, msg, tb=None):
        r = {"id": req_id, "ok": False, "error": code, "msg": str(msg)}
        if tb:
            r["traceback"] = tb
        return r

    try:
        # SYSTEM
        if op == OP_PING:
            from core.native_backend import diagnostics
            return reply_ok({"version": PROTOCOL_VERSION, "pid": os.getpid(),
                             "native_backend": diagnostics()})

        if op == OP_SHUTDOWN:
            loop = asyncio.get_event_loop()
            loop.call_later(0.1, lambda: os._exit(0))
            return reply_ok("bye")

        # CONFIG ENGINE
        if op == OP_CONFIG_INIT:
            base = args["base"]
            eng = _get_engine(base)
            return reply_ok(_engine_state(eng))

        if op == OP_CONFIG_GET_STATE:
            base = args["base"]
            eng = _get_engine(base)
            return reply_ok(_engine_state(eng))

        if op == OP_CONFIG_LOAD_FULL_STATE:
            # ctx + fs + neocat + neobranch overlay sekaligus
            # — heavy disk IO di service proc, main thread bersih
            base = args["base"]
            eng = _get_engine(base)
            loop = asyncio.get_event_loop()
            full = await loop.run_in_executor(None, _load_full_state_pure, base)
            # Sync ke instance engine biar state-nya konsisten dengan UI
            eng.ctx = full["ctx"]
            eng.fs = full["fs"]
            return reply_ok({
                "ctx": full["ctx"],
                "fs": full["fs"],
                "neocat": full["neocat"],
                "root_map_ctx": dict(eng.root_map_ctx),
                "root_map_fs": dict(eng.root_map_fs),
            })

        if op == OP_CONFIG_APPLY:
            base = args["base"]
            rel = args["rel"]
            eng = _get_engine(base)
            eng.apply(rel)
            return reply_ok(_engine_state(eng))

        if op == OP_CONFIG_MOVE_PATHS:
            base = args["base"]
            eng = _get_engine(base)
            eng.move_paths(args["rel_old"], args["rel_new"])
            return reply_ok(_engine_state(eng))

        if op == OP_CONFIG_MOVE_PATHS_CROSS:
            base_dst = args["base_dst"]
            base_src = args["base_src"]
            dst_eng = _get_engine(base_dst)
            src_eng = _get_engine(base_src)
            dst_eng.move_paths_cross(src_eng, args["rel_old"], args["rel_new"])
            return reply_ok({
                "src": _engine_state(src_eng),
                "dst": _engine_state(dst_eng),
            })

        if op == OP_CONFIG_DELETE_PATHS:
            base = args["base"]
            eng = _get_engine(base)
            eng.delete_paths(args["rel"])
            return reply_ok(_engine_state(eng))

        if op == OP_CONFIG_APPLY_CTX_ONLY:
            base = args["base"]
            eng = _get_engine(base)
            eng.apply_context_only(args["rel"])
            return reply_ok(_engine_state(eng))

        if op == OP_CONFIG_APPLY_PERM_ONLY:
            base = args["base"]
            eng = _get_engine(base)
            eng.apply_permission_only(args["rel"])
            return reply_ok(_engine_state(eng))

        if op == OP_CONFIG_APPLY_REPACK:
            base = args["base"]
            eng = _get_engine(base)
            # blocking heavy op → run di thread pool
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, eng.apply_perm_on_repack, args["fs_root_folder"])
            return reply_ok(_engine_state(eng))

        if op == OP_CONFIG_PREDICTED_CTX:
            base = args["base"]
            eng = _get_engine(base)
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                eng.apply_predicted_context,
                args["fs_root_folder"],
                args.get("mode", "ai"),
                args.get("tree_id", 0),
                args.get("dry_run", False),
            )
            return reply_ok(_engine_state(eng))

        if op == OP_CONFIG_VALIDATE_FS:
            base = args["base"]
            eng = _get_engine(base)
            eng.validate_fs_config_for_mkfs(args["fs_config_path"])
            return reply_ok(None)

        if op == OP_CONFIG_HANDLE_TRANSFER:
            base = args["base"]
            eng = _get_engine(base)
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                eng.handle_fs_transfer,
                args.get("old_rel"),
                args["new_rel"],
                args.get("old_base"),
                args.get("new_base"),
                args.get("src_ctx"),
                args.get("src_fs"),
            )
            return reply_ok(_engine_state(eng))

        if op == OP_CONFIG_REBUILD_ROOT_MAP:
            base = args["base"]
            eng = _get_engine(base)
            eng.rebuild_root_map()
            return reply_ok(_engine_state(eng))

        # STANDALONE HELPERS
        if op == OP_PRESCAN_FULL:
            import functools
            base = args["base"]
            recursive = bool(args.get("recursive", False))
            loop = asyncio.get_event_loop()
            ok, payload = await loop.run_in_executor(
                None, functools.partial(prescan_task_full, base, recursive=recursive)
            )
            return reply_ok({"ok": ok, "payload": payload})

        if op == OP_PRESCAN_FAST:
            import functools
            base = args["base"]
            recursive = bool(args.get("recursive", False))


            from core.fsdetect import _prescan_task_inproc
            loop = asyncio.get_event_loop()
            ok, payload = await loop.run_in_executor(
                None, functools.partial(_prescan_task_inproc, base, recursive=recursive)
            )
            return reply_ok({"ok": ok, "payload": payload})

        if op == OP_LOAD_FILE_CONTEXT:
            return reply_ok(await asyncio.to_thread(load_file_context, args["base"]))

        if op == OP_LOAD_FS_CONFIG:
            return reply_ok(await asyncio.to_thread(load_fs_config, args["base"]))

        if op == OP_PAYLOAD_EXTRACT_PARTITION:
            payload_path = args["payload_path"]
            partition = args["partition"]
            out_dir = args["out_dir"]
            max_workers = int(args.get("max_workers") or 0)
            if max_workers <= 0:
                from core.storage_detector import get_optimal_io_workers
                max_workers = get_optimal_io_workers(out_dir, max_cap=64, hdd_cap=4)
            print(
                f"[PAYLOAD_DBG][SERVICE] REQUEST id={req_id} part={partition!r} "
                f"max_workers={max_workers} payload={payload_path!r} out_dir={out_dir!r}",
                flush=True,
            )

            def _extract_one():
                from core.payload_extract import extract_partitions_from_payload
                print(
                    f"[PAYLOAD_DBG][SERVICE_WORKER] START id={req_id} part={partition!r}",
                    flush=True,
                )
                with open(payload_path, "rb") as f:
                    extract_partitions_from_payload(
                        reader=f,
                        partitions_name=[partition],
                        out_dir=out_dir,
                        max_workers=max_workers,
                        on_partition_done=None,
                    )
                    print(
                        f"[PAYLOAD_DBG][SERVICE_WORKER] EXTRACT_RETURN id={req_id} part={partition!r}",
                        flush=True,
                    )
                print(
                    f"[PAYLOAD_DBG][SERVICE_WORKER] FILE_CLOSED id={req_id} part={partition!r}",
                    flush=True,
                )
                print(
                    f"[PAYLOAD_DBG][SERVICE_WORKER] DONE id={req_id} part={partition!r}",
                    flush=True,
                )
                return {"partition": partition}

            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, _extract_one)
            print(
                f"[PAYLOAD_DBG][SERVICE] REPLY id={req_id} part={partition!r} result={result!r}",
                flush=True,
            )
            return reply_ok(result)

        # UNKNOWN OP
        return reply_err(ERR_INVALID_OP, f"unknown op: {op}")

    except KeyError as e:
        return reply_err(ERR_BAD_ARGS, f"missing arg: {e}", traceback.format_exc())
    except Exception as e:
        return reply_err(ERR_INTERNAL, str(e), traceback.format_exc())


def _get_expected_token() -> str | None:
    return os.environ.get(HANDSHAKE_TOKEN_ENV)


# WS HANDLER

async def _handler(ws):

    expected = _get_expected_token()
    if not expected:
        await ws.close(code=4401, reason="service authentication unavailable")
        return

    print(f"[ConfigEngineService] client connected", flush=True)
    try:
        async for raw in ws:
            try:
                req = json.loads(raw)
            except Exception as e:
                print(f"[ConfigEngineService] invalid JSON: {e}", flush=True)
                continue

            # Token validation
            if expected:
                req_token = req.get("token", "")
                if not isinstance(req_token, str) or not hmac.compare_digest(req_token, expected):
                    err_reply = {
                        "id": req.get("id"),
                        "ok": False,
                        "error": "unauthorized",
                        "msg": "[SEC] Token tidak valid. Koneksi ditolak.",
                    }
                    try:
                        await ws.send(json.dumps(err_reply))
                    except Exception:
                        pass
                    print(
                        f"[ConfigEngineService] ⛔ request ditolak: token tidak valid "
                        f"(op={req.get('op')!r})",
                        flush=True,
                    )
                    # Tutup koneksi yang tidak ter-autentikasi
                    await ws.close(code=4401, reason="unauthorized")
                    return
            # End token validation

            reply = await _dispatch(req, ws)
            try:
                await ws.send(json.dumps(reply))
            except Exception as e:
                print(f"[ConfigEngineService] send error: {e}", flush=True)
                break
    except websockets.exceptions.ConnectionClosed:
        pass
    print(f"[ConfigEngineService] client disconnected", flush=True)


# ENTRY

async def _publish_handshake(hf: str, token: str, port: int) -> None:
    tmp = hf + ".tmp"
    pid = os.getpid()
    proof = handshake_mac(token, port, pid, PROTOCOL_VERSION)
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({
                "port": port, "pid": pid, "version": PROTOCOL_VERSION,
                "proof": proof,
            }, f)
        for attempt in range(10):
            try:
                os.replace(tmp, hf)
                return
            except PermissionError:
                if attempt == 9:
                    raise
                await asyncio.sleep(0.02)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


async def run_server():
    token = _get_expected_token()
    if not token:
        raise RuntimeError("RAK_SERVICE_TOKEN wajib ada; jalankan service via ServiceProcessManager")
    server = await websockets.serve(_handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    # tulis port ke handshake file biar parent tahu mau connect ke mana
    hf = os.environ.get(HANDSHAKE_FILE_ENV)
    if hf:
        try:
            await _publish_handshake(hf, token, port)
        except Exception:
            server.close()
            await server.wait_closed()
            raise

    print(f"[ConfigEngineService] listening on 127.0.0.1:{port} (pid={os.getpid()})", flush=True)
    await asyncio.Future()  # run forever
