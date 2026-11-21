// SPDX-License-Identifier: MPL-2.0
//! Precision SIMD avatar resizer and active-display cache sync.

use fast_image_resize::images::Image;
use fast_image_resize::{FilterType, PixelType, ResizeAlg, ResizeOptions, Resizer};
use image::codecs::png::{CompressionType, FilterType as PngFilterType, PngEncoder};
use image::{ColorType, ImageEncoder, ImageReader};
use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use rayon::prelude::*;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::io::Cursor;
use std::path::{Path, PathBuf};
use std::time::{Instant, UNIX_EPOCH};

const ENGINE_VERSION: u32 = 2;
const META_FILENAME: &str = ".active_meta.json";
const MIN_TARGET_DIM: u32 = 32;
const MAX_TARGET_DIM: u32 = 1024;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
struct SourceStamp {
    len: u64,
    mtime_ns: u128,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct ActiveMeta {
    engine_version: u32,
    target_dim: u32,
    #[serde(default = "default_true")]
    crop: bool,
    screen_width: u32,
    screen_height: u32,
    dpr_milli: u32,
    sources: BTreeMap<String, SourceStamp>,
}

fn default_true() -> bool {
    true
}

#[derive(Debug, Serialize)]
struct SyncReport {
    target_dim: u32,
    screen_width: u32,
    screen_height: u32,
    phys_width: u32,
    phys_height: u32,
    dpr: f64,
    processed: usize,
    reused: usize,
    removed: usize,
    elapsed_ms: u64,
}

struct SourceEntry {
    out_name: String,
    path: PathBuf,
    stamp: SourceStamp,
    is_png: bool,
}

/// Computes an exact square avatar pixel target from the active display metrics.
///
/// Accounts for both UI 2x supersampling (`max_ui_logical * 2 * dpr`) and
/// physical screen resolution (`min(phys_w, phys_h) * 128 / 720`) so avatars
/// remain crisp from 720p up to 4K/8K and tall mobile/portrait displays.
pub(crate) fn compute_target_dim(
    screen_width: u32,
    screen_height: u32,
    dpr: f64,
    max_ui_logical: u32,
) -> (u32, u32, u32, f64) {
    let safe_dpr = if dpr.is_finite() && dpr > 0.0 { dpr } else { 1.0 };
    let phys_w = ((screen_width.max(1) as f64) * safe_dpr).round().max(1.0);
    let phys_h = ((screen_height.max(1) as f64) * safe_dpr).round().max(1.0);
    let short_edge = phys_w.min(phys_h);

    let clamped_ui = max_ui_logical.max(16) as f64;
    let ui_supersampled = clamped_ui * 2.0 * safe_dpr;
    let screen_scaled = short_edge * ((clamped_ui * 2.0) / 720.0);
    let raw = ui_supersampled.max(screen_scaled).ceil() as u32;
    let even = (raw.saturating_add(1)) & !1;
    let target = even.clamp(MIN_TARGET_DIM, MAX_TARGET_DIM);
    (target, phys_w as u32, phys_h as u32, safe_dpr)
}

fn read_stamp(path: &Path) -> std::io::Result<SourceStamp> {
    let meta = fs::metadata(path)?;
    let mtime_ns = meta
        .modified()
        .ok()
        .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    Ok(SourceStamp {
        len: meta.len(),
        mtime_ns,
    })
}

fn is_supported_avatar_ext(ext: &str) -> bool {
    matches!(ext, "png" | "jpg" | "jpeg" | "webp")
}

fn collect_sources(source_dir: &Path) -> Result<Vec<SourceEntry>, String> {
    let entries = fs::read_dir(source_dir)
        .map_err(|e| format!("Cannot read avatar source dir {}: {e}", source_dir.display()))?;
    let mut by_stem: BTreeMap<String, SourceEntry> = BTreeMap::new();
    for entry in entries.flatten() {
        let path = entry.path();
        let Ok(ft) = entry.file_type() else {
            continue;
        };
        if !ft.is_file() {
            continue;
        }
        let Some(name) = path.file_name().and_then(|n| n.to_str()) else {
            continue;
        };
        if name.starts_with('.') || name.ends_with(".tmp") {
            continue;
        }
        let Some(ext) = path.extension().and_then(|e| e.to_str()) else {
            continue;
        };
        let ext_lower = ext.to_ascii_lowercase();
        if !is_supported_avatar_ext(&ext_lower) {
            continue;
        }
        let Some(stem) = path.file_stem().and_then(|s| s.to_str()) else {
            continue;
        };
        let is_png = ext_lower == "png";
        // Prefer .png when multiple extensions share a stem.
        if let Some(existing) = by_stem.get(stem) {
            if existing.is_png && !is_png {
                continue;
            }
        }
        let Ok(stamp) = read_stamp(&path) else {
            continue;
        };
        by_stem.insert(
            stem.to_owned(),
            SourceEntry {
                out_name: format!("{stem}.png"),
                path,
                stamp,
                is_png,
            },
        );
    }
    Ok(by_stem.into_values().collect())
}

fn atomic_write(path: &Path, data: &[u8]) -> std::io::Result<()> {
    let tmp_name = format!(
        ".{}.tmp.{}",
        path.file_name()
            .and_then(|n| n.to_str())
            .unwrap_or("avatar"),
        std::process::id()
    );
    let tmp_path = path.with_file_name(tmp_name);
    fs::write(&tmp_path, data)?;
    if let Err(err) = fs::rename(&tmp_path, path) {
        let _ = fs::remove_file(&tmp_path);
        return Err(err);
    }
    Ok(())
}

fn resize_one_avatar(
    entry: &SourceEntry,
    target_dim: u32,
    active_dir: &Path,
    crop: bool,
) -> Result<(), String> {
    let bytes = fs::read(&entry.path)
        .map_err(|e| format!("Failed reading {}: {e}", entry.path.display()))?;
    let decoded = ImageReader::new(Cursor::new(&bytes))
        .with_guessed_format()
        .map_err(|e| format!("Failed guessing format for {}: {e}", entry.path.display()))?
        .decode()
        .map_err(|e| format!("Failed decoding {}: {e}", entry.path.display()))?;

    let rgba = decoded.to_rgba8();
    let src_w = rgba.width();
    let src_h = rgba.height();
    let out_path = active_dir.join(&entry.out_name);

    let (dst_w, dst_h) = if crop {
        let side = src_w.min(src_h).max(1);
        let dim = target_dim.min(side).max(1);
        (dim, dim)
    } else {
        let max_side = src_w.max(src_h).max(1);
        let scale_dim = target_dim.min(max_side).max(1);
        let w = ((src_w as f64 * scale_dim as f64 / max_side as f64).round() as u32).max(1);
        let h = ((src_h as f64 * scale_dim as f64 / max_side as f64).round() as u32).max(1);
        (w, h)
    };

    // If source is already a PNG of the exact target dimensions, copy raw bytes directly.
    if entry.is_png && src_w == dst_w && src_h == dst_h {
        atomic_write(&out_path, &bytes)
            .map_err(|e| format!("Failed writing {}: {e}", out_path.display()))?;
        return Ok(());
    }

    let src_image = Image::from_vec_u8(src_w, src_h, rgba.into_raw(), PixelType::U8x4)
        .map_err(|e| format!("Invalid source buffer {}: {e}", entry.path.display()))?;
    let mut dst_image = Image::new(dst_w, dst_h, PixelType::U8x4);
    let mut options = ResizeOptions::new()
        .resize_alg(ResizeAlg::Convolution(FilterType::Lanczos3))
        .use_alpha(true);
    if crop {
        options = options.fit_into_destination(Some((0.5, 0.5)));
    }

    let mut resizer = Resizer::new();
    resizer
        .resize(&src_image, &mut dst_image, &options)
        .map_err(|e| format!("Resize failed for {}: {e}", entry.path.display()))?;

    let mut png_bytes = Vec::with_capacity((dst_w as usize) * (dst_h as usize) * 2);
    let encoder = PngEncoder::new_with_quality(
        &mut png_bytes,
        CompressionType::Fast,
        PngFilterType::Adaptive,
    );
    encoder
        .write_image(dst_image.buffer(), dst_w, dst_h, ColorType::Rgba8.into())
        .map_err(|e| format!("PNG encode failed for {}: {e}", entry.out_name))?;

    atomic_write(&out_path, &png_bytes)
        .map_err(|e| format!("Failed writing {}: {e}", out_path.display()))?;
    Ok(())
}

fn sync_active_avatars_impl(
    source_dir: &Path,
    active_dir: &Path,
    screen_width: u32,
    screen_height: u32,
    dpr: f64,
    max_ui_logical: u32,
    crop: bool,
) -> Result<SyncReport, String> {
    let started = Instant::now();
    let (target_dim, phys_width, phys_height, safe_dpr) =
        compute_target_dim(screen_width, screen_height, dpr, max_ui_logical);

    fs::create_dir_all(active_dir)
        .map_err(|e| format!("Cannot create active avatar dir {}: {e}", active_dir.display()))?;

    let sources = collect_sources(source_dir)?;
    let expected_names: BTreeSet<String> = sources.iter().map(|s| s.out_name.clone()).collect();

    // Remove stale output files or stray temp files in active/.
    let mut removed = 0usize;
    if let Ok(entries) = fs::read_dir(active_dir) {
        for entry in entries.flatten() {
            let path = entry.path();
            let Ok(ft) = entry.file_type() else {
                continue;
            };
            if !ft.is_file() {
                continue;
            }
            let Some(name) = path.file_name().and_then(|n| n.to_str()) else {
                continue;
            };
            if name == META_FILENAME {
                continue;
            }
            if !expected_names.contains(name) {
                if fs::remove_file(&path).is_ok() {
                    removed += 1;
                }
            }
        }
    }

    let meta_path = active_dir.join(META_FILENAME);
    let existing_meta: Option<ActiveMeta> = fs::read(&meta_path)
        .ok()
        .and_then(|raw| serde_json::from_slice(&raw).ok());

    let target_changed = existing_meta.as_ref().is_none_or(|m| {
        m.engine_version != ENGINE_VERSION || m.target_dim != target_dim || m.crop != crop
    });

    let mut to_process: Vec<&SourceEntry> = Vec::new();
    let mut reused = 0usize;
    for entry in &sources {
        let out_exists = active_dir.join(&entry.out_name).is_file();
        let stamp_matches = existing_meta
            .as_ref()
            .and_then(|m| m.sources.get(&entry.out_name))
            .is_some_and(|s| s == &entry.stamp);
        if !target_changed && out_exists && stamp_matches {
            reused += 1;
        } else {
            to_process.push(entry);
        }
    }

    let results: Vec<(&SourceEntry, Result<(), String>)> = to_process
        .par_iter()
        .map(|entry| (*entry, resize_one_avatar(entry, target_dim, active_dir, crop)))
        .collect();

    let mut next_sources = BTreeMap::new();
    for entry in &sources {
        next_sources.insert(entry.out_name.clone(), entry.stamp.clone());
    }

    let mut processed = 0usize;
    for (entry, res) in results {
        match res {
            Ok(()) => processed += 1,
            Err(err) => {
                // Downloaded Google avatar might be malformed/empty; skip rather than crashing startup.
                if entry.out_name == "google.png" && crop {
                    next_sources.remove(&entry.out_name);
                    let _ = fs::remove_file(active_dir.join(&entry.out_name));
                } else {
                    return Err(err);
                }
            }
        }
    }

    if processed > 0 || removed > 0 || target_changed || existing_meta.is_none() {
        let dpr_milli = (safe_dpr * 1000.0).round().clamp(1.0, 100_000.0) as u32;
        let next_meta = ActiveMeta {
            engine_version: ENGINE_VERSION,
            target_dim,
            crop,
            screen_width: phys_width,
            screen_height: phys_height,
            dpr_milli,
            sources: next_sources,
        };
        let encoded = serde_json::to_vec_pretty(&next_meta)
            .map_err(|e| format!("Failed serializing active avatar meta: {e}"))?;
        atomic_write(&meta_path, &encoded)
            .map_err(|e| format!("Failed writing active avatar meta: {e}"))?;
    }

    Ok(SyncReport {
        target_dim,
        screen_width,
        screen_height,
        phys_width,
        phys_height,
        dpr: safe_dpr,
        processed,
        reused,
        removed,
        elapsed_ms: started.elapsed().as_millis() as u64,
    })
}

#[pyfunction]
#[pyo3(signature = (screen_width, screen_height, dpr=1.0, max_ui_logical=64))]
pub(crate) fn compute_avatar_target_dim(
    screen_width: u32,
    screen_height: u32,
    dpr: f64,
    max_ui_logical: u32,
) -> u32 {
    compute_target_dim(screen_width, screen_height, dpr, max_ui_logical).0
}

#[pyfunction]
#[pyo3(signature = (source_dir, active_dir, screen_width, screen_height, dpr=1.0, max_ui_logical=64, crop=true))]
pub(crate) fn sync_active_avatars_json(
    py: Python<'_>,
    source_dir: PathBuf,
    active_dir: PathBuf,
    screen_width: u32,
    screen_height: u32,
    dpr: f64,
    max_ui_logical: u32,
    crop: bool,
) -> PyResult<String> {
    let report = py
        .detach(move || {
            sync_active_avatars_impl(
                &source_dir,
                &active_dir,
                screen_width,
                screen_height,
                dpr,
                max_ui_logical,
                crop,
            )
        })
        .map_err(PyRuntimeError::new_err)?;
    serde_json::to_string(&report).map_err(|e| PyRuntimeError::new_err(e.to_string()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use image::{ImageBuffer, Rgba};
    use std::time::{SystemTime, UNIX_EPOCH};

    fn make_temp_dir(label: &str) -> PathBuf {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or(0);
        let dir = std::env::temp_dir().join(format!(
            "rak_avatar_test_{label}_{}_{nanos}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn write_test_png(path: &Path, w: u32, h: u32) {
        let img: ImageBuffer<Rgba<u8>, Vec<u8>> = ImageBuffer::from_fn(w, h, |x, y| {
            let dx = x as f32 - (w as f32) / 2.0;
            let dy = y as f32 - (h as f32) / 2.0;
            let inside = (dx * dx + dy * dy).sqrt() <= (w.min(h) as f32) * 0.45;
            if inside {
                Rgba([(x % 255) as u8, (y % 255) as u8, 200, 255])
            } else {
                Rgba([0, 0, 0, 0])
            }
        });
        img.save(path).unwrap();
    }

    #[test]
    fn target_dim_scales_across_display_resolutions() {
        assert_eq!(compute_target_dim(1280, 720, 1.0, 64).0, 128);
        assert_eq!(compute_target_dim(1920, 1080, 1.0, 64).0, 192);
        assert_eq!(compute_target_dim(1536, 864, 1.25, 64).0, 192); // 1080p @ 125%
        assert_eq!(compute_target_dim(1920, 1080, 1.25, 64).0, 240); // 1350p physical
        assert_eq!(compute_target_dim(1080, 2340, 1.0, 64).0, 192);
        assert_eq!(compute_target_dim(360, 780, 3.0, 64).0, 384);
        assert_eq!(compute_target_dim(2560, 1440, 1.0, 64).0, 256);
        assert_eq!(compute_target_dim(1920, 1080, 2.0, 64).0, 384); // 4K @ 200%
        assert_eq!(compute_target_dim(3840, 2160, 1.0, 64).0, 384); // 4K @ 100%
        assert_eq!(compute_target_dim(1280, 720, 1.0, 24).0, 48);   // badge @ 720p
        assert_eq!(compute_target_dim(1920, 1080, 1.0, 24).0, 72);  // badge @ 1080p
        assert_eq!(compute_target_dim(3840, 2160, 1.0, 24).0, 144); // badge @ 4K
    }

    #[test]
    fn sync_active_avatars_lifecycle() {
        let root = make_temp_dir("lifecycle");
        let src = root.join("src");
        let active = root.join("active");
        fs::create_dir_all(&src).unwrap();
        write_test_png(&src.join("000.png"), 512, 512);
        write_test_png(&src.join("001.png"), 512, 512);

        // 1. Initial 720p build
        let r1 = sync_active_avatars_impl(&src, &active, 1280, 720, 1.0, 64, true).unwrap();
        assert_eq!(r1.target_dim, 128);
        assert_eq!(r1.processed, 2);
        assert_eq!(r1.reused, 0);
        let im0 = ImageReader::open(active.join("000.png")).unwrap().decode().unwrap();
        assert_eq!((im0.width(), im0.height()), (128, 128));

        // 2. Unchanged restart reuses both
        let r2 = sync_active_avatars_impl(&src, &active, 1280, 720, 1.0, 64, true).unwrap();
        assert_eq!(r2.processed, 0);
        assert_eq!(r2.reused, 2);

        // 3. Missing file in active/ regenerates only that file
        fs::remove_file(active.join("001.png")).unwrap();
        let r3 = sync_active_avatars_impl(&src, &active, 1280, 720, 1.0, 64, true).unwrap();
        assert_eq!(r3.processed, 1);
        assert_eq!(r3.reused, 1);
        assert!(active.join("001.png").is_file());

        // 4. Display resolution change to 4K regenerates to 384x384
        let r4 = sync_active_avatars_impl(&src, &active, 1920, 1080, 2.0, 64, true).unwrap();
        assert_eq!(r4.target_dim, 384);
        assert_eq!(r4.processed, 2);
        let im0_4k = ImageReader::open(active.join("000.png")).unwrap().decode().unwrap();
        assert_eq!((im0_4k.width(), im0_4k.height()), (384, 384));

        // 5. Removing source cleans stale active output
        fs::remove_file(src.join("001.png")).unwrap();
        let r5 = sync_active_avatars_impl(&src, &active, 1920, 1080, 2.0, 64, true).unwrap();
        assert_eq!(r5.removed, 1);
        assert!(!active.join("001.png").exists());

        // 6. Corrupt google.png is skipped without failing startup
        fs::write(src.join("google.png"), b"not an image").unwrap();
        let r6 = sync_active_avatars_impl(&src, &active, 1920, 1080, 2.0, 64, true).unwrap();
        assert_eq!(r6.reused, 1);
        assert!(!active.join("google.png").exists());

        // 7. Non-square badge asset with crop=false preserves aspect ratio without clipping
        let badge_src = root.join("badge_src");
        let badge_active = root.join("badge_active");
        fs::create_dir_all(&badge_src).unwrap();
        write_test_png(&badge_src.join("google.png"), 120, 123);
        let rb = sync_active_avatars_impl(&badge_src, &badge_active, 1920, 1080, 1.0, 24, false).unwrap();
        assert_eq!(rb.target_dim, 72);
        let imb = ImageReader::open(badge_active.join("google.png")).unwrap().decode().unwrap();
        assert_eq!((imb.width(), imb.height()), (70, 72));

        let _ = fs::remove_dir_all(&root);
    }
}
