# SPDX-License-Identifier: MPL-2.0
import argparse
import bz2
import gc
import lzma
import mmap
import os
import queue
import struct
import threading
import time
import zipfile
from concurrent.futures import Future, ThreadPoolExecutor
from io import (
    SEEK_CUR,
    SEEK_SET,
    BufferedWriter,
)
import zstandard
from queue import Queue
from typing import IO, List

import requests

# Support package imports and direct execution.
try:
    from . import update_metadata_pb2
except ImportError:
    import update_metadata_pb2

print("[CORE STAGE] PAYLOAD EXTRACT")

class BadPayload(Exception):
    def __init__(self, *args):
        super().__init__("Invalid payload:", *args)


PAYLOAD_MAGIC = b"CrAU"


class PayloadHdr(object):
    _fmtstr = ">4sQQI"
    _payload = []

    def __init__(self, data: bytes):
        (
            self.magic,
            self.version,
            self.manifest_len,
            self.manifest_sig_len,
        ) = struct.unpack(self._fmtstr, data)

    def __len__(self) -> int:
        return struct.calcsize(self._fmtstr)


class OrderedFileWriter(object):
    def __init__(
        self,
        file: BufferedWriter,
        max_workers: int,
        label: str = "",
    ):
        self.file = file
        self.task_queue = Queue(max_workers * 2)
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.label = label
        self.error = None
        self.writer_thread = threading.Thread(target=self._write_worker, daemon=True)
        self.writer_thread.start()  # Start the thread immediately after creation


    def _write_worker(self):
        while not self.stop_event.is_set() or not self.task_queue.empty():
            data = None
            try:
                pos, data = self.task_queue.get(timeout=0.1)

                try:
                    with self.lock:
                        self.file.seek(pos, SEEK_SET)
                        self.file.write(data)
                except Exception as e:
                    self.error = e
                    print(
                        f"[PAYLOAD_DBG][CORE] WRITER_ERROR part={self.label!r} "
                        f"pos={pos} err={e!r}",
                        flush=True,
                    )
                    self.stop_event.set()
                finally:
                    del data
                    self.task_queue.task_done()

                if self.task_queue.qsize() % 50 == 0 and self.task_queue.qsize() > 0:
                    gc.collect()
            except queue.Empty:
                continue

    def write(self, pos: int, data: bytes):
        while not self.stop_event.is_set():
            if self.error:
                raise self.error
            try:
                self.task_queue.put((pos, data), timeout=0.2)
                return
            except queue.Full:
                continue
        if self.error:
            raise self.error

    def close(self):
        print(
            f"[PAYLOAD_DBG][CORE] WRITER_CLOSE_START part={self.label!r} "
            f"qsize={self.task_queue.qsize()} alive={self.writer_thread.is_alive()}",
            flush=True,
        )
        self.stop_event.set()
        self.writer_thread.join()
        try:
            self.file.flush()
        except Exception as e:
            self.error = self.error or e
            print(
                f"[PAYLOAD_DBG][CORE] WRITER_FLUSH_ERROR part={self.label!r} err={e!r}",
                flush=True,
            )
        print(
            f"[PAYLOAD_DBG][CORE] WRITER_CLOSE_DONE part={self.label!r} "
            f"qsize={self.task_queue.qsize()} error={self.error!r}",
            flush=True,
        )
        if self.error:
            raise self.error

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        self.close()
        # Return False so any worker/decompression exception is propagated properly
        return False


def init_payload_info(reader: IO[bytes]) -> update_metadata_pb2.DeltaArchiveManifest:
    hdr = PayloadHdr(reader.read(struct.calcsize(PayloadHdr._fmtstr)))

    if hdr.magic != PAYLOAD_MAGIC:
        raise BadPayload("invalid magic")
    if hdr.version != 2:
        print(f"Warning: payload version is {hdr.version} != 2 may be unsupported")
    if hdr.manifest_len == 0:
        raise BadPayload("manifest length is zero")
    if hdr.manifest_sig_len == 0:
        raise BadPayload("manifest signature length is zero")

    manifest = update_metadata_pb2.DeltaArchiveManifest.FromString(
        reader.read(hdr.manifest_len)
    )
    if manifest.minor_version not in [0, 8]:
        print(f"[!] Delta payload detected (minor_version={manifest.minor_version})")

    reader.seek(hdr.manifest_sig_len, SEEK_CUR)

    return manifest


def _extract_operation_to_file(
    operation: update_metadata_pb2.InstallOperation,
    writer: OrderedFileWriter,  # multi thread use
    out_offset: int,
    block_size: int,
    data: bytes,
):
    match operation.type:
        case update_metadata_pb2.InstallOperation.REPLACE:
            if len(operation.dst_extents) <= 1:
                writer.write(out_offset, data)
            else:
                off = 0
                for ext in operation.dst_extents:
                    sz = ext.num_blocks * block_size
                    writer.write(ext.start_block * block_size, data[off : off + sz])
                    off += sz
        case update_metadata_pb2.InstallOperation.ZERO:
            for ext in operation.dst_extents:
                out_seek = ext.start_block * block_size
                num_bytes = ext.num_blocks * block_size
                # Write in chunks to prevent huge single zero buffer allocation
                chunk_sz = 1024 * 1024
                while num_bytes > 0:
                    cur_sz = min(chunk_sz, num_bytes)
                    writer.write(out_seek, b"\0" * cur_sz)
                    out_seek += cur_sz
                    num_bytes -= cur_sz
        case (
            update_metadata_pb2.InstallOperation.REPLACE_BZ
            | update_metadata_pb2.InstallOperation.REPLACE_XZ
            | update_metadata_pb2.InstallOperation.REPLACE_ZSTD
        ):
            if operation.type == update_metadata_pb2.InstallOperation.REPLACE_BZ:
                decompressed_data = bz2.decompress(data)
            elif operation.type == update_metadata_pb2.InstallOperation.REPLACE_XZ:
                decompressed_data = lzma.decompress(data)
            elif operation.type == update_metadata_pb2.InstallOperation.REPLACE_ZSTD:
                decompressed_data = zstandard.decompress(data)

            if len(operation.dst_extents) <= 1:
                writer.write(out_offset, decompressed_data)
            else:
                off = 0
                for ext in operation.dst_extents:
                    sz = ext.num_blocks * block_size
                    writer.write(ext.start_block * block_size, decompressed_data[off : off + sz])
                    off += sz

            del decompressed_data

        case (
            update_metadata_pb2.InstallOperation.MOVE
            | update_metadata_pb2.InstallOperation.SOURCE_COPY
            | update_metadata_pb2.InstallOperation.BSDIFF
            | update_metadata_pb2.InstallOperation.SOURCE_BSDIFF
            | update_metadata_pb2.InstallOperation.BROTLI_BSDIFF
            | update_metadata_pb2.InstallOperation.PUFFDIFF
            | update_metadata_pb2.InstallOperation.ZUCCHINI
            | update_metadata_pb2.InstallOperation.LZ4DIFF_BSDIFF
            | update_metadata_pb2.InstallOperation.LZ4DIFF_PUFFDIFF
        ):
            writer.write(out_offset, data)
            print(f"[!] Delta op skipped (zero-filled): {operation.type}")

    del data


def _extract_partition_from_payload(
    reader: IO[bytes],
    block_size: int,
    partition: update_metadata_pb2.PartitionUpdate,
    out_path: str,
    total_size: int,
    executor: ThreadPoolExecutor,
):
    op_count = len(partition.operations)
    print(
        f"[PAYLOAD_DBG][CORE] PART_START part={partition.partition_name!r} "
        f"ops={op_count} out={out_path!r} total_size={total_size}",
        flush=True,
    )
    with (
        open(out_path, "wb") as out_file,
        OrderedFileWriter(out_file, executor._max_workers, partition.partition_name) as writer,
    ):
        out_file.truncate(total_size)  # pre set memory

        curr_data_offset = 0

        futures: List[Future] = []
        max_pending = max(64, executor._max_workers * 2)

        for op_index, operation in enumerate(
            sorted(partition.operations, key=lambda o: o.data_offset),
            start=1,
        ):
            if writer.error:
                raise writer.error

            data_len = operation.data_length
            data_offset = operation.data_offset
            if op_index == 1 or op_index == op_count or op_index % 500 == 0:
                print(
                    f"[PAYLOAD_DBG][CORE] OP part={partition.partition_name!r} "
                    f"{op_index}/{op_count} type={operation.type} data_len={data_len} "
                    f"data_offset={data_offset}",
                    flush=True,
                )

            reader.seek(data_offset - curr_data_offset, SEEK_CUR)

            data = reader.read(data_len)

            curr_data_offset = data_offset + data_len
            if writer:
                out_seek_block = operation.dst_extents[0].start_block * block_size if operation.dst_extents else 0
                futures.append(
                    executor.submit(
                        _extract_operation_to_file,
                        operation,
                        writer,
                        out_seek_block,
                        block_size,
                        data,
                    )
                )

                # Sliding window: drain completed futures to keep memory bounded
                while len(futures) >= max_pending:
                    futures.pop(0).result()
            del data

        print(
            f"[PAYLOAD_DBG][CORE] WAIT_FUTURES part={partition.partition_name!r} "
            f"count={len(futures)}",
            flush=True,
        )
        for future in futures:
            future.result()
        futures.clear()

        print(
            f"[PAYLOAD_DBG][CORE] FUTURES_DONE part={partition.partition_name!r}",
            flush=True,
        )

    print(f"Extract partition: {partition.partition_name:<16} size: {total_size:<10} ... Done!", flush=True)
    print(
        f"[PAYLOAD_DBG][CORE] PART_DONE part={partition.partition_name!r}",
        flush=True,
    )

    # SIGNAL PARTITION READY (HIDDEN, DO NOT CHANGE LOGIC)
    try:
        cfg_dir = os.path.join(os.path.dirname(out_path), "config", ".payload_ready")
        os.makedirs(cfg_dir, exist_ok=True)

        part_name = os.path.basename(out_path)
        ready_flag = os.path.join(cfg_dir, part_name + ".ready")

        with open(ready_flag, "w") as f:
            f.write("ready")
    except Exception:
        pass


def extract_partitions_from_payload(
    reader: IO[bytes],
    partitions_name: List[str] = [],
    out_dir: str = "out",
    max_workers: int = None,
    on_partition_done=None,
):
    if max_workers is None or max_workers <= 0:
        from core.storage_detector import get_optimal_io_workers
        max_workers = get_optimal_io_workers(out_dir, max_cap=64, hdd_cap=4)
    print(
        f"[PAYLOAD_DBG][CORE] EXTRACT_START requested={partitions_name} out_dir={out_dir!r} max_workers={max_workers}",
        flush=True,
    )
    reader.seek(0, SEEK_SET)
    os.makedirs(out_dir, exist_ok=True)

    manifest = init_payload_info(reader)
    baseoff = reader.tell()

    all_parts = (
        [p for p in manifest.partitions if p.partition_name in partitions_name]
        if partitions_name
        else manifest.partitions
    )
    print(
        f"[PAYLOAD_DBG][CORE] MANIFEST parts={[p.partition_name for p in all_parts]} "
        f"block_size={manifest.block_size}",
        flush=True,
    )

    block_size = manifest.block_size
    executor = ThreadPoolExecutor(max_workers=max_workers)
    try:
        for p in all_parts:
            reader.seek(baseoff, SEEK_SET)

            # Compute maximum block reached across all extents safely
            max_block = 0
            for op in p.operations:
                for ext in op.dst_extents:
                    end_b = ext.start_block + ext.num_blocks
                    if end_b > max_block:
                        max_block = end_b
            total_length = max_block * block_size

            print(f"Extracting {p.partition_name} ...")
            _extract_partition_from_payload(
                reader,
                block_size,
                p,
                os.path.join(out_dir, p.partition_name + ".img"),
                total_length,
                executor,
            )
            print(
                f"[PAYLOAD_DBG][CORE] EXTRACT_PART_RETURN part={p.partition_name!r}",
                flush=True,
            )


            if on_partition_done:
                on_partition_done(p.partition_name)
    finally:
        print(
            f"[PAYLOAD_DBG][CORE] EXECUTOR_SHUTDOWN_START requested={partitions_name}",
            flush=True,
        )
        executor.shutdown(wait=False, cancel_futures=True)
        print(
            f"[PAYLOAD_DBG][CORE] EXECUTOR_SHUTDOWN_DONE requested={partitions_name}",
            flush=True,
        )

    print(
        f"[PAYLOAD_DBG][CORE] EXTRACT_DONE requested={partitions_name}",
        flush=True,
    )

class SeekableMmap(mmap.mmap):
    def seekable(self) -> bool:  # stub
        return True


class UrlFileReader(IO[bytes]):
    UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"

    def __init__(self, url: str):
        self.url = url
        self.pos = 0
        self.total = 0
        self._closed = False

        resp = requests.head(self.url, headers={"User-Agent": self.UA})
        try:
            if resp.status_code == 200:
                if resp.headers.get("Accept-Ranges") != "bytes":
                    raise Exception("URL does not support range requests")
                self.total = int(resp.headers.get("Content-Length", 0))
            else:
                raise Exception(f"HTTP request failed with status {resp.status_code}")
        finally:
            resp.close()

        if self.total == 0:
            raise Exception("Could not determine content length")

        self.stream = None
        self.stream_start = 0
        self.stream_pos = 0

    def seekable(self) -> bool:
        return not self.closed

    def readable(self) -> bool:
        return not self.closed

    @property
    def closed(self) -> bool:
        return self._closed

    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        if self.closed:
            raise ValueError("I/O operation on closed file")

        if whence == os.SEEK_SET:
            self.pos = offset
        elif whence == os.SEEK_CUR:
            self.pos += offset
        elif whence == os.SEEK_END:
            self.pos = self.total + offset
        else:
            raise ValueError("Invalid whence value")

        self.pos = max(0, min(self.total, self.pos))
        return self.pos

    def tell(self) -> int:
        return self.pos

    def read(self, size: int = -1) -> bytes:
        if self.closed:
            raise ValueError("I/O operation on closed file")

        if self.pos >= self.total:
            return b""

        # 初始化或重新定位流
        if self.stream is None or self.stream_start + self.stream_pos != self.pos:
            if self.stream is not None:
                self.stream.close()

            headers = {"User-Agent": self.UA, "Range": f"bytes={self.pos}-"}
            self.stream = requests.get(self.url, headers=headers, stream=True)

            # 检查Range请求是否成功
            if self.stream.status_code not in (200, 206):
                self.stream.close()
                raise Exception(
                    f"Range request failed with status {self.stream.status_code}"
                )

            self.stream_start = self.pos
            self.stream_pos = 0

        # 确定要读取的大小
        if size < 0:
            size = self.total - self.pos
        else:
            size = min(size, self.total - self.pos)

        data = b""
        remaining = size
        while remaining > 0 and not self.stream.raw.closed:
            chunk_size = min(8192, remaining)
            chunk = self.stream.raw.read(chunk_size)
            if not chunk:
                break
            data += chunk
            read_len = len(chunk)
            self.stream_pos += read_len
            self.pos += read_len
            remaining -= read_len

        return data

    def close(self) -> None:
        if not self._closed:
            if self.stream is not None:
                self.stream.close()
            self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def __del__(self):
        self.close()


if __name__ == "__main__":

    parser = argparse.ArgumentParser(
        prog="payload_extract",
        description="extract zip/bin/url payload.bin",
    )

    parser.add_argument(
        "-t",
        "--type",
        choices=["zip", "bin", "url"],
        default="bin",
        dest="type",
        help="type of input",
    )
    parser.add_argument(
        "-T",
        "--thread",
        type=int,
        default=os.cpu_count() or 2,
        metavar="thread",
        dest="workers",
        help="workers",
    )
    parser.add_argument(
        "-i",
        "--input",
        type=str,
        metavar="input",
        dest="input",
        help="input file path",
        required=True,
    )
    parser.add_argument(
        "-o",
        "--outdir",
        type=str,
        metavar="outdir",
        default="out",
        dest="out",
        help="output directory",
    )


    parser.add_argument(
        "-X",
        "--extract-partitions",
        type=str,
        help="extract partitions, split with ','",
        metavar="boot,system,vendor...",
        dest="extract_partitions",
        default=None,
    )

    args = parser.parse_args()

    print("Extracting payload ...")
    now = time.time()

    match args.type:
        case "zip":
            with open(args.input, "rb") as f:
                with zipfile.ZipFile(f, "r") as zip:
                    for file in zip.filelist:
                        if file.filename.endswith("payload.bin"):
                            with zip.open(file, "r") as zf:
                                extract_partitions_from_payload(
                                    zf,
                                    (
                                        args.extract_partitions.split(",")
                                        if args.extract_partitions
                                        else []
                                    ),
                                    args.out,
                                    args.workers,
                                )
        case "bin":
            with open(args.input, "rb") as f:
                extract_partitions_from_payload(
                    f,
                    (
                        args.extract_partitions.split(",")
                        if args.extract_partitions
                        else []
                    ),
                    args.out,
                    args.workers,
                )
        case "url":
            with UrlFileReader(args.input) as r:
                with zipfile.ZipFile(r, "r") as zip:
                    for file in zip.filelist:
                        if file.filename.endswith("payload.bin"):
                            with zip.open(file, "r") as zf:
                                extract_partitions_from_payload(
                                    zf,
                                    (
                                        args.extract_partitions.split(",")
                                        if args.extract_partitions
                                        else []
                                    ),
                                    args.out,
                                    args.workers,
                                )
        case _:
            raise Exception("type not support")
    tooks = time.time() - now

    print("Done! tooks: %.2f" % tooks)
