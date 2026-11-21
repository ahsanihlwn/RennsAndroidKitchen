use std::ffi::CString;
use std::ptr;
use std::time::{Duration, Instant};
use termcore::*;

fn spawn(cmd: &str, args: &[&str], cols: i32, rows: i32) -> *mut Core {
    let prog = CString::new(cmd).unwrap();
    let owned: Vec<CString> = args.iter().map(|a| CString::new(*a).unwrap()).collect();
    let mut argv: Vec<*const std::ffi::c_char> = owned.iter().map(|c| c.as_ptr()).collect();
    argv.push(ptr::null());
    let h = term_new(prog.as_ptr(), argv.as_ptr(), ptr::null(), cols, rows, 1000, None, ptr::null_mut());
    assert!(!h.is_null());
    h
}

struct Snap { cells: Vec<CCell>, info: CInfo }
fn snap(h: *mut Core) -> Snap {
    let mut info = CInfo::default();
    let mut cells: Vec<CCell> = Vec::new();
    term_snapshot(h, ptr::null_mut(), 0, &mut info);
    cells.resize_with((info.cols * info.rows) as usize, || CCell { ch: 0, fg: 0, bg: 0, ul: 0, flags: 0 });
    assert_eq!(term_snapshot(h, cells.as_mut_ptr(), cells.len(), &mut info), 0);
    Snap { cells, info }
}
impl Snap {
    fn row(&self, r: usize) -> String {
        let c = self.info.cols as usize;
        self.cells[r * c..(r + 1) * c].iter().map(|x| char::from_u32(x.ch).unwrap()).collect::<String>().trim_end().to_string()
    }
    fn cell(&self, c: usize, r: usize) -> &CCell { &self.cells[r * self.info.cols as usize + c] }
}
fn wait_for(h: *mut Core, what: &str, f: impl Fn(&Snap) -> bool) -> Snap {
    let t = Instant::now();
    loop {
        let s = snap(h);
        if f(&s) { return s; }
        if t.elapsed() > Duration::from_secs(5) {
            let rows: Vec<String> = (0..s.info.rows as usize).map(|r| s.row(r)).collect();
            panic!("timeout waiting for {what}; screen = {rows:?}");
        }
        std::thread::sleep(Duration::from_millis(20));
    }
}

#[test]
fn colors_and_attrs() {
    let h = spawn("/bin/sh", &["-c", r#"printf '\033[31mred\033[0m \033[1;32mbold\033[0m \033[38;2;10;20;30mtc\033[0m \033[4:3mcurl\033[0m \033[48;5;21mbg\033[0m\n'; sleep 3"#], 40, 5);
    let s = wait_for(h, "text", |s| s.row(0).contains("curl"));
    assert_eq!(s.row(0), "red bold tc curl bg");
    assert_eq!(s.cell(0, 0).fg, 0xcc0000, "ANSI red = Tango red");
    assert_eq!(s.cell(4, 0).fg, 0x8ae234, "bold+green -> bright green");
    assert!(s.cell(4, 0).flags & CF_BOLD != 0);
    assert_eq!(s.cell(9, 0).fg, 0x0a141e, "truecolor");
    assert!(s.cell(12, 0).flags & CF_UL_CURLY != 0, "undercurl via 4:3");
    assert_eq!(s.cell(17, 0).bg, 0x0000ff, "256-color index 21 = 0000ff");
    term_free(h);
}

#[test]
fn wide_chars_and_combining() {
    let h = spawn("/bin/sh", &["-c", "printf 'a\\344\\270\\255b e\\314\\201x\\n'; sleep 3"], 20, 3);
    let s = wait_for(h, "text", |s| s.row(0).contains('x'));
    assert!(s.cell(1, 0).flags & CF_WIDE != 0);
    assert!(s.cell(2, 0).flags & CF_SPACER != 0);
    assert_eq!(s.cell(3, 0).ch, 'b' as u32, "grid stays aligned after wide char");
    let idx = s.cells.iter().position(|c| c.flags & CF_HAS_EXTRA != 0).expect("combining mark flagged");
    let mut out = [0u32; 4];
    let n = term_cell_extra(h, (idx % 20) as i32, (idx / 20) as i32, out.as_mut_ptr(), 4);
    assert_eq!((n, out[0]), (1, 0x301));
    term_free(h);
}

#[test]
fn input_roundtrip_and_resize() {
    let h = spawn("/bin/cat", &[], 30, 5);
    term_write(h, b"hello\r".as_ptr(), 6);
    let s = wait_for(h, "echo", |s| s.row(1) == "hello");
    assert_eq!(s.row(0), "hello");
    assert_eq!(s.info.cursor_row, 2);
    term_resize(h, 40, 8, 9, 18);
    let s = snap(h);
    assert_eq!((s.info.cols, s.info.rows), (40, 8));
    term_free(h);
}

#[test]
fn terminal_query_responses() {
    // App asks for cursor position + DA1 and prints what the terminal answered.
    let script = r#"stty raw -echo; printf '\033[6n'; dd bs=1 count=6 2>/dev/null | od -An -c | tr -s ' ' ; sleep 3"#;
    let h = spawn("/bin/sh", &["-c", script], 60, 6);
    let s = wait_for(h, "CPR reply", |s| (0..6).any(|r| s.row(r).contains("033")));
    let all: String = (0..6).map(|r| s.row(r)).collect::<Vec<_>>().join("|");
    assert!(all.contains("033") && all.contains("[") && all.contains("R"), "got {all}");
    term_free(h);
}

#[test]
fn alt_screen_mouse_modes_and_scrollback() {
    let h = spawn("/bin/sh", &["-c", r#"for i in 1 2 3 4 5 6 7 8 9 10; do echo line$i; done; sleep 3"#], 20, 4);
    wait_for(h, "scroll", |s| s.row(2) == "line10" || s.row(3) == "line10");
    let s = snap(h);
    assert!(s.info.history_size >= 6, "scrollback kept: {}", s.info.history_size);
    term_scroll(h, 3, 0, 0, 0);
    let s2 = snap(h);
    assert_eq!(s2.info.display_offset, 3);
    assert_ne!(s2.row(0), s.row(0));
    term_scroll_local(h, 4, 0);
    assert_eq!(snap(h).info.display_offset, 0);
    term_free(h);

    // alt screen + SGR mouse + bracketed paste advertised by app
    let h = spawn("/bin/sh", &["-c", r#"printf '\033[?1049h\033[?1000h\033[?1006h\033[?2004h\033[?1h'; sleep 3"#], 20, 4);
    let s = wait_for(h, "modes", |s| s.info.mode & MODE_ALT_SCREEN != 0);
    assert!(s.info.mode & MODE_MOUSE != 0 && s.info.mode & MODE_BRACKETED_PASTE != 0 && s.info.mode & MODE_APP_CURSOR != 0);
    term_free(h);
}

#[test]
fn selection_and_links() {
    let h = spawn("/bin/sh", &["-c", r#"printf 'see https://example.com/a?b=1, ok\n\033]8;;http://osc8.dev\033\\link text\033]8;;\033\\ end\n'; sleep 3"#], 40, 5);
    wait_for(h, "text", |s| s.row(1).contains("end"));
    let mut r = [0i32; 4];
    let p = term_link_at(h, 8, 0, r.as_mut_ptr());
    assert!(!p.is_null());
    let url = unsafe { std::ffi::CStr::from_ptr(p) }.to_str().unwrap().to_string();
    term_str_free(p);
    assert_eq!(url, "https://example.com/a?b=1", "trailing comma trimmed");
    assert_eq!(r, [4, 0, 28, 0]);
    let p = term_link_at(h, 2, 1, r.as_mut_ptr());
    assert_eq!(unsafe { std::ffi::CStr::from_ptr(p) }.to_str().unwrap(), "http://osc8.dev");
    term_str_free(p);
    assert_eq!(r, [0, 1, 8, 1]);
    assert!(term_link_at(h, 1, 0, r.as_mut_ptr()).is_null());

    // double-click word selection + copy
    term_sel_start(h, 2, 1, 0, 0);
    let t = term_sel_text(h);
    assert_eq!(unsafe { std::ffi::CStr::from_ptr(t) }.to_str().unwrap(), "see");
    term_str_free(t);
    assert!(snap(h).cell(0, 0).flags & CF_SELECTED != 0);
    // drag select across a row
    term_sel_start(h, 0, 0, 1, 0);
    term_sel_update(h, 3, 1, 1);
    let t = term_sel_text(h);
    assert_eq!(unsafe { std::ffi::CStr::from_ptr(t) }.to_str().unwrap(), "link");
    term_str_free(t);
    term_free(h);
}

#[test]
fn child_exit_reported() {
    let h = spawn("/bin/sh", &["-c", "exit 7"], 20, 3);
    let t = Instant::now();
    while term_alive(h) != 0 && t.elapsed() < Duration::from_secs(5) { std::thread::sleep(Duration::from_millis(20)); }
    assert_eq!(term_alive(h), 0);
    assert_eq!(term_exit_code(h), 7);
    term_free(h);
}

#[test]
fn sync_output_is_atomic() {
    // Intermediate frame "AAA" is drawn inside a 2026 sync block; it must never be observable.
    let h = spawn("/bin/sh", &["-c", r#"printf 'old'; sleep 0.3; printf '\033[?2026h\rAAA'; sleep 0.08; printf '\rBBB\033[?2026l'; sleep 3"#], 20, 3);
    let mut seen = std::collections::BTreeSet::new();
    let t = Instant::now();
    while t.elapsed() < Duration::from_millis(900) {
        seen.insert(snap(h).row(0));
        std::thread::sleep(Duration::from_millis(2));
    }
    assert!(seen.contains("old") && seen.contains("BBB"), "seen {seen:?}");
    assert!(!seen.contains("AAA"), "partial frame leaked: {seen:?}");
    term_free(h);
}

#[test]
fn sync_output_times_out() {
    // App begins sync and never ends it: the frame must still appear (timeout flush).
    let h = spawn("/bin/sh", &["-c", r#"printf '\033[?2026h\rSTUCK'; sleep 3"#], 20, 3);
    wait_for(h, "timeout flush", |s| s.row(0) == "STUCK");
    term_free(h);
}
