// SPDX-License-Identifier: MIT

//! Virtual directory child index and Android cross-partition symlink resolver.

use crate::reader::FileNode;
use serde::Serialize;
use std::collections::HashMap;
use std::path::Path;

#[derive(Debug, Clone, Serialize)]
pub struct IndexChild {
    pub name: String,
    pub path: String,
    pub is_dir: bool,
    pub is_link: bool,
    pub size: u64,
    pub nid: u64,
    pub perm: String,
    pub selinux: String,
    pub target: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct SymlinkResolution {
    pub target_image: Option<String>,
    pub target_internal_path: Option<String>,
    pub target_disk_path: Option<String>,
    pub is_dir: bool,
    pub exists: bool,
}

const KNOWN_PARTITIONS: &[&str] = &[
    "system_ext",
    "system_dlkm",
    "vendor_dlkm",
    "odm_dlkm",
    "my_product",
    "my_stock",
    "my_heytap",
    "my_carrier",
    "my_region",
    "my_engineering",
    "my_preload",
    "mi_ext",
    "system",
    "product",
    "vendor",
    "odm",
    "oem",
    "cust",
    "preload",
    "apex",
    "data",
];

/// Build a `parent_path -> sorted children` index from a flat list of `FileNode`s.
pub fn build_child_index(nodes: &[FileNode]) -> HashMap<String, Vec<IndexChild>> {
    let mut index: HashMap<String, Vec<IndexChild>> = HashMap::new();
    for n in nodes {
        let p = n.path.trim_matches('/');
        if p.is_empty() {
            continue;
        }
        let (parent, name) = match p.rsplit_once('/') {
            Some((par, nm)) => (par.to_string(), nm.to_string()),
            None => (String::new(), p.to_string()),
        };
        index.entry(parent).or_default().push(IndexChild {
            name,
            path: p.to_string(),
            is_dir: n.is_dir,
            is_link: n.is_link,
            size: n.size,
            nid: n.nid,
            perm: n.perm.clone(),
            selinux: n.selinux.clone().unwrap_or_default(),
            target: n.target.clone().unwrap_or_default(),
        });
    }
    for children in index.values_mut() {
        children.sort_by(|a, b| {
            (!a.is_dir)
                .cmp(&(!b.is_dir))
                .then_with(|| a.name.to_ascii_lowercase().cmp(&b.name.to_ascii_lowercase()))
        });
    }
    index
}

/// Multi-image virtual mount registry capable of resolving cross-partition Android symlinks.
#[derive(Default)]
pub struct MountRegistry {
    pub indexes: HashMap<String, HashMap<String, Vec<IndexChild>>>,
}

impl MountRegistry {
    pub fn insert(&mut self, image_key: String, nodes: &[FileNode]) {
        self.indexes.insert(image_key, build_child_index(nodes));
    }

    fn lookup_in_index(
        index: &HashMap<String, Vec<IndexChild>>,
        subpath: &str,
    ) -> Option<(bool, String)> {
        let sub = subpath.replace('\\', "/");
        let sub = sub.trim_matches('/');
        if sub.is_empty() {
            return Some((true, String::new()));
        }
        if index.contains_key(sub) {
            return Some((true, sub.to_string()));
        }
        let (parent, name) = match sub.rsplit_once('/') {
            Some((p, n)) => (p, n),
            None => ("", sub),
        };
        if let Some(list) = index.get(parent) {
            for c in list {
                if c.name.eq_ignore_ascii_case(name) {
                    let actual = if parent.is_empty() {
                        c.name.clone()
                    } else {
                        format!("{parent}/{}", c.name)
                    };
                    return Some((c.is_dir, actual));
                }
            }
        }
        None
    }

    fn stem_clean(img_key: &str) -> (String, String) {
        let base = Path::new(img_key)
            .file_name()
            .and_then(|s| s.to_str())
            .unwrap_or(img_key)
            .to_ascii_lowercase();
        let mut stem = base.split('.').next().unwrap_or(&base).to_string();
        for suffix in ["_slot_a", "_slot_b", "_a", "_b", "-a", "-b"] {
            if let Some(stripped) = stem.strip_suffix(suffix) {
                stem = stripped.to_string();
                break;
            }
        }
        (base, stem)
    }

    fn match_score(img_key: &str, partition: &str) -> i32 {
        let (base, stem) = Self::stem_clean(img_key);
        let p = partition.to_ascii_lowercase();
        if stem == p {
            return 2;
        }
        let norm_p = p.replace(['_', '-'], "");
        let norm_s = stem.replace(['_', '-'], "");
        if norm_p == norm_s {
            return 1;
        }
        if base.starts_with(&format!("{p}.")) || base.starts_with(&format!("{p}-")) {
            return 0;
        }
        if p == "product" && matches!(stem.as_str(), "my_product" | "mi_ext") {
            return 0;
        }
        if p == "system_ext" && stem == "mi_ext" {
            return 0;
        }
        -1
    }

    /// Resolve a symlink `target` located at `(img_key, internal_path)` across all registered images.
    pub fn resolve_symlink(
        &self,
        img_key: &str,
        internal: &str,
        target: &str,
    ) -> SymlinkResolution {
        let norm_target = target.replace('\\', "/");
        let norm_target = norm_target.trim();
        let clean = if let Some(stripped) = norm_target.strip_prefix('/') {
            stripped.to_string()
        } else {
            let parent = internal.rsplit_once('/').map(|(p, _)| p).unwrap_or("");
            let mut parts: Vec<&str> = if parent.is_empty() {
                Vec::new()
            } else {
                parent.split('/').collect()
            };
            for seg in norm_target.split('/') {
                match seg {
                    "" | "." => {}
                    ".." => {
                        parts.pop();
                    }
                    other => parts.push(other),
                }
            }
            parts.join("/")
        };
        let clean_norm = clean.trim_matches('/');

        // 1. Check same image first
        let mut curr_cands = vec![clean_norm.to_string()];
        if let Some(rest) = clean_norm.strip_prefix("system/") {
            curr_cands.push(rest.to_string());
        } else if !clean_norm.is_empty() {
            curr_cands.push(format!("system/{clean_norm}"));
        }

        let mut stub_hit: Option<(String, bool)> = None;
        if let Some(curr_idx) = self.indexes.get(img_key) {
            for cand in &curr_cands {
                if cand == internal.trim_matches('/') {
                    continue;
                }
                if let Some((is_dir, actual)) = Self::lookup_in_index(curr_idx, cand) {
                    if is_dir && curr_idx.get(actual.trim_matches('/')).map_or(true, |v| v.is_empty())
                    {
                        if stub_hit.is_none() {
                            stub_hit = Some((actual, is_dir));
                        }
                        continue;
                    }
                    return SymlinkResolution {
                        target_image: Some(img_key.to_string()),
                        target_internal_path: Some(actual),
                        target_disk_path: None,
                        is_dir,
                        exists: true,
                    };
                }
            }
        }

        // 2. Check cross-partition mounts
        let mut split_cands: Vec<(&str, Vec<String>)> = Vec::new();
        for prefix in ["system/", "vendor/"] {
            if let Some(after) = clean_norm.strip_prefix(prefix) {
                let after = after.trim_matches('/');
                for &part in KNOWN_PARTITIONS {
                    if format!("{part}/") == prefix {
                        continue;
                    }
                    if after == part {
                        split_cands.push((part, vec![String::new(), part.to_string()]));
                    } else if let Some(sub) = after.strip_prefix(&format!("{part}/")) {
                        split_cands.push((part, vec![sub.to_string(), format!("{part}/{sub}")]));
                    }
                }
            }
        }
        for &part in KNOWN_PARTITIONS {
            if clean_norm == part {
                split_cands.push((part, vec![String::new(), part.to_string()]));
            } else if let Some(sub) = clean_norm.strip_prefix(&format!("{part}/")) {
                split_cands.push((part, vec![sub.to_string(), format!("{part}/{sub}")]));
            }
        }
        for sys_dir in [
            "bin/", "etc/", "framework/", "lib/", "lib64/", "xbin/", "priv-app/", "app/",
            "media/", "usr/", "fonts/",
        ] {
            if clean_norm.starts_with(sys_dir) {
                split_cands.push((
                    "system",
                    vec![clean_norm.to_string(), format!("system/{clean_norm}")],
                ));
                break;
            }
        }

        for (part, sub_cands) in split_cands {
            let mut ranked: Vec<(&String, &HashMap<String, Vec<IndexChild>>, i32)> = self
                .indexes
                .iter()
                .map(|(k, v)| (k, v, Self::match_score(k, part)))
                .filter(|(_, _, sc)| *sc >= 0)
                .collect();
            ranked.sort_by(|a, b| b.2.cmp(&a.2));

            for (m_key, m_idx, _) in ranked {
                if m_key == img_key && part != "system" {
                    continue;
                }
                for sub in &sub_cands {
                    if sub.is_empty() {
                        return SymlinkResolution {
                            target_image: Some(m_key.clone()),
                            target_internal_path: Some(String::new()),
                            target_disk_path: None,
                            is_dir: true,
                            exists: true,
                        };
                    }
                    if let Some((is_dir, actual)) = Self::lookup_in_index(m_idx, sub) {
                        return SymlinkResolution {
                            target_image: Some(m_key.clone()),
                            target_internal_path: Some(actual),
                            target_disk_path: None,
                            is_dir,
                            exists: true,
                        };
                    }
                }
            }
        }

        if let Some((actual, is_dir)) = stub_hit {
            return SymlinkResolution {
                target_image: Some(img_key.to_string()),
                target_internal_path: Some(actual),
                target_disk_path: None,
                is_dir,
                exists: true,
            };
        }

        let is_dir_guess = norm_target.ends_with('/')
            || (!clean_norm.contains('.') && !norm_target.contains("/bin/"));
        SymlinkResolution {
            target_image: None,
            target_internal_path: Some(clean_norm.to_string()),
            target_disk_path: None,
            is_dir: is_dir_guess,
            exists: false,
        }
    }
}
