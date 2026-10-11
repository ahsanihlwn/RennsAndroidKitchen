# SPDX-License-Identifier: MPL-2.0
import os
import hashlib
import json
from html import escape as escape_html

from PySide6.QtWidgets import QWidget, QVBoxLayout, QApplication
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtCore import Qt, QObject, Slot, Signal, QUrl, QTimer
from PySide6.QtGui import QKeySequence, QShortcut, QColor

from main_ext.class_ThemeUi import theme as _th
from core.monaco_assets import resolve_monaco_vs, monaco_vs_url

print("[MAIN STAGE] WEB_EDITOR_PANEL")


# LANGUAGE DETECTION

def _detect_language(filepath: str) -> str:
    ext = os.path.splitext(filepath)[1].lower()
    name = os.path.basename(filepath)

    # Special filenames (no extension)
    _name_map = {
        "makefile": "makefile",
        "dockerfile": "dockerfile",
    }
    if name.lower() in _name_map:
        return _name_map[name.lower()]

    return {
        ".py"    : "python",
        ".js"    : "javascript",
        ".ts"    : "typescript",
        ".jsx"   : "javascript",
        ".tsx"   : "typescript",
        ".json"  : "json",
        ".xml"   : "xml",
        ".html"  : "html",
        ".htm"   : "html",
        ".css"   : "css",
        ".java"  : "java",
        ".kt"    : "kotlin",
        ".cpp"   : "cpp",
        ".cc"    : "cpp",
        ".cxx"   : "cpp",
        ".c"     : "c",
        ".h"     : "cpp",
        ".hpp"   : "cpp",
        ".rs"    : "rust",
        ".go"    : "go",
        ".sh"    : "shell",
        ".bash"  : "shell",
        ".zsh"   : "shell",
        ".md"    : "markdown",
        ".yaml"  : "yaml",
        ".yml"   : "yaml",
        ".toml"  : "ini",
        ".ini"   : "ini",
        ".cfg"   : "ini",
        ".conf"  : "ini",
        ".php"   : "php",
        ".rb"    : "ruby",
        ".swift" : "swift",
        ".smali" : "java",
        ".rc"    : "plaintext",
        ".txt"   : "plaintext",
        ".log"   : "plaintext",
        ".prop"  : "ini",
        ".mk"    : "makefile",
    }.get(ext, "plaintext")


_EOL_LABEL = {"\n": "LF", "\r\n": "CRLF", "\r": "CR"}


def _detect_eol(text: str) -> str:

    crlf = text.count("\r\n")
    lf   = text.count("\n") - crlf
    cr   = text.count("\r") - crlf
    if crlf == 0 and lf == 0 and cr == 0:
        return "\n"
    if crlf >= lf and crlf >= cr:
        return "\r\n"
    if cr > lf:
        return "\r"
    return "\n"


def _decode_bytes(raw: bytes):

    if raw[:3] == b"\xef\xbb\xbf":
        return raw[3:].decode("utf-8", errors="replace"), "utf-8", True
    if raw[:2] == b"\xff\xfe":
        return raw[2:].decode("utf-16-le", errors="replace"), "utf-16-le", True
    if raw[:2] == b"\xfe\xff":
        return raw[2:].decode("utf-16-be", errors="replace"), "utf-16-be", True
    try:
        return raw.decode("utf-8"), "utf-8", False
    except UnicodeDecodeError:
        return raw.decode("latin-1"), "latin-1", False


def read_text_meta(filepath: str):

    with open(filepath, "rb") as f:
        raw = f.read()
    text, enc, bom = _decode_bytes(raw)
    eol = _detect_eol(text)
    if eol != "\n":
        text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text, enc, eol, bom


def encode_text_meta(content: str, encoding: str = "utf-8",
                     eol: str = "\n", bom: bool = False) -> bytes:

    text = content.replace("\r\n", "\n").replace("\r", "\n")
    if eol != "\n":
        text = text.replace("\n", eol)
    try:
        data = text.encode(encoding)
    except (LookupError, UnicodeEncodeError):
        # Ada karakter baru di luar jangkauan encoding asli (mis. emoji di file
        # latin-1) — naikkan ke UTF-8 daripada menjatuhkan karakternya.
        encoding, data = "utf-8", text.encode("utf-8")
    if bom:
        if encoding == "utf-8":
            data = b"\xef\xbb\xbf" + data
        elif encoding == "utf-16-le":
            data = b"\xff\xfe" + data
        elif encoding == "utf-16-be":
            data = b"\xfe\xff" + data
    return data


# BRIDGE

class EditorBridge(QObject):


    # Python → JS
    fileLoaded   = Signal(str, str, str, str, bool)  # filepath, content, language, icon_b64, read_only
    fileSaved    = Signal(str)             # filepath
    fileUnsaved  = Signal(str)             # filepath — emitted when content diverges from saved
    fileReloaded = Signal(str, str)        # filepath, content (external change)
    tabClosed    = Signal(str)             # filepath
    editorReady  = Signal()
    viewRequested = Signal()

    # Dipancing saat tab VIRTUAL (mount) di-save via Ctrl+S — Python yang
    # menulis ke image, bukan ke disk. (filepath/tab_id, content)
    virtualSaveRequested = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        # filepath → { content, hash, unsaved, language, read_only, virtual }
        self._tabs   = {}
        self._active = None
        self._ready  = False
        # Antrian emit fileLoaded yang nyangkut karena JS belum connect.
        # Item: (filepath, content, language, icon_b64, read_only)
        self._pending_emits = []
        # tab_id virtual -> callback Python on_save(tab_id, content)
        self._virtual_save_cbs = {}

    # Public API (dipanggil dari Python side)

    @staticmethod
    def _ext_to_icon_key(filepath: str) -> str:
        ext = os.path.splitext(filepath)[1].lower()
        name = os.path.basename(filepath).lower()
        _map = {
            ".py": "icon_py", ".pyw": "icon_py",
            ".js": "icon_js", ".mjs": "icon_js", ".cjs": "icon_js",
            ".ts": "icon_js",
            ".html": "icon_html", ".htm": "icon_html",
            ".css": "icon_css",
            ".qss": "icon_qss",
            ".json": "icon_json", ".jsonc": "icon_json",
            ".xml": "icon_xml", ".xsl": "icon_xml", ".xslt": "icon_xml",
            ".xsd": "icon_xml", ".dtd": "icon_xml", ".rss": "icon_xml", ".atom": "icon_xml",
            ".csv": "icon_csv",
            ".cfg": "icon_cfg", ".conf": "icon_cfg", ".ini": "icon_cfg",
            ".prop": "icon_prop", ".properties": "icon_prop",
            ".rc": "icon_rc",
            ".java": "icon_java",
            ".kt": "icon_kt", ".kts": "icon_kt",
            ".smali": "icon_smali",
            ".c": "icon_c",
            ".cpp": "icon_cpp", ".cc": "icon_cpp", ".cxx": "icon_cpp",
            ".h": "icon_h",
            ".hpp": "icon_hpp", ".hxx": "icon_hpp", ".hh": "icon_hpp",
            ".cs": "icon_cs",
            ".rs": "icon_rust",
            ".go": "icon_json",
            ".php": "icon_php", ".php3": "icon_php", ".php4": "icon_php",
            ".php5": "icon_php", ".phtml": "icon_php",
            ".swift": "icon_swift",
            ".sh": "icon_sh", ".bash": "icon_sh", ".ksh": "icon_sh",
            ".csh": "icon_sh", ".tcsh": "icon_sh", ".dash": "icon_sh",
            ".zsh": "icon_zsh",
            ".fish": "icon_fish",
            ".cob": "icon_cobol", ".cbl": "icon_cobol", ".cobol": "icon_cobol",
            ".asm": "icon_asm", ".s": "icon_asm",
            ".txt": "txt", ".text": "txt", ".log": "txt",
            ".md": "txt", ".markdown": "txt",
        }
        if name in ("makefile", "dockerfile"):
            return "icon_sh"
        return _map.get(ext, "file")

    @staticmethod
    def _resolve_icon_b64(filepath: str) -> str:
        import base64
        try:
            from icons.handler import icon as _icon
            key = EditorBridge._ext_to_icon_key(filepath)
            svg_path = _icon(key)
            print(f"[TAB_ICON] {filepath} → key={key} → {svg_path} exists={os.path.isfile(svg_path) if svg_path else False}")
            if svg_path and os.path.isfile(svg_path):
                with open(svg_path, "rb") as f:
                    data = base64.b64encode(f.read()).decode("ascii")
                mime = "image/svg+xml" if svg_path.endswith(".svg") else "image/png"
                return f"data:{mime};base64," + data
        except Exception as e:
            print(f"[TAB_ICON] ERROR: {e}")
        return ""

    def open_file(self, filepath: str):

        if not os.path.isfile(filepath):
            return False

        icon_b64 = self._resolve_icon_b64(filepath)

        if filepath in self._tabs:
            # Sudah ada — emit supaya JS switch
            tab = self._tabs[filepath]
            self._emit_or_queue_file_loaded(filepath, tab["content"], tab["language"], icon_b64)
            self._active = filepath
            return True

        MAX_EDITOR_SIZE = 300 * 1024 * 1024  # 300 MB (dukung log besar 100-200MB, cegah OOM image 4GB)
        try:
            fsize = os.path.getsize(filepath)
            if fsize > MAX_EDITOR_SIZE:
                from main_ext.class_AppDialog import AppDialog
                AppDialog.warning(
                    self.window(),
                    "File Terlalu Besar",
                    f"File '{os.path.basename(filepath)}' berukuran {fsize / (1024*1024):.1f} MB (batas editor: 300 MB).<br><br>"
                    "File terlalu besar untuk dimuat ke Text Editor agar tidak memicu Out-of-Memory. "
                    "Gunakan Hex Viewer atau viewer eksternal."
                )
                return False
        except Exception:
            pass

        enc, eol, bom = "utf-8", "\n", False
        try:
            content, enc, eol, bom = read_text_meta(filepath)
        except Exception as e:
            content = f"# Error loading: {e}"

        h    = hashlib.md5(content.encode("utf-8", errors="ignore")).hexdigest()
        lang = _detect_language(filepath)

        self._tabs[filepath] = {
            "content" : content,
            "hash"    : h,
            "unsaved" : False,
            "language": lang,
            "read_only": False,
            "virtual" : False,
            "encoding": enc,
            "eol"     : eol,
            "bom"     : bom,
        }
        self._active = filepath
        self._emit_or_queue_file_loaded(filepath, content, lang, icon_b64, False)
        return True

    def bind_neobranch_tab(self, filepath: str, tree_base: str, fs_root: str,
                          source_path: str, branch_name):

        tab = self._tabs.get(filepath)
        if tab is not None and not tab.get("virtual"):
            tab["neobranch"] = {
                "tree_base": os.path.abspath(tree_base),
                "fs_root": fs_root,
                "source_path": os.path.abspath(source_path),
                "branch_name": branch_name,
            }

    def neobranch_tabs(self, tree_base: str, fs_root: str) -> list:
        base = os.path.normcase(os.path.abspath(tree_base))
        return [
            filepath for filepath, tab in self._tabs.items()
            if (ctx := tab.get("neobranch"))
            and os.path.normcase(ctx["tree_base"]) == base
            and ctx["fs_root"] == fs_root
        ]

    def open_virtual(self, tab_id: str, content: str, language: str = None,
                     read_only: bool = False, on_save=None, icon_b64: str = ""):

        if language is None:
            language = _detect_language(tab_id)
        h = hashlib.md5(content.encode("utf-8", errors="ignore")).hexdigest()
        self._tabs[tab_id] = {
            "content" : content,
            "hash"    : h,
            "unsaved" : False,
            "language": language,
            "read_only": bool(read_only),
            "virtual" : True,
        }
        if on_save is not None:
            self._virtual_save_cbs[tab_id] = on_save
        self._active = tab_id
        if not icon_b64:
            icon_b64 = self._resolve_icon_b64(tab_id)
        self._emit_or_queue_file_loaded(tab_id, content, language, icon_b64, bool(read_only))
        return True

    def _emit_or_queue_file_loaded(self, filepath, content, lang, icon_b64, read_only=False):
        # Direct bridge callers (virtual tabs, tab switching) also need the
        # lazy browser, while read-only state queries must remain cheap.
        self.viewRequested.emit()
        # JS connect fileLoaded di dalam callback QWebChannel + butuh Monaco ready.
        # Selama itu, emit langsung akan hilang. Antrikan dulu, flush di on_editor_ready.
        if self._ready:
            self.fileLoaded.emit(filepath, content, lang, icon_b64, read_only)
        else:
            # Drop duplikat untuk filepath sama — pakai versi terakhir.
            self._pending_emits = [p for p in self._pending_emits if p[0] != filepath]
            self._pending_emits.append((filepath, content, lang, icon_b64, read_only))

    def close_file(self, filepath: str):

        if filepath in self._tabs:
            del self._tabs[filepath]
        self.tabClosed.emit(filepath)

    def reload_file(self, filepath: str):

        if not os.path.isfile(filepath):
            return
        try:
            content, enc, eol, bom = read_text_meta(filepath)
        except Exception:
            return
        h = hashlib.md5(content.encode("utf-8", errors="ignore")).hexdigest()
        if filepath in self._tabs:
            self._tabs[filepath]["content"]  = content
            self._tabs[filepath]["hash"]     = h
            self._tabs[filepath]["unsaved"]  = False
            self._tabs[filepath]["encoding"] = enc
            self._tabs[filepath]["eol"]      = eol
            self._tabs[filepath]["bom"]      = bom
        self.fileReloaded.emit(filepath, content)

    def has_unsaved(self) -> bool:
        return any(v["unsaved"] for v in self._tabs.values())

    def get_open_files(self) -> list:
        return list(self._tabs.keys())

    def get_active_file(self):
        return self._active

    def is_unsaved(self, filepath: str) -> bool:
        return self._tabs.get(filepath, {}).get("unsaved", False)

    def tab_count(self) -> int:

        return len(self._tabs)

    # Dipanggil dari JS (lewat QWebChannel)

    @Slot(str, str)
    def on_content_changed(self, filepath: str, content: str):

        if filepath not in self._tabs:
            return
        new_hash = hashlib.md5(content.encode("utf-8", errors="ignore")).hexdigest()
        old_hash = self._tabs[filepath]["hash"]
        was_unsaved = self._tabs[filepath]["unsaved"]
        now_unsaved = (new_hash != old_hash)
        self._tabs[filepath]["content"] = content
        self._tabs[filepath]["unsaved"] = now_unsaved
        if now_unsaved and not was_unsaved:
            self.fileUnsaved.emit(filepath)
        elif not now_unsaved and was_unsaved:
            self.fileSaved.emit(filepath)

    @Slot(str, result=str)
    def get_text_meta(self, filepath: str) -> str:

        tab = self._tabs.get(filepath)
        if not tab or tab.get("virtual"):
            return ""
        label = _EOL_LABEL.get(tab.get("eol", "\n"), "LF")
        enc   = (tab.get("encoding") or "utf-8").upper()
        if tab.get("bom"):
            enc += " BOM"
        return f"{label}  ·  {enc}"

    @Slot(str, str)
    def save_file(self, filepath: str, content: str):

        tab = self._tabs.get(filepath)
        if tab is None:
            return
        if tab and tab.get("read_only"):
            return  # read-only: jangan simpan apa-apa

        if tab and tab.get("virtual"):
            cb = self._virtual_save_cbs.get(filepath)
            ok = True
            if cb is not None:
                try:
                    cb(filepath, content)         # Python tulis ke image
                except Exception as e:
                    ok = False
                    print(f"[WebEditorPanel] virtual save error: {e}")
            self.virtualSaveRequested.emit(filepath, content)
            if ok:
                h = hashlib.md5(content.encode("utf-8", errors="ignore")).hexdigest()
                tab["content"] = content
                tab["hash"]    = h
                tab["unsaved"] = False
                self.fileSaved.emit(filepath)
            return

        ctx = tab.get("neobranch")
        nb = None
        if ctx:
            from main_ext.class_NeoBranchManager import NeoBranchManager
            nb = NeoBranchManager(ctx["tree_base"])
            manifest = os.path.join(nb._source_dir(ctx["fs_root"]), "repack_manifest.json")
            if os.path.isfile(manifest):
                from main_ext.class_AppDialog import AppDialog
                AppDialog.warning(self.parent(), "NeoBranch busy", "Wait for the partition repack to finish.")
                return
            active = nb.get_active_branch(ctx["fs_root"])
            active_name = active["name"] if active else None
            rel = nb._normalize_branch_rel(
                ctx["fs_root"], ctx["branch_name"] or "", ctx["source_path"]
            )
            expected = (
                os.path.join(nb._branch_dir(ctx["fs_root"], ctx["branch_name"]), rel)
                if ctx["branch_name"] and rel else ctx["source_path"]
            )
            if (active_name != ctx["branch_name"] or not rel or
                    os.path.normcase(os.path.abspath(filepath)) !=
                    os.path.normcase(os.path.abspath(expected))):
                from main_ext.class_AppDialog import AppDialog
                AppDialog.warning(
                    self.parent(), "NeoBranch changed",
                    "This tab belongs to a different NeoBranch. Switch back to its branch "
                    "or reopen the file before saving."
                )
                return

        meta = tab
        try:
            data = encode_text_meta(content,
                                    meta.get("encoding", "utf-8"),
                                    meta.get("eol", "\n"),
                                    meta.get("bom", False))
            with open(filepath, "wb") as f:
                f.write(data)
        except Exception as e:
            print(f"[WebEditorPanel] Save error: {e}")
            return

        if ctx and ctx["branch_name"]:
            try:
                nb.record_edit(ctx["fs_root"], ctx["branch_name"], ctx["source_path"])
            except Exception as e:
                from main_ext.class_AppDialog import AppDialog
                AppDialog.warning(self.parent(), "NeoBranch save error", str(e))
                return

        h = hashlib.md5(content.encode("utf-8", errors="ignore")).hexdigest()
        if filepath in self._tabs:
            self._tabs[filepath]["content"] = content
            self._tabs[filepath]["hash"]    = h
            self._tabs[filepath]["unsaved"] = False
        self.fileSaved.emit(filepath)
        print(f"[WebEditorPanel] Saved → {filepath}")


    @Slot(str)
    def on_active_changed(self, filepath: str):

        self._active = filepath

    @Slot(str)
    def on_tab_closed_from_js(self, filepath: str):

        if filepath in self._tabs:
            del self._tabs[filepath]
        self._active = None

    @Slot()
    def on_editor_ready(self):

        self._ready = True
        self.editorReady.emit()
        print("[WebEditorPanel] Monaco ready ✓")
        if self._pending_emits:
            print(f"[WebEditorPanel] Flushing {len(self._pending_emits)} pending fileLoaded")
            pending = self._pending_emits
            self._pending_emits = []
            for fp, content, lang, icon_b64, read_only in pending:
                self.fileLoaded.emit(fp, content, lang, icon_b64, read_only)

    @Slot(str)
    def log(self, msg: str):
        print(f"[JS→Py] {msg}")


# HTML

def _build_html() -> str:
    # Theme-derived colors injected into the HTML/CSS + Monaco theme
    BG          = _th.BG3            # body + editor background
    FG          = _th.FG             # body foreground
    STATUS_BG   = _th.BG             # status bar background
    STATUS_FG   = _th.FG_DIM         # status bar text
    LANG_FG     = _th.accent_hex     # status language label
    SAVE_FG     = _th.GREEN_SAVE     # save flash
    EMPTY_FG    = _th.GRAY_DARK      # empty state text
    BORDER      = _th.border_color(0.05)

    # Monaco theme colors derived from theme
    _is_light   = _th._palette.get("_is_light", False)
    M_BG        = _th.BG3
    M_FG        = _th.FG
    M_LINE_HL   = _th.BG_SELECTED
    M_LINENO    = _th.FG_MUTED      # gutter inaktif: samar tapi tetap kebaca
    M_LINENO_AC = _th.FG            # gutter baris aktif: jelas menonjol
    M_INDENT    = _th.FG_DISABLED   # indent guide: tetap halus (jangan ramai)
    M_INDENT_AC = _th.FG_DIM        # indent guide aktif: sedikit lebih jelas
    M_WIDGET_BG = _th.BG_MODAL
    M_WIDGET_BR = _th.border_color(0.20)
    M_THEME_NAME = "app-light" if _is_light else "app-dark"

    # CSS part (theme-substituted) — uses .format because f-string would need to escape every JS `{` later
    _css = (
        "* { margin:0; padding:0; box-sizing:border-box; }\n"
        "::-webkit-scrollbar { width: 10px; height: 10px; }\n"
        "::-webkit-scrollbar-track { background: transparent; }\n"
        "::-webkit-scrollbar-thumb { background: rgba(127, 127, 127, 0.3); border-radius: 5px; }\n"
        "::-webkit-scrollbar-thumb:hover { background: rgba(127, 127, 127, 0.5); }\n"
        "::-webkit-scrollbar-corner { background: transparent; }\n"
        "body {\n"
        f"    background: {BG};\n"
        f"    color: {FG};\n"
        "    font-family: 'Poppins', 'Inter', 'Segoe UI', 'SF Pro Text', sans-serif;\n"
        "    height: 100vh;\n"
        "    display: flex;\n"
        "    flex-direction: column;\n"
        "    overflow: hidden;\n"
        "}\n"
        "#tab-bar { display: none; }\n"
        "#editor-wrap { flex: 1; position: relative; overflow: hidden; }\n"
        "#monaco-container { width: 100%; height: 100%; }\n"
        ".minimap-slider { opacity: 1 !important; }\n"
        ".minimap-slider-horizontal { opacity: 1 !important; }\n"
        "#empty-state {\n"
        "    position: absolute; inset: 0;\n"
        "    display: flex; flex-direction: column;\n"
        "    align-items: center; justify-content: center;\n"
        "    gap: 10px;\n"
        f"    color: {EMPTY_FG};\n"
        "    pointer-events: none;\n"
        "}\n"
        "#empty-state svg { opacity: 0.25; }\n"
        "#empty-state p { font-size: 11px; letter-spacing: 0.1em; text-transform: uppercase; "
        "font-family: 'Poppins','Inter','Segoe UI',sans-serif; }\n"
        "#status-bar {\n"
        "    height: 20px;\n"
        f"    background: {STATUS_BG};\n"
        f"    border-top: 1px solid {BORDER};\n"
        "    display: flex;\n"
        "    align-items: center;\n"
        "    padding: 0 12px;\n"
        "    gap: 14px;\n"
        "    font-size: 11px;\n"
        "    font-family: 'Poppins', 'Inter', 'Segoe UI', sans-serif;\n"
        f"    color: {STATUS_FG};\n"
        "    flex-shrink: 0;\n"
        "}\n"
        f"#status-lang   {{ color: {LANG_FG}; }}\n"
        "#status-cursor { margin-left: auto; }\n"
        f"#status-eol    {{ color: {LANG_FG}; }}\n"
        f"#save-flash    {{ color: {SAVE_FG}; opacity: 0; transition: opacity 0.3s; }}\n"
        "#save-flash.show { opacity: 1; }\n"
        "#md-toggle {\n"
        "    display: none; cursor: pointer; padding: 2px 8px;\n"
        "    border-radius: 4px; margin-left: 10px; user-select: none;\n"
        "    font-weight: 500; transition: 0.2s;\n"
        f"    background: {STATUS_BG};\n"
        f"    border: 1px solid {BORDER};\n"
        "}\n"
        "#md-toggle:hover { filter: brightness(1.2); }\n"
        "#markdown-preview {\n"
        "    display: none; width: 100%; height: 100%; overflow-y: auto;\n"
        f"    padding: 20px 40px; color: {FG}; font-size: 14px;\n"
        "    line-height: 1.6; font-family: 'Segoe UI', 'Inter', sans-serif;\n"
        "}\n"
        "#markdown-preview h1, #markdown-preview h2, #markdown-preview h3 {\n"
        "    margin-top: 24px; margin-bottom: 16px; font-weight: 600; line-height: 1.25;\n"
        "}\n"
        "#markdown-preview h1, #markdown-preview h2 {\n"
        f"    border-bottom: 1px solid {BORDER}; padding-bottom: 0.3em;\n"
        "}\n"
        f"#markdown-preview a {{ color: {LANG_FG}; text-decoration: none; }}\n"
        "#markdown-preview code {\n"
        "    background: rgba(127,127,127,0.1); padding: 0.2em 0.4em;\n"
        "    border-radius: 3px; font-family: 'JetBrains Mono', Consolas, monospace; font-size: 85%;\n"
        "}\n"
        "#markdown-preview pre {\n"
        "    background: rgba(127,127,127,0.1); padding: 16px; border-radius: 6px; overflow: auto;\n"
        "}\n"
        "#markdown-preview pre code { background: transparent; padding: 0; }\n"
        "#markdown-preview blockquote {\n"
        f"    padding: 0 1em; color: {STATUS_FG}; border-left: 0.25em solid {BORDER}; margin: 0;\n"
        "}\n"
        "#markdown-preview ul, #markdown-preview ol { padding-left: 2em; margin-bottom: 16px; }\n"
        "#markdown-preview table { border-collapse: collapse; margin: 16px 0; width: 100%; }\n"
        f"#markdown-preview th, #markdown-preview td {{ border: 1px solid {BORDER}; padding: 6px 13px; }}\n"
        f"#markdown-preview tr:nth-child(2n) {{ background: rgba(127,127,127,0.05); }}\n"
    )

    try:
        import os
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        marked_path = os.path.join(base_dir, "core", "marked.min.js")
        with open(marked_path, "r", encoding="utf-8") as f:
            _script_marked = f"<script>{f.read()}</script>"
    except Exception as e:
        print(f"[WebEditorPanel] Failed to bundle marked.js: {e}")
        _script_marked = "<script>console.error('marked.js not bundled');</script>"

    html = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Editor</title>
<style>
""" + _css + r"""
</style>
</head>
<body>

<div id="tab-bar"></div>

<div id="editor-wrap">
  <div id="empty-state">
    <svg width="40" height="40" viewBox="0 0 24 24" fill="none"
         stroke="currentColor" stroke-width="1.2">
      <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/>
      <polyline points="14 2 14 8 20 8"/>
      <line x1="16" y1="13" x2="8" y2="13"/>
      <line x1="16" y1="17" x2="8" y2="17"/>
    </svg>
    <p>Pilih file dari tree</p>
  </div>
  <div id="monaco-container"></div>
  <div id="markdown-preview"></div>
</div>

<div id="status-bar">
  <span id="status-fp">—</span>
  <span id="status-lang">plaintext</span>
  <span id="md-toggle">👁️ View</span>
  <span id="save-flash">✓ Saved</span>
  <span id="status-cursor">Ln 1, Col 1</span>
  <span id="status-eol"></span>
</div>

<script src="qrc:///qtwebchannel/qwebchannel.js"></script>
""" + _script_marked + r"""
<script src="__MONACO_LOADER_SRC__"></script>

<script>
// ── State ─────────────────────────────────────────────────────────────
const tabs   = {};    // filepath → { model, unsaved, lang, mdMode }
let active   = null;
let editor   = null;
let bridge   = null;
let monacoReady = false;
let pendingOpen = [];   // antrian kalau Monaco belum ready

// ── QWebChannel init ──────────────────────────────────────────────────
new QWebChannel(qt.webChannelTransport, ch => {
    bridge = ch.objects.bridge;

    bridge.fileLoaded.connect((fp, content, lang, icon, readonly) => {
        if (monacoReady) openTab(fp, content, lang, icon, readonly);
        else pendingOpen.push({ fp, content, lang, icon, readonly });
    });

    bridge.fileReloaded.connect((fp, content) => {
        if (tabs[fp]) {
            tabs[fp].model.setValue(content);
            tabs[fp].unsaved = false;
            renderTabBar();
        }
    });

    bridge.tabClosed.connect(fp => closeTab(fp, true));
});

// ── Monaco init ────────────────────────────────────────────────────────
require.config({
    paths: { vs: __MONACO_VS_PATH__ }
});

require(['vs/editor/editor.main'], () => {

    const _sharedColors = {
        'editor.background'                   : '__M_BG__',
        'editor.foreground'                   : '__M_FG__',
        'editor.lineHighlightBackground'      : '__M_LINE_HL__',
        'editor.lineHighlightBorder'          : '#00000000',
        'editor.selectionBackground'          : '__M_SEL_BG__',
        'editor.inactiveSelectionBackground'  : '__M_SEL_BG_INACT__',
        'editor.wordHighlightBackground'      : '__M_SEL_BG__',
        'editor.wordHighlightStrongBackground': '__M_SEL_BG_INACT__',
        'editorCursor.foreground'             : '__M_FG__',
        'editorLineNumber.foreground'         : '__M_LINENO__',
        'editorLineNumber.activeForeground'   : '__M_LINENO_AC__',
        'editorIndentGuide.background1'       : '__M_INDENT__',
        'editorIndentGuide.activeBackground1' : '__M_INDENT_AC__',
        'scrollbarSlider.background'          : '__M_SCROLL__',
        'scrollbarSlider.hoverBackground'     : '__M_SCROLL_H__',
        'scrollbarSlider.activeBackground'    : '__M_SCROLL_A__',
        'editorWidget.background'             : '__M_WIDGET_BG__',
        'editorWidget.border'                 : '__M_WIDGET_BR__',
        'editorSuggestWidget.background'      : '__M_WIDGET_BG__',
        'editorSuggestWidget.border'          : '__M_WIDGET_BR__',
        'editorSuggestWidget.selectedBackground': '__M_SEL_BG__',
    };

    // ── Dark token palette (One Dark-ish) ──────────────────────────────────
    monaco.editor.defineTheme('app-dark', {
        base: 'vs-dark', inherit: true,
        rules: [
            { token: 'comment',          foreground: '6a737d', fontStyle: 'italic' },
            { token: 'keyword',          foreground: 'c792ea' },
            { token: 'keyword.operator', foreground: '89ddff' },
            { token: 'string',           foreground: '98c379' },
            { token: 'string.escape',    foreground: '56b6c2' },
            { token: 'number',           foreground: 'd19a66' },
            { token: 'type',             foreground: 'e5c07b' },
            { token: 'type.identifier',  foreground: 'e5c07b' },
            { token: 'function',         foreground: '61afef' },
            { token: 'variable',         foreground: 'e06c75' },
            { token: 'tag',              foreground: 'e06c75' },
            { token: 'attribute.name',   foreground: 'd19a66' },
            { token: 'attribute.value',  foreground: '98c379' },
            { token: 'delimiter',        foreground: '89ddff' },
            { token: 'constant',         foreground: 'd19a66' },
        ],
        colors: Object.assign({}, _sharedColors, {
            'editorFindMatch.background'         : '#3d4a2e',
            'editorFindMatchHighlight.background': '#2d3826',
        })
    });

    // ── Light token palette (GitHub Light-ish) ─────────────────────────────
    monaco.editor.defineTheme('app-light', {
        base: 'vs', inherit: true,
        rules: [
            { token: 'comment',          foreground: '6e7781', fontStyle: 'italic' },
            { token: 'keyword',          foreground: 'cf222e' },
            { token: 'keyword.operator', foreground: '0550ae' },
            { token: 'string',           foreground: '0a3069' },
            { token: 'string.escape',    foreground: '116329' },
            { token: 'number',           foreground: '0550ae' },
            { token: 'type',             foreground: '953800' },
            { token: 'type.identifier',  foreground: '953800' },
            { token: 'function',         foreground: '6639ba' },
            { token: 'variable',         foreground: 'e36209' },
            { token: 'tag',              foreground: '116329' },
            { token: 'attribute.name',   foreground: '0550ae' },
            { token: 'attribute.value',  foreground: '0a3069' },
            { token: 'delimiter',        foreground: '24292f' },
            { token: 'constant',         foreground: '0550ae' },
        ],
        colors: Object.assign({}, _sharedColors, {
            'editorFindMatch.background'         : '#fff8c5',
            'editorFindMatchHighlight.background': '#fae17d80',
        })
    });

    const _activeTheme = '__M_THEME_NAME__';

    editor = monaco.editor.create(document.getElementById('monaco-container'), {
        theme           : _activeTheme,
        automaticLayout : true,
        fontSize        : 12,
        fontFamily      : "'JetBrains Mono','Cascadia Code','Fira Code',Consolas,'Courier New',monospace",
        fontLigatures   : true,
        lineHeight      : 22,
        letterSpacing   : 0.3,
        padding         : { top: 12, bottom: 12 },
        minimap         : { enabled: true, scale: 1, renderCharacters: false, showSlider: 'always' },
        overviewRulerLanes: 3,
        overviewRulerBorder: false,
        scrollBeyondLastLine: false,
        smoothScrolling : true,
        cursorBlinking  : 'smooth',
        cursorSmoothCaretAnimation: 'on',
        renderWhitespace: 'none',
        bracketPairColorization: { enabled: true },
        guides          : { bracketPairs: true, indentation: true },
        tabSize         : 4,
        insertSpaces    : true,
        wordWrap        : 'off',
        // Selection highlight tidak terlalu blazing
        occurrencesHighlight: 'singleFile',
        selectionHighlight: true,
        scrollbar: {
            vertical               : 'auto',
            horizontal             : 'auto',
            verticalScrollbarSize  : 6,
            horizontalScrollbarSize: 6,
            useShadows             : false,
        }
    });

    // Ctrl+S → save (read-only tab diabaikan)
    editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => {
        if (!active || !bridge) return;
        if (tabs[active] && tabs[active].readonly) return;
        bridge.save_file(active, editor.getValue());
    });

    // Cursor → status bar
    editor.onDidChangeCursorPosition(e => {
        document.getElementById('status-cursor').textContent =
            `Ln ${e.position.lineNumber}, Col ${e.position.column}`;
    });

    // Content change → notify Python
    editor.onDidChangeModelContent(() => {
        if (!active || !tabs[active]) return;
        const content = editor.getValue();
        bridge && bridge.on_content_changed(active, content);
        tabs[active].unsaved = true;
        renderTabBar();
        
        // Auto-update markdown preview if in view mode
        if (tabs[active].lang === 'markdown' && tabs[active].mdMode === 'view' && typeof marked !== 'undefined') {
            document.getElementById('markdown-preview').innerHTML = marked.parse(content);
        }
    });

    // Toggle button click
    document.getElementById('md-toggle').onclick = () => {
        if (!active || !tabs[active] || tabs[active].lang !== 'markdown') return;
        tabs[active].mdMode = tabs[active].mdMode === 'view' ? 'edit' : 'view';
        applyMdMode();
    };

    monacoReady = true;

    // Flush pending
    for (const p of pendingOpen) openTab(p.fp, p.content, p.lang, p.icon, p.readonly);
    pendingOpen = [];

    // Notify Python
    bridge && bridge.on_editor_ready();
});

// ── Tab management ──────────────────────────────────────────────────────
function openTab(fp, content, lang, icon, readonly) {
    if (!tabs[fp]) {
        const model = monaco.editor.createModel(
            content, lang, monaco.Uri.parse('file:///' + fp.replace(/\\/g, '/'))
        );
        tabs[fp] = { model, unsaved: false, lang, icon: icon || '', readonly: !!readonly, mdMode: (lang === 'markdown') ? 'view' : 'edit' };
    } else {
        if (tabs[fp].model.getValue() !== content) {
            tabs[fp].model.setValue(content);
            tabs[fp].unsaved = false;
        }
        if (icon) tabs[fp].icon = icon;
        tabs[fp].readonly = !!readonly;
    }
    switchTab(fp);
}

function applyMdMode() {
    if (!active || !tabs[active]) return;
    const isMd = tabs[active].lang === 'markdown';
    const toggleBtn = document.getElementById('md-toggle');
    const monacoCont = document.getElementById('monaco-container');
    const mdCont = document.getElementById('markdown-preview');
    
    if (isMd) {
        toggleBtn.style.display = 'inline-block';
        if (tabs[active].mdMode === 'view') {
            toggleBtn.textContent = '✏️ Edit';
            monacoCont.style.display = 'none';
            mdCont.style.display = 'block';
            if (typeof marked !== 'undefined') {
                mdCont.innerHTML = marked.parse(editor.getValue());
            } else {
                mdCont.innerHTML = "<p>Loading markdown engine...</p>";
                setTimeout(applyMdMode, 200);
            }
        } else {
            toggleBtn.textContent = '👁️ View';
            monacoCont.style.display = 'block';
            mdCont.style.display = 'none';
            if (editor) editor.layout();
            editor.focus();
        }
    } else {
        toggleBtn.style.display = 'none';
        monacoCont.style.display = 'block';
        mdCont.style.display = 'none';
        if (editor) editor.layout();
        editor.focus();
    }
}

function switchTab(fp) {
    if (!tabs[fp] || !editor) return;
    active = fp;
    bridge && bridge.on_active_changed(fp);

    editor.setModel(tabs[fp].model);
    monaco.editor.setModelLanguage(tabs[fp].model, tabs[fp].lang);
    // Monaco single-editor: read-only di-set per tab aktif.
    editor.updateOptions({ readOnly: !!tabs[fp].readonly });

    const name = fp.split(/[\\/]/).pop();
    document.getElementById('status-fp').textContent = name;
    document.getElementById('status-lang').textContent =
        tabs[fp].lang + (tabs[fp].readonly ? '  ·  read-only' : '');
    const eolEl = document.getElementById('status-eol');
    eolEl.textContent = '';
    bridge && bridge.get_text_meta(fp, m => { if (active === fp) eolEl.textContent = m; });
    document.getElementById('empty-state').style.display = 'none';

    renderTabBar();
    applyMdMode();
}

function closeTab(fp, fromPython = false) {
    if (!tabs[fp]) return;

    // Kalau unsaved dan bukan dari Python (Python udah handle confirm)
    // Untuk close dari JS (klik X), bridge Python yang handle confirm via on_tab_closed_from_js
    tabs[fp].model.dispose();
    delete tabs[fp];

    if (active === fp) {
        const remaining = Object.keys(tabs);
        if (remaining.length > 0) {
            switchTab(remaining[remaining.length - 1]);
        } else {
            active = null;
            editor && editor.setModel(null);
            document.getElementById('empty-state').style.display = 'flex';
            document.getElementById('status-fp').textContent = '—';
            document.getElementById('status-lang').textContent = 'plaintext';
            document.getElementById('md-toggle').style.display = 'none';
            document.getElementById('markdown-preview').style.display = 'none';
            document.getElementById('monaco-container').style.display = 'block';
        }
    }

    if (!fromPython) bridge && bridge.on_tab_closed_from_js(fp);
    renderTabBar();
}

function renderTabBar() {
    const bar = document.getElementById('tab-bar');
    bar.innerHTML = '';
    for (const [fp, state] of Object.entries(tabs)) {
        const name = fp.split(/[\\/]/).pop();
        const div = document.createElement('div');
        div.className = 'tab' +
            (fp === active  ? ' active'  : '') +
            (state.unsaved  ? ' unsaved' : '');

        if (state.icon) {
            const img = document.createElement('img');
            img.src = state.icon;
            img.style.cssText = 'width:14px;height:14px;margin-right:5px;vertical-align:middle;flex-shrink:0;';
            div.appendChild(img);
        }

        const label = document.createElement('span');
        label.textContent = name;

        const x = document.createElement('span');
        x.className = 'tab-close';
        x.innerHTML = '×';
        x.onclick = e => { e.stopPropagation(); closeTab(fp); };

        div.appendChild(label);
        div.appendChild(x);
        div.onclick = () => switchTab(fp);
        bar.appendChild(div);
    }
    const el = bar.querySelector('.tab.active');
    if (el) el.scrollIntoView({ block: 'nearest', inline: 'nearest' });
}

// Tampilkan konfirmasi save dari Python
window.addEventListener('bridge-saved', e => {
    const fl = document.getElementById('save-flash');
    fl.classList.add('show');
    setTimeout(() => fl.classList.remove('show'), 1500);
});
</script>

</body>
</html>"""


    _border_hex = M_WIDGET_BR if M_WIDGET_BR.startswith("#") else "#333646"

    _subs = {
        "__M_BG__"           : M_BG,
        "__M_FG__"           : M_FG,
        "__M_LINE_HL__"      : M_LINE_HL,
        "__M_LINENO__"       : M_LINENO,
        "__M_LINENO_AC__"    : M_LINENO_AC,
        "__M_INDENT__"       : M_INDENT,
        "__M_INDENT_AC__"    : M_INDENT_AC,
        "__M_WIDGET_BG__"    : M_WIDGET_BG,
        "__M_WIDGET_BR__"    : _border_hex,
        "__M_SEL_BG__"       : _th.accent_hex + "30",
        "__M_SEL_BG_INACT__" : _th.accent_hex + "20",
        "__M_SCROLL__"       : _th.SCROLL_HANDLE + "80",
        "__M_SCROLL_H__"     : _th.SCROLL_HANDLE + "99",
        "__M_SCROLL_A__"     : _th.SCROLL_HANDLE + "cc",
        "__M_THEME_NAME__"   : M_THEME_NAME,
        "__MONACO_LOADER_SRC__": escape_html(f"{monaco_vs_url()}/loader.js", quote=True),
        "__MONACO_VS_PATH__"  : json.dumps(monaco_vs_url()),
    }
    for k, v in _subs.items():
        html = html.replace(k, v)

    return html


# MAIN WIDGET

class WebEditorPanel(QWidget):


    fileSaved  = Signal(str)
    tabOpened  = Signal(str)
    allClosed  = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)

        # Cegah background flash putih saat show/hide
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Bridge
        self.bridge = EditorBridge(self)
        self.bridge.fileSaved.connect(self._on_saved)
        self.bridge.fileLoaded.connect(lambda fp, *_: self.tabOpened.emit(fp))
        self.bridge.viewRequested.connect(self._ensure_view)
        self.bridge.editorReady.connect(lambda: print("[WebEditorPanel] Ready"))
        self._view = None
        self.channel = None
        self._view_layout = layout

    @property
    def view(self):

        return self._ensure_view()

    def view_initialized(self):
        return self._view is not None

    def is_ready(self) -> bool:
        return bool(getattr(self.bridge, "_ready", False))

    def prepare(self):

        self._ensure_view()

    def _ensure_view(self):
        if self._view is not None:
            return self._view

        # WebView
        self._view = QWebEngineView(self)

        # Cegah background putih / flicker saat load dan resize
        self.view.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.view.setStyleSheet(f"background: {_th.BG3};")
        # Set page background color sebelum load HTML — eliminasi flash putih
        self.view.page().setBackgroundColor(QColor(_th.BG3))

        s = self.view.settings()
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled, True)

        # Channel
        self.channel = QWebChannel(self.view.page())
        self.channel.registerObject("bridge", self.bridge)
        self.view.page().setWebChannel(self.channel)

        self.bridge._ready = False
        base_dir = str(resolve_monaco_vs().parent) + os.sep
        self.view.setHtml(_build_html(), QUrl.fromLocalFile(base_dir))
        self._view_layout.addWidget(self.view)
        return self._view

    # Public API

    def open_file(self, filepath: str) -> bool:

        return self.bridge.open_file(filepath)

    def close_file(self, filepath: str):

        self.bridge.close_file(filepath)
        if self.bridge.tab_count() == 0:
            self.allClosed.emit()

    def save_current(self):

        fp = self.bridge.get_active_file()
        if fp and fp in self.bridge._tabs:
            content = self.bridge._tabs[fp]["content"]
            self.bridge.save_file(fp, content)

    def tab_count(self) -> int:

        return self.bridge.tab_count()

    def has_unsaved(self) -> bool:
        return self.bridge.has_unsaved()

    def is_unsaved(self, filepath: str) -> bool:
        return self.bridge.is_unsaved(filepath)

    def get_open_files(self) -> list:
        return self.bridge.get_open_files()

    def get_active_file(self):
        return self.bridge.get_active_file()

    # Callbacks

    def _on_saved(self, filepath: str):
        self.fileSaved.emit(filepath)
        # Trigger JS flash via evaluate
        self.view.page().runJavaScript(
            "window.dispatchEvent(new Event('bridge-saved'));"
        )
