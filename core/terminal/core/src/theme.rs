//! Palette + color resolution (replaces xterm.js ThemeService).
use alacritty_terminal::term::color::Colors;
use alacritty_terminal::vte::ansi::{Color, Rgb};

#[derive(Clone)]
pub struct Theme {
    pub fg: Rgb,
    pub bg: Rgb,
    pub cursor: Rgb,
    pub cursor_text: Rgb,
    pub sel_bg: Rgb,
    pub ansi: [Rgb; 16],
}

const fn rgb(r: u8, g: u8, b: u8) -> Rgb {
    Rgb { r, g, b }
}

impl Default for Theme {
    /// Same defaults as xterm.js (Tango palette, white on black).
    fn default() -> Self {
        Theme {
            fg: rgb(255, 255, 255),
            bg: rgb(0, 0, 0),
            cursor: rgb(255, 255, 255),
            cursor_text: rgb(0, 0, 0),
            sel_bg: rgb(77, 77, 77),
            ansi: [
                rgb(0x2e, 0x34, 0x36), rgb(0xcc, 0x00, 0x00), rgb(0x4e, 0x9a, 0x06), rgb(0xc4, 0xa0, 0x00),
                rgb(0x34, 0x65, 0xa4), rgb(0x75, 0x50, 0x7b), rgb(0x06, 0x98, 0x9a), rgb(0xd3, 0xd7, 0xcf),
                rgb(0x55, 0x57, 0x53), rgb(0xef, 0x29, 0x29), rgb(0x8a, 0xe2, 0x34), rgb(0xfc, 0xe9, 0x4f),
                rgb(0x72, 0x9f, 0xcf), rgb(0xad, 0x7f, 0xa8), rgb(0x34, 0xe2, 0xe2), rgb(0xee, 0xee, 0xec),
            ],
        }
    }
}

pub fn pack(c: Rgb) -> u32 {
    ((c.r as u32) << 16) | ((c.g as u32) << 8) | c.b as u32
}

pub fn unpack(v: u32) -> Rgb {
    rgb((v >> 16) as u8, (v >> 8) as u8, v as u8)
}

pub fn blend(top: Rgb, bottom: Rgb, alpha: f32) -> Rgb {
    let m = |t: u8, b: u8| (t as f32 * alpha + b as f32 * (1.0 - alpha)).round() as u8;
    rgb(m(top.r, bottom.r), m(top.g, bottom.g), m(top.b, bottom.b))
}

impl Theme {
    /// Color for palette index 0..=255 (16 ansi + 6x6x6 cube + 24 grays).
    pub fn indexed(&self, i: usize) -> Rgb {
        match i {
            0..=15 => self.ansi[i],
            16..=231 => {
                let n = i - 16;
                let lv = [0u8, 95, 135, 175, 215, 255];
                rgb(lv[n / 36 % 6], lv[n / 6 % 6], lv[n % 6])
            }
            _ => {
                let v = (8 + 10 * (i - 232)) as u8;
                rgb(v, v, v)
            }
        }
    }

    /// Resolve a cell color. `colors` holds runtime overrides set via OSC 4/10/11/12.
    pub fn resolve(&self, c: Color, is_fg: bool, bold: bool, colors: &Colors) -> Rgb {
        match c {
            Color::Spec(rgb) => rgb,
            Color::Indexed(i) => {
                let mut i = i as usize;
                if is_fg && bold && i < 8 {
                    i += 8; // drawBoldTextInBrightColors
                }
                colors[i].unwrap_or_else(|| self.indexed(i))
            }
            Color::Named(n) => {
                let mut i = n as usize;
                if let Some(o) = colors[i] {
                    return o;
                }
                if is_fg && bold && i < 8 {
                    i += 8;
                }
                match i {
                    0..=15 => self.ansi[i],
                    256 | 267 => self.fg,
                    257 => self.bg,
                    258 => self.cursor,
                    259..=266 => blend(self.ansi[i - 259], self.bg, 0.5), // dim ansi
                    268 => blend(self.fg, self.bg, 0.5),
                    _ => self.fg,
                }
            }
        }
    }
}
