// SPDX-License-Identifier: MPL-2.0
//! Native CommonMark + GFM Markdown parser and Qt RichText / structured block renderer.
//! Detaches Python's GIL while parsing and formatting so large documents stay responsive.

use pyo3::exceptions::PyOSError;
use pyo3::prelude::*;
use serde::{Deserialize, Serialize};
use std::collections::HashSet;
use std::fs;
use std::path::PathBuf;

#[derive(Clone, Deserialize)]
#[serde(default)]
pub(crate) struct MarkdownTheme {
    is_light: bool,
    fg: String,
    fg_bright: String,
    fg_muted: String,
    accent: String,
    code_bg: String,
    code_fg: String,
    quote_fg: String,
    link: String,
}

impl Default for MarkdownTheme {
    fn default() -> Self {
        Self {
            is_light: false,
            fg: "#e2e8f0".to_owned(),
            fg_bright: "#ffffff".to_owned(),
            fg_muted: "#94a3b8".to_owned(),
            accent: "#38bdf8".to_owned(),
            code_bg: "#161b22".to_owned(),
            code_fg: "#e6edf3".to_owned(),
            quote_fg: "#cbd5e1".to_owned(),
            link: "#38bdf8".to_owned(),
        }
    }
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct HeadingItem {
    level: u8,
    text: String,
    anchor: String,
    block_index: usize,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct ListItem {
    depth: usize,
    ordered: bool,
    marker: String,
    checked: Option<bool>,
    html: String,
    text: String,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct TableRow {
    cells_html: Vec<String>,
    cells_text: Vec<String>,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct MarkdownBlock {
    kind: &'static str,
    level: u8,
    anchor: String,
    text: String,
    html: String,
    language: String,
    code: String,
    url: String,
    alt: String,
    items: Vec<ListItem>,
    headers_html: Vec<String>,
    headers_text: Vec<String>,
    alignments: Vec<&'static str>,
    rows: Vec<TableRow>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct MarkdownStats {
    headings_count: usize,
    words: usize,
    lines: usize,
    read_minutes: usize,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct MarkdownDocumentData {
    title: String,
    blocks: Vec<MarkdownBlock>,
    headings: Vec<HeadingItem>,
    stats: MarkdownStats,
}

fn empty_block(kind: &'static str) -> MarkdownBlock {
    MarkdownBlock {
        kind,
        level: 0,
        anchor: String::new(),
        text: String::new(),
        html: String::new(),
        language: String::new(),
        code: String::new(),
        url: String::new(),
        alt: String::new(),
        items: Vec::new(),
        headers_html: Vec::new(),
        headers_text: Vec::new(),
        alignments: Vec::new(),
        rows: Vec::new(),
    }
}

fn escape_html(input: &str) -> String {
    let mut out = String::with_capacity(input.len() + 8);
    for ch in input.chars() {
        match ch {
            '&' => out.push_str("&amp;"),
            '<' => out.push_str("&lt;"),
            '>' => out.push_str("&gt;"),
            '"' => out.push_str("&quot;"),
            '\'' => out.push_str("&#39;"),
            _ => out.push(ch),
        }
    }
    out
}

fn slugify(text: &str, seen: &mut HashSet<String>) -> String {
    let mut base = String::new();
    let mut prev_dash = false;
    for ch in text.chars() {
        if ch.is_alphanumeric() {
            for lc in ch.to_lowercase() {
                base.push(lc);
            }
            prev_dash = false;
        } else if (ch == ' ' || ch == '-' || ch == '_') && !base.is_empty() && !prev_dash {
            base.push('-');
            prev_dash = true;
        }
    }
    while base.ends_with('-') {
        base.pop();
    }
    if base.is_empty() {
        base = "section".to_owned();
    }
    let mut candidate = base.clone();
    let mut idx = 2usize;
    while !seen.insert(candidate.clone()) {
        candidate = format!("{}-{}", base, idx);
        idx += 1;
    }
    candidate
}

fn strip_inline_plain(input: &str) -> String {
    let mut out = String::with_capacity(input.len());
    let chars: Vec<char> = input.chars().collect();
    let mut i = 0;
    while i < chars.len() {
        if chars[i] == '\\' && i + 1 < chars.len() {
            out.push(chars[i + 1]);
            i += 2;
            continue;
        }
        if chars[i] == '`' {
            let mut ticks = 0;
            while i + ticks < chars.len() && chars[i + ticks] == '`' {
                ticks += 1;
            }
            let mut j = i + ticks;
            let mut found = None;
            while j + ticks <= chars.len() {
                if chars[j..j + ticks].iter().all(|&c| c == '`')
                    && (j + ticks == chars.len() || chars[j + ticks] != '`')
                {
                    found = Some(j);
                    break;
                }
                j += 1;
            }
            if let Some(end) = found {
                for &c in &chars[i + ticks..end] {
                    out.push(c);
                }
                i = end + ticks;
                continue;
            }
        }
        if chars[i] == '[' || (chars[i] == '!' && i + 1 < chars.len() && chars[i + 1] == '[') {
            if let Some((label, _url, next_idx)) = parse_link_or_image(&chars, i) {
                out.push_str(&strip_inline_plain(&label));
                i = next_idx;
                continue;
            }
        }
        if matches!(chars[i], '*' | '_' | '~') {
            if i + 1 < chars.len() && chars[i + 1] == chars[i] {
                i += 2;
                continue;
            }
            if chars[i] != '~' {
                i += 1;
                continue;
            }
        }
        out.push(chars[i]);
        i += 1;
    }
    out
}

fn parse_link_or_image(chars: &[char], start: usize) -> Option<(String, String, usize)> {
    let bracket_start = if chars[start] == '!' { start + 1 } else { start };
    if bracket_start >= chars.len() || chars[bracket_start] != '[' {
        return None;
    }
    let mut depth = 0i32;
    let mut j = bracket_start;
    let mut label_end = None;
    while j < chars.len() {
        if chars[j] == '\\' && j + 1 < chars.len() {
            j += 2;
            continue;
        }
        if chars[j] == '[' {
            depth += 1;
        } else if chars[j] == ']' {
            depth -= 1;
            if depth == 0 {
                label_end = Some(j);
                break;
            }
        }
        j += 1;
    }
    let close_bracket = label_end?;
    if close_bracket + 1 >= chars.len() || chars[close_bracket + 1] != '(' {
        return None;
    }
    let mut k = close_bracket + 2;
    let mut paren_depth = 1i32;
    let mut url_end = None;
    while k < chars.len() {
        if chars[k] == '\\' && k + 1 < chars.len() {
            k += 2;
            continue;
        }
        if chars[k] == '(' {
            paren_depth += 1;
        } else if chars[k] == ')' {
            paren_depth -= 1;
            if paren_depth == 0 {
                url_end = Some(k);
                break;
            }
        }
        k += 1;
    }
    let close_paren = url_end?;
    let label: String = chars[bracket_start + 1..close_bracket].iter().collect();
    let raw_url: String = chars[close_bracket + 2..close_paren].iter().collect();
    let mut url = raw_url.trim().to_owned();
    if let Some((first, _)) = url.split_once(char::is_whitespace) {
        url = first.to_owned();
    }
    if url.starts_with('<') && url.ends_with('>') && url.len() >= 2 {
        url = url[1..url.len() - 1].to_owned();
    }
    Some((label, url, close_paren + 1))
}

fn find_closing_delim(chars: &[char], from: usize, delim: &str) -> Option<usize> {
    let dchars: Vec<char> = delim.chars().collect();
    let len = dchars.len();
    let mut i = from;
    while i + len <= chars.len() {
        if chars[i] == '\\' && i + 1 < chars.len() {
            i += 2;
            continue;
        }
        if chars[i] == '`' {
            let mut ticks = 0;
            while i + ticks < chars.len() && chars[i + ticks] == '`' {
                ticks += 1;
            }
            let mut j = i + ticks;
            while j + ticks <= chars.len() {
                if chars[j..j + ticks].iter().all(|&c| c == '`') {
                    j += ticks;
                    break;
                }
                j += 1;
            }
            i = j;
            continue;
        }
        if chars[i..i + len] == dchars[..] {
            // Avoid matching empty content or middle of word for '_'
            if i > from {
                if dchars[0] == '_' {
                    let next_alnum = i + len < chars.len() && chars[i + len].is_alphanumeric();
                    if next_alnum {
                        i += 1;
                        continue;
                    }
                }
                return Some(i);
            }
        }
        i += 1;
    }
    None
}

fn render_inline(input: &str, theme: &MarkdownTheme) -> String {
    let chars: Vec<char> = input.chars().collect();
    let mut out = String::with_capacity(input.len() * 2);
    let mut i = 0;
    let inline_code_style = format!(
        "font-family:'JetBrains Mono','Cascadia Mono','Consolas'; background-color:{}; color:{};",
        theme.code_bg, theme.code_fg
    );
    let link_style = format!("color:{}; text-decoration:underline;", theme.link);

    while i < chars.len() {
        // Escaped punctuation
        if chars[i] == '\\' && i + 1 < chars.len() {
            let next = chars[i + 1];
            if next == '\n' {
                out.push_str("<br/>");
                i += 2;
                continue;
            }
            if next.is_ascii_punctuation() {
                out.push_str(&escape_html(&next.to_string()));
                i += 2;
                continue;
            }
        }

        // Line breaks (two spaces before newline)
        if chars[i] == '\n' {
            out.push_str("<br/>");
            i += 1;
            continue;
        }

        // Inline code spans
        if chars[i] == '`' {
            let mut ticks = 0;
            while i + ticks < chars.len() && chars[i + ticks] == '`' {
                ticks += 1;
            }
            let mut j = i + ticks;
            let mut found = None;
            while j + ticks <= chars.len() {
                if chars[j..j + ticks].iter().all(|&c| c == '`')
                    && (j + ticks == chars.len() || chars[j + ticks] != '`')
                {
                    found = Some(j);
                    break;
                }
                j += 1;
            }
            if let Some(end) = found {
                let raw: String = chars[i + ticks..end].iter().collect();
                let trimmed = if raw.len() >= 2 && raw.starts_with(' ') && raw.ends_with(' ') && raw.chars().any(|c| c != ' ') {
                    &raw[1..raw.len() - 1]
                } else {
                    &raw
                };
                out.push_str("<span style=\"");
                out.push_str(&inline_code_style);
                out.push_str("\">&nbsp;");
                out.push_str(&escape_html(trimmed));
                out.push_str("&nbsp;</span>");
                i = end + ticks;
                continue;
            }
        }

        // Image ![alt](url) inline fallback
        if chars[i] == '!' && i + 1 < chars.len() && chars[i + 1] == '[' {
            if let Some((alt, url, next_idx)) = parse_link_or_image(&chars, i) {
                out.push_str("<a href=\"");
                out.push_str(&escape_html(&url));
                out.push_str("\" style=\"");
                out.push_str(&link_style);
                out.push_str("\">[");
                out.push_str(&escape_html(if alt.is_empty() { "image" } else { &alt }));
                out.push_str("]</a>");
                i = next_idx;
                continue;
            }
        }

        // Link [label](url)
        if chars[i] == '[' {
            if let Some((label, url, next_idx)) = parse_link_or_image(&chars, i) {
                out.push_str("<a href=\"");
                out.push_str(&escape_html(&url));
                out.push_str("\" style=\"");
                out.push_str(&link_style);
                out.push_str("\">");
                out.push_str(&render_inline(&label, theme));
                out.push_str("</a>");
                i = next_idx;
                continue;
            }
        }

        // Autolink <https://...>
        if chars[i] == '<' {
            let mut k = i + 1;
            while k < chars.len() && chars[k] != '>' && chars[k] != '\n' && !chars[k].is_whitespace() {
                k += 1;
            }
            if k < chars.len() && chars[k] == '>' {
                let inside: String = chars[i + 1..k].iter().collect();
                if inside.starts_with("http://") || inside.starts_with("https://") || inside.starts_with("mailto:") {
                    out.push_str("<a href=\"");
                    out.push_str(&escape_html(&inside));
                    out.push_str("\" style=\"");
                    out.push_str(&link_style);
                    out.push_str("\">");
                    out.push_str(&escape_html(&inside));
                    out.push_str("</a>");
                    i = k + 1;
                    continue;
                }
            }
        }

        // Bare URL autolink
        if (chars[i] == 'h')
            && (chars[i..].starts_with(&['h', 't', 't', 'p', ':', '/', '/'])
                || chars[i..].starts_with(&['h', 't', 't', 'p', 's', ':', '/', '/']))
        {
            let prev_ok = i == 0 || !chars[i - 1].is_alphanumeric();
            if prev_ok {
                let mut k = i;
                while k < chars.len() && !chars[k].is_whitespace() && !matches!(chars[k], '<' | '>' | '"' | '\'') {
                    k += 1;
                }
                while k > i && matches!(chars[k - 1], '.' | ',' | ';' | ':' | '!' | '?' | ')') {
                    k -= 1;
                }
                let url: String = chars[i..k].iter().collect();
                out.push_str("<a href=\"");
                out.push_str(&escape_html(&url));
                out.push_str("\" style=\"");
                out.push_str(&link_style);
                out.push_str("\">");
                out.push_str(&escape_html(&url));
                out.push_str("</a>");
                i = k;
                continue;
            }
        }

        // Bold + Italic *** or ___
        if i + 2 < chars.len()
            && ((chars[i] == '*' && chars[i + 1] == '*' && chars[i + 2] == '*')
                || (chars[i] == '_' && chars[i + 1] == '_' && chars[i + 2] == '_'))
        {
            let delim: String = chars[i..i + 3].iter().collect();
            if let Some(end) = find_closing_delim(&chars, i + 3, &delim) {
                let inner: String = chars[i + 3..end].iter().collect();
                out.push_str("<b><i>");
                out.push_str(&render_inline(&inner, theme));
                out.push_str("</i></b>");
                i = end + 3;
                continue;
            }
        }

        // Bold ** or __
        if i + 1 < chars.len()
            && ((chars[i] == '*' && chars[i + 1] == '*')
                || (chars[i] == '_' && chars[i + 1] == '_'))
        {
            let prev_alnum = chars[i] == '_' && i > 0 && chars[i - 1].is_alphanumeric();
            if !prev_alnum {
                let delim: String = chars[i..i + 2].iter().collect();
                if let Some(end) = find_closing_delim(&chars, i + 2, &delim) {
                    let inner: String = chars[i + 2..end].iter().collect();
                    out.push_str("<b>");
                    out.push_str(&render_inline(&inner, theme));
                    out.push_str("</b>");
                    i = end + 2;
                    continue;
                }
            }
        }

        // Strikethrough ~~
        if i + 1 < chars.len() && chars[i] == '~' && chars[i + 1] == '~' {
            if let Some(end) = find_closing_delim(&chars, i + 2, "~~") {
                let inner: String = chars[i + 2..end].iter().collect();
                out.push_str("<s>");
                out.push_str(&render_inline(&inner, theme));
                out.push_str("</s>");
                i = end + 2;
                continue;
            }
        }

        // Italic * or _
        if chars[i] == '*' || chars[i] == '_' {
            let prev_alnum = chars[i] == '_' && i > 0 && chars[i - 1].is_alphanumeric();
            let next_space = i + 1 >= chars.len() || chars[i + 1].is_whitespace();
            if !prev_alnum && !next_space {
                let delim = chars[i].to_string();
                if let Some(end) = find_closing_delim(&chars, i + 1, &delim) {
                    let inner: String = chars[i + 1..end].iter().collect();
                    out.push_str("<i>");
                    out.push_str(&render_inline(&inner, theme));
                    out.push_str("</i>");
                    i = end + 1;
                    continue;
                }
            }
        }

        match chars[i] {
            '&' => out.push_str("&amp;"),
            '<' => out.push_str("&lt;"),
            '>' => out.push_str("&gt;"),
            '"' => out.push_str("&quot;"),
            '\'' => out.push_str("&#39;"),
            ch => out.push(ch),
        }
        i += 1;
    }
    out
}

fn highlight_code_html(code: &str, language: &str, theme: &MarkdownTheme) -> String {
    let kw_color = if theme.is_light { "#8250df" } else { "#c084fc" };
    let str_color = if theme.is_light { "#0a3069" } else { "#7dd3fc" };
    let cmt_color = theme.fg_muted.as_str();
    let num_color = if theme.is_light { "#953800" } else { "#fbbf24" };

    let lang = language.to_ascii_lowercase();
    let keywords: &[&str] = match lang.as_str() {
        "rust" | "rs" => &[
            "fn", "let", "mut", "pub", "struct", "enum", "impl", "trait", "for", "in",
            "while", "loop", "if", "else", "match", "return", "use", "mod", "crate",
            "where", "async", "await", "const", "static", "type", "true", "false", "self", "Self",
        ],
        "python" | "py" => &[
            "def", "class", "import", "from", "as", "if", "elif", "else", "for", "in",
            "while", "return", "yield", "try", "except", "finally", "with", "async",
            "await", "pass", "break", "continue", "True", "False", "None", "and", "or", "not",
        ],
        "bash" | "sh" | "zsh" | "shell" => &[
            "if", "then", "else", "elif", "fi", "for", "in", "do", "done", "while",
            "case", "esac", "function", "export", "local", "return", "exit", "echo", "sudo",
        ],
        "js" | "javascript" | "ts" | "typescript" | "qml" => &[
            "function", "const", "let", "var", "if", "else", "for", "while", "return",
            "import", "from", "export", "class", "property", "readonly", "signal",
            "true", "false", "null", "undefined", "async", "await",
        ],
        "c" | "cpp" | "c++" | "java" | "kotlin" | "kt" | "go" => &[
            "fn", "func", "void", "int", "bool", "char", "struct", "class", "enum",
            "public", "private", "if", "else", "for", "while", "switch", "case", "return",
            "import", "package", "include", "const", "true", "false", "null",
        ],
        _ => &[],
    };

    if keywords.is_empty() && !matches!(lang.as_str(), "json" | "jsonc" | "yaml" | "yml" | "toml" | "ini" | "prop" | "properties") {
        return escape_html(code);
    }

    let mut out = String::with_capacity(code.len() * 2);
    for (line_idx, line) in code.split('\n').enumerate() {
        if line_idx > 0 {
            out.push('\n');
        }
        let trimmed = line.trim_start();
        if trimmed.starts_with('#') || trimmed.starts_with("//") {
            out.push_str("<span style=\"color:");
            out.push_str(cmt_color);
            out.push_str(";\">");
            out.push_str(&escape_html(line));
            out.push_str("</span>");
            continue;
        }
        let chars: Vec<char> = line.chars().collect();
        let mut i = 0;
        while i < chars.len() {
            if (chars[i] == '"' || chars[i] == '\'') && (i == 0 || chars[i - 1] != '\\') {
                let quote = chars[i];
                let mut j = i + 1;
                while j < chars.len() {
                    if chars[j] == '\\' && j + 1 < chars.len() {
                        j += 2;
                        continue;
                    }
                    if chars[j] == quote {
                        j += 1;
                        break;
                    }
                    j += 1;
                }
                let s: String = chars[i..j].iter().collect();
                out.push_str("<span style=\"color:");
                out.push_str(str_color);
                out.push_str(";\">");
                out.push_str(&escape_html(&s));
                out.push_str("</span>");
                i = j;
                continue;
            }
            if chars[i] == '/' && i + 1 < chars.len() && chars[i + 1] == '/' {
                let s: String = chars[i..].iter().collect();
                out.push_str("<span style=\"color:");
                out.push_str(cmt_color);
                out.push_str(";\">");
                out.push_str(&escape_html(&s));
                out.push_str("</span>");
                break;
            }
            if chars[i].is_ascii_alphabetic() || chars[i] == '_' {
                let mut j = i + 1;
                while j < chars.len() && (chars[j].is_ascii_alphanumeric() || chars[j] == '_') {
                    j += 1;
                }
                let word: String = chars[i..j].iter().collect();
                if keywords.contains(&word.as_str()) {
                    out.push_str("<span style=\"color:");
                    out.push_str(kw_color);
                    out.push_str("; font-weight:600;\">");
                    out.push_str(&escape_html(&word));
                    out.push_str("</span>");
                } else {
                    out.push_str(&escape_html(&word));
                }
                i = j;
                continue;
            }
            if chars[i].is_ascii_digit() && (i == 0 || !chars[i - 1].is_ascii_alphanumeric()) {
                let mut j = i + 1;
                while j < chars.len() && (chars[j].is_ascii_alphanumeric() || chars[j] == '.' || chars[j] == '_') {
                    j += 1;
                }
                let num: String = chars[i..j].iter().collect();
                out.push_str("<span style=\"color:");
                out.push_str(num_color);
                out.push_str(";\">");
                out.push_str(&escape_html(&num));
                out.push_str("</span>");
                i = j;
                continue;
            }
            out.push_str(&escape_html(&chars[i].to_string()));
            i += 1;
        }
    }
    out
}

fn is_horizontal_rule(trimmed: &str) -> bool {
    let compact: String = trimmed.chars().filter(|c| !c.is_whitespace()).collect();
    if compact.len() < 3 {
        return false;
    }
    let first = compact.chars().next().unwrap();
    matches!(first, '-' | '*' | '_') && compact.chars().all(|c| c == first)
}

fn parse_atx_heading(trimmed: &str) -> Option<(u8, String)> {
    let mut level = 0usize;
    for ch in trimmed.chars() {
        if ch == '#' && level < 6 {
            level += 1;
        } else {
            break;
        }
    }
    if level == 0 {
        return None;
    }
    let rest = &trimmed[level..];
    if !rest.is_empty() && !rest.starts_with(char::is_whitespace) {
        return None;
    }
    let mut text = rest.trim().to_owned();
    let trimmed_end = text.trim_end_matches('#').trim_end();
    if trimmed_end.len() < text.len() && (trimmed_end.is_empty() || text[trimmed_end.len()..].starts_with('#')) {
        let hashes_removed = text[trimmed_end.len()..].chars().all(|c| c == '#');
        if hashes_removed && (trimmed_end.is_empty() || trimmed_end.ends_with(char::is_whitespace) || text.chars().all(|c| c == '#')) {
            text = trimmed_end.trim_end().to_owned();
        }
    }
    Some((level as u8, text))
}

fn parse_fence_open(trimmed: &str) -> Option<(char, usize, String)> {
    let first = trimmed.chars().next()?;
    if first != '`' && first != '~' {
        return None;
    }
    let count = trimmed.chars().take_while(|&c| c == first).count();
    if count < 3 {
        return None;
    }
    let info = trimmed[count..].trim();
    if first == '`' && info.contains('`') {
        return None;
    }
    let lang = info.split_whitespace().next().unwrap_or("").to_owned();
    Some((first, count, lang))
}

fn is_fence_close(trimmed: &str, ch: char, min_len: usize) -> bool {
    let count = trimmed.chars().take_while(|&c| c == ch).count();
    count >= min_len && trimmed[count..].trim().is_empty()
}

fn parse_list_line(line: &str) -> Option<(usize, bool, String, Option<bool>, String)> {
    let indent = line.chars().take_while(|c| *c == ' ' || *c == '\t').map(|c| if c == '\t' { 4 } else { 1 }).sum::<usize>();
    let rest = line.trim_start();
    let (ordered, marker, after_marker) = if let Some(stripped) = rest.strip_prefix("- ").or_else(|| rest.strip_prefix("* ")).or_else(|| rest.strip_prefix("+ ")) {
        (false, "•".to_owned(), stripped)
    } else {
        let digits = rest.chars().take_while(|c| c.is_ascii_digit()).count();
        if digits == 0 || digits > 9 {
            return None;
        }
        let after_digits = &rest[digits..];
        let sep = if after_digits.starts_with(". ") { '.' } else if after_digits.starts_with(") ") { ')' } else { return None; };
        let marker = format!("{}{}", &rest[..digits], sep);
        (true, marker, &after_digits[2..])
    };
    let depth = indent / 2;
    let (checked, body) = if let Some(b) = after_marker.strip_prefix("[ ] ") {
        (Some(false), b)
    } else if let Some(b) = after_marker.strip_prefix("[x] ").or_else(|| after_marker.strip_prefix("[X] ")) {
        (Some(true), b)
    } else {
        (None, after_marker)
    };
    Some((depth, ordered, marker, checked, body.to_owned()))
}

fn split_table_row(line: &str) -> Vec<String> {
    let mut s = line.trim();
    if s.starts_with('|') {
        s = &s[1..];
    }
    if s.ends_with('|') && !s.ends_with("\\|") {
        s = &s[..s.len() - 1];
    }
    let mut cells = Vec::new();
    let mut cur = String::new();
    let chars: Vec<char> = s.chars().collect();
    let mut i = 0;
    let mut in_code = false;
    while i < chars.len() {
        if chars[i] == '\\' && i + 1 < chars.len() && chars[i + 1] == '|' {
            cur.push('|');
            i += 2;
            continue;
        }
        if chars[i] == '`' {
            in_code = !in_code;
            cur.push('`');
            i += 1;
            continue;
        }
        if chars[i] == '|' && !in_code {
            cells.push(cur.trim().to_owned());
            cur.clear();
            i += 1;
            continue;
        }
        cur.push(chars[i]);
        i += 1;
    }
    cells.push(cur.trim().to_owned());
    cells
}

fn parse_table_separator(line: &str) -> Option<Vec<&'static str>> {
    if !line.contains('|') && !line.trim().starts_with('-') && !line.trim().starts_with(':') {
        return None;
    }
    let cells = split_table_row(line);
    if cells.is_empty() {
        return None;
    }
    let mut alignments = Vec::with_capacity(cells.len());
    for cell in &cells {
        let t = cell.trim();
        if t.is_empty() {
            return None;
        }
        let left = t.starts_with(':');
        let right = t.ends_with(':');
        let inner = t.trim_start_matches(':').trim_end_matches(':');
        if inner.is_empty() || !inner.chars().all(|c| c == '-') {
            return None;
        }
        alignments.push(match (left, right) {
            (true, true) => "center",
            (false, true) => "right",
            _ => "left",
        });
    }
    Some(alignments)
}

fn parse_standalone_image(trimmed: &str) -> Option<(String, String)> {
    if !trimmed.starts_with("![") {
        return None;
    }
    let chars: Vec<char> = trimmed.chars().collect();
    let (alt, url, next_idx) = parse_link_or_image(&chars, 0)?;
    if next_idx == chars.len() {
        Some((alt, url))
    } else {
        None
    }
}

pub(crate) fn render_markdown_doc(source: &str, file_path: Option<&str>, theme: &MarkdownTheme) -> MarkdownDocumentData {
    let normalized = source.replace("\r\n", "\n").replace('\r', "\n");
    let lines: Vec<&str> = normalized.split('\n').collect();
    let mut blocks: Vec<MarkdownBlock> = Vec::new();
    let mut headings: Vec<HeadingItem> = Vec::new();
    let mut seen_anchors: HashSet<String> = HashSet::new();
    let mut i = 0usize;

    while i < lines.len() {
        let line = lines[i];
        let trimmed = line.trim();

        if trimmed.is_empty() {
            i += 1;
            continue;
        }

        // Fenced code block
        if let Some((ch, min_len, lang)) = parse_fence_open(trimmed) {
            let mut code_lines = Vec::new();
            i += 1;
            while i < lines.len() {
                if is_fence_close(lines[i].trim(), ch, min_len) {
                    i += 1;
                    break;
                }
                code_lines.push(lines[i]);
                i += 1;
            }
            let code = code_lines.join("\n");
            let mut b = empty_block("code");
            b.language = lang.clone();
            b.html = highlight_code_html(&code, &lang, theme);
            b.code = code;
            blocks.push(b);
            continue;
        }

        // ATX Heading
        if let Some((level, raw_text)) = parse_atx_heading(trimmed) {
            let plain = strip_inline_plain(&raw_text);
            let anchor = slugify(&plain, &mut seen_anchors);
            let idx = blocks.len();
            headings.push(HeadingItem {
                level,
                text: plain.clone(),
                anchor: anchor.clone(),
                block_index: idx,
            });
            let mut b = empty_block("heading");
            b.level = level;
            b.anchor = anchor;
            b.text = plain;
            b.html = render_inline(&raw_text, theme);
            blocks.push(b);
            i += 1;
            continue;
        }

        // Horizontal rule
        if is_horizontal_rule(trimmed) {
            blocks.push(empty_block("hr"));
            i += 1;
            continue;
        }

        // Standalone image ![alt](url)
        if let Some((alt, url)) = parse_standalone_image(trimmed) {
            let mut b = empty_block("image");
            b.alt = alt.clone();
            b.url = url;
            b.text = alt;
            blocks.push(b);
            i += 1;
            continue;
        }

        // GFM Table
        if i + 1 < lines.len() && line.contains('|') {
            if let Some(alignments) = parse_table_separator(lines[i + 1]) {
                let header_cells = split_table_row(line);
                if header_cells.len() == alignments.len() {
                    let mut b = empty_block("table");
                    b.alignments = alignments;
                    b.headers_text = header_cells.iter().map(|c| strip_inline_plain(c)).collect();
                    b.headers_html = header_cells.iter().map(|c| render_inline(c, theme)).collect();
                    i += 2;
                    while i < lines.len() {
                        let row_line = lines[i];
                        if row_line.trim().is_empty() || !row_line.contains('|') {
                            break;
                        }
                        let mut cells = split_table_row(row_line);
                        cells.resize(b.alignments.len(), String::new());
                        let cells_text = cells.iter().map(|c| strip_inline_plain(c)).collect();
                        let cells_html = cells.iter().map(|c| render_inline(c, theme)).collect();
                        b.rows.push(TableRow { cells_html, cells_text });
                        i += 1;
                    }
                    blocks.push(b);
                    continue;
                }
            }
        }

        // Blockquote
        if trimmed.starts_with('>') {
            let mut quote_lines = Vec::new();
            while i < lines.len() {
                let qt = lines[i].trim_start();
                if let Some(stripped) = qt.strip_prefix('>') {
                    quote_lines.push(stripped.strip_prefix(' ').unwrap_or(stripped).trim_end());
                    i += 1;
                } else if qt.trim().is_empty() {
                    break;
                } else {
                    break;
                }
            }
            let joined = quote_lines.join("\n");
            let mut b = empty_block("blockquote");
            b.text = strip_inline_plain(&joined);
            b.html = render_inline(&joined, theme);
            blocks.push(b);
            continue;
        }

        // List (ordered, unordered, or task list)
        if parse_list_line(line).is_some() {
            let mut items = Vec::new();
            while i < lines.len() {
                let cur = lines[i];
                if cur.trim().is_empty() {
                    if i + 1 < lines.len() && parse_list_line(lines[i + 1]).is_some() {
                        i += 1;
                        continue;
                    }
                    break;
                }
                if let Some((depth, ordered, marker, checked, body)) = parse_list_line(cur) {
                    items.push(ListItem {
                        depth,
                        ordered,
                        marker,
                        checked,
                        html: render_inline(&body, theme),
                        text: strip_inline_plain(&body),
                    });
                    i += 1;
                } else if (cur.starts_with("  ") || cur.starts_with('\t')) && !items.is_empty() {
                    let cont = cur.trim();
                    if let Some(last) = items.last_mut() {
                        last.text.push(' ');
                        last.text.push_str(&strip_inline_plain(cont));
                        last.html.push(' ');
                        last.html.push_str(&render_inline(cont, theme));
                    }
                    i += 1;
                } else {
                    break;
                }
            }
            let mut b = empty_block("list");
            b.items = items;
            blocks.push(b);
            continue;
        }

        // Indented code block (4 spaces or tab)
        if line.starts_with("    ") || line.starts_with('\t') {
            let mut code_lines = Vec::new();
            while i < lines.len() {
                let cl = lines[i];
                if cl.trim().is_empty() {
                    code_lines.push("");
                    i += 1;
                } else if let Some(rest) = cl.strip_prefix("    ") {
                    code_lines.push(rest);
                    i += 1;
                } else if let Some(rest) = cl.strip_prefix('\t') {
                    code_lines.push(rest);
                    i += 1;
                } else {
                    break;
                }
            }
            while code_lines.last() == Some(&"") {
                code_lines.pop();
            }
            let code = code_lines.join("\n");
            let mut b = empty_block("code");
            b.html = escape_html(&code);
            b.code = code;
            blocks.push(b);
            continue;
        }

        // Paragraph (or Setext heading === / ---)
        let mut para_lines: Vec<&str> = Vec::new();
        while i < lines.len() {
            let pl = lines[i];
            let pt = pl.trim();
            if pt.is_empty() {
                break;
            }
            if para_lines.len() == 1 && (pt.chars().all(|c| c == '=') || pt.chars().all(|c| c == '-')) && pt.len() >= 2 {
                let level = if pt.starts_with('=') { 1 } else { 2 };
                let raw_text = para_lines.pop().unwrap().trim().to_owned();
                let plain = strip_inline_plain(&raw_text);
                let anchor = slugify(&plain, &mut seen_anchors);
                let idx = blocks.len();
                headings.push(HeadingItem {
                    level,
                    text: plain.clone(),
                    anchor: anchor.clone(),
                    block_index: idx,
                });
                let mut b = empty_block("heading");
                b.level = level;
                b.anchor = anchor;
                b.text = plain;
                b.html = render_inline(&raw_text, theme);
                blocks.push(b);
                i += 1;
                break;
            }
            if !para_lines.is_empty()
                && (parse_fence_open(pt).is_some()
                    || parse_atx_heading(pt).is_some()
                    || pt.starts_with('>')
                    || parse_list_line(pl).is_some()
                    || is_horizontal_rule(pt)
                    || parse_standalone_image(pt).is_some())
            {
                break;
            }
            para_lines.push(pl.trim_end());
            i += 1;
        }
        if !para_lines.is_empty() {
            let mut joined = String::new();
            for (idx, pl) in para_lines.iter().enumerate() {
                if idx > 0 {
                    if para_lines[idx - 1].ends_with("  ") {
                        joined.push('\n');
                    } else {
                        joined.push(' ');
                    }
                }
                joined.push_str(pl.trim());
            }
            let mut b = empty_block("paragraph");
            b.text = strip_inline_plain(&joined);
            b.html = render_inline(&joined, theme);
            blocks.push(b);
        }
    }

    let title = headings
        .first()
        .map(|h| h.text.clone())
        .or_else(|| {
            file_path.and_then(|p| {
                std::path::Path::new(p)
                    .file_name()
                    .map(|n| n.to_string_lossy().into_owned())
            })
        })
        .unwrap_or_else(|| "Markdown".to_owned());

    let words = normalized.split_whitespace().count();
    let stats = MarkdownStats {
        headings_count: headings.len(),
        words,
        lines: lines.len(),
        read_minutes: if words == 0 { 0 } else { (words + 199) / 200 },
    };

    MarkdownDocumentData {
        title,
        blocks,
        headings,
        stats,
    }
}

#[pyfunction]
#[pyo3(signature = (text, file_path=None, theme_json=None))]
pub(crate) fn markdown_render_json(
    py: Python<'_>,
    text: String,
    file_path: Option<String>,
    theme_json: Option<String>,
) -> PyResult<String> {
    let theme: MarkdownTheme = match theme_json {
        Some(raw) if !raw.trim().is_empty() => serde_json::from_str(&raw).unwrap_or_default(),
        _ => MarkdownTheme::default(),
    };
    py.detach(move || {
        let doc = render_markdown_doc(&text, file_path.as_deref(), &theme);
        serde_json::to_string(&doc)
    })
    .map_err(|error| PyOSError::new_err(error.to_string()))
}

#[pyfunction]
#[pyo3(signature = (path, theme_json=None))]
pub(crate) fn markdown_render_file_json(
    py: Python<'_>,
    path: PathBuf,
    theme_json: Option<String>,
) -> PyResult<String> {
    let theme: MarkdownTheme = match theme_json {
        Some(raw) if !raw.trim().is_empty() => serde_json::from_str(&raw).unwrap_or_default(),
        _ => MarkdownTheme::default(),
    };
    py.detach(move || -> Result<(String, MarkdownDocumentData), String> {
        let bytes = fs::read(&path).map_err(|e| e.to_string())?;
        let text = String::from_utf8(bytes)
            .unwrap_or_else(|e| crate::decode_utf8_ignore(e.as_bytes()));
        let path_str = path.to_string_lossy().into_owned();
        let doc = render_markdown_doc(&text, Some(&path_str), &theme);
        Ok((text, doc))
    })
    .and_then(|(text, doc)| {
        #[derive(Serialize)]
        #[serde(rename_all = "camelCase")]
        struct FilePayload {
            source: String,
            document: MarkdownDocumentData,
        }
        serde_json::to_string(&FilePayload { source: text, document: doc })
            .map_err(|e| e.to_string())
    })
    .map_err(PyOSError::new_err)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_headings_lists_code_tables_and_inline() {
        let md = "\
# Hello **World**

Paragraph with *italic*, **bold**, `code`, ~~strike~~, and [link](https://example.com).

- [x] Done task
- [ ] Open task

```rust
fn main() {
    let x = 42;
}
```

| Name | Value |
| :--- | ---: |
| Alpha | 10 |

> Quoted line
";
        let doc = render_markdown_doc(md, Some("README.md"), &MarkdownTheme::default());
        assert_eq!(doc.title, "Hello World");
        assert_eq!(doc.headings.len(), 1);
        assert_eq!(doc.headings[0].anchor, "hello-world");
        assert!(doc.blocks.iter().any(|b| b.kind == "paragraph" && b.html.contains("<b>bold</b>")));
        assert!(doc.blocks.iter().any(|b| b.kind == "list" && b.items.len() == 2 && b.items[0].checked == Some(true)));
        assert!(doc.blocks.iter().any(|b| b.kind == "code" && b.language == "rust" && b.html.contains("fn")));
        assert!(doc.blocks.iter().any(|b| b.kind == "table" && b.rows.len() == 1 && b.alignments == ["left", "right"]));
        assert!(doc.blocks.iter().any(|b| b.kind == "blockquote" && b.text == "Quoted line"));
    }
}
