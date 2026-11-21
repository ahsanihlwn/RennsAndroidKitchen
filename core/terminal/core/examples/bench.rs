// Parse-throughput benchmark: child floods logcat-style colored lines; we time until it exits.
use std::ffi::CString;
use std::ptr;
use std::time::Instant;
use termcore::*;
fn main() {
    let mb: usize = std::env::args().nth(1).and_then(|s| s.parse().ok()).unwrap_or(100);
    let gen = format!(r#"
import sys,itertools
tags=['ActivityManager','WindowManager','SurfaceFlinger','chatty','BluetoothAdapter']
lv=[('V','37'),('D','34'),('I','32'),('W','33'),('E','31')]
out=sys.stdout.buffer; n=0; i=0; target={mb}*1024*1024
while n<target:
    l,c=lv[i%5]; t=tags[i%5]
    s=('11-17 19:%02d:%02d.%03d  %5d %5d \x1b[%sm%s\x1b[0m \x1b[1m%-18s\x1b[0m: message number %d with some payload data 0x%08x and a long tail to force wrapping on narrow terminals xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx\n'
       %((i//60)%60,i%60,i%1000,1000+i%50,2000+i%70,c,l,t,i,i*2654435761%2**32)).encode()
    out.write(s); n+=len(s); i+=1
out.flush()
"#);
    let prog = CString::new("/usr/bin/python3").unwrap();
    let a1 = CString::new("-c").unwrap(); let a2 = CString::new(gen).unwrap();
    let argv = [a1.as_ptr(), a2.as_ptr(), ptr::null()];
    let t = Instant::now();
    let h = term_new(prog.as_ptr(), argv.as_ptr(), ptr::null(), 120, 40, 10000, None, ptr::null_mut());
    let mut snaps = 0u64; let mut snap_ns = 0u128;
    let mut info = CInfo::default();
    term_snapshot(h, ptr::null_mut(), 0, &mut info);
    let mut cells: Vec<CCell> = (0..info.cols*info.rows).map(|_| CCell{ch:0,fg:0,bg:0,ul:0,flags:0}).collect();
    // emulate a UI snapshotting at ~120 Hz while the flood runs
    while term_alive(h) != 0 {
        let s = Instant::now();
        term_snapshot(h, cells.as_mut_ptr(), cells.len(), &mut info);
        snap_ns += s.elapsed().as_nanos(); snaps += 1;
        std::thread::sleep(std::time::Duration::from_millis(8));
    }
    let el = t.elapsed().as_secs_f64();
    println!("{mb} MB flood in {el:.2}s = {:.0} MB/s (incl. python generator), {} snapshots @120Hz, avg snapshot {:.3} ms, history={}", mb as f64 / el, snaps, snap_ns as f64 / snaps as f64 / 1e6, info.history_size);
    term_free(h);
}
