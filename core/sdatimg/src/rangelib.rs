use std::cmp::Ordering;
use std::fmt;

use crate::error::{Error, Result};

/// A sorted set of non-overlapping half-open integer ranges `[start, end)`.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct RangeSet {
    /// Stored as flattened sorted pairs `[s0, e0, s1, e1, ...]`.
    data: Vec<u64>,
    pub monotonic: bool,
}

impl RangeSet {
    pub fn new() -> Self {
        Self {
            data: Vec::new(),
            monotonic: false,
        }
    }

    pub fn from_range(start: u64, end: u64) -> Self {
        if start < end {
            Self {
                data: vec![start, end],
                monotonic: true,
            }
        } else {
            Self::new()
        }
    }

    pub fn from_data(data: Vec<u64>) -> Result<Self> {
        if !data.len().is_multiple_of(2) {
            return Err(Error::InvalidRangeSet(format!(
                "RangeSet data length must be even, got {}",
                data.len()
            )));
        }
        if data.is_empty() {
            return Ok(Self::new());
        }
        let cleaned = Self::remove_pairs(data);
        let monotonic = cleaned.windows(2).all(|w| w[0] < w[1]);
        Ok(Self {
            data: cleaned,
            monotonic,
        })
    }

    /// Parse a space-separated list of blocks and inclusive ranges, e.g. `"10-20 30 35-40"`.
    pub fn parse(text: &str) -> Result<Self> {
        let mut data = Vec::new();
        let mut last: i128 = -1;
        let mut monotonic = true;

        for token in text.split_whitespace() {
            if let Some((s_str, e_str)) = token.split_once('-') {
                let s: u64 = s_str.parse().map_err(|_| {
                    Error::InvalidRangeSet(format!("invalid range start in '{token}'"))
                })?;
                let e: u64 = e_str.parse().map_err(|_| {
                    Error::InvalidRangeSet(format!("invalid range end in '{token}'"))
                })?;
                if s > e {
                    return Err(Error::InvalidRangeSet(format!(
                        "range start {s} > end {e} in '{token}'"
                    )));
                }
                data.push(s);
                data.push(e + 1);
                if last <= s as i128 && s <= e {
                    last = e as i128;
                } else {
                    monotonic = false;
                }
            } else {
                let s: u64 = token.parse().map_err(|_| {
                    Error::InvalidRangeSet(format!("invalid block number '{token}'"))
                })?;
                data.push(s);
                data.push(s + 1);
                if last <= s as i128 {
                    last = (s + 1) as i128;
                } else {
                    monotonic = false;
                }
            }
        }

        if data.is_empty() {
            return Ok(Self::new());
        }

        data.sort_unstable();
        let cleaned = Self::remove_pairs(data);
        Ok(Self {
            data: cleaned,
            monotonic,
        })
    }

    /// Parse raw transfer.list rangeset string `"count,s0,e0,s1,e1,..."` preserving pair order.
    pub fn parse_raw_pairs(src: &str) -> Result<Vec<(u64, u64)>> {
        let trimmed = src.trim();
        let parts: Vec<&str> = trimmed.split(',').collect();
        let mut nums = Vec::with_capacity(parts.len());
        for item in &parts {
            let v: u64 = item.trim().parse().map_err(|_| {
                Error::InvalidRangeSet(format!(
                    "Error on parsing following data to rangeset:\n{src}"
                ))
            })?;
            nums.push(v);
        }
        if nums.is_empty() || nums.len() != (nums[0] as usize) + 1 || !nums[0].is_multiple_of(2) {
            return Err(Error::InvalidRangeSet(format!(
                "Error on parsing following data to rangeset:\n{src}"
            )));
        }
        let mut pairs = Vec::with_capacity((nums.len() - 1) / 2);
        for &[s, e] in nums[1..].as_chunks::<2>().0 {
            if s > e {
                return Err(Error::InvalidRangeSet(format!(
                    "Invalid range ({s}, {e}) in rangeset:\n{src}"
                )));
            }
            pairs.push((s, e));
        }
        Ok(pairs)
    }

    fn remove_pairs(source: Vec<u64>) -> Vec<u64> {
        let mut out = Vec::with_capacity(source.len());
        let mut last: Option<u64> = None;
        for i in source {
            if Some(i) == last {
                last = None;
            } else {
                if let Some(prev) = last {
                    out.push(prev);
                }
                last = Some(i);
            }
        }
        if let Some(prev) = last {
            out.push(prev);
        }
        out
    }

    pub fn is_empty(&self) -> bool {
        self.data.is_empty()
    }

    pub fn data(&self) -> &[u64] {
        &self.data
    }

    pub fn iter(&self) -> RangeSetIter<'_> {
        RangeSetIter {
            slice: &self.data,
            idx: 0,
        }
    }

    pub fn to_range_string(&self) -> String {
        let mut out = Vec::with_capacity(self.data.len() / 2);
        for (s, e) in self.iter() {
            if e == s + 1 {
                out.push(s.to_string());
            } else {
                out.push(format!("{s}-{}", e - 1));
            }
        }
        out.join(" ")
    }

    pub fn to_string_raw(&self) -> Result<String> {
        if self.data.is_empty() {
            return Err(Error::InvalidRangeSet(
                "cannot format empty RangeSet as raw".to_string(),
            ));
        }
        let mut s = self.data.len().to_string();
        for v in &self.data {
            s.push(',');
            s.push_str(&v.to_string());
        }
        Ok(s)
    }

    /// Merge sweep events from `self` and `other`, ordering closing edges (`-1`)
    /// before opening edges (`+1`) at identical coordinates.
    fn sweep_merge<'a>(&'a self, other: &'a RangeSet, other_flip: bool) -> SweepMerge<'a> {
        SweepMerge {
            a: &self.data,
            ai: 0,
            b: &other.data,
            bi: 0,
            other_flip,
        }
    }

    pub fn union(&self, other: &RangeSet) -> RangeSet {
        let mut out = Vec::new();
        let mut z: i32 = 0;
        for (p, d) in self.sweep_merge(other, false) {
            if (z == 0 && d == 1) || (z == 1 && d == -1) {
                out.push(p);
            }
            z += d as i32;
        }
        RangeSet::from_data(out).expect("union produces valid even-length data")
    }

    pub fn intersect(&self, other: &RangeSet) -> RangeSet {
        let mut out = Vec::new();
        let mut z: i32 = 0;
        for (p, d) in self.sweep_merge(other, false) {
            if (z == 1 && d == 1) || (z == 2 && d == -1) {
                out.push(p);
            }
            z += d as i32;
        }
        RangeSet::from_data(out).expect("intersect produces valid even-length data")
    }

    pub fn subtract(&self, other: &RangeSet) -> RangeSet {
        let mut out = Vec::new();
        let mut z: i32 = 0;
        for (p, d) in self.sweep_merge(other, true) {
            if (z == 0 && d == 1) || (z == 1 && d == -1) {
                out.push(p);
            }
            z += d as i32;
        }
        RangeSet::from_data(out).expect("subtract produces valid even-length data")
    }

    pub fn overlaps(&self, other: &RangeSet) -> bool {
        let mut z: i32 = 0;
        for (_, d) in self.sweep_merge(other, false) {
            if (z == 1 && d == 1) || (z == 2 && d == -1) {
                return true;
            }
            z += d as i32;
        }
        false
    }

    pub fn size(&self) -> u64 {
        let mut total: u64 = 0;
        for (s, e) in self.iter() {
            total += e - s;
        }
        total
    }

    /// Map `other` (a subset of `self`) into contiguous coordinates starting at 0.
    pub fn map_within(&self, other: &RangeSet) -> RangeSet {
        let mut out = Vec::new();
        let mut offset: u64 = 0;
        let mut start: Option<u64> = None;

        let mut ai = 0;
        let mut bi = 0;
        while ai < self.data.len() || bi < other.data.len() {
            let take_a = match (ai < self.data.len(), bi < other.data.len()) {
                (true, true) => {
                    let pa = self.data[ai];
                    let da: i8 = if ai % 2 == 0 { -5 } else { 5 };
                    let pb = other.data[bi];
                    let db: i8 = if bi % 2 == 0 { -1 } else { 1 };
                    (pa, da) <= (pb, db)
                }
                (true, false) => true,
                (false, true) => false,
                (false, false) => break,
            };

            if take_a {
                let p = self.data[ai];
                let d = if ai % 2 == 0 { -5 } else { 5 };
                ai += 1;
                if d == -5 {
                    start = Some(p);
                } else {
                    let s = start.expect("self interval end without start");
                    offset += p - s;
                    start = None;
                }
            } else {
                let p = other.data[bi];
                bi += 1;
                let s = start.expect("other must be subset of self in map_within");
                out.push(offset + p - s);
            }
        }

        RangeSet::from_data(out).expect("map_within produces valid even-length data")
    }

    pub fn extend(&self, n: u64) -> RangeSet {
        let mut out = self.clone();
        for (s, e) in self.iter() {
            let s1 = s.saturating_sub(n);
            let e1 = e.saturating_add(n);
            out = out.union(&RangeSet::from_range(s1, e1));
        }
        out
    }

    pub fn first(&self, mut n: u64) -> RangeSet {
        if self.size() <= n {
            return self.clone();
        }
        let mut out = Vec::new();
        for (s, e) in self.iter() {
            if n == 0 {
                break;
            }
            if e - s >= n {
                out.push(s);
                out.push(s + n);
                break;
            } else {
                out.push(s);
                out.push(e);
                n -= e - s;
            }
        }
        RangeSet::from_data(out).expect("first produces valid even-length data")
    }
}

impl fmt::Display for RangeSet {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        if self.data.is_empty() {
            f.write_str("empty")
        } else {
            f.write_str(&self.to_range_string())
        }
    }
}

impl<'a> IntoIterator for &'a RangeSet {
    type Item = (u64, u64);
    type IntoIter = RangeSetIter<'a>;

    fn into_iter(self) -> Self::IntoIter {
        self.iter()
    }
}

pub struct RangeSetIter<'a> {
    slice: &'a [u64],
    idx: usize,
}

impl<'a> Iterator for RangeSetIter<'a> {
    type Item = (u64, u64);

    fn next(&mut self) -> Option<Self::Item> {
        if self.idx + 1 < self.slice.len() {
            let s = self.slice[self.idx];
            let e = self.slice[self.idx + 1];
            self.idx += 2;
            Some((s, e))
        } else {
            None
        }
    }
}

struct SweepMerge<'a> {
    a: &'a [u64],
    ai: usize,
    b: &'a [u64],
    bi: usize,
    other_flip: bool,
}

impl<'a> Iterator for SweepMerge<'a> {
    type Item = (u64, i8);

    fn next(&mut self) -> Option<Self::Item> {
        match (self.ai < self.a.len(), self.bi < self.b.len()) {
            (true, true) => {
                let pa = self.a[self.ai];
                let da: i8 = if self.ai.is_multiple_of(2) { 1 } else { -1 };
                let pb = self.b[self.bi];
                let mut db: i8 = if self.bi.is_multiple_of(2) { 1 } else { -1 };
                if self.other_flip {
                    db = -db;
                }
                match (pa, da).cmp(&(pb, db)) {
                    Ordering::Less | Ordering::Equal => {
                        self.ai += 1;
                        Some((pa, da))
                    }
                    Ordering::Greater => {
                        self.bi += 1;
                        Some((pb, db))
                    }
                }
            }
            (true, false) => {
                let pa = self.a[self.ai];
                let da: i8 = if self.ai.is_multiple_of(2) { 1 } else { -1 };
                self.ai += 1;
                Some((pa, da))
            }
            (false, true) => {
                let pb = self.b[self.bi];
                let mut db: i8 = if self.bi.is_multiple_of(2) { 1 } else { -1 };
                if self.other_flip {
                    db = -db;
                }
                self.bi += 1;
                Some((pb, db))
            }
            (false, false) => None,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_rangelib_doctests() {
        assert_eq!(
            RangeSet::parse("10-19")
                .unwrap()
                .union(&RangeSet::parse("20-29").unwrap())
                .to_range_string(),
            "10-29"
        );
        assert_eq!(
            RangeSet::parse("10-19 30-34")
                .unwrap()
                .union(&RangeSet::parse("18-29").unwrap())
                .to_range_string(),
            "10-34"
        );
        assert_eq!(
            RangeSet::parse("10-19 30-34")
                .unwrap()
                .union(&RangeSet::parse("22 32").unwrap())
                .to_range_string(),
            "10-19 22 30-34"
        );
        assert_eq!(
            RangeSet::parse("10-19 30-34")
                .unwrap()
                .intersect(&RangeSet::parse("18-32").unwrap())
                .to_range_string(),
            "18-19 30-32"
        );
        assert!(RangeSet::parse("10-19 30-34")
            .unwrap()
            .intersect(&RangeSet::parse("22-28").unwrap())
            .is_empty());
        assert_eq!(
            RangeSet::parse("10-19 30-34")
                .unwrap()
                .subtract(&RangeSet::parse("18-32").unwrap())
                .to_range_string(),
            "10-17 33-34"
        );
        assert_eq!(
            RangeSet::parse("10-19 30-34")
                .unwrap()
                .subtract(&RangeSet::parse("22-28").unwrap())
                .to_range_string(),
            "10-19 30-34"
        );
        assert!(RangeSet::parse("10-19 30-34")
            .unwrap()
            .overlaps(&RangeSet::parse("18-32").unwrap()));
        assert!(!RangeSet::parse("10-19 30-34")
            .unwrap()
            .overlaps(&RangeSet::parse("22-28").unwrap()));
        assert_eq!(RangeSet::parse("10-19 30-34").unwrap().size(), 15);
        assert_eq!(
            RangeSet::parse("0-9")
                .unwrap()
                .map_within(&RangeSet::parse("3-4").unwrap())
                .to_range_string(),
            "3-4"
        );
        assert_eq!(
            RangeSet::parse("10-19")
                .unwrap()
                .map_within(&RangeSet::parse("13-14").unwrap())
                .to_range_string(),
            "3-4"
        );
        assert_eq!(
            RangeSet::parse("10-19 30-39")
                .unwrap()
                .map_within(&RangeSet::parse("17-19 30-32").unwrap())
                .to_range_string(),
            "7-12"
        );
        assert_eq!(
            RangeSet::parse("10-19 30-39")
                .unwrap()
                .map_within(&RangeSet::parse("12-13 17-19 30-32").unwrap())
                .to_range_string(),
            "2-3 7-12"
        );
        assert_eq!(
            RangeSet::parse("0-9").unwrap().extend(1).to_range_string(),
            "0-10"
        );
        assert_eq!(
            RangeSet::parse("10-19").unwrap().extend(15).to_range_string(),
            "0-34"
        );
        assert_eq!(
            RangeSet::parse("10-19 30-39")
                .unwrap()
                .extend(4)
                .to_range_string(),
            "6-23 26-43"
        );
        assert_eq!(
            RangeSet::parse("10-19 30-39")
                .unwrap()
                .extend(10)
                .to_range_string(),
            "0-49"
        );
        assert_eq!(
            RangeSet::parse("0-9").unwrap().first(1).to_range_string(),
            "0"
        );
        assert_eq!(
            RangeSet::parse("10-19").unwrap().first(5).to_range_string(),
            "10-14"
        );
        assert_eq!(
            RangeSet::parse("10-19").unwrap().first(15).to_range_string(),
            "10-19"
        );
        assert_eq!(
            RangeSet::parse("10-19 30-39")
                .unwrap()
                .first(3)
                .to_range_string(),
            "10-12"
        );
        assert_eq!(
            RangeSet::parse("10-19 30-39")
                .unwrap()
                .first(15)
                .to_range_string(),
            "10-19 30-34"
        );
        assert_eq!(
            RangeSet::parse("10-19 30-39")
                .unwrap()
                .first(30)
                .to_range_string(),
            "10-19 30-39"
        );
        assert!(RangeSet::parse("0-9").unwrap().first(0).is_empty());
        assert_eq!(
            RangeSet::parse("10-19").unwrap().to_string_raw().unwrap(),
            "2,10,20"
        );
        assert!(RangeSet::parse("10-20 30").unwrap().monotonic);
        assert!(!RangeSet::parse("15-20 30 10-14").unwrap().monotonic);
    }
}
