#[path = "editor_floem.rs"]
mod editor;
mod lexer;

use editor::{ColoredLine, Editor};
use pyo3::exceptions::{PyIOError, PyValueError};
use pyo3::prelude::*;

#[pyclass(module = "quill_native._engine")]
struct Document {
    inner: Editor,
}

#[pymethods]
impl Document {
    #[new]
    #[pyo3(signature = (text="", path=""))]
    fn new(text: &str, path: &str) -> Self {
        Self {
            inner: Editor::from_text(text, path),
        }
    }
    fn load(&mut self, path: &str) -> PyResult<()> {
        let loaded = Editor::open(path).map_err(|e| PyIOError::new_err(e.to_string()))?;
        self.inner = loaded;
        Ok(())
    }
    fn save(&mut self, path: &str) -> PyResult<()> {
        self.inner
            .save(path)
            .map_err(|e| PyIOError::new_err(e.to_string()))
    }
    fn line_count(&self) -> usize {
        self.inner.line_count()
    }
    fn byte_count(&self) -> usize {
        self.inner.byte_count()
    }
    fn char_count(&self) -> usize {
        self.inner.char_count()
    }
    fn dirty(&self) -> bool {
        self.inner.dirty()
    }
    fn can_undo(&self) -> bool {
        self.inner.can_undo()
    }
    fn can_redo(&self) -> bool {
        self.inner.can_redo()
    }
    fn language(&self) -> &str {
        &self.inner.language
    }
    fn eol(&self) -> &str {
        &self.inner.eol
    }
    fn revision(&self) -> u64 {
        self.inner.id
    }
    fn line(&self, row: usize) -> String {
        self.inner.line(row)
    }
    fn line_length(&self, row: usize) -> usize {
        self.inner.line_len(row)
    }
    fn caret(&self) -> (usize, usize) {
        self.inner.position(self.inner.caret_offset())
    }
    fn selection(&self) -> (usize, usize, usize, usize) {
        let (a, b) = self.inner.selection();
        let (ar, ac) = self.inner.position(a);
        let (br, bc) = self.inner.position(b);
        (ar, ac, br, bc)
    }
    fn has_selection(&self) -> bool {
        self.inner.has_selection()
    }
    fn selected_text(&self) -> String {
        self.inner.selected_text()
    }
    #[pyo3(signature = (row, column, extend=false))]
    fn set_cursor(&mut self, row: usize, column: usize, extend: bool) {
        self.inner.set_cursor(row, column, extend);
    }
    #[pyo3(signature = (direction, extend=false))]
    fn move_cursor(&mut self, direction: &str, extend: bool) {
        self.inner.move_cursor(direction, extend);
    }
    fn select_word(&mut self, row: usize, column: usize) {
        self.inner.select_word(row, column);
    }
    fn select_all(&mut self) {
        self.inner.select_all();
    }
    #[pyo3(signature = (text, typing=false))]
    fn insert(&mut self, text: &str, typing: bool) -> usize {
        self.inner.insert(text, typing)
    }
    fn delete(&mut self, backwards: bool) -> usize {
        self.inner.delete(backwards)
    }
    fn newline(&mut self) -> usize {
        self.inner.newline()
    }
    #[pyo3(signature = (outdent=false))]
    fn indent(&mut self, outdent: bool) -> usize {
        self.inner.indent(outdent)
    }
    fn backend(&self) -> &'static str {
        "floem-editor-core/0.2.0"
    }
    fn indent_unit(&self) -> String {
        self.inner.buffer.indent_unit().to_owned()
    }
    fn history_info(&self) -> (usize, usize, usize, usize) {
        self.inner.history_info()
    }
    fn undo(&mut self) -> bool {
        self.inner.history(false)
    }
    fn redo(&mut self) -> bool {
        self.inner.history(true)
    }
    fn viewport(&mut self, first: usize, count: usize) -> (Vec<ColoredLine>, bool) {
        self.inner.viewport(first, count)
    }
    fn set_language(&mut self, language: &str) -> PyResult<()> {
        if !lexer::LANGUAGES.contains(&language) {
            return Err(PyValueError::new_err("unsupported language"));
        }
        self.inner.set_language(language);
        Ok(())
    }
    #[pyo3(signature = (needle, backwards=false))]
    fn find(&mut self, needle: &str, backwards: bool) -> bool {
        self.inner.find(needle, backwards)
    }
}

#[pyfunction]
fn languages() -> Vec<&'static str> {
    lexer::LANGUAGES.to_vec()
}

#[pymodule]
fn _engine(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<Document>()?;
    m.add_function(wrap_pyfunction!(languages, m)?)?;
    Ok(())
}
