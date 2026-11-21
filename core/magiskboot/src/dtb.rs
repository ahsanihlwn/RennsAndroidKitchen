use std::env;
use std::fs;

use memchr::memmem;

use crate::format::DTB_MAGIC;
use crate::pattern::patch_verity;

const FDT_BEGIN_NODE: u32 = 1;
const FDT_END_NODE: u32 = 2;
const FDT_PROP: u32 = 3;
const FDT_NOP: u32 = 4;
const FDT_END: u32 = 9;

fn align4(x: usize) -> usize {
    (x + 3) & !3
}

fn align8(x: usize) -> usize {
    (x + 7) & !7
}

fn read_be_u32(buf: &[u8], off: usize) -> Option<u32> {
    let slice = buf.get(off..off + 4)?;
    Some(u32::from_be_bytes(slice.try_into().unwrap()))
}

struct FdtNode {
    offset: usize,
    name: String,
    props: Vec<FdtProp>,
    children: Vec<FdtNode>,
}

struct FdtProp {
    name: String,
    val_abs_off: usize,
    len: usize,
}

struct FdtHeader {
    totalsize: usize,
    off_dt_struct: usize,
    off_dt_strings: usize,
    version: u32,
}

fn parse_fdt_header(buf: &[u8]) -> Option<FdtHeader> {
    if buf.len() < 40 {
        return None;
    }
    if &buf[0..4] != DTB_MAGIC {
        return None;
    }
    let totalsize = read_be_u32(buf, 4)? as usize;
    let off_dt_struct = read_be_u32(buf, 8)? as usize;
    let off_dt_strings = read_be_u32(buf, 12)? as usize;
    let version = read_be_u32(buf, 20)?;
    if totalsize < 40 || totalsize > buf.len() || off_dt_struct >= totalsize || off_dt_strings > totalsize {
        return None;
    }
    Some(FdtHeader {
        totalsize,
        off_dt_struct,
        off_dt_strings,
        version,
    })
}

fn read_cstr(buf: &[u8], start: usize) -> Option<(&str, usize)> {
    let end_rel = buf.get(start..)?.iter().position(|&b| b == 0)?;
    let s = std::str::from_utf8(&buf[start..start + end_rel]).ok()?;
    Some((s, start + end_rel + 1))
}

fn parse_node_tree(
    fdt: &[u8],
    fdt_base: usize,
    hdr: &FdtHeader,
    pos: &mut usize,
) -> Result<FdtNode, String> {
    let node_offset = *pos - hdr.off_dt_struct;
    let (name_raw, after_name) =
        read_cstr(&fdt[..hdr.totalsize], *pos + 4).ok_or("Malformed FDT node name")?;
    let name = if hdr.version < 16 {
        name_raw.rsplit('/').next().unwrap_or(name_raw).to_string()
    } else {
        name_raw.to_string()
    };
    *pos = hdr.off_dt_struct + align4(after_name - hdr.off_dt_struct);

    let mut props = Vec::new();
    let mut children = Vec::new();

    loop {
        let tag = read_be_u32(&fdt[..hdr.totalsize], *pos).ok_or("Unexpected EOF in FDT")?;
        match tag {
            FDT_NOP => {
                *pos += 4;
            }
            FDT_PROP => {
                let len = read_be_u32(fdt, *pos + 4).ok_or("Malformed FDT_PROP len")? as usize;
                let nameoff = read_be_u32(fdt, *pos + 8).ok_or("Malformed FDT_PROP nameoff")? as usize;
                let mut val_off = *pos + 12;
                if hdr.version < 16 && len >= 8 {
                    let rel = val_off - hdr.off_dt_struct;
                    val_off = hdr.off_dt_struct + align8(rel);
                }
                if val_off + len > hdr.totalsize {
                    return Err("FDT_PROP value out of bounds".into());
                }
                let (pname, _) = read_cstr(&fdt[..hdr.totalsize], hdr.off_dt_strings + nameoff)
                    .ok_or("Malformed FDT property name")?;
                props.push(FdtProp {
                    name: pname.to_string(),
                    val_abs_off: fdt_base + val_off,
                    len,
                });
                let end_rel = (val_off + len) - hdr.off_dt_struct;
                *pos = hdr.off_dt_struct + align4(end_rel);
            }
            FDT_BEGIN_NODE => {
                children.push(parse_node_tree(fdt, fdt_base, hdr, pos)?);
            }
            FDT_END_NODE => {
                *pos += 4;
                break;
            }
            FDT_END => {
                return Err("Premature FDT_END".into());
            }
            other => {
                return Err(format!("Unknown FDT token 0x{other:08x}"));
            }
        }
    }

    Ok(FdtNode {
        offset: node_offset,
        name,
        props,
        children,
    })
}

fn parse_fdt_at(full: &[u8], base: usize) -> Option<(usize, FdtNode)> {
    let slice = &full[base..];
    let hdr = parse_fdt_header(slice)?;
    let mut pos = hdr.off_dt_struct;
    while pos + 4 <= hdr.totalsize {
        let tag = read_be_u32(&slice[..hdr.totalsize], pos)?;
        if tag == FDT_NOP {
            pos += 4;
            continue;
        }
        if tag != FDT_BEGIN_NODE {
            return None;
        }
        let root = parse_node_tree(slice, base, &hdr, &mut pos).ok()?;
        return Some((hdr.totalsize, root));
    }
    None
}

fn fdt_offsets(buf: &[u8]) -> Vec<(usize, usize, FdtNode)> {
    let mut out = Vec::new();
    let mut pos = 0usize;
    while pos < buf.len() {
        let Some(rel) = memmem::find(&buf[pos..], DTB_MAGIC) else {
            break;
        };
        let abs = pos + rel;
        if let Some((totalsize, root)) = parse_fdt_at(buf, abs) {
            out.push((abs, totalsize, root));
            pos = abs + totalsize;
        } else {
            pos = abs + 4;
        }
    }
    out
}

fn find_node<'a>(node: &'a FdtNode, target_name: &str) -> Option<&'a FdtNode> {
    if node.name == target_name {
        return Some(node);
    }
    for c in &node.children {
        if let Some(found) = find_node(c, target_name) {
            return Some(found);
        }
    }
    None
}

fn is_printable_cstr(val: &[u8]) -> bool {
    if val.is_empty() {
        return true;
    }
    if val.len() > 1 && val[0] == 0 {
        return false;
    }
    for (i, &b) in val.iter().enumerate() {
        if i == val.len() - 1 {
            return b == 0;
        }
        if b < 32 || b >= 127 {
            return false;
        }
    }
    true
}

fn print_node(buf: &[u8], node: &FdtNode, depth: usize, depth_flags: &mut Vec<bool>) {
    if depth > 0 {
        for &has_more in &depth_flags[..depth - 1] {
            print!("{}", if has_more { "│   " } else { "    " });
        }
        print!(
            "{}",
            if depth_flags[depth - 1] {
                "├── "
            } else {
                "└── "
            }
        );
    }
    println!("#{}: {}", node.offset, node.name);

    let has_children = !node.children.is_empty();
    if depth_flags.len() <= depth {
        depth_flags.resize(depth + 1, false);
    }
    depth_flags[depth] = has_children;

    for prop in &node.props {
        for &has_more in &depth_flags[..depth] {
            print!("{}", if has_more { "│   " } else { "    " });
        }
        print!(
            "{}",
            if depth_flags[depth] { "│  " } else { "   " }
        );
        let val = &buf[prop.val_abs_off..prop.val_abs_off + prop.len];
        if is_printable_cstr(val) {
            let s = if val.is_empty() {
                ""
            } else {
                let end = val.iter().position(|&b| b == 0).unwrap_or(val.len());
                std::str::from_utf8(&val[..end]).unwrap_or("")
            };
            println!("[{}]: [{}]", prop.name, s);
        } else {
            println!("[{}]: <bytes>({})", prop.name, prop.len);
        }
    }

    let last_idx = node.children.len().saturating_sub(1);
    for (i, child) in node.children.iter().enumerate() {
        depth_flags[depth] = i < last_idx;
        print_node(buf, child, depth + 1, depth_flags);
    }
}

pub fn dtb_print(file: &str, fstab_only: bool) -> Result<(), String> {
    let data = fs::read(file).map_err(|e| format!("Failed reading [{file}]: {e}"))?;
    eprintln!("Loading dtbs from [{file}]");
    let fdts = fdt_offsets(&data);
    let mut depth_flags = Vec::new();
    for (idx, (_, _, root)) in fdts.iter().enumerate() {
        if fstab_only {
            if let Some(fstab) = find_node(root, "fstab") {
                eprintln!("Found fstab in dtb.{idx:04}");
                print_node(&data, fstab, 0, &mut depth_flags);
            }
        } else {
            eprintln!("Printing dtb.{idx:04}");
            print_node(&data, root, 0, &mut depth_flags);
        }
    }
    eprintln!();
    Ok(())
}

pub fn dtb_patch(file: &str) -> Result<bool, String> {
    eprintln!("Loading dtbs from [{file}]");
    let keep_verity = env::var("KEEPVERITY").is_ok_and(|v| v == "true");
    let mut data = fs::read(file).map_err(|e| format!("Failed reading [{file}]: {e}"))?;
    let fdts = fdt_offsets(&data);
    let mut patched = false;

    for (_, _, root) in &fdts {
        if let Some(chosen) = root.children.iter().find(|c| c.name == "chosen") {
            if let Some(bootargs) = chosen.props.iter().find(|p| p.name == "bootargs") {
                let slice = &mut data[bootargs.val_abs_off..bootargs.val_abs_off + bootargs.len];
                if let Some(off) = memmem::find(slice, b"skip_initramfs") {
                    eprintln!("Patch [skip_initramfs] -> [want_initramfs]");
                    slice[off..off + 4].copy_from_slice(b"want");
                    patched = true;
                }
            }
        }

        if !keep_verity {
            if let Some(fstab) = find_node(root, "fstab") {
                for child in &fstab.children {
                    if let Some(flags) = child.props.iter().find(|p| p.name == "fsmgr_flags") {
                        let slice =
                            &mut data[flags.val_abs_off..flags.val_abs_off + flags.len];
                        let orig_len = slice.len();
                        let new_len = patch_verity(slice);
                        if new_len != orig_len {
                            patched = true;
                        }
                    }
                }
            }
        }
    }

    if patched {
        fs::write(file, &data).map_err(|e| format!("Failed writing [{file}]: {e}"))?;
    }
    Ok(patched)
}

pub fn dtb_test(file: &str) -> Result<bool, String> {
    let data = fs::read(file).map_err(|e| format!("Failed reading [{file}]: {e}"))?;
    let fdts = fdt_offsets(&data);
    for (_, _, root) in &fdts {
        if let Some(fstab) = find_node(root, "fstab") {
            if let Some(system) = fstab.children.iter().find(|c| c.name == "system") {
                if let Some(mnt) = system.props.iter().find(|p| p.name == "mnt_point") {
                    let val = &data[mnt.val_abs_off..mnt.val_abs_off + mnt.len];
                    let trimmed = val.split(|&b| b == 0).next().unwrap_or(val);
                    if trimmed == b"/system_root" {
                        return Ok(false);
                    }
                }
            }
        }
    }
    Ok(true)
}

pub fn find_embedded_dtb_offset(buf: &[u8]) -> Option<usize> {
    let mut pos = 0usize;
    while pos < buf.len() {
        let rel = memmem::find(&buf[pos..], DTB_MAGIC)?;
        let abs = pos + rel;
        let rem = buf.len() - abs;
        if rem >= 40 {
            if let (Some(totalsize), Some(off_dt_struct)) =
                (read_be_u32(buf, abs + 4), read_be_u32(buf, abs + 8))
            {
                let totalsize = totalsize as usize;
                let off_dt_struct = off_dt_struct as usize;
                if totalsize <= rem && off_dt_struct + 4 <= rem {
                    if read_be_u32(buf, abs + off_dt_struct) == Some(FDT_BEGIN_NODE) {
                        return Some(abs);
                    }
                }
            }
        }
        pos = abs + 4;
    }
    None
}

pub fn cmd_dtb(args: &[String]) -> Result<i32, String> {
    if args.len() < 2 {
        return Err("Invalid dtb arguments".into());
    }
    let file = &args[0];
    match args[1].as_str() {
        "print" => {
            let fstab_only = args.get(2).is_some_and(|s| s == "-f");
            dtb_print(file, fstab_only)?;
            Ok(0)
        }
        "patch" => {
            if dtb_patch(file)? {
                Ok(0)
            } else {
                Ok(1)
            }
        }
        "test" => {
            if dtb_test(file)? {
                Ok(0)
            } else {
                Ok(1)
            }
        }
        _ => Err(format!("Unknown dtb subcommand: {}", args[1])),
    }
}
