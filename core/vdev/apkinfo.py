# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import hashlib
import posixpath
import struct
import warnings
import zipfile

import io as _io

from core.vdev.axml import parse as parse_axml

print("[CORE STAGE] VDEV APKINFO")


try:
    from cryptography.hazmat.primitives.serialization import pkcs7 as _pkcs7
    from cryptography.hazmat.primitives.hashes import SHA256 as _sha256
except Exception:                           # noqa: BLE001
    _pkcs7 = None
    _sha256 = None

MANIFEST = "AndroidManifest.xml"
_HOME_CATEGORY = "android.intent.category.HOME"


class ApkInfo:
    __slots__ = ("vpath", "host", "package", "shared_user_id", "permissions",
                 "libraries", "overlay_target", "is_launcher", "error")

    def __init__(self, vpath, host):
        self.vpath = vpath
        self.host = host
        self.package = ""
        self.shared_user_id = ""
        self.permissions = set()
        self.libraries = []        # [(nama, required: bool)]
        self.overlay_target = ""
        self.is_launcher = False
        self.error = ""

    @property
    def name(self):
        return posixpath.basename(self.vpath)

    @property
    def privileged(self):

        return "/priv-app/" in self.vpath

    def __repr__(self):
        return f"<ApkInfo {self.package or self.name}>"


def _as_bool(value, default=True):
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() not in ("false", "0")
    return default


def _zip_arg(src):

    if isinstance(src, (bytes, bytearray, memoryview)):
        return _io.BytesIO(src)
    return src


def read_apk(vpath, host):

    info = ApkInfo(vpath, host)
    try:
        with zipfile.ZipFile(_zip_arg(host)) as z:
            data = z.read(MANIFEST)
    except KeyError:
        info.error = "no AndroidManifest.xml"
        return info
    except Exception as exc:                # noqa: BLE001 — APK rusak itu wajar
        info.error = str(exc)
        return info

    events = parse_axml(data)
    if events is None:
        info.error = "AndroidManifest is not AXML"
        return info

    for _depth, tag, attrs in events:
        if tag == "manifest":
            pkg = attrs.get("package")
            if isinstance(pkg, str):
                info.package = pkg
            suid = attrs.get("sharedUserId")
            if isinstance(suid, str):
                info.shared_user_id = suid
        elif tag == "uses-permission":
            n = attrs.get("name")
            if isinstance(n, str) and n:
                info.permissions.add(n)
        elif tag == "uses-library":
            n = attrs.get("name")
            if isinstance(n, str) and n:
                info.libraries.append((n, _as_bool(attrs.get("required"), True)))
        elif tag == "overlay":
            t = attrs.get("targetPackage")
            if isinstance(t, str) and t:
                info.overlay_target = t
        elif tag == "category":
            if attrs.get("name") == _HOME_CATEGORY:
                info.is_launcher = True

    return info


_SIG_BLOCK_MAGIC = b"APK Sig Block 42"
_EOCD_MAGIC = b"PK\x05\x06"
_SIG_SCHEME_IDS = (0x7109871A, 0xF05368C0, 0x1B93AD61)   # v2, v3, v3.1
_EOCD_SEARCH = 66000            # ukuran maksimum comment ZIP + header


_MAX_SIG_BLOCK = 8 << 20        # 8 MB; blok tanda tangan nyata jauh di bawah ini


def _cert_from_signing_block(host):

    if isinstance(host, (bytes, bytearray, memoryview)):
        return _cert_from_bytes(bytes(host))
    try:


        if hasattr(host, "seek") and hasattr(host, "read"):
            return _cert_from_stream(host)
        with open(host, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            if size < 32:
                return None
            tail_at = max(0, size - _EOCD_SEARCH)
            f.seek(tail_at)
            tail = f.read()

            i = tail.rfind(_EOCD_MAGIC)
            if i < 0:
                return None
            cd_offset = struct.unpack_from("<I", tail, i + 16)[0]
            if cd_offset < 24 or cd_offset > size:
                return None

            # 24 byte tepat sebelum central directory: [size u64][magic 16].
            f.seek(cd_offset - 24)
            footer = f.read(24)
            if len(footer) < 24 or footer[8:24] != _SIG_BLOCK_MAGIC:
                return None
            block_size = struct.unpack_from("<Q", footer, 0)[0]
            if block_size <= 0 or block_size > _MAX_SIG_BLOCK:
                return None
            start = cd_offset - 8 - block_size
            if start < 0:
                return None

            f.seek(start)
            data = f.read(block_size + 8)
    except OSError:
        return None

    return _scan_sig_pairs(data[8:len(data) - 24])


def _scan_sig_pairs(body):

    try:
        pos, end = 0, len(body)
        value = None
        while pos + 12 <= end:
            pair_len = struct.unpack_from("<Q", body, pos)[0]
            pair_id = struct.unpack_from("<I", body, pos + 8)[0]
            if pair_id in _SIG_SCHEME_IDS:
                value = pos + 12
                break
            if pair_len <= 0:
                return None
            pos += 8 + pair_len
        if value is None:
            return None

        p = value
        p += 4                                   # panjang rangkaian signers
        p += 4                                   # panjang signer pertama
        p += 4                                   # panjang signed data
        digests_len = struct.unpack_from("<I", body, p)[0]
        p += 4 + digests_len
        p += 4                                   # panjang rangkaian sertifikat
        cert_len = struct.unpack_from("<I", body, p)[0]
        p += 4
        der = body[p:p + cert_len]
        return hashlib.sha256(der).hexdigest() if der else None
    except (struct.error, IndexError, ValueError):
        return None


def _cert_from_stream(f):

    try:
        f.seek(0, 2)
        size = f.tell()
        if size < 32:
            return None
        f.seek(max(0, size - _EOCD_SEARCH))
        tail = f.read()
        i = tail.rfind(_EOCD_MAGIC)
        if i < 0:
            return None
        cd_offset = struct.unpack_from("<I", tail, i + 16)[0]
        if cd_offset < 24 or cd_offset > size:
            return None
        f.seek(cd_offset - 24)
        footer = f.read(24)
        if len(footer) < 24 or footer[8:24] != _SIG_BLOCK_MAGIC:
            return None
        block_size = struct.unpack_from("<Q", footer, 0)[0]
        if block_size <= 0 or block_size > _MAX_SIG_BLOCK:
            return None
        start = cd_offset - 8 - block_size
        if start < 0:
            return None
        f.seek(start)
        data = f.read(block_size + 8)
    except (OSError, ValueError, struct.error):
        return None
    return _scan_sig_pairs(data[8:len(data) - 24])


def _cert_from_bytes(blob):

    i = blob.rfind(_EOCD_MAGIC, max(0, len(blob) - _EOCD_SEARCH))
    if i < 0:
        return None
    try:
        cd_offset = struct.unpack_from("<I", blob, i + 16)[0]
        if cd_offset < 24 or cd_offset > len(blob):
            return None
        if blob[cd_offset - 16:cd_offset] != _SIG_BLOCK_MAGIC:
            return None
        block_size = struct.unpack_from("<Q", blob, cd_offset - 24)[0]
        if block_size <= 0 or block_size > _MAX_SIG_BLOCK:
            return None
        start = cd_offset - 8 - block_size
        if start < 0:
            return None
        return _scan_sig_pairs(blob[start + 8:cd_offset - 24])
    except (struct.error, IndexError, ValueError):
        return None


def cert_available():

    return True


def cert_fingerprint(host):


    fp = _cert_from_signing_block(host)
    if fp is not None:
        return fp
    if _pkcs7 is None:
        return None
    try:
        with warnings.catch_warnings():
            # cryptography memperingatkan tiap sertifikat ber-encoding BER.
            # Banyak APK OEM begitu, dan peringatannya muncul per-APK.
            warnings.simplefilter("ignore")
            return _first_cert(host)
    except Exception:                       # noqa: BLE001
        return None


def _first_cert(host):
    try:
        with zipfile.ZipFile(_zip_arg(host)) as z:
            for entry in z.namelist():
                upper = entry.upper()
                if upper.startswith("META-INF/") and upper.endswith((".RSA", ".DSA", ".EC")):
                    certs = _pkcs7.load_der_pkcs7_certificates(z.read(entry))
                    if certs:
                        return certs[0].fingerprint(_sha256()).hex()
    except Exception:                       # noqa: BLE001
        return None
    return None


def platform_permissions(host):

    try:
        with zipfile.ZipFile(_zip_arg(host)) as z:
            data = z.read(MANIFEST)
    except Exception:                       # noqa: BLE001
        return None
    events = parse_axml(data)
    if events is None:
        return None
    out = {}
    for _depth, tag, attrs in events:
        if tag != "permission":
            continue
        name = attrs.get("name")
        level = attrs.get("protectionLevel")
        if isinstance(name, str) and isinstance(level, int) and not isinstance(level, bool):
            out[name] = level
    return out
