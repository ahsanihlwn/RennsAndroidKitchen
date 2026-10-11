# SPDX-License-Identifier: MPL-2.0
import os
import zipfile

from core import flashable_size as _fs

_ASSET_ZSTD = os.path.join(os.path.dirname(__file__), "flashable_assets", "zstd")


# materialize RAW image stream dari berbagai bentuk fisik

def _open_raw_stream(path, kind):

    if kind == "raw":
        f = open(path, "rb")
        return f, f.close

    if kind == "sparse":
        from core.sparse_reader import SparseReader
        sr = SparseReader(path)
        return sr, sr.close

    if kind == "zst":
        import zstandard
        f = open(path, "rb")
        dctx = zstandard.ZstdDecompressor()
        reader = dctx.stream_reader(f)

        def _close():
            try: reader.close()
            except Exception: pass
            try: f.close()
            except Exception: pass
        return reader, _close

    if kind == "br":
        f = open(path, "rb")
        wrapper = _BrotliStream(f)
        return wrapper, wrapper.close

    if kind in ("dat", "dat_br"):
        # sdat2img butuh nulis ke file -> materialize ke temp dulu.
        tmp = _dat_to_temp_img(path, kind)
        f = open(tmp, "rb")

        def _close():
            try: f.close()
            except Exception: pass
            try: os.remove(tmp)
            except Exception: pass
        return f, _close

    f = open(path, "rb")
    return f, f.close


class _BrotliStream:

    def __init__(self, fileobj):
        import brotli
        self._f = fileobj
        self._dec = brotli.Decompressor()
        self._buf = b""
        self._eof = False

    def read(self, n=-1):
        if n is None or n < 0:
            chunks = [self._buf]
            self._buf = b""
            while not self._eof:
                chunks.append(self._fill_all())
            return b"".join(chunks)
        while len(self._buf) < n and not self._eof:
            self._buf += self._fill_all()
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def _fill_all(self):
        raw = self._f.read(1 << 20)
        if not raw:
            self._eof = True
            return b""
        return self._dec.process(raw)

    def close(self):
        try: self._f.close()
        except Exception: pass


def _dat_to_temp_img(path, kind):

    import tempfile
    from core.flashable_size import _find_transfer_list

    dat_path = path
    if kind == "dat_br":
        # decompress brotli ke temp .new.dat dulu.
        import brotli
        td = tempfile.mkdtemp(prefix="flashdat_")
        dat_path = os.path.join(td, "part.new.dat")
        dec = brotli.Decompressor()
        with open(path, "rb") as fin, open(dat_path, "wb") as fout:
            while True:
                b = fin.read(1 << 20)
                if not b:
                    break
                fout.write(dec.process(b))

    transfer = _find_transfer_list(path, br=(kind == "dat_br"))
    out_img = tempfile.NamedTemporaryFile(prefix="flashimg_", suffix=".img", delete=False)
    out_img.close()
    from core.sdat2img.sdat2img import main as _sdat2img
    _sdat2img(transfer, dat_path, out_img.name)
    return out_img.name


def _copy_stream(src, dst, chunk=1 << 20, on_chunk=None, is_cancelled=None):
    while True:
        if is_cancelled and is_cancelled():
            raise InterruptedError("Cancelled by user")
        b = src.read(chunk)
        if not b:
            break
        dst.write(b)
        if on_chunk:
            on_chunk(len(b))


# builder

def build_flashable(out_zip, parts, script_text, progress=None, is_cancelled=None):

    total_parts = len(parts)
    # Total raw bytes to process across all parts
    total_expected_bytes = 0
    for p in parts:
        sz = int(getattr(p, "size", 0) or 0)
        if sz <= 0:
            sp = getattr(p, "src", None) or p.arc
            if sp and os.path.isfile(sp):
                sz = os.path.getsize(sp)
        total_expected_bytes += max(0, sz)

    bytes_done = 0

    def _report(part_idx, label, part_bytes_done=0, part_bytes_total=0):
        if not progress:
            return
        if total_expected_bytes > 0:
            pct = min(99, int((bytes_done / total_expected_bytes) * 100))
        else:
            pct = min(99, int((part_idx / (total_parts + 1)) * 100))
        try:
            progress(part_idx, total_parts, label, pct, part_bytes_done, part_bytes_total, bytes_done, total_expected_bytes)
        except TypeError:
            try:
                progress(pct, label)
            except Exception:
                try:
                    progress(part_idx, total_parts + 1, label)
                except Exception:
                    pass

    needs_zstd = any(getattr(p, "zstd", False) for p in parts)
    tmp_out = out_zip + ".part"

    try:
        if os.path.exists(tmp_out):
            try: os.remove(tmp_out)
            except Exception: pass

        with zipfile.ZipFile(tmp_out, "w", zipfile.ZIP_STORED, allowZip64=True) as z:
            for idx, p in enumerate(parts):
                if is_cancelled and is_cancelled():
                    raise InterruptedError("Cancelled by user")

                kind = _fs.classify(p.src) if getattr(p, "src", None) else "raw"
                src_path = getattr(p, "src", None) or p.arc
                part_sz = int(getattr(p, "size", 0) or 0)
                if part_sz <= 0 and src_path and os.path.isfile(src_path):
                    part_sz = os.path.getsize(src_path)

                part_bytes_done = 0
                label = f"{p.name} (.zst)" if getattr(p, "zstd", False) else p.name
                _report(idx, label, 0, part_sz)

                last_reported_bytes = 0

                def _on_chunk(n, p_name=label, p_sz=part_sz):
                    nonlocal bytes_done, part_bytes_done, last_reported_bytes
                    bytes_done += n
                    part_bytes_done += n
                    if (part_bytes_done - last_reported_bytes >= (2 << 20)) or (part_bytes_done >= p_sz):
                        last_reported_bytes = part_bytes_done
                        _report(idx, p_name, part_bytes_done, p_sz)

                _write_part(z, p, src_path, kind, on_chunk=_on_chunk, is_cancelled=is_cancelled)
                _report(idx + 1, label, part_bytes_done, part_sz)

            if is_cancelled and is_cancelled():
                raise InterruptedError("Cancelled by user")

            _report(total_parts, "update-binary", 0, 0)
            # update-binary
            info = zipfile.ZipInfo("META-INF/com/google/android/update-binary")
            info.external_attr = 0o755 << 16  # executable
            z.writestr(info, script_text.encode("utf-8"))

            # updater-script placeholder (beberapa recovery butuh ada filenya)
            z.writestr("META-INF/com/google/android/updater-script",
                       "# dummy — logic ada di update-binary\n")

            # zstd binary (cuma kalau dibutuhin)
            if needs_zstd:
                if not os.path.exists(_ASSET_ZSTD):
                    raise FileNotFoundError("binary zstd (aarch64) tidak ditemukan di assets")
                zi = zipfile.ZipInfo("META-INF/zstd")
                zi.external_attr = 0o755 << 16
                with open(_ASSET_ZSTD, "rb") as f:
                    z.writestr(zi, f.read())

            _report(total_parts + 1, "Finalizing", total_expected_bytes, total_expected_bytes)

        if is_cancelled and is_cancelled():
            raise InterruptedError("Cancelled by user")

        if os.path.exists(out_zip):
            os.remove(out_zip)
        os.replace(tmp_out, out_zip)

        if progress:
            try:
                progress(total_parts + 1, total_parts + 1, "Done", 100, total_expected_bytes, total_expected_bytes, total_expected_bytes, total_expected_bytes)
            except Exception:
                pass

        return True, out_zip
    except InterruptedError:
        try:
            if os.path.exists(tmp_out):
                os.remove(tmp_out)
        except Exception:
            pass
        return False, "Cancelled"
    except Exception as e:
        try:
            if os.path.exists(tmp_out):
                os.remove(tmp_out)
        except Exception:
            pass
        return False, f"{type(e).__name__}: {e}"


def _write_part(z, p, src_path, kind, on_chunk=None, is_cancelled=None):

    if getattr(p, "zstd", False):
        import zstandard
        cctx = zstandard.ZstdCompressor(level=int(getattr(p, "zstd_level", 19)))
        src, closer = _open_raw_stream(src_path, kind)


        raw_sz = int(getattr(p, "size", 0) or 0)
        try:
            zi = zipfile.ZipInfo(p.arc)
            with z.open(zi, "w", force_zip64=True) as dst:
                sw_kwargs = {"closefd": False}
                if raw_sz > 0:
                    sw_kwargs["size"] = raw_sz
                with cctx.stream_writer(dst, **sw_kwargs) as comp:
                    _copy_stream(src, comp, on_chunk=on_chunk, is_cancelled=is_cancelled)
        finally:
            closer()
    else:
        # raw: kalau sumber sudah .img polos -> langsung copy file (cepat);
        # kalau .zst/.br/.dat/sparse -> decompress jadi raw.
        if kind == "raw":
            zi = zipfile.ZipInfo(p.arc)
            with open(src_path, "rb") as fin, z.open(zi, "w", force_zip64=True) as dst:
                _copy_stream(fin, dst, on_chunk=on_chunk, is_cancelled=is_cancelled)
        else:
            src, closer = _open_raw_stream(src_path, kind)
            try:
                zi = zipfile.ZipInfo(p.arc)
                with z.open(zi, "w", force_zip64=True) as dst:
                    _copy_stream(src, dst, on_chunk=on_chunk, is_cancelled=is_cancelled)
            finally:
                closer()
