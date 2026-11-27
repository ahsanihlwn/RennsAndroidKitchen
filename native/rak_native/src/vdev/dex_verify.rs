// SPDX-License-Identifier: MPL-2.0
//! Pure-Rust Android DEX/VDEX bytecode verifier, cross-classpath linkage checker,
//! intra-block null-dereference detector, and resources.arsc / RRO overlay auditor.
//! Zero JDK dependency; parallelized via rayon with zero-allocation member lookups.

use crate::apk::{parse_axml_manifest, parse_string_pool};
use crate::image_tools::{NativeErofsReader, NativeExtReader};
use pyo3::exceptions::{PyOSError, PyValueError};
use pyo3::prelude::*;
use rayon::prelude::*;
use serde::Serialize;
use std::collections::{BTreeMap, HashMap, HashSet};
use std::fs::{self, File};
use std::io::{Cursor, Read, Seek};
use std::path::PathBuf;
use std::time::Instant;
use zip::ZipArchive;

// ── DEX access flags ──
const ACC_STATIC: u32 = 0x0008;
const ACC_NATIVE: u32 = 0x0100;
const ACC_INTERFACE: u32 = 0x0200;
const ACC_ABSTRACT: u32 = 0x0400;

// ── ARSC chunk constants ──
const RES_TABLE_TYPE: u16 = 0x0002;
const RES_TABLE_PACKAGE_TYPE: u16 = 0x0200;
const RES_TABLE_TYPE_TYPE: u16 = 0x0201;

// ── Fast FNV-1a 64-bit hash for zero-alloc (name, sig) member keys ──
#[inline]
fn member_hash(name: &str, sig: &str) -> u64 {
    let mut h: u64 = 0xcbf2_9ce4_8422_2325;
    for &b in name.as_bytes() {
        h ^= b as u64;
        h = h.wrapping_mul(0x0000_0100_0000_01b3);
    }
    h ^= 0xff;
    h = h.wrapping_mul(0x0000_0100_0000_01b3);
    for &b in sig.as_bytes() {
        h ^= b as u64;
        h = h.wrapping_mul(0x0000_0100_0000_01b3);
    }
    h
}

// ── Basic little-endian readers ──
#[inline]
fn r_u16(b: &[u8], o: usize) -> Option<u16> {
    let s: [u8; 2] = b.get(o..o + 2)?.try_into().ok()?;
    Some(u16::from_le_bytes(s))
}

#[inline]
fn r_u32(b: &[u8], o: usize) -> Option<u32> {
    let s: [u8; 4] = b.get(o..o + 4)?.try_into().ok()?;
    Some(u32::from_le_bytes(s))
}

#[inline]
fn read_uleb128(b: &[u8], pos: &mut usize) -> Option<u32> {
    let mut result: u32 = 0;
    let mut shift: u32 = 0;
    for _ in 0..5 {
        let byte = *b.get(*pos)?;
        *pos += 1;
        result |= ((byte & 0x7f) as u32) << shift;
        if (byte & 0x80) == 0 {
            return Some(result);
        }
        shift += 7;
    }
    None
}

// ── VDEX / ZIP / raw DEX extraction ──

fn is_valid_dex_at(data: &[u8], off: usize) -> Option<usize> {
    if off + 0x70 > data.len() {
        return None;
    }
    if &data[off..off + 4] != b"dex\n" || data[off + 7] != 0 {
        return None;
    }
    let header_size = r_u32(data, off + 36)?;
    let endian_tag = r_u32(data, off + 40)?;
    if header_size != 0x70 || endian_tag != 0x1234_5678 {
        return None;
    }
    let file_size = r_u32(data, off + 32)? as usize;
    if file_size < 0x70 || off.checked_add(file_size)? > data.len() {
        return None;
    }
    Some(file_size)
}

fn extract_dex_slices_from_vdex(data: &[u8]) -> Vec<Vec<u8>> {
    let mut out = Vec::new();
    if data.len() < 16 || &data[0..4] != b"vdex" {
        return out;
    }
    let mut pos = 4usize;
    while pos + 0x70 <= data.len() {
        if let Some(rel) = data[pos..].windows(4).position(|w| w == b"dex\n") {
            let off = pos + rel;
            if let Some(sz) = is_valid_dex_at(data, off) {
                out.push(data[off..off + sz].to_vec());
                pos = off + sz;
                continue;
            }
            pos = off + 4;
        } else {
            break;
        }
    }
    out
}

fn extract_dex_buffers(data: &[u8]) -> (Vec<Vec<u8>>, bool) {
    if data.len() >= 0x70 && is_valid_dex_at(data, 0).is_some() {
        return (vec![data.to_vec()], false);
    }
    if data.len() >= 16 && &data[0..4] == b"vdex" {
        let dexes = extract_dex_slices_from_vdex(data);
        let used_vdex = !dexes.is_empty();
        return (dexes, used_vdex);
    }
    if data.len() >= 4 && &data[0..2] == b"PK" {
        if let Ok(mut arc) = ZipArchive::new(Cursor::new(data)) {
            let mut dexes = Vec::new();
            for i in 0..arc.len() {
                if let Ok(mut ent) = arc.by_index(i) {
                    let name = ent.name().to_string();
                    if name.ends_with(".dex") && !name.contains('/') {
                        let mut buf = Vec::with_capacity(ent.size() as usize);
                        if ent.read_to_end(&mut buf).is_ok()
                            && is_valid_dex_at(&buf, 0).is_some()
                        {
                            dexes.push(buf);
                        }
                    }
                }
            }
            return (dexes, false);
        }
    }
    (Vec::new(), false)
}

// ── DEX parsed tables (lazy UTF-8 slicing without allocating unused string literals) ──

struct DexTables<'a> {
    data: &'a [u8],
    string_ids_size: usize,
    string_ids_off: usize,
    field_ids_size: usize,
    field_ids_off: usize,
    method_ids_size: usize,
    method_ids_off: usize,
    class_defs_size: usize,
    class_defs_off: usize,
    types_cache: Vec<&'a str>,
    protos_cache: Vec<String>,
}

impl<'a> DexTables<'a> {
    #[inline]
    fn raw_str(data: &'a [u8], string_ids_off: usize, string_ids_size: usize, idx: usize) -> &'a str {
        if idx >= string_ids_size {
            return "";
        }
        let Some(s_off) = r_u32(data, string_ids_off + idx * 4).map(|v| v as usize) else {
            return "";
        };
        let mut p = s_off;
        let _utf16_len = read_uleb128(data, &mut p).unwrap_or(0);
        let start = p;
        let mut end = start;
        while end < data.len() && data[end] != 0 {
            end += 1;
        }
        std::str::from_utf8(&data[start..end]).unwrap_or("")
    }

    fn parse(data: &'a [u8]) -> Option<Self> {
        let _sz = is_valid_dex_at(data, 0)?;
        let string_ids_size = r_u32(data, 56)? as usize;
        let string_ids_off = r_u32(data, 60)? as usize;
        let type_ids_size = r_u32(data, 64)? as usize;
        let type_ids_off = r_u32(data, 68)? as usize;
        let proto_ids_size = r_u32(data, 72)? as usize;
        let proto_ids_off = r_u32(data, 76)? as usize;
        let field_ids_size = r_u32(data, 80)? as usize;
        let field_ids_off = r_u32(data, 84)? as usize;
        let method_ids_size = r_u32(data, 88)? as usize;
        let method_ids_off = r_u32(data, 92)? as usize;
        let class_defs_size = r_u32(data, 96)? as usize;
        let class_defs_off = r_u32(data, 100)? as usize;

        if string_ids_size > 2_000_000
            || type_ids_size > 500_000
            || proto_ids_size > 500_000
            || field_ids_size > 2_000_000
            || method_ids_size > 2_000_000
            || class_defs_size > 500_000
        {
            return None;
        }

        let mut types_cache = Vec::with_capacity(type_ids_size);
        for i in 0..type_ids_size {
            let desc_idx = r_u32(data, type_ids_off + i * 4)? as usize;
            types_cache.push(Self::raw_str(data, string_ids_off, string_ids_size, desc_idx));
        }

        let mut protos_cache = Vec::with_capacity(proto_ids_size);
        for i in 0..proto_ids_size {
            let base = proto_ids_off + i * 12;
            let ret_idx = r_u32(data, base + 4)? as usize;
            let params_off = r_u32(data, base + 8)? as usize;
            let mut sig = String::with_capacity(28);
            sig.push('(');
            if params_off != 0 {
                if let Some(cnt) = r_u32(data, params_off) {
                    let cnt = (cnt as usize).min(256);
                    for j in 0..cnt {
                        if let Some(t_idx) = r_u16(data, params_off + 4 + j * 2) {
                            if let Some(&t) = types_cache.get(t_idx as usize) {
                                sig.push_str(t);
                            }
                        }
                    }
                }
            }
            sig.push(')');
            if let Some(&ret) = types_cache.get(ret_idx) {
                sig.push_str(ret);
            }
            protos_cache.push(sig);
        }

        Some(Self {
            data,
            string_ids_size,
            string_ids_off,
            field_ids_size,
            field_ids_off,
            method_ids_size,
            method_ids_off,
            class_defs_size,
            class_defs_off,
            types_cache,
            protos_cache,
        })
    }

    #[inline]
    fn type_name(&self, idx: usize) -> &'a str {
        self.types_cache.get(idx).copied().unwrap_or("")
    }

    #[inline]
    fn string_at(&self, idx: usize) -> &'a str {
        Self::raw_str(self.data, self.string_ids_off, self.string_ids_size, idx)
    }

    #[inline]
    fn field_triple(&self, idx: usize) -> Option<(&'a str, &'a str, &'a str)> {
        if idx >= self.field_ids_size {
            return None;
        }
        let base = self.field_ids_off + idx * 8;
        let cls_idx = r_u16(self.data, base)? as usize;
        let type_idx = r_u16(self.data, base + 2)? as usize;
        let name_idx = r_u32(self.data, base + 4)? as usize;
        Some((
            self.type_name(cls_idx),
            self.string_at(name_idx),
            self.type_name(type_idx),
        ))
    }

    #[inline]
    fn method_triple(&self, idx: usize) -> Option<(&'a str, &'a str, &str)> {
        if idx >= self.method_ids_size {
            return None;
        }
        let base = self.method_ids_off + idx * 8;
        let cls_idx = r_u16(self.data, base)? as usize;
        let proto_idx = r_u16(self.data, base + 2)? as usize;
        let name_idx = r_u32(self.data, base + 4)? as usize;
        Some((
            self.type_name(cls_idx),
            self.string_at(name_idx),
            self.protos_cache.get(proto_idx)?.as_str(),
        ))
    }
}

#[inline]
fn name_hash(name: &str) -> u64 {
    let mut h: u64 = 0xcbf2_9ce4_8422_2325;
    for &b in name.as_bytes() {
        h ^= b as u64;
        h = h.wrapping_mul(0x0100_0000_01b3);
    }
    h
}

#[derive(Clone, Default)]
struct ClassInfo {
    superclass: String,
    interfaces: Vec<String>,
    /// FNV-1a(name, proto) -> access_flags
    methods: HashMap<u64, u32>,
    /// FNV-1a(method_name) for reflection getMethod/getDeclaredMethod checks
    method_names: HashSet<u64>,
    /// Only abstract method signatures kept as strings for AbstractMethodError diagnostics
    abstract_methods: Vec<(String, String, u64)>,
    /// FNV-1a(name, type_desc) -> access_flags
    fields: HashMap<u64, u32>,
    /// FNV-1a(field_name) for reflection getField/getDeclaredField checks
    field_names: HashSet<u64>,
    /// FNV-1a(field_name) -> static final String value (from static_values_off + <clinit>)
    static_strings: HashMap<u64, String>,
    /// ACC_NATIVE method names declared on this class (for JNI symbol binding audit)
    native_methods: Vec<String>,
}

impl ClassInfo {
    fn merge_from(&mut self, other: ClassInfo) {
        let was_empty = self.superclass.is_empty() && self.methods.is_empty();
        if self.superclass.is_empty() && !other.superclass.is_empty() {
            self.superclass = other.superclass;
        }
        if was_empty {
            for inf in other.interfaces {
                if !self.interfaces.contains(&inf) {
                    self.interfaces.push(inf);
                }
            }
            for item in other.abstract_methods {
                if !self.abstract_methods.iter().any(|x| x.2 == item.2) {
                    self.abstract_methods.push(item);
                }
            }
        }
        for (h, fl) in other.methods {
            self.methods
                .entry(h)
                .and_modify(|existing| {
                    // Prefer concrete (non-abstract) definition if shaded copies coexist
                    if (*existing & ACC_ABSTRACT) != 0 && (fl & ACC_ABSTRACT) == 0 {
                        *existing = fl;
                    }
                })
                .or_insert(fl);
        }
        self.method_names.extend(other.method_names);
        for (h, fl) in other.fields {
            self.fields.entry(h).or_insert(fl);
        }
        self.field_names.extend(other.field_names);
        for (h, s) in other.static_strings {
            self.static_strings.entry(h).or_insert(s);
        }
        for nm in other.native_methods {
            if !self.native_methods.contains(&nm) {
                self.native_methods.push(nm);
            }
        }
    }
}

fn read_le_uint(data: &[u8], pos: &mut usize, nbytes: usize) -> Option<u32> {
    if *pos + nbytes > data.len() || nbytes == 0 || nbytes > 4 {
        return None;
    }
    let mut v = 0u32;
    for i in 0..nbytes {
        v |= (data[*pos + i] as u32) << (i * 8);
    }
    *pos += nbytes;
    Some(v)
}

fn skip_encoded_value(data: &[u8], pos: &mut usize, depth: u8) -> Option<Option<u32>> {
    if depth > 6 || *pos >= data.len() {
        return None;
    }
    let hdr = data[*pos];
    *pos += 1;
    let vtype = hdr & 0x1f;
    let varg = ((hdr >> 5) & 0x07) as usize;
    match vtype {
        0x00 => {
            *pos = pos.checked_add(1)?;
            (*pos <= data.len()).then_some(None)
        }
        0x02 | 0x03 | 0x04 | 0x06 | 0x10 | 0x11 | 0x15 | 0x16 | 0x18 | 0x19 | 0x1a | 0x1b => {
            *pos = pos.checked_add(varg + 1)?;
            (*pos <= data.len()).then_some(None)
        }
        0x17 => {
            // VALUE_STRING: unsigned little-endian string_idx
            let sidx = read_le_uint(data, pos, varg + 1)?;
            Some(Some(sidx))
        }
        0x1c => {
            // VALUE_ARRAY
            let sz = read_uleb128(data, pos)? as usize;
            for _ in 0..sz.min(512) {
                skip_encoded_value(data, pos, depth + 1)?;
            }
            Some(None)
        }
        0x1d => {
            // VALUE_ANNOTATION
            let _type_idx = read_uleb128(data, pos)?;
            let sz = read_uleb128(data, pos)? as usize;
            for _ in 0..sz.min(128) {
                let _name_idx = read_uleb128(data, pos)?;
                skip_encoded_value(data, pos, depth + 1)?;
            }
            Some(None)
        }
        0x1e | 0x1f => Some(None),
        _ => None,
    }
}

fn extract_clinit_static_strings(
    t: &DexTables<'_>,
    cls_desc: &str,
    code_off: usize,
    out: &mut HashMap<u64, String>,
) {
    if code_off + 16 > t.data.len() {
        return;
    }
    let insns_size = r_u32(t.data, code_off + 12).unwrap_or(0) as usize;
    if insns_size == 0 || insns_size > 2048 {
        return;
    }
    let start = code_off + 16;
    if start + insns_size * 2 > t.data.len() {
        return;
    }
    let mut insns = Vec::with_capacity(insns_size);
    for i in 0..insns_size {
        insns.push(r_u16(t.data, start + i * 2).unwrap_or(0));
    }
    let mut reg_s: [Option<u32>; 32] = [None; 32];
    let mut pc = 0usize;
    while pc < insns_size {
        let word = insns[pc];
        let Some(len) = dalvik_insn_len(&insns, pc) else {
            break;
        };
        if pc + len > insns_size {
            break;
        }
        let op = (word & 0xff) as u8;
        let aa = ((word >> 8) & 0xff) as usize;
        match op {
            0x1a if aa < 32 => {
                reg_s[aa] = Some(insns[pc + 1] as u32);
            }
            0x1b if aa < 32 => {
                reg_s[aa] = Some((insns[pc + 1] as u32) | ((insns[pc + 2] as u32) << 16));
            }
            0x69 if aa < 32 => {
                // sput-object vAA, field@BBBB
                if let Some(sidx) = reg_s[aa] {
                    let fidx = insns[pc + 1] as usize;
                    if let Some((owner, fname, ftype)) = t.field_triple(fidx) {
                        if owner == cls_desc && ftype == "Ljava/lang/String;" {
                            let val = t.string_at(sidx as usize);
                            if !val.is_empty() && val.len() <= 256 && out.len() < 128 {
                                out.entry(name_hash(fname)).or_insert_with(|| val.to_string());
                            }
                        }
                    }
                }
            }
            0x0a..=0x19 | 0x1c | 0x22 | 0x60..=0x66 if aa < 32 => {
                reg_s[aa] = None;
            }
            _ => {}
        }
        pc += len;
    }
}

fn index_dex_classes(dex_bytes: &[u8]) -> HashMap<String, ClassInfo> {
    let mut out: HashMap<String, ClassInfo> = HashMap::new();
    let Some(t) = DexTables::parse(dex_bytes) else {
        return out;
    };

    for i in 0..t.class_defs_size {
        let base = t.class_defs_off + i * 32;
        let Some(cls_idx) = r_u32(t.data, base).map(|v| v as usize) else {
            continue;
        };
        let cls_desc = t.type_name(cls_idx);
        if cls_desc.is_empty() {
            continue;
        }
        let super_idx = r_u32(t.data, base + 8).unwrap_or(0xffff_ffff);
        let ifaces_off = r_u32(t.data, base + 12).unwrap_or(0) as usize;
        let class_data_off = r_u32(t.data, base + 24).unwrap_or(0) as usize;
        let static_values_off = r_u32(t.data, base + 28).unwrap_or(0) as usize;

        let superclass = if super_idx == 0xffff_ffff {
            String::new()
        } else {
            t.type_name(super_idx as usize).to_string()
        };

        let mut interfaces = Vec::new();
        if ifaces_off != 0 {
            if let Some(cnt) = r_u32(t.data, ifaces_off) {
                let cnt = (cnt as usize).min(256);
                for j in 0..cnt {
                    if let Some(ti) = r_u16(t.data, ifaces_off + 4 + j * 2) {
                        let name = t.type_name(ti as usize);
                        if !name.is_empty() {
                            interfaces.push(name.to_string());
                        }
                    }
                }
            }
        }

        let mut methods = HashMap::new();
        let mut method_names = HashSet::new();
        let mut abstract_methods = Vec::new();
        let mut fields = HashMap::new();
        let mut field_names = HashSet::new();
        let mut static_strings = HashMap::new();
        let mut native_methods = Vec::new();
        let mut static_field_order: Vec<(&str, &str)> = Vec::new();
        let mut clinit_code_off: usize = 0;

        if class_data_off != 0 && class_data_off < t.data.len() {
            let mut pos = class_data_off;
            let sf = read_uleb128(t.data, &mut pos).unwrap_or(0) as usize;
            let inf = read_uleb128(t.data, &mut pos).unwrap_or(0) as usize;
            let dm = read_uleb128(t.data, &mut pos).unwrap_or(0) as usize;
            let vm = read_uleb128(t.data, &mut pos).unwrap_or(0) as usize;

            for (fi_kind, count) in [(0usize, sf), (1usize, inf)] {
                let mut fidx: usize = 0;
                for _ in 0..count {
                    let Some(diff) = read_uleb128(t.data, &mut pos) else {
                        break;
                    };
                    let Some(flags) = read_uleb128(t.data, &mut pos) else {
                        break;
                    };
                    fidx = fidx.saturating_add(diff as usize);
                    if let Some((_c, fname, ftype)) = t.field_triple(fidx) {
                        fields.insert(member_hash(fname, ftype), flags);
                        field_names.insert(name_hash(fname));
                        if fi_kind == 0 {
                            static_field_order.push((fname, ftype));
                        }
                    } else if fi_kind == 0 {
                        static_field_order.push(("", ""));
                    }
                }
            }
            for count in [dm, vm] {
                let mut midx: usize = 0;
                for _ in 0..count {
                    let Some(diff) = read_uleb128(t.data, &mut pos) else {
                        break;
                    };
                    let Some(flags) = read_uleb128(t.data, &mut pos) else {
                        break;
                    };
                    let code_off = read_uleb128(t.data, &mut pos).unwrap_or(0) as usize;
                    midx = midx.saturating_add(diff as usize);
                    if let Some((_c, mname, mproto)) = t.method_triple(midx) {
                        let h = member_hash(mname, mproto);
                        methods.insert(h, flags);
                        method_names.insert(name_hash(mname));
                        if (flags & ACC_ABSTRACT) != 0 {
                            abstract_methods.push((mname.to_string(), mproto.to_string(), h));
                        }
                        if (flags & ACC_NATIVE) != 0 && native_methods.len() < 128 {
                            native_methods.push(mname.to_string());
                        }
                        if mname == "<clinit>" && code_off != 0 {
                            clinit_code_off = code_off;
                        }
                    }
                }
            }
        }

        // Parse static_values_off encoded_array for compile-time static final String constants
        if static_values_off != 0 && static_values_off < t.data.len() && !static_field_order.is_empty() {
            let mut spos = static_values_off;
            if let Some(arr_sz) = read_uleb128(t.data, &mut spos) {
                let lim = (arr_sz as usize).min(static_field_order.len()).min(256);
                for k in 0..lim {
                    let Some(maybe_sidx) = skip_encoded_value(t.data, &mut spos, 0) else {
                        break;
                    };
                    if let Some(sidx) = maybe_sidx {
                        let (fname, ftype) = static_field_order[k];
                        if !fname.is_empty() && ftype == "Ljava/lang/String;" {
                            let val = t.string_at(sidx as usize);
                            if !val.is_empty() && val.len() <= 256 {
                                static_strings.insert(name_hash(fname), val.to_string());
                            }
                        }
                    }
                }
            }
        }
        if clinit_code_off != 0 && is_core_android_or_oem_class(cls_desc) {
            extract_clinit_static_strings(&t, cls_desc, clinit_code_off, &mut static_strings);
        }

        let info = ClassInfo {
            superclass,
            interfaces,
            methods,
            method_names,
            abstract_methods,
            fields,
            field_names,
            static_strings,
            native_methods,
        };
        out.entry(cls_desc.to_string())
            .and_modify(|e| e.merge_from(info.clone()))
            .or_insert(info);
    }

    out
}

// ── Dalvik opcode table (in 16-bit code units) ──

#[inline]
fn dalvik_insn_len(insns: &[u16], pc: usize) -> Option<usize> {
    let word = *insns.get(pc)?;
    if word == 0x0100 {
        let size = *insns.get(pc + 1)? as usize;
        return Some(size.checked_mul(2)?.checked_add(4)?);
    }
    if word == 0x0200 {
        let size = *insns.get(pc + 1)? as usize;
        return Some(size.checked_mul(4)?.checked_add(2)?);
    }
    if word == 0x0300 {
        let elem_w = *insns.get(pc + 1)? as usize;
        let lo = *insns.get(pc + 2)? as u32;
        let hi = *insns.get(pc + 3)? as u32;
        let size = (lo | (hi << 16)) as usize;
        let units = (size.checked_mul(elem_w)?.checked_add(1)?) / 2;
        return Some(units.checked_add(4)?);
    }
    let op = (word & 0xff) as u8;
    let len = match op {
        0x00 | 0x01 | 0x04 | 0x07 | 0x0a..=0x12 | 0x1d | 0x1e | 0x21 | 0x27 | 0x28
        | 0x7b..=0x8f | 0xb0..=0xcf => 1,
        0x02 | 0x05 | 0x08 | 0x13 | 0x15 | 0x16 | 0x19 | 0x1a | 0x1c | 0x1f | 0x20 | 0x22
        | 0x23 | 0x29 | 0x2d..=0x3d | 0x44..=0x6d | 0x90..=0xaf | 0xd0..=0xe2 | 0xfe | 0xff => 2,
        0x03 | 0x06 | 0x09 | 0x14 | 0x17 | 0x1b | 0x24..=0x26 | 0x2a..=0x2c | 0x6e..=0x72
        | 0x74..=0x78 | 0xfc | 0xfd => 3,
        0xfa | 0xfb => 4,
        0x18 => 5,
        _ => return None,
    };
    Some(len)
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum InvokeKind {
    Virtual,
    Super,
    Direct,
    Static,
    Interface,
}

#[derive(Clone, Serialize)]
pub struct DexIssue {
    pub kind: String,
    pub source: String,
    pub class_name: String,
    pub method: String,
    pub target: String,
    pub pc: usize,
    pub detail: String,
    pub boot_critical: bool,
}

#[derive(Clone, Serialize, Default)]
pub struct MissingPrefixSummary {
    pub count: usize,
    pub sample_classes: Vec<String>,
    pub sample_callers: Vec<String>,
}

#[derive(Clone, Serialize)]
pub struct OverlayIssue {
    pub kind: String,
    pub apk_path: String,
    pub package: String,
    pub target_package: String,
    pub detail: String,
    pub missing_count: usize,
    pub total_resources: usize,
    pub sample_missing: Vec<String>,
}

#[derive(Serialize)]
pub struct DexVerifyStats {
    pub provider_jars: usize,
    pub provider_classes: usize,
    pub provider_methods: usize,
    pub provider_fields: usize,
    pub target_jars: usize,
    pub target_classes: usize,
    pub target_methods_verified: usize,
    pub insns_verified: usize,
    pub vdex_extracted: usize,
    pub native_methods_checked: usize,
    pub overlays_checked: usize,
    pub elapsed_ms: u64,
}

#[derive(Clone, Serialize)]
pub struct LogicCallSite {
    pub value: String,
    pub secondary: String,
    pub source: String,
    pub caller: String,
    pub resolved: bool,
    pub blocking: bool,
    pub boot_critical: bool,
    pub guarded: bool,
    pub dead_branch: bool,
}

#[derive(Clone, Serialize)]
pub struct JniBinding {
    pub lib: String,
    pub class_name: String,
    pub methods: Vec<String>,
    pub source: String,
    pub guarded: bool,
    pub boot_critical: bool,
}

#[derive(Default, Serialize)]
pub struct DexLogicMap {
    pub system_services: Vec<LogicCallSite>,
    pub jni_libraries: Vec<LogicCallSite>,
    pub jni_bindings: Vec<JniBinding>,
    pub reflection_classes: Vec<LogicCallSite>,
    pub reflection_members: Vec<LogicCallSite>,
    pub binder_services: Vec<LogicCallSite>,
    pub system_properties: Vec<String>,
    pub component_targets: Vec<LogicCallSite>,
    pub dead_branches_pruned: usize,
    pub static_strings_resolved: usize,
}

#[derive(Default)]
struct UnitLogicAcc {
    system_services: Vec<LogicCallSite>,
    jni_libraries: Vec<LogicCallSite>,
    jni_bindings: Vec<JniBinding>,
    reflection_classes: Vec<LogicCallSite>,
    reflection_members: Vec<LogicCallSite>,
    binder_services: Vec<LogicCallSite>,
    system_properties: HashSet<String>,
    component_targets: Vec<LogicCallSite>,
    dead_branches_pruned: usize,
    static_strings_resolved: usize,
}

fn resolve_static_string<'a>(
    own: &'a HashMap<String, ClassInfo>,
    global: &'a HashMap<String, ClassInfo>,
    owner: &str,
    fname: &str,
) -> Option<&'a str> {
    let h = name_hash(fname);
    let mut visited: HashSet<&str> = HashSet::new();
    let mut stack: Vec<&str> = vec![owner];
    while let Some(cur) = stack.pop() {
        if !visited.insert(cur) {
            continue;
        }
        if let Some(info) = own.get(cur) {
            if let Some(s) = info.static_strings.get(&h) {
                return Some(s.as_str());
            }
            if !info.superclass.is_empty() {
                stack.push(info.superclass.as_str());
            }
            for inf in &info.interfaces {
                stack.push(inf.as_str());
            }
        }
        if let Some(info) = global.get(cur) {
            if let Some(s) = info.static_strings.get(&h) {
                return Some(s.as_str());
            }
            if !info.superclass.is_empty() {
                stack.push(info.superclass.as_str());
            }
            for inf in &info.interfaces {
                stack.push(inf.as_str());
            }
        }
    }
    None
}

fn has_member_name(
    own: &HashMap<String, ClassInfo>,
    global: &HashMap<String, ClassInfo>,
    owner: &str,
    mname: &str,
    is_method: bool,
) -> Option<bool> {
    let h = name_hash(mname);
    if lookup_class(own, global, owner).is_none() {
        return None;
    }
    let mut visited: HashSet<&str> = HashSet::new();
    let mut stack: Vec<&str> = vec![owner];
    let mut complete = true;
    while let Some(cur) = stack.pop() {
        if !visited.insert(cur) {
            continue;
        }
        let Some(info) = lookup_class(own, global, cur) else {
            complete = false;
            continue;
        };
        let found = if is_method {
            info.method_names.contains(&h)
        } else {
            info.field_names.contains(&h)
        };
        if found {
            return Some(true);
        }
        if let Some(ginfo) = global.get(cur) {
            let gfound = if is_method {
                ginfo.method_names.contains(&h)
            } else {
                ginfo.field_names.contains(&h)
            };
            if gfound {
                return Some(true);
            }
        }
        if !info.superclass.is_empty() {
            stack.push(info.superclass.as_str());
        }
        for inf in &info.interfaces {
            stack.push(inf.as_str());
        }
    }
    if complete { Some(false) } else { None }
}

fn eval_sysprop_bool(props: &HashMap<String, String>, key: &str, default_val: bool) -> bool {
    match props.get(key).map(|s| s.trim().to_ascii_lowercase()) {
        Some(v) => match v.as_str() {
            "1" | "y" | "yes" | "true" | "on" => true,
            "0" | "n" | "no" | "false" | "off" => false,
            _ => default_val,
        },
        None => default_val,
    }
}

#[derive(Serialize)]
pub struct DexVerifyReport {
    pub stats: DexVerifyStats,
    pub verify_errors: Vec<DexIssue>,
    pub null_deref_risks: Vec<DexIssue>,
    pub missing_superclasses: Vec<DexIssue>,
    pub missing_methods: Vec<DexIssue>,
    pub missing_fields: Vec<DexIssue>,
    pub incompatible_changes: Vec<DexIssue>,
    pub missing_ref_classes_by_prefix: BTreeMap<String, MissingPrefixSummary>,
    pub missing_framework_res_ids: Vec<DexIssue>,
    pub overlay_issues: Vec<OverlayIssue>,
    pub logic_map: DexLogicMap,
}

// ── Classpath hierarchy resolver (superclass chain FIRST, then interfaces, zero heap alloc) ──

#[derive(Clone, Copy, PartialEq, Eq)]
enum ResolveResult {
    Found(u32),
    MissingResolvedChain,
    UnresolvedHierarchy,
}

fn is_polymorphic_owner(cls: &str, method: &str) -> bool {
    if cls == "Ljava/lang/invoke/MethodHandle;" {
        return matches!(method, "invoke" | "invokeExact");
    }
    if cls == "Ljava/lang/invoke/VarHandle;" {
        return true;
    }
    false
}

#[inline]
fn lookup_class<'a>(
    own: &'a HashMap<String, ClassInfo>,
    global: &'a HashMap<String, ClassInfo>,
    cls: &str,
) -> Option<&'a ClassInfo> {
    own.get(cls).or_else(|| global.get(cls))
}

fn resolve_method(
    own: &HashMap<String, ClassInfo>,
    global: &HashMap<String, ClassInfo>,
    owner_raw: &str,
    name: &str,
    proto: &str,
) -> ResolveResult {
    let owner = if owner_raw.starts_with('[') {
        "Ljava/lang/Object;"
    } else {
        owner_raw
    };
    if is_polymorphic_owner(owner, name) {
        return ResolveResult::Found(0);
    }
    if owner_raw.starts_with('[') && name == "clone" && proto == "()Ljava/lang/Object;" {
        return ResolveResult::Found(0);
    }
    if lookup_class(own, global, owner).is_none() {
        return ResolveResult::UnresolvedHierarchy;
    }

    let key = member_hash(name, proto);
    let mut visited: HashSet<&str> = HashSet::new();
    let mut chain_complete = true;
    let mut ifaces_queue: Vec<&str> = Vec::new();
    let mut abstract_hit: Option<u32> = None;

    // 1) Walk superclass chain FIRST (per JVM/ART spec: concrete superclass wins over abstract interface)
    let mut cur_opt = Some(owner);
    while let Some(cur) = cur_opt {
        if !visited.insert(cur) {
            break;
        }
        let Some(info) = lookup_class(own, global, cur) else {
            chain_complete = false;
            break;
        };
        if let Some(&flags) = info.methods.get(&key) {
            if (flags & ACC_ABSTRACT) == 0 {
                return ResolveResult::Found(flags);
            }
            abstract_hit.get_or_insert(flags);
        }
        // Also check merged global definition if own had a partial shaded copy
        if let Some(ginfo) = global.get(cur) {
            if let Some(&flags) = ginfo.methods.get(&key) {
                if (flags & ACC_ABSTRACT) == 0 {
                    return ResolveResult::Found(flags);
                }
                abstract_hit.get_or_insert(flags);
            }
            for inf in &ginfo.interfaces {
                ifaces_queue.push(inf.as_str());
            }
        }
        for inf in &info.interfaces {
            ifaces_queue.push(inf.as_str());
        }
        cur_opt = if info.superclass.is_empty() {
            None
        } else {
            Some(info.superclass.as_str())
        };
    }

    // 2) Implicit java.lang.Object concrete methods (e.g. Comparator.equals, toString, hashCode)
    if let Some(obj) = global.get("Ljava/lang/Object;") {
        if let Some(&flags) = obj.methods.get(&key) {
            if (flags & ACC_ABSTRACT) == 0 {
                return ResolveResult::Found(flags);
            }
        }
    }

    // 3) Walk interfaces transitively (preferring concrete default methods over abstract)
    while let Some(cur) = ifaces_queue.pop() {
        if !visited.insert(cur) {
            continue;
        }
        let Some(info) = lookup_class(own, global, cur) else {
            chain_complete = false;
            continue;
        };
        if let Some(&flags) = info.methods.get(&key) {
            if (flags & ACC_ABSTRACT) == 0 {
                return ResolveResult::Found(flags);
            }
            abstract_hit.get_or_insert(flags);
        }
        if let Some(ginfo) = global.get(cur) {
            if let Some(&flags) = ginfo.methods.get(&key) {
                if (flags & ACC_ABSTRACT) == 0 {
                    return ResolveResult::Found(flags);
                }
                abstract_hit.get_or_insert(flags);
            }
        }
        for inf in &info.interfaces {
            ifaces_queue.push(inf.as_str());
        }
    }

    if let Some(flags) = abstract_hit {
        return ResolveResult::Found(flags);
    }

    if chain_complete {
        ResolveResult::MissingResolvedChain
    } else {
        ResolveResult::UnresolvedHierarchy
    }
}

fn resolve_field(
    own: &HashMap<String, ClassInfo>,
    global: &HashMap<String, ClassInfo>,
    owner: &str,
    name: &str,
    ftype: &str,
) -> ResolveResult {
    if lookup_class(own, global, owner).is_none() {
        return ResolveResult::UnresolvedHierarchy;
    }
    let key = member_hash(name, ftype);
    let mut visited: HashSet<&str> = HashSet::new();
    let mut stack: Vec<&str> = vec![owner];
    let mut chain_complete = true;

    while let Some(cur) = stack.pop() {
        if !visited.insert(cur) {
            continue;
        }
        let Some(info) = lookup_class(own, global, cur) else {
            chain_complete = false;
            continue;
        };
        if let Some(&flags) = info.fields.get(&key) {
            return ResolveResult::Found(flags);
        }
        if let Some(ginfo) = global.get(cur) {
            if let Some(&flags) = ginfo.fields.get(&key) {
                return ResolveResult::Found(flags);
            }
        }
        if !info.superclass.is_empty() {
            stack.push(info.superclass.as_str());
        }
        for iface in &info.interfaces {
            stack.push(iface.as_str());
        }
    }

    if chain_complete {
        ResolveResult::MissingResolvedChain
    } else {
        ResolveResult::UnresolvedHierarchy
    }
}

fn is_boot_critical_class(desc: &str) -> bool {
    desc == "Lcom/android/server/SystemServer;"
        || desc == "Lcom/android/systemui/SystemUIApplication;"
        || desc == "Lcom/android/systemui/SystemUIFactory;"
        || desc == "Lcom/android/systemui/SystemUIService;"
        || desc == "Lcom/android/server/am/ActivityManagerService;"
        || desc == "Lcom/android/server/pm/PackageManagerService;"
        || desc == "Lcom/android/server/wm/WindowManagerService;"
        || desc == "Lcom/android/server/power/PowerManagerService;"
        || desc == "Lcom/android/internal/os/ZygoteInit;"
}

fn is_ignored_desktop_or_test_class(desc: &str) -> bool {
    desc.starts_with("Ljava/lang/instrument/")
        || desc.starts_with("Ljavax/management/")
        || desc.starts_with("Ljavax/script/")
        || desc.starts_with("Lsun/misc/")
        || desc.starts_with("Lorg/junit/")
        || desc.starts_with("Ljunit/")
        || desc.starts_with("Landroidx/test/")
}

fn is_core_android_or_oem_class(desc: &str) -> bool {
    let d = desc.trim_start_matches('[');
    if is_ignored_desktop_or_test_class(d) {
        return false;
    }
    d.starts_with("Landroid/")
        || d.starts_with("Lcom/android/")
        || d.starts_with("Ljava/")
        || d.starts_with("Ljavax/")
        || d.starts_with("Ldalvik/")
        || d.starts_with("Lcom/mediatek/")
        || d.starts_with("Lcom/transsion/")
        || d.starts_with("Lcom/oplus/")
        || d.starts_with("Lcom/coloros/")
        || d.starts_with("Lcom/samsung/")
        || d.starts_with("Lmiui/")
        || d.starts_with("Lcom/xiaomi/")
        || d.starts_with("Lcom/google/android/systemui/")
        || d.starts_with("Lcom/qualcomm/")
}

fn package_prefix3(desc: &str) -> String {
    let trimmed = desc
        .trim_start_matches('[')
        .trim_start_matches('L')
        .trim_end_matches(';');
    let parts: Vec<&str> = trimmed.split('/').collect();
    if parts.len() <= 1 {
        return trimmed.to_string();
    }
    let take = (parts.len() - 1).min(3);
    parts[..take].join(".")
}

// ── Single-method Dalvik bytecode verifier + null-flow + callsite checker ──

struct MethodVerifyCtx<'a> {
    tables: &'a DexTables<'a>,
    own_classes: &'a HashMap<String, ClassInfo>,
    global_classes: &'a HashMap<String, ClassInfo>,
    source: &'a str,
    class_name: &'a str,
    method_sig: &'a str,
    return_type: &'a str,
    boot_critical: bool,
    framework_res_ids: Option<&'a HashSet<u32>>,
    enabled_features: Option<&'a HashSet<String>>,
    rom_props: Option<&'a HashMap<String, String>>,
    verify_errors: &'a mut Vec<DexIssue>,
    null_derefs: &'a mut Vec<DexIssue>,
    missing_methods: &'a mut Vec<DexIssue>,
    missing_fields: &'a mut Vec<DexIssue>,
    incompat: &'a mut Vec<DexIssue>,
    missing_classes: &'a mut HashMap<String, Vec<String>>,
    missing_res: &'a mut Vec<DexIssue>,
    logic: &'a mut UnitLogicAcc,
    insns_counted: &'a mut usize,
}

fn dot_class_to_desc(name: &str) -> String {
    let trimmed = name.trim();
    if trimmed.starts_with('L') && trimmed.ends_with(';') {
        return trimmed.to_string();
    }
    if trimmed.starts_with('[') {
        return trimmed.replace('.', "/");
    }
    format!("L{};", trimmed.replace('.', "/"))
}

fn desc_to_dot_class(desc: &str) -> String {
    desc.trim_start_matches('L')
        .trim_end_matches(';')
        .replace('/', ".")
}

fn verify_code_item(ctx: &mut MethodVerifyCtx<'_>, code_off: usize) {
    let data = ctx.tables.data;
    if code_off + 16 > data.len() {
        ctx.verify_errors.push(DexIssue {
            kind: "VerifyError".into(),
            source: ctx.source.into(),
            class_name: ctx.class_name.into(),
            method: ctx.method_sig.into(),
            target: String::new(),
            pc: 0,
            detail: format!("code_item offset 0x{code_off:x} out of bounds"),
            boot_critical: ctx.boot_critical,
        });
        return;
    }

    let regs_size = r_u16(data, code_off).unwrap_or(0) as usize;
    let ins_size = r_u16(data, code_off + 2).unwrap_or(0) as usize;
    let tries_size = r_u16(data, code_off + 6).unwrap_or(0) as usize;
    let insns_size = r_u32(data, code_off + 12).unwrap_or(0) as usize;

    if ins_size > regs_size {
        ctx.verify_errors.push(DexIssue {
            kind: "VerifyError".into(),
            source: ctx.source.into(),
            class_name: ctx.class_name.into(),
            method: ctx.method_sig.into(),
            target: String::new(),
            pc: 0,
            detail: format!("ins_size ({ins_size}) > registers_size ({regs_size})"),
            boot_critical: ctx.boot_critical,
        });
        return;
    }

    let insns_start = code_off + 16;
    let Some(insns_bytes) = insns_size.checked_mul(2) else {
        return;
    };
    if insns_start + insns_bytes > data.len() {
        ctx.verify_errors.push(DexIssue {
            kind: "VerifyError".into(),
            source: ctx.source.into(),
            class_name: ctx.class_name.into(),
            method: ctx.method_sig.into(),
            target: String::new(),
            pc: 0,
            detail: "code_item insns array exceeds DEX file size".into(),
            boot_critical: ctx.boot_critical,
        });
        return;
    }

    let mut insns = Vec::with_capacity(insns_size);
    for i in 0..insns_size {
        insns.push(r_u16(data, insns_start + i * 2).unwrap_or(0));
    }
    *ctx.insns_counted += insns_size;

    let mut jump_targets: HashSet<usize> = HashSet::new();
    let mut catch_targets: HashSet<usize> = HashSet::new();
    struct TryGuard<'a> {
        start: usize,
        end: usize,
        catches_all: bool,
        caught_types: Vec<&'a str>,
    }
    let mut try_guards: Vec<TryGuard<'_>> = Vec::new();
    if tries_size > 0 {
        let pad = if insns_size % 2 == 1 { 2 } else { 0 };
        let tries_off = insns_start + insns_bytes + pad;
        let handlers_off = tries_off + tries_size * 8;
        let mut raw_tries: Vec<(usize, usize, usize)> = Vec::with_capacity(tries_size.min(256));
        for t in 0..tries_size {
            let to = tries_off + t * 8;
            if to + 8 > data.len() {
                ctx.verify_errors.push(DexIssue {
                    kind: "VerifyError".into(),
                    source: ctx.source.into(),
                    class_name: ctx.class_name.into(),
                    method: ctx.method_sig.into(),
                    target: String::new(),
                    pc: 0,
                    detail: "try_item exceeds DEX file size".into(),
                    boot_critical: ctx.boot_critical,
                });
                break;
            }
            let start_addr = r_u32(data, to).unwrap_or(0) as usize;
            let count = r_u16(data, to + 4).unwrap_or(0) as usize;
            let handler_off = r_u16(data, to + 6).unwrap_or(0) as usize;
            if start_addr + count > insns_size {
                ctx.verify_errors.push(DexIssue {
                    kind: "VerifyError".into(),
                    source: ctx.source.into(),
                    class_name: ctx.class_name.into(),
                    method: ctx.method_sig.into(),
                    target: String::new(),
                    pc: start_addr,
                    detail: format!(
                        "try_item range [{start_addr}..{}) exceeds insns_size ({insns_size})",
                        start_addr + count
                    ),
                    boot_critical: ctx.boot_critical,
                });
            }
            jump_targets.insert(start_addr);
            if raw_tries.len() < 256 {
                raw_tries.push((start_addr, start_addr + count, handler_off));
            }
        }
        let read_sleb128 = |buf: &[u8], pos: &mut usize| -> Option<i32> {
            let mut val: i32 = 0;
            let mut shift = 0u32;
            for _ in 0..5 {
                let &b = buf.get(*pos)?;
                *pos += 1;
                val |= ((b & 0x7f) as i32) << shift;
                shift += 7;
                if (b & 0x80) == 0 {
                    if shift < 32 && (b & 0x40) != 0 {
                        val |= !0i32 << shift;
                    }
                    return Some(val);
                }
            }
            None
        };
        if handlers_off < data.len() {
            for (t_start, t_end, h_off) in raw_tries {
                let mut hpos = handlers_off + h_off;
                if hpos >= data.len() {
                    continue;
                }
                let Some(size_i) = read_sleb128(data, &mut hpos) else {
                    continue;
                };
                let abs_sz = size_i.unsigned_abs() as usize;
                let mut caught = Vec::new();
                for _ in 0..abs_sz.min(32) {
                    let Some(tidx) = read_uleb128(data, &mut hpos) else {
                        break;
                    };
                    if let Some(addr) = read_uleb128(data, &mut hpos) {
                        jump_targets.insert(addr as usize);
                        catch_targets.insert(addr as usize);
                    }
                    let tn = ctx.tables.type_name(tidx as usize);
                    if !tn.is_empty() {
                        caught.push(tn);
                    }
                }
                let mut catches_all = false;
                if size_i <= 0 {
                    if let Some(addr) = read_uleb128(data, &mut hpos) {
                        let a = addr as usize;
                        jump_targets.insert(a);
                        catch_targets.insert(a);
                        // Distinguish real catch-all recovery from compiler-generated
                        // `finally { ...; throw e; }` / `synchronized { monitor-exit; throw e; }`
                        // re-throw blocks, which do NOT swallow exceptions.
                        let mut hp = a;
                        let mut is_rethrow = false;
                        for _ in 0..24 {
                            let Some(w) = insns.get(hp).copied() else {
                                break;
                            };
                            let hop = (w & 0xff) as u8;
                            if hop == 0x27 {
                                is_rethrow = true;
                                break;
                            }
                            if matches!(hop, 0x0e..=0x11 | 0x28..=0x2a | 0x2b | 0x2c) {
                                break;
                            }
                            let Some(hlen) = dalvik_insn_len(&insns, hp) else {
                                break;
                            };
                            hp += hlen;
                        }
                        if !is_rethrow {
                            catches_all = true;
                        }
                    } else {
                        catches_all = true;
                    }
                }
                try_guards.push(TryGuard {
                    start: t_start,
                    end: t_end,
                    catches_all,
                    caught_types: caught,
                });
            }
        }
    }
    let is_guarded = |pc: usize, kind: &str| -> bool {
        for g in &try_guards {
            if pc < g.start || pc >= g.end {
                continue;
            }
            if g.catches_all {
                return true;
            }
            for &ty in &g.caught_types {
                if ty == "Ljava/lang/Throwable;" {
                    return true;
                }
                match kind {
                    "jni" => {
                        if matches!(
                            ty,
                            "Ljava/lang/Error;"
                                | "Ljava/lang/LinkageError;"
                                | "Ljava/lang/UnsatisfiedLinkError;"
                                | "Ljava/lang/SecurityException;"
                        ) {
                            return true;
                        }
                    }
                    "reflection" => {
                        if matches!(
                            ty,
                            "Ljava/lang/Exception;"
                                | "Ljava/lang/ReflectiveOperationException;"
                                | "Ljava/lang/ClassNotFoundException;"
                                | "Ljava/lang/NoSuchMethodException;"
                                | "Ljava/lang/NoSuchFieldException;"
                                | "Ljava/lang/IllegalAccessException;"
                                | "Ljava/lang/SecurityException;"
                        ) {
                            return true;
                        }
                    }
                    "service" | "npe" => {
                        if matches!(
                            ty,
                            "Ljava/lang/Exception;"
                                | "Ljava/lang/RuntimeException;"
                                | "Ljava/lang/NullPointerException;"
                                | "Ljava/lang/IllegalStateException;"
                                | "Ljava/lang/SecurityException;"
                        ) {
                            return true;
                        }
                    }
                    _ => {}
                }
            }
        }
        false
    };

    // Pass 1: decode instruction boundaries + branch targets + SSA def counts + verify structure
    let mut pc = 0usize;
    let mut decoded: Vec<(usize, usize)> = Vec::with_capacity(insns_size / 2 + 1);
    let mut reg_def_count = [0u16; 64];

    while pc < insns_size {
        let Some(len) = dalvik_insn_len(&insns, pc) else {
            ctx.verify_errors.push(DexIssue {
                kind: "VerifyError".into(),
                source: ctx.source.into(),
                class_name: ctx.class_name.into(),
                method: ctx.method_sig.into(),
                target: String::new(),
                pc,
                detail: format!("invalid/unused Dalvik opcode 0x{:04x}", insns[pc]),
                boot_critical: ctx.boot_critical,
            });
            return;
        };
        if pc + len > insns_size {
            ctx.verify_errors.push(DexIssue {
                kind: "VerifyError".into(),
                source: ctx.source.into(),
                class_name: ctx.class_name.into(),
                method: ctx.method_sig.into(),
                target: String::new(),
                pc,
                detail: format!(
                    "truncated instruction at pc={pc} (needs {len} units, insns_size={insns_size})"
                ),
                boot_critical: ctx.boot_critical,
            });
            return;
        }
        let word = insns[pc];
        if word != 0x0100 && word != 0x0200 && word != 0x0300 {
            let op = (word & 0xff) as u8;
            let dst_reg: Option<usize> = match op {
                0x01 | 0x04 | 0x07 | 0x12 | 0x20 | 0x21 | 0x23 | 0x52..=0x58 | 0x7b..=0x8f
                | 0xb0..=0xcf | 0xd0..=0xe2 => Some(((word >> 8) & 0x0f) as usize),
                0x02 | 0x05 | 0x08 | 0x0a..=0x0d | 0x13..=0x1c | 0x22 | 0x2d..=0x31
                | 0x44..=0x4a | 0x60..=0x66 | 0x90..=0xaf | 0xfe | 0xff => {
                    Some(((word >> 8) & 0xff) as usize)
                }
                0x03 | 0x06 | 0x09 => Some(insns[pc + 1] as usize),
                _ => None,
            };
            if let Some(dr) = dst_reg {
                if dr < 64 {
                    reg_def_count[dr] = reg_def_count[dr].saturating_add(1);
                }
            }
            let branch_rel: Option<i32> = match op {
                0x28 => Some(((word >> 8) as i8) as i32),
                0x29 => Some((insns[pc + 1] as i16) as i32),
                0x2a => Some(((insns[pc + 1] as u32) | ((insns[pc + 2] as u32) << 16)) as i32),
                0x32..=0x3d => Some((insns[pc + 1] as i16) as i32),
                _ => None,
            };
            if let Some(rel) = branch_rel {
                let target_i = pc as i64 + rel as i64;
                if target_i < 0 || target_i as usize >= insns_size {
                    ctx.verify_errors.push(DexIssue {
                        kind: "VerifyError".into(),
                        source: ctx.source.into(),
                        class_name: ctx.class_name.into(),
                        method: ctx.method_sig.into(),
                        target: String::new(),
                        pc,
                        detail: format!(
                            "branch target {target_i} out of bounds [0..{insns_size})"
                        ),
                        boot_critical: ctx.boot_critical,
                    });
                } else {
                    jump_targets.insert(target_i as usize);
                }
            }
            if op == 0x2b || op == 0x2c {
                let rel = ((insns[pc + 1] as u32) | ((insns[pc + 2] as u32) << 16)) as i32;
                let pay_i = pc as i64 + rel as i64;
                if pay_i >= 0 && (pay_i as usize) + 2 <= insns_size {
                    let pay = pay_i as usize;
                    if insns[pay] == 0x0100 {
                        let sz = insns[pay + 1] as usize;
                        for k in 0..sz {
                            let off_u = pay + 4 + k * 2;
                            if off_u + 1 < insns_size {
                                let d = ((insns[off_u] as u32) | ((insns[off_u + 1] as u32) << 16)) as i32;
                                let dst = pc as i64 + d as i64;
                                if dst >= 0 && (dst as usize) < insns_size {
                                    jump_targets.insert(dst as usize);
                                }
                            }
                        }
                    } else if insns[pay] == 0x0200 {
                        let sz = insns[pay + 1] as usize;
                        let targets_base = pay + 2 + sz * 2;
                        for k in 0..sz {
                            let off_u = targets_base + k * 2;
                            if off_u + 1 < insns_size {
                                let d = ((insns[off_u] as u32) | ((insns[off_u + 1] as u32) << 16)) as i32;
                                let dst = pc as i64 + d as i64;
                                if dst >= 0 && (dst as usize) < insns_size {
                                    jump_targets.insert(dst as usize);
                                }
                            }
                        }
                    }
                }
            }
        }
        decoded.push((pc, len));
        pc += len;
    }

    // Pass 2: register bounds, return type match, null-flow, constant-string/class/bool/StringBuilder dataflow,
    // dead-branch feature/prop pruning, try/catch guard tracking, and linkage callsites
    #[derive(Clone, Copy)]
    enum RegStr {
        None,
        Dex(u32),
        Dyn(u16),
    }
    #[derive(Clone, Copy)]
    enum RegCls {
        None,
        Dex(u32),
        Dyn(u16),
    }
    let track_regs = regs_size.min(64);
    let mut reg_null = [false; 64];
    let mut reg_str = [RegStr::None; 64];
    let mut reg_cls = [RegCls::None; 64];
    let mut reg_sb: [Option<u16>; 64] = [None; 64];
    let mut reg_bool: [Option<bool>; 64] = [None; 64];
    let mut dyn_strs: Vec<String> = Vec::new();
    let mut dyn_clss: Vec<String> = Vec::new();
    let mut sb_bufs: Vec<Option<String>> = Vec::new();
    let mut pending_str: Option<String> = None;
    let mut pending_cls: Option<String> = None;
    let mut pending_sb: Option<u16> = None;
    let mut pending_bool: Option<bool> = None;
    let mut dead_until: usize = 0;
    let mut prev_was_uncond_term = false;

    let check_reg = |r: usize, pc: usize, errs: &mut Vec<DexIssue>| {
        if r >= regs_size && errs.len() < 80 {
            errs.push(DexIssue {
                kind: "VerifyError".into(),
                source: ctx.source.into(),
                class_name: ctx.class_name.into(),
                method: ctx.method_sig.into(),
                target: String::new(),
                pc,
                detail: format!("register v{r} out of bounds (registers_size={regs_size})"),
                boot_critical: ctx.boot_critical,
            });
        }
    };

    for &(pc, _len) in &decoded {
        if pc >= dead_until {
            dead_until = 0;
        }
        let word = insns[pc];
        if word == 0x0100 || word == 0x0200 || word == 0x0300 {
            reg_null.fill(false);
            reg_str.fill(RegStr::None);
            reg_cls.fill(RegCls::None);
            reg_sb.fill(None);
            reg_bool.fill(None);
            pending_str = None;
            pending_cls = None;
            pending_sb = None;
            pending_bool = None;
            dead_until = 0;
            prev_was_uncond_term = true;
            continue;
        }
        if jump_targets.contains(&pc) || prev_was_uncond_term {
            let is_catch = catch_targets.contains(&pc);
            reg_null.fill(false);
            reg_sb.fill(None);
            // Preserve true single-def (SSA/final) registers across normal basic-block merges
            // so `final boolean isWatch = hasSystemFeature(...)` survives intervening if/else blocks!
            for r in 0..track_regs {
                if is_catch || reg_def_count[r] != 1 {
                    reg_str[r] = RegStr::None;
                    reg_cls[r] = RegCls::None;
                    reg_bool[r] = None;
                }
            }
            pending_str = None;
            pending_cls = None;
            pending_sb = None;
            pending_bool = None;
            if pc >= dead_until || is_catch {
                dead_until = 0;
            }
            prev_was_uncond_term = false;
        }

        let is_dead = pc < dead_until;
        let op = (word & 0xff) as u8;
        let aa = ((word >> 8) & 0xff) as usize;
        let a4 = ((word >> 8) & 0x0f) as usize;
        let b4 = ((word >> 12) & 0x0f) as usize;

        if !matches!(op, 0x0a..=0x0d | 0x6e..=0x72 | 0x74..=0x78) {
            pending_str = None;
            pending_cls = None;
            pending_sb = None;
            pending_bool = None;
        }

        match op {
            0x01 | 0x04 | 0x07 => {
                check_reg(a4, pc, ctx.verify_errors);
                check_reg(b4, pc, ctx.verify_errors);
                if a4 < track_regs {
                    if op == 0x07 && b4 < track_regs {
                        reg_null[a4] = reg_null[b4];
                        reg_str[a4] = reg_str[b4];
                        reg_cls[a4] = reg_cls[b4];
                        reg_sb[a4] = reg_sb[b4];
                        reg_bool[a4] = None;
                    } else if op == 0x01 && b4 < track_regs {
                        reg_null[a4] = false;
                        reg_str[a4] = RegStr::None;
                        reg_cls[a4] = RegCls::None;
                        reg_sb[a4] = None;
                        reg_bool[a4] = reg_bool[b4];
                    } else {
                        reg_null[a4] = false;
                        reg_str[a4] = RegStr::None;
                        reg_cls[a4] = RegCls::None;
                        reg_sb[a4] = None;
                        reg_bool[a4] = None;
                    }
                }
            }
            0x02 | 0x05 | 0x08 => {
                let b16 = insns[pc + 1] as usize;
                check_reg(aa, pc, ctx.verify_errors);
                check_reg(b16, pc, ctx.verify_errors);
                if aa < track_regs {
                    if op == 0x08 && b16 < track_regs {
                        reg_null[aa] = reg_null[b16];
                        reg_str[aa] = reg_str[b16];
                        reg_cls[aa] = reg_cls[b16];
                        reg_sb[aa] = reg_sb[b16];
                        reg_bool[aa] = None;
                    } else {
                        reg_null[aa] = false;
                        reg_str[aa] = RegStr::None;
                        reg_cls[aa] = RegCls::None;
                        reg_sb[aa] = None;
                        reg_bool[aa] = if op == 0x02 && b16 < track_regs { reg_bool[b16] } else { None };
                    }
                }
            }
            0x03 | 0x06 | 0x09 => {
                let a16 = insns[pc + 1] as usize;
                let b16 = insns[pc + 2] as usize;
                check_reg(a16, pc, ctx.verify_errors);
                check_reg(b16, pc, ctx.verify_errors);
                if a16 < track_regs {
                    if op == 0x09 && b16 < track_regs {
                        reg_null[a16] = reg_null[b16];
                        reg_str[a16] = reg_str[b16];
                        reg_cls[a16] = reg_cls[b16];
                        reg_sb[a16] = reg_sb[b16];
                        reg_bool[a16] = None;
                    } else {
                        reg_null[a16] = false;
                        reg_str[a16] = RegStr::None;
                        reg_cls[a16] = RegCls::None;
                        reg_sb[a16] = None;
                        reg_bool[a16] = None;
                    }
                }
            }
            0x0a..=0x0d => {
                check_reg(aa, pc, ctx.verify_errors);
                if aa < track_regs {
                    reg_null[aa] = false;
                    reg_str[aa] = RegStr::None;
                    reg_cls[aa] = RegCls::None;
                    reg_sb[aa] = None;
                    reg_bool[aa] = None;
                    if op == 0x0a {
                        reg_bool[aa] = pending_bool.take();
                    } else if op == 0x0c {
                        if let Some(s) = pending_str.take() {
                            if dyn_strs.len() < 128 {
                                let idx = dyn_strs.len() as u16;
                                dyn_strs.push(s);
                                reg_str[aa] = RegStr::Dyn(idx);
                            }
                        }
                        if let Some(c) = pending_cls.take() {
                            if dyn_clss.len() < 64 {
                                let idx = dyn_clss.len() as u16;
                                dyn_clss.push(c);
                                reg_cls[aa] = RegCls::Dyn(idx);
                            }
                        }
                        if let Some(sb) = pending_sb.take() {
                            reg_sb[aa] = Some(sb);
                        }
                    }
                }
                pending_str = None;
                pending_cls = None;
                pending_sb = None;
                pending_bool = None;
            }
            0x0e => {
                if !ctx.return_type.is_empty()
                    && ctx.return_type != "V"
                    && ctx.verify_errors.len() < 80
                {
                    ctx.verify_errors.push(DexIssue {
                        kind: "VerifyError".into(),
                        source: ctx.source.into(),
                        class_name: ctx.class_name.into(),
                        method: ctx.method_sig.into(),
                        target: String::new(),
                        pc,
                        detail: format!("return-void in method returning '{}'", ctx.return_type),
                        boot_critical: ctx.boot_critical,
                    });
                }
                prev_was_uncond_term = true;
            }
            0x0f => {
                check_reg(aa, pc, ctx.verify_errors);
                if (matches!(ctx.return_type, "V" | "J" | "D")
                    || ctx.return_type.starts_with('L')
                    || ctx.return_type.starts_with('['))
                    && ctx.verify_errors.len() < 80
                {
                    ctx.verify_errors.push(DexIssue {
                        kind: "VerifyError".into(),
                        source: ctx.source.into(),
                        class_name: ctx.class_name.into(),
                        method: ctx.method_sig.into(),
                        target: String::new(),
                        pc,
                        detail: format!("primitive return in method returning '{}'", ctx.return_type),
                        boot_critical: ctx.boot_critical,
                    });
                }
                prev_was_uncond_term = true;
            }
            0x10 => {
                check_reg(aa, pc, ctx.verify_errors);
                if regs_size > 0 {
                    check_reg(aa + 1, pc, ctx.verify_errors);
                }
                if !ctx.return_type.is_empty()
                    && !matches!(ctx.return_type, "J" | "D")
                    && ctx.verify_errors.len() < 80
                {
                    ctx.verify_errors.push(DexIssue {
                        kind: "VerifyError".into(),
                        source: ctx.source.into(),
                        class_name: ctx.class_name.into(),
                        method: ctx.method_sig.into(),
                        target: String::new(),
                        pc,
                        detail: format!("return-wide in method returning '{}'", ctx.return_type),
                        boot_critical: ctx.boot_critical,
                    });
                }
                prev_was_uncond_term = true;
            }
            0x11 => {
                check_reg(aa, pc, ctx.verify_errors);
                if !ctx.return_type.is_empty()
                    && !ctx.return_type.starts_with('L')
                    && !ctx.return_type.starts_with('[')
                    && ctx.verify_errors.len() < 80
                {
                    ctx.verify_errors.push(DexIssue {
                        kind: "VerifyError".into(),
                        source: ctx.source.into(),
                        class_name: ctx.class_name.into(),
                        method: ctx.method_sig.into(),
                        target: String::new(),
                        pc,
                        detail: format!("return-object in method returning '{}'", ctx.return_type),
                        boot_critical: ctx.boot_critical,
                    });
                }
                prev_was_uncond_term = true;
            }
            0x12 => {
                check_reg(a4, pc, ctx.verify_errors);
                if a4 < track_regs {
                    reg_null[a4] = b4 == 0;
                    reg_str[a4] = RegStr::None;
                    reg_cls[a4] = RegCls::None;
                    reg_sb[a4] = None;
                    reg_bool[a4] = Some(b4 != 0);
                }
            }
            0x13 => {
                check_reg(aa, pc, ctx.verify_errors);
                if aa < track_regs {
                    reg_null[aa] = insns[pc + 1] == 0;
                    reg_str[aa] = RegStr::None;
                    reg_cls[aa] = RegCls::None;
                    reg_sb[aa] = None;
                    reg_bool[aa] = Some(insns[pc + 1] != 0);
                }
            }
            0x14 => {
                check_reg(aa, pc, ctx.verify_errors);
                let val = (insns[pc + 1] as u32) | ((insns[pc + 2] as u32) << 16);
                if aa < track_regs {
                    reg_null[aa] = val == 0;
                    reg_str[aa] = RegStr::None;
                    reg_cls[aa] = RegCls::None;
                    reg_sb[aa] = None;
                    reg_bool[aa] = None;
                }
                if let Some(fw_ids) = ctx.framework_res_ids {
                    let pkg = (val >> 24) & 0xff;
                    let typ = (val >> 16) & 0xff;
                    let entry = val & 0xffff;
                    if pkg == 0x01
                        && (0x02..=0x16).contains(&typ)
                        && entry > 0
                        && entry < 0x4000
                        && (ctx.class_name.contains("/R$") || ctx.class_name.contains("SystemUI"))
                        && !fw_ids.contains(&val)
                        && ctx.missing_res.len() < 50
                    {
                        ctx.missing_res.push(DexIssue {
                            kind: "MissingFrameworkResId".into(),
                            source: ctx.source.into(),
                            class_name: ctx.class_name.into(),
                            method: ctx.method_sig.into(),
                            target: format!("0x{val:08x}"),
                            pc,
                            detail: format!(
                                "references framework resource 0x{val:08x} absent from framework-res.apk"
                            ),
                            boot_critical: ctx.boot_critical,
                        });
                    }
                }
            }
            0x15..=0x1c | 0xfe | 0xff => {
                check_reg(aa, pc, ctx.verify_errors);
                if aa < track_regs {
                    reg_null[aa] = false;
                    reg_sb[aa] = None;
                    reg_bool[aa] = None;
                    if op == 0x1a {
                        reg_str[aa] = RegStr::Dex(insns[pc + 1] as u32);
                        reg_cls[aa] = RegCls::None;
                    } else if op == 0x1b {
                        let sidx = (insns[pc + 1] as u32) | ((insns[pc + 2] as u32) << 16);
                        reg_str[aa] = RegStr::Dex(sidx);
                        reg_cls[aa] = RegCls::None;
                    } else if op == 0x1c {
                        reg_cls[aa] = RegCls::Dex(insns[pc + 1] as u32);
                        reg_str[aa] = RegStr::None;
                    } else {
                        reg_str[aa] = RegStr::None;
                        reg_cls[aa] = RegCls::None;
                    }
                }
                if op == 0x1c {
                    let tidx = insns[pc + 1] as usize;
                    let target = ctx.tables.type_name(tidx);
                    if !target.is_empty()
                        && !target.starts_with('[')
                        && target.starts_with('L')
                        && lookup_class(ctx.own_classes, ctx.global_classes, target).is_none()
                        && is_core_android_or_oem_class(target)
                    {
                        ctx.missing_classes
                            .entry(target.to_string())
                            .or_default()
                            .push(format!("{}.{}", ctx.class_name, ctx.method_sig));
                    }
                }
            }
            // monitor-enter / monitor-exit / throw
            // Note: R8 intentionally emits `const/4 v0, 0; throw v0` for dead-code elimination,
            // so `throw` (0x27) is NOT flagged as an NPE bug.
            0x1d | 0x1e | 0x27 => {
                check_reg(aa, pc, ctx.verify_errors);
                if op == 0x1d && aa < track_regs && reg_null[aa] && ctx.null_derefs.len() < 80 {
                    ctx.null_derefs.push(DexIssue {
                        kind: "NullPointerException".into(),
                        source: ctx.source.into(),
                        class_name: ctx.class_name.into(),
                        method: ctx.method_sig.into(),
                        target: "monitor-enter null".into(),
                        pc,
                        detail: format!("monitor-enter on definitely-null register v{aa} at pc={pc}"),
                        boot_critical: ctx.boot_critical,
                    });
                }
                if op == 0x27 {
                    prev_was_uncond_term = true;
                }
            }
            0x1f => {
                check_reg(aa, pc, ctx.verify_errors);
            }
            0x20 => {
                check_reg(a4, pc, ctx.verify_errors);
                check_reg(b4, pc, ctx.verify_errors);
                if a4 < track_regs {
                    reg_null[a4] = false;
                }
            }
            0x21 => {
                check_reg(a4, pc, ctx.verify_errors);
                check_reg(b4, pc, ctx.verify_errors);
                if b4 < track_regs && reg_null[b4] && ctx.null_derefs.len() < 80 {
                    ctx.null_derefs.push(DexIssue {
                        kind: "NullPointerException".into(),
                        source: ctx.source.into(),
                        class_name: ctx.class_name.into(),
                        method: ctx.method_sig.into(),
                        target: "array-length".into(),
                        pc,
                        detail: format!("array-length on definitely-null register v{b4} at pc={pc}"),
                        boot_critical: ctx.boot_critical,
                    });
                }
                if a4 < track_regs {
                    reg_null[a4] = false;
                }
            }
            0x22 => {
                check_reg(aa, pc, ctx.verify_errors);
                let tidx = insns[pc + 1] as usize;
                let target = ctx.tables.type_name(tidx);
                if aa < track_regs {
                    reg_null[aa] = false;
                    reg_str[aa] = RegStr::None;
                    reg_cls[aa] = RegCls::None;
                    reg_bool[aa] = None;
                    if target == "Ljava/lang/StringBuilder;" && sb_bufs.len() < 32 {
                        let idx = sb_bufs.len() as u16;
                        sb_bufs.push(Some(String::new()));
                        reg_sb[aa] = Some(idx);
                    } else {
                        reg_sb[aa] = None;
                    }
                }
                if !target.is_empty()
                    && lookup_class(ctx.own_classes, ctx.global_classes, target).is_none()
                    && is_core_android_or_oem_class(target)
                {
                    ctx.missing_classes
                        .entry(target.to_string())
                        .or_default()
                        .push(format!("{}.{}", ctx.class_name, ctx.method_sig));
                }
            }
            0x23 => {
                check_reg(a4, pc, ctx.verify_errors);
                check_reg(b4, pc, ctx.verify_errors);
                if a4 < track_regs {
                    reg_null[a4] = false;
                    reg_str[a4] = RegStr::None;
                    reg_cls[a4] = RegCls::None;
                    reg_sb[a4] = None;
                    reg_bool[a4] = None;
                }
            }
            0x28..=0x2a => {
                prev_was_uncond_term = true;
            }
            0x38 | 0x39 => {
                check_reg(aa, pc, ctx.verify_errors);
                if aa < track_regs {
                    reg_null[aa] = false;
                    if let Some(bval) = reg_bool[aa] {
                        let taken = if op == 0x38 { !bval } else { bval };
                        let rel = (insns[pc + 1] as i16) as i64;
                        let target_i = pc as i64 + rel;
                        if taken && target_i > (pc + 2) as i64 && target_i <= (pc + 4096) as i64 {
                            let tgt = target_i as usize;
                            if tgt > dead_until {
                                dead_until = tgt;
                                ctx.logic.dead_branches_pruned += 1;
                            }
                        }
                    }
                }
            }
            0x32..=0x37 => {
                check_reg(a4, pc, ctx.verify_errors);
                check_reg(b4, pc, ctx.verify_errors);
                if a4 < track_regs {
                    reg_null[a4] = false;
                }
                if b4 < track_regs {
                    reg_null[b4] = false;
                }
            }
            0x44..=0x51 => {
                let v_arr = (insns[pc + 1] & 0xff) as usize;
                let v_idx = ((insns[pc + 1] >> 8) & 0xff) as usize;
                check_reg(aa, pc, ctx.verify_errors);
                check_reg(v_arr, pc, ctx.verify_errors);
                check_reg(v_idx, pc, ctx.verify_errors);
                if v_arr < track_regs && reg_null[v_arr] && ctx.null_derefs.len() < 80 {
                    ctx.null_derefs.push(DexIssue {
                        kind: "NullPointerException".into(),
                        source: ctx.source.into(),
                        class_name: ctx.class_name.into(),
                        method: ctx.method_sig.into(),
                        target: if op <= 0x4a { "aget".into() } else { "aput".into() },
                        pc,
                        detail: format!("array access on definitely-null register v{v_arr} at pc={pc}"),
                        boot_critical: ctx.boot_critical,
                    });
                }
                if op <= 0x4a && aa < track_regs {
                    reg_null[aa] = false;
                    reg_str[aa] = RegStr::None;
                    reg_cls[aa] = RegCls::None;
                    reg_sb[aa] = None;
                    reg_bool[aa] = None;
                }
            }
            0x52..=0x5f => {
                check_reg(a4, pc, ctx.verify_errors);
                check_reg(b4, pc, ctx.verify_errors);
                let fidx = insns[pc + 1] as usize;
                if b4 < track_regs && reg_null[b4] && ctx.null_derefs.len() < 80 {
                    let target = ctx
                        .tables
                        .field_triple(fidx)
                        .map(|(c, n, _)| format!("{c}->{n}"))
                        .unwrap_or_else(|| "field".into());
                    ctx.null_derefs.push(DexIssue {
                        kind: "NullPointerException".into(),
                        source: ctx.source.into(),
                        class_name: ctx.class_name.into(),
                        method: ctx.method_sig.into(),
                        target,
                        pc,
                        detail: format!(
                            "instance field access on definitely-null receiver v{b4} at pc={pc}"
                        ),
                        boot_critical: ctx.boot_critical,
                    });
                }
                if op <= 0x58 && a4 < track_regs {
                    reg_null[a4] = false;
                    reg_str[a4] = RegStr::None;
                    reg_cls[a4] = RegCls::None;
                    reg_sb[a4] = None;
                    reg_bool[a4] = None;
                }
                if let Some((owner, fname, ftype)) = ctx.tables.field_triple(fidx) {
                    if is_core_android_or_oem_class(owner) {
                        match resolve_field(ctx.own_classes, ctx.global_classes, owner, fname, ftype) {
                            ResolveResult::Found(flags) => {
                                if (flags & ACC_STATIC) != 0 && ctx.incompat.len() < 80 {
                                    ctx.incompat.push(DexIssue {
                                        kind: "IncompatibleClassChangeError".into(),
                                        source: ctx.source.into(),
                                        class_name: ctx.class_name.into(),
                                        method: ctx.method_sig.into(),
                                        target: format!("{owner}->{fname}:{ftype}"),
                                        pc,
                                        detail: "instance field opcode targets a static field".into(),
                                        boot_critical: ctx.boot_critical,
                                    });
                                }
                            }
                            ResolveResult::MissingResolvedChain => {
                                if ctx.missing_fields.len() < 120 {
                                    ctx.missing_fields.push(DexIssue {
                                        kind: "NoSuchFieldError".into(),
                                        source: ctx.source.into(),
                                        class_name: ctx.class_name.into(),
                                        method: ctx.method_sig.into(),
                                        target: format!("{owner}->{fname}:{ftype}"),
                                        pc,
                                        detail: format!(
                                            "field {fname}:{ftype} missing on resolved class {owner}"
                                        ),
                                        boot_critical: ctx.boot_critical,
                                    });
                                }
                            }
                            ResolveResult::UnresolvedHierarchy => {
                                if lookup_class(ctx.own_classes, ctx.global_classes, owner).is_none() {
                                    ctx.missing_classes
                                        .entry(owner.to_string())
                                        .or_default()
                                        .push(format!("{}.{}", ctx.class_name, ctx.method_sig));
                                }
                            }
                        }
                    }
                }
            }
            0x60..=0x6d => {
                check_reg(aa, pc, ctx.verify_errors);
                if op <= 0x66 && aa < track_regs {
                    reg_null[aa] = false;
                    reg_str[aa] = RegStr::None;
                    reg_cls[aa] = RegCls::None;
                    reg_sb[aa] = None;
                    reg_bool[aa] = None;
                }
                let fidx = insns[pc + 1] as usize;
                if let Some((owner, fname, ftype)) = ctx.tables.field_triple(fidx) {
                    if op == 0x62 && aa < track_regs && ftype == "Ljava/lang/String;" {
                        if let Some(sval) = resolve_static_string(ctx.own_classes, ctx.global_classes, owner, fname) {
                            if dyn_strs.len() < 128 {
                                let idx = dyn_strs.len() as u16;
                                dyn_strs.push(sval.to_string());
                                reg_str[aa] = RegStr::Dyn(idx);
                                ctx.logic.static_strings_resolved += 1;
                            }
                        }
                    }
                    if is_core_android_or_oem_class(owner) {
                        match resolve_field(ctx.own_classes, ctx.global_classes, owner, fname, ftype) {
                            ResolveResult::Found(flags) => {
                                if (flags & ACC_STATIC) == 0 && ctx.incompat.len() < 80 {
                                    ctx.incompat.push(DexIssue {
                                        kind: "IncompatibleClassChangeError".into(),
                                        source: ctx.source.into(),
                                        class_name: ctx.class_name.into(),
                                        method: ctx.method_sig.into(),
                                        target: format!("{owner}->{fname}:{ftype}"),
                                        pc,
                                        detail: "static field opcode targets an instance field".into(),
                                        boot_critical: ctx.boot_critical,
                                    });
                                }
                            }
                            ResolveResult::MissingResolvedChain => {
                                if ctx.missing_fields.len() < 120 {
                                    ctx.missing_fields.push(DexIssue {
                                        kind: "NoSuchFieldError".into(),
                                        source: ctx.source.into(),
                                        class_name: ctx.class_name.into(),
                                        method: ctx.method_sig.into(),
                                        target: format!("{owner}->{fname}:{ftype}"),
                                        pc,
                                        detail: format!(
                                            "static field {fname}:{ftype} missing on resolved class {owner}"
                                        ),
                                        boot_critical: ctx.boot_critical,
                                    });
                                }
                            }
                            ResolveResult::UnresolvedHierarchy => {
                                if lookup_class(ctx.own_classes, ctx.global_classes, owner).is_none() {
                                    ctx.missing_classes
                                        .entry(owner.to_string())
                                        .or_default()
                                        .push(format!("{}.{}", ctx.class_name, ctx.method_sig));
                                }
                            }
                        }
                    }
                }
            }
            0x6e..=0x72 | 0x74..=0x78 => {
                let kind = match op {
                    0x6e | 0x74 => InvokeKind::Virtual,
                    0x6f | 0x75 => InvokeKind::Super,
                    0x70 | 0x76 => InvokeKind::Direct,
                    0x71 | 0x77 => InvokeKind::Static,
                    _ => InvokeKind::Interface,
                };
                let midx = insns[pc + 1] as usize;
                let mut inv_args = [0usize; 5];
                let mut inv_argc = 0usize;
                let mut inv_range_first: Option<usize> = None;
                let receiver: Option<usize> = if op <= 0x72 {
                    let arg_cnt = ((word >> 12) & 0x0f) as usize;
                    let g = ((word >> 8) & 0x0f) as usize;
                    let w2 = insns[pc + 2];
                    let regs = [
                        (w2 & 0x0f) as usize,
                        ((w2 >> 4) & 0x0f) as usize,
                        ((w2 >> 8) & 0x0f) as usize,
                        ((w2 >> 12) & 0x0f) as usize,
                        g,
                    ];
                    if arg_cnt > 5 {
                        if ctx.verify_errors.len() < 80 {
                            ctx.verify_errors.push(DexIssue {
                                kind: "VerifyError".into(),
                                source: ctx.source.into(),
                                class_name: ctx.class_name.into(),
                                method: ctx.method_sig.into(),
                                target: String::new(),
                                pc,
                                detail: format!("invoke 35c arg count {arg_cnt} > 5"),
                                boot_critical: ctx.boot_critical,
                            });
                        }
                        None
                    } else {
                        inv_args = regs;
                        inv_argc = arg_cnt;
                        for &r in &regs[..arg_cnt] {
                            check_reg(r, pc, ctx.verify_errors);
                        }
                        if arg_cnt >= 1 { Some(regs[0]) } else { None }
                    }
                } else {
                    let arg_cnt = aa;
                    let first = insns[pc + 2] as usize;
                    inv_argc = arg_cnt;
                    inv_range_first = Some(first);
                    if arg_cnt > 0 {
                        check_reg(first, pc, ctx.verify_errors);
                        check_reg(first + arg_cnt - 1, pc, ctx.verify_errors);
                        Some(first)
                    } else {
                        None
                    }
                };
                let arg_reg_at = |idx: usize| -> Option<usize> {
                    if idx >= inv_argc {
                        return None;
                    }
                    if let Some(f) = inv_range_first {
                        Some(f + idx)
                    } else if idx < 5 {
                        Some(inv_args[idx])
                    } else {
                        None
                    }
                };
                pending_str = None;
                pending_cls = None;
                pending_sb = None;
                pending_bool = None;
                let arg_str_at = |idx: usize| -> Option<&str> {
                    let r = arg_reg_at(idx)?;
                    if r >= track_regs {
                        return None;
                    }
                    let s = match reg_str[r] {
                        RegStr::None => return None,
                        RegStr::Dex(sidx) => ctx.tables.string_at(sidx as usize),
                        RegStr::Dyn(didx) => dyn_strs.get(didx as usize).map(|x| x.as_str()).unwrap_or(""),
                    };
                    if s.is_empty() { None } else { Some(s) }
                };
                let arg_cls_at = |idx: usize| -> Option<&str> {
                    let r = arg_reg_at(idx)?;
                    if r >= track_regs {
                        return None;
                    }
                    let t = match reg_cls[r] {
                        RegCls::None => return None,
                        RegCls::Dex(tidx) => ctx.tables.type_name(tidx as usize),
                        RegCls::Dyn(didx) => dyn_clss.get(didx as usize).map(|x| x.as_str()).unwrap_or(""),
                    };
                    if t.is_empty() { None } else { Some(t) }
                };
                let arg_bool_at = |idx: usize| -> Option<bool> {
                    let r = arg_reg_at(idx)?;
                    if r >= track_regs {
                        return None;
                    }
                    reg_bool[r]
                };
                let arg_sb_at = |idx: usize| -> Option<u16> {
                    let r = arg_reg_at(idx)?;
                    if r >= track_regs {
                        return None;
                    }
                    reg_sb[r]
                };

                if kind != InvokeKind::Static && !ctx.method_sig.contains("$$forInline") {
                    if let Some(rcv) = receiver {
                        if rcv < track_regs && reg_null[rcv] && ctx.null_derefs.len() < 80 {
                            let target = ctx
                                .tables
                                .method_triple(midx)
                                .map(|(c, n, p)| format!("{c}->{n}{p}"))
                                .unwrap_or_else(|| "method".into());
                            ctx.null_derefs.push(DexIssue {
                                kind: "NullPointerException".into(),
                                source: ctx.source.into(),
                                class_name: ctx.class_name.into(),
                                method: ctx.method_sig.into(),
                                target,
                                pc,
                                detail: format!(
                                    "instance invoke on definitely-null receiver v{rcv} at pc={pc}"
                                ),
                                boot_critical: ctx.boot_critical,
                            });
                        }
                    }
                }

                if let Some((owner, mname, mproto)) = ctx.tables.method_triple(midx) {
                    // ── Semantic DEX Logic & Dataflow Extraction ──
                    match owner {
                        "Ljava/lang/StringBuilder;" => {
                            if let Some(sb_idx) = arg_sb_at(0) {
                                let si = sb_idx as usize;
                                if mname == "<init>" {
                                    if mproto == "(Ljava/lang/String;)V" {
                                        if let Some(s) = arg_str_at(1) {
                                            if s.len() <= 256 {
                                                if let Some(slot) = sb_bufs.get_mut(si) {
                                                    *slot = Some(s.to_string());
                                                }
                                            }
                                        } else if let Some(slot) = sb_bufs.get_mut(si) {
                                            *slot = None;
                                        }
                                    } else if mproto != "()V" && mproto != "(I)V" {
                                        if let Some(slot) = sb_bufs.get_mut(si) {
                                            *slot = None;
                                        }
                                    }
                                } else if mname == "append" {
                                    if mproto == "(Ljava/lang/String;)Ljava/lang/StringBuilder;" {
                                        if let Some(s) = arg_str_at(1) {
                                            let s_owned = s.to_string();
                                            if let Some(Some(buf)) = sb_bufs.get_mut(si) {
                                                if buf.len() + s_owned.len() <= 256 {
                                                    buf.push_str(&s_owned);
                                                    pending_sb = Some(sb_idx);
                                                } else {
                                                    sb_bufs[si] = None;
                                                }
                                            }
                                        } else if let Some(slot) = sb_bufs.get_mut(si) {
                                            *slot = None;
                                        }
                                    } else if let Some(slot) = sb_bufs.get_mut(si) {
                                        *slot = None;
                                    }
                                } else if mname == "toString" && mproto == "()Ljava/lang/String;" {
                                    if let Some(Some(buf)) = sb_bufs.get(si) {
                                        if !buf.is_empty() {
                                            pending_str = Some(buf.clone());
                                        }
                                    }
                                }
                            }
                        }
                        "Ljava/lang/String;" if mname == "concat" && mproto == "(Ljava/lang/String;)Ljava/lang/String;" => {
                            if let (Some(a), Some(b)) = (arg_str_at(0), arg_str_at(1)) {
                                if a.len() + b.len() <= 256 {
                                    pending_str = Some(format!("{a}{b}"));
                                }
                            }
                        }
                        "Landroid/content/pm/PackageManager;"
                        | "Lcom/android/server/pm/PackageManagerService;"
                        | "Landroid/content/pm/IPackageManager;"
                            if mname == "hasSystemFeature" =>
                        {
                            if let (Some(feat), Some(enabled)) = (arg_str_at(1), ctx.enabled_features) {
                                pending_bool = Some(enabled.contains(feat));
                            }
                        }
                        "Ljava/lang/System;" if mname == "loadLibrary" => {
                            if let Some(lib) = arg_str_at(0) {
                                let guarded = is_guarded(pc, "jni");
                                if ctx.logic.jni_libraries.len() < 400 {
                                    ctx.logic.jni_libraries.push(LogicCallSite {
                                        value: lib.to_string(),
                                        secondary: "System.loadLibrary".into(),
                                        source: ctx.source.into(),
                                        caller: format!("{}.{}", ctx.class_name, ctx.method_sig),
                                        resolved: true,
                                        blocking: true,
                                        boot_critical: ctx.boot_critical,
                                        guarded,
                                        dead_branch: is_dead,
                                    });
                                }
                                if !is_dead && ctx.logic.jni_bindings.len() < 250 {
                                    if let Some(cinfo) = lookup_class(ctx.own_classes, ctx.global_classes, ctx.class_name) {
                                        if !cinfo.native_methods.is_empty() {
                                            ctx.logic.jni_bindings.push(JniBinding {
                                                lib: lib.to_string(),
                                                class_name: ctx.class_name.to_string(),
                                                methods: cinfo.native_methods.clone(),
                                                source: ctx.source.into(),
                                                guarded,
                                                boot_critical: ctx.boot_critical,
                                            });
                                        }
                                    }
                                }
                            }
                        }
                        "Ljava/lang/Runtime;" if mname == "loadLibrary" || mname == "loadLibrary0" => {
                            let lib = arg_str_at(1).or_else(|| arg_str_at(2));
                            if let Some(lib) = lib {
                                let guarded = is_guarded(pc, "jni");
                                if ctx.logic.jni_libraries.len() < 400 {
                                    ctx.logic.jni_libraries.push(LogicCallSite {
                                        value: lib.to_string(),
                                        secondary: format!("Runtime.{mname}"),
                                        source: ctx.source.into(),
                                        caller: format!("{}.{}", ctx.class_name, ctx.method_sig),
                                        resolved: true,
                                        blocking: true,
                                        boot_critical: ctx.boot_critical,
                                        guarded,
                                        dead_branch: is_dead,
                                    });
                                }
                                if !is_dead && ctx.logic.jni_bindings.len() < 250 {
                                    if let Some(cinfo) = lookup_class(ctx.own_classes, ctx.global_classes, ctx.class_name) {
                                        if !cinfo.native_methods.is_empty() {
                                            ctx.logic.jni_bindings.push(JniBinding {
                                                lib: lib.to_string(),
                                                class_name: ctx.class_name.to_string(),
                                                methods: cinfo.native_methods.clone(),
                                                source: ctx.source.into(),
                                                guarded,
                                                boot_critical: ctx.boot_critical,
                                            });
                                        }
                                    }
                                }
                            }
                        }
                        "Lcom/android/server/SystemServiceManager;" if mname == "startService" => {
                            let phase = if ctx.method_sig.contains("Bootstrap") {
                                "bootstrap"
                            } else if ctx.method_sig.contains("Core") {
                                "core"
                            } else if ctx.method_sig.contains("Other") {
                                "other"
                            } else {
                                "runtime"
                            };
                            let guarded = is_guarded(pc, "service");
                            if mproto.starts_with("(Ljava/lang/Class;") {
                                if let Some(tcls) = arg_cls_at(1) {
                                    if ctx.logic.system_services.len() < 500 {
                                        let ok = lookup_class(ctx.own_classes, ctx.global_classes, tcls).is_some();
                                        ctx.logic.system_services.push(LogicCallSite {
                                            value: desc_to_dot_class(tcls),
                                            secondary: phase.into(),
                                            source: ctx.source.into(),
                                            caller: format!("{}.{}", ctx.class_name, ctx.method_sig),
                                            resolved: ok,
                                            blocking: true,
                                            boot_critical: true,
                                            guarded,
                                            dead_branch: is_dead,
                                        });
                                    }
                                }
                            } else if mproto.starts_with("(Ljava/lang/String;") {
                                if let Some(cls_str) = arg_str_at(1) {
                                    let desc = dot_class_to_desc(cls_str);
                                    let ok = lookup_class(ctx.own_classes, ctx.global_classes, &desc).is_some();
                                    let site = LogicCallSite {
                                        value: cls_str.to_string(),
                                        secondary: phase.into(),
                                        source: ctx.source.into(),
                                        caller: format!("{}.{}", ctx.class_name, ctx.method_sig),
                                        resolved: ok,
                                        blocking: true,
                                        boot_critical: true,
                                        guarded,
                                        dead_branch: is_dead,
                                    };
                                    if ctx.logic.system_services.len() < 500 {
                                        ctx.logic.system_services.push(site.clone());
                                    }
                                    if ctx.logic.reflection_classes.len() < 400 {
                                        let mut rsite = site;
                                        rsite.secondary = "SystemServiceManager.startService".into();
                                        rsite.guarded = is_guarded(pc, "reflection");
                                        ctx.logic.reflection_classes.push(rsite);
                                    }
                                }
                            }
                        }
                        "Ljava/lang/Class;" if mname == "forName" => {
                            if let Some(cls_str) = arg_str_at(0) {
                                if cls_str.contains('.') && !cls_str.contains(' ') {
                                    let desc = dot_class_to_desc(cls_str);
                                    pending_cls = Some(desc.clone());
                                    if ctx.logic.reflection_classes.len() < 400 {
                                        let ok = lookup_class(ctx.own_classes, ctx.global_classes, &desc).is_some();
                                        ctx.logic.reflection_classes.push(LogicCallSite {
                                            value: cls_str.to_string(),
                                            secondary: "Class.forName".into(),
                                            source: ctx.source.into(),
                                            caller: format!("{}.{}", ctx.class_name, ctx.method_sig),
                                            resolved: ok,
                                            blocking: false,
                                            boot_critical: ctx.boot_critical,
                                            guarded: is_guarded(pc, "reflection"),
                                            dead_branch: is_dead,
                                        });
                                    }
                                }
                            }
                        }
                        "Ljava/lang/Class;"
                            if matches!(
                                mname,
                                "getMethod" | "getDeclaredMethod" | "getField" | "getDeclaredField"
                            ) =>
                        {
                            if let (Some(cls_desc), Some(member_name)) = (arg_cls_at(0), arg_str_at(1)) {
                                if is_core_android_or_oem_class(cls_desc)
                                    && !member_name.is_empty()
                                    && !member_name.contains(' ')
                                    && ctx.logic.reflection_members.len() < 400
                                {
                                    let is_method = mname == "getMethod" || mname == "getDeclaredMethod";
                                    if let Some(found) = has_member_name(
                                        ctx.own_classes,
                                        ctx.global_classes,
                                        cls_desc,
                                        member_name,
                                        is_method,
                                    ) {
                                        ctx.logic.reflection_members.push(LogicCallSite {
                                            value: format!("{}->{member_name}", desc_to_dot_class(cls_desc)),
                                            secondary: mname.into(),
                                            source: ctx.source.into(),
                                            caller: format!("{}.{}", ctx.class_name, ctx.method_sig),
                                            resolved: found,
                                            blocking: false,
                                            boot_critical: ctx.boot_critical,
                                            guarded: is_guarded(pc, "reflection"),
                                            dead_branch: is_dead,
                                        });
                                    }
                                }
                            }
                        }
                        "Ljava/lang/ClassLoader;" if mname == "loadClass" => {
                            if let Some(cls_str) = arg_str_at(1) {
                                if cls_str.contains('.') && !cls_str.contains(' ') {
                                    let desc = dot_class_to_desc(cls_str);
                                    pending_cls = Some(desc.clone());
                                    if ctx.logic.reflection_classes.len() < 400 {
                                        let ok = lookup_class(ctx.own_classes, ctx.global_classes, &desc).is_some();
                                        ctx.logic.reflection_classes.push(LogicCallSite {
                                            value: cls_str.to_string(),
                                            secondary: "ClassLoader.loadClass".into(),
                                            source: ctx.source.into(),
                                            caller: format!("{}.{}", ctx.class_name, ctx.method_sig),
                                            resolved: ok,
                                            blocking: false,
                                            boot_critical: ctx.boot_critical,
                                            guarded: is_guarded(pc, "reflection"),
                                            dead_branch: is_dead,
                                        });
                                    }
                                }
                            }
                        }
                        "Landroid/os/ServiceManager;"
                            if matches!(
                                mname,
                                "waitForDeclaredService"
                                    | "waitForService"
                                    | "getServiceOrThrow"
                                    | "getService"
                                    | "checkService"
                                    | "addService"
                            ) =>
                        {
                            if let Some(svc) = arg_str_at(0) {
                                if ctx.logic.binder_services.len() < 500 {
                                    let blocking = matches!(
                                        mname,
                                        "waitForDeclaredService" | "waitForService" | "getServiceOrThrow"
                                    );
                                    ctx.logic.binder_services.push(LogicCallSite {
                                        value: svc.to_string(),
                                        secondary: mname.into(),
                                        source: ctx.source.into(),
                                        caller: format!("{}.{}", ctx.class_name, ctx.method_sig),
                                        resolved: true,
                                        blocking,
                                        boot_critical: ctx.boot_critical,
                                        guarded: is_guarded(pc, "service"),
                                        dead_branch: is_dead,
                                    });
                                }
                            }
                        }
                        "Landroid/os/SystemProperties;"
                            if matches!(mname, "get" | "getBoolean" | "getInt" | "getLong") =>
                        {
                            if let Some(prop) = arg_str_at(0) {
                                if ctx.logic.system_properties.len() < 1200
                                    && (prop.starts_with("ro.")
                                        || prop.starts_with("persist.")
                                        || prop.starts_with("sys.")
                                        || prop.starts_with("vendor.")
                                        || prop.starts_with("debug.")
                                        || prop.starts_with("dalvik.")
                                        || prop.starts_with("service."))
                                {
                                    ctx.logic.system_properties.insert(prop.to_string());
                                }
                                if mname == "getBoolean" {
                                    if let Some(props) = ctx.rom_props {
                                        if props.contains_key(prop) {
                                            pending_bool = Some(eval_sysprop_bool(props, prop, arg_bool_at(1).unwrap_or(false)));
                                        } else if let Some(def_b) = arg_bool_at(1) {
                                            // Only evaluate default when key is a static ro.* / persist.sys.* prop
                                            if prop.starts_with("ro.") {
                                                pending_bool = Some(def_b);
                                            }
                                        }
                                    }
                                } else if mname == "get" {
                                    if let Some(props) = ctx.rom_props {
                                        if let Some(v) = props.get(prop) {
                                            if !v.is_empty() && v.len() <= 128 {
                                                pending_str = Some(v.clone());
                                            }
                                        } else if prop.starts_with("ro.") {
                                            if let Some(def_s) = arg_str_at(1) {
                                                if def_s.len() <= 128 {
                                                    pending_str = Some(def_s.to_string());
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                        "Landroid/content/ComponentName;"
                            if mname == "<init>" && mproto == "(Ljava/lang/String;Ljava/lang/String;)V" =>
                        {
                            if let (Some(pkg), Some(cls)) = (arg_str_at(1), arg_str_at(2)) {
                                if pkg.contains('.') && !pkg.contains(' ') && ctx.logic.component_targets.len() < 400 {
                                    ctx.logic.component_targets.push(LogicCallSite {
                                        value: format!("{pkg}/{cls}"),
                                        secondary: pkg.to_string(),
                                        source: ctx.source.into(),
                                        caller: format!("{}.{}", ctx.class_name, ctx.method_sig),
                                        resolved: true,
                                        blocking: false,
                                        boot_critical: ctx.boot_critical,
                                        guarded: is_guarded(pc, "service"),
                                        dead_branch: is_dead,
                                    });
                                }
                            }
                        }
                        _ => {}
                    }

                    if is_core_android_or_oem_class(owner) {
                        match resolve_method(ctx.own_classes, ctx.global_classes, owner, mname, mproto) {
                            ResolveResult::Found(flags) => {
                                let is_stat = (flags & ACC_STATIC) != 0;
                                if kind == InvokeKind::Static && !is_stat && ctx.incompat.len() < 80 {
                                    ctx.incompat.push(DexIssue {
                                        kind: "IncompatibleClassChangeError".into(),
                                        source: ctx.source.into(),
                                        class_name: ctx.class_name.into(),
                                        method: ctx.method_sig.into(),
                                        target: format!("{owner}->{mname}{mproto}"),
                                        pc,
                                        detail: "invoke-static targets a non-static method".into(),
                                        boot_critical: ctx.boot_critical,
                                    });
                                } else if kind != InvokeKind::Static && is_stat && ctx.incompat.len() < 80 {
                                    ctx.incompat.push(DexIssue {
                                        kind: "IncompatibleClassChangeError".into(),
                                        source: ctx.source.into(),
                                        class_name: ctx.class_name.into(),
                                        method: ctx.method_sig.into(),
                                        target: format!("{owner}->{mname}{mproto}"),
                                        pc,
                                        detail: "non-static invoke targets a static method".into(),
                                        boot_critical: ctx.boot_critical,
                                    });
                                }
                            }
                            ResolveResult::MissingResolvedChain => {
                                if ctx.missing_methods.len() < 150 {
                                    ctx.missing_methods.push(DexIssue {
                                        kind: "NoSuchMethodError".into(),
                                        source: ctx.source.into(),
                                        class_name: ctx.class_name.into(),
                                        method: ctx.method_sig.into(),
                                        target: format!("{owner}->{mname}{mproto}"),
                                        pc,
                                        detail: format!(
                                            "method {mname}{mproto} missing on resolved class {owner}"
                                        ),
                                        boot_critical: ctx.boot_critical,
                                    });
                                }
                            }
                            ResolveResult::UnresolvedHierarchy => {
                                if !owner.starts_with('[')
                                    && lookup_class(ctx.own_classes, ctx.global_classes, owner).is_none()
                                {
                                    ctx.missing_classes
                                        .entry(owner.to_string())
                                        .or_default()
                                        .push(format!("{}.{}", ctx.class_name, ctx.method_sig));
                                }
                            }
                        }
                    }
                }
            }
            0x7b..=0x8f | 0xb0..=0xcf | 0xd0..=0xe2 => {
                check_reg(a4, pc, ctx.verify_errors);
                if a4 < track_regs {
                    reg_null[a4] = false;
                }
            }
            0x2d..=0x31 | 0x90..=0xaf => {
                check_reg(aa, pc, ctx.verify_errors);
                if aa < track_regs {
                    reg_null[aa] = false;
                }
            }
            _ => {}
        }
    }
}

// ── Compact resources.arsc parser ──

#[derive(Clone, Default)]
pub struct ArscPackage {
    pub package_id: u32,
    pub package_name: String,
    pub res_ids: HashSet<u32>,
    pub res_names: HashSet<String>,
}

pub fn parse_arsc_packages(data: &[u8]) -> Vec<ArscPackage> {
    let mut out = Vec::new();
    if data.len() < 12 || r_u16(data, 0) != Some(RES_TABLE_TYPE) {
        return out;
    }
    let total_size = (r_u32(data, 4).unwrap_or(0) as usize).min(data.len());
    let mut pos = r_u16(data, 2).unwrap_or(12) as usize;

    while pos + 8 <= total_size {
        let Some(ctype) = r_u16(data, pos) else { break };
        let hdr_sz = r_u16(data, pos + 2).unwrap_or(0) as usize;
        let csize = r_u32(data, pos + 4).unwrap_or(0) as usize;
        if csize < 8 || pos + csize > total_size {
            break;
        }
        if ctype == RES_TABLE_PACKAGE_TYPE && hdr_sz >= 284 && csize >= hdr_sz {
            let chunk = &data[pos..pos + csize];
            let pkg_id = r_u32(chunk, 8).unwrap_or(0);
            let mut name_u16 = Vec::with_capacity(64);
            for i in 0..128 {
                let u = r_u16(chunk, 12 + i * 2).unwrap_or(0);
                if u == 0 {
                    break;
                }
                name_u16.push(u);
            }
            let pkg_name = String::from_utf16_lossy(&name_u16);
            let type_str_off = r_u32(chunk, 268).unwrap_or(0) as usize;
            let key_str_off = r_u32(chunk, 276).unwrap_or(0) as usize;

            let type_strings = if type_str_off > 0 && type_str_off + 8 <= chunk.len() {
                let sz = r_u32(chunk, type_str_off + 4).unwrap_or(0) as usize;
                if sz >= 28 && type_str_off + sz <= chunk.len() {
                    parse_string_pool(&chunk[type_str_off..type_str_off + sz]).unwrap_or_default()
                } else {
                    Vec::new()
                }
            } else {
                Vec::new()
            };
            let key_strings = if key_str_off > 0 && key_str_off + 8 <= chunk.len() {
                let sz = r_u32(chunk, key_str_off + 4).unwrap_or(0) as usize;
                if sz >= 28 && key_str_off + sz <= chunk.len() {
                    parse_string_pool(&chunk[key_str_off..key_str_off + sz]).unwrap_or_default()
                } else {
                    Vec::new()
                }
            } else {
                Vec::new()
            };

            let mut res_ids = HashSet::new();
            let mut res_names = HashSet::new();

            let mut sub = hdr_sz;
            while sub + 8 <= chunk.len() {
                let Some(st) = r_u16(chunk, sub) else { break };
                let sh = r_u16(chunk, sub + 2).unwrap_or(0) as usize;
                let sc = r_u32(chunk, sub + 4).unwrap_or(0) as usize;
                if sc < 8 || sub + sc > chunk.len() {
                    break;
                }
                if st == RES_TABLE_TYPE_TYPE && sh >= 20 && sc >= sh {
                    let tc = &chunk[sub..sub + sc];
                    let type_id = tc[8] as u32;
                    let flags = tc[9];
                    let entry_count = r_u32(tc, 12).unwrap_or(0) as usize;
                    let entries_start = r_u32(tc, 16).unwrap_or(0) as usize;
                    let type_name = type_id
                        .checked_sub(1)
                        .and_then(|i| type_strings.get(i as usize))
                        .map(|s| s.as_str())
                        .unwrap_or("");
                    let is_sparse = (flags & 0x01) != 0;
                    let is_offset16 = (flags & 0x02) != 0;

                    if entries_start <= tc.len() {
                        if is_sparse {
                            for i in 0..entry_count {
                                let p = sh + i * 4;
                                if p + 4 > entries_start {
                                    break;
                                }
                                let idx = r_u16(tc, p).unwrap_or(0) as u32;
                                let off16 = r_u16(tc, p + 2).unwrap_or(0xffff);
                                if off16 == 0xffff {
                                    continue;
                                }
                                let e_off = entries_start + (off16 as usize) * 4;
                                res_ids.insert((pkg_id << 24) | (type_id << 16) | idx);
                                if let Some(key_idx) = r_u32(tc, e_off + 4) {
                                    if let Some(kname) = key_strings.get(key_idx as usize) {
                                        if !type_name.is_empty() && !kname.is_empty() {
                                            res_names.insert(format!("{type_name}/{kname}"));
                                        }
                                    }
                                }
                            }
                        } else if is_offset16 {
                            for i in 0..entry_count {
                                let p = sh + i * 2;
                                if p + 2 > entries_start {
                                    break;
                                }
                                let off16 = r_u16(tc, p).unwrap_or(0xffff);
                                if off16 == 0xffff {
                                    continue;
                                }
                                let e_off = entries_start + (off16 as usize) * 4;
                                res_ids.insert((pkg_id << 24) | (type_id << 16) | (i as u32));
                                if e_off + 8 <= tc.len() {
                                    let hdr_flags = r_u16(tc, e_off + 2).unwrap_or(0);
                                    let key_idx = if (hdr_flags & 0x0008) != 0 {
                                        r_u16(tc, e_off).unwrap_or(0) as usize
                                    } else {
                                        r_u32(tc, e_off + 4).unwrap_or(0) as usize
                                    };
                                    if let Some(kname) = key_strings.get(key_idx) {
                                        if !type_name.is_empty() && !kname.is_empty() {
                                            res_names.insert(format!("{type_name}/{kname}"));
                                        }
                                    }
                                }
                            }
                        } else {
                            for i in 0..entry_count {
                                let p = sh + i * 4;
                                if p + 4 > entries_start {
                                    break;
                                }
                                let off32 = r_u32(tc, p).unwrap_or(0xffff_ffff);
                                if off32 == 0xffff_ffff {
                                    continue;
                                }
                                let e_off = entries_start + off32 as usize;
                                res_ids.insert((pkg_id << 24) | (type_id << 16) | (i as u32));
                                if e_off + 8 <= tc.len() {
                                    let hdr_flags = r_u16(tc, e_off + 2).unwrap_or(0);
                                    let key_idx = if (hdr_flags & 0x0008) != 0 {
                                        r_u16(tc, e_off).unwrap_or(0) as usize
                                    } else {
                                        r_u32(tc, e_off + 4).unwrap_or(0) as usize
                                    };
                                    if let Some(kname) = key_strings.get(key_idx) {
                                        if !type_name.is_empty() && !kname.is_empty() {
                                            res_names.insert(format!("{type_name}/{kname}"));
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                sub += sc;
            }

            out.push(ArscPackage {
                package_id: pkg_id,
                package_name: pkg_name,
                res_ids,
                res_names,
            });
        }
        pos += csize;
    }
    out
}

fn read_zip_member<R: Read + Seek>(reader: R, member: &str) -> Option<Vec<u8>> {
    let mut arc = ZipArchive::new(reader).ok()?;
    let mut ent = arc.by_name(member).ok()?;
    let mut buf = Vec::with_capacity((ent.size() as usize).min(64 * 1024 * 1024));
    ent.read_to_end(&mut buf).ok()?;
    Some(buf)
}

fn parse_overlay_apk<R: Read + Seek>(
    reader: R,
) -> (Option<crate::apk::ManifestParsed>, Vec<ArscPackage>) {
    let Ok(mut arc) = ZipArchive::new(reader) else {
        return (None, Vec::new());
    };
    let manifest = arc.by_name("AndroidManifest.xml").ok().and_then(|mut ent| {
        let mut buf = Vec::with_capacity((ent.size() as usize).min(8 * 1024 * 1024));
        ent.read_to_end(&mut buf).ok()?;
        parse_axml_manifest(&buf)
    });
    let arsc = arc
        .by_name("resources.arsc")
        .ok()
        .and_then(|mut ent| {
            let mut buf = Vec::with_capacity((ent.size() as usize).min(32 * 1024 * 1024));
            ent.read_to_end(&mut buf).ok()?;
            Some(parse_arsc_packages(&buf))
        })
        .unwrap_or_default();
    (manifest, arsc)
}

// ── Stateful NativeDexVerifier PyO3 class ──

struct TargetEntry {
    label: String,
    is_core_target: bool,
    dex_buffers: Vec<Vec<u8>>,
    own_classes: HashMap<String, ClassInfo>,
}

struct ParsedOverlayRecord {
    apk_path: String,
    package: String,
    target_package: String,
    is_static: bool,
    priority: i32,
    required_prop: String,
    corrupt: bool,
    res_names: HashSet<String>,
}

#[pyclass]
pub struct NativeDexVerifier {
    classes: HashMap<String, ClassInfo>,
    provider_jars: usize,
    vdex_extracted: usize,
    targets: Vec<TargetEntry>,
    arsc_targets: HashMap<String, ArscPackage>,
    overlays: Vec<ParsedOverlayRecord>,
}

impl NativeDexVerifier {
    fn absorb_indexed(&mut self, map: HashMap<String, ClassInfo>) {
        for (k, v) in map {
            self.classes
                .entry(k)
                .and_modify(|existing| existing.merge_from(v.clone()))
                .or_insert(v);
        }
    }

    fn push_target(
        &mut self,
        label: String,
        is_core_target: bool,
        used_vdex: bool,
        dexes: Vec<Vec<u8>>,
        maps: Vec<HashMap<String, ClassInfo>>,
    ) -> usize {
        if used_vdex {
            self.vdex_extracted += 1;
        }
        let leak_to_global = !label.to_ascii_lowercase().ends_with(".apk");
        let mut own = HashMap::new();
        let mut added = 0;
        for m in maps {
            added += m.len();
            for (k, v) in m {
                if leak_to_global {
                    own.entry(k.clone())
                        .and_modify(|e: &mut ClassInfo| e.merge_from(v.clone()))
                        .or_insert_with(|| v.clone());
                    self.classes
                        .entry(k)
                        .and_modify(|e| e.merge_from(v.clone()))
                        .or_insert(v);
                } else {
                    own.entry(k)
                        .and_modify(|e: &mut ClassInfo| e.merge_from(v.clone()))
                        .or_insert(v);
                }
            }
        }
        self.targets.push(TargetEntry {
            label,
            is_core_target,
            dex_buffers: dexes,
            own_classes: own,
        });
        added
    }

    fn absorb_overlay_parsed(
        &mut self,
        apk_path: String,
        manifest: Option<crate::apk::ManifestParsed>,
        arsc: Vec<ArscPackage>,
    ) {
        let Some(m) = manifest else {
            self.overlays.push(ParsedOverlayRecord {
                apk_path,
                package: String::new(),
                target_package: String::new(),
                is_static: false,
                priority: 0,
                required_prop: String::new(),
                corrupt: true,
                res_names: HashSet::new(),
            });
            return;
        };
        if m.overlay_target.is_empty() {
            return;
        }
        let mut res_names = HashSet::new();
        for p in arsc {
            res_names.extend(p.res_names);
        }
        self.overlays.push(ParsedOverlayRecord {
            apk_path,
            package: m.package,
            target_package: m.overlay_target,
            is_static: m.overlay_is_static,
            priority: m.overlay_priority,
            required_prop: m.overlay_required_prop_name,
            corrupt: false,
            res_names,
        });
    }
}

#[pymethods]
impl NativeDexVerifier {
    #[new]
    pub fn new() -> Self {
        Self {
            classes: HashMap::new(),
            provider_jars: 0,
            vdex_extracted: 0,
            targets: Vec::new(),
            arsc_targets: HashMap::new(),
            overlays: Vec::new(),
        }
    }

    pub fn add_provider_bytes(&mut self, py: Python<'_>, _label: String, data: &[u8]) -> usize {
        let owned = data.to_vec();
        let (dexes, used_vdex) = py.detach(move || extract_dex_buffers(&owned));
        if dexes.is_empty() {
            return 0;
        }
        self.provider_jars += 1;
        if used_vdex {
            self.vdex_extracted += 1;
        }
        let maps: Vec<HashMap<String, ClassInfo>> = py.detach(move || {
            dexes.par_iter().map(|buf| index_dex_classes(buf)).collect()
        });
        let mut added = 0;
        for m in maps {
            added += m.len();
            self.absorb_indexed(m);
        }
        added
    }

    pub fn add_provider_files(
        &mut self,
        py: Python<'_>,
        items: Vec<(String, PathBuf)>,
    ) -> Vec<String> {
        let results: Vec<(String, bool, Vec<HashMap<String, ClassInfo>>)> = py.detach(move || {
            items
                .par_iter()
                .map(|(label, path)| {
                    let Ok(raw) = fs::read(path) else {
                        return (label.clone(), false, Vec::new());
                    };
                    let (dexes, used_vdex) = extract_dex_buffers(&raw);
                    if dexes.is_empty() {
                        return (label.clone(), false, Vec::new());
                    }
                    let maps: Vec<HashMap<String, ClassInfo>> =
                        dexes.par_iter().map(|buf| index_dex_classes(buf)).collect();
                    (label.clone(), used_vdex, maps)
                })
                .collect()
        });
        let mut empty_labels = Vec::new();
        for (label, used_vdex, maps) in results {
            if maps.is_empty() {
                empty_labels.push(label);
                continue;
            }
            self.provider_jars += 1;
            if used_vdex {
                self.vdex_extracted += 1;
            }
            for m in maps {
                self.absorb_indexed(m);
            }
        }
        empty_labels
    }

    pub fn add_provider_erofs(
        &mut self,
        py: Python<'_>,
        reader: &NativeErofsReader,
        items: Vec<(String, String)>,
    ) -> PyResult<Vec<String>> {
        let img = reader.get_image()?;
        let results: Vec<(String, bool, Vec<HashMap<String, ClassInfo>>)> = py.detach(move || {
            items
                .par_iter()
                .map(|(label, rel)| {
                    let Ok((raw, _node)) = img.read_path(rel) else {
                        return (label.clone(), false, Vec::new());
                    };
                    let (dexes, used_vdex) = extract_dex_buffers(&raw);
                    if dexes.is_empty() {
                        return (label.clone(), false, Vec::new());
                    }
                    let maps: Vec<HashMap<String, ClassInfo>> =
                        dexes.par_iter().map(|buf| index_dex_classes(buf)).collect();
                    (label.clone(), used_vdex, maps)
                })
                .collect()
        });
        let mut empty_labels = Vec::new();
        for (label, used_vdex, maps) in results {
            if maps.is_empty() {
                empty_labels.push(label);
                continue;
            }
            self.provider_jars += 1;
            if used_vdex {
                self.vdex_extracted += 1;
            }
            for m in maps {
                self.absorb_indexed(m);
            }
        }
        Ok(empty_labels)
    }

    pub fn add_provider_ext(
        &mut self,
        py: Python<'_>,
        reader: &NativeExtReader,
        items: Vec<(String, String)>,
    ) -> PyResult<Vec<String>> {
        let mut raw_items = Vec::with_capacity(items.len());
        let mut empty_labels = Vec::new();
        reader.with_vol(|vol| {
            for (label, rel) in &items {
                if let Ok((raw, _)) = vol.read_path(rel) {
                    raw_items.push((label.clone(), raw));
                } else {
                    empty_labels.push(label.clone());
                }
            }
            Ok(())
        })?;
        let results: Vec<(String, bool, Vec<HashMap<String, ClassInfo>>)> = py.detach(move || {
            raw_items
                .par_iter()
                .map(|(label, raw)| {
                    let (dexes, used_vdex) = extract_dex_buffers(raw);
                    if dexes.is_empty() {
                        return (label.clone(), false, Vec::new());
                    }
                    let maps: Vec<HashMap<String, ClassInfo>> =
                        dexes.par_iter().map(|buf| index_dex_classes(buf)).collect();
                    (label.clone(), used_vdex, maps)
                })
                .collect()
        });
        for (label, used_vdex, maps) in results {
            if maps.is_empty() {
                empty_labels.push(label);
                continue;
            }
            self.provider_jars += 1;
            if used_vdex {
                self.vdex_extracted += 1;
            }
            for m in maps {
                self.absorb_indexed(m);
            }
        }
        Ok(empty_labels)
    }

    #[pyo3(signature = (label, data, is_core_target=false))]
    pub fn add_target_bytes(
        &mut self,
        py: Python<'_>,
        label: String,
        data: &[u8],
        is_core_target: bool,
    ) -> usize {
        let owned = data.to_vec();
        let (dexes, used_vdex) = py.detach(move || extract_dex_buffers(&owned));
        if dexes.is_empty() {
            return 0;
        }
        let maps: Vec<HashMap<String, ClassInfo>> = py.detach(|| {
            dexes.par_iter().map(|buf| index_dex_classes(buf)).collect()
        });
        self.push_target(label, is_core_target, used_vdex, dexes, maps)
    }

    pub fn add_target_files(
        &mut self,
        py: Python<'_>,
        items: Vec<(String, PathBuf, bool)>,
    ) -> (Vec<String>, Vec<(String, bool)>) {
        let loaded: Vec<(String, bool, bool, Vec<Vec<u8>>, Vec<HashMap<String, ClassInfo>>)> =
            py.detach(move || {
                items
                    .par_iter()
                    .map(|(label, path, is_core)| {
                        let Ok(raw) = fs::read(path) else {
                            return (label.clone(), *is_core, false, Vec::new(), Vec::new());
                        };
                        let (dexes, used_vdex) = extract_dex_buffers(&raw);
                        if dexes.is_empty() {
                            return (label.clone(), *is_core, false, Vec::new(), Vec::new());
                        }
                        let maps: Vec<HashMap<String, ClassInfo>> =
                            dexes.par_iter().map(|buf| index_dex_classes(buf)).collect();
                        (label.clone(), *is_core, used_vdex, dexes, maps)
                    })
                    .collect()
            });
        let mut ok_labels = Vec::new();
        let mut missing = Vec::new();
        for (label, is_core, used_vdex, dexes, maps) in loaded {
            if dexes.is_empty() {
                missing.push((label, is_core));
                continue;
            }
            ok_labels.push(label.clone());
            self.push_target(label, is_core, used_vdex, dexes, maps);
        }
        (ok_labels, missing)
    }

    pub fn add_target_erofs(
        &mut self,
        py: Python<'_>,
        reader: &NativeErofsReader,
        items: Vec<(String, String, bool)>,
    ) -> PyResult<(Vec<String>, Vec<(String, bool)>)> {
        let img = reader.get_image()?;
        let loaded: Vec<(String, bool, bool, Vec<Vec<u8>>, Vec<HashMap<String, ClassInfo>>)> =
            py.detach(move || {
                items
                    .par_iter()
                    .map(|(label, rel, is_core)| {
                        let Ok((raw, _)) = img.read_path(rel) else {
                            return (label.clone(), *is_core, false, Vec::new(), Vec::new());
                        };
                        let (dexes, used_vdex) = extract_dex_buffers(&raw);
                        if dexes.is_empty() {
                            return (label.clone(), *is_core, false, Vec::new(), Vec::new());
                        }
                        let maps: Vec<HashMap<String, ClassInfo>> =
                            dexes.par_iter().map(|buf| index_dex_classes(buf)).collect();
                        (label.clone(), *is_core, used_vdex, dexes, maps)
                    })
                    .collect()
            });
        let mut ok_labels = Vec::new();
        let mut missing = Vec::new();
        for (label, is_core, used_vdex, dexes, maps) in loaded {
            if dexes.is_empty() {
                missing.push((label, is_core));
                continue;
            }
            ok_labels.push(label.clone());
            self.push_target(label, is_core, used_vdex, dexes, maps);
        }
        Ok((ok_labels, missing))
    }

    pub fn add_target_ext(
        &mut self,
        py: Python<'_>,
        reader: &NativeExtReader,
        items: Vec<(String, String, bool)>,
    ) -> PyResult<(Vec<String>, Vec<(String, bool)>)> {
        let mut raw_items = Vec::with_capacity(items.len());
        let mut missing = Vec::new();
        reader.with_vol(|vol| {
            for (label, rel, is_core) in &items {
                if let Ok((raw, _)) = vol.read_path(rel) {
                    raw_items.push((label.clone(), raw, *is_core));
                } else {
                    missing.push((label.clone(), *is_core));
                }
            }
            Ok(())
        })?;
        let loaded: Vec<(String, bool, bool, Vec<Vec<u8>>, Vec<HashMap<String, ClassInfo>>)> =
            py.detach(move || {
                raw_items
                    .par_iter()
                    .map(|(label, raw, is_core)| {
                        let (dexes, used_vdex) = extract_dex_buffers(raw);
                        if dexes.is_empty() {
                            return (label.clone(), *is_core, false, Vec::new(), Vec::new());
                        }
                        let maps: Vec<HashMap<String, ClassInfo>> =
                            dexes.par_iter().map(|buf| index_dex_classes(buf)).collect();
                        (label.clone(), *is_core, used_vdex, dexes, maps)
                    })
                    .collect()
            });
        let mut ok_labels = Vec::new();
        for (label, is_core, used_vdex, dexes, maps) in loaded {
            if dexes.is_empty() {
                missing.push((label, is_core));
                continue;
            }
            ok_labels.push(label.clone());
            self.push_target(label, is_core, used_vdex, dexes, maps);
        }
        Ok((ok_labels, missing))
    }

    pub fn add_arsc_target_files(
        &mut self,
        py: Python<'_>,
        items: Vec<(String, PathBuf)>,
    ) -> usize {
        let parsed: Vec<(String, Vec<ArscPackage>)> = py.detach(move || {
            items
                .par_iter()
                .filter_map(|(pkg, path)| {
                    let f = File::open(path).ok()?;
                    let arsc = read_zip_member(f, "resources.arsc")?;
                    Some((pkg.clone(), parse_arsc_packages(&arsc)))
                })
                .collect()
        });
        let mut cnt = 0;
        for (pkg, list) in parsed {
            for p in list {
                let key = if pkg.is_empty() { p.package_name.clone() } else { pkg.clone() };
                cnt += p.res_names.len();
                let entry = self.arsc_targets.entry(key).or_default();
                entry.package_id = p.package_id;
                if entry.package_name.is_empty() {
                    entry.package_name = p.package_name;
                }
                entry.res_ids.extend(p.res_ids);
                entry.res_names.extend(p.res_names);
            }
        }
        cnt
    }

    pub fn add_arsc_target_erofs(
        &mut self,
        py: Python<'_>,
        reader: &NativeErofsReader,
        items: Vec<(String, String)>,
    ) -> PyResult<usize> {
        let img = reader.get_image()?;
        let parsed: Vec<(String, Vec<ArscPackage>)> = py.detach(move || {
            items
                .par_iter()
                .filter_map(|(pkg, rel)| {
                    let rdr = img.open_path_reader(rel).ok()?;
                    let arsc = read_zip_member(rdr, "resources.arsc")?;
                    Some((pkg.clone(), parse_arsc_packages(&arsc)))
                })
                .collect()
        });
        let mut cnt = 0;
        for (pkg, list) in parsed {
            for p in list {
                let key = if pkg.is_empty() { p.package_name.clone() } else { pkg.clone() };
                cnt += p.res_names.len();
                let entry = self.arsc_targets.entry(key).or_default();
                entry.package_id = p.package_id;
                if entry.package_name.is_empty() {
                    entry.package_name = p.package_name;
                }
                entry.res_ids.extend(p.res_ids);
                entry.res_names.extend(p.res_names);
            }
        }
        Ok(cnt)
    }

    pub fn add_arsc_target_ext(
        &mut self,
        py: Python<'_>,
        reader: &NativeExtReader,
        items: Vec<(String, String)>,
    ) -> PyResult<usize> {
        let arsc_blobs: Vec<(String, Vec<u8>)> = py.detach(|| {
            reader.with_vol(|vol| {
                let mut out = Vec::with_capacity(items.len());
                for (pkg, rel) in &items {
                    if let Ok(rdr) = vol.open_path_reader(rel.trim_start_matches('/')) {
                        if let Some(arsc) = read_zip_member(rdr, "resources.arsc") {
                            out.push((pkg.clone(), arsc));
                        }
                    }
                }
                Ok(out)
            })
        })?;
        let parsed: Vec<(String, Vec<ArscPackage>)> = py.detach(move || {
            arsc_blobs
                .par_iter()
                .map(|(pkg, arsc)| (pkg.clone(), parse_arsc_packages(arsc)))
                .collect()
        });
        let mut cnt = 0;
        for (pkg, list) in parsed {
            for p in list {
                let key = if pkg.is_empty() { p.package_name.clone() } else { pkg.clone() };
                cnt += p.res_names.len();
                let entry = self.arsc_targets.entry(key).or_default();
                entry.package_id = p.package_id;
                if entry.package_name.is_empty() {
                    entry.package_name = p.package_name;
                }
                entry.res_ids.extend(p.res_ids);
                entry.res_names.extend(p.res_names);
            }
        }
        Ok(cnt)
    }

    pub fn add_overlays_files(
        &mut self,
        py: Python<'_>,
        items: Vec<(String, PathBuf)>,
    ) -> usize {
        let parsed: Vec<(String, Option<crate::apk::ManifestParsed>, Vec<ArscPackage>)> =
            py.detach(move || {
                items
                    .par_iter()
                    .map(|(vpath, path)| {
                        let Ok(f) = File::open(path) else {
                            return (vpath.clone(), None, Vec::new());
                        };
                        let (m, a) = parse_overlay_apk(f);
                        (vpath.clone(), m, a)
                    })
                    .collect()
            });
        let n = parsed.len();
        for (vp, m, a) in parsed {
            self.absorb_overlay_parsed(vp, m, a);
        }
        n
    }

    pub fn add_overlays_erofs(
        &mut self,
        py: Python<'_>,
        reader: &NativeErofsReader,
        items: Vec<(String, String)>,
    ) -> PyResult<usize> {
        let img = reader.get_image()?;
        let parsed: Vec<(String, Option<crate::apk::ManifestParsed>, Vec<ArscPackage>)> =
            py.detach(move || {
                items
                    .par_iter()
                    .map(|(vpath, rel)| {
                        let Ok(rdr) = img.open_path_reader(rel) else {
                            return (vpath.clone(), None, Vec::new());
                        };
                        let (m, a) = parse_overlay_apk(rdr);
                        (vpath.clone(), m, a)
                    })
                    .collect()
            });
        let n = parsed.len();
        for (vp, m, a) in parsed {
            self.absorb_overlay_parsed(vp, m, a);
        }
        Ok(n)
    }

    pub fn add_overlays_ext(
        &mut self,
        py: Python<'_>,
        reader: &NativeExtReader,
        items: Vec<(String, String)>,
    ) -> PyResult<usize> {
        let parsed: Vec<(String, Option<crate::apk::ManifestParsed>, Vec<ArscPackage>)> =
            py.detach(|| {
                reader.with_vol(|vol| {
                    let mut out = Vec::with_capacity(items.len());
                    for (vpath, rel) in &items {
                        let (m, a) = match vol.open_path_reader(rel.trim_start_matches('/')) {
                            Ok(rdr) => parse_overlay_apk(rdr),
                            Err(_) => (None, Vec::new()),
                        };
                        out.push((vpath.clone(), m, a));
                    }
                    Ok(out)
                })
            })?;
        let n = parsed.len();
        for (vp, m, a) in parsed {
            self.absorb_overlay_parsed(vp, m, a);
        }
        Ok(n)
    }

    #[pyo3(signature = (installed_packages=None, enabled_features=None, rom_props=None))]
    pub fn verify_json(
        &self,
        py: Python<'_>,
        installed_packages: Option<Vec<String>>,
        enabled_features: Option<Vec<String>>,
        rom_props: Option<HashMap<String, String>>,
    ) -> PyResult<String> {
        let installed_set: HashSet<String> = installed_packages
            .unwrap_or_default()
            .into_iter()
            .collect();
        let feature_set_opt: Option<HashSet<String>> = enabled_features.and_then(|v| {
            if v.is_empty() {
                None
            } else {
                Some(v.into_iter().collect())
            }
        });
        let props_opt: Option<HashMap<String, String>> = rom_props.and_then(|m| {
            if m.is_empty() {
                None
            } else {
                Some(m)
            }
        });

        let report = py.detach(|| {
            let t0 = Instant::now();
            let fw_res_ids = self.arsc_targets.get("android").map(|p| &p.res_ids);
            let enabled_features_ref = feature_set_opt.as_ref();
            let rom_props_ref = props_opt.as_ref();

            let work_units: Vec<(&TargetEntry, &[u8])> = self
                .targets
                .iter()
                .flat_map(|t| t.dex_buffers.iter().map(move |buf| (t, buf.as_slice())))
                .collect();

            struct UnitResult {
                classes: usize,
                methods: usize,
                insns: usize,
                native_methods: usize,
                verify_errors: Vec<DexIssue>,
                null_derefs: Vec<DexIssue>,
                missing_supers: Vec<DexIssue>,
                missing_methods: Vec<DexIssue>,
                missing_fields: Vec<DexIssue>,
                incompat: Vec<DexIssue>,
                missing_classes: HashMap<String, Vec<String>>,
                missing_res: Vec<DexIssue>,
                logic: UnitLogicAcc,
            }

            let unit_results: Vec<UnitResult> = work_units
                .par_iter()
                .map(|&(target_entry, dex_bytes)| {
                    let label = target_entry.label.as_str();
                    let own_classes = &target_entry.own_classes;
                    let mut res = UnitResult {
                        classes: 0,
                        methods: 0,
                        insns: 0,
                        native_methods: 0,
                        verify_errors: Vec::new(),
                        null_derefs: Vec::new(),
                        missing_supers: Vec::new(),
                        missing_methods: Vec::new(),
                        missing_fields: Vec::new(),
                        incompat: Vec::new(),
                        missing_classes: HashMap::new(),
                        missing_res: Vec::new(),
                        logic: UnitLogicAcc::default(),
                    };
                    let Some(tables) = DexTables::parse(dex_bytes) else {
                        return res;
                    };
                    for i in 0..tables.class_defs_size {
                        let base = tables.class_defs_off + i * 32;
                        let Some(cls_idx) = r_u32(tables.data, base).map(|v| v as usize) else {
                            continue;
                        };
                        let cls_desc = tables.type_name(cls_idx);
                        if cls_desc.is_empty() {
                            continue;
                        }
                        res.classes += 1;
                        let access_flags = r_u32(tables.data, base + 4).unwrap_or(0);
                        let super_idx = r_u32(tables.data, base + 8).unwrap_or(0xffff_ffff);
                        let ifaces_off = r_u32(tables.data, base + 12).unwrap_or(0) as usize;
                        let class_data_off = r_u32(tables.data, base + 24).unwrap_or(0) as usize;
                        let boot_crit = is_boot_critical_class(cls_desc);

                        // 1. Check superclass & interfaces exist in classpath
                        if super_idx != 0xffff_ffff {
                            let sup = tables.type_name(super_idx as usize);
                            if !sup.is_empty()
                                && lookup_class(own_classes, &self.classes, sup).is_none()
                                && is_core_android_or_oem_class(sup)
                                && res.missing_supers.len() < 80
                            {
                                res.missing_supers.push(DexIssue {
                                    kind: "NoClassDefFoundError".into(),
                                    source: label.into(),
                                    class_name: cls_desc.into(),
                                    method: "<class-load>".into(),
                                    target: sup.into(),
                                    pc: 0,
                                    detail: format!(
                                        "superclass {sup} is missing from classpath"
                                    ),
                                    boot_critical: boot_crit || target_entry.is_core_target,
                                });
                            }
                        }
                        if ifaces_off != 0 {
                            if let Some(cnt) = r_u32(tables.data, ifaces_off) {
                                for j in 0..(cnt as usize).min(128) {
                                    if let Some(ti) = r_u16(tables.data, ifaces_off + 4 + j * 2) {
                                        let ifn = tables.type_name(ti as usize);
                                        if !ifn.is_empty()
                                            && lookup_class(own_classes, &self.classes, ifn).is_none()
                                            && is_core_android_or_oem_class(ifn)
                                            && res.missing_supers.len() < 80
                                        {
                                            res.missing_supers.push(DexIssue {
                                                kind: "NoClassDefFoundError".into(),
                                                source: label.into(),
                                                class_name: cls_desc.into(),
                                                method: "<class-load>".into(),
                                                target: ifn.into(),
                                                pc: 0,
                                                detail: format!(
                                                    "implemented interface {ifn} is missing from classpath"
                                                ),
                                                boot_critical: boot_crit || target_entry.is_core_target,
                                            });
                                        }
                                    }
                                }
                            }
                        }

                        // 2. Concrete class abstract-method completeness check (superclass-first resolution)
                        if (access_flags & (ACC_ABSTRACT | ACC_INTERFACE)) == 0
                            && is_core_android_or_oem_class(cls_desc)
                            && res.incompat.len() < 80
                        {
                            if let Some(cinfo) = lookup_class(own_classes, &self.classes, cls_desc) {
                                let mut abstract_needed: Vec<(&str, &str, &str)> = Vec::new();
                                let mut visited: HashSet<&str> = HashSet::new();
                                let mut stack: Vec<&str> = Vec::new();
                                if !cinfo.superclass.is_empty() {
                                    stack.push(cinfo.superclass.as_str());
                                }
                                for inf in &cinfo.interfaces {
                                    stack.push(inf.as_str());
                                }
                                let mut complete = true;
                                while let Some(cur) = stack.pop() {
                                    if !visited.insert(cur) {
                                        continue;
                                    }
                                    let Some(pinfo) = lookup_class(own_classes, &self.classes, cur) else {
                                        complete = false;
                                        continue;
                                    };
                                    for (mn, mp, _h) in &pinfo.abstract_methods {
                                        abstract_needed.push((cur, mn.as_str(), mp.as_str()));
                                    }
                                    if !pinfo.superclass.is_empty() {
                                        stack.push(pinfo.superclass.as_str());
                                    }
                                    for inf in &pinfo.interfaces {
                                        stack.push(inf.as_str());
                                    }
                                }
                                if complete {
                                    let is_apk_target = label.ends_with(".apk");
                                    for (decl_owner, mn, mp) in abstract_needed {
                                        if is_apk_target
                                            && (mn.len() <= 2
                                                || decl_owner.starts_with("Landroidx/")
                                                || decl_owner.starts_with("Lkotlin/")
                                                || decl_owner.starts_with("Lkotlinx/")
                                                || decl_owner.starts_with("Lcom/android/compose/"))
                                        {
                                            continue;
                                        }
                                        if let ResolveResult::Found(rf) =
                                            resolve_method(own_classes, &self.classes, cls_desc, mn, mp)
                                        {
                                            if (rf & ACC_ABSTRACT) != 0 && res.incompat.len() < 80 {
                                                res.incompat.push(DexIssue {
                                                    kind: "AbstractMethodError".into(),
                                                    source: label.into(),
                                                    class_name: cls_desc.into(),
                                                    method: format!("{mn}{mp}"),
                                                    target: format!("{decl_owner}->{mn}{mp}"),
                                                    pc: 0,
                                                    detail: format!(
                                                        "concrete class does not implement abstract method {mn}{mp} from {decl_owner}"
                                                    ),
                                                    boot_critical: boot_crit,
                                                });
                                            }
                                        }
                                    }
                                }
                            }
                        }

                        // 3. Verify methods in class_data_item
                        if class_data_off == 0 || class_data_off >= tables.data.len() {
                            continue;
                        }
                        let mut pos = class_data_off;
                        let sf = read_uleb128(tables.data, &mut pos).unwrap_or(0) as usize;
                        let inf = read_uleb128(tables.data, &mut pos).unwrap_or(0) as usize;
                        let dm = read_uleb128(tables.data, &mut pos).unwrap_or(0) as usize;
                        let vm = read_uleb128(tables.data, &mut pos).unwrap_or(0) as usize;
                        for _ in 0..(sf + inf) {
                            let _ = read_uleb128(tables.data, &mut pos);
                            let _ = read_uleb128(tables.data, &mut pos);
                        }
                        for count in [dm, vm] {
                            let mut midx: usize = 0;
                            for _ in 0..count {
                                let Some(diff) = read_uleb128(tables.data, &mut pos) else {
                                    break;
                                };
                                let Some(mflags) = read_uleb128(tables.data, &mut pos) else {
                                    break;
                                };
                                let Some(code_off) = read_uleb128(tables.data, &mut pos) else {
                                    break;
                                };
                                midx = midx.saturating_add(diff as usize);
                                if (mflags & ACC_NATIVE) != 0 {
                                    res.native_methods += 1;
                                }
                                if code_off == 0 {
                                    continue;
                                }
                                let Some((_c, mname, mproto)) = tables.method_triple(midx) else {
                                    continue;
                                };
                                res.methods += 1;
                                let msig = format!("{mname}{mproto}");
                                let ret_type = mproto.rsplit(')').next().unwrap_or("");
                                let mut mctx = MethodVerifyCtx {
                                    tables: &tables,
                                    own_classes,
                                    global_classes: &self.classes,
                                    source: label,
                                    class_name: cls_desc,
                                    method_sig: &msig,
                                    return_type: ret_type,
                                    boot_critical: boot_crit,
                                    framework_res_ids: fw_res_ids,
                                    enabled_features: enabled_features_ref,
                                    rom_props: rom_props_ref,
                                    verify_errors: &mut res.verify_errors,
                                    null_derefs: &mut res.null_derefs,
                                    missing_methods: &mut res.missing_methods,
                                    missing_fields: &mut res.missing_fields,
                                    incompat: &mut res.incompat,
                                    missing_classes: &mut res.missing_classes,
                                    missing_res: &mut res.missing_res,
                                    logic: &mut res.logic,
                                    insns_counted: &mut res.insns,
                                };
                                verify_code_item(&mut mctx, code_off as usize);
                            }
                        }
                    }
                    res
                })
                .collect();

            let mut target_classes = 0usize;
            let mut target_methods = 0usize;
            let mut insns_verified = 0usize;
            let mut native_methods_checked = 0usize;
            let mut verify_errors = Vec::new();
            let mut null_deref_risks = Vec::new();
            let mut missing_superclasses = Vec::new();
            let mut missing_methods = Vec::new();
            let mut missing_fields = Vec::new();
            let mut incompatible_changes = Vec::new();
            let mut missing_framework_res_ids = Vec::new();
            let mut all_missing_classes: HashMap<String, Vec<String>> = HashMap::new();
            let mut logic_sys_svcs = Vec::new();
            let mut logic_jni = Vec::new();
            let mut logic_jni_bindings = Vec::new();
            let mut logic_refl = Vec::new();
            let mut logic_refl_members = Vec::new();
            let mut logic_binder = Vec::new();
            let mut logic_props: HashSet<String> = HashSet::new();
            let mut logic_comps = Vec::new();
            let mut dead_branches_pruned = 0usize;
            let mut static_strings_resolved = 0usize;

            for u in unit_results {
                target_classes += u.classes;
                target_methods += u.methods;
                insns_verified += u.insns;
                native_methods_checked += u.native_methods;
                verify_errors.extend(u.verify_errors);
                null_deref_risks.extend(u.null_derefs);
                missing_superclasses.extend(u.missing_supers);
                missing_methods.extend(u.missing_methods);
                missing_fields.extend(u.missing_fields);
                incompatible_changes.extend(u.incompat);
                missing_framework_res_ids.extend(u.missing_res);
                logic_sys_svcs.extend(u.logic.system_services);
                logic_jni.extend(u.logic.jni_libraries);
                logic_jni_bindings.extend(u.logic.jni_bindings);
                logic_refl.extend(u.logic.reflection_classes);
                logic_refl_members.extend(u.logic.reflection_members);
                logic_binder.extend(u.logic.binder_services);
                logic_props.extend(u.logic.system_properties);
                logic_comps.extend(u.logic.component_targets);
                dead_branches_pruned += u.logic.dead_branches_pruned;
                static_strings_resolved += u.logic.static_strings_resolved;
                for (cls, callers) in u.missing_classes {
                    let e = all_missing_classes.entry(cls).or_default();
                    if e.len() < 4 {
                        for c in callers {
                            if e.len() < 4 && !e.contains(&c) {
                                e.push(c);
                            }
                        }
                    }
                }
            }

            verify_errors.truncate(100);
            null_deref_risks.truncate(100);
            missing_superclasses.truncate(100);
            missing_methods.truncate(150);
            missing_fields.truncate(120);
            incompatible_changes.truncate(100);
            missing_framework_res_ids.truncate(80);

            let mut by_prefix: BTreeMap<String, MissingPrefixSummary> = BTreeMap::new();
            let mut sorted_missing: Vec<(String, Vec<String>)> =
                all_missing_classes.into_iter().collect();
            sorted_missing.sort_by(|a, b| a.0.cmp(&b.0));
            for (cls, callers) in sorted_missing {
                let pfx = package_prefix3(&cls);
                let e = by_prefix.entry(pfx).or_default();
                e.count += 1;
                if e.sample_classes.len() < 8 {
                    e.sample_classes.push(cls);
                }
                for c in callers {
                    if e.sample_callers.len() < 6 && !e.sample_callers.contains(&c) {
                        e.sample_callers.push(c);
                    }
                }
            }

            // Audit RRO Overlays against installed packages + target ARSC tables
            let mut overlay_issues = Vec::new();
            let mut seen_overlay_pkgs: HashMap<String, String> = HashMap::new();
            let mut static_res_owners: HashMap<(String, String, i32), String> = HashMap::new();

            for ov in &self.overlays {
                if ov.corrupt {
                    overlay_issues.push(OverlayIssue {
                        kind: "corrupt-overlay".into(),
                        apk_path: ov.apk_path.clone(),
                        package: ov.package.clone(),
                        target_package: ov.target_package.clone(),
                        detail: "AndroidManifest.xml missing or unreadable in overlay APK".into(),
                        missing_count: 0,
                        total_resources: 0,
                        sample_missing: Vec::new(),
                    });
                    continue;
                }
                if !ov.package.is_empty() {
                    if let Some(prev) = seen_overlay_pkgs.insert(ov.package.clone(), ov.apk_path.clone()) {
                        overlay_issues.push(OverlayIssue {
                            kind: "duplicate-overlay".into(),
                            apk_path: ov.apk_path.clone(),
                            package: ov.package.clone(),
                            target_package: ov.target_package.clone(),
                            detail: format!("duplicate overlay package '{}' also at {}", ov.package, prev),
                            missing_count: 0,
                            total_resources: ov.res_names.len(),
                            sample_missing: Vec::new(),
                        });
                    }
                }
                if !installed_set.is_empty()
                    && ov.target_package != "android"
                    && !installed_set.contains(&ov.target_package)
                {
                    overlay_issues.push(OverlayIssue {
                        kind: "missing-target-package".into(),
                        apk_path: ov.apk_path.clone(),
                        package: ov.package.clone(),
                        target_package: ov.target_package.clone(),
                        detail: format!(
                            "overlay targets '{}' which is not installed in this ROM",
                            ov.target_package
                        ),
                        missing_count: ov.res_names.len(),
                        total_resources: ov.res_names.len(),
                        sample_missing: Vec::new(),
                    });
                    continue;
                }
                if let Some(target_arsc) = self.arsc_targets.get(&ov.target_package) {
                    if !ov.res_names.is_empty() && !target_arsc.res_names.is_empty() {
                        let mut missing: Vec<String> = ov
                            .res_names
                            .iter()
                            .filter(|r| !target_arsc.res_names.contains(*r))
                            .cloned()
                            .collect();
                        missing.sort();
                        if !missing.is_empty() {
                            let mcnt = missing.len();
                            let tot = ov.res_names.len();
                            if mcnt >= 5 || (mcnt * 100 / tot.max(1)) >= 30 {
                                overlay_issues.push(OverlayIssue {
                                    kind: "orphaned-overlay-resources".into(),
                                    apk_path: ov.apk_path.clone(),
                                    package: ov.package.clone(),
                                    target_package: ov.target_package.clone(),
                                    detail: format!(
                                        "{mcnt}/{tot} resource(s) overridden by '{}' do not exist in target '{}'",
                                        ov.package, ov.target_package
                                    ),
                                    missing_count: mcnt,
                                    total_resources: tot,
                                    sample_missing: missing.into_iter().take(10).collect(),
                                });
                            }
                        }
                    }
                }
                if ov.is_static && ov.required_prop.is_empty() {
                    let mut conflicts = Vec::new();
                    for r in &ov.res_names {
                        let key = (ov.target_package.clone(), r.clone(), ov.priority);
                        if let Some(prev) = static_res_owners.get(&key) {
                            if prev != &ov.apk_path {
                                conflicts.push(format!("{r} (vs {prev})"));
                            }
                        } else {
                            static_res_owners.insert(key, ov.apk_path.clone());
                        }
                    }
                    if !conflicts.is_empty() {
                        overlay_issues.push(OverlayIssue {
                            kind: "static-overlay-conflict".into(),
                            apk_path: ov.apk_path.clone(),
                            package: ov.package.clone(),
                            target_package: ov.target_package.clone(),
                            detail: format!(
                                "{} static resource override conflict(s) at priority {}",
                                conflicts.len(),
                                ov.priority
                            ),
                            missing_count: conflicts.len(),
                            total_resources: ov.res_names.len(),
                            sample_missing: conflicts.into_iter().take(8).collect(),
                        });
                    }
                }
            }

            let dedup_sites = |items: Vec<LogicCallSite>, cap: usize| -> Vec<LogicCallSite> {
                let mut idx_map: HashMap<(String, String, String), usize> = HashMap::new();
                let mut out: Vec<LogicCallSite> = Vec::new();
                for it in items {
                    let key = (it.value.clone(), it.secondary.clone(), it.source.clone());
                    if let Some(&pos) = idx_map.get(&key) {
                        let ex = &mut out[pos];
                        ex.dead_branch = ex.dead_branch && it.dead_branch;
                        ex.guarded = ex.guarded && it.guarded;
                        ex.boot_critical = ex.boot_critical || it.boot_critical;
                        ex.blocking = ex.blocking || it.blocking;
                        if !it.dead_branch && !it.guarded {
                            ex.caller = it.caller;
                        }
                    } else if out.len() < cap {
                        idx_map.insert(key, out.len());
                        out.push(it);
                    }
                }
                out
            };
            let dedup_bindings = |items: Vec<JniBinding>, cap: usize| -> Vec<JniBinding> {
                let mut idx_map: HashMap<(String, String), usize> = HashMap::new();
                let mut out: Vec<JniBinding> = Vec::new();
                for it in items {
                    let key = (it.lib.clone(), it.class_name.clone());
                    if let Some(&pos) = idx_map.get(&key) {
                        let ex = &mut out[pos];
                        ex.guarded = ex.guarded && it.guarded;
                        ex.boot_critical = ex.boot_critical || it.boot_critical;
                    } else if out.len() < cap {
                        idx_map.insert(key, out.len());
                        out.push(it);
                    }
                }
                out
            };
            let mut sorted_props: Vec<String> = logic_props.into_iter().collect();
            sorted_props.sort();
            sorted_props.truncate(600);
            let logic_map = DexLogicMap {
                system_services: dedup_sites(logic_sys_svcs, 350),
                jni_libraries: dedup_sites(logic_jni, 350),
                jni_bindings: dedup_bindings(logic_jni_bindings, 250),
                reflection_classes: dedup_sites(logic_refl, 350),
                reflection_members: dedup_sites(logic_refl_members, 350),
                binder_services: dedup_sites(logic_binder, 400),
                system_properties: sorted_props,
                component_targets: dedup_sites(logic_comps, 300),
                dead_branches_pruned,
                static_strings_resolved,
            };

            let provider_methods: usize = self.classes.values().map(|c| c.methods.len()).sum();
            let provider_fields: usize = self.classes.values().map(|c| c.fields.len()).sum();

            DexVerifyReport {
                stats: DexVerifyStats {
                    provider_jars: self.provider_jars,
                    provider_classes: self.classes.len(),
                    provider_methods,
                    provider_fields,
                    target_jars: self.targets.len(),
                    target_classes,
                    target_methods_verified: target_methods,
                    insns_verified,
                    vdex_extracted: self.vdex_extracted,
                    native_methods_checked,
                    overlays_checked: self.overlays.len(),
                    elapsed_ms: t0.elapsed().as_millis() as u64,
                },
                verify_errors,
                null_deref_risks,
                missing_superclasses,
                missing_methods,
                missing_fields,
                incompatible_changes,
                missing_ref_classes_by_prefix: by_prefix,
                missing_framework_res_ids,
                overlay_issues,
                logic_map,
            }
        });

        serde_json::to_string(&report).map_err(|e| PyOSError::new_err(e.to_string()))
    }

    pub fn has_classes(&self, target_label: String, names: Vec<String>) -> Vec<bool> {
        let target_own = self
            .targets
            .iter()
            .find(|t| t.label == target_label)
            .or_else(|| {
                let tail = target_label.rsplit('/').next().unwrap_or(&target_label);
                self.targets
                    .iter()
                    .find(|t| t.label.rsplit('/').next().unwrap_or(&t.label) == tail)
            })
            .map(|t| &t.own_classes);

        names
            .iter()
            .map(|n| {
                let desc = dot_class_to_desc(n);
                target_own.is_some_and(|m| m.contains_key(&desc))
                    || self.classes.contains_key(&desc)
            })
            .collect()
    }
}

#[pyfunction]
pub fn vdev_parse_arsc_bytes_json(py: Python<'_>, data: &[u8]) -> PyResult<String> {
    let owned = data.to_vec();
    let pkgs = py.detach(move || parse_arsc_packages(&owned));
    #[derive(Serialize)]
    struct OutPkg {
        package_id: u32,
        package_name: String,
        res_id_count: usize,
        res_name_count: usize,
        sample_names: Vec<String>,
    }
    let out: Vec<OutPkg> = pkgs
        .into_iter()
        .map(|p| {
            let mut names: Vec<String> = p.res_names.into_iter().collect();
            names.sort();
            let ncnt = names.len();
            OutPkg {
                package_id: p.package_id,
                package_name: p.package_name,
                res_id_count: p.res_ids.len(),
                res_name_count: ncnt,
                sample_names: names.into_iter().take(20).collect(),
            }
        })
        .collect();
    serde_json::to_string(&out).map_err(|e| PyValueError::new_err(e.to_string()))
}
