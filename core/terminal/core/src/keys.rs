//! Keyboard + mouse encoding. Legacy xterm sequences, plus the kitty keyboard
//! protocol when the application has enabled it (so Shift+Enter etc. work).
use alacritty_terminal::term::TermMode;

pub const MOD_SHIFT: u32 = 1;
pub const MOD_ALT: u32 = 2;
pub const MOD_CTRL: u32 = 4;
pub const MOD_SUPER: u32 = 8;

pub const K_CHAR: i32 = 0;
pub const K_ENTER: i32 = 1;
pub const K_TAB: i32 = 2;
pub const K_BACKSPACE: i32 = 3;
pub const K_ESCAPE: i32 = 4;
pub const K_UP: i32 = 5;
pub const K_DOWN: i32 = 6;
pub const K_LEFT: i32 = 7;
pub const K_RIGHT: i32 = 8;
pub const K_HOME: i32 = 9;
pub const K_END: i32 = 10;
pub const K_PGUP: i32 = 11;
pub const K_PGDN: i32 = 12;
pub const K_INSERT: i32 = 13;
pub const K_DELETE: i32 = 14;
pub const K_F1: i32 = 20; // F1..F24 = 20..=43

pub const EV_PRESS: i32 = 1;
pub const EV_REPEAT: i32 = 2;
pub const EV_RELEASE: i32 = 3;

pub struct Key<'a> {
    pub key: i32,
    /// Unshifted key codepoint (lowercase letter, etc). 0 if unknown.
    pub base: u32,
    /// Text the key produces (empty when ctrl is held etc).
    pub text: &'a str,
    pub mods: u32,
    pub event: i32,
}

fn csi(buf: &mut Vec<u8>, s: &str) {
    buf.extend_from_slice(b"\x1b[");
    buf.extend_from_slice(s.as_bytes());
}

/// ";mods[:event]" suffix, or "" if both are default.
fn mod_suffix(mods: u32, event: i32, with_events: bool) -> String {
    let ev = if with_events && event != EV_PRESS { Some(event) } else { None };
    if mods == 0 && ev.is_none() {
        return String::new();
    }
    match ev {
        Some(e) => format!(";{}:{}", mods + 1, e),
        None => format!(";{}", mods + 1),
    }
}

fn ctrl_code(c: u32) -> Option<u8> {
    let ch = char::from_u32(c)?.to_ascii_lowercase();
    Some(match ch {
        'a'..='z' => ch as u8 - b'a' + 1,
        '@' | ' ' | '2' => 0,
        '[' | '3' => 27,
        '\\' | '4' => 28,
        ']' | '5' => 29,
        '^' | '6' => 30,
        '_' | '/' | '7' => 31,
        '8' | '?' => 127,
        _ => return None,
    })
}

pub fn encode_key(k: &Key, mode: TermMode) -> Option<Vec<u8>> {
    let kitty = mode.intersects(TermMode::KITTY_KEYBOARD_PROTOCOL);
    let report_all = mode.contains(TermMode::REPORT_ALL_KEYS_AS_ESC);
    let events = mode.contains(TermMode::REPORT_EVENT_TYPES);
    let assoc_text = mode.contains(TermMode::REPORT_ASSOCIATED_TEXT);
    let app_cursor = mode.contains(TermMode::APP_CURSOR);
    let m = k.mods;
    let is_release = k.event == EV_RELEASE;

    if is_release {
        if !(kitty && events) {
            return None;
        }
        if matches!(k.key, K_ENTER | K_TAB | K_BACKSPACE) && !report_all {
            return None;
        }
    }

    let mut out = Vec::with_capacity(16);
    let suffix = mod_suffix(m, k.event, kitty && events);

    // cursor-style keys: ESC [ 1 ; m X   or  ESC O X / ESC [ X
    let cursor_key = |letter: char, out: &mut Vec<u8>| {
        if suffix.is_empty() {
            if app_cursor && matches!(letter, 'A' | 'B' | 'C' | 'D' | 'H' | 'F') {
                out.extend_from_slice(&[0x1b, b'O', letter as u8]);
            } else {
                csi(out, &letter.to_string());
            }
        } else {
            csi(out, &format!("1{}{}", suffix, letter));
        }
    };
    let tilde_key = |n: u32, out: &mut Vec<u8>| csi(out, &format!("{}{}~", n, suffix));

    match k.key {
        K_UP => cursor_key('A', &mut out),
        K_DOWN => cursor_key('B', &mut out),
        K_RIGHT => cursor_key('C', &mut out),
        K_LEFT => cursor_key('D', &mut out),
        K_HOME => cursor_key('H', &mut out),
        K_END => cursor_key('F', &mut out),
        K_INSERT => tilde_key(2, &mut out),
        K_DELETE => tilde_key(3, &mut out),
        K_PGUP => tilde_key(5, &mut out),
        K_PGDN => tilde_key(6, &mut out),
        f if (K_F1..K_F1 + 24).contains(&f) => {
            let n = (f - K_F1 + 1) as usize;
            if n <= 4 {
                let letter = ['P', 'Q', 'R', 'S'][n - 1];
                if suffix.is_empty() {
                    out.extend_from_slice(&[0x1b, b'O', letter as u8]);
                } else {
                    csi(&mut out, &format!("1{}{}", suffix, letter));
                }
            } else {
                let codes = [15, 17, 18, 19, 20, 21, 23, 24, 25, 26, 28, 29, 31, 32, 33, 34, 42, 43, 44, 45];
                let code = *codes.get(n - 5)?;
                tilde_key(code, &mut out);
            }
        }
        K_ESCAPE => {
            if kitty || report_all {
                csi(&mut out, &format!("27{}u", suffix));
            } else {
                if m & MOD_ALT != 0 {
                    out.push(0x1b);
                }
                out.push(0x1b);
            }
        }
        K_ENTER | K_TAB | K_BACKSPACE => {
            let (cp, legacy): (u32, u8) = match k.key {
                K_ENTER => (13, b'\r'),
                K_TAB => (9, b'\t'),
                _ => (127, 0x7f),
            };
            if kitty && (report_all || (m & (MOD_SHIFT | MOD_ALT | MOD_CTRL | MOD_SUPER)) != 0) {
                // Shift+Tab with only shift is the classic CSI Z in legacy; kitty uses CSI 9;2 u.
                csi(&mut out, &format!("{}{}u", cp, suffix));
            } else {
                if k.key == K_TAB && m & MOD_SHIFT != 0 {
                    csi(&mut out, "Z");
                    return Some(out);
                }
                if m & MOD_ALT != 0 {
                    out.push(0x1b);
                }
                if k.key == K_BACKSPACE && m & MOD_CTRL != 0 {
                    out.push(0x08);
                } else {
                    out.push(legacy);
                }
            }
        }
        _ => {
            // character key
            let base = if k.base != 0 { k.base } else { k.text.chars().next().map(|c| c as u32).unwrap_or(0) };
            if kitty && base != 0 {
                let has_nontext_mod = m & (MOD_ALT | MOD_CTRL | MOD_SUPER) != 0;
                if report_all || has_nontext_mod {
                    let mut s = format!("{}{}", base, suffix);
                    if report_all && assoc_text && !is_release && !k.text.is_empty() && !has_nontext_mod {
                        if suffix.is_empty() {
                            s.push_str(";1");
                        }
                        s.push(';');
                        let cps: Vec<String> = k.text.chars().map(|c| (c as u32).to_string()).collect();
                        s.push_str(&cps.join(":"));
                    }
                    csi(&mut out, &format!("{}u", s));
                    return Some(out);
                }
            }
            if is_release {
                return None;
            }
            // legacy
            if m & MOD_CTRL != 0 {
                if let Some(c) = ctrl_code(base) {
                    if m & MOD_ALT != 0 {
                        out.push(0x1b);
                    }
                    out.push(c);
                    return Some(out);
                }
            }
            if !k.text.is_empty() {
                if m & MOD_ALT != 0 {
                    out.push(0x1b);
                }
                out.extend_from_slice(k.text.as_bytes());
            } else if base != 0 && m & MOD_ALT != 0 {
                let mut ch = char::from_u32(base)?;
                if m & MOD_SHIFT != 0 {
                    ch = ch.to_ascii_uppercase();
                }
                out.push(0x1b);
                let mut b = [0u8; 4];
                out.extend_from_slice(ch.encode_utf8(&mut b).as_bytes());
            } else {
                return None;
            }
        }
    }
    Some(out)
}

pub const ACT_PRESS: i32 = 0;
pub const ACT_RELEASE: i32 = 1;
pub const ACT_MOTION: i32 = 2;

/// button: 0 left, 1 middle, 2 right, 3 none (motion only), 64..=67 wheel.
pub fn encode_mouse(mode: TermMode, button: i32, action: i32, col: i32, row: i32, mods: u32) -> Option<Vec<u8>> {
    if !mode.intersects(TermMode::MOUSE_MODE) {
        return None;
    }
    if action == ACT_MOTION {
        let any = mode.contains(TermMode::MOUSE_MOTION);
        let drag = mode.contains(TermMode::MOUSE_DRAG);
        if button == 3 && !any {
            return None;
        }
        if button != 3 && !(any || drag) {
            return None;
        }
    }
    let wheel = button >= 64;
    if wheel && action == ACT_RELEASE {
        return None;
    }
    let sgr = mode.contains(TermMode::SGR_MOUSE);
    let mut cb = button;
    if action == ACT_RELEASE && !sgr {
        cb = 3;
    }
    if mods & MOD_SHIFT != 0 {
        cb |= 4;
    }
    if mods & MOD_ALT != 0 {
        cb |= 8;
    }
    if mods & MOD_CTRL != 0 {
        cb |= 16;
    }
    if action == ACT_MOTION {
        cb |= 32;
    }
    let (c, r) = (col.max(0) + 1, row.max(0) + 1);
    if sgr {
        let fin = if action == ACT_RELEASE { 'm' } else { 'M' };
        return Some(format!("\x1b[<{};{};{}{}", cb, c, r, fin).into_bytes());
    }
    let mut out = vec![0x1b, b'[', b'M', (32 + cb) as u8];
    if mode.contains(TermMode::UTF8_MOUSE) {
        for v in [c, r] {
            let ch = char::from_u32((32 + v) as u32)?;
            let mut b = [0u8; 4];
            out.extend_from_slice(ch.encode_utf8(&mut b).as_bytes());
        }
    } else {
        if 32 + c > 255 || 32 + r > 255 {
            return None;
        }
        out.push((32 + c) as u8);
        out.push((32 + r) as u8);
    }
    Some(out)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn k(key: i32, base: u32, text: &'static str, mods: u32) -> Key<'static> {
        Key { key, base, text, mods, event: EV_PRESS }
    }
    fn s(v: Option<Vec<u8>>) -> String {
        String::from_utf8_lossy(&v.unwrap()).replace('\x1b', "\\e")
    }
    #[test]
    fn legacy() {
        let m = TermMode::empty();
        assert_eq!(s(encode_key(&k(K_ENTER, 0, "", 0), m)), "\r");
        assert_eq!(s(encode_key(&k(K_ENTER, 0, "", MOD_SHIFT), m)), "\r"); // same as xterm.js
        assert_eq!(s(encode_key(&k(K_ENTER, 0, "", MOD_ALT), m)), "\\e\r");
        assert_eq!(s(encode_key(&k(K_UP, 0, "", 0), m)), "\\e[A");
        assert_eq!(s(encode_key(&k(K_UP, 0, "", MOD_CTRL), m)), "\\e[1;5A");
        assert_eq!(s(encode_key(&k(K_UP, 0, "", 0), TermMode::APP_CURSOR)), "\\eOA");
        assert_eq!(s(encode_key(&k(K_TAB, 0, "", MOD_SHIFT), m)), "\\e[Z");
        assert_eq!(s(encode_key(&k(K_CHAR, 'c' as u32, "", MOD_CTRL), m)), "\x03");
        assert_eq!(s(encode_key(&k(K_CHAR, 'b' as u32, "b", MOD_ALT), m)), "\\eb");
        assert_eq!(s(encode_key(&k(K_CHAR, 'a' as u32, "A", MOD_SHIFT), m)), "A");
        assert_eq!(s(encode_key(&k(K_DELETE, 0, "", 0), m)), "\\e[3~");
        assert_eq!(s(encode_key(&k(K_F1 + 4, 0, "", 0), m)), "\\e[15~");
        assert_eq!(s(encode_key(&k(K_F1, 0, "", MOD_SHIFT), m)), "\\e[1;2P");
        assert_eq!(s(encode_key(&k(K_CHAR, 0, "é", 0), m)), "é");
    }
    #[test]
    fn kitty() {
        let m = TermMode::DISAMBIGUATE_ESC_CODES;
        assert_eq!(s(encode_key(&k(K_ENTER, 0, "", MOD_SHIFT), m)), "\\e[13;2u");
        assert_eq!(s(encode_key(&k(K_ENTER, 0, "", 0), m)), "\r");
        assert_eq!(s(encode_key(&k(K_ESCAPE, 0, "", 0), m)), "\\e[27u");
        assert_eq!(s(encode_key(&k(K_CHAR, 'i' as u32, "", MOD_CTRL), m)), "\\e[105;5u");
        assert_eq!(s(encode_key(&k(K_CHAR, 'a' as u32, "A", MOD_SHIFT), m)), "A");
        let all = TermMode::DISAMBIGUATE_ESC_CODES | TermMode::REPORT_ALL_KEYS_AS_ESC | TermMode::REPORT_EVENT_TYPES;
        let rel = Key { key: K_CHAR, base: 'a' as u32, text: "a", mods: 0, event: EV_RELEASE };
        assert_eq!(s(encode_key(&rel, all)), "\\e[97;1:3u");
        assert!(encode_key(&rel, m).is_none());
    }
    #[test]
    fn mouse() {
        let sgr = TermMode::MOUSE_REPORT_CLICK | TermMode::SGR_MOUSE;
        assert_eq!(s(encode_mouse(sgr, 0, ACT_PRESS, 4, 9, 0)), "\\e[<0;5;10M");
        assert_eq!(s(encode_mouse(sgr, 0, ACT_RELEASE, 4, 9, 0)), "\\e[<0;5;10m");
        assert_eq!(s(encode_mouse(sgr, 64, ACT_PRESS, 0, 0, MOD_CTRL)), "\\e[<80;1;1M");
        assert!(encode_mouse(sgr, 3, ACT_MOTION, 0, 0, 0).is_none());
        assert!(encode_mouse(TermMode::empty(), 0, ACT_PRESS, 0, 0, 0).is_none());
    }
}
