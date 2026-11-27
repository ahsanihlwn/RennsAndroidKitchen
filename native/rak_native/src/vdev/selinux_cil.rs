// SPDX-License-Identifier: MPL-2.0
//! Ultra-fast u32-interned Rust SELinux CIL policy parser & evaluator.

use pyo3::prelude::*;
use std::collections::{HashMap, HashSet};

#[derive(Clone, Debug)]
enum SExpr {
    Atom(u32),
    List(Vec<SExpr>),
}

#[pyclass]
pub struct NativeSelinuxPolicy {
    intern: HashMap<String, u32>,
    names: Vec<String>,
    all_id: u32,
    self_id: u32,
    process_id: u32,
    file_id: u32,
    execute_id: u32,
    execute_no_trans_id: u32,

    types: HashSet<u32>,
    attributes: HashSet<u32>,
    attr_exprs: HashMap<u32, Vec<SExpr>>,
    // (src_id, cls_id, perm_id) -> set of tgt_ids
    allow_idx: HashMap<(u32, u32, u32), HashSet<u32>>,
    dontaudit_idx: HashMap<(u32, u32, u32), HashSet<u32>>,
    // (src_id, tgt_id) -> result_type_id for process transitions
    typetrans_proc: HashMap<(u32, u32), u32>,
    sid_context: HashMap<String, String>,
    class_perms: HashMap<u32, HashSet<u32>>,
    common_perms: HashMap<u32, HashSet<u32>>,
    class_common: HashMap<u32, u32>,

    members_cache: HashMap<u32, HashSet<u32>>,
    covers_map: Option<HashMap<u32, Vec<u32>>>,
    allow_rules_count: usize,
}

impl NativeSelinuxPolicy {
    fn intern_str(&mut self, s: &str) -> u32 {
        if let Some(&id) = self.intern.get(s) {
            return id;
        }
        let id = self.names.len() as u32;
        self.names.push(s.to_string());
        self.intern.insert(s.to_string(), id);
        id
    }

    fn lookup_id(&self, s: &str) -> Option<u32> {
        self.intern.get(s).copied()
    }

    fn parse_sexpr_line(&mut self, line: &str) -> Option<SExpr> {
        if !line.starts_with('(') {
            return None;
        }
        let mut stack: Vec<Vec<SExpr>> = vec![Vec::new()];
        let bytes = line.as_bytes();
        let mut i = 0usize;
        while i < bytes.len() {
            let b = bytes[i];
            if b.is_ascii_whitespace() {
                i += 1;
                continue;
            }
            if b == b'(' {
                stack.push(Vec::new());
                i += 1;
            } else if b == b')' {
                if stack.len() > 1 {
                    let done = stack.pop().unwrap();
                    stack.last_mut().unwrap().push(SExpr::List(done));
                }
                i += 1;
            } else if b == b'"' {
                let start = i + 1;
                i += 1;
                while i < bytes.len() && bytes[i] != b'"' {
                    i += 1;
                }
                let s = std::str::from_utf8(&bytes[start..i]).unwrap_or("");
                let id = self.intern_str(s);
                stack.last_mut().unwrap().push(SExpr::Atom(id));
                if i < bytes.len() {
                    i += 1;
                }
            } else {
                let start = i;
                while i < bytes.len()
                    && !bytes[i].is_ascii_whitespace()
                    && bytes[i] != b'('
                    && bytes[i] != b')'
                {
                    i += 1;
                }
                let s = std::str::from_utf8(&bytes[start..i]).unwrap_or("");
                let id = self.intern_str(s);
                stack.last_mut().unwrap().push(SExpr::Atom(id));
            }
        }
        stack.into_iter().next()?.into_iter().next()
    }

    fn flatten_atoms(expr: &SExpr, out: &mut Vec<u32>) {
        match expr {
            SExpr::Atom(id) => out.push(*id),
            SExpr::List(items) => {
                for item in items {
                    Self::flatten_atoms(item, out);
                }
            }
        }
    }

    fn absorb_text(&mut self, text: &str) {
        let id_type = self.intern_str("type");
        let id_typeattr = self.intern_str("typeattribute");
        let id_typeattrset = self.intern_str("typeattributeset");
        let id_allow = self.intern_str("allow");
        let id_dontaudit = self.intern_str("dontaudit");
        let id_typetrans = self.intern_str("typetransition");
        let id_sidctx = self.intern_str("sidcontext");
        let id_common = self.intern_str("common");
        let id_class = self.intern_str("class");
        let id_classcommon = self.intern_str("classcommon");

        for raw in text.lines() {
            let line = raw.trim();
            if !line.starts_with('(') {
                continue;
            }
            let head = line[1..]
                .split(|c: char| c.is_ascii_whitespace() || c == ')')
                .next()
                .unwrap_or("");
            if !matches!(
                head,
                "allow"
                    | "dontaudit"
                    | "typetransition"
                    | "type"
                    | "typeattribute"
                    | "typeattributeset"
                    | "class"
                    | "common"
                    | "classcommon"
                    | "sidcontext"
            ) {
                continue;
            }
            let Some(SExpr::List(node)) = self.parse_sexpr_line(line) else {
                continue;
            };
            if node.len() < 2 {
                continue;
            }
            let SExpr::Atom(tag) = node[0] else {
                continue;
            };

            if tag == id_type {
                if let SExpr::Atom(name) = node[1] {
                    self.types.insert(name);
                }
            } else if tag == id_typeattr {
                if let SExpr::Atom(name) = node[1] {
                    self.attributes.insert(name);
                }
            } else if tag == id_typeattrset && node.len() >= 3 {
                if let SExpr::Atom(name) = node[1] {
                    self.attributes.insert(name);
                    self.attr_exprs.entry(name).or_default().push(node[2].clone());
                }
            } else if (tag == id_allow || tag == id_dontaudit) && node.len() >= 4 {
                if let (SExpr::Atom(s), SExpr::Atom(t)) = (&node[1], &node[2]) {
                    let (cls, perms) = match &node[3] {
                        SExpr::List(cp) if cp.len() >= 2 => {
                            let SExpr::Atom(c) = cp[0] else {
                                continue;
                            };
                            let mut ps = Vec::new();
                            for p in &cp[1..] {
                                Self::flatten_atoms(p, &mut ps);
                            }
                            (c, ps)
                        }
                        SExpr::Atom(c) => (*c, Vec::new()),
                        _ => continue,
                    };
                    if tag == id_allow {
                        self.allow_rules_count += 1;
                        for p in perms {
                            self.allow_idx.entry((*s, cls, p)).or_default().insert(*t);
                        }
                    } else {
                        for p in perms {
                            self.dontaudit_idx.entry((*s, cls, p)).or_default().insert(*t);
                        }
                    }
                }
            } else if tag == id_typetrans && node.len() >= 5 {
                let tail_idx = if node.len() == 6 { 5 } else { 4 };
                if let (SExpr::Atom(s), SExpr::Atom(t), SExpr::Atom(c), SExpr::Atom(res)) =
                    (&node[1], &node[2], &node[3], &node[tail_idx])
                {
                    if *c == self.process_id {
                        self.typetrans_proc.insert((*s, *t), *res);
                    }
                }
            } else if tag == id_sidctx && node.len() >= 3 {
                if let SExpr::Atom(sid_id) = node[1] {
                    let mut parts = Vec::new();
                    Self::flatten_atoms(&node[2], &mut parts);
                    if parts.len() >= 3 {
                        let sid = self.names[sid_id as usize].clone();
                        let ctx = format!(
                            "{}:{}:{}",
                            self.names[parts[0] as usize],
                            self.names[parts[1] as usize],
                            self.names[parts[2] as usize]
                        );
                        self.sid_context.insert(sid, ctx);
                    }
                }
            } else if tag == id_common && node.len() >= 3 {
                if let SExpr::Atom(cname) = node[1] {
                    let mut ps = Vec::new();
                    Self::flatten_atoms(&node[2], &mut ps);
                    self.common_perms.entry(cname).or_default().extend(ps);
                }
            } else if tag == id_class && node.len() >= 2 {
                if let SExpr::Atom(cname) = node[1] {
                    let mut ps = Vec::new();
                    if node.len() >= 3 {
                        Self::flatten_atoms(&node[2], &mut ps);
                    }
                    self.class_perms.entry(cname).or_default().extend(ps);
                }
            } else if tag == id_classcommon && node.len() >= 3 {
                if let (SExpr::Atom(c), SExpr::Atom(cm)) = (&node[1], &node[2]) {
                    self.class_common.insert(*c, *cm);
                }
            }
        }
    }

    fn finish(&mut self) {
        for (&cls, &common) in &self.class_common {
            if let Some(base) = self.common_perms.get(&common) {
                self.class_perms.entry(cls).or_default().extend(base.iter().copied());
            }
        }
        self.members_cache.clear();
        self.covers_map = None;
    }

    fn resolve_attr_inner(&mut self, id: u32, visiting: &mut HashSet<u32>) -> HashSet<u32> {
        if let Some(cached) = self.members_cache.get(&id) {
            return cached.clone();
        }
        if !visiting.insert(id) {
            return HashSet::new();
        }
        let exprs = self.attr_exprs.get(&id).cloned().unwrap_or_default();
        let mut out = HashSet::new();
        for e in &exprs {
            let sub = self.eval_expr(e, visiting);
            out.extend(sub);
        }
        visiting.remove(&id);
        self.members_cache.insert(id, out.clone());
        out
    }

    fn eval_expr(&mut self, expr: &SExpr, visiting: &mut HashSet<u32>) -> HashSet<u32> {
        match expr {
            SExpr::Atom(tok) => {
                if *tok == self.all_id {
                    return self.types.clone();
                }
                if self.types.contains(tok) {
                    let mut s = HashSet::with_capacity(1);
                    s.insert(*tok);
                    return s;
                }
                if self.attributes.contains(tok) || self.attr_exprs.contains_key(tok) {
                    return self.resolve_attr_inner(*tok, visiting);
                }
                HashSet::new()
            }
            SExpr::List(items) => {
                if items.is_empty() {
                    return HashSet::new();
                }
                if let SExpr::Atom(op_id) = items[0] {
                    let op = self.names[op_id as usize].as_str();
                    match op {
                        "or" => {
                            let mut out = HashSet::new();
                            for sub in &items[1..] {
                                out.extend(self.eval_expr(sub, visiting));
                            }
                            return out;
                        }
                        "and" => {
                            let mut iter = items[1..].iter();
                            let Some(first) = iter.next() else {
                                return HashSet::new();
                            };
                            let mut out = self.eval_expr(first, visiting);
                            for sub in iter {
                                let rhs = self.eval_expr(sub, visiting);
                                out.retain(|k| rhs.contains(k));
                            }
                            return out;
                        }
                        "not" => {
                            let neg = if items.len() >= 2 {
                                self.eval_expr(&items[1], visiting)
                            } else {
                                HashSet::new()
                            };
                            return self.types.difference(&neg).copied().collect();
                        }
                        "all" => return self.types.clone(),
                        "xor" => {
                            let mut out = HashSet::new();
                            for sub in &items[1..] {
                                let rhs = self.eval_expr(sub, visiting);
                                for k in rhs {
                                    if !out.remove(&k) {
                                        out.insert(k);
                                    }
                                }
                            }
                            return out;
                        }
                        _ => {}
                    }
                }
                let mut out = HashSet::new();
                for sub in items {
                    out.extend(self.eval_expr(sub, visiting));
                }
                out
            }
        }
    }

    fn ensure_covers(&mut self) {
        if self.covers_map.is_some() {
            return;
        }
        let all_attrs: Vec<u32> = self
            .attributes
            .iter()
            .chain(self.attr_exprs.keys())
            .copied()
            .collect();
        let mut ta: HashMap<u32, Vec<u32>> = HashMap::with_capacity(self.types.len());
        let mut visiting = HashSet::new();
        for attr in all_attrs {
            visiting.clear();
            let members = self.resolve_attr_inner(attr, &mut visiting);
            for m in members {
                ta.entry(m).or_default().push(attr);
            }
        }
        for (&t, vec) in ta.iter_mut() {
            vec.push(t);
            vec.sort_unstable();
            vec.dedup();
        }
        self.covers_map = Some(ta);
    }

    fn covers_slice(&self, id: u32) -> &[u32] {
        self.covers_map
            .as_ref()
            .and_then(|m| m.get(&id))
            .map(|v| v.as_slice())
            .unwrap_or(&[])
    }

    fn check_table(
        &self,
        table: &HashMap<(u32, u32, u32), HashSet<u32>>,
        src_id: u32,
        tgt_id: u32,
        cls_id: u32,
        perm_id: u32,
    ) -> bool {
        let sc = self.covers_slice(src_id);
        let tc = self.covers_slice(tgt_id);
        let src_iter = if sc.is_empty() { std::slice::from_ref(&src_id) } else { sc };
        let tgt_iter = if tc.is_empty() { std::slice::from_ref(&tgt_id) } else { tc };
        let same_domain = src_id == tgt_id || src_iter.binary_search(&tgt_id).is_ok();

        for &s in src_iter {
            for &p in &[perm_id, self.all_id] {
                if let Some(targets) = table.get(&(s, cls_id, p)) {
                    if same_domain && targets.contains(&self.self_id) {
                        return true;
                    }
                    for &t in tgt_iter {
                        if targets.contains(&t) {
                            return true;
                        }
                    }
                }
            }
        }
        false
    }
}

#[pymethods]
impl NativeSelinuxPolicy {
    #[new]
    fn new() -> Self {
        let mut pol = Self {
            intern: HashMap::with_capacity(4096),
            names: Vec::with_capacity(4096),
            all_id: 0,
            self_id: 0,
            process_id: 0,
            file_id: 0,
            execute_id: 0,
            execute_no_trans_id: 0,
            types: HashSet::new(),
            attributes: HashSet::new(),
            attr_exprs: HashMap::new(),
            allow_idx: HashMap::new(),
            dontaudit_idx: HashMap::new(),
            typetrans_proc: HashMap::new(),
            sid_context: HashMap::new(),
            class_perms: HashMap::new(),
            common_perms: HashMap::new(),
            class_common: HashMap::new(),
            members_cache: HashMap::new(),
            covers_map: None,
            allow_rules_count: 0,
        };
        pol.all_id = pol.intern_str("all");
        pol.self_id = pol.intern_str("self");
        pol.process_id = pol.intern_str("process");
        pol.file_id = pol.intern_str("file");
        pol.execute_id = pol.intern_str("execute");
        pol.execute_no_trans_id = pol.intern_str("execute_no_trans");
        pol
    }

    fn load_texts(&mut self, py: Python<'_>, texts: Vec<String>) {
        py.detach(|| {
            for t in &texts {
                self.absorb_text(t);
            }
            self.finish();
            self.ensure_covers();
        });
    }

    fn stats(&self) -> (usize, usize, usize, usize) {
        (
            self.types.len(),
            self.attributes.len(),
            self.allow_rules_count,
            self.typetrans_proc.len(),
        )
    }

    fn is_known(&self, name: &str) -> bool {
        let Some(id) = self.lookup_id(name) else {
            return false;
        };
        self.types.contains(&id) || self.attributes.contains(&id)
    }

    fn sid_domain(&self, sid: &str) -> String {
        let Some(ctx) = self.sid_context.get(sid) else {
            return String::new();
        };
        let parts: Vec<&str> = ctx.split(':').collect();
        if parts.len() >= 3 {
            parts[2].to_string()
        } else {
            String::new()
        }
    }

    fn allows(&mut self, src: &str, tgt: &str, cls: &str, perm: &str) -> bool {
        self.ensure_covers();
        let (Some(sid), Some(tid), Some(cid), Some(pid)) = (
            self.lookup_id(src),
            self.lookup_id(tgt),
            self.lookup_id(cls),
            self.lookup_id(perm),
        ) else {
            return false;
        };
        self.check_table(&self.allow_idx, sid, tid, cid, pid)
    }

    fn silenced(&mut self, src: &str, tgt: &str, cls: &str, perm: &str) -> bool {
        self.ensure_covers();
        let (Some(sid), Some(tid), Some(cid), Some(pid)) = (
            self.lookup_id(src),
            self.lookup_id(tgt),
            self.lookup_id(cls),
            self.lookup_id(perm),
        ) else {
            return false;
        };
        self.check_table(&self.dontaudit_idx, sid, tid, cid, pid)
    }

    fn domain_for_exec(&mut self, src_domain: &str, exec_type: &str) -> String {
        self.ensure_covers();
        let (Some(sid), Some(tid)) = (self.lookup_id(src_domain), self.lookup_id(exec_type)) else {
            return String::new();
        };
        let sc = self.covers_slice(sid);
        let tc = self.covers_slice(tid);
        let src_iter = if sc.is_empty() { std::slice::from_ref(&sid) } else { sc };
        let tgt_iter = if tc.is_empty() { std::slice::from_ref(&tid) } else { tc };
        for &s in src_iter {
            for &t in tgt_iter {
                if let Some(&res_id) = self.typetrans_proc.get(&(s, t)) {
                    if self.check_table(&self.allow_idx, sid, tid, self.file_id, self.execute_id)
                        || self.check_table(
                            &self.allow_idx,
                            sid,
                            tid,
                            self.file_id,
                            self.execute_no_trans_id,
                        )
                    {
                        return self.names[res_id as usize].clone();
                    }
                }
            }
        }
        String::new()
    }

    fn resolve_attr(&mut self, name: &str) -> Vec<String> {
        let leaf = name.rsplit('.').next().unwrap_or(name);
        let Some(id) = self.lookup_id(leaf) else {
            return Vec::new();
        };
        let mut visiting = HashSet::new();
        self.resolve_attr_inner(id, &mut visiting)
            .into_iter()
            .map(|m| self.names[m as usize].clone())
            .collect()
    }
}
