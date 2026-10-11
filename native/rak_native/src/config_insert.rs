// SPDX-License-Identifier: MPL-2.0
use pyo3::prelude::*;
use std::collections::{BTreeMap, HashSet};

fn python_strip(s: &str) -> &str {
    s.trim_matches(super::python_whitespace)
}

fn python_split_first(s: &str) -> Option<&str> {
    let s = python_strip(s);
    if s.is_empty() {
        return None;
    }
    for (idx, ch) in s.char_indices() {
        if super::python_whitespace(ch) {
            return Some(&s[..idx]);
        }
    }
    Some(s)
}

pub fn perform_config_insert(
    lines: &[String],
    entries: &[(String, String)],
    skip_existing: bool,
) -> String {
    let mut existing_keys = HashSet::new();
    if skip_existing {
        for ln in lines {
            let s = python_strip(ln);
            if !s.is_empty() && !s.starts_with('#') {
                if let Some(key) = python_split_first(s) {
                    existing_keys.insert(key);
                }
            }
        }
    }

    let mut pending: BTreeMap<String, Vec<usize>> = BTreeMap::new();
    for (idx, (nk, _)) in entries.iter().enumerate() {
        if skip_existing && existing_keys.contains(nk.as_str()) {
            continue;
        }
        pending.entry(nk.clone()).or_default().push(idx);
    }

    let mut final_lines: Vec<String> = Vec::new();
    let mut inserted: HashSet<String> = HashSet::new();

    for ln in lines {
        let s = python_strip(ln);
        if !s.is_empty() && !s.starts_with('#') {
            if let Some(key) = python_split_first(s) {
                let remaining = pending.split_off(key);
                let drained = std::mem::replace(&mut pending, remaining);
                if !drained.is_empty() {
                    let mut eligible_indices: Vec<usize> = Vec::new();
                    for (_k, idxs) in drained {
                        eligible_indices.extend(idxs);
                    }
                    eligible_indices.sort_unstable();
                    for idx in eligible_indices {
                        let (nk, nrest) = &entries[idx];
                        if inserted.insert(nk.clone()) {
                            final_lines.push(format!("{nk} {nrest}"));
                        }
                    }
                }
            }
        }
        final_lines.push(ln.clone());
    }

    for (nk, nrest) in entries {
        if !inserted.contains(nk) && (!skip_existing || !existing_keys.contains(nk.as_str())) {
            final_lines.push(format!("{nk} {nrest}"));
        }
    }

    let mut result = final_lines.join("\n");
    result.push('\n');
    result
}

#[pyfunction]
#[pyo3(signature = (lines, entries, skip_existing = false))]
pub fn config_insert_lines(
    py: Python<'_>,
    lines: Vec<String>,
    entries: Vec<(String, String)>,
    skip_existing: bool,
) -> PyResult<String> {
    let result = py.detach(|| perform_config_insert(&lines, &entries, skip_existing));
    Ok(result)
}

#[pyfunction]
#[pyo3(signature = (text, entries, skip_existing = false))]
pub fn config_insert_text(
    py: Python<'_>,
    text: String,
    entries: Vec<(String, String)>,
    skip_existing: bool,
) -> PyResult<String> {
    let lines: Vec<String> = if text.is_empty() {
        Vec::new()
    } else {
        let s = if text.ends_with('\n') {
            &text[..text.len() - 1]
        } else {
            &text[..]
        };
        s.split('\n').map(|l| l.to_string()).collect()
    };
    let result = py.detach(|| perform_config_insert(&lines, &entries, skip_existing));
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_empty_inputs() {
        let lines: Vec<String> = vec![];
        let entries: Vec<(String, String)> = vec![];
        let res = perform_config_insert(&lines, &entries, false);
        assert_eq!(res, "\n");
    }

    #[test]
    fn test_single_line_empty() {
        let lines = vec!["".to_string()];
        let entries = vec![("a".to_string(), "rest".to_string())];
        let res = perform_config_insert(&lines, &entries, false);
        assert_eq!(res, "\na rest\n");
    }

    #[test]
    fn test_basic_insertion_sorted() {
        let lines = vec!["b 1".to_string(), "d 2".to_string()];
        let entries = vec![
            ("a".to_string(), "new1".to_string()),
            ("c".to_string(), "new2".to_string()),
            ("e".to_string(), "new3".to_string()),
        ];
        let res = perform_config_insert(&lines, &entries, false);
        assert_eq!(res, "a new1\nb 1\nc new2\nd 2\ne new3\n");
    }

    #[test]
    fn test_unsorted_incoming_preserved() {
        let lines = vec!["z 1".to_string()];
        let entries = vec![
            ("b".to_string(), "first".to_string()),
            ("a".to_string(), "second".to_string()),
        ];
        let res = perform_config_insert(&lines, &entries, false);
        assert_eq!(res, "b first\na second\nz 1\n");
    }

    #[test]
    fn test_duplicates_in_entries() {
        let lines = vec!["m 1".to_string()];
        let entries = vec![
            ("a".to_string(), "early1".to_string()),
            ("a".to_string(), "early2".to_string()),
            ("z".to_string(), "tail1".to_string()),
            ("z".to_string(), "tail2".to_string()),
        ];
        let res = perform_config_insert(&lines, &entries, false);
        assert_eq!(res, "a early1\nm 1\nz tail1\nz tail2\n");
    }

    #[test]
    fn test_skip_existing_true_and_false() {
        let lines = vec!["# comment".to_string(), "b 1".to_string()];
        let entries = vec![
            ("a".to_string(), "new".to_string()),
            ("b".to_string(), "collision".to_string()),
        ];
        let res_skip = perform_config_insert(&lines, &entries, true);
        assert_eq!(res_skip, "# comment\na new\nb 1\n");

        let res_no_skip = perform_config_insert(&lines, &entries, false);
        assert_eq!(res_no_skip, "# comment\na new\nb 1\nb collision\n");
    }

    #[test]
    fn test_comments_and_blanks() {
        let lines = vec![
            "# header".to_string(),
            "".to_string(),
            "  # indented".to_string(),
            "b 1".to_string(),
        ];
        let entries = vec![("a".to_string(), "new".to_string())];
        let res = perform_config_insert(&lines, &entries, false);
        assert_eq!(res, "# header\n\n  # indented\na new\nb 1\n");
    }

    #[test]
    fn test_raw_regex_and_unicode() {
        let lines = vec![
            "/system/bin/bb 0".to_string(),
            "/system/日本語 0".to_string(),
        ];
        let entries = vec![
            (
                r"/system/bin/a\-tool(\..*)?".to_string(),
                "exec".to_string(),
            ),
            ("/system/bin/ba".to_string(), "1".to_string()),
        ];
        let res = perform_config_insert(&lines, &entries, false);
        assert_eq!(
            res,
            "/system/bin/a\\-tool(\\..*)? exec\n/system/bin/ba 1\n/system/bin/bb 0\n/system/日本語 0\n"
        );
    }
}
