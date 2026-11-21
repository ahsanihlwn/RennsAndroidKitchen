const VERITY_PATTERNS: &[&[u8]] = &[
    b"verifyatboot",
    b"verify",
    b"avb_keys",
    b"avb",
    b"support_scfs",
    b"fsverity",
];

const ENCRYPTION_PATTERNS: &[&[u8]] = &[
    b"forceencrypt",
    b"forcefdeorfbe",
    b"fileencryption",
];

fn match_pattern(slice: &[u8], prefixes: &[&[u8]]) -> usize {
    if slice.is_empty() {
        return 0;
    }
    let mut skip = usize::from(slice[0] == b',');
    let rest = &slice[skip..];
    let matched = prefixes.iter().find(|&&p| rest.starts_with(p));
    let Some(&prefix) = matched else {
        return 0;
    };
    skip += prefix.len();
    if slice.get(skip) == Some(&b'=') {
        while let Some(&b) = slice.get(skip) {
            if b == b' ' || b == b'\n' || b == b',' || b == 0 {
                break;
            }
            skip += 1;
        }
    }
    skip
}

fn remove_patterns(buf: &mut [u8], prefixes: &[&[u8]]) -> usize {
    let orig_len = buf.len();
    let mut read_idx = 0usize;
    let mut write_idx = 0usize;
    let mut new_len = orig_len;

    while read_idx < orig_len {
        let skip = match_pattern(&buf[read_idx..], prefixes);
        if skip > 0 {
            let pat = String::from_utf8_lossy(&buf[read_idx..read_idx + skip]);
            eprintln!("Remove pattern [{pat}]");
            new_len -= skip;
            read_idx += skip;
        } else {
            buf[write_idx] = buf[read_idx];
            write_idx += 1;
            read_idx += 1;
        }
    }
    buf[write_idx..orig_len].fill(0);
    new_len
}

pub fn patch_verity(buf: &mut [u8]) -> usize {
    remove_patterns(buf, VERITY_PATTERNS)
}

pub fn patch_encryption(buf: &mut [u8]) -> usize {
    remove_patterns(buf, ENCRYPTION_PATTERNS)
}
