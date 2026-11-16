//! Native document processing. Viewport-sized rendering.
use std::path::PathBuf;

#[derive(Debug)]
struct Workspace {
    name: String,
    root: PathBuf,
}

impl Workspace {
    fn greeting(&self) -> String {
        // Multiline comments and raw strings are colored too.
        format!("Welcome to {} 🦀", self.name)
    }
}

fn main() {
    let workspace = Workspace {
        name: "Quill Native".into(),
        root: PathBuf::from("."),
    };
    println!("{}", workspace.greeting());
    let native = true;
    let buffer_size: usize = 8192;
}
