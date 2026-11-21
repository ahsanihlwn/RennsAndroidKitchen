use std::cmp::Reverse;
use std::collections::{BTreeSet, BinaryHeap, HashMap, VecDeque};
use std::fs::File;
use std::io::{BufWriter, Write};
use std::path::Path;
use std::process::{Command, Stdio};
use std::sync::Mutex;

use indexmap::IndexMap;
use sha1::{Digest, Sha1};

use crate::error::{Error, Result};
use crate::rangelib::RangeSet;

pub const BLOCK_SIZE: u64 = 4096;

/// Abstraction over block-based partition images used by `BlockImageDiff`.
pub trait Image {
    fn blocksize(&self) -> u64;
    fn total_blocks(&self) -> u64;
    fn care_map(&self) -> &RangeSet;
    fn clobbered_blocks(&self) -> &RangeSet;
    fn extended(&self) -> &RangeSet;
    fn file_map(&self) -> &IndexMap<String, RangeSet>;

    fn read_range_set(&mut self, ranges: &RangeSet) -> Result<Vec<Vec<u8>>>;

    fn stream_range_set(
        &mut self,
        ranges: &RangeSet,
        cb: &mut dyn FnMut(&[u8]) -> Result<()>,
    ) -> Result<()> {
        for piece in self.read_range_set(ranges)? {
            cb(&piece)?;
        }
        Ok(())
    }

    fn total_sha1(&mut self, include_clobbered_blocks: bool) -> Result<String>;
}

/// A zero-length image, used as `src` for full OTAs (`img2sdat`).
#[derive(Debug, Clone, Default)]
pub struct EmptyImage {
    care_map: RangeSet,
    clobbered_blocks: RangeSet,
    extended: RangeSet,
    file_map: IndexMap<String, RangeSet>,
}

impl EmptyImage {
    pub fn new() -> Self {
        Self::default()
    }
}

impl Image for EmptyImage {
    fn blocksize(&self) -> u64 {
        BLOCK_SIZE
    }

    fn total_blocks(&self) -> u64 {
        0
    }

    fn care_map(&self) -> &RangeSet {
        &self.care_map
    }

    fn clobbered_blocks(&self) -> &RangeSet {
        &self.clobbered_blocks
    }

    fn extended(&self) -> &RangeSet {
        &self.extended
    }

    fn file_map(&self) -> &IndexMap<String, RangeSet> {
        &self.file_map
    }

    fn read_range_set(&mut self, _ranges: &RangeSet) -> Result<Vec<Vec<u8>>> {
        Ok(Vec::new())
    }

    fn total_sha1(&mut self, _include_clobbered_blocks: bool) -> Result<String> {
        let hasher = Sha1::new();
        Ok(hex_encode(&hasher.finalize()))
    }
}

/// An image wrapped around an in-memory byte buffer.
#[derive(Debug, Clone)]
pub struct DataImage {
    data: Vec<u8>,
    blocksize: u64,
    total_blocks: u64,
    care_map: RangeSet,
    clobbered_blocks: RangeSet,
    extended: RangeSet,
    file_map: IndexMap<String, RangeSet>,
}

impl DataImage {
    pub fn new(mut data: Vec<u8>, trim: bool, pad: bool) -> Result<Self> {
        let blocksize = BLOCK_SIZE as usize;
        if trim && pad {
            return Err(Error::InvalidData(
                "trim and pad cannot both be true".to_string(),
            ));
        }

        let partial = data.len() % blocksize;
        let mut padded = false;
        if partial > 0 {
            if trim {
                data.truncate(data.len() - partial);
            } else if pad {
                data.resize(data.len() + (blocksize - partial), 0);
                padded = true;
            } else {
                return Err(Error::InvalidData(format!(
                    "data for DataImage must be multiple of {} bytes unless trim or pad is specified",
                    blocksize
                )));
            }
        }

        let total_blocks = (data.len() / blocksize) as u64;
        let care_map = RangeSet::from_range(0, total_blocks);
        let clobbered_blocks = if padded && total_blocks > 0 {
            RangeSet::from_range(total_blocks - 1, total_blocks)
        } else {
            RangeSet::new()
        };
        let extended = RangeSet::new();

        let mut zero_blocks = Vec::new();
        let mut nonzero_blocks = Vec::new();
        let limit = if padded && total_blocks > 0 {
            total_blocks - 1
        } else {
            total_blocks
        };

        for i in 0..limit {
            let start = (i as usize) * blocksize;
            let end = start + blocksize;
            if data[start..end].iter().all(|&b| b == 0) {
                zero_blocks.push(i);
                zero_blocks.push(i + 1);
            } else {
                nonzero_blocks.push(i);
                nonzero_blocks.push(i + 1);
            }
        }

        if zero_blocks.is_empty() && nonzero_blocks.is_empty() && clobbered_blocks.is_empty() {
            return Err(Error::InvalidData("DataImage cannot be empty".to_string()));
        }

        let mut file_map = IndexMap::new();
        if !zero_blocks.is_empty() {
            file_map.insert("__ZERO".to_string(), RangeSet::from_data(zero_blocks)?);
        }
        if !nonzero_blocks.is_empty() {
            file_map.insert(
                "__NONZERO".to_string(),
                RangeSet::from_data(nonzero_blocks)?,
            );
        }
        if !clobbered_blocks.is_empty() {
            file_map.insert("__COPY".to_string(), clobbered_blocks.clone());
        }

        Ok(Self {
            data,
            blocksize: BLOCK_SIZE,
            total_blocks,
            care_map,
            clobbered_blocks,
            extended,
            file_map,
        })
    }
}

impl Image for DataImage {
    fn blocksize(&self) -> u64 {
        self.blocksize
    }

    fn total_blocks(&self) -> u64 {
        self.total_blocks
    }

    fn care_map(&self) -> &RangeSet {
        &self.care_map
    }

    fn clobbered_blocks(&self) -> &RangeSet {
        &self.clobbered_blocks
    }

    fn extended(&self) -> &RangeSet {
        &self.extended
    }

    fn file_map(&self) -> &IndexMap<String, RangeSet> {
        &self.file_map
    }

    fn read_range_set(&mut self, ranges: &RangeSet) -> Result<Vec<Vec<u8>>> {
        let bs = self.blocksize as usize;
        let mut out = Vec::new();
        for (s, e) in ranges {
            out.push(self.data[(s as usize) * bs..(e as usize) * bs].to_vec());
        }
        Ok(out)
    }

    fn total_sha1(&mut self, include_clobbered_blocks: bool) -> Result<String> {
        let mut hasher = Sha1::new();
        if !include_clobbered_blocks {
            let ranges = self.care_map.subtract(&self.clobbered_blocks);
            let bs = self.blocksize as usize;
            for (s, e) in &ranges {
                hasher.update(&self.data[(s as usize) * bs..(e as usize) * bs]);
            }
        } else {
            hasher.update(&self.data);
        }
        Ok(hex_encode(&hasher.finalize()))
    }
}

/// Configuration options for stash budgeting during block diff generation.
#[derive(Debug, Clone)]
pub struct BlockOptions {
    pub cache_size: Option<u64>,
    pub stash_threshold: f64,
}

impl Default for BlockOptions {
    fn default() -> Self {
        Self {
            cache_size: None,
            stash_threshold: 0.8,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum TransferStyle {
    New,
    Zero,
    Diff,
    Move,
    Bsdiff,
    Imgdiff,
}

impl TransferStyle {
    fn as_str(self) -> &'static str {
        match self {
            TransferStyle::New => "new",
            TransferStyle::Zero => "zero",
            TransferStyle::Diff => "diff",
            TransferStyle::Move => "move",
            TransferStyle::Bsdiff => "bsdiff",
            TransferStyle::Imgdiff => "imgdiff",
        }
    }
}

#[derive(Debug, Clone)]
struct Transfer {
    id: usize,
    order: usize,
    tgt_name: String,
    src_name: Option<String>,
    tgt_ranges: RangeSet,
    src_ranges: RangeSet,
    style: TransferStyle,
    intact: bool,
    goes_before: IndexMap<usize, u64>,
    goes_after: IndexMap<usize, u64>,
    stash_before: Vec<(usize, RangeSet)>,
    use_stash: Vec<(usize, RangeSet)>,
    patch_start: u64,
    patch_len: u64,
}

impl Transfer {
    fn new(
        id: usize,
        tgt_name: String,
        src_name: Option<String>,
        tgt_ranges: RangeSet,
        src_ranges: RangeSet,
        style: TransferStyle,
    ) -> Self {
        let intact = tgt_ranges.monotonic && src_ranges.monotonic;
        Self {
            id,
            order: id,
            tgt_name,
            src_name,
            tgt_ranges,
            src_ranges,
            style,
            intact,
            goes_before: IndexMap::new(),
            goes_after: IndexMap::new(),
            stash_before: Vec::new(),
            use_stash: Vec::new(),
            patch_start: 0,
            patch_len: 0,
        }
    }

    fn net_stash_change(&self) -> i64 {
        let stash_in: u64 = self.stash_before.iter().map(|(_, sr)| sr.size()).sum();
        let stash_out: u64 = self.use_stash.iter().map(|(_, sr)| sr.size()).sum();
        (stash_in as i64) - (stash_out as i64)
    }

    fn convert_to_new(&mut self) {
        self.use_stash.clear();
        self.style = TransferStyle::New;
        self.src_ranges = RangeSet::new();
    }
}

impl std::fmt::Display for Transfer {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(
            f,
            "{}: <{} {} to {}>",
            self.id,
            self.src_ranges,
            self.style.as_str(),
            self.tgt_ranges
        )
    }
}

pub struct BlockImageDiff<'a> {
    tgt: &'a mut dyn Image,
    src: &'a mut dyn Image,
    version: u8,
    threads: usize,
    disable_imgdiff: bool,
    options: BlockOptions,
    transfers: Vec<Transfer>,
    transfer_order: Vec<usize>,
    src_basenames: IndexMap<String, String>,
    src_numpatterns: IndexMap<String, String>,
    max_stashed_size: u64,
    pub touched_src_ranges: RangeSet,
    pub touched_src_sha1: Option<String>,
    pub quiet: bool,
}

impl<'a> BlockImageDiff<'a> {
    pub fn new(
        tgt: &'a mut dyn Image,
        src: &'a mut dyn Image,
        version: u8,
        threads: Option<usize>,
        disable_imgdiff: bool,
        options: BlockOptions,
    ) -> Result<Self> {
        if !(1..=4).contains(&version) {
            return Err(Error::InvalidData(format!(
                "transfer.list version must be 1..4, got {version}"
            )));
        }
        if tgt.blocksize() != BLOCK_SIZE || src.blocksize() != BLOCK_SIZE {
            return Err(Error::InvalidData(format!(
                "blocksize must be {BLOCK_SIZE}, got tgt={} src={}",
                tgt.blocksize(),
                src.blocksize()
            )));
        }

        let threads = threads.unwrap_or_else(|| {
            let n = std::thread::available_parallelism()
                .map(|p| p.get())
                .unwrap_or(1);
            (n / 2).max(1)
        });

        Self::assert_partition(src.care_map(), src.file_map().values())?;
        Self::assert_partition(tgt.care_map(), tgt.file_map().values())?;

        Ok(Self {
            tgt,
            src,
            version,
            threads: threads.max(1),
            disable_imgdiff,
            options,
            transfers: Vec::new(),
            transfer_order: Vec::new(),
            src_basenames: IndexMap::new(),
            src_numpatterns: IndexMap::new(),
            max_stashed_size: 0,
            touched_src_ranges: RangeSet::new(),
            touched_src_sha1: None,
            quiet: false,
        })
    }

    pub fn max_stashed_size(&self) -> u64 {
        self.max_stashed_size
    }

    pub fn compute(&mut self, prefix: &Path) -> Result<()> {
        self.abbreviate_source_names();
        self.find_transfers()?;

        self.generate_digraph();
        self.find_vertex_sequence();

        if self.version == 1 {
            self.remove_backward_edges();
        } else {
            self.reverse_backward_edges()?;
            self.improve_vertex_sequence()?;
        }

        if self.version >= 2 && self.options.cache_size.is_some() {
            self.revise_stash_size()?;
        }

        self.assert_sequence_good()?;

        self.compute_patches(prefix)?;
        self.write_transfers(prefix)?;
        Ok(())
    }

    fn abbreviate_source_names(&mut self) {
        for k in self.src.file_map().keys() {
            let b = basename(k);
            self.src_basenames.insert(b.to_string(), k.clone());
            let pattern = replace_digit_runs_with_hash(b);
            self.src_numpatterns.insert(pattern, k.clone());
        }
    }

    fn find_transfers(&mut self) -> Result<()> {
        let empty = RangeSet::new();
        let tgt_entries: Vec<(String, RangeSet)> = self
            .tgt
            .file_map()
            .iter()
            .map(|(k, v)| (k.clone(), v.clone()))
            .collect();

        for (tgt_fn, tgt_ranges) in tgt_entries {
            if tgt_fn == "__ZERO" {
                let src_ranges = self
                    .src
                    .file_map()
                    .get("__ZERO")
                    .cloned()
                    .unwrap_or_else(|| empty.clone());
                self.add_transfer(
                    tgt_fn,
                    Some("__ZERO".to_string()),
                    tgt_ranges,
                    src_ranges,
                    TransferStyle::Zero,
                    false,
                )?;
                continue;
            }

            if tgt_fn == "__COPY" {
                self.add_transfer(
                    tgt_fn,
                    None,
                    tgt_ranges,
                    empty.clone(),
                    TransferStyle::New,
                    false,
                )?;
                continue;
            }

            if let Some(src_ranges) = self.src.file_map().get(&tgt_fn).cloned() {
                self.add_transfer(
                    tgt_fn.clone(),
                    Some(tgt_fn),
                    tgt_ranges,
                    src_ranges,
                    TransferStyle::Diff,
                    self.version >= 3,
                )?;
                continue;
            }

            let b = basename(&tgt_fn).to_string();
            if let Some(src_fn) = self.src_basenames.get(&b).cloned() {
                let src_ranges = self.src.file_map()[&src_fn].clone();
                self.add_transfer(
                    tgt_fn,
                    Some(src_fn),
                    tgt_ranges,
                    src_ranges,
                    TransferStyle::Diff,
                    self.version >= 3,
                )?;
                continue;
            }

            let pat = replace_digit_runs_with_hash(&b);
            if let Some(src_fn) = self.src_numpatterns.get(&pat).cloned() {
                let src_ranges = self.src.file_map()[&src_fn].clone();
                self.add_transfer(
                    tgt_fn,
                    Some(src_fn),
                    tgt_ranges,
                    src_ranges,
                    TransferStyle::Diff,
                    self.version >= 3,
                )?;
                continue;
            }

            self.add_transfer(
                tgt_fn,
                None,
                tgt_ranges,
                empty.clone(),
                TransferStyle::New,
                false,
            )?;
        }

        self.transfer_order = (0..self.transfers.len()).collect();
        Ok(())
    }

    fn add_transfer(
        &mut self,
        tgt_name: String,
        src_name: Option<String>,
        mut tgt_ranges: RangeSet,
        mut src_ranges: RangeSet,
        style: TransferStyle,
        split: bool,
    ) -> Result<()> {
        if style != TransferStyle::Diff || !split {
            let id = self.transfers.len();
            self.transfers.push(Transfer::new(
                id, tgt_name, src_name, tgt_ranges, src_ranges, style,
            ));
            return Ok(());
        }

        let Some(cache_size) = self.options.cache_size else {
            let id = self.transfers.len();
            self.transfers.push(Transfer::new(
                id, tgt_name, src_name, tgt_ranges, src_ranges, style,
            ));
            return Ok(());
        };

        let split_threshold = 0.125_f64;
        let max_blocks_per_transfer =
            ((cache_size as f64) * split_threshold / (self.tgt.blocksize() as f64)) as u64;

        if max_blocks_per_transfer == 0
            || (tgt_ranges.size() <= max_blocks_per_transfer
                && src_ranges.size() <= max_blocks_per_transfer)
        {
            let id = self.transfers.len();
            self.transfers.push(Transfer::new(
                id, tgt_name, src_name, tgt_ranges, src_ranges, style,
            ));
            return Ok(());
        }

        let mut pieces = 0usize;
        while tgt_ranges.size() > max_blocks_per_transfer
            && src_ranges.size() > max_blocks_per_transfer
        {
            let tgt_split_name = format!("{tgt_name}-{pieces}");
            let src_split_name = src_name.as_ref().map(|s| format!("{s}-{pieces}"));
            let tgt_first = tgt_ranges.first(max_blocks_per_transfer);
            let src_first = src_ranges.first(max_blocks_per_transfer);

            let id = self.transfers.len();
            self.transfers.push(Transfer::new(
                id,
                tgt_split_name,
                src_split_name,
                tgt_first.clone(),
                src_first.clone(),
                style,
            ));

            tgt_ranges = tgt_ranges.subtract(&tgt_first);
            src_ranges = src_ranges.subtract(&src_first);
            pieces += 1;
        }

        if tgt_ranges.size() > 0 || src_ranges.size() > 0 {
            if tgt_ranges.is_empty() || src_ranges.is_empty() {
                return Err(Error::Invariant(
                    "remaining split ranges must both be non-empty".to_string(),
                ));
            }
            let tgt_split_name = format!("{tgt_name}-{pieces}");
            let src_split_name = src_name.as_ref().map(|s| format!("{s}-{pieces}"));
            let id = self.transfers.len();
            self.transfers.push(Transfer::new(
                id,
                tgt_split_name,
                src_split_name,
                tgt_ranges,
                src_ranges,
                style,
            ));
        }

        Ok(())
    }

    fn generate_digraph(&mut self) {
        if !self.quiet {
            println!("Generating digraph...");
        }

        let mut source_ranges: Vec<Vec<usize>> = Vec::new();
        for b in &self.transfers {
            for (s, e) in &b.src_ranges {
                let e_usize = e as usize;
                if e_usize > source_ranges.len() {
                    source_ranges.resize_with(e_usize, Vec::new);
                }
                for slot in &mut source_ranges[(s as usize)..e_usize] {
                    slot.push(b.id);
                }
            }
        }

        let n = self.transfers.len();
        let mut edges: Vec<(usize, usize, u64)> = Vec::new();
        for a_id in 0..n {
            let mut intersections = BTreeSet::new();
            for (s, e) in &self.transfers[a_id].tgt_ranges {
                let s_u = s as usize;
                let e_u = (e as usize).min(source_ranges.len());
                if s_u >= e_u {
                    continue;
                }
                for slot in &source_ranges[s_u..e_u] {
                    for &b_id in slot {
                        intersections.insert(b_id);
                    }
                }
            }

            for b_id in intersections {
                if a_id == b_id {
                    continue;
                }
                let inter = self.transfers[a_id]
                    .tgt_ranges
                    .intersect(&self.transfers[b_id].src_ranges);
                if !inter.is_empty() {
                    let size = if self.transfers[b_id].src_name.as_deref() == Some("__ZERO") {
                        0
                    } else {
                        inter.size()
                    };
                    edges.push((b_id, a_id, size));
                }
            }
        }

        for (b_id, a_id, size) in edges {
            self.transfers[b_id].goes_before.insert(a_id, size);
            self.transfers[a_id].goes_after.insert(b_id, size);
        }
    }

    fn find_vertex_sequence(&mut self) {
        if !self.quiet {
            println!("Finding vertex sequence...");
        }

        let n = self.transfers.len();
        let mut incoming: Vec<IndexMap<usize, u64>> = self
            .transfers
            .iter()
            .map(|t| t.goes_after.clone())
            .collect();
        let mut outgoing: Vec<IndexMap<usize, u64>> = self
            .transfers
            .iter()
            .map(|t| t.goes_before.clone())
            .collect();
        let mut scores: Vec<i64> = (0..n)
            .map(|i| {
                let out_sum: u64 = outgoing[i].values().sum();
                let in_sum: u64 = incoming[i].values().sum();
                (out_sum as i64) - (in_sum as i64)
            })
            .collect();

        let mut in_graph = vec![true; n];
        let mut remaining = n;
        let mut s1: Vec<usize> = Vec::new();
        let mut s2: VecDeque<usize> = VecDeque::new();

        let mut heap_ver = vec![0usize; n];
        let mut heap: BinaryHeap<(i64, usize, usize)> = BinaryHeap::with_capacity(n);
        for (id, &score) in scores.iter().enumerate() {
            heap.push((score, 0, id));
        }

        let mut sinks: BTreeSet<usize> = (0..n).filter(|&u| outgoing[u].is_empty()).collect();
        let mut sources: BTreeSet<usize> = (0..n).filter(|&u| incoming[u].is_empty()).collect();

        let adjust_score = |iu: usize,
                            delta: i64,
                            scores: &mut [i64],
                            heap_ver: &mut [usize],
                            heap: &mut BinaryHeap<(i64, usize, usize)>| {
            scores[iu] += delta;
            heap_ver[iu] += 1;
            heap.push((scores[iu], heap_ver[iu], iu));
        };

        while remaining > 0 {
            while !sinks.is_empty() {
                let mut new_sinks = BTreeSet::new();
                for u in sinks {
                    if !in_graph[u] {
                        continue;
                    }
                    s2.push_front(u);
                    in_graph[u] = false;
                    remaining -= 1;
                    let inc: Vec<usize> = incoming[u].keys().copied().collect();
                    for iu in inc {
                        if let Some(w) = outgoing[iu].shift_remove(&u) {
                            adjust_score(iu, -(w as i64), &mut scores, &mut heap_ver, &mut heap);
                        }
                        if outgoing[iu].is_empty() && in_graph[iu] {
                            new_sinks.insert(iu);
                        }
                    }
                }
                sinks = new_sinks;
            }

            while !sources.is_empty() {
                let mut new_sources = BTreeSet::new();
                for u in sources {
                    if !in_graph[u] {
                        continue;
                    }
                    s1.push(u);
                    in_graph[u] = false;
                    remaining -= 1;
                    let outg: Vec<usize> = outgoing[u].keys().copied().collect();
                    for iu in outg {
                        if let Some(w) = incoming[iu].shift_remove(&u) {
                            adjust_score(iu, w as i64, &mut scores, &mut heap_ver, &mut heap);
                        }
                        if incoming[iu].is_empty() && in_graph[iu] {
                            new_sources.insert(iu);
                        }
                    }
                }
                sources = new_sources;
            }

            if remaining == 0 {
                break;
            }

            let u = loop {
                let (_, ver, cand) = heap
                    .pop()
                    .expect("heap cannot be empty while graph has remaining vertices");
                if in_graph[cand] && ver == heap_ver[cand] {
                    break cand;
                }
            };

            s1.push(u);
            in_graph[u] = false;
            remaining -= 1;

            let outg: Vec<usize> = outgoing[u].keys().copied().collect();
            for iu in outg {
                if let Some(w) = incoming[iu].shift_remove(&u) {
                    adjust_score(iu, w as i64, &mut scores, &mut heap_ver, &mut heap);
                }
                if incoming[iu].is_empty() && in_graph[iu] {
                    sources.insert(iu);
                }
            }

            let inc: Vec<usize> = incoming[u].keys().copied().collect();
            for iu in inc {
                if let Some(w) = outgoing[iu].shift_remove(&u) {
                    adjust_score(iu, -(w as i64), &mut scores, &mut heap_ver, &mut heap);
                }
                if outgoing[iu].is_empty() && in_graph[iu] {
                    sinks.insert(iu);
                }
            }
        }

        let mut new_order = Vec::with_capacity(n);
        for id in s1.into_iter().chain(s2) {
            self.transfers[id].order = new_order.len();
            new_order.push(id);
        }
        self.transfer_order = new_order;
    }

    fn remove_backward_edges(&mut self) {
        if !self.quiet {
            println!("Removing backward edges...");
        }
        let mut in_order = 0usize;
        let mut out_of_order = 0usize;
        let mut lost_source = 0u64;

        let order = self.transfer_order.clone();
        for xf_id in order {
            let size = self.transfers[xf_id].src_ranges.size();
            let goes_before: Vec<usize> = self.transfers[xf_id].goes_before.keys().copied().collect();
            let xf_order = self.transfers[xf_id].order;

            for u_id in goes_before {
                let u_order = self.transfers[u_id].order;
                if xf_order < u_order {
                    in_order += 1;
                } else {
                    out_of_order += 1;
                    let u_tgt = self.transfers[u_id].tgt_ranges.clone();
                    let xf = &mut self.transfers[xf_id];
                    xf.src_ranges = xf.src_ranges.subtract(&u_tgt);
                    xf.intact = false;
                }
            }

            let xf = &mut self.transfers[xf_id];
            if xf.style == TransferStyle::Diff && xf.src_ranges.is_empty() {
                xf.style = TransferStyle::New;
            }
            lost_source += size - xf.src_ranges.size();
        }

        if !self.quiet {
            let total = in_order + out_of_order;
            let pct = if total > 0 {
                (out_of_order as f64) * 100.0 / (total as f64)
            } else {
                0.0
            };
            println!(
                "  {}/{} dependencies ({:.2}%) were violated; {} source blocks removed.",
                out_of_order, total, pct, lost_source
            );
        }
    }

    fn reverse_backward_edges(&mut self) -> Result<()> {
        if !self.quiet {
            println!("Reversing backward edges...");
        }
        let mut in_order = 0usize;
        let mut out_of_order = 0usize;
        let mut stashes = 0usize;
        let mut stash_size = 0u64;

        let order = self.transfer_order.clone();
        for xf_id in order {
            let goes_before: Vec<usize> = self.transfers[xf_id].goes_before.keys().copied().collect();
            let xf_order = self.transfers[xf_id].order;

            for u_id in goes_before {
                let u_order = self.transfers[u_id].order;
                if xf_order < u_order {
                    in_order += 1;
                } else {
                    out_of_order += 1;
                    let overlap = self.transfers[xf_id]
                        .src_ranges
                        .intersect(&self.transfers[u_id].tgt_ranges);
                    if overlap.is_empty() {
                        return Err(Error::Invariant(
                            "expected non-empty overlap when reversing backward edge".to_string(),
                        ));
                    }

                    self.transfers[u_id]
                        .stash_before
                        .push((stashes, overlap.clone()));
                    self.transfers[xf_id]
                        .use_stash
                        .push((stashes, overlap.clone()));
                    stashes += 1;
                    stash_size += overlap.size();

                    self.transfers[xf_id].goes_before.shift_remove(&u_id);
                    self.transfers[u_id].goes_after.shift_remove(&xf_id);
                    self.transfers[xf_id].goes_after.insert(u_id, 0);
                    self.transfers[u_id].goes_before.insert(xf_id, 0);
                }
            }
        }

        if !self.quiet {
            let total = in_order + out_of_order;
            let pct = if total > 0 {
                (out_of_order as f64) * 100.0 / (total as f64)
            } else {
                0.0
            };
            println!(
                "  {}/{} dependencies ({:.2}%) were violated; {} source blocks stashed.",
                out_of_order, total, pct, stash_size
            );
        }
        Ok(())
    }

    fn improve_vertex_sequence(&mut self) -> Result<()> {
        if !self.quiet {
            println!("Improving vertex order...");
        }

        let n = self.transfers.len();
        let mut incoming: Vec<IndexMap<usize, u64>> = self
            .transfers
            .iter()
            .map(|t| t.goes_after.clone())
            .collect();
        let outgoing: Vec<IndexMap<usize, u64>> = self
            .transfers
            .iter()
            .map(|t| t.goes_before.clone())
            .collect();

        let mut heap: BinaryHeap<Reverse<(i64, usize, usize)>> = BinaryHeap::new();
        for (id, xf) in self.transfers.iter().enumerate() {
            if incoming[id].is_empty() {
                heap.push(Reverse((xf.net_stash_change(), xf.order, id)));
            }
        }

        let mut new_order = Vec::with_capacity(n);
        while let Some(Reverse((_, _, xf_id))) = heap.pop() {
            new_order.push(xf_id);
            for &u_id in outgoing[xf_id].keys() {
                incoming[u_id].shift_remove(&xf_id);
                if incoming[u_id].is_empty() {
                    let u = &self.transfers[u_id];
                    heap.push(Reverse((u.net_stash_change(), u.order, u_id)));
                }
            }
        }

        if new_order.len() != n {
            return Err(Error::Invariant(
                "cycle detected during improve_vertex_sequence".to_string(),
            ));
        }

        for (i, &id) in new_order.iter().enumerate() {
            self.transfers[id].order = i;
        }
        self.transfer_order = new_order;
        Ok(())
    }

    fn revise_stash_size(&mut self) -> Result<()> {
        if !self.quiet {
            println!("Revising stash size...");
        }
        let cache_size = self.options.cache_size.expect("checked before call");
        let stash_threshold = self.options.stash_threshold;
        let max_allowed = (cache_size as f64) * stash_threshold / (self.tgt.blocksize() as f64);

        // stash_id -> (sr, def_cmd_id, use_cmd_id)
        let mut stashes: HashMap<usize, (RangeSet, usize, Option<usize>)> = HashMap::new();
        for &xf_id in &self.transfer_order {
            for (idx, sr) in &self.transfers[xf_id].stash_before {
                stashes.insert(*idx, (sr.clone(), xf_id, None));
            }
            for (idx, _) in &self.transfers[xf_id].use_stash {
                if let Some(entry) = stashes.get_mut(idx) {
                    entry.2 = Some(xf_id);
                }
            }
        }

        let mut stashed_blocks: u64 = 0;
        let mut new_blocks: u64 = 0;

        let order = self.transfer_order.clone();
        for xf_id in order {
            let mut replaced_cmds = Vec::new();

            let stash_before = self.transfers[xf_id].stash_before.clone();
            for (idx, sr) in stash_before {
                if ((stashed_blocks + sr.size()) as f64) > max_allowed {
                    if let Some((_, _, Some(use_cmd_id))) = stashes.get(&idx) {
                        replaced_cmds.push(*use_cmd_id);
                        if !self.quiet {
                            println!(
                                "{:10}  {:>9}  {}",
                                sr.size(),
                                "explicit",
                                self.transfers[*use_cmd_id]
                            );
                        }
                    }
                } else {
                    stashed_blocks += sr.size();
                }
            }

            for (_, sr) in &self.transfers[xf_id].use_stash {
                stashed_blocks = stashed_blocks.saturating_sub(sr.size());
            }

            if self.transfers[xf_id].style == TransferStyle::Diff && self.version >= 3 {
                let xf = &self.transfers[xf_id];
                if xf.src_ranges.overlaps(&xf.tgt_ranges)
                    && ((stashed_blocks + xf.src_ranges.size()) as f64) > max_allowed
                {
                    replaced_cmds.push(xf_id);
                    if !self.quiet {
                        println!("{:10}  {:>9}  {}", xf.src_ranges.size(), "implicit", xf);
                    }
                }
            }

            for cmd_id in replaced_cmds {
                let use_stash = self.transfers[cmd_id].use_stash.clone();
                for (idx, sr) in use_stash {
                    if let Some((_, def_cmd_id, _)) = stashes.get(&idx) {
                        let def_cmd = &mut self.transfers[*def_cmd_id];
                        if let Some(pos) = def_cmd
                            .stash_before
                            .iter()
                            .position(|(i, r)| *i == idx && *r == sr)
                        {
                            def_cmd.stash_before.remove(pos);
                        }
                    }
                }
                new_blocks += self.transfers[cmd_id].tgt_ranges.size();
                self.transfers[cmd_id].convert_to_new();
            }
        }

        if !self.quiet {
            let num_of_bytes = new_blocks * self.tgt.blocksize();
            println!(
                "  Total {} blocks ({} bytes) are packed as new blocks due to insufficient cache size.",
                new_blocks, num_of_bytes
            );
        }
        Ok(())
    }

    fn assert_sequence_good(&self) -> Result<()> {
        let total_blocks = self.tgt.total_blocks() as usize;
        let mut touched = vec![0u8; total_blocks];

        for &xf_id in &self.transfer_order {
            let xf = &self.transfers[xf_id];
            let mut x = xf.src_ranges.clone();
            if self.version >= 2 {
                for (_, sr) in &xf.use_stash {
                    x = x.subtract(sr);
                }
            }

            for (s, e) in &x {
                let start = s as usize;
                let end = (e as usize).min(total_blocks);
                if start < end {
                    for (i, &val) in touched[start..end].iter().enumerate() {
                        if val != 0 {
                            return Err(Error::Invariant(format!(
                                "block {} read after being written in transfer {}",
                                start + i,
                                xf
                            )));
                        }
                    }
                }
            }

            for (s, e) in &xf.tgt_ranges {
                let start = s as usize;
                let end = e as usize;
                for (i, slot) in touched[start..end].iter_mut().enumerate() {
                    if *slot != 0 {
                        return Err(Error::Invariant(format!(
                            "block {} written twice in transfer {}",
                            start + i,
                            xf
                        )));
                    }
                    *slot = 1;
                }
            }
        }

        for (s, e) in self.tgt.care_map() {
            let start = s as usize;
            let end = e as usize;
            for (i, &val) in touched[start..end].iter().enumerate() {
                if val != 1 {
                    return Err(Error::Invariant(format!(
                        "care_map block {} was not written",
                        start + i
                    )));
                }
            }
        }
        Ok(())
    }

    fn compute_patches(&mut self, prefix: &Path) -> Result<()> {
        if !self.quiet {
            println!("Generating block data and patches...");
        }

        struct DiffJob {
            tgt_size: usize,
            src: Vec<Vec<u8>>,
            tgt: Vec<Vec<u8>>,
            xf_id: usize,
            patch_num: usize,
            use_imgdiff: bool,
            tgt_name: String,
            src_name: String,
        }

        let mut diff_q: Vec<DiffJob> = Vec::new();
        let mut patch_num = 0usize;

        let new_path = with_suffix(prefix, ".new.dat");
        {
            let new_file = File::create(&new_path)?;
            let mut new_writer = BufWriter::with_capacity(4 * 1024 * 1024, new_file);

            for &xf_id in &self.transfer_order {
                let style = self.transfers[xf_id].style;
                match style {
                    TransferStyle::Zero => {}
                    TransferStyle::New => {
                        let ranges = self.transfers[xf_id].tgt_ranges.clone();
                        self.tgt.stream_range_set(&ranges, &mut |piece| {
                            new_writer.write_all(piece)?;
                            Ok(())
                        })?;
                    }
                    TransferStyle::Diff => {
                        let src_ranges = self.transfers[xf_id].src_ranges.clone();
                        let tgt_ranges = self.transfers[xf_id].tgt_ranges.clone();
                        let src_pieces = self.src.read_range_set(&src_ranges)?;
                        let tgt_pieces = self.tgt.read_range_set(&tgt_ranges)?;

                        let mut src_sha1 = Sha1::new();
                        for p in &src_pieces {
                            src_sha1.update(p);
                        }
                        let mut tgt_sha1 = Sha1::new();
                        let mut tgt_size = 0usize;
                        for p in &tgt_pieces {
                            tgt_sha1.update(p);
                            tgt_size += p.len();
                        }

                        if src_sha1.finalize() == tgt_sha1.finalize() {
                            self.transfers[xf_id].style = TransferStyle::Move;
                        } else {
                            let xf = &mut self.transfers[xf_id];
                            let is_zip = xf
                                .tgt_name
                                .rsplit('.')
                                .next()
                                .map(|ext| {
                                    matches!(
                                        ext.to_ascii_lowercase().as_str(),
                                        "apk" | "jar" | "zip"
                                    )
                                })
                                .unwrap_or(false);
                            let use_imgdiff = !self.disable_imgdiff && xf.intact && is_zip;
                            xf.style = if use_imgdiff {
                                TransferStyle::Imgdiff
                            } else {
                                TransferStyle::Bsdiff
                            };
                            let tgt_name = xf.tgt_name.clone();
                            let src_name = xf.src_name.clone().unwrap_or_default();
                            diff_q.push(DiffJob {
                                tgt_size,
                                src: src_pieces,
                                tgt: tgt_pieces,
                                xf_id,
                                patch_num,
                                use_imgdiff,
                                tgt_name,
                                src_name,
                            });
                            patch_num += 1;
                        }
                    }
                    _ => {
                        return Err(Error::Invariant(format!(
                            "unexpected transfer style {}",
                            style.as_str()
                        )));
                    }
                }
            }

            new_writer.flush()?;
        }

        let mut patches: Vec<Option<(Vec<u8>, usize)>> = vec![None; patch_num];
        if !diff_q.is_empty() {
            if !self.quiet {
                if self.threads > 1 {
                    println!("Computing patches (using {} threads)...", self.threads);
                } else {
                    println!("Computing patches...");
                }
            }
            diff_q.sort_by_key(|j| j.tgt_size);

            let queue_mutex = Mutex::new(diff_q);
            let patches_mutex = Mutex::new(&mut patches);
            let quiet = self.quiet;

            std::thread::scope(|s| -> Result<()> {
                let mut handles = Vec::with_capacity(self.threads);
                for _ in 0..self.threads {
                    handles.push(s.spawn(|| -> Result<()> {
                        loop {
                            let job = {
                                let mut q = queue_mutex.lock().unwrap();
                                q.pop()
                            };
                            let Some(job) = job else {
                                return Ok(());
                            };

                            let patch = compute_patch(&job.src, &job.tgt, job.use_imgdiff)?;
                            let size = patch.len();
                            let style_str = if job.use_imgdiff { "imgdiff" } else { "bsdiff" };

                            {
                                let mut p = patches_mutex.lock().unwrap();
                                p[job.patch_num] = Some((patch, job.xf_id));
                                if !quiet {
                                    let label = if job.tgt_name == job.src_name {
                                        job.tgt_name.clone()
                                    } else {
                                        format!("{} (from {})", job.tgt_name, job.src_name)
                                    };
                                    let pct = if job.tgt_size > 0 {
                                        (size as f64) * 100.0 / (job.tgt_size as f64)
                                    } else {
                                        0.0
                                    };
                                    println!(
                                        "{:10} {:10} ({:6.2}%) {:>7} {}",
                                        size, job.tgt_size, pct, style_str, label
                                    );
                                }
                            }
                        }
                    }));
                }

                for h in handles {
                    h.join().map_err(|_| {
                        Error::ExternalDiffFailed("patch worker thread panicked".to_string())
                    })??;
                }
                Ok(())
            })?;
        }

        let patch_path = with_suffix(prefix, ".patch.dat");
        let patch_file = File::create(&patch_path)?;
        let mut patch_writer = BufWriter::with_capacity(1024 * 1024, patch_file);
        let mut pos = 0u64;
        for entry in patches {
            let (patch, xf_id) = entry.expect("all patch slots filled");
            self.transfers[xf_id].patch_start = pos;
            self.transfers[xf_id].patch_len = patch.len() as u64;
            patch_writer.write_all(&patch)?;
            pos += patch.len() as u64;
        }
        patch_writer.flush()?;

        Ok(())
    }

    fn hash_src_blocks(&mut self, ranges: &RangeSet) -> Result<String> {
        let mut hasher = Sha1::new();
        self.src.stream_range_set(ranges, &mut |piece| {
            hasher.update(piece);
            Ok(())
        })?;
        Ok(hex_encode(&hasher.finalize()))
    }

    fn hash_tgt_blocks(&mut self, ranges: &RangeSet) -> Result<String> {
        let mut hasher = Sha1::new();
        self.tgt.stream_range_set(ranges, &mut |piece| {
            hasher.update(piece);
            Ok(())
        })?;
        Ok(hex_encode(&hasher.finalize()))
    }

    fn write_transfers(&mut self, prefix: &Path) -> Result<()> {
        fn write_transfers_zero(out: &mut Vec<String>, mut to_zero: RangeSet) -> Result<u64> {
            let zero_blocks_limit = 1024u64;
            let mut total = 0u64;
            while to_zero.size() > 0 {
                let zero_blocks = to_zero.first(zero_blocks_limit);
                out.push(format!("zero {}\n", zero_blocks.to_string_raw()?));
                total += zero_blocks.size();
                to_zero = to_zero.subtract(&zero_blocks);
            }
            Ok(total)
        }

        let mut out: Vec<String> = Vec::new();
        let mut total: u64 = 0;

        let mut stash_ids: HashMap<usize, usize> = HashMap::new();
        let mut stash_refcounts: HashMap<String, usize> = HashMap::new();
        let mut stashed_blocks: u64 = 0;
        let mut max_stashed_blocks: u64 = 0;

        let mut free_stash_ids: BinaryHeap<Reverse<usize>> = BinaryHeap::new();
        let mut next_stash_id: usize = 0;

        let order = self.transfer_order.clone();
        for xf_id in order {
            let xf = self.transfers[xf_id].clone();

            if self.version < 2 && (!xf.stash_before.is_empty() || !xf.use_stash.is_empty()) {
                return Err(Error::Invariant(
                    "stash commands are not supported in version < 2".to_string(),
                ));
            }

            for (s, sr) in &xf.stash_before {
                let sid = if let Some(Reverse(recycled)) = free_stash_ids.pop() {
                    recycled
                } else {
                    let id = next_stash_id;
                    next_stash_id += 1;
                    id
                };
                stash_ids.insert(*s, sid);

                if self.version == 2 {
                    stashed_blocks += sr.size();
                    out.push(format!("stash {} {}\n", sid, sr.to_string_raw()?));
                } else {
                    let sh = self.hash_src_blocks(sr)?;
                    let count = stash_refcounts.entry(sh.clone()).or_insert(0);
                    *count += 1;
                    if *count == 1 {
                        stashed_blocks += sr.size();
                        self.touched_src_ranges = self.touched_src_ranges.union(sr);
                        out.push(format!("stash {} {}\n", sh, sr.to_string_raw()?));
                    }
                }
            }

            if stashed_blocks > max_stashed_blocks {
                max_stashed_blocks = stashed_blocks;
            }

            let mut free_string: Vec<String> = Vec::new();
            let mut free_size: u64 = 0;

            let src_str = if self.version == 1 {
                if xf.src_ranges.size() > 0 {
                    xf.src_ranges.to_string_raw()?
                } else {
                    String::new()
                }
            } else {
                let size = xf.src_ranges.size();
                let mut parts: Vec<String> = vec![size.to_string()];
                let mut unstashed_src_ranges = xf.src_ranges.clone();
                let mut mapped_stashes: Vec<RangeSet> = Vec::new();

                for (s, sr) in &xf.use_stash {
                    let sid = stash_ids
                        .remove(s)
                        .ok_or_else(|| Error::Invariant(format!("missing stash id {s}")))?;
                    unstashed_src_ranges = unstashed_src_ranges.subtract(sr);
                    let sh = self.hash_src_blocks(sr)?;
                    let mapped = xf.src_ranges.map_within(sr);
                    mapped_stashes.push(mapped.clone());

                    if self.version == 2 {
                        parts.push(format!("{}:{}", sid, mapped.to_string_raw()?));
                        free_string.push(format!("free {sid}\n"));
                        free_size += mapped.size();
                    } else {
                        parts.push(format!("{}:{}", sh, mapped.to_string_raw()?));
                        let rc = stash_refcounts.get_mut(&sh).ok_or_else(|| {
                            Error::Invariant(format!("missing stash hash {sh}"))
                        })?;
                        *rc -= 1;
                        if *rc == 0 {
                            free_size += mapped.size();
                            free_string.push(format!("free {sh}\n"));
                            stash_refcounts.remove(&sh);
                        }
                    }
                    free_stash_ids.push(Reverse(sid));
                }

                if unstashed_src_ranges.size() > 0 {
                    parts.insert(1, unstashed_src_ranges.to_string_raw()?);
                    if !xf.use_stash.is_empty() {
                        let mapped_unstashed = xf.src_ranges.map_within(&unstashed_src_ranges);
                        parts.insert(2, mapped_unstashed.to_string_raw()?);
                        mapped_stashes.push(mapped_unstashed);
                        Self::assert_partition(&RangeSet::from_range(0, size), &mapped_stashes)?;
                    }
                } else {
                    parts.insert(1, "-".to_string());
                    Self::assert_partition(&RangeSet::from_range(0, size), &mapped_stashes)?;
                }

                parts.join(" ")
            };

            let tgt_size = xf.tgt_ranges.size();
            match xf.style {
                TransferStyle::New => {
                    out.push(format!("new {}\n", xf.tgt_ranges.to_string_raw()?));
                    total += tgt_size;
                }
                TransferStyle::Move => {
                    if xf.src_ranges != xf.tgt_ranges {
                        if self.version == 1 {
                            out.push(format!(
                                "move {} {}\n",
                                xf.src_ranges.to_string_raw()?,
                                xf.tgt_ranges.to_string_raw()?
                            ));
                        } else if self.version == 2 {
                            out.push(format!(
                                "move {} {}\n",
                                xf.tgt_ranges.to_string_raw()?,
                                src_str
                            ));
                        } else {
                            if xf.src_ranges.overlaps(&xf.tgt_ranges) {
                                let temp_stash_usage = stashed_blocks + xf.src_ranges.size();
                                if temp_stash_usage > max_stashed_blocks {
                                    max_stashed_blocks = temp_stash_usage;
                                }
                            }
                            self.touched_src_ranges =
                                self.touched_src_ranges.union(&xf.src_ranges);
                            let tgt_hash = self.hash_tgt_blocks(&xf.tgt_ranges)?;
                            out.push(format!(
                                "move {} {} {}\n",
                                tgt_hash,
                                xf.tgt_ranges.to_string_raw()?,
                                src_str
                            ));
                        }
                        total += tgt_size;
                    }
                }
                TransferStyle::Bsdiff | TransferStyle::Imgdiff => {
                    let style_str = xf.style.as_str();
                    if self.version == 1 {
                        out.push(format!(
                            "{} {} {} {} {}\n",
                            style_str,
                            xf.patch_start,
                            xf.patch_len,
                            xf.src_ranges.to_string_raw()?,
                            xf.tgt_ranges.to_string_raw()?
                        ));
                    } else if self.version == 2 {
                        out.push(format!(
                            "{} {} {} {} {}\n",
                            style_str,
                            xf.patch_start,
                            xf.patch_len,
                            xf.tgt_ranges.to_string_raw()?,
                            src_str
                        ));
                    } else {
                        if xf.src_ranges.overlaps(&xf.tgt_ranges) {
                            let temp_stash_usage = stashed_blocks + xf.src_ranges.size();
                            if temp_stash_usage > max_stashed_blocks {
                                max_stashed_blocks = temp_stash_usage;
                            }
                        }
                        self.touched_src_ranges = self.touched_src_ranges.union(&xf.src_ranges);
                        let src_hash = self.hash_src_blocks(&xf.src_ranges)?;
                        let tgt_hash = self.hash_tgt_blocks(&xf.tgt_ranges)?;
                        out.push(format!(
                            "{} {} {} {} {} {} {}\n",
                            style_str,
                            xf.patch_start,
                            xf.patch_len,
                            src_hash,
                            tgt_hash,
                            xf.tgt_ranges.to_string_raw()?,
                            src_str
                        ));
                    }
                    total += tgt_size;
                }
                TransferStyle::Zero => {
                    let to_zero = xf.tgt_ranges.subtract(&xf.src_ranges);
                    let written = write_transfers_zero(&mut out, to_zero.clone())?;
                    if written != to_zero.size() {
                        return Err(Error::Invariant("zero block count mismatch".to_string()));
                    }
                    total += to_zero.size();
                }
                TransferStyle::Diff => {
                    return Err(Error::Invariant(
                        "unresolved 'diff' transfer in write_transfers".to_string(),
                    ));
                }
            }

            if !free_string.is_empty() {
                out.push(free_string.concat());
                stashed_blocks -= free_size;
            }

            if self.version >= 2
                && let Some(cache_size) = self.options.cache_size
            {
                let stash_threshold = self.options.stash_threshold;
                let max_allowed = ((cache_size as f64) * stash_threshold) as u64;
                let stashed_bytes = max_stashed_blocks * self.tgt.blocksize();
                if stashed_bytes >= max_allowed {
                    return Err(Error::StashLimitExceeded {
                        stashed_bytes,
                        stashed_blocks: max_stashed_blocks,
                        block_size: self.tgt.blocksize(),
                        max_allowed,
                        cache_size,
                        threshold: stash_threshold,
                    });
                }
            }
        }

        if self.version >= 3 {
            let touched = self.touched_src_ranges.clone();
            self.touched_src_sha1 = Some(self.hash_src_blocks(&touched)?);
        }

        let extended = self.tgt.extended().clone();
        if extended.size() > 0 {
            let written = write_transfers_zero(&mut out, extended.clone())?;
            if written != extended.size() {
                return Err(Error::Invariant("extended zero count mismatch".to_string()));
            }
            total += extended.size();
        }

        let all_tgt = RangeSet::from_range(0, self.tgt.total_blocks());
        let all_tgt_minus_extended = all_tgt.subtract(self.tgt.extended());
        let new_dontcare = all_tgt_minus_extended.subtract(self.tgt.care_map());

        let erase_first = new_dontcare.subtract(&self.touched_src_ranges);
        if erase_first.size() > 0 {
            out.insert(0, format!("erase {}\n", erase_first.to_string_raw()?));
        }

        let erase_last = new_dontcare.subtract(&erase_first);
        if erase_last.size() > 0 {
            out.push(format!("erase {}\n", erase_last.to_string_raw()?));
        }

        out.insert(0, format!("{}\n", self.version));
        out.insert(1, format!("{total}\n"));
        if self.version >= 2 {
            out.insert(2, format!("{next_stash_id}\n"));
            out.insert(3, format!("{max_stashed_blocks}\n"));
        }

        let transfer_path = with_suffix(prefix, ".transfer.list");
        let f = File::create(&transfer_path)?;
        let mut writer = BufWriter::new(f);
        for line in &out {
            writer.write_all(line.as_bytes())?;
        }
        writer.flush()?;

        if self.version >= 2 {
            self.max_stashed_size = max_stashed_blocks * self.tgt.blocksize();
            if !self.quiet {
                if let Some(cache_size) = self.options.cache_size {
                    let max_allowed = (cache_size as f64) * self.options.stash_threshold;
                    println!(
                        "max stashed blocks: {}  ({} bytes), limit: {} bytes ({:.2}%)\n",
                        max_stashed_blocks,
                        self.max_stashed_size,
                        max_allowed as u64,
                        (self.max_stashed_size as f64) * 100.0 / max_allowed
                    );
                } else {
                    println!(
                        "max stashed blocks: {}  ({} bytes), limit: <unknown>\n",
                        max_stashed_blocks, self.max_stashed_size
                    );
                }
            }
        }

        Ok(())
    }

    pub fn assert_partition<'b>(
        total: &RangeSet,
        seq: impl IntoIterator<Item = &'b RangeSet>,
    ) -> Result<()> {
        let mut so_far = RangeSet::new();
        for r in seq {
            if so_far.overlaps(r) {
                return Err(Error::Invariant(
                    "file_map ranges overlap in assert_partition".to_string(),
                ));
            }
            so_far = so_far.union(r);
        }
        if &so_far != total {
            return Err(Error::Invariant(format!(
                "file_map partition {} does not equal care_map {}",
                so_far, total
            )));
        }
        Ok(())
    }
}

fn with_suffix(prefix: &Path, suffix: &str) -> std::path::PathBuf {
    let mut os = prefix.as_os_str().to_os_string();
    os.push(suffix);
    os.into()
}

fn basename(path: &str) -> &str {
    path.rsplit(['/', '\\']).next().unwrap_or(path)
}

fn replace_digit_runs_with_hash(input: &str) -> String {
    let mut out = String::with_capacity(input.len());
    let mut in_digits = false;
    for ch in input.chars() {
        if ch.is_ascii_digit() {
            if !in_digits {
                out.push('#');
                in_digits = true;
            }
        } else {
            in_digits = false;
            out.push(ch);
        }
    }
    out
}

pub fn hex_encode(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut out = String::with_capacity(bytes.len() * 2);
    for &b in bytes {
        out.push(HEX[(b >> 4) as usize] as char);
        out.push(HEX[(b & 0x0f) as usize] as char);
    }
    out
}

fn compute_patch(src: &[Vec<u8>], tgt: &[Vec<u8>], imgdiff: bool) -> Result<Vec<u8>> {
    let tmp_dir = std::env::temp_dir();
    let nonce = std::process::id();
    let tid = format!("{:?}", std::thread::current().id());
    let tag: String = tid.chars().filter(|c| c.is_ascii_alphanumeric()).collect();

    let src_path = tmp_dir.join(format!("sdatimg-src-{nonce}-{tag}.tmp"));
    let tgt_path = tmp_dir.join(format!("sdatimg-tgt-{nonce}-{tag}.tmp"));
    let patch_path = tmp_dir.join(format!("sdatimg-patch-{nonce}-{tag}.tmp"));

    struct Cleanup<'a>(&'a Path, &'a Path, &'a Path);
    impl Drop for Cleanup<'_> {
        fn drop(&mut self) {
            let _ = std::fs::remove_file(self.0);
            let _ = std::fs::remove_file(self.1);
            let _ = std::fs::remove_file(self.2);
        }
    }
    let _cleanup = Cleanup(&src_path, &tgt_path, &patch_path);

    {
        let mut f = BufWriter::new(File::create(&src_path)?);
        for p in src {
            f.write_all(p)?;
        }
        f.flush()?;
    }
    {
        let mut f = BufWriter::new(File::create(&tgt_path)?);
        for p in tgt {
            f.write_all(p)?;
        }
        f.flush()?;
    }

    let status = if imgdiff {
        Command::new("imgdiff")
            .arg("-z")
            .arg(&src_path)
            .arg(&tgt_path)
            .arg(&patch_path)
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
    } else {
        Command::new("bsdiff")
            .arg(&src_path)
            .arg(&tgt_path)
            .arg(&patch_path)
            .status()
    };

    let status = status.map_err(|e| {
        Error::ExternalDiffFailed(format!(
            "failed to run {}: {e}",
            if imgdiff { "imgdiff" } else { "bsdiff" }
        ))
    })?;

    if !status.success() {
        return Err(Error::ExternalDiffFailed(format!(
            "diff failed with status {status}"
        )));
    }

    Ok(std::fs::read(&patch_path)?)
}
