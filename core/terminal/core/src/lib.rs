//! termcore: PTY + VT emulator (alacritty_terminal) with a C ABI for a Qt Quick front end.
mod keys;
mod theme;

use std::ffi::{c_char, c_void, CStr, CString};
use std::io::{Read, Write};
use std::sync::atomic::{AtomicBool, AtomicU16, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use alacritty_terminal::event::{Event, EventListener, WindowSize};
use alacritty_terminal::grid::{Dimensions, Scroll};
use alacritty_terminal::index::{Column, Line, Point, Side};
use alacritty_terminal::selection::{Selection, SelectionType};
use alacritty_terminal::term::cell::Flags;
use alacritty_terminal::term::{Config, Osc52, Term, TermMode};
use alacritty_terminal::vte::ansi::{CursorShape, Processor};
use portable_pty::{native_pty_system, CommandBuilder, MasterPty, PtySize};
use regex::Regex;

use theme::{pack, unpack, Theme};

// ---- event kinds passed to the C callback ----
pub const EV_WAKEUP: i32 = 0;
pub const EV_TITLE: i32 = 1;
pub const EV_BELL: i32 = 2;
pub const EV_CLIPBOARD: i32 = 3;
pub const EV_EXIT: i32 = 4;

// ---- cell flags (stable ABI, mirrored in terminal_item.cpp) ----
pub const CF_BOLD: u32 = 1;
pub const CF_ITALIC: u32 = 2;
pub const CF_UL_SINGLE: u32 = 4;
pub const CF_UL_DOUBLE: u32 = 8;
pub const CF_UL_CURLY: u32 = 16;
pub const CF_UL_DOTTED: u32 = 32;
pub const CF_UL_DASHED: u32 = 64;
pub const CF_STRIKE: u32 = 128;
pub const CF_WIDE: u32 = 256;
pub const CF_SPACER: u32 = 512;
pub const CF_SELECTED: u32 = 1024;
pub const CF_HAS_EXTRA: u32 = 2048;
pub const CF_LINK: u32 = 4096;

// ---- mode bits reported to the UI ----
pub const MODE_MOUSE: u32 = 1;
pub const MODE_ALT_SCREEN: u32 = 2;
pub const MODE_APP_CURSOR: u32 = 4;
pub const MODE_BRACKETED_PASTE: u32 = 8;
pub const MODE_FOCUS_REPORT: u32 = 16;

type Callback = extern "C" fn(*mut c_void, i32);

struct Shared {
    cb: Mutex<Option<(Callback, usize)>>,
    title: Mutex<String>,
    clipboard: Mutex<Option<String>>,
    cell_w: AtomicU16,
    cell_h: AtomicU16,
    alive: AtomicBool,
    exit_code: Mutex<i32>,
}

impl Shared {
    fn notify(&self, kind: i32) {
        let cb = {
            let g = self.cb.lock().unwrap();
            *g
        };
        if let Some((f, u)) = cb {
            f(u as *mut c_void, kind);
        }
    }
}

type Writer = Arc<Mutex<Box<dyn Write + Send>>>;

#[derive(Clone)]
struct Proxy {
    shared: Arc<Shared>,
    writer: Writer,
    theme: Arc<Mutex<Theme>>,
    size: Arc<Mutex<(u16, u16)>>, // cols, rows
}

impl Proxy {
    fn write(&self, s: &str) {
        let mut w = self.writer.lock().unwrap();
        let _ = w.write_all(s.as_bytes());
        let _ = w.flush();
    }
}

impl EventListener for Proxy {
    fn send_event(&self, event: Event) {
        match event {
            Event::PtyWrite(s) => self.write(&s),
            Event::Title(t) => {
                *self.shared.title.lock().unwrap() = t;
                self.shared.notify(EV_TITLE);
            }
            Event::ResetTitle => {
                self.shared.title.lock().unwrap().clear();
                self.shared.notify(EV_TITLE);
            }
            Event::Bell => self.shared.notify(EV_BELL),
            Event::ClipboardStore(_, text) => {
                *self.shared.clipboard.lock().unwrap() = Some(text);
                self.shared.notify(EV_CLIPBOARD);
            }
            Event::ColorRequest(idx, f) => {
                let t = self.theme.lock().unwrap();
                let c = match idx {
                    256 => t.fg,
                    257 => t.bg,
                    258 => t.cursor,
                    i if i < 256 => t.indexed(i),
                    _ => t.fg,
                };
                drop(t);
                self.write(&f(c));
            }
            Event::TextAreaSizeRequest(f) => {
                let (cols, rows) = *self.size.lock().unwrap();
                let ws = WindowSize {
                    num_lines: rows,
                    num_cols: cols,
                    cell_width: self.shared.cell_w.load(Ordering::Relaxed),
                    cell_height: self.shared.cell_h.load(Ordering::Relaxed),
                };
                self.write(&f(ws));
            }
            Event::CursorBlinkingChange | Event::MouseCursorDirty | Event::Wakeup => {
                self.shared.notify(EV_WAKEUP)
            }
            _ => {}
        }
    }
}

struct Dims {
    cols: usize,
    lines: usize,
}
impl Dimensions for Dims {
    fn total_lines(&self) -> usize {
        self.lines
    }
    fn screen_lines(&self) -> usize {
        self.lines
    }
    fn columns(&self) -> usize {
        self.cols
    }
}

struct Inner {
    term: Term<Proxy>,
    parser: Processor,
}

struct PtySyncState {
    muted: bool,
    row_bias: i32,
    pending_esc: Vec<u8>,
}

pub struct Core {
    inner: Arc<Mutex<Inner>>,
    writer: Writer,
    master: Option<Mutex<Box<dyn MasterPty + Send>>>,
    killer: Option<Mutex<Box<dyn portable_pty::ChildKiller + Send + Sync>>>,
    theme: Arc<Mutex<Theme>>,
    shared: Arc<Shared>,
    size: Arc<Mutex<(u16, u16)>>,
    stop: Arc<AtomicBool>,
    pty_sync: Arc<Mutex<PtySyncState>>,
}

pub struct SpawnOptions<'a> {
    pub program: Option<&'a str>,
    pub args: Vec<String>,
    pub cwd: Option<&'a str>,
    pub cols: u16,
    pub rows: u16,
    pub scrollback: usize,
}

impl Core {
    fn new(o: &SpawnOptions, cb: Option<(Callback, usize)>, writer: Writer, alive: bool) -> Self {
        let shared = Arc::new(Shared {
            cb: Mutex::new(cb),
            title: Mutex::new(String::new()),
            clipboard: Mutex::new(None),
            cell_w: AtomicU16::new(0),
            cell_h: AtomicU16::new(0),
            alive: AtomicBool::new(alive),
            exit_code: Mutex::new(0),
        });
        let theme = Arc::new(Mutex::new(Theme::default()));
        let size = Arc::new(Mutex::new((o.cols, o.rows)));
        let proxy = Proxy { shared: shared.clone(), writer: writer.clone(), theme: theme.clone(), size: size.clone() };
        let config = Config {
            scrolling_history: o.scrollback,
            kitty_keyboard: true,
            osc52: Osc52::OnlyCopy,
            ..Config::default()
        };
        let term = Term::new(config, &Dims { cols: o.cols as usize, lines: o.rows as usize }, proxy);
        let inner = Arc::new(Mutex::new(Inner { term, parser: Processor::default() }));
        let stop = Arc::new(AtomicBool::new(false));
        let pty_sync = Arc::new(Mutex::new(PtySyncState {
            muted: false,
            row_bias: 0,
            pending_esc: Vec::new(),
        }));
        Self { inner, writer, master: None, killer: None, theme, shared, size, stop, pty_sync }
    }

    pub fn spawn(o: SpawnOptions, cb: Option<(Callback, usize)>) -> Result<Core, String> {
        let pair = native_pty_system()
            .openpty(PtySize { rows: o.rows, cols: o.cols, pixel_width: 0, pixel_height: 0 })
            .map_err(|e| e.to_string())?;
        let mut cmd = match o.program {
            Some(p) => {
                let mut c = CommandBuilder::new(p);
                c.args(&o.args);
                c
            }
            None => CommandBuilder::new_default_prog(),
        };
        if let Some(d) = o.cwd {
            cmd.cwd(d);
        }
        cmd.env("TERM", "xterm-256color");
        cmd.env("COLORTERM", "truecolor");
        let mut child = pair.slave.spawn_command(cmd).map_err(|e| e.to_string())?;
        drop(pair.slave);
        let killer = child.clone_killer();
        let mut reader = pair.master.try_clone_reader().map_err(|e| e.to_string())?;
        let writer: Writer = Arc::new(Mutex::new(pair.master.take_writer().map_err(|e| e.to_string())?));

        let mut core = Self::new(&o, cb, writer, true);
        core.master = Some(Mutex::new(pair.master));
        core.killer = Some(Mutex::new(killer));

        // PTY reader
        {
            let inner = core.inner.clone();
            let shared = core.shared.clone();
            let pty_sync = core.pty_sync.clone();
            std::thread::spawn(move || {
                let mut buf = vec![0u8; 65536];
                loop {
                    match reader.read(&mut buf) {
                        Ok(0) | Err(_) => break,
                        Ok(n) => {
                            let mut sync = pty_sync.lock().unwrap();
                            if sync.muted {
                                continue;
                            }
                            if sync.row_bias == 0 && sync.pending_esc.is_empty() {
                                drop(sync);
                                {
                                    let mut g = inner.lock().unwrap();
                                    let Inner { term, parser } = &mut *g;
                                    parser.advance(term, &buf[..n]);
                                }
                                shared.notify(EV_WAKEUP);
                            } else {
                                let mut combined = Vec::with_capacity(sync.pending_esc.len() + n);
                                combined.extend_from_slice(&sync.pending_esc);
                                combined.extend_from_slice(&buf[..n]);
                                sync.pending_esc.clear();

                                {
                                    let mut g = inner.lock().unwrap();
                                    let Inner { term, parser } = &mut *g;
                                    advance_with_row_bias(term, parser, &combined, &mut sync);
                                }
                                drop(sync);
                                shared.notify(EV_WAKEUP);
                            }
                        }
                    }
                }
                let code = child.wait().map(|s| s.exit_code() as i32).unwrap_or(-1);
                *shared.exit_code.lock().unwrap() = code;
                shared.alive.store(false, Ordering::SeqCst);
                shared.notify(EV_EXIT);
            });
        }
        // Synchronized-output (mode 2026) timeout flusher
        {
            let inner = core.inner.clone();
            let shared = core.shared.clone();
            let stop = core.stop.clone();
            std::thread::spawn(move || {
                while !stop.load(Ordering::Relaxed) {
                    std::thread::sleep(Duration::from_millis(25));
                    let mut flushed = false;
                    {
                        let mut g = inner.lock().unwrap();
                        let Inner { term, parser } = &mut *g;
                        if let Some(t) = parser.sync_timeout().sync_timeout() {
                            if Instant::now() >= t {
                                parser.stop_sync(term);
                                flushed = true;
                            }
                        }
                    }
                    if flushed {
                        shared.notify(EV_WAKEUP);
                    }
                }
            });
        }

        Ok(core)
    }

    fn write_bytes(&self, b: &[u8]) {
        let mut w = self.writer.lock().unwrap();
        let _ = w.write_all(b);
        let _ = w.flush();
    }

    pub fn resize(&self, cols: u16, rows: u16, cw: u16, ch: u16) {
        let cols = cols.max(2);
        let rows = rows.max(1);
        self.shared.cell_w.store(cw, Ordering::Relaxed);
        self.shared.cell_h.store(ch, Ordering::Relaxed);
        {
            let mut s = self.size.lock().unwrap();
            if *s == (cols, rows) {
                return;
            }
            *s = (cols, rows);
        }
        self.inner.lock().unwrap().term.resize(Dims { cols: cols as usize, lines: rows as usize });
        if let Some(master) = &self.master {
            let _ = master.lock().unwrap().resize(PtySize {
                rows,
                cols,
                pixel_width: cols.saturating_mul(cw),
                pixel_height: rows.saturating_mul(ch),
            });
        }
    }

    fn user_input(&self) {
        let mut g = self.inner.lock().unwrap();
        g.term.selection = None;
        if g.term.grid().display_offset() > 0 {
            g.term.scroll_display(Scroll::Bottom);
        }
    }

    pub fn mode(&self) -> TermMode {
        *self.inner.lock().unwrap().term.mode()
    }

    pub fn paste(&self, text: &str) {
        let mut t = text.replace("\x1b[201~", "").replace("\r\n", "\r").replace('\n', "\r");
        if self.mode().contains(TermMode::BRACKETED_PASTE) {
            t = format!("\x1b[200~{}\x1b[201~", t);
        }
        self.user_input();
        self.write_bytes(t.as_bytes());
    }

    fn point(term: &Term<Proxy>, col: i32, row: i32) -> Point {
        let cols = term.columns() as i32;
        let rows = term.screen_lines() as i32;
        let off = term.grid().display_offset() as i32;
        Point::new(Line(row.clamp(0, rows - 1) - off), Column(col.clamp(0, cols - 1) as usize))
    }

    pub fn link_at(&self, col: i32, row: i32) -> Option<(String, [i32; 4])> {
        let g = self.inner.lock().unwrap();
        let term = &g.term;
        let cols = term.columns() as i32;
        let rows = term.screen_lines() as i32;
        if col < 0 || row < 0 || col >= cols || row >= rows {
            return None;
        }
        let off = term.grid().display_offset() as i32;
        let line_of = |r: i32| Line(r - off);
        let cell_at = |c: i32, r: i32| &term.grid()[line_of(r)][Column(c as usize)];

        // 1) explicit OSC 8 hyperlink
        if let Some(h) = cell_at(col, row).hyperlink() {
            let same = |c: i32| cell_at(c, row).hyperlink().map_or(false, |x| x == h);
            let (mut a, mut b) = (col, col);
            while a > 0 && same(a - 1) {
                a -= 1;
            }
            while b + 1 < cols && same(b + 1) {
                b += 1;
            }
            return Some((h.uri().to_string(), [a, row, b, row]));
        }

        // 2) plain-text URL detection across soft-wrapped rows
        let wraps = |r: i32| cell_at(cols - 1, r).flags.contains(Flags::WRAPLINE);
        let mut r0 = row;
        while r0 > 0 && wraps(r0 - 1) {
            r0 -= 1;
        }
        let mut r1 = row;
        while r1 + 1 < rows && wraps(r1) {
            r1 += 1;
        }
        let mut text = String::new();
        let mut starts: Vec<usize> = Vec::new();
        let mut cellmap: Vec<(i32, i32, i32)> = Vec::new(); // (col,row,width)
        let mut target = None;
        for r in r0..=r1 {
            for c in 0..cols {
                let cell = cell_at(c, r);
                if cell.flags.intersects(Flags::WIDE_CHAR_SPACER | Flags::LEADING_WIDE_CHAR_SPACER) {
                    continue;
                }
                if r == row && (c == col || (c + 1 == col && cell.flags.contains(Flags::WIDE_CHAR))) {
                    target = Some(cellmap.len());
                }
                starts.push(text.len());
                let ch = if cell.c == '\0' { ' ' } else { cell.c };
                text.push(ch);
                cellmap.push((c, r, if cell.flags.contains(Flags::WIDE_CHAR) { 2 } else { 1 }));
            }
        }
        let target = target?;
        let re = url_regex();
        for m in re.find_iter(&text) {
            let s = starts.partition_point(|&o| o < m.start());
            let e = starts.partition_point(|&o| o < m.end()); // exclusive char index
            if target >= s && target < e && e > s {
                let (c0, rr0, _) = cellmap[s];
                let (c1, rr1, w) = cellmap[e - 1];
                return Some((m.as_str().to_string(), [c0, rr0, c1 + w - 1, rr1]));
            }
        }
        None
    }
}

fn url_regex() -> &'static Regex {
    use std::sync::OnceLock;
    static RE: OnceLock<Regex> = OnceLock::new();
    // same pattern as xterm-addon-web-links
    RE.get_or_init(|| Regex::new(r##"https?://[^\s"'!*(){}|\\\^<>`]*[^\s"':,.!?{}|\\\^~\[\]`()<>]"##).unwrap())
}

impl Drop for Core {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
        if let Some(killer) = &self.killer {
            let _ = killer.lock().unwrap().kill();
        }
        *self.shared.cb.lock().unwrap() = None;
    }
}

// =====================================================================================
// C ABI
// =====================================================================================

#[repr(C)]
pub struct CCell {
    pub ch: u32,
    pub fg: u32,
    pub bg: u32,
    pub ul: u32,
    pub flags: u32,
}

#[repr(C)]
#[derive(Default)]
pub struct CInfo {
    pub cols: i32,
    pub rows: i32,
    pub cursor_col: i32,
    pub cursor_row: i32,
    /// 0 block, 1 underline, 2 beam, -1 hidden
    pub cursor_shape: i32,
    pub cursor_blink: i32,
    pub display_offset: i32,
    pub history_size: i32,
    pub mode: u32,
    pub has_selection: i32,
    pub fg: u32,
    pub bg: u32,
    pub cursor_color: u32,
    pub cursor_text: u32,
    pub sel_bg: u32,
}

fn cstr<'a>(p: *const c_char) -> Option<&'a str> {
    if p.is_null() {
        None
    } else {
        unsafe { CStr::from_ptr(p) }.to_str().ok()
    }
}

fn into_c(s: String) -> *mut c_char {
    CString::new(s.replace('\0', "")).unwrap().into_raw()
}

/// argv: NULL-terminated array of extra args (may be NULL). program NULL = user's shell.
#[no_mangle]
pub extern "C" fn term_new(
    program: *const c_char,
    argv: *const *const c_char,
    cwd: *const c_char,
    cols: i32,
    rows: i32,
    scrollback: i32,
    cb: Option<Callback>,
    user: *mut c_void,
) -> *mut Core {
    let mut args = Vec::new();
    if !argv.is_null() {
        let mut i = 0;
        loop {
            let p = unsafe { *argv.add(i) };
            if p.is_null() {
                break;
            }
            if let Some(s) = cstr(p) {
                args.push(s.to_string());
            }
            i += 1;
        }
    }
    let opts = SpawnOptions {
        program: cstr(program),
        args,
        cwd: cstr(cwd),
        cols: cols.clamp(2, 1000) as u16,
        rows: rows.clamp(1, 1000) as u16,
        scrollback: scrollback.max(0) as usize,
    };
    match Core::spawn(opts, cb.map(|f| (f, user as usize))) {
        Ok(c) => Box::into_raw(Box::new(c)),
        Err(e) => {
            eprintln!("termcore: spawn failed: {e}");
            std::ptr::null_mut()
        }
    }
}

#[no_mangle]
pub extern "C" fn term_new_log(
    cols: i32,
    rows: i32,
    scrollback: i32,
    cb: Option<Callback>,
    user: *mut c_void,
) -> *mut Core {
    let opts = SpawnOptions {
        program: None,
        args: Vec::new(),
        cwd: None,
        cols: cols.clamp(2, 1000) as u16,
        rows: rows.clamp(1, 1000) as u16,
        scrollback: scrollback.max(0) as usize,
    };
    let writer: Writer = Arc::new(Mutex::new(Box::new(std::io::sink())));
    Box::into_raw(Box::new(Core::new(&opts, cb.map(|f| (f, user as usize)), writer, false)))
}

#[no_mangle]
pub extern "C" fn term_free(h: *mut Core) {
    if !h.is_null() {
        drop(unsafe { Box::from_raw(h) });
    }
}

#[no_mangle]
pub extern "C" fn term_str_free(s: *mut c_char) {
    if !s.is_null() {
        drop(unsafe { CString::from_raw(s) });
    }
}

#[no_mangle]
pub extern "C" fn term_resize(h: *mut Core, cols: i32, rows: i32, cell_w: i32, cell_h: i32) {
    let c = unsafe { &*h };
    c.resize(cols.clamp(2, 1000) as u16, rows.clamp(1, 1000) as u16, cell_w.max(0) as u16, cell_h.max(0) as u16);
}

#[no_mangle]
pub extern "C" fn term_set_theme(h: *mut Core, fg: u32, bg: u32, cursor: u32, cursor_text: u32, sel_bg: u32, ansi16: *const u32) {
    let c = unsafe { &*h };
    let mut t = c.theme.lock().unwrap();
    t.fg = unpack(fg);
    t.bg = unpack(bg);
    t.cursor = unpack(cursor);
    t.cursor_text = unpack(cursor_text);
    t.sel_bg = unpack(sel_bg);
    if !ansi16.is_null() {
        for i in 0..16 {
            t.ansi[i] = unpack(unsafe { *ansi16.add(i) });
        }
    }
}

/// Fill `cells` (row-major, cols*rows) and `info`. Returns 0 on success, -1 if cap too small
/// (info.cols/rows then hold the required size).
#[no_mangle]
pub extern "C" fn term_snapshot(h: *mut Core, cells: *mut CCell, cap: usize, info: *mut CInfo) -> i32 {
    let c = unsafe { &*h };
    let info = unsafe { &mut *info };
    let theme = c.theme.lock().unwrap().clone();
    let g = c.inner.lock().unwrap();
    let term = &g.term;
    let cols = term.columns();
    let rows = term.screen_lines();
    let off = term.grid().display_offset();
    info.cols = cols as i32;
    info.rows = rows as i32;
    if cols * rows > cap {
        return -1;
    }
    let out = unsafe { std::slice::from_raw_parts_mut(cells, cols * rows) };
    let colors = term.colors();
    let sel = term.selection.as_ref().and_then(|s| s.to_range(term));
    let sel_bg = pack(theme.sel_bg);

    for r in 0..rows {
        let line = Line(r as i32 - off as i32);
        let row = &term.grid()[line];
        for col in 0..cols {
            let cell = &row[Column(col)];
            let fl = cell.flags;
            let bold = fl.contains(Flags::BOLD);
            let mut fg = theme.resolve(cell.fg, true, bold, colors);
            let mut bg = theme.resolve(cell.bg, false, false, colors);
            if fl.contains(Flags::INVERSE) {
                std::mem::swap(&mut fg, &mut bg);
            }
            if fl.contains(Flags::DIM) {
                fg = theme::blend(fg, bg, 0.5);
            }
            let mut f = 0u32;
            if bold { f |= CF_BOLD; }
            if fl.contains(Flags::ITALIC) { f |= CF_ITALIC; }
            if fl.contains(Flags::UNDERLINE) { f |= CF_UL_SINGLE; }
            if fl.contains(Flags::DOUBLE_UNDERLINE) { f |= CF_UL_DOUBLE; }
            if fl.contains(Flags::UNDERCURL) { f |= CF_UL_CURLY; }
            if fl.contains(Flags::DOTTED_UNDERLINE) { f |= CF_UL_DOTTED; }
            if fl.contains(Flags::DASHED_UNDERLINE) { f |= CF_UL_DASHED; }
            if fl.contains(Flags::STRIKEOUT) { f |= CF_STRIKE; }
            if fl.contains(Flags::WIDE_CHAR) { f |= CF_WIDE; }
            if fl.contains(Flags::WIDE_CHAR_SPACER) { f |= CF_SPACER; }
            let mut ul = fg;
            if let Some(uc) = cell.underline_color() {
                ul = theme.resolve(uc, true, false, colors);
            }
            if cell.hyperlink().is_some() {
                f |= CF_LINK | CF_UL_DASHED;
            }
            let selected = sel.as_ref().map_or(false, |s| s.contains(Point::new(line, Column(col))));
            let mut bgp = pack(bg);
            if selected {
                f |= CF_SELECTED;
                bgp = sel_bg;
            }
            let mut ch = cell.c as u32;
            if fl.contains(Flags::HIDDEN) || fl.contains(Flags::LEADING_WIDE_CHAR_SPACER) || ch == 0 {
                ch = ' ' as u32;
            }
            if cell.zerowidth().map_or(false, |z| !z.is_empty()) {
                f |= CF_HAS_EXTRA;
            }
            out[r * cols + col] = CCell { ch, fg: pack(fg), bg: bgp, ul: pack(ul), flags: f };
        }
    }

    let cur = term.grid().cursor.point;
    let crow = cur.line.0 + off as i32;
    let style = term.cursor_style();
    let visible = term.mode().contains(TermMode::SHOW_CURSOR) && crow >= 0 && crow < rows as i32;
    info.cursor_col = (cur.column.0).min(cols - 1) as i32;
    info.cursor_row = crow;
    info.cursor_shape = if !visible {
        -1
    } else {
        match style.shape {
            CursorShape::Underline => 1,
            CursorShape::Beam => 2,
            CursorShape::Hidden => -1,
            _ => 0,
        }
    };
    info.cursor_blink = style.blinking as i32;
    info.display_offset = off as i32;
    info.history_size = term.history_size() as i32;
    let m = *term.mode();
    let mut mb = 0;
    if m.intersects(TermMode::MOUSE_MODE) { mb |= MODE_MOUSE; }
    if m.contains(TermMode::ALT_SCREEN) { mb |= MODE_ALT_SCREEN; }
    if m.contains(TermMode::APP_CURSOR) { mb |= MODE_APP_CURSOR; }
    if m.contains(TermMode::BRACKETED_PASTE) { mb |= MODE_BRACKETED_PASTE; }
    if m.contains(TermMode::FOCUS_IN_OUT) { mb |= MODE_FOCUS_REPORT; }
    info.mode = mb;
    info.has_selection = sel.is_some() as i32;
    info.fg = pack(theme.fg);
    info.bg = pack(theme.bg);
    info.cursor_color = pack(theme.cursor);
    info.cursor_text = pack(theme.cursor_text);
    info.sel_bg = sel_bg;
    0
}

/// Combining / zero-width codepoints attached to a cell. Returns count written.
#[no_mangle]
pub extern "C" fn term_cell_extra(h: *mut Core, col: i32, row: i32, out: *mut u32, cap: usize) -> usize {
    let c = unsafe { &*h };
    let g = c.inner.lock().unwrap();
    let term = &g.term;
    if col < 0 || row < 0 || col as usize >= term.columns() || row as usize >= term.screen_lines() {
        return 0;
    }
    let line = Line(row - term.grid().display_offset() as i32);
    let cell = &term.grid()[line][Column(col as usize)];
    let mut n = 0;
    if let Some(z) = cell.zerowidth() {
        for ch in z.iter().take(cap) {
            unsafe { *out.add(n) = *ch as u32 };
            n += 1;
        }
    }
    n
}

#[no_mangle]
pub extern "C" fn term_write(h: *mut Core, data: *const u8, len: usize) {
    let c = unsafe { &*h };
    c.user_input();
    c.write_bytes(unsafe { std::slice::from_raw_parts(data, len) });
}

fn advance_with_row_bias(
    term: &mut Term<Proxy>,
    parser: &mut Processor,
    data: &[u8],
    sync: &mut PtySyncState,
) {
    let mut i = 0;
    while i < data.len() {
        if sync.row_bias == 0 {
            parser.advance(term, &data[i..]);
            break;
        }
        if data[i] == 0x1b {
            if i + 1 >= data.len() {
                sync.pending_esc.extend_from_slice(&data[i..]);
                break;
            }
            if data[i + 1] == b'[' {
                // Scan CSI sequence: ESC [ <params> <final 0x40..=0x7E>
                let mut j = i + 2;
                while j < data.len() && !(0x40..=0x7e).contains(&data[j]) {
                    j += 1;
                }
                if j >= data.len() {
                    if data.len() - i <= 64 {
                        sync.pending_esc.extend_from_slice(&data[i..]);
                    } else {
                        parser.advance(term, &data[i..]);
                    }
                    break;
                }
                let final_byte = data[j];
                let params = &data[i + 2..j];
                if final_byte == b'H' || final_byte == b'f' {
                    if let Ok(param_str) = std::str::from_utf8(params) {
                        if !param_str.starts_with(['?', '>', '<', '=']) {
                            let mut parts = param_str.split(';');
                            let r_raw = parts.next().unwrap_or("");
                            let c_raw = parts.next().unwrap_or("");
                            let row: i32 = if r_raw.is_empty() { 1 } else { r_raw.parse().unwrap_or(1) };
                            let col: i32 = if c_raw.is_empty() { 1 } else { c_raw.parse().unwrap_or(1) };
                            let screen_rows = term.screen_lines() as i32;
                            let target_row = row + sync.row_bias;
                            if target_row <= screen_rows {
                                let seq = format!("\x1b[{};{}{}", target_row.max(1), col.max(1), final_byte as char);
                                parser.advance(term, seq.as_bytes());
                            } else {
                                let scroll = target_row - screen_rows;
                                sync.row_bias = (sync.row_bias - scroll).max(0);
                                let move_bot = format!("\x1b[{screen_rows};1H");
                                parser.advance(term, move_bot.as_bytes());
                                for _ in 0..scroll {
                                    parser.advance(term, b"\n");
                                }
                                let move_col = format!("\x1b[{screen_rows};{}{}", col.max(1), final_byte as char);
                                parser.advance(term, move_col.as_bytes());
                            }
                            i = j + 1;
                            continue;
                        }
                    }
                } else if final_byte == b'd' {
                    if let Ok(param_str) = std::str::from_utf8(params) {
                        if let Ok(row) = if param_str.is_empty() { Ok(1) } else { param_str.parse::<i32>() } {
                            let screen_rows = term.screen_lines() as i32;
                            let target_row = row + sync.row_bias;
                            if target_row <= screen_rows {
                                let seq = format!("\x1b[{}d", target_row.max(1));
                                parser.advance(term, seq.as_bytes());
                            } else {
                                let scroll = target_row - screen_rows;
                                sync.row_bias = (sync.row_bias - scroll).max(0);
                                let cur_col = term.grid().cursor.point.column.0 + 1;
                                let move_bot = format!("\x1b[{screen_rows};1H");
                                parser.advance(term, move_bot.as_bytes());
                                for _ in 0..scroll {
                                    parser.advance(term, b"\n");
                                }
                                let restore_col = format!("\x1b[{screen_rows};{cur_col}H");
                                parser.advance(term, restore_col.as_bytes());
                            }
                            i = j + 1;
                            continue;
                        }
                    }
                } else if (final_byte == b'J' && (params == b"2" || params == b"3"))
                    || (final_byte == b'h' && (params == b"?1049" || params == b"?47" || params == b"?1047"))
                {
                    sync.row_bias = 0;
                }
                parser.advance(term, &data[i..=j]);
                i = j + 1;
                continue;
            }
            parser.advance(term, &data[i..i + 1]);
            i += 1;
            continue;
        }

        // Plain bytes up to next ESC or LF
        let start = i;
        while i < data.len() && data[i] != 0x1b && data[i] != b'\n' {
            i += 1;
        }
        if i > start {
            parser.advance(term, &data[start..i]);
        }
        if i < data.len() && data[i] == b'\n' {
            let bottom = term.screen_lines() as i32 - 1;
            if term.grid().cursor.point.line.0 >= bottom && sync.row_bias > 0 {
                sync.row_bias -= 1;
            }
            parser.advance(term, b"\n");
            i += 1;
        }
    }
}

#[no_mangle]
pub extern "C" fn term_feed(h: *mut Core, data: *const u8, len: usize) {
    if h.is_null() || data.is_null() || len == 0 {
        return;
    }
    let c = unsafe { &*h };
    let slice = unsafe { std::slice::from_raw_parts(data, len) };
    let delta = {
        let mut g = c.inner.lock().unwrap();
        let Inner { term, parser } = &mut *g;
        let alt_before = term.mode().contains(TermMode::ALT_SCREEN);
        let row_before = term.grid().cursor.point.line.0;
        parser.advance(term, slice);
        let alt_after = term.mode().contains(TermMode::ALT_SCREEN);
        let row_after = term.grid().cursor.point.line.0;
        if !alt_before && !alt_after && c.shared.alive.load(Ordering::SeqCst) {
            (row_after - row_before).max(0)
        } else {
            0
        }
    };
    if delta > 0 {
        c.pty_sync.lock().unwrap().row_bias += delta;
    }
    c.shared.notify(EV_WAKEUP);
}

#[no_mangle]
pub extern "C" fn term_set_pty_mute(h: *mut Core, muted: i32, _marker: *const c_char) {
    if h.is_null() {
        return;
    }
    let c = unsafe { &*h };
    c.pty_sync.lock().unwrap().muted = muted != 0;
}

#[no_mangle]
pub extern "C" fn term_paste(h: *mut Core, text: *const c_char) {
    if let Some(s) = cstr(text) {
        unsafe { &*h }.paste(s);
    }
}

#[no_mangle]
pub extern "C" fn term_focus(h: *mut Core, focused: i32) {
    let c = unsafe { &*h };
    if c.mode().contains(TermMode::FOCUS_IN_OUT) {
        c.write_bytes(if focused != 0 { b"\x1b[I" } else { b"\x1b[O" });
    }
}

/// Encode + send a key. Returns 1 if bytes were sent.
#[no_mangle]
pub extern "C" fn term_key(h: *mut Core, key: i32, base: u32, text: *const c_char, mods: u32, event: i32) -> i32 {
    let c = unsafe { &*h };
    let k = keys::Key { key, base, text: cstr(text).unwrap_or(""), mods, event };
    match keys::encode_key(&k, c.mode()) {
        Some(b) => {
            if event != keys::EV_RELEASE {
                c.user_input();
            }
            c.write_bytes(&b);
            1
        }
        None => 0,
    }
}

/// Mouse report. Returns 1 if the app has mouse tracking on and the event was sent.
#[no_mangle]
pub extern "C" fn term_mouse(h: *mut Core, button: i32, action: i32, col: i32, row: i32, mods: u32) -> i32 {
    let c = unsafe { &*h };
    match keys::encode_mouse(c.mode(), button, action, col, row, mods) {
        Some(b) => {
            c.write_bytes(&b);
            1
        }
        None => 0,
    }
}

/// Wheel / scroll. lines > 0 = up (towards history). Handles mouse reporting, alt-screen
/// arrow translation and local scrollback.
#[no_mangle]
pub extern "C" fn term_scroll(h: *mut Core, lines: i32, col: i32, row: i32, mods: u32) {
    if lines == 0 {
        return;
    }
    let c = unsafe { &*h };
    let mode = c.mode();
    let n = lines.unsigned_abs().min(200);
    if mode.intersects(TermMode::MOUSE_MODE) {
        let btn = if lines > 0 { 64 } else { 65 };
        for _ in 0..n {
            if let Some(b) = keys::encode_mouse(mode, btn, keys::ACT_PRESS, col, row, mods) {
                c.write_bytes(&b);
            }
        }
    } else if mode.contains(TermMode::ALT_SCREEN) && mode.contains(TermMode::ALTERNATE_SCROLL) {
        let l = if lines > 0 { 'A' } else { 'B' };
        let seq = if mode.contains(TermMode::APP_CURSOR) { format!("\x1bO{l}") } else { format!("\x1b[{l}") };
        for _ in 0..n {
            c.write_bytes(seq.as_bytes());
        }
    } else {
        c.inner.lock().unwrap().term.scroll_display(Scroll::Delta(lines));
        c.shared.notify(EV_WAKEUP);
    }
}

#[no_mangle]
pub extern "C" fn term_scroll_local(h: *mut Core, mode: i32, lines: i32) {
    // mode: 0 delta, 1 page up, 2 page down, 3 top, 4 bottom, 5 absolute offset (lines = offset)
    let c = unsafe { &*h };
    let mut g = c.inner.lock().unwrap();
    match mode {
        0 => g.term.scroll_display(Scroll::Delta(lines)),
        1 => g.term.scroll_display(Scroll::PageUp),
        2 => g.term.scroll_display(Scroll::PageDown),
        3 => g.term.scroll_display(Scroll::Top),
        4 => g.term.scroll_display(Scroll::Bottom),
        _ => {
            let cur = g.term.grid().display_offset() as i32;
            g.term.scroll_display(Scroll::Delta(lines - cur));
        }
    }
    drop(g);
    c.shared.notify(EV_WAKEUP);
}

/// kind: 0 simple, 1 block, 2 semantic (word), 3 lines
#[no_mangle]
pub extern "C" fn term_sel_start(h: *mut Core, kind: i32, col: i32, row: i32, right_half: i32) {
    let c = unsafe { &*h };
    let mut g = c.inner.lock().unwrap();
    let ty = match kind {
        1 => SelectionType::Block,
        2 => SelectionType::Semantic,
        3 => SelectionType::Lines,
        _ => SelectionType::Simple,
    };
    let p = Core::point(&g.term, col, row);
    let side = if right_half != 0 { Side::Right } else { Side::Left };
    g.term.selection = Some(Selection::new(ty, p, side));
    drop(g);
    c.shared.notify(EV_WAKEUP);
}

#[no_mangle]
pub extern "C" fn term_sel_update(h: *mut Core, col: i32, row: i32, right_half: i32) {
    let c = unsafe { &*h };
    let mut g = c.inner.lock().unwrap();
    let p = Core::point(&g.term, col, row);
    let side = if right_half != 0 { Side::Right } else { Side::Left };
    if let Some(s) = g.term.selection.as_mut() {
        s.update(p, side);
    }
    drop(g);
    c.shared.notify(EV_WAKEUP);
}

#[no_mangle]
pub extern "C" fn term_sel_clear(h: *mut Core) {
    let c = unsafe { &*h };
    c.inner.lock().unwrap().term.selection = None;
    c.shared.notify(EV_WAKEUP);
}

#[no_mangle]
pub extern "C" fn term_sel_all(h: *mut Core) {
    let c = unsafe { &*h };
    let mut g = c.inner.lock().unwrap();
    let top = Point::new(g.term.topmost_line(), Column(0));
    let bottom = Point::new(g.term.bottommost_line(), g.term.last_column());
    let mut s = Selection::new(SelectionType::Simple, top, Side::Left);
    s.update(bottom, Side::Right);
    g.term.selection = Some(s);
    drop(g);
    c.shared.notify(EV_WAKEUP);
}

/// Caller frees with term_str_free. NULL if no selection.
#[no_mangle]
pub extern "C" fn term_sel_text(h: *mut Core) -> *mut c_char {
    let c = unsafe { &*h };
    match c.inner.lock().unwrap().term.selection_to_string() {
        Some(s) if !s.is_empty() => into_c(s),
        _ => std::ptr::null_mut(),
    }
}

/// URL under a viewport cell (OSC 8 or detected). range = [c0,r0,c1,r1] inclusive.
#[no_mangle]
pub extern "C" fn term_link_at(h: *mut Core, col: i32, row: i32, range: *mut i32) -> *mut c_char {
    let c = unsafe { &*h };
    match c.link_at(col, row) {
        Some((u, r)) => {
            unsafe { std::ptr::copy_nonoverlapping(r.as_ptr(), range, 4) };
            into_c(u)
        }
        None => std::ptr::null_mut(),
    }
}

#[no_mangle]
pub extern "C" fn term_title(h: *mut Core) -> *mut c_char {
    into_c(unsafe { &*h }.shared.title.lock().unwrap().clone())
}

#[no_mangle]
pub extern "C" fn term_take_clipboard(h: *mut Core) -> *mut c_char {
    match unsafe { &*h }.shared.clipboard.lock().unwrap().take() {
        Some(s) => into_c(s),
        None => std::ptr::null_mut(),
    }
}

#[no_mangle]
pub extern "C" fn term_alive(h: *mut Core) -> i32 {
    unsafe { &*h }.shared.alive.load(Ordering::SeqCst) as i32
}

#[no_mangle]
pub extern "C" fn term_exit_code(h: *mut Core) -> i32 {
    *unsafe { &*h }.shared.exit_code.lock().unwrap()
}

// Used by tests
#[doc(hidden)]
pub fn __debug_row_text(c: &Core, row: usize) -> String {
    let g = c.inner.lock().unwrap();
    let term = &g.term;
    let off = term.grid().display_offset() as i32;
    let line = Line(row as i32 - off);
    (0..term.columns()).map(|i| term.grid()[line][Column(i)].c).collect::<String>().trim_end().to_string()
}
