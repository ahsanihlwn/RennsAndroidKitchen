# SPDX-License-Identifier: MPL-2.0
"""Editor documents and WebChannel protocol shared by QWidget and Qt Quick views."""
import os
import json
import uuid
import hashlib
from PySide6.QtCore import QObject, Slot, Signal, Property


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
    warningRaised = Signal(str, str)

    # Dipancing saat tab VIRTUAL (mount) di-save via Ctrl+S — Python yang
    # menulis ke image, bukan ke disk. (filepath/tab_id, content)
    virtualSaveRequested = Signal(str, str)
    documentChanged = Signal(str)
    documentSaved = Signal(str)
    documentSaveFailed = Signal(str,str)
    selectionChanged = Signal(str, object)
    navigateRequested = Signal(str, int, int, int, int)
    agentEditRequested = Signal(str, str, str, str)
    agentEditFinished = Signal(str, str, bool, str)
    sendToAgentRequested = Signal()
    agentAvailableChanged = Signal(bool)

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
        self._selections = {}
        self._pending_navigation = None
        self._pending_agent_edits = []
        self._save_failures = {}
        self._agent_available = False

    @Property(bool, notify=agentAvailableChanged)
    def agentAvailable(self):return self._agent_available

    def set_agent_available(self,available):
        if self._agent_available!=bool(available):
            self._agent_available=bool(available);self.agentAvailableChanged.emit(self._agent_available)

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
                self.warningRaised.emit(
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
        self._tabs[filepath].setdefault('revision', uuid.uuid4().hex)
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
        self._selections.pop(filepath,None)
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
            self._tabs[filepath]['revision'] = uuid.uuid4().hex
            self.documentChanged.emit(filepath)
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
        if self._tabs[filepath].get('read_only') or self._tabs[filepath]['content']==content:return
        new_hash = hashlib.md5(content.encode("utf-8", errors="ignore")).hexdigest()
        old_hash = self._tabs[filepath]["hash"]
        was_unsaved = self._tabs[filepath]["unsaved"]
        now_unsaved = (new_hash != old_hash)
        self._tabs[filepath]["content"] = content
        self._tabs[filepath]["unsaved"] = now_unsaved
        self._tabs[filepath]['revision'] = uuid.uuid4().hex
        self.documentChanged.emit(filepath)
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
        self._save_failures.pop(filepath,None)
        tab = self._tabs.get(filepath)
        if tab is None:
            self.documentSaveFailed.emit(filepath,'The document was closed before saving')
            return
        if tab and tab.get("read_only"):
            self.documentSaveFailed.emit(filepath,'This document is read-only')
            return  # read-only: jangan simpan apa-apa
        self.on_content_changed(filepath,content)

        if tab and tab.get("virtual"):
            cb = self._virtual_save_cbs.get(filepath)
            ok = True
            if cb is not None:
                try:
                    # A native image save may finish asynchronously. False
                    # leaves the buffer dirty until its worker acknowledges it.
                    deferred = cb(filepath, content) is False
                    if deferred:
                        self.virtualSaveRequested.emit(filepath, content)
                        return
                except Exception as e:
                    ok = False
                    print(f"[WebEditorPanel] virtual save error: {e}")
                    self._save_failure(filepath,e)
            self.virtualSaveRequested.emit(filepath, content)
            if ok:
                h = hashlib.md5(content.encode("utf-8", errors="ignore")).hexdigest()
                tab["content"] = content
                tab["hash"]    = h
                tab["unsaved"] = False
                self.fileSaved.emit(filepath)
                self.documentSaved.emit(filepath)
            return

        ctx = tab.get("neobranch")
        nb = None
        if ctx:
            from main_ext.class_NeoBranchManager import NeoBranchManager
            nb = NeoBranchManager(ctx["tree_base"])
            manifest = os.path.join(nb._source_dir(ctx["fs_root"]), "repack_manifest.json")
            if os.path.isfile(manifest) or os.path.isfile(os.path.join(nb._nb_root(ctx['fs_root']),'checkout','journal.json')):
                self.documentSaveFailed.emit(filepath,'Wait for the partition repack to finish')
                self.warningRaised.emit("NeoBranch busy", "Wait for the partition repack to finish.")
                return
            active = nb.get_active_branch(ctx["fs_root"])
            active_name = active["name"] if active else None
            rel = nb._normalize_branch_rel(
                ctx["fs_root"], ctx["branch_name"] or "", ctx["source_path"]
            )
            from main_ext.class_NeoBranchManager import CONFIG_SUFFIXES
            metadata = nb.is_physical_checkout(ctx['fs_root']) and os.path.normcase(os.path.abspath(ctx['source_path'])) in {
                os.path.normcase(os.path.abspath(os.path.join(ctx['tree_base'],'config',ctx['fs_root']+suffix))) for suffix in CONFIG_SUFFIXES
            }
            if metadata:rel=os.path.relpath(ctx['source_path'],ctx['tree_base'])
            expected = (
                os.path.join(nb._branch_dir(ctx["fs_root"], ctx["branch_name"]), rel)
                if ctx["branch_name"] and rel and not nb.is_physical_checkout(ctx['fs_root']) else ctx["source_path"]
            )
            if (active_name != ctx["branch_name"] or not rel or
                    os.path.normcase(os.path.abspath(filepath)) !=
                    os.path.normcase(os.path.abspath(expected))):
                self.documentSaveFailed.emit(filepath,'The active NeoBranch changed before saving')
                self.warningRaised.emit(
                    "NeoBranch changed",
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
            self._save_failure(filepath,e)
            return

        if ctx and ctx["branch_name"] and not metadata:
            try:
                nb.record_edit(ctx["fs_root"], ctx["branch_name"], ctx["source_path"])
            except Exception as e:
                self.warningRaised.emit("NeoBranch save error", str(e))
                self._save_failure(filepath,e)
                return

        h = hashlib.md5(content.encode("utf-8", errors="ignore")).hexdigest()
        if filepath in self._tabs:
            self._tabs[filepath]["content"] = content
            self._tabs[filepath]["hash"]    = h
            self._tabs[filepath]["unsaved"] = False
        self.fileSaved.emit(filepath)
        self.documentSaved.emit(filepath)
        print(f"[WebEditorPanel] Saved → {filepath}")

    def _save_failure(self,filepath,error):
        from core.command_context import failure_from_exception
        from core.error_reporting import report_fatal
        failure=failure_from_exception(error)
        self._save_failures[filepath]=failure
        self.documentSaveFailed.emit(filepath,str(failure))
        report_fatal('Save '+filepath+'\n\n'+str(failure),application_error=failure.application_error)

    def save_failure(self,filepath):return self._save_failures.get(filepath)


    @Slot(str)
    def on_active_changed(self, filepath: str):

        self._active = filepath

    @Slot(str)
    def on_tab_closed_from_js(self, filepath: str):

        if filepath in self._tabs:
            del self._tabs[filepath]
        self._active = None
        self._selections.pop(filepath,None)

    @Slot(str,str)
    def on_selection_changed(self,filepath,payload):
        if filepath not in self._tabs:return
        try:
            data=json.loads(payload)
            names=('startLineNumber','startColumn','endLineNumber','endColumn')
            position={name:max(1,int(data[name])) for name in names}
            position['text']=str(data.get('text',''))[:8192]
        except (ValueError,KeyError,TypeError):return
        if self._selections.get(filepath)!=position:
            self._selections[filepath]=position
            self.selectionChanged.emit(filepath,position)

    @Slot()
    def request_agent(self):
        if self._agent_available:self.sendToAgentRequested.emit()

    def request_navigation(self,path,line,column,end_line,end_column):
        if self._ready:self.navigateRequested.emit(path,line,column,end_line,end_column)
        else:self._pending_navigation=(path,line,column,end_line,end_column)

    def request_agent_edit(self,key,path,before,after):
        if self._ready:self.agentEditRequested.emit(key,path,before,after)
        else:self._pending_agent_edits.append((key,path,before,after))

    @Slot(str,str,bool,str)
    def on_agent_edit_finished(self,key,path,ok,reason):
        self.agentEditFinished.emit(key,path,ok,reason)

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
        if self._pending_navigation:
            self.navigateRequested.emit(*self._pending_navigation);self._pending_navigation=None
        for edit in self._pending_agent_edits:self.agentEditRequested.emit(*edit)
        self._pending_agent_edits=[]

    @Slot(str)
    def log(self, msg: str):
        print(f"[JS→Py] {msg}")


