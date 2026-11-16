//! Adapter for published floem-editor-core. Core coordinates are UTF-8 bytes.
use crate::lexer::{self, State as LexState};
use floem_editor_core::{
    buffer::{rope_text::RopeText, Buffer},
    command::EditCommand,
    cursor::{Cursor, CursorMode},
    editor::{Action, EditConf, EditType},
    line_ending::LineEnding,
    mode::Mode,
    register::{Clipboard, Register},
    selection::{InsertDrift, Selection},
    xi_rope::{
        rope::{BaseMetric, Utf16CodeUnitsMetric},
        tree::TreeBuilder,
        DeltaElement, Rope, RopeDelta,
    },
};
use std::{
    collections::VecDeque,
    fs::File,
    io::{self, BufReader, BufWriter, Read, Write},
    path::Path,
    time::{Duration, Instant},
};

pub const MAX_RENDER_CHARS: usize = 8192;
const MAX_LEX_BYTES: usize = 32768;
const HISTORY_LIMIT: usize = 128;
const HISTORY_BUDGET: usize = 16 * 1024 * 1024;
pub type ColoredLine = (String, Vec<(usize, usize, u8)>);

// Replay records only compact upstream history. Edits and undo always execute
// in Floem Buffer; these are not a second text-undo implementation.
struct Replacement {
    start: usize,
    end: usize,
    text: Rope,
}
struct Step {
    edits: Vec<Replacement>,
}
impl Step {
    fn cost(&self) -> usize {
        self.edits
            .iter()
            .map(|e| e.end - e.start + e.text.len())
            .sum()
    }
}
struct Group {
    base: Rope,
    before: CursorMode,
    after: CursorMode,
    before_id: u64,
    after_id: u64,
    cost: usize,
    steps: Vec<Step>,
}
struct NoClipboard;
impl Clipboard for NoClipboard {
    fn get_string(&mut self) -> Option<String> {
        None
    }
    fn put_string(&mut self, _: impl AsRef<str>) {}
}

pub struct Editor {
    pub buffer: Buffer,
    pub cursor: Cursor,
    pub language: String,
    pub eol: String,
    pub bom: bool,
    pub id: u64,
    saved_id: u64,
    state_id: u64,
    next_state_id: u64,
    groups: VecDeque<Group>,
    applied: usize,
    history_floor: usize,
    core_operations: usize,
    compactions: usize,
    compacted_cost: usize,
    lex_states: Vec<LexState>,
    last_typing: Option<(Instant, usize)>,
}
impl Default for Editor {
    fn default() -> Self {
        Self::from_text("", "")
    }
}

impl Editor {
    fn from_rope(mut text: Rope, path: &str) -> Self {
        let bom = text.len() >= 3
            && text.byte_at(0) == 0xef
            && text.byte_at(1) == 0xbb
            && text.byte_at(2) == 0xbf;
        if bom {
            text.edit(..3, "");
        }
        let mut buffer = Buffer::new(text);
        buffer.detect_indent(|| floem_editor_core::indent::IndentStyle::Spaces(4));
        buffer.set_pristine();
        let eol = match buffer.line_ending() {
            LineEnding::CrLf => "\r\n",
            LineEnding::Lf => "\n",
        }
        .into();
        Self {
            buffer,
            cursor: Cursor::origin(false),
            language: lexer::detect(path).into(),
            eol,
            bom,
            id: 0,
            saved_id: 0,
            state_id: 0,
            next_state_id: 1,
            groups: VecDeque::new(),
            applied: 0,
            history_floor: 0,
            core_operations: 0,
            compactions: 0,
            compacted_cost: 0,
            lex_states: vec![LexState::default()],
            last_typing: None,
        }
    }
    pub fn from_text(text: &str, path: &str) -> Self {
        Self::from_rope(Rope::from(text), path)
    }
    pub fn open(path: &str) -> io::Result<Self> {
        let text = read_utf8(BufReader::new(File::open(path)?))?;
        // Buffer::new normalizes bare CR. Refuse those files instead of silently
        // changing a file that the host considers pristine.
        let mut previous_cr = false;
        for byte in text.iter_chunks(..).flat_map(str::bytes) {
            if previous_cr && byte != b'\n' {
                return Err(bare_cr_error());
            }
            previous_cr = byte == b'\r';
        }
        if previous_cr {
            return Err(bare_cr_error());
        }
        Ok(Self::from_rope(text, path))
    }
    pub fn save(&mut self, path: &str) -> io::Result<()> {
        let destination = Path::new(path);
        let parent = destination
            .parent()
            .filter(|p| !p.as_os_str().is_empty())
            .unwrap_or(Path::new("."));
        let mut temp = tempfile::NamedTempFile::new_in(parent)?;
        if let Ok(metadata) = std::fs::metadata(destination) {
            std::fs::set_permissions(temp.path(), metadata.permissions())?;
        }
        {
            let mut writer = BufWriter::new(temp.as_file_mut());
            if self.bom {
                writer.write_all(b"\xef\xbb\xbf")?;
            }
            for chunk in self.buffer.text().iter_chunks(..) {
                writer.write_all(chunk.as_bytes())?;
            }
            writer.flush()?;
        }
        temp.as_file().sync_all()?;
        temp.persist(destination).map_err(|e| e.error)?;
        self.saved_id = self.state_id;
        self.buffer.set_pristine();
        self.break_typing();
        Ok(())
    }
    pub fn dirty(&self) -> bool {
        self.saved_id != self.state_id
    }
    pub fn can_undo(&self) -> bool {
        self.applied > self.history_floor
    }
    pub fn can_redo(&self) -> bool {
        self.applied < self.groups.len()
    }
    pub fn caret_offset(&self) -> usize {
        self.cursor.offset()
    }
    pub fn has_selection(&self) -> bool {
        !self.cursor.edit_selection(&self.buffer).is_caret()
    }
    pub fn selection(&self) -> (usize, usize) {
        let s = self.cursor.edit_selection(&self.buffer);
        (s.min_offset(), s.max_offset())
    }
    pub fn selected_text(&self) -> String {
        let (a, b) = self.selection();
        self.buffer.slice_to_cow(a..b).into_owned()
    }
    pub fn byte_count(&self) -> usize {
        self.buffer.len()
    }
    pub fn char_count(&self) -> usize {
        self.buffer
            .text()
            .iter_chunks(..)
            .map(|s| s.chars().count())
            .sum()
    }
    pub fn line_count(&self) -> usize {
        self.buffer.last_line() + 1
    }
    fn bounds(&self, row: usize) -> (usize, usize) {
        let row = row.min(self.line_count() - 1);
        let start = self.buffer.offset_of_line(row);
        let mut end = self.buffer.offset_of_line(row + 1);
        if end > start && self.buffer.text().byte_at(end - 1) == b'\n' {
            end -= 1;
        }
        if end > start && self.buffer.text().byte_at(end - 1) == b'\r' {
            end -= 1;
        }
        (start, end)
    }
    pub fn line_len(&self, row: usize) -> usize {
        let (a, b) = self.bounds(row);
        self.buffer
            .text()
            .iter_chunks(a..b)
            .map(|s| s.chars().count())
            .sum()
    }
    pub fn line(&self, row: usize) -> String {
        let (a, b) = self.bounds(row);
        self.buffer
            .text()
            .iter_chunks(a..b)
            .flat_map(str::chars)
            .take(MAX_RENDER_CHARS)
            .collect()
    }
    pub fn position(&self, offset: usize) -> (usize, usize) {
        let row = self.buffer.line_of_offset(offset.min(self.buffer.len()));
        let (start, end) = self.bounds(row);
        let at = self
            .buffer
            .text()
            .at_or_prev_codepoint_boundary(offset.min(end))
            .unwrap_or(start);
        let metric = |byte| {
            self.buffer
                .text()
                .convert_metrics::<BaseMetric, Utf16CodeUnitsMetric>(byte)
        };
        (row, metric(at) - metric(start))
    }
    pub fn index(&self, row: usize, column: usize) -> usize {
        let (a, b) = self.bounds(row);
        let text = self.buffer.text();
        let start = text.convert_metrics::<BaseMetric, Utf16CodeUnitsMetric>(a);
        let end = text.convert_metrics::<BaseMetric, Utf16CodeUnitsMetric>(b);
        let target = start + column.min(end - start);
        let mut at = text
            .convert_metrics::<Utf16CodeUnitsMetric, BaseMetric>(target)
            .min(b);
        // Xi rounds up inside surrogate pairs; the Qt adapter snaps before them.
        if text.convert_metrics::<BaseMetric, Utf16CodeUnitsMetric>(at) > target {
            at = text.prev_codepoint_offset(at).unwrap_or(a);
        }
        at
    }
    fn break_typing(&mut self) {
        self.last_typing = None;
        self.buffer.reset_edit_type();
    }
    pub fn set_cursor(&mut self, row: usize, column: usize, extend: bool) {
        self.cursor
            .set_offset(self.index(row, column), extend, false);
        self.cursor.history_selections.clear();
        self.break_typing();
    }
    pub fn select_all(&mut self) {
        self.cursor.mode = CursorMode::Insert(Selection::region(0, self.buffer.len()));
        self.break_typing();
    }
    pub fn move_cursor(&mut self, direction: &str, extend: bool) {
        let at = self.cursor.offset();
        let (row, col) = self.position(at);
        let next = match direction {
            "left" if !extend && self.has_selection() => self.selection().0,
            "right" if !extend && self.has_selection() => self.selection().1,
            "left" => self.buffer.prev_grapheme_offset(at, 1, 0),
            "right" => self.buffer.next_grapheme_offset(at, 1, self.buffer.len()),
            "up" => self.index(row.saturating_sub(1), col),
            "down" => self.index((row + 1).min(self.line_count() - 1), col),
            "home" => self.bounds(row).0,
            "end" => self.bounds(row).1,
            "doc_home" => 0,
            "doc_end" => self.buffer.len(),
            "word_left" => self.buffer.move_word_backward(at, Mode::Insert),
            "word_right" => self.buffer.move_word_forward(at),
            _ => at,
        };
        self.cursor.set_offset(next, extend, false);
        self.cursor.history_selections.clear();
        self.break_typing();
    }
    pub fn select_word(&mut self, row: usize, column: usize) {
        let (a, b) = self.buffer.select_word(self.index(row, column));
        self.cursor.mode = CursorMode::Insert(Selection::region(a, b));
        self.break_typing();
    }
    fn step(&self, delta: &RopeDelta) -> Step {
        let mut edits = Vec::new();
        let mut at = 0;
        let mut inserted = Rope::default();
        for element in &delta.els {
            match element {
                DeltaElement::Copy(start, end) => {
                    if *start > at || !inserted.is_empty() {
                        edits.push(Replacement {
                            start: at,
                            end: *start,
                            text: std::mem::take(&mut inserted),
                        });
                    }
                    at = *end;
                }
                DeltaElement::Insert(text) => {
                    let end = inserted.len();
                    inserted.edit(end..end, text.clone());
                }
            }
        }
        if at < delta.base_len || !inserted.is_empty() {
            edits.push(Replacement {
                start: at,
                end: delta.base_len,
                text: inserted,
            });
        }
        Step { edits }
    }
    fn remember(&mut self, base: Rope, before: CursorMode, step: Step, cost: usize, merge: bool) {
        self.groups.truncate(self.applied);
        let after = self.cursor.mode.clone();
        let next = self.next_state_id;
        self.next_state_id += 1;
        if merge {
            let group = self.groups.back_mut().expect("typing group exists");
            // Adjacent typing is represented by one compact replay record.
            let append = step.edits.len() == 1
                && group.steps.last().is_some_and(|last| {
                    last.edits.len() == 1
                        && last.edits[0].start == last.edits[0].end
                        && step.edits[0].start == step.edits[0].end
                        && last.edits[0].start + last.edits[0].text.len() == step.edits[0].start
                });
            if append {
                let last = &mut group.steps.last_mut().unwrap().edits[0];
                let end = last.text.len();
                last.text
                    .edit(end..end, step.edits.into_iter().next().unwrap().text);
            } else {
                group.steps.push(step);
            }
            group.cost += cost;
            group.after = after;
            group.after_id = next;
        } else {
            self.groups.push_back(Group {
                base,
                before,
                after,
                before_id: self.state_id,
                after_id: next,
                cost,
                steps: vec![step],
            });
        }
        self.applied = self.groups.len();
        self.history_floor = self
            .history_floor
            .max(self.applied.saturating_sub(HISTORY_LIMIT));
        self.state_id = next;
        self.id += 1;
        self.core_operations += 1;
        self.compact_if_needed();
    }
    pub fn insert(&mut self, text: &str, typing: bool) -> usize {
        let selection = self.cursor.edit_selection(&self.buffer);
        let (a, b) = self.selection();
        let first = self.buffer.line_of_offset(a);
        if a == b && text.is_empty() {
            return first;
        }
        let is_typing =
            typing && a == b && !text.contains(['\n', '\r']) && text.chars().count() == 1;
        let merge = is_typing
            && self.applied == self.groups.len()
            && self
                .last_typing
                .is_some_and(|(when, end)| end == a && when.elapsed() < Duration::from_millis(700));
        if !merge {
            self.buffer.reset_edit_type();
        }
        let before = self.cursor.mode.clone();
        let (base, delta, inval) = self.buffer.edit(
            [(&selection, text)],
            if is_typing {
                EditType::InsertChars
            } else {
                EditType::Other
            },
        );
        self.buffer.set_cursor_before(before.clone());
        self.cursor.mode =
            CursorMode::Insert(selection.apply_delta(&delta, true, InsertDrift::Default));
        self.buffer.set_cursor_after(self.cursor.mode.clone());
        let step = self.step(&delta);
        let cost = step.cost();
        self.remember(base, before, step, cost, merge);
        self.last_typing = if is_typing {
            Some((Instant::now(), self.cursor.offset()))
        } else {
            None
        };
        self.lex_states
            .truncate((inval.start_line + 1).min(self.lex_states.len()));
        first
    }
    pub fn delete(&mut self, backwards: bool) -> usize {
        let before = self.cursor.mode.clone();
        if !self.has_selection() {
            let at = self.cursor.offset();
            let next = if backwards {
                self.buffer.prev_grapheme_offset(at, 1, 0)
            } else {
                self.buffer.next_grapheme_offset(at, 1, self.buffer.len())
            };
            self.cursor.mode = CursorMode::Insert(Selection::region(at, next));
        }
        let id = self.id;
        let first = self.insert("", false);
        if self.id != id {
            self.buffer.set_cursor_before(before.clone());
            self.groups.back_mut().unwrap().before = before;
        }
        first
    }
    pub fn newline(&mut self) -> usize {
        let (a, _) = self.selection();
        let row = self.buffer.line_of_offset(a);
        let before = self.buffer.slice_to_cow(self.bounds(row).0..a);
        let indent: String = before
            .chars()
            .take_while(|c| *c == ' ' || *c == '\t')
            .collect();
        let extra = if before.trim_end().ends_with(['{', '[', '(', ':']) {
            self.buffer.indent_unit()
        } else {
            ""
        };
        self.insert(&format!("\n{indent}{extra}"), false)
    }
    pub fn indent(&mut self, outdent: bool) -> usize {
        self.break_typing();
        let before = self.cursor.mode.clone();
        let base = self.buffer.text().clone();
        let first = self.buffer.line_of_offset(self.selection().0);
        if outdent {
            let (_, end) = self.selection();
            let mut last = self.buffer.line_of_offset(end);
            if last > first && end == self.buffer.offset_of_line(last) {
                last -= 1;
            }
            let has_indent = (first..=last).any(|row| {
                let (a, b) = self.bounds(row);
                a < b && matches!(self.buffer.text().byte_at(a), b' ' | b'\t')
            });
            if !has_indent {
                return first;
            }
        }
        let deltas = Action::do_edit(
            &mut self.cursor,
            &mut self.buffer,
            &if outdent {
                EditCommand::OutdentLine
            } else {
                EditCommand::IndentLine
            },
            &mut NoClipboard,
            &mut Register::default(),
            EditConf {
                comment_token: "",
                modal: false,
                smart_tab: true,
                keep_indent: true,
                auto_indent: true,
            },
        );
        if let Some((_, delta, inval)) = deltas.first() {
            debug_assert_eq!(deltas.len(), 1);
            self.buffer.set_cursor_before(before.clone());
            self.buffer.set_cursor_after(self.cursor.mode.clone());
            let step = self.step(delta);
            let cost = step.cost();
            self.remember(base, before, step, cost, false);
            self.lex_states
                .truncate((inval.start_line + 1).min(self.lex_states.len()));
        }
        self.cursor.history_selections.clear();
        first
    }
    pub fn history(&mut self, redo: bool) -> bool {
        if if redo {
            !self.can_redo()
        } else {
            !self.can_undo()
        } {
            return false;
        }
        let result = if redo {
            self.buffer.do_redo()
        } else {
            self.buffer.do_undo()
        };
        let Some((_, _, inval, cursor)) = result else {
            return false;
        };
        if redo {
            self.state_id = self.groups[self.applied].after_id;
            self.applied += 1;
        } else {
            self.applied -= 1;
            self.state_id = self.groups[self.applied].before_id;
        }
        if let Some(cursor) = cursor {
            self.cursor.mode = cursor;
        }
        self.id += 1;
        self.core_operations += 1;
        self.break_typing();
        self.lex_states
            .truncate((inval.start_line + 1).min(self.lex_states.len()));
        self.compact_if_needed();
        true
    }
    // No public history-GC API exists in this core release. Periodically rebuild
    // Buffer and replay retained groups through its own edit/undo APIs. Rebuild
    // work is amortized; shared Rope nodes avoid copies of whole documents.
    fn compact_if_needed(&mut self) {
        let cost: usize = self.groups.iter().map(|g| g.cost).sum();
        let group_trigger =
            self.groups.len() >= HISTORY_LIMIT * 2 && self.applied >= HISTORY_LIMIT * 2;
        if !group_trigger
            && self.core_operations < 1024
            && cost.saturating_sub(self.compacted_cost) < HISTORY_BUDGET * 2
        {
            return;
        }
        let mut drop_count = self
            .history_floor
            .max(self.applied.saturating_sub(HISTORY_LIMIT));
        let mut retained: usize = self.groups.iter().skip(drop_count).map(|g| g.cost).sum();
        while retained > HISTORY_BUDGET && drop_count + 1 < self.applied {
            retained -= self.groups[drop_count].cost;
            drop_count += 1;
        }
        self.groups.drain(..drop_count);
        self.applied -= drop_count;
        self.history_floor = self.history_floor.saturating_sub(drop_count);
        let Some(first) = self.groups.front() else {
            return;
        };
        let mut buffer = Buffer::new(first.base.clone());
        buffer.set_line_ending(self.buffer.line_ending());
        buffer.detect_indent(|| self.buffer.indent_style());
        let mut operations = 0;
        for group in &self.groups {
            buffer.reset_edit_type();
            for step in &group.steps {
                let replacements: Vec<_> = step
                    .edits
                    .iter()
                    .map(|edit| {
                        (
                            Selection::region(edit.start, edit.end),
                            edit.text.slice_to_cow(..),
                        )
                    })
                    .collect();
                let edits: Vec<_> = replacements
                    .iter()
                    .map(|(selection, value)| (selection, value.as_ref()))
                    .collect();
                buffer.edit(&edits, EditType::InsertChars);
                buffer.set_cursor_before(group.before.clone());
                buffer.set_cursor_after(group.after.clone());
                operations += 1;
            }
        }
        for _ in self.applied..self.groups.len() {
            buffer.do_undo();
            operations += 1;
        }
        self.buffer = buffer;
        self.core_operations = operations;
        self.compactions += 1;
        self.compacted_cost = self.groups.iter().map(|g| g.cost).sum();
    }
    pub fn history_info(&self) -> (usize, usize, usize, usize) {
        (
            self.applied.saturating_sub(self.history_floor),
            self.groups.len() - self.applied,
            self.groups.len(),
            self.compactions,
        )
    }
    pub fn set_language(&mut self, language: &str) {
        self.language = if lexer::LANGUAGES.contains(&language) {
            language
        } else {
            "Plain Text"
        }
        .into();
        self.lex_states.truncate(1);
    }
    fn lex_text(&self, row: usize) -> String {
        let (a, b) = self.bounds(row);
        if b - a > MAX_LEX_BYTES {
            return String::new();
        }
        self.buffer
            .slice_to_cow(a..self.buffer.offset_of_line(row + 1))
            .into_owned()
    }
    pub fn prepare_colors(&mut self, end: usize, budget: usize) -> bool {
        let end = end.min(self.line_count());
        if self.language == "Plain Text" {
            return true;
        }
        let target = end.min(self.lex_states.len() - 1 + budget);
        let mut bytes = 0;
        while self.lex_states.len() <= target {
            let row = self.lex_states.len() - 1;
            let text = self.lex_text(row);
            bytes += text.len();
            let (_, state) = lexer::lex(&text, &self.language, *self.lex_states.last().unwrap());
            self.lex_states.push(state);
            if bytes >= 128 * 1024 {
                break;
            }
        }
        self.lex_states.len() > end
    }
    pub fn viewport(&mut self, first: usize, count: usize) -> (Vec<ColoredLine>, bool) {
        let first = first.min(self.line_count() - 1);
        let end = (first + count.min(300)).min(self.line_count());
        let ready = self.prepare_colors(end, 1500);
        let lines = (first..end)
            .map(|row| {
                let text = self.line(row);
                let (a, b) = self.bounds(row);
                let spans = if self.language != "Plain Text"
                    && row < self.lex_states.len()
                    && b - a <= MAX_LEX_BYTES
                {
                    lexer::lex(&text, &self.language, self.lex_states[row]).0
                } else {
                    Vec::new()
                };
                (text, spans)
            })
            .collect();
        (lines, ready)
    }
    pub fn find(&mut self, needle: &str, backwards: bool) -> bool {
        let pattern = needle.as_bytes();
        if pattern.is_empty() {
            return false;
        }
        let mut table = vec![0; pattern.len()];
        for i in 1..pattern.len() {
            let mut j = table[i - 1];
            while j > 0 && pattern[i] != pattern[j] {
                j = table[j - 1];
            }
            if pattern[i] == pattern[j] {
                j += 1;
            }
            table[i] = j;
        }
        let (a, b) = self.selection();
        let scan = |start: usize, end: usize, last: bool| {
            let mut matched = 0;
            let mut found = None;
            for (offset, byte) in self
                .buffer
                .text()
                .iter_chunks(start..end)
                .flat_map(str::bytes)
                .enumerate()
            {
                while matched > 0 && byte != pattern[matched] {
                    matched = table[matched - 1];
                }
                if byte == pattern[matched] {
                    matched += 1;
                }
                if matched == pattern.len() {
                    found = Some(start + offset + 1 - pattern.len());
                    if !last {
                        break;
                    }
                    matched = table[matched - 1];
                }
            }
            found
        };
        let result = if backwards {
            scan(0, a, true).or_else(|| scan(a, self.buffer.len(), true))
        } else {
            scan(b, self.buffer.len(), false).or_else(|| scan(0, b, false))
        };
        if let Some(start) = result {
            self.cursor.mode = CursorMode::Insert(Selection::region(start, start + pattern.len()));
            self.break_typing();
            true
        } else {
            false
        }
    }
}
fn bare_cr_error() -> io::Error {
    io::Error::new(io::ErrorKind::Unsupported,"Bare CR line endings are unsupported by Floem. Convert explicitly to LF or CRLF first; the original file was not changed.")
}
fn read_utf8(mut input: impl Read) -> io::Result<Rope> {
    let mut builder = TreeBuilder::new();
    let mut pending = Vec::with_capacity(65540);
    let mut chunk = [0; 65536];
    loop {
        let len = input.read(&mut chunk)?;
        if len == 0 {
            break;
        }
        pending.extend_from_slice(&chunk[..len]);
        match std::str::from_utf8(&pending) {
            Ok(text) => {
                builder.push_str(text);
                pending.clear();
            }
            Err(error) => {
                if error.error_len().is_some() {
                    return Err(io::Error::new(io::ErrorKind::InvalidData, error));
                }
                let valid = error.valid_up_to();
                builder.push_str(std::str::from_utf8(&pending[..valid]).unwrap());
                pending.drain(..valid);
            }
        }
    }
    if !pending.is_empty() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "incomplete UTF-8 sequence",
        ));
    }
    Ok(builder.build())
}

#[cfg(test)]
mod tests {
    use super::*;
    fn value(e: &Editor) -> String {
        e.buffer.text().to_string()
    }
    #[test]
    fn unicode_crlf_and_cursor_history() {
        let mut e = Editor::from_text("a😀e\u{301}\r\nx", "x.rs");
        e.set_cursor(0, 5, false);
        e.delete(true);
        assert_eq!(value(&e), "a😀\r\nx");
        e.history(false);
        assert_eq!(e.position(e.caret_offset()), (0, 5));
        assert!(!e.has_selection());
        e.history(true);
        e.move_cursor("right", false);
        assert_eq!(e.position(e.caret_offset()), (1, 0));
        e.delete(true);
        assert_eq!(value(&e), "a😀x");
    }
    #[test]
    fn surrogate_positions_snap_before_emoji() {
        let mut e = Editor::from_text("😀🦀\n日本語", "");
        e.set_cursor(0, 1, false);
        assert_eq!(e.position(e.caret_offset()), (0, 0));
        e.set_cursor(0, 3, false);
        assert_eq!(e.position(e.caret_offset()), (0, 2));
        e.set_cursor(9, usize::MAX, false);
        assert_eq!(e.position(e.caret_offset()), (1, 3));
    }
    #[test]
    fn core_history_compaction_retains_latest_128_groups() {
        let mut e = Editor::default();
        for _ in 0..500 {
            e.insert("x", false);
        }
        assert!(e.history_info().3 > 0);
        for _ in 0..128 {
            assert!(e.history(false));
        }
        assert!(!e.history(false));
        assert_eq!(value(&e), "x".repeat(372));
        for _ in 0..128 {
            assert!(e.history(true));
        }
        assert_eq!(value(&e), "x".repeat(500));
        assert!(!e.can_redo());
        e.history(false);
        e.insert("🦀", false);
        assert!(!e.can_redo());
    }
    #[test]
    fn long_typing_run_compacts_and_remains_one_undo() {
        let mut e = Editor::default();
        for _ in 0..5000 {
            e.insert("é", true);
        }
        assert!(e.history_info().3 >= 4);
        assert_eq!(e.history_info().0, 1);
        e.history(false);
        assert_eq!(value(&e), "");
        assert!(!e.dirty());
        e.history(true);
        assert_eq!(value(&e), "é".repeat(5000));
    }
    #[test]
    fn multi_range_replay_preserves_mixed_endings() {
        let mut e = Editor::from_text("a\r\nb\nc", "x.rs");
        e.select_all();
        e.indent(false);
        assert_eq!(value(&e), "    a\r\n    b\n    c");
        e.move_cursor("doc_end", false);
        for _ in 0..1100 {
            e.insert("x", true);
        }
        assert!(e.history_info().3 > 0);
        e.history(false);
        assert_eq!(value(&e), "    a\r\n    b\n    c");
        e.history(false);
        assert_eq!(value(&e), "a\r\nb\nc");
        e.history(true);
        assert_eq!(value(&e), "    a\r\n    b\n    c");
    }
    #[test]
    fn streaming_decoder_handles_every_byte_boundary() {
        struct Tiny<'a>(&'a [u8]);
        impl Read for Tiny<'_> {
            fn read(&mut self, out: &mut [u8]) -> io::Result<usize> {
                if self.0.is_empty() {
                    return Ok(0);
                }
                out[0] = self.0[0];
                self.0 = &self.0[1..];
                Ok(1)
            }
        }
        let text = "\u{feff}😀日本語\r\n🦀";
        assert_eq!(read_utf8(Tiny(text.as_bytes())).unwrap().to_string(), text);
        assert!(read_utf8(Tiny(&[0xf0, 0x9f])).is_err());
    }
    #[test]
    fn save_and_compaction_keep_dirty_tracking() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("x.rs");
        let mut e = Editor::default();
        for _ in 0..280 {
            e.insert("x", false);
        }
        e.save(path.to_str().unwrap()).unwrap();
        for _ in 0..110 {
            e.insert("x", false);
        }
        for _ in 0..110 {
            e.history(false);
        }
        assert!(!e.dirty());
        assert_eq!(value(&e), "x".repeat(280));
    }
}
