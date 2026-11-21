use std::cell::RefCell;
use std::collections::HashMap;
use std::fs::File;
use std::io::{self, BufRead, BufReader};
use std::path::Path;

use regex::{Regex, RegexBuilder};

use crate::ext4::types::{
    S_IFBLK, S_IFCHR, S_IFDIR, S_IFIFO, S_IFLNK, S_IFMT, S_IFREG, S_IFSOCK,
};

#[derive(Clone, Debug)]
enum CompiledMatcher {
    Exact(String),
    Regex(Regex),
}

struct Spec {
    regex_str: String,
    ctx_raw: String,
    mode: u32,
    has_meta_chars: bool,
    stem_id: Option<usize>,
    matcher: RefCell<Option<CompiledMatcher>>,
}

pub struct FileContexts {
    specs: Vec<Spec>,
    stems: Vec<String>,
}

fn get_stem_from_spec(buf: &str) -> Option<&str> {
    if buf.len() <= 1 {
        return None;
    }
    let rest = &buf[1..];
    let pos = rest.find('/')?;
    let stem_len = 1 + pos;
    let stem = &buf[..stem_len];
    if stem
        .bytes()
        .any(|b| matches!(b, b'.' | b'^' | b'$' | b'?' | b'*' | b'+' | b'|' | b'[' | b'(' | b'{'))
    {
        return None;
    }
    Some(stem)
}

fn get_stem_from_file_name(buf: &str) -> Option<&str> {
    if buf.len() <= 1 {
        return None;
    }
    let pos = buf[1..].find('/')?;
    Some(&buf[..1 + pos])
}

fn has_meta_chars(s: &str) -> bool {
    let bytes = s.as_bytes();
    let mut i = 0;
    while i < bytes.len() {
        match bytes[i] {
            b'.' | b'^' | b'$' | b'?' | b'*' | b'+' | b'|' | b'[' | b'(' | b'{' => return true,
            b'\\' => i += 1,
            _ => {}
        }
        i += 1;
    }
    false
}

/// Translates a POSIX ERE pattern into a Rust `regex` pattern anchored with `^...$`.
fn posix_ere_to_rust_regex(pattern: &str) -> String {
    let mut out = String::with_capacity(pattern.len() + 8);
    out.push('^');
    let chars: Vec<char> = pattern.chars().collect();
    let mut i = 0;
    let mut in_class = false;

    while i < chars.len() {
        let c = chars[i];
        if in_class {
            if c == '\\' {
                // In POSIX ERE, '\' inside [...] is literal
                out.push_str("\\\\");
                i += 1;
                continue;
            }
            if c == '[' && i + 1 < chars.len() && matches!(chars[i + 1], ':' | '.' | '=') {
                let close_tag = chars[i + 1];
                out.push('[');
                out.push(close_tag);
                i += 2;
                while i < chars.len() {
                    let ch = chars[i];
                    out.push(ch);
                    if ch == close_tag && i + 1 < chars.len() && chars[i + 1] == ']' {
                        out.push(']');
                        i += 2;
                        break;
                    }
                    i += 1;
                }
                continue;
            }
            if c == ']' {
                in_class = false;
            }
            out.push(c);
            i += 1;
        } else if c == '\\' {
            if i + 1 < chars.len() {
                let next = chars[i + 1];
                if matches!(
                    next,
                    '.' | '^' | '$' | '?' | '*' | '+' | '|' | '[' | ']' | '(' | ')' | '{' | '}' | '\\' | '/' | '-'
                ) {
                    out.push('\\');
                    out.push(next);
                } else {
                    out.push(next);
                }
                i += 2;
            } else {
                out.push_str("\\\\");
                i += 1;
            }
        } else if c == '[' {
            in_class = true;
            out.push('[');
            i += 1;
            if i < chars.len() && chars[i] == '^' {
                out.push('^');
                i += 1;
            }
            if i < chars.len() && chars[i] == ']' {
                // Literal ']' as first char in class
                out.push_str("\\]");
                i += 1;
            }
        } else if c == '{' {
            let is_quant = i + 1 < chars.len() && chars[i + 1].is_ascii_digit();
            if is_quant {
                out.push('{');
            } else {
                out.push_str("\\{");
            }
            i += 1;
        } else {
            out.push(c);
            i += 1;
        }
    }

    out.push('$');
    out
}

fn compile_matcher(reg_buf: &str, has_meta: bool) -> Result<CompiledMatcher, String> {
    if !has_meta && !reg_buf.contains('\\') {
        return Ok(CompiledMatcher::Exact(reg_buf.to_string()));
    }
    let pat = posix_ere_to_rust_regex(reg_buf);
    let re = RegexBuilder::new(&pat)
        .dot_matches_new_line(true)
        .build()
        .map_err(|e| format!("invalid regex {}: {}", reg_buf, e))?;
    Ok(CompiledMatcher::Regex(re))
}

impl FileContexts {
    pub fn open(path: &Path) -> io::Result<Self> {
        let f = File::open(path)?;
        let meta = f.metadata()?;
        if !meta.is_file() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "file_contexts is not a regular file",
            ));
        }

        let mut specs = Vec::new();
        let mut stems: Vec<String> = Vec::new();
        let mut stem_map: HashMap<String, usize> = HashMap::new();

        Self::read_file_into(path, f, true, &mut specs, &mut stems, &mut stem_map)?;

        let homedirs = Path::new(&format!("{}.homedirs", path.display())).to_path_buf();
        if let Ok(hf) = File::open(&homedirs) {
            Self::read_file_into(&homedirs, hf, false, &mut specs, &mut stems, &mut stem_map)?;
        }

        let local = Path::new(&format!("{}.local", path.display())).to_path_buf();
        if let Ok(lf) = File::open(&local) {
            Self::read_file_into(&local, lf, false, &mut specs, &mut stems, &mut stem_map)?;
        }

        // Move exact pathname specifications (has_meta_chars == false) to the end, preserving relative order.
        let mut reordered = Vec::with_capacity(specs.len());
        let mut exact = Vec::new();
        for s in specs {
            if s.has_meta_chars {
                reordered.push(s);
            } else {
                exact.push(s);
            }
        }
        reordered.extend(exact);

        Ok(Self {
            specs: reordered,
            stems,
        })
    }

    fn read_file_into(
        path: &Path,
        file: File,
        check_dups: bool,
        specs: &mut Vec<Spec>,
        stems: &mut Vec<String>,
        stem_map: &mut HashMap<String, usize>,
    ) -> io::Result<()> {
        let reader = BufReader::new(file);
        let mut seen: HashMap<String, Vec<(u32, String)>> = HashMap::new();

        for (idx, line_res) in reader.lines().enumerate() {
            let lineno = idx + 1;
            let line = line_res?;
            let trimmed = line.trim_start();
            if trimmed.is_empty() || trimmed.starts_with('#') {
                continue;
            }
            let mut parts = line.split_ascii_whitespace();
            let Some(regex_tok) = parts.next() else {
                continue;
            };
            let Some(second_tok) = parts.next() else {
                eprintln!("{}:  line {} is missing fields, skipping", path.display(), lineno);
                continue;
            };
            let (type_str, ctx_tok) = match parts.next() {
                Some(third_tok) => (Some(second_tok), third_tok),
                None => (None, second_tok),
            };

            let mode = if let Some(t) = type_str {
                if !t.starts_with('-') || t.len() != 2 {
                    eprintln!(
                        "{}:  line {} has invalid file type {}",
                        path.display(),
                        lineno,
                        t
                    );
                    continue;
                }
                match t.as_bytes()[1] {
                    b'b' => S_IFBLK,
                    b'c' => S_IFCHR,
                    b'd' => S_IFDIR,
                    b'p' => S_IFIFO,
                    b'l' => S_IFLNK,
                    #[cfg(not(windows))]
                    b's' => S_IFSOCK,
                    b'-' => S_IFREG,
                    _ => {
                        eprintln!(
                            "{}:  line {} has invalid file type {}",
                            path.display(),
                            lineno,
                            t
                        );
                        continue;
                    }
                }
            } else {
                0
            };

            let stem_id = if let Some(stem) = get_stem_from_spec(regex_tok) {
                if let Some(&id) = stem_map.get(stem) {
                    Some(id)
                } else {
                    let id = stems.len();
                    stems.push(stem.to_string());
                    stem_map.insert(stem.to_string(), id);
                    Some(id)
                }
            } else {
                None
            };

            if check_dups {
                let list = seen.entry(regex_tok.to_string()).or_default();
                for &(prev_mode, ref prev_ctx) in list.iter() {
                    if prev_mode == 0 || mode == 0 || prev_mode == mode {
                        if prev_ctx != ctx_tok {
                            eprintln!(
                                "{}: Multiple different specifications for {}  ({} and {}).",
                                path.display(),
                                regex_tok,
                                ctx_tok,
                                prev_ctx
                            );
                        } else {
                            eprintln!(
                                "{}: Multiple same specifications for {}.",
                                path.display(),
                                regex_tok
                            );
                        }
                        return Err(io::Error::new(
                            io::ErrorKind::InvalidData,
                            "duplicate file_contexts specification",
                        ));
                    }
                }
                list.push((mode, ctx_tok.to_string()));
            }

            let has_meta = has_meta_chars(regex_tok);
            specs.push(Spec {
                regex_str: regex_tok.to_string(),
                ctx_raw: ctx_tok.to_string(),
                mode,
                has_meta_chars: has_meta,
                stem_id,
                matcher: RefCell::new(None),
            });
        }

        Ok(())
    }

    pub fn lookup(&self, key: &str, file_type: u32) -> Option<String> {
        if self.specs.is_empty() {
            return None;
        }

        let cleaned;
        let key_ref = if key.contains("//") {
            let mut s = String::with_capacity(key.len());
            let mut prev_slash = false;
            for ch in key.chars() {
                if ch == '/' {
                    if !prev_slash {
                        s.push('/');
                    }
                    prev_slash = true;
                } else {
                    prev_slash = false;
                    s.push(ch);
                }
            }
            cleaned = s;
            cleaned.as_str()
        } else {
            key
        };

        let (file_stem_id, tail) = if let Some(stem) = get_stem_from_file_name(key_ref) {
            if let Some(id) = self.stems.iter().position(|s| s == stem) {
                (Some(id), &key_ref[stem.len()..])
            } else {
                (None, key_ref)
            }
        } else {
            (None, key_ref)
        };

        let mode = file_type & S_IFMT;

        for spec in self.specs.iter().rev() {
            if (spec.stem_id.is_none() || spec.stem_id == file_stem_id)
                && (mode == 0 || spec.mode == 0 || mode == spec.mode)
            {
                let mut m_ref = spec.matcher.borrow_mut();
                if m_ref.is_none() {
                    let reg_buf = match spec.stem_id {
                        Some(id) => &spec.regex_str[self.stems[id].len()..],
                        None => spec.regex_str.as_str(),
                    };
                    match compile_matcher(reg_buf, spec.has_meta_chars) {
                        Ok(compiled) => *m_ref = Some(compiled),
                        Err(_) => return None,
                    }
                }
                let target = if spec.stem_id.is_some() { tail } else { key_ref };
                let matched = match m_ref.as_ref().unwrap() {
                    CompiledMatcher::Exact(exact) => target == exact,
                    CompiledMatcher::Regex(re) => re.is_match(target),
                };
                if matched {
                    if spec.ctx_raw == "<<none>>" {
                        return None;
                    }
                    return Some(spec.ctx_raw.clone());
                }
            }
        }

        None
    }
}

#[allow(dead_code)]
fn _keep_sock() {
    let _ = S_IFSOCK;
}
