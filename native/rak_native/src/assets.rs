// SPDX-License-Identifier: MPL-2.0
//! A small immutable filename index; Qt icons/pixmaps remain on the GUI thread.
use crate::path_string;
use pyo3::prelude::*;
use serde::Serialize;
use std::collections::BTreeMap;
use std::fs;
use std::path::PathBuf;

#[derive(Default, Serialize)]
struct Catalog {
    entries: Vec<String>,
    files: Vec<String>,
}

#[pyfunction]
pub(crate) fn asset_index_json(py: Python<'_>, directories: Vec<PathBuf>) -> PyResult<String> {
    py.detach(move || {
        let mut output = BTreeMap::new();
        for directory in directories {
            let mut catalog = Catalog::default();
            if let Ok(entries) = fs::read_dir(&directory) {
                for entry in entries.flatten() {
                    let Ok(kind) = entry.file_type() else {
                        continue;
                    };
                    // Match exists()/isfile(): a broken OS symlink is absent.
                    let file = if kind.is_symlink() {
                        match fs::metadata(entry.path()) {
                            Ok(info) => info.is_file(),
                            Err(_) => continue,
                        }
                    } else {
                        kind.is_file()
                    };
                    let name = entry.file_name().to_string_lossy().into_owned();
                    if file {
                        catalog.files.push(name.clone());
                    }
                    catalog.entries.push(name);
                }
            }
            catalog.entries.sort();
            catalog.files.sort();
            output.insert(path_string(&directory), catalog);
        }
        serde_json::to_string(&output)
    })
    .map_err(|error| pyo3::exceptions::PyValueError::new_err(error.to_string()))
}
