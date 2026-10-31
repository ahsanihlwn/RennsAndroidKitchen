# SPDX-License-Identifier: MPL-2.0
"""The Monaco presentation shared by both Qt frontends."""
import os
import json
from html import escape as escape_html
from main_ext.class_ThemeUi import theme as _th
from core.monaco_assets import monaco_vs_url


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
        "html:not(.rak-scroll-hints) ::-webkit-scrollbar-thumb:not(:hover) { background: transparent; }\n"
        ".monaco-scrollable-element > .scrollbar > .slider { opacity: 0; }\n"
        ".rak-scroll-hints .monaco-scrollable-element > .scrollbar { opacity: 1 !important; }\n"
        ".rak-scroll-hints .monaco-scrollable-element > .scrollbar > .slider, .monaco-scrollable-element > .scrollbar:hover > .slider, .monaco-scrollable-element > .scrollbar:active > .slider { opacity: 1; }\n"
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
let pendingAgentActions = [];
let agentChatAction = null;
function syncAgentChatAction() {
    if (agentChatAction) {agentChatAction.dispose();agentChatAction=null;}
    if (editor && bridge && bridge.agentAvailable) {
        agentChatAction=editor.addAction({id:'rak.sendToAgent',label:'Send to agent',contextMenuGroupId:'navigation',contextMenuOrder:0.5,
            run:()=>{if(active && bridge)bridge.request_agent();}});
    }
}

function navigateAgent(fp, line, column, endLine, endColumn) {
    if (!monacoReady) { pendingAgentActions.push(() => navigateAgent(fp,line,column,endLine,endColumn)); return; }
    if (!tabs[fp]) return;
    switchTab(fp);
    const range=tabs[fp].model.validateRange(new monaco.Range(line,column,endLine,endColumn));
    editor.setSelection(range); editor.revealRangeInCenter(range); editor.focus();
}
function applyAgentEdit(id,fp,before,after) {
    if (!monacoReady) { pendingAgentActions.push(() => applyAgentEdit(id,fp,before,after)); return; }
    const tab=tabs[fp];
    if (!tab || tab.readonly || tab.model.getValue()!==before) {
        bridge.on_agent_edit_finished(id,fp,false,'Document changed or is no longer editable. Read the current buffer.');return;
    }
    const model=tab.model;
    model.pushStackElement();
    model.pushEditOperations([], [{range:model.getFullModelRange(),text:after}], () => null);
    model.pushStackElement();
    tab.unsaved=true;renderTabBar();
    bridge.on_content_changed(fp,model.getValue());
    bridge.on_agent_edit_finished(id,fp,true,'');
}

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
    bridge.navigateRequested.connect(navigateAgent);
    bridge.agentEditRequested.connect(applyAgentEdit);
    bridge.agentAvailableChanged.connect(syncAgentChatAction);
    syncAgentChatAction();
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

    let scrollHintTimer;
    function revealScrollHints() {
        document.documentElement.classList.add('rak-scroll-hints');
        clearTimeout(scrollHintTimer);
        scrollHintTimer=setTimeout(() => document.documentElement.classList.remove('rak-scroll-hints'),1000);
    }
    document.addEventListener('scroll',revealScrollHints,true);

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

    // Expose ke window agar runJavaScript injection dari Qt bisa menemukannya
    window.editor = editor;

    editor.onDidScrollChange(revealScrollHints);

    // Ctrl+S → save (read-only tab diabaikan)
    syncAgentChatAction();
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
    editor.onDidChangeCursorSelection(e => {
        if (!active || !bridge || !tabs[active]) return;
        const selection=e.selection;
        bridge.on_selection_changed(active,JSON.stringify({
            startLineNumber:selection.startLineNumber,startColumn:selection.startColumn,
            endLineNumber:selection.endLineNumber,endColumn:selection.endColumn,
            text:tabs[active].model.getValueInRange(selection).slice(0,8192)
        }));
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
    const actions=pendingAgentActions;pendingAgentActions=[];for (const action of actions) action();

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

