//! Clean-room MIT SQLite 3 CLI shell backed by Public Domain SQLite via `rusqlite` (`bundled`).

use std::fs::{self, File};
use std::io::{self, BufRead, BufReader, IsTerminal, Read, Write};
use std::path::{Path, PathBuf};
use std::time::Duration;

use rusqlite::types::ValueRef;
use rusqlite::{Connection, OpenFlags};

/// Output formatting modes supported by `sqlite3`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum OutputMode {
    List,
    Csv,
    Column,
    Box,
    Table,
    Markdown,
    Json,
    Line,
    Html,
    Quote,
    Ascii,
    Tabs,
    Insert(String),
}

impl OutputMode {
    pub fn name(&self) -> &str {
        match self {
            Self::List => "list",
            Self::Csv => "csv",
            Self::Column => "column",
            Self::Box => "box",
            Self::Table => "table",
            Self::Markdown => "markdown",
            Self::Json => "json",
            Self::Line => "line",
            Self::Html => "html",
            Self::Quote => "quote",
            Self::Ascii => "ascii",
            Self::Tabs => "tabs",
            Self::Insert(_) => "insert",
        }
    }
}

/// Mutable state of an active `sqlite3` CLI shell session.
pub struct ShellState {
    pub db_path: Option<PathBuf>,
    pub readonly: bool,
    pub mode: OutputMode,
    pub show_headers: bool,
    pub col_sep: String,
    pub row_sep: String,
    pub null_value: String,
    pub echo: bool,
    pub bail: bool,
    pub show_changes: bool,
    pub col_widths: Vec<usize>,
    pub output_path: Option<PathBuf>,
    pub once_path: Option<PathBuf>,
    pub exit_requested: Option<i32>,
}

impl Default for ShellState {
    fn default() -> Self {
        Self {
            db_path: None,
            readonly: false,
            mode: OutputMode::List,
            show_headers: false,
            col_sep: "|".to_string(),
            row_sep: "\n".to_string(),
            null_value: String::new(),
            echo: false,
            bail: false,
            show_changes: false,
            col_widths: Vec::new(),
            output_path: None,
            once_path: None,
            exit_requested: None,
        }
    }
}

impl ShellState {
    pub fn apply_mode(&mut self, mode_str: &str, extra_arg: Option<&str>) -> Result<(), String> {
        match mode_str.to_ascii_lowercase().as_str() {
            "list" => {
                self.mode = OutputMode::List;
                self.col_sep = "|".to_string();
                self.row_sep = "\n".to_string();
            }
            "csv" => {
                self.mode = OutputMode::Csv;
                self.col_sep = ",".to_string();
                self.row_sep = "\r\n".to_string();
            }
            "column" | "columns" => {
                self.mode = OutputMode::Column;
                self.show_headers = true;
            }
            "box" => {
                self.mode = OutputMode::Box;
                self.show_headers = true;
            }
            "table" => {
                self.mode = OutputMode::Table;
                self.show_headers = true;
            }
            "markdown" => {
                self.mode = OutputMode::Markdown;
                self.show_headers = true;
            }
            "json" => {
                self.mode = OutputMode::Json;
            }
            "line" | "lines" => {
                self.mode = OutputMode::Line;
            }
            "html" => {
                self.mode = OutputMode::Html;
            }
            "quote" => {
                self.mode = OutputMode::Quote;
            }
            "ascii" => {
                self.mode = OutputMode::Ascii;
                self.col_sep = "\x1f".to_string();
                self.row_sep = "\x1e".to_string();
            }
            "tabs" => {
                self.mode = OutputMode::Tabs;
                self.col_sep = "\t".to_string();
                self.row_sep = "\n".to_string();
            }
            "insert" => {
                let tbl = extra_arg.unwrap_or("table").to_string();
                self.mode = OutputMode::Insert(tbl);
            }
            other => return Err(format!("Error: mode should be one of: ascii box column csv html insert json line list markdown quote table tabs (got '{other}')")),
        }
        Ok(())
    }

    fn with_output_writer<F>(&mut self, f: F) -> io::Result<()>
    where
        F: FnOnce(&mut dyn Write) -> io::Result<()>,
    {
        if let Some(once) = self.once_path.take() {
            let mut file = File::create(once)?;
            return f(&mut file);
        }
        if let Some(ref out_p) = self.output_path {
            let mut file = fs::OpenOptions::new()
                .create(true)
                .append(true)
                .open(out_p)?;
            return f(&mut file);
        }
        let stdout = io::stdout();
        let mut handle = stdout.lock();
        f(&mut handle)
    }
}

/// Returns the version banner string (`3.53.4 ...`) matching `platform-tools` v37.0.3.
pub fn version_string() -> String {
    "3.53.4 2026-07-24 19:02:57 bf7c7f30031888f4e796e429ab3978879485813aaca6f641c7b33e4e09459bcc (32-bit)".to_string()
}

/// Returns the `-help` usage message matching `platform-tools/sqlite3` v37.0.3.
pub fn usage_text() -> String {
    let prog = std::env::args()
        .next()
        .unwrap_or_else(|| "sqlite3".to_string());
    let body = concat!(
        "FILENAME is the name of an SQLite database. A new database is created\n",
        "if the file does not previously exist. Defaults to :memory:.\n",
        "OPTIONS include:\n",
        "   --                   treat no subsequent arguments as options\n",
        "   -append              append the database to the end of the file\n",
        "   -ascii               set output mode to 'ascii'\n",
        "   -bail                stop after hitting an error\n",
        "   -batch               force batch I/O\n",
        "   -box                 set output mode to 'box'\n",
        "   -cmd COMMAND         run \"COMMAND\" before reading stdin\n",
        "   -column              set output mode to 'column'\n",
        "   -csv                 set output mode to 'csv'\n",
        "   -deserialize         open the database using sqlite3_deserialize()\n",
        "   -echo                print inputs before execution\n",
        "   -escape T            ctrl-char escape; T is one of: symbol, ascii, off\n",
        "   -init FILENAME       read/process named file\n",
        "   -[no]header          turn headers on or off\n",
        "   -help                show this message\n",
        "   -html                set output mode to HTML\n",
        "   -ifexists            only open if database already exists\n",
        "   -interactive         force interactive I/O\n",
        "   -json                set output mode to 'json'\n",
        "   -line                set output mode to 'line'\n",
        "   -list                set output mode to 'list'\n",
        "   -lookaside SIZE N    use N entries of SZ bytes for lookaside memory\n",
        "   -markdown            set output mode to 'markdown'\n",
        "   -maxsize N           maximum size for a --deserialize database\n",
        "   -memtrace            trace all memory allocations and deallocations\n",
        "   -mmap N              default mmap size set to N\n",
        "   -newline SEP         set output row separator. Default: '\\n'\n",
        "   -nofollow            refuse to open symbolic links to database files\n",
        "   -noinit              Do not read the ~/.sqliterc file at startup\n",
        "   -nonce STRING        set the safe-mode escape nonce\n",
        "   -no-rowid-in-view    Disable rowid-in-view using sqlite3_config()\n",
        "   -nullvalue TEXT      set text string for NULL values. Default ''\n",
        "   -pagecache SIZE N    use N slots of SZ bytes each for page cache memory\n",
        "   -pcachetrace         trace all page cache operations\n",
        "   -quote               set output mode to 'quote'\n",
        "   -readonly            open the database read-only\n",
        "   -safe                enable safe-mode\n",
        "   -screenwidth N       use N as the default screenwidth \n",
        "   -separator SEP       set output column separator. Default: '|'\n",
        "   -stats               print memory stats before each finalize\n",
        "   -table               set output mode to 'table'\n",
        "   -tabs                set output mode to 'tabs'\n",
        "   -unsafe-testing      allow unsafe commands and modes for testing\n",
        "   -version             show SQLite version\n",
        "   -vfs NAME            use NAME as the default VFS\n",
        "   -vfstrace            enable tracing of all VFS calls\n",
    );
    format!("Usage: {prog} [OPTIONS] [FILENAME [SQL...]]\n{body}")
}

#[derive(Clone, Debug)]
enum CellVal {
    Null,
    Int(i64),
    Real(f64),
    Text(String),
    Blob(Vec<u8>),
}

impl CellVal {
    fn from_ref(v: ValueRef<'_>) -> Self {
        match v {
            ValueRef::Null => Self::Null,
            ValueRef::Integer(i) => Self::Int(i),
            ValueRef::Real(r) => Self::Real(r),
            ValueRef::Text(t) => Self::Text(String::from_utf8_lossy(t).into_owned()),
            ValueRef::Blob(b) => Self::Blob(b.to_vec()),
        }
    }

    fn display_plain(&self, null_str: &str) -> String {
        match self {
            Self::Null => null_str.to_string(),
            Self::Int(i) => i.to_string(),
            Self::Real(r) => format_sqlite_float(*r),
            Self::Text(s) => s.clone(),
            Self::Blob(b) => String::from_utf8_lossy(b).into_owned(),
        }
    }

    fn display_sql_literal(&self) -> String {
        match self {
            Self::Null => "NULL".to_string(),
            Self::Int(i) => i.to_string(),
            Self::Real(r) => format_sqlite_float(*r),
            Self::Text(s) => format!("'{}'", s.replace('\'', "''")),
            Self::Blob(b) => {
                let mut hex = String::with_capacity(3 + b.len() * 2);
                hex.push_str("X'");
                for byte in b {
                    use std::fmt::Write as _;
                    let _ = write!(&mut hex, "{byte:02X}");
                }
                hex.push('\'');
                hex
            }
        }
    }

    fn display_json(&self) -> String {
        match self {
            Self::Null => "null".to_string(),
            Self::Int(i) => i.to_string(),
            Self::Real(r) => {
                if r.is_finite() {
                    format_sqlite_float(*r)
                } else {
                    "null".to_string()
                }
            }
            Self::Text(s) => escape_json_string(s),
            Self::Blob(b) => escape_json_string(&String::from_utf8_lossy(b)),
        }
    }
}

fn format_sqlite_float(r: f64) -> String {
    let s = format!("{r}");
    if s.contains('.') || s.contains('e') || s.contains('E') {
        s
    } else {
        format!("{s}.0")
    }
}

fn escape_json_string(s: &str) -> String {
    let mut out = String::with_capacity(s.len() + 2);
    out.push('"');
    for ch in s.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\x08' => out.push_str("\\b"),
            '\x0c' => out.push_str("\\f"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => {
                use std::fmt::Write as _;
                let _ = write!(&mut out, "\\u{:04x}", c as u32);
            }
            c => out.push(c),
        }
    }
    out.push('"');
    out
}

fn escape_csv_field(field: &str, sep: &str) -> String {
    if field.contains('"') || field.contains('\n') || field.contains('\r') || field.contains(sep) {
        format!("\"{}\"", field.replace('"', "\"\""))
    } else {
        field.to_string()
    }
}

fn escape_html(s: &str) -> String {
    s.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
}

fn render_query_results(
    state: &mut ShellState,
    col_names: &[String],
    rows: &[Vec<CellVal>],
) -> io::Result<()> {
    let mode = state.mode.clone();
    let show_headers = state.show_headers;
    let col_sep = state.col_sep.clone();
    let row_sep = state.row_sep.clone();
    let null_val = state.null_value.clone();
    let min_widths = state.col_widths.clone();

    state.with_output_writer(|w| match mode {
        OutputMode::List | OutputMode::Ascii | OutputMode::Tabs => {
            if show_headers && !col_names.is_empty() {
                let hdr = col_names.join(&col_sep);
                w.write_all(hdr.as_bytes())?;
                w.write_all(row_sep.as_bytes())?;
            }
            for row in rows {
                let cells: Vec<String> = row.iter().map(|c| c.display_plain(&null_val)).collect();
                w.write_all(cells.join(&col_sep).as_bytes())?;
                w.write_all(row_sep.as_bytes())?;
            }
            Ok(())
        }
        OutputMode::Csv => {
            if show_headers && !col_names.is_empty() {
                let hdr: Vec<String> = col_names
                    .iter()
                    .map(|c| escape_csv_field(c, &col_sep))
                    .collect();
                w.write_all(hdr.join(&col_sep).as_bytes())?;
                w.write_all(row_sep.as_bytes())?;
            }
            for row in rows {
                let cells: Vec<String> = row
                    .iter()
                    .map(|c| escape_csv_field(&c.display_plain(&null_val), &col_sep))
                    .collect();
                w.write_all(cells.join(&col_sep).as_bytes())?;
                w.write_all(row_sep.as_bytes())?;
            }
            Ok(())
        }
        OutputMode::Quote => {
            if show_headers && !col_names.is_empty() {
                let hdr: Vec<String> = col_names
                    .iter()
                    .map(|c| CellVal::Text(c.clone()).display_sql_literal())
                    .collect();
                writeln!(w, "{}", hdr.join(&col_sep))?;
            }
            for row in rows {
                let cells: Vec<String> = row.iter().map(CellVal::display_sql_literal).collect();
                writeln!(w, "{}", cells.join(&col_sep))?;
            }
            Ok(())
        }
        OutputMode::Insert(ref tbl) => {
            for row in rows {
                let cells: Vec<String> = row.iter().map(CellVal::display_sql_literal).collect();
                writeln!(w, "INSERT INTO {} VALUES({});", tbl, cells.join(","))?;
            }
            Ok(())
        }
        OutputMode::Json => {
            if rows.is_empty() {
                return Ok(());
            }
            w.write_all(b"[")?;
            for (r_idx, row) in rows.iter().enumerate() {
                if r_idx > 0 {
                    w.write_all(b",\n")?;
                }
                w.write_all(b"{")?;
                for (c_idx, cell) in row.iter().enumerate() {
                    if c_idx > 0 {
                        w.write_all(b",")?;
                    }
                    let cname = col_names.get(c_idx).map(String::as_str).unwrap_or("");
                    write!(w, "{}:{}", escape_json_string(cname), cell.display_json())?;
                }
                w.write_all(b"}")?;
            }
            writeln!(w, "]")?;
            Ok(())
        }
        OutputMode::Line => {
            let max_col_w = col_names.iter().map(String::len).max().unwrap_or(5);
            for (r_idx, row) in rows.iter().enumerate() {
                if r_idx > 0 {
                    writeln!(w)?;
                }
                for (c_idx, cell) in row.iter().enumerate() {
                    let cname = col_names.get(c_idx).map(String::as_str).unwrap_or("");
                    writeln!(
                        w,
                        "{:>width$} = {}",
                        cname,
                        cell.display_plain(&null_val),
                        width = max_col_w
                    )?;
                }
            }
            Ok(())
        }
        OutputMode::Html => {
            if show_headers && !col_names.is_empty() {
                w.write_all(b"<TR>")?;
                for c in col_names {
                    writeln!(w, "<TH>{}</TH>", escape_html(c))?;
                }
                writeln!(w, "</TR>")?;
            }
            for row in rows {
                w.write_all(b"<TR>")?;
                for cell in row {
                    writeln!(w, "<TD>{}</TD>", escape_html(&cell.display_plain(&null_val)))?;
                }
                writeln!(w, "</TR>")?;
            }
            Ok(())
        }
        OutputMode::Column | OutputMode::Table | OutputMode::Box | OutputMode::Markdown => {
            if rows.is_empty() && !show_headers {
                return Ok(());
            }
            let ncols = col_names.len();
            let mut widths = vec![0usize; ncols];
            for (i, name) in col_names.iter().enumerate() {
                widths[i] = name.chars().count();
                if let Some(&mw) = min_widths.get(i) {
                    widths[i] = widths[i].max(mw);
                }
            }
            let mut str_rows: Vec<Vec<String>> = Vec::with_capacity(rows.len());
            for row in rows {
                let mut srow = Vec::with_capacity(ncols);
                for (i, cell) in row.iter().enumerate() {
                    let s = cell.display_plain(&null_val);
                    if i < widths.len() {
                        widths[i] = widths[i].max(s.chars().count());
                    }
                    srow.push(s);
                }
                str_rows.push(srow);
            }

            match mode {
                OutputMode::Column => {
                    if show_headers {
                        let hdr: Vec<String> = col_names
                            .iter()
                            .enumerate()
                            .map(|(i, n)| format!("{:<width$}", n, width = widths[i]))
                            .collect();
                        writeln!(w, "{}", hdr.join("  ").trim_end())?;
                        let dashes: Vec<String> =
                            widths.iter().map(|& len| "-".repeat(len.max(1))).collect();
                        writeln!(w, "{}", dashes.join("  "))?;
                    }
                    for srow in &str_rows {
                        let line: Vec<String> = srow
                            .iter()
                            .enumerate()
                            .map(|(i, s)| format!("{:<width$}", s, width = widths[i]))
                            .collect();
                        writeln!(w, "{}", line.join("  ").trim_end())?;
                    }
                }
                OutputMode::Markdown => {
                    let hdr: Vec<String> = col_names
                        .iter()
                        .enumerate()
                        .map(|(i, n)| format!(" {:<width$} ", n, width = widths[i]))
                        .collect();
                    writeln!(w, "|{}|", hdr.join("|"))?;
                    let sep: Vec<String> =
                        widths.iter().map(|&len| "-".repeat(len + 2)).collect();
                    writeln!(w, "|{}|", sep.join("|"))?;
                    for srow in &str_rows {
                        let line: Vec<String> = srow
                            .iter()
                            .enumerate()
                            .map(|(i, s)| format!(" {:<width$} ", s, width = widths[i]))
                            .collect();
                        writeln!(w, "|{}|", line.join("|"))?;
                    }
                }
                OutputMode::Table => {
                    let border_segs: Vec<String> =
                        widths.iter().map(|&len| "-".repeat(len + 2)).collect();
                    let border = format!("+{}+", border_segs.join("+"));
                    writeln!(w, "{border}")?;
                    if show_headers {
                        let hdr: Vec<String> = col_names
                            .iter()
                            .enumerate()
                            .map(|(i, n)| format!(" {:<width$} ", n, width = widths[i]))
                            .collect();
                        writeln!(w, "|{}|", hdr.join("|"))?;
                        writeln!(w, "{border}")?;
                    }
                    for srow in &str_rows {
                        let line: Vec<String> = srow
                            .iter()
                            .enumerate()
                            .map(|(i, s)| format!(" {:<width$} ", s, width = widths[i]))
                            .collect();
                        writeln!(w, "|{}|", line.join("|"))?;
                    }
                    if !str_rows.is_empty() {
                        writeln!(w, "{border}")?;
                    }
                }
                OutputMode::Box => {
                    let top_segs: Vec<String> =
                        widths.iter().map(|&len| "─".repeat(len + 2)).collect();
                    writeln!(w, "┌{}┐", top_segs.join("┬"))?;
                    if show_headers {
                        let hdr: Vec<String> = col_names
                            .iter()
                            .enumerate()
                            .map(|(i, n)| format!(" {:<width$} ", n, width = widths[i]))
                            .collect();
                        writeln!(w, "│{}│", hdr.join("│"))?;
                        writeln!(w, "├{}┤", top_segs.join("┼"))?;
                    }
                    for srow in &str_rows {
                        let line: Vec<String> = srow
                            .iter()
                            .enumerate()
                            .map(|(i, s)| format!(" {:<width$} ", s, width = widths[i]))
                            .collect();
                        writeln!(w, "│{}│", line.join("│"))?;
                    }
                    writeln!(w, "└{}┘", top_segs.join("┴"))?;
                }
                _ => unreachable!(),
            }
            Ok(())
        }
    })
}

/// Executes one or more semicolon-terminated SQL statements on `conn`.
pub fn execute_sql_batch(
    conn: &Connection,
    state: &mut ShellState,
    sql_batch: &str,
) -> Result<(), String> {
    if state.echo {
        println!("{sql_batch}");
    }

    for stmt_str in split_sql_statements(sql_batch) {
        let trimmed = stmt_str.trim();
        if trimmed.is_empty() {
            continue;
        }
        let mut stmt = match conn.prepare(trimmed) {
            Ok(s) => s,
            Err(e) => return Err(format!("Parse error: {e}")),
        };
        let col_count = stmt.column_count();
        if col_count == 0 {
            stmt.execute([])
                .map_err(|e| format!("Runtime error: {e}"))?;
            if state.show_changes {
                println!(
                    "changes: {:>4}   total_changes: {:>4}",
                    conn.changes(),
                    conn.total_changes()
                );
            }
        } else {
            let col_names: Vec<String> = stmt
                .column_names()
                .into_iter()
                .map(str::to_string)
                .collect();
            let mut rows_out: Vec<Vec<CellVal>> = Vec::new();
            let mut rows = stmt.query([]).map_err(|e| format!("Runtime error: {e}"))?;
            while let Some(row) = rows.next().map_err(|e| format!("Runtime error: {e}"))? {
                let mut rvec = Vec::with_capacity(col_count);
                for idx in 0..col_count {
                    let v = row
                        .get_ref(idx)
                        .map_err(|e| format!("Runtime error: {e}"))?;
                    rvec.push(CellVal::from_ref(v));
                }
                rows_out.push(rvec);
            }
            if !rows_out.is_empty() {
                render_query_results(state, &col_names, &rows_out)
                    .map_err(|e| format!("I/O error: {e}"))?;
            }
        }
    }
    Ok(())
}

/// Splits a multi-statement SQL script on top-level `;` (respecting single/double/backtick/bracket quotes and comments).
fn split_sql_statements(script: &str) -> Vec<String> {
    let mut stmts = Vec::new();
    let mut cur = String::new();
    let chars: Vec<char> = script.chars().collect();
    let mut i = 0;
    let mut in_quote: Option<char> = None;

    while i < chars.len() {
        let c = chars[i];
        if let Some(q) = in_quote {
            cur.push(c);
            if (q == '[' && c == ']') || (q != '[' && c == q) {
                if i + 1 < chars.len() && chars[i + 1] == q && q != '[' {
                    cur.push(chars[i + 1]);
                    i += 2;
                    continue;
                }
                in_quote = None;
            }
            i += 1;
            continue;
        }

        if c == '-' && i + 1 < chars.len() && chars[i + 1] == '-' {
            while i < chars.len() && chars[i] != '\n' {
                i += 1;
            }
            continue;
        }
        if c == '/' && i + 1 < chars.len() && chars[i + 1] == '*' {
            i += 2;
            while i + 1 < chars.len() && !(chars[i] == '*' && chars[i + 1] == '/') {
                i += 1;
            }
            i += 2;
            continue;
        }

        match c {
            '\'' | '"' | '`' | '[' => {
                in_quote = Some(c);
                cur.push(c);
            }
            ';' => {
                cur.push(';');
                if !cur.trim().is_empty() {
                    stmts.push(std::mem::take(&mut cur));
                }
            }
            _ => cur.push(c),
        }
        i += 1;
    }

    if !cur.trim().is_empty() {
        stmts.push(cur);
    }
    stmts
}

fn parse_dot_tokens(line: &str) -> Vec<String> {
    let mut tokens = Vec::new();
    let mut cur = String::new();
    let mut in_q: Option<char> = None;
    let chars: Vec<char> = line.trim().chars().collect();
    let mut i = 0;

    while i < chars.len() {
        let c = chars[i];
        if let Some(q) = in_q {
            if c == '\\' && i + 1 < chars.len() && q == '"' {
                let next = chars[i + 1];
                match next {
                    'n' => cur.push('\n'),
                    'r' => cur.push('\r'),
                    't' => cur.push('\t'),
                    other => cur.push(other),
                }
                i += 2;
                continue;
            }
            if c == q {
                in_q = None;
            } else {
                cur.push(c);
            }
            i += 1;
            continue;
        }

        if c == '"' || c == '\'' {
            in_q = Some(c);
        } else if c.is_whitespace() {
            if !cur.is_empty() {
                tokens.push(std::mem::take(&mut cur));
            }
        } else {
            cur.push(c);
        }
        i += 1;
    }
    if !cur.is_empty() {
        tokens.push(cur);
    }
    tokens
}

fn parse_bool_arg(s: &str) -> bool {
    matches!(s.to_ascii_lowercase().as_str(), "on" | "yes" | "true" | "1")
}

/// Executes a `.dot` meta-command on `conn` and `state`.
pub fn execute_dot_command(
    conn: &mut Connection,
    state: &mut ShellState,
    line: &str,
) -> Result<(), String> {
    if state.echo {
        println!("{line}");
    }
    let tokens = parse_dot_tokens(line);
    if tokens.is_empty() {
        return Ok(());
    }
    let cmd = tokens[0].as_str();
    let args = &tokens[1..];

    match cmd {
        ".quit" | ".exit" => {
            let code = args.first().and_then(|s| s.parse::<i32>().ok()).unwrap_or(0);
            state.exit_requested = Some(code);
        }
        ".help" => {
            let _ = state.with_output_writer(|w| {
                writeln!(
                    w,
                    ".backup ?DB? FILE        Backup DB (default \"main\") to FILE\n\
                     .bail on|off             Stop after hitting an error.  Default OFF\n\
                     .cd DIRECTORY            Change the working directory to DIRECTORY\n\
                     .changes on|off          Show number of rows changed by SQL\n\
                     .databases               List names and files of attached databases\n\
                     .dump ?OBJECTS?          Render database content as SQL\n\
                     .echo on|off             Turn command echo on or off\n\
                     .exit ?CODE?             Exit this program with return-code CODE\n\
                     .headers on|off          Turn display of headers on or off\n\
                     .help                    Show this message\n\
                     .import FILE TABLE       Import data from FILE into TABLE\n\
                     .indexes ?TABLE?         Show names of indexes\n\
                     .mode MODE ?OPTIONS?     Set output mode\n\
                     .nullvalue STRING        Use STRING in place of NULL values\n\
                     .once FILE               Output for the next SQL command only to FILE\n\
                     .output ?FILE?           Send output to FILE or stdout if FILE is omitted\n\
                     .print STRING...         Print literal STRING\n\
                     .quit                    Exit this program\n\
                     .read FILE               Read input from FILE\n\
                     .restore ?DB? FILE       Restore content of DB (default \"main\") from FILE\n\
                     .save FILE               Write in-memory database into FILE\n\
                     .schema ?PATTERN?        Show the CREATE statements matching PATTERN\n\
                     .separator COL ?ROW?     Change the column and row separators\n\
                     .show                    Show the current values for various settings\n\
                     .tables ?TABLE?          List names of tables matching LIKE pattern TABLE\n\
                     .timeout MS              Try opening locked tables for MS milliseconds\n\
                     .width NUM1 NUM2 ...     Set minimum column widths for \"column\" mode"
                )
            });
        }
        ".mode" => {
            if let Some(m) = args.first() {
                state.apply_mode(m, args.get(1).map(String::as_str))?;
            } else {
                println!("current output mode: {}", state.mode.name());
            }
        }
        ".headers" | ".header" => {
            if let Some(v) = args.first() {
                state.show_headers = parse_bool_arg(v);
            }
        }
        ".separator" => {
            if let Some(c) = args.first() {
                state.col_sep = c.clone();
            }
            if let Some(r) = args.get(1) {
                state.row_sep = r.clone();
            }
        }
        ".nullvalue" => {
            state.null_value = args.first().cloned().unwrap_or_default();
        }
        ".echo" => {
            if let Some(v) = args.first() {
                state.echo = parse_bool_arg(v);
            }
        }
        ".bail" => {
            if let Some(v) = args.first() {
                state.bail = parse_bool_arg(v);
            }
        }
        ".changes" => {
            if let Some(v) = args.first() {
                state.show_changes = parse_bool_arg(v);
            }
        }
        ".timeout" => {
            if let Some(ms) = args.first().and_then(|s| s.parse::<u64>().ok()) {
                let _ = conn.busy_timeout(Duration::from_millis(ms));
            }
        }
        ".width" => {
            state.col_widths = args
                .iter()
                .filter_map(|s| s.parse::<isize>().ok())
                .map(|w| w.unsigned_abs())
                .collect();
        }
        ".print" => {
            let msg = args.join(" ");
            let _ = state.with_output_writer(|w| writeln!(w, "{msg}"));
        }
        ".output" => {
            match args.first().map(String::as_str) {
                None | Some("stdout") => state.output_path = None,
                Some(p) => {
                    let _ = File::create(p);
                    state.output_path = Some(PathBuf::from(p));
                }
            }
        }
        ".once" => {
            if let Some(p) = args.first() {
                state.once_path = Some(PathBuf::from(p));
            }
        }
        ".cd" => {
            if let Some(dir) = args.first() {
                std::env::set_current_dir(dir).map_err(|e| format!("Cannot change dir: {e}"))?;
            }
        }
        ".show" => {
            let echo_s = if state.echo { "on" } else { "off" };
            let hdr_s = if state.show_headers { "on" } else { "off" };
            let mode_s = state.mode.name().to_string();
            let null_s = state.null_value.clone();
            let out_s = state
                .output_path
                .as_ref()
                .map(|p| p.display().to_string())
                .unwrap_or_else(|| "stdout".to_string());
            let csep = state.col_sep.clone();
            let rsep = state.row_sep.escape_default().to_string();
            let _ = state.with_output_writer(|w| {
                writeln!(
                    w,
                    "        echo: {echo_s}\n\
                     \x20    headers: {hdr_s}\n\
                     \x20       mode: {mode_s}\n\
                     \x20  nullvalue: \"{null_s}\"\n\
                     \x20     output: {out_s}\n\
                     colseparator: \"{csep}\"\n\
                     rowseparator: \"{rsep}\""
                )
            });
        }
        ".databases" => {
            let mut stmt = conn
                .prepare("PRAGMA database_list;")
                .map_err(|e| e.to_string())?;
            let mut rows = stmt.query([]).map_err(|e| e.to_string())?;
            let mut lines = Vec::new();
            while let Some(r) = rows.next().map_err(|e| e.to_string())? {
                let name: String = r.get(1).unwrap_or_default();
                let file: String = r.get(2).unwrap_or_default();
                lines.push(format!("{name}: {file} r/w"));
            }
            let _ = state.with_output_writer(|w| {
                for l in &lines {
                    writeln!(w, "{l}")?;
                }
                Ok(())
            });
        }
        ".tables" => {
            let pattern = args.first().map(String::as_str);
            let sql = match pattern {
                Some(p) => format!(
                    "SELECT name FROM sqlite_schema WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' AND name LIKE '{}' ORDER BY 1;",
                    p.replace('\'', "''")
                ),
                None => "SELECT name FROM sqlite_schema WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY 1;".to_string(),
            };
            let mut stmt = conn.prepare(&sql).map_err(|e| e.to_string())?;
            let names: Vec<String> = stmt
                .query_map([], |r| r.get::<_, String>(0))
                .map_err(|e| e.to_string())?
                .filter_map(Result::ok)
                .collect();
            if !names.is_empty() {
                let joined = names.join("  ");
                let _ = state.with_output_writer(|w| writeln!(w, "{joined}"));
            }
        }
        ".indexes" | ".indices" => {
            let pattern = args.first().map(String::as_str);
            let sql = match pattern {
                Some(p) => format!(
                    "SELECT name FROM sqlite_schema WHERE type='index' AND tbl_name LIKE '{}' ORDER BY 1;",
                    p.replace('\'', "''")
                ),
                None => "SELECT name FROM sqlite_schema WHERE type='index' ORDER BY 1;".to_string(),
            };
            let mut stmt = conn.prepare(&sql).map_err(|e| e.to_string())?;
            let names: Vec<String> = stmt
                .query_map([], |r| r.get::<_, String>(0))
                .map_err(|e| e.to_string())?
                .filter_map(Result::ok)
                .collect();
            if !names.is_empty() {
                let joined = names.join("  ");
                let _ = state.with_output_writer(|w| writeln!(w, "{joined}"));
            }
        }
        ".schema" | ".fullschema" => {
            let pattern = args.first().map(String::as_str);
            let sql = match pattern {
                Some(p) => format!(
                    "SELECT sql FROM sqlite_schema WHERE sql IS NOT NULL AND tbl_name LIKE '{}' ORDER BY rowid;",
                    p.replace('\'', "''")
                ),
                None => "SELECT sql FROM sqlite_schema WHERE sql IS NOT NULL ORDER BY rowid;".to_string(),
            };
            let mut stmt = conn.prepare(&sql).map_err(|e| e.to_string())?;
            let sqls: Vec<String> = stmt
                .query_map([], |r| r.get::<_, String>(0))
                .map_err(|e| e.to_string())?
                .filter_map(Result::ok)
                .collect();
            let _ = state.with_output_writer(|w| {
                for s in &sqls {
                    writeln!(w, "{s};")?;
                }
                Ok(())
            });
        }
        ".dump" => {
            dump_database(conn, state, args.first().map(String::as_str))?;
        }
        ".import" => {
            if args.len() < 2 {
                return Err("Error:Usage: .import FILE TABLE".into());
            }
            import_file_into_table(conn, state, Path::new(&args[0]), &args[1])?;
        }
        ".read" => {
            let Some(file_arg) = args.first() else {
                return Err("Error:Usage: .read FILE".into());
            };
            let content =
                fs::read_to_string(file_arg).map_err(|e| format!("Error: cannot open \"{file_arg}\": {e}"))?;
            process_input_text(conn, state, &content)?;
        }
        ".backup" | ".save" => {
            let target_file = args
                .last()
                .ok_or_else(|| "Error: missing backup file name".to_string())?;
            let mut dst = Connection::open(target_file).map_err(|e| e.to_string())?;
            let backup =
                rusqlite::backup::Backup::new(conn, &mut dst).map_err(|e| e.to_string())?;
            backup
                .run_to_completion(100, Duration::from_millis(10), None)
                .map_err(|e| e.to_string())?;
        }
        ".restore" => {
            let src_file = args
                .last()
                .ok_or_else(|| "Error: missing restore file name".to_string())?;
            let src = Connection::open(src_file).map_err(|e| e.to_string())?;
            let backup =
                rusqlite::backup::Backup::new(&src, conn).map_err(|e| e.to_string())?;
            backup
                .run_to_completion(100, Duration::from_millis(10), None)
                .map_err(|e| e.to_string())?;
        }
        _ => {
            return Err(format!(
                "Error: unknown command or invalid arguments:  \"{}\". Enter \".help\" for help",
                &cmd[1..]
            ));
        }
    }
    Ok(())
}

fn dump_database(
    conn: &Connection,
    state: &mut ShellState,
    table_filter: Option<&str>,
) -> Result<(), String> {
    let mut lines: Vec<String> = vec![
        "PRAGMA foreign_keys=OFF;".to_string(),
        "BEGIN TRANSACTION;".to_string(),
    ];

    let tbl_query = match table_filter {
        Some(p) => format!(
            "SELECT name, type, sql FROM sqlite_schema WHERE sql IS NOT NULL AND type='table' AND name NOT LIKE 'sqlite_%' AND name LIKE '{}' ORDER BY rowid;",
            p.replace('\'', "''")
        ),
        None => "SELECT name, type, sql FROM sqlite_schema WHERE sql IS NOT NULL AND type='table' AND name NOT LIKE 'sqlite_%' ORDER BY rowid;".to_string(),
    };
    let mut stmt = conn.prepare(&tbl_query).map_err(|e| e.to_string())?;
    let tables: Vec<(String, String)> = stmt
        .query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(2)?)))
        .map_err(|e| e.to_string())?
        .filter_map(Result::ok)
        .collect();

    for (tname, create_sql) in &tables {
        lines.push(format!("{create_sql};"));
        let sel = format!("SELECT * FROM \"{}\";", tname.replace('"', "\"\""));
        let mut dstmt = conn.prepare(&sel).map_err(|e| e.to_string())?;
        let ncols = dstmt.column_count();
        let mut rows = dstmt.query([]).map_err(|e| e.to_string())?;
        while let Some(row) = rows.next().map_err(|e| e.to_string())? {
            let mut vals = Vec::with_capacity(ncols);
            for i in 0..ncols {
                let v = row.get_ref(i).map_err(|e| e.to_string())?;
                vals.push(CellVal::from_ref(v).display_sql_literal());
            }
            lines.push(format!(
                "INSERT INTO {} VALUES({});",
                tname,
                vals.join(",")
            ));
        }
    }

    // Indices, triggers, views
    let other_query = match table_filter {
        Some(p) => format!(
            "SELECT sql FROM sqlite_schema WHERE sql IS NOT NULL AND type IN ('index','trigger','view') AND tbl_name LIKE '{}' ORDER BY rowid;",
            p.replace('\'', "''")
        ),
        None => "SELECT sql FROM sqlite_schema WHERE sql IS NOT NULL AND type IN ('index','trigger','view') ORDER BY rowid;".to_string(),
    };
    let mut ostmt = conn.prepare(&other_query).map_err(|e| e.to_string())?;
    let others: Vec<String> = ostmt
        .query_map([], |r| r.get::<_, String>(0))
        .map_err(|e| e.to_string())?
        .filter_map(Result::ok)
        .collect();
    for s in others {
        lines.push(format!("{s};"));
    }

    lines.push("COMMIT;".to_string());
    state
        .with_output_writer(|w| {
            for l in &lines {
                writeln!(w, "{l}")?;
            }
            Ok(())
        })
        .map_err(|e| e.to_string())
}

fn import_file_into_table(
    conn: &mut Connection,
    state: &ShellState,
    file_path: &Path,
    table_name: &str,
) -> Result<(), String> {
    let f = File::open(file_path).map_err(|e| format!("Error: cannot open \"{}\": {e}", file_path.display()))?;
    let reader = BufReader::new(f);
    let sep = &state.col_sep;

    let mut rows: Vec<Vec<String>> = Vec::new();
    for line_res in reader.lines() {
        let line = line_res.map_err(|e| e.to_string())?;
        if line.is_empty() {
            continue;
        }
        rows.push(parse_delimited_row(&line, sep));
    }
    if rows.is_empty() {
        return Ok(());
    }

    let table_exists: bool = conn
        .query_row(
            "SELECT COUNT(*) > 0 FROM sqlite_schema WHERE type='table' AND lower(name)=lower(?1);",
            [table_name],
            |r| r.get(0),
        )
        .unwrap_or(false);

    let data_start = if !table_exists {
        let headers = &rows[0];
        let cols: Vec<String> = headers
            .iter()
            .map(|h| format!("\"{}\" TEXT", h.replace('"', "\"\"")))
            .collect();
        let create_sql = format!(
            "CREATE TABLE \"{}\"({});",
            table_name.replace('"', "\"\""),
            cols.join(", ")
        );
        conn.execute(&create_sql, []).map_err(|e| e.to_string())?;
        1
    } else {
        0
    };

    let ncols = rows[0].len();
    let placeholders: Vec<&str> = vec!["?"; ncols];
    let ins_sql = format!(
        "INSERT INTO \"{}\" VALUES({});",
        table_name.replace('"', "\"\""),
        placeholders.join(",")
    );

    let tx = conn.transaction().map_err(|e| e.to_string())?;
    {
        let mut stmt = tx.prepare(&ins_sql).map_err(|e| e.to_string())?;
        for row in &rows[data_start..] {
            let mut padded: Vec<&str> = row.iter().map(String::as_str).collect();
            padded.resize(ncols, "");
            stmt.execute(rusqlite::params_from_iter(padded))
                .map_err(|e| e.to_string())?;
        }
    }
    tx.commit().map_err(|e| e.to_string())?;
    Ok(())
}

fn parse_delimited_row(line: &str, sep: &str) -> Vec<String> {
    if sep == "," {
        let mut out = Vec::new();
        let mut cur = String::new();
        let mut in_q = false;
        let chars: Vec<char> = line.chars().collect();
        let mut i = 0;
        while i < chars.len() {
            let c = chars[i];
            if in_q {
                if c == '"' {
                    if i + 1 < chars.len() && chars[i + 1] == '"' {
                        cur.push('"');
                        i += 2;
                        continue;
                    }
                    in_q = false;
                } else {
                    cur.push(c);
                }
            } else if c == '"' {
                in_q = true;
            } else if c == ',' {
                out.push(std::mem::take(&mut cur));
            } else {
                cur.push(c);
            }
            i += 1;
        }
        out.push(cur);
        out
    } else {
        line.split(sep).map(str::to_string).collect()
    }
}

/// Processes multi-line script input containing mixed `.dot` commands and SQL statements.
pub fn process_input_text(
    conn: &mut Connection,
    state: &mut ShellState,
    text: &str,
) -> Result<(), String> {
    let mut sql_buf = String::new();

    for raw_line in text.lines() {
        let trimmed = raw_line.trim();
        if sql_buf.trim().is_empty() && trimmed.starts_with('.') {
            execute_dot_command(conn, state, trimmed)?;
            if state.exit_requested.is_some() {
                return Ok(());
            }
            continue;
        }

        sql_buf.push_str(raw_line);
        sql_buf.push('\n');

        if is_complete_sql(&sql_buf) {
            let batch = std::mem::take(&mut sql_buf);
            execute_sql_batch(conn, state, &batch)?;
        }
    }

    if !sql_buf.trim().is_empty() {
        execute_sql_batch(conn, state, &sql_buf)?;
    }
    Ok(())
}

fn is_complete_sql(sql: &str) -> bool {
    let trimmed = sql.trim();
    if trimmed.is_empty() {
        return false;
    }
    let stmts = split_sql_statements(trimmed);
    stmts
        .last()
        .map(|s| s.trim_end().ends_with(';'))
        .unwrap_or(false)
}

/// Runs the `sqlite3` CLI with the given command-line arguments (`args[1..]`).
pub fn run_cli(args: &[String]) -> i32 {
    let mut state = ShellState::default();
    let mut init_files: Vec<PathBuf> = Vec::new();
    let mut pre_commands: Vec<String> = Vec::new();
    let mut force_batch = false;
    let mut force_interactive = false;
    let mut positional: Vec<String> = Vec::new();

    let mut i = 0usize;
    while i < args.len() {
        let arg = &args[i];
        if !arg.starts_with('-') || arg == "-" {
            positional.extend_from_slice(&args[i..]);
            break;
        }
        let flag = arg.trim_start_matches('-');
        let take_next = |i: &mut usize| -> Result<String, String> {
            if *i + 1 < args.len() {
                *i += 1;
                Ok(args[*i].clone())
            } else {
                Err(format!("Error: missing argument to {arg}"))
            }
        };

        match flag {
            "version" => {
                println!("{}", version_string());
                return 0;
            }
            "help" | "h" => {
                eprint!("{}", usage_text());
                return 0;
            }
            "bail" => state.bail = true,
            "batch" => force_batch = true,
            "interactive" => force_interactive = true,
            "echo" => state.echo = true,
            "header" => state.show_headers = true,
            "noheader" => state.show_headers = false,
            "readonly" => state.readonly = true,
            "ascii" | "box" | "column" | "csv" | "html" | "json" | "line" | "list"
            | "markdown" | "quote" | "table" | "tabs" => {
                let _ = state.apply_mode(flag, None);
            }
            "separator" => match take_next(&mut i) {
                Ok(v) => state.col_sep = v,
                Err(e) => {
                    eprintln!("{e}");
                    return 1;
                }
            },
            "newline" => match take_next(&mut i) {
                Ok(v) => state.row_sep = v,
                Err(e) => {
                    eprintln!("{e}");
                    return 1;
                }
            },
            "nullvalue" => match take_next(&mut i) {
                Ok(v) => state.null_value = v,
                Err(e) => {
                    eprintln!("{e}");
                    return 1;
                }
            },
            "cmd" => match take_next(&mut i) {
                Ok(v) => pre_commands.push(v),
                Err(e) => {
                    eprintln!("{e}");
                    return 1;
                }
            },
            "init" => match take_next(&mut i) {
                Ok(v) => init_files.push(PathBuf::from(v)),
                Err(e) => {
                    eprintln!("{e}");
                    return 1;
                }
            },
            "lookaside" | "pagecache" => {
                let _ = take_next(&mut i);
                let _ = take_next(&mut i);
            }
            "maxsize" | "mmap" | "nonce" | "vfs" => {
                let _ = take_next(&mut i);
            }
            "append" | "deserialize" | "memtrace" | "nofollow" | "pcachetrace" | "safe"
            | "stats" | "unsafe-testing" | "zip" => {}
            _ => {
                eprintln!("{}: Error: unknown option: {arg}", args[0]);
                eprintln!("Use -help for a list of options.");
                return 1;
            }
        }
        i += 1;
    }

    let db_arg = positional.first().map(String::as_str);
    let sql_args = if positional.len() > 1 {
        &positional[1..]
    } else {
        &[]
    };

    let mut conn = match open_database(db_arg, state.readonly) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("Error: unable to open database \"{}\": {e}", db_arg.unwrap_or(":memory:"));
            return 1;
        }
    };
    state.db_path = db_arg.filter(|&s| s != ":memory:").map(PathBuf::from);

    for init_file in &init_files {
        if let Ok(content) = fs::read_to_string(init_file) {
            if let Err(e) = process_input_text(&mut conn, &mut state, &content) {
                eprintln!("{e}");
                if state.bail {
                    return 1;
                }
            }
        }
    }

    for cmd in &pre_commands {
        let res = if cmd.trim_start().starts_with('.') {
            execute_dot_command(&mut conn, &mut state, cmd.trim())
        } else {
            execute_sql_batch(&conn, &mut state, cmd)
        };
        if let Err(e) = res {
            eprintln!("{e}");
            if state.bail {
                return 1;
            }
        }
        if let Some(code) = state.exit_requested {
            return code;
        }
    }

    if !sql_args.is_empty() {
        for s in sql_args {
            let res = if s.trim_start().starts_with('.') {
                execute_dot_command(&mut conn, &mut state, s.trim())
            } else {
                execute_sql_batch(&conn, &mut state, s)
            };
            if let Err(e) = res {
                eprintln!("{e}");
                return 1;
            }
            if let Some(code) = state.exit_requested {
                return code;
            }
        }
        return 0;
    }

    let stdin = io::stdin();
    let is_tty = (stdin.is_terminal() && !force_batch) || force_interactive;
    if !is_tty {
        let mut all_input = String::new();
        if stdin.lock().read_to_string(&mut all_input).is_ok() {
            if let Err(e) = process_input_text(&mut conn, &mut state, &all_input) {
                eprintln!("{e}");
                return 1;
            }
        }
        return state.exit_requested.unwrap_or(0);
    }

    println!("SQLite version {}", version_string());
    println!("Enter \".help\" for usage hints.");
    if db_arg.is_none() {
        println!("Connected to a transient in-memory database.");
        println!("Use \".open FILENAME\" to reopen on a persistent database.");
    }

    let mut sql_buf = String::new();
    let mut handle = stdin.lock();
    loop {
        let prompt = if sql_buf.trim().is_empty() {
            "sqlite> "
        } else {
            "   ...> "
        };
        print!("{prompt}");
        let _ = io::stdout().flush();

        let mut line = String::new();
        match handle.read_line(&mut line) {
            Ok(0) => break,
            Ok(_) => {}
            Err(_) => break,
        }

        let trimmed = line.trim();
        if sql_buf.trim().is_empty() && trimmed.starts_with('.') {
            if let Err(e) = execute_dot_command(&mut conn, &mut state, trimmed) {
                eprintln!("{e}");
                if state.bail {
                    return 1;
                }
            }
            if let Some(code) = state.exit_requested {
                return code;
            }
            continue;
        }

        sql_buf.push_str(&line);
        if is_complete_sql(&sql_buf) {
            let batch = std::mem::take(&mut sql_buf);
            if let Err(e) = execute_sql_batch(&conn, &mut state, &batch) {
                eprintln!("{e}");
                if state.bail {
                    return 1;
                }
            }
        }
    }

    state.exit_requested.unwrap_or(0)
}

fn open_database(db_arg: Option<&str>, readonly: bool) -> Result<Connection, rusqlite::Error> {
    match db_arg {
        None | Some("") | Some(":memory:") => Connection::open_in_memory(),
        Some(path) => {
            if readonly {
                Connection::open_with_flags(path, OpenFlags::SQLITE_OPEN_READ_ONLY)
            } else {
                Connection::open(path)
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_sql_execution_and_modes() {
        let dir = tempfile::tempdir().unwrap();
        let db_path = dir.path().join("test.db");
        let out_json = dir.path().join("out.json");

        let mut conn = Connection::open(&db_path).unwrap();
        let mut state = ShellState::default();
        process_input_text(
            &mut conn,
            &mut state,
            "CREATE TABLE items(id INTEGER PRIMARY KEY, name TEXT, score REAL);\n\
             INSERT INTO items VALUES(1, 'alpha', 9.5), (2, 'beta', 8.0);\n",
        )
        .unwrap();

        execute_dot_command(&mut conn, &mut state, ".mode json").unwrap();
        execute_dot_command(
            &mut conn,
            &mut state,
            &format!(".once \"{}\"", out_json.display().to_string().replace('\\', "/")),
        )
        .unwrap();
        execute_sql_batch(&conn, &mut state, "SELECT id, name, score FROM items ORDER BY id;")
            .unwrap();

        let json_str = fs::read_to_string(&out_json).unwrap();
        assert_eq!(
            json_str.trim(),
            "[{\"id\":1,\"name\":\"alpha\",\"score\":9.5},\n{\"id\":2,\"name\":\"beta\",\"score\":8.0}]"
        );
    }
}
