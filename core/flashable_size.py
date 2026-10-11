# SPDX-License-Identifier: MPL-2.0
import os
import struct

# Magic-magic yang kita kenali dari header file.
_SPARSE_MAGIC = 0xED26FF3A
_ZSTD_MAGIC   = 0xFD2FB528   # little-endian frame magic


def _read_head(path, n=64):
    with open(path, "rb") as f:
        return f.read(n)


def classify(path):

    low = path.lower()
    # Ekstensi dulu (paling andal buat dat/brotli yang gak punya magic stabil).
    if low.endswith(".new.dat.br"):
        return "dat_br"
    if low.endswith(".new.dat"):
        return "dat"
    if low.endswith(".br"):
        return "br"

    head = _read_head(path, 8)
    if len(head) >= 4:
        magic = struct.unpack_from("<I", head, 0)[0]
        if magic == _SPARSE_MAGIC:
            return "sparse"
        if magic == _ZSTD_MAGIC:
            return "zst"
    if low.endswith(".zst") or low.endswith(".zstd"):
        return "zst"
    return "raw"


# per-format size

def _size_raw(path):
    return os.path.getsize(path)


def _size_sparse(path):
    from core.sparse_reader import SparseReader
    r = SparseReader(path)
    try:
        return r.total_blocks * r.block_size
    finally:
        try:
            r.close()
        except Exception:
            pass


def _size_zst(path):

    import zstandard
    with open(path, "rb") as f:
        head = f.read(18)  # max zstd frame header size
    size = zstandard.frame_content_size(head)
    # -1 = unknown (content-size flag gak diset waktu compress).
    if size is not None and size >= 0:
        return size
    # Fallback: stream-decompress, hitung byte (lambat tapi pasti).
    total = 0
    dctx = zstandard.ZstdDecompressor()
    with open(path, "rb") as f:
        reader = dctx.stream_reader(f)
        while True:
            chunk = reader.read(1 << 20)
            if not chunk:
                break
            total += len(chunk)
    return total


def _size_br(path):

    import brotli
    total = 0
    dec = brotli.Decompressor()
    with open(path, "rb") as f:
        while True:
            buf = f.read(1 << 20)
            if not buf:
                break
            out = dec.process(buf)
            total += len(out)
    return total


def _size_dat(path):

    transfer = _find_transfer_list(path, br=False)
    return _dat_size_from_transfer(transfer)


def _size_dat_br(path):

    transfer = _find_transfer_list(path, br=True)
    return _dat_size_from_transfer(transfer)


def _find_transfer_list(dat_path, br):

    base = dat_path
    for suf in (".new.dat.br", ".new.dat", ".br"):
        if base.lower().endswith(suf):
            base = base[: -len(suf)]
            break
    cand = base + ".transfer.list"
    if os.path.exists(cand):
        return cand
    # kadang nama persis "<part>.transfer.list" satu folder
    d = os.path.dirname(dat_path)
    name = os.path.basename(base) + ".transfer.list"
    cand2 = os.path.join(d, name)
    if os.path.exists(cand2):
        return cand2
    return None


def _dat_size_from_transfer(transfer_path):

    if not transfer_path or not os.path.exists(transfer_path):
        raise FileNotFoundError("transfer.list tidak ditemukan untuk .dat")
    with open(transfer_path, "r") as f:
        lines = [ln.strip() for ln in f.readlines()]
    if len(lines) < 2:
        raise ValueError("transfer.list tidak valid")
    # baris[0]=version, baris[1]=total blocks (format Android sdat).
    try:
        total_blocks = int(lines[1])
    except ValueError:
        raise ValueError("transfer.list: total blocks tidak terbaca")
    return total_blocks * 4096


_DISPATCH = {
    "raw":    _size_raw,
    "sparse": _size_sparse,
    "zst":    _size_zst,
    "br":     _size_br,
    "dat":    _size_dat,
    "dat_br": _size_dat_br,
}


def raw_image_size(path, kind=None):

    if kind is None:
        kind = classify(path)
    fn = _DISPATCH.get(kind, _size_raw)
    return fn(path)


def human_size(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return (f"{n:.0f} {unit}" if unit == "B" else f"{n:.2f} {unit}")
        n /= 1024
