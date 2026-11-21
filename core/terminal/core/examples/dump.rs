use std::ffi::CString;
use std::ptr;
use termcore::*;
fn main() {
    let a: Vec<String> = std::env::args().skip(1).collect();
    let secs: u64 = a[0].parse().unwrap();
    let prog = CString::new(a[1].clone()).unwrap();
    let owned: Vec<CString> = a[2..].iter().map(|x| CString::new(x.clone()).unwrap()).collect();
    let mut argv: Vec<*const std::ffi::c_char> = owned.iter().map(|c| c.as_ptr()).collect();
    argv.push(ptr::null());
    let h = term_new(prog.as_ptr(), argv.as_ptr(), ptr::null(), 100, 30, 1000, None, ptr::null_mut());
    std::thread::sleep(std::time::Duration::from_secs(secs));
    if let Ok(txt) = std::env::var("DUMP_TEXT") {
        term_write(h, txt.as_ptr(), txt.len());
        std::thread::sleep(std::time::Duration::from_secs(2));
    }
    if let Ok(k) = std::env::var("DUMP_CTRL") {
        let t = CString::new("").unwrap();
        let r = term_key(h, 0, k.chars().next().unwrap() as u32, t.as_ptr(), 4, 1);
        println!("sent ctrl+{k} -> {r}");
        std::thread::sleep(std::time::Duration::from_secs(3));
    }
    let mut info = CInfo::default();
    term_snapshot(h, ptr::null_mut(), 0, &mut info);
    let mut cells: Vec<CCell> = (0..info.cols * info.rows).map(|_| CCell { ch: 0, fg: 0, bg: 0, ul: 0, flags: 0 }).collect();
    term_snapshot(h, cells.as_mut_ptr(), cells.len(), &mut info);
    println!("mode={:#x} cursor=({}, {}) shape={} blink={} alive={}", info.mode, info.cursor_col, info.cursor_row, info.cursor_shape, info.cursor_blink, term_alive(h));
    let mut bgs = std::collections::BTreeMap::new();
    for c in &cells { *bgs.entry(c.bg).or_insert(0) += 1; }
    println!("bg histogram: {:06x?}", bgs);
    for r in 0..info.rows as usize {
        let line: String = (0..info.cols as usize).map(|c| char::from_u32(cells[r * info.cols as usize + c].ch).unwrap_or(' ')).collect();
        println!("{:2}|{}", r, line.trim_end());
    }
    term_free(h);
}
