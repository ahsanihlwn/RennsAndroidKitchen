//! Execution of Fastboot CLI tasks against a [`AnyFastbootTransport`] (MIT).

use std::fs;
use std::io::{Cursor, Read};
use std::num::NonZeroU32;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use android_avb::{is_vbmeta_partition, patch_vbmeta_flags};
use android_bootimg::{build_boot_image, repack_vendor_boot_image};
use android_lp::build_empty_super_metadata;
use android_sparse::{
    is_sparse_image, split_raw, split_sparse, ParsedChunk, SparseReader, SplitPart,
};
use extfs_mkfs::{format_extfs, ExtMkfsConfig};
use f2fs_mkfs::spec::{
    F2FS_ENC_UTF8_12_1, F2FS_FEATURE_CASEFOLD, F2FS_FEATURE_COMPRESSION, F2FS_FEATURE_ENCRYPT,
    F2FS_FEATURE_EXTRA_ATTR, F2FS_FEATURE_PRJQUOTA, F2FS_FEATURE_QUOTA_INO, F2FS_FEATURE_VERITY,
};
use f2fs_mkfs::{format_f2fs, MkfsConfig as F2fsMkfsConfig};

use crate::config::{
    help_output, parse_cli_args, parse_fastboot_info, version_output, FastbootCliAction,
    FastbootInfoStep, FastbootSessionConfig, FastbootTask,
};
use crate::protocol::{
    parse_u64_auto, FastbootError, FastbootEvent, FastbootEventCallback, FastbootRunResult,
};
use crate::transport::{list_usb_devices, AnyFastbootTransport};

struct OutputCollector {
    is_captured: bool,
    stdout: String,
    stderr: String,
    combined: String,
    on_event: Option<FastbootEventCallback>,
}

impl OutputCollector {
    fn stdio() -> Self {
        Self {
            is_captured: false,
            stdout: String::new(),
            stderr: String::new(),
            combined: String::new(),
            on_event: None,
        }
    }

    fn captured(on_event: Option<FastbootEventCallback>) -> Self {
        Self {
            is_captured: true,
            stdout: String::new(),
            stderr: String::new(),
            combined: String::new(),
            on_event,
        }
    }

    fn push_out(&mut self, text: &str) {
        if text.is_empty() {
            return;
        }
        if !self.is_captured {
            print!("{text}");
            return;
        }
        self.stdout.push_str(text);
        self.combined.push_str(text);
        if let Some(cb) = &self.on_event {
            cb(FastbootEvent::Output {
                text: text.to_string(),
                is_stderr: false,
            });
        }
    }

    fn push_err(&mut self, text: &str) {
        if text.is_empty() {
            return;
        }
        if !self.is_captured {
            eprint!("{text}");
            return;
        }
        self.stderr.push_str(text);
        self.combined.push_str(text);
        if let Some(cb) = &self.on_event {
            cb(FastbootEvent::Output {
                text: text.to_string(),
                is_stderr: true,
            });
        }
    }

    fn print_status(&mut self, action: &str, dur: Duration) {
        self.push_err(&format!("{:<50} OKAY [{:7.3}s]\n", action, dur.as_secs_f64()));
    }

    fn emit_progress(&self, ev: FastbootEvent) {
        if let Some(cb) = &self.on_event {
            cb(ev);
        }
    }

    fn into_result(self, exit_code: i32) -> FastbootRunResult {
        FastbootRunResult {
            exit_code,
            stdout: self.stdout,
            stderr: self.stderr,
            output: self.combined,
        }
    }
}

#[derive(Clone, Copy)]
struct AbortCtx<'a> {
    cancel: Option<&'a AtomicBool>,
    deadline: Option<Instant>,
}

impl<'a> AbortCtx<'a> {
    fn none() -> Self {
        Self {
            cancel: None,
            deadline: None,
        }
    }

    fn check(&self) -> Result<(), FastbootError> {
        if let Some(flag) = self.cancel {
            if flag.load(Ordering::Relaxed) {
                return Err(FastbootError::Cancelled);
            }
        }
        if let Some(dl) = self.deadline {
            if Instant::now() >= dl {
                return Err(FastbootError::TimedOut);
            }
        }
        Ok(())
    }
}

fn resolve_path(cwd: Option<&Path>, p: &Path) -> PathBuf {
    if p.is_relative() {
        if let Some(base) = cwd {
            return base.join(p);
        }
    }
    p.to_path_buf()
}

fn serialize_split_part(part: &SplitPart, src: &[u8]) -> Vec<u8> {
    let mut out = Vec::with_capacity(part.sparse_size() as usize);
    out.extend_from_slice(&part.header.to_bytes());
    for c in &part.chunks {
        out.extend_from_slice(&c.header.to_bytes());
        if c.data_size > 0 {
            let start = c.input_offset as usize;
            let end = (start + c.data_size as usize).min(src.len());
            out.extend_from_slice(&src[start..end]);
            let pad = c.data_size as usize - (end - start);
            if pad > 0 {
                out.resize(out.len() + pad, 0);
            }
        }
    }
    out
}

/// Resolves target partition name(s) accounting for `has-slot:<part>`, `current-slot`, and `--slot`.
async fn resolve_target_partitions(
    transport: &mut AnyFastbootTransport,
    raw_part: &str,
    slot_override: Option<&str>,
    abort: AbortCtx<'_>,
) -> Result<Vec<String>, FastbootError> {
    let check_fn = || abort.check();
    if raw_part.ends_with("_a") || raw_part.ends_with("_b") {
        return Ok(vec![raw_part.to_string()]);
    }
    let has_slot = transport
        .get_var_optional_io(&format!("has-slot:{raw_part}"), &check_fn)
        .await?
        .unwrap_or_default();
    if has_slot != "yes" {
        return Ok(vec![raw_part.to_string()]);
    }

    if slot_override == Some("all") {
        return Ok(vec![format!("{raw_part}_a"), format!("{raw_part}_b")]);
    }

    let cur_slot_raw = transport
        .get_var_optional_io("current-slot", &check_fn)
        .await?
        .unwrap_or_else(|| "a".to_string());
    let cur_slot = cur_slot_raw.trim_start_matches('_');
    let cur_slot = if cur_slot.is_empty() { "a" } else { cur_slot };

    let chosen_slot = match slot_override {
        Some("other") => {
            if cur_slot == "a" {
                "b"
            } else {
                "a"
            }
        }
        Some(s) => s.trim_start_matches('_'),
        None => cur_slot,
    };

    Ok(vec![format!("{raw_part}_{chosen_slot}")])
}

async fn get_max_download_size(
    transport: &mut AnyFastbootTransport,
    cfg: &FastbootSessionConfig,
    abort: AbortCtx<'_>,
) -> Result<u32, FastbootError> {
    if let Some(lim) = cfg.sparse_limit {
        return Ok(lim.min(u64::from(u32::MAX)) as u32);
    }
    let check_fn = || abort.check();
    if let Some(val) = transport
        .get_var_optional_io("max-download-size", &check_fn)
        .await?
    {
        if let Ok(parsed) = parse_u64_auto(&val) {
            if parsed > 0 {
                return Ok(parsed.min(u64::from(u32::MAX)) as u32);
            }
        }
    }
    Ok(256 * 1024 * 1024)
}

fn format_mb(bytes: u64) -> f64 {
    bytes as f64 / (1024.0 * 1024.0)
}

/// Flashes an in-memory image buffer to `part`, automatically patching `vbmeta` flags if requested
/// and splitting oversized raw or sparse images into `max_download_size` sparse chunks, while
/// reporting smooth chunk-level byte progress.
async fn flash_buffer_to_partition(
    transport: &mut AnyFastbootTransport,
    cfg: &FastbootSessionConfig,
    part: &str,
    mut data: Vec<u8>,
    io_out: &mut OutputCollector,
    abort: AbortCtx<'_>,
) -> Result<(), FastbootError> {
    abort.check()?;
    if is_vbmeta_partition(part) && (cfg.disable_verity || cfg.disable_verification) {
        let _ = patch_vbmeta_flags(&mut data, cfg.disable_verity, cfg.disable_verification)?;
    }

    let max_dl = get_max_download_size(transport, cfg, abort).await?;
    let mut cursor = Cursor::new(&data);
    let is_sp = is_sparse_image(&mut cursor)?;
    let total_img_bytes = data.len() as u64;
    let check_fn = || abort.check();

    if data.len() as u64 > u64::from(max_dl) {
        let parts: Vec<SplitPart> = if is_sp {
            let mut sr = SparseReader::new(Cursor::new(&data), false)?;
            let hdr = sr.header().clone();
            let mut ch_hdrs = Vec::new();
            while let Some(c) = sr.next_chunk()? {
                match c {
                    ParsedChunk::Raw { header, .. } => {
                        sr.skip_raw_data()?;
                        ch_hdrs.push(header);
                    }
                    ParsedChunk::Fill { header, .. }
                    | ParsedChunk::DontCare { header }
                    | ParsedChunk::Crc32 { header, .. } => ch_hdrs.push(header),
                }
            }
            split_sparse(&hdr, &ch_hdrs, u64::from(max_dl))?
        } else {
            split_raw(
                data.len() as u64,
                NonZeroU32::new(4096).unwrap(),
                u64::from(max_dl),
            )?
        };
        let total = parts.len().max(1);
        for (idx, part_item) in parts.into_iter().enumerate() {
            abort.check()?;
            let chunk_bytes = serialize_split_part(&part_item, &data);
            let chunk_len = chunk_bytes.len() as u64;
            let chunk_start_pct = (((idx as f64) / (total as f64)) * 100.0) as i32;
            io_out.emit_progress(FastbootEvent::Progress {
                partition: part.to_string(),
                stage: "sending".to_string(),
                chunk_index: idx + 1,
                chunk_total: total,
                bytes_sent: 0,
                bytes_total: chunk_len,
                percent: chunk_start_pct,
                speed_bps: 0,
                detail: format!(
                    "Sending sparse '{}' {}/{} ({:.1} MB)",
                    part,
                    idx + 1,
                    total,
                    format_mb(chunk_len)
                ),
            });

            let cb_opt = io_out.on_event.clone();
            let mut last_emit = Instant::now() - Duration::from_secs(1);
            let mut last_pct = -1i32;
            let xfer_start = Instant::now();
            let part_owned = part.to_string();
            let dl_dur = {
                let mut sink_fn = |text: &str, is_err: bool| {
                    if is_err {
                        io_out.push_err(text);
                    } else {
                        io_out.push_out(text);
                    }
                };
                transport
                    .download_data_with_progress(
                        &chunk_bytes,
                        &check_fn,
                        &mut sink_fn,
                        |sent, chunk_tot| {
                            let frac = if chunk_tot > 0 {
                                sent as f64 / chunk_tot as f64
                            } else {
                                1.0
                            };
                            let pct = (((idx as f64 + frac * 0.85) / (total as f64)) * 100.0)
                                .clamp(0.0, 98.0) as i32;
                            if sent < chunk_tot
                                && pct == last_pct
                                && last_emit.elapsed() < Duration::from_millis(60)
                            {
                                return;
                            }
                            last_pct = pct;
                            last_emit = Instant::now();
                            let secs = xfer_start.elapsed().as_secs_f64().max(0.001);
                            let speed_bps = ((sent as f64) / secs) as u64;
                            if let Some(cb) = &cb_opt {
                                cb(FastbootEvent::Progress {
                                    partition: part_owned.clone(),
                                    stage: "sending".to_string(),
                                    chunk_index: idx + 1,
                                    chunk_total: total,
                                    bytes_sent: sent,
                                    bytes_total: chunk_tot,
                                    percent: pct,
                                    speed_bps,
                                    detail: format!(
                                        "Sending sparse '{}' {}/{} ({:.1}/{:.1} MB · {:.1} MB/s)",
                                        part_owned,
                                        idx + 1,
                                        total,
                                        format_mb(sent),
                                        format_mb(chunk_tot),
                                        format_mb(speed_bps)
                                    ),
                                });
                            }
                        },
                    )
                    .await?
            };

            io_out.print_status(
                &format!(
                    "Sending sparse '{}' {}/{} ({} KB)",
                    part,
                    idx + 1,
                    total,
                    chunk_bytes.len() / 1024
                ),
                dl_dur,
            );
            let write_pct = (((idx as f64 + 0.90) / (total as f64)) * 100.0).clamp(0.0, 98.0) as i32;
            io_out.emit_progress(FastbootEvent::Progress {
                partition: part.to_string(),
                stage: "writing".to_string(),
                chunk_index: idx + 1,
                chunk_total: total,
                bytes_sent: chunk_len,
                bytes_total: chunk_len,
                percent: write_pct,
                speed_bps: 0,
                detail: format!("Writing '{}' {}/{}", part, idx + 1, total),
            });
            let fl_dur = {
                let mut sink_fn = |text: &str, is_err: bool| {
                    if is_err {
                        io_out.push_err(text);
                    } else {
                        io_out.push_out(text);
                    }
                };
                let (_, d) = transport
                    .execute_command_io(&format!("flash:{part}"), &check_fn, &mut sink_fn)
                    .await?;
                d
            };
            io_out.print_status(&format!("Writing '{part}'"), fl_dur);
            let done_pct = (((idx + 1) * 100) / total).min(99) as i32;
            io_out.emit_progress(FastbootEvent::Progress {
                partition: part.to_string(),
                stage: "writing".to_string(),
                chunk_index: idx + 1,
                chunk_total: total,
                bytes_sent: chunk_len,
                bytes_total: chunk_len,
                percent: done_pct,
                speed_bps: 0,
                detail: format!("Writing '{}' {}/{} OKAY", part, idx + 1, total),
            });
        }
    } else {
        io_out.emit_progress(FastbootEvent::Progress {
            partition: part.to_string(),
            stage: "sending".to_string(),
            chunk_index: 1,
            chunk_total: 1,
            bytes_sent: 0,
            bytes_total: total_img_bytes,
            percent: 0,
            speed_bps: 0,
            detail: format!("Sending '{}' ({:.1} MB)", part, format_mb(total_img_bytes)),
        });

        let cb_opt = io_out.on_event.clone();
        let mut last_emit = Instant::now() - Duration::from_secs(1);
        let mut last_pct = -1i32;
        let xfer_start = Instant::now();
        let part_owned = part.to_string();
        let dl_dur = {
            let mut sink_fn = |text: &str, is_err: bool| {
                if is_err {
                    io_out.push_err(text);
                } else {
                    io_out.push_out(text);
                }
            };
            transport
                .download_data_with_progress(&data, &check_fn, &mut sink_fn, |sent, tot| {
                    let pct = if tot > 0 {
                        ((sent.saturating_mul(90)) / tot).min(90) as i32
                    } else {
                        90
                    };
                    if sent < tot
                        && pct == last_pct
                        && last_emit.elapsed() < Duration::from_millis(60)
                    {
                        return;
                    }
                    last_pct = pct;
                    last_emit = Instant::now();
                    let secs = xfer_start.elapsed().as_secs_f64().max(0.001);
                    let speed_bps = ((sent as f64) / secs) as u64;
                    if let Some(cb) = &cb_opt {
                        cb(FastbootEvent::Progress {
                            partition: part_owned.clone(),
                            stage: "sending".to_string(),
                            chunk_index: 1,
                            chunk_total: 1,
                            bytes_sent: sent,
                            bytes_total: tot,
                            percent: pct,
                            speed_bps,
                            detail: format!(
                                "Sending '{}' ({:.1}/{:.1} MB · {:.1} MB/s)",
                                part_owned,
                                format_mb(sent),
                                format_mb(tot),
                                format_mb(speed_bps)
                            ),
                        });
                    }
                })
                .await?
        };
        io_out.print_status(
            &format!("Sending '{}' ({} KB)", part, (data.len().max(1024)) / 1024),
            dl_dur,
        );
        io_out.emit_progress(FastbootEvent::Progress {
            partition: part.to_string(),
            stage: "writing".to_string(),
            chunk_index: 1,
            chunk_total: 1,
            bytes_sent: total_img_bytes,
            bytes_total: total_img_bytes,
            percent: 92,
            speed_bps: 0,
            detail: format!("Writing '{part}'"),
        });
        let fl_dur = {
            let mut sink_fn = |text: &str, is_err: bool| {
                if is_err {
                    io_out.push_err(text);
                } else {
                    io_out.push_out(text);
                }
            };
            let (_, d) = transport
                .execute_command_io(&format!("flash:{part}"), &check_fn, &mut sink_fn)
                .await?;
            d
        };
        io_out.print_status(&format!("Writing '{part}'"), fl_dur);
        io_out.emit_progress(FastbootEvent::Progress {
            partition: part.to_string(),
            stage: "writing".to_string(),
            chunk_index: 1,
            chunk_total: 1,
            bytes_sent: total_img_bytes,
            bytes_total: total_img_bytes,
            percent: 99,
            speed_bps: 0,
            detail: format!("Writing '{part}' OKAY"),
        });
    }
    Ok(())
}

async fn fetch_partition_bytes(
    transport: &mut AnyFastbootTransport,
    part: &str,
    offset: Option<u64>,
    size: Option<u64>,
    io_out: &mut OutputCollector,
    abort: AbortCtx<'_>,
) -> Result<(Vec<u8>, Duration), FastbootError> {
    let check_fn = || abort.check();
    let cmd = match (offset, size) {
        (Some(off), Some(sz)) => format!("fetch:{part}:0x{off:08x}:0x{sz:08x}"),
        (Some(off), None) => format!("fetch:{part}:0x{off:08x}"),
        _ => format!("fetch:{part}"),
    };
    let mut sink_fn = |text: &str, is_err: bool| {
        if is_err {
            io_out.push_err(text);
        } else {
            io_out.push_out(text);
        }
    };
    transport.upload_data_io(&cmd, &check_fn, &mut sink_fn).await
}

async fn execute_format_partition(
    transport: &mut AnyFastbootTransport,
    cfg: &FastbootSessionConfig,
    raw_part: &str,
    fs_type_override: Option<&str>,
    size_override: Option<u64>,
    io_out: &mut OutputCollector,
    abort: AbortCtx<'_>,
) -> Result<(), FastbootError> {
    let check_fn = || abort.check();
    let targets =
        resolve_target_partitions(transport, raw_part, cfg.slot_override.as_deref(), abort).await?;
    for part in targets {
        let fs_type = match fs_type_override {
            Some(t) => t.to_string(),
            None => transport
                .get_var_optional_io(&format!("partition-type:{part}"), &check_fn)
                .await?
                .unwrap_or_else(|| "ext4".to_string()),
        };
        let part_size = match size_override {
            Some(s) => s,
            None => {
                let s_str = transport
                    .get_var_optional_io(&format!("partition-size:{part}"), &check_fn)
                    .await?
                    .unwrap_or_else(|| "0x04000000".to_string());
                parse_u64_auto(&s_str).unwrap_or(64 * 1024 * 1024)
            }
        };

        let er_dur = {
            let mut sink_fn = |text: &str, is_err: bool| {
                if is_err {
                    io_out.push_err(text);
                } else {
                    io_out.push_out(text);
                }
            };
            let (_, d) = transport
                .execute_command_io(&format!("erase:{part}"), &check_fn, &mut sink_fn)
                .await?;
            d
        };
        io_out.print_status(&format!("Erasing '{part}'"), er_dur);

        if fs_type == "raw" || part_size == 0 {
            continue;
        }

        let tmp_dir = tempfile::tempdir()?;
        let sp_img_path = tmp_dir.path().join("formatted_sparse.img");

        let has_casefold = cfg.fs_options.iter().any(|o| o == "casefold");
        let has_projid = cfg.fs_options.iter().any(|o| o == "projid");
        let has_compress = cfg.fs_options.iter().any(|o| o == "compress");

        match fs_type.as_str() {
            "ext4" | "ext3" | "ext2" => {
                let mut ecfg = ExtMkfsConfig {
                    device_path: sp_img_path.clone(),
                    blocks_count_arg: Some(part_size / 4096),
                    block_size: 4096,
                    block_size_explicit: true,
                    inode_size: 256,
                    inode_size_explicit: true,
                    android_sparse: true,
                    quiet: true,
                    force: true,
                    fs_type: fs_type.clone(),
                    ..ExtMkfsConfig::default()
                };
                extfs_mkfs::config::apply_fs_type_profile(&mut ecfg, &fs_type);
                if has_casefold {
                    extfs_mkfs::config::apply_features(&mut ecfg, "casefold")
                        .map_err(|e| FastbootError::InvalidArgument(e.to_string()))?;
                }
                if has_projid {
                    extfs_mkfs::config::apply_features(&mut ecfg, "project")
                        .map_err(|e| FastbootError::InvalidArgument(e.to_string()))?;
                }
                format_extfs(ecfg).map_err(|e| FastbootError::InvalidArgument(e.to_string()))?;
            }
            "f2fs" => {
                let mut fcfg = F2fsMkfsConfig {
                    device_path: sp_img_path.clone(),
                    sparse_size: Some(part_size),
                    trim: false,
                    force: true,
                    quiet: true,
                    nat_bits: false,
                    features: F2FS_FEATURE_ENCRYPT
                        | F2FS_FEATURE_EXTRA_ATTR
                        | F2FS_FEATURE_PRJQUOTA
                        | F2FS_FEATURE_QUOTA_INO
                        | F2FS_FEATURE_VERITY,
                    usr_grp_quota: true,
                    prj_quota: true,
                    ..F2fsMkfsConfig::default()
                };
                if has_casefold {
                    fcfg.features |= F2FS_FEATURE_CASEFOLD;
                    fcfg.encoding = F2FS_ENC_UTF8_12_1;
                }
                if has_projid {
                    fcfg.features |= F2FS_FEATURE_PRJQUOTA | F2FS_FEATURE_EXTRA_ATTR;
                    fcfg.prj_quota = true;
                }
                if has_compress {
                    fcfg.features |= F2FS_FEATURE_COMPRESSION | F2FS_FEATURE_EXTRA_ATTR;
                }
                format_f2fs(&fcfg).map_err(|e| FastbootError::InvalidArgument(e.to_string()))?;
            }
            other => {
                io_out.push_err(&format!(
                    "fastboot: error: Formatting is not supported for filesystem with type '{other}'.\n"
                ));
                continue;
            }
        }

        let sp_bytes = fs::read(&sp_img_path)?;
        flash_buffer_to_partition(transport, cfg, &part, sp_bytes, io_out, abort).await?;
    }
    Ok(())
}

fn resolve_product_out_image(part_base: &str) -> Result<PathBuf, FastbootError> {
    let out_dir = std::env::var("ANDROID_PRODUCT_OUT").map_err(|_| {
        FastbootError::InvalidArgument(
            "ANDROID_PRODUCT_OUT not set and no image filename specified".into(),
        )
    })?;
    let stripped = part_base
        .strip_suffix("_a")
        .or_else(|| part_base.strip_suffix("_b"))
        .unwrap_or(part_base);
    Ok(Path::new(&out_dir).join(format!("{stripped}.img")))
}

/// Executes all tasks in [`FastbootSessionConfig`].
pub async fn execute_session(cfg: FastbootSessionConfig) -> Result<(), FastbootError> {
    let mut io_out = OutputCollector::stdio();
    execute_session_io(cfg, None, &mut io_out, AbortCtx::none()).await
}

async fn execute_session_io(
    cfg: FastbootSessionConfig,
    cwd: Option<&Path>,
    io_out: &mut OutputCollector,
    abort: AbortCtx<'_>,
) -> Result<(), FastbootError> {
    abort.check()?;
    let check_fn = || abort.check();
    let session_start = Instant::now();
    let mut transport = AnyFastbootTransport::connect(cfg.serial.as_deref()).await?;

    if let Some(slot_opt) = &cfg.set_active_slot {
        let slot = match slot_opt {
            Some(s) => s.clone(),
            None => cfg
                .slot_override
                .clone()
                .unwrap_or_else(|| "a".to_string()),
        };
        execute_set_active(&mut transport, &slot, io_out, abort).await?;
    }

    for task in &cfg.tasks {
        abort.check()?;
        match task {
            FastbootTask::GetVar(name) => {
                if name == "all" {
                    let _ = {
                        let mut sink_fn = |text: &str, is_err: bool| {
                            if is_err {
                                io_out.push_err(text);
                            } else {
                                io_out.push_out(text);
                            }
                        };
                        transport.get_all_vars_io(&check_fn, &mut sink_fn).await?
                    };
                    io_out.push_err("all: \n");
                } else {
                    let (val, _) = {
                        let mut sink_fn = |text: &str, is_err: bool| {
                            if is_err {
                                io_out.push_err(text);
                            } else {
                                io_out.push_out(text);
                            }
                        };
                        transport.get_var_io(name, &check_fn, &mut sink_fn).await?
                    };
                    io_out.push_err(&format!("{name}: {val}\n"));
                }
            }
            FastbootTask::Erase(raw_part) => {
                let targets = resolve_target_partitions(
                    &mut transport,
                    raw_part,
                    cfg.slot_override.as_deref(),
                    abort,
                )
                .await?;
                for part in targets {
                    let _ = transport
                        .get_var_optional_io(&format!("partition-type:{part}"), &check_fn)
                        .await?;
                    let dur = {
                        let mut sink_fn = |text: &str, is_err: bool| {
                            if is_err {
                                io_out.push_err(text);
                            } else {
                                io_out.push_out(text);
                            }
                        };
                        let (_, d) = transport
                            .execute_command_io(&format!("erase:{part}"), &check_fn, &mut sink_fn)
                            .await?;
                        d
                    };
                    io_out.print_status(&format!("Erasing '{part}'"), dur);
                }
            }
            FastbootTask::Format {
                partition,
                fs_type,
                size_override,
            } => {
                execute_format_partition(
                    &mut transport,
                    &cfg,
                    partition,
                    fs_type.as_deref(),
                    *size_override,
                    io_out,
                    abort,
                )
                .await?;
            }
            FastbootTask::Flash {
                partition,
                filename,
            } => {
                if let Some((vb_base, ramdisk_name)) = partition.split_once(':') {
                    let img_path = match filename {
                        Some(p) => resolve_path(cwd, p),
                        None => resolve_product_out_image("vendor_ramdisk")?,
                    };
                    let new_ramdisk = fs::read(&img_path)?;
                    let dtb_bytes = match &cfg.dtb_path {
                        Some(p) => Some(fs::read(resolve_path(cwd, p))?),
                        None => None,
                    };
                    let targets = resolve_target_partitions(
                        &mut transport,
                        vb_base,
                        cfg.slot_override.as_deref(),
                        abort,
                    )
                    .await?;
                    for part in targets {
                        let (existing_vb, fetch_dur) = fetch_partition_bytes(
                            &mut transport,
                            &part,
                            None,
                            None,
                            io_out,
                            abort,
                        )
                        .await?;
                        io_out.print_status(&format!("Fetching '{part}'"), fetch_dur);
                        let repacked = repack_vendor_boot_image(
                            &existing_vb,
                            ramdisk_name,
                            &new_ramdisk,
                            dtb_bytes.as_deref(),
                        )?;
                        flash_buffer_to_partition(
                            &mut transport,
                            &cfg,
                            &part,
                            repacked,
                            io_out,
                            abort,
                        )
                        .await?;
                    }
                } else {
                    let img_path = match filename {
                        Some(p) => resolve_path(cwd, p),
                        None => resolve_product_out_image(partition)?,
                    };
                    let data = fs::read(&img_path)?;
                    let targets = resolve_target_partitions(
                        &mut transport,
                        partition,
                        cfg.slot_override.as_deref(),
                        abort,
                    )
                    .await?;
                    for part in targets {
                        flash_buffer_to_partition(
                            &mut transport,
                            &cfg,
                            &part,
                            data.clone(),
                            io_out,
                            abort,
                        )
                        .await?;
                    }
                }
            }
            FastbootTask::FlashRaw {
                partition,
                kernel,
                ramdisk,
                second,
            } => {
                let k_bytes = fs::read(resolve_path(cwd, kernel))?;
                let r_bytes = match ramdisk {
                    Some(p) => Some(fs::read(resolve_path(cwd, p))?),
                    None => None,
                };
                let s_bytes = match second {
                    Some(p) => Some(fs::read(resolve_path(cwd, p))?),
                    None => None,
                };
                let d_bytes = match &cfg.dtb_path {
                    Some(p) => Some(fs::read(resolve_path(cwd, p))?),
                    None => None,
                };
                let boot_img = build_boot_image(
                    &cfg.boot_args,
                    &k_bytes,
                    r_bytes.as_deref(),
                    s_bytes.as_deref(),
                    d_bytes.as_deref(),
                )?;
                let targets = resolve_target_partitions(
                    &mut transport,
                    partition,
                    cfg.slot_override.as_deref(),
                    abort,
                )
                .await?;
                for part in targets {
                    flash_buffer_to_partition(
                        &mut transport,
                        &cfg,
                        &part,
                        boot_img.clone(),
                        io_out,
                        abort,
                    )
                    .await?;
                }
            }
            FastbootTask::Boot {
                kernel,
                ramdisk,
                second,
            } => {
                let k_bytes = fs::read(resolve_path(cwd, kernel))?;
                let r_bytes = match ramdisk {
                    Some(p) => Some(fs::read(resolve_path(cwd, p))?),
                    None => None,
                };
                let s_bytes = match second {
                    Some(p) => Some(fs::read(resolve_path(cwd, p))?),
                    None => None,
                };
                let d_bytes = match &cfg.dtb_path {
                    Some(p) => Some(fs::read(resolve_path(cwd, p))?),
                    None => None,
                };
                let boot_img = build_boot_image(
                    &cfg.boot_args,
                    &k_bytes,
                    r_bytes.as_deref(),
                    s_bytes.as_deref(),
                    d_bytes.as_deref(),
                )?;
                let dl_dur = {
                    let mut sink_fn = |text: &str, is_err: bool| {
                        if is_err {
                            io_out.push_err(text);
                        } else {
                            io_out.push_out(text);
                        }
                    };
                    transport
                        .download_data_with_progress(&boot_img, &check_fn, &mut sink_fn, |_, _| {})
                        .await?
                };
                io_out.print_status(
                    &format!("Sending 'boot.img' ({} KB)", boot_img.len().max(1024) / 1024),
                    dl_dur,
                );
                let b_dur = {
                    let mut sink_fn = |text: &str, is_err: bool| {
                        if is_err {
                            io_out.push_err(text);
                        } else {
                            io_out.push_out(text);
                        }
                    };
                    let (_, d) = transport
                        .execute_command_io("boot", &check_fn, &mut sink_fn)
                        .await?;
                    d
                };
                io_out.print_status("Booting", b_dur);
            }
            FastbootTask::FlashAll => {
                let out_dir = PathBuf::from(std::env::var("ANDROID_PRODUCT_OUT").map_err(
                    |_| FastbootError::InvalidArgument("ANDROID_PRODUCT_OUT not set".into()),
                )?);
                execute_flash_from_dir(&mut transport, &cfg, &out_dir, io_out, abort).await?;
            }
            FastbootTask::Update(zip_path) => {
                let resolved_zip = resolve_path(cwd, zip_path);
                let zip_file = fs::File::open(resolved_zip)?;
                let mut archive = zip::ZipArchive::new(zip_file)?;
                let tmp_dir = tempfile::tempdir()?;
                for idx in 0..archive.len() {
                    abort.check()?;
                    let mut entry = archive.by_index(idx)?;
                    if entry.is_dir() {
                        continue;
                    }
                    let fname = Path::new(entry.name())
                        .file_name()
                        .map(|s| s.to_os_string())
                        .unwrap_or_default();
                    if fname.is_empty() {
                        continue;
                    }
                    let out_path = tmp_dir.path().join(fname);
                    let mut out_f = fs::File::create(out_path)?;
                    std::io::copy(&mut entry, &mut out_f)?;
                }
                execute_flash_from_dir(&mut transport, &cfg, tmp_dir.path(), io_out, abort).await?;
            }
            FastbootTask::Reboot(target_opt) => {
                let (wire_cmd, label) = match target_opt.as_deref() {
                    Some("bootloader") => ("reboot-bootloader".to_string(), "Rebooting into bootloader".to_string()),
                    Some("fastboot") => ("reboot-fastboot".to_string(), "Rebooting into fastboot".to_string()),
                    Some("recovery") => ("reboot-recovery".to_string(), "Rebooting into recovery".to_string()),
                    Some(other) => (format!("reboot-{other}"), format!("Rebooting into {other}")),
                    None => ("reboot".to_string(), "Rebooting".to_string()),
                };
                let dur = {
                    let mut sink_fn = |text: &str, is_err: bool| {
                        if is_err {
                            io_out.push_err(text);
                        } else {
                            io_out.push_out(text);
                        }
                    };
                    let (_, d) = transport
                        .execute_command_io(&wire_cmd, &check_fn, &mut sink_fn)
                        .await?;
                    d
                };
                io_out.print_status(&label, dur);
            }
            FastbootTask::Continue => {
                let dur = {
                    let mut sink_fn = |text: &str, is_err: bool| {
                        if is_err {
                            io_out.push_err(text);
                        } else {
                            io_out.push_out(text);
                        }
                    };
                    let (_, d) = transport
                        .execute_command_io("continue", &check_fn, &mut sink_fn)
                        .await?;
                    d
                };
                io_out.print_status("Resuming boot", dur);
            }
            FastbootTask::SetActive(slot) => {
                execute_set_active(&mut transport, slot, io_out, abort).await?;
            }
            FastbootTask::Flashing(sub) => {
                let cmd = format!("flashing {sub}");
                let dur = {
                    let mut sink_fn = |text: &str, is_err: bool| {
                        if is_err {
                            io_out.push_err(text);
                        } else {
                            io_out.push_out(text);
                        }
                    };
                    let (_, d) = transport
                        .execute_command_io(&cmd, &check_fn, &mut sink_fn)
                        .await?;
                    d
                };
                io_out.push_err(&format!("OKAY [{:7.3}s]\n", dur.as_secs_f64()));
            }
            FastbootTask::Oem(args_str) => {
                let cmd = format!("oem {args_str}");
                let dur = {
                    let mut sink_fn = |text: &str, is_err: bool| {
                        if is_err {
                            io_out.push_err(text);
                        } else {
                            io_out.push_out(text);
                        }
                    };
                    let (_, d) = transport
                        .execute_command_io(&cmd, &check_fn, &mut sink_fn)
                        .await?;
                    d
                };
                io_out.push_err(&format!("OKAY [{:7.3}s]\n", dur.as_secs_f64()));
            }
            FastbootTask::Gsi(sub) => {
                let cmd = format!("gsi:{sub}");
                let dur = {
                    let mut sink_fn = |text: &str, is_err: bool| {
                        if is_err {
                            io_out.push_err(text);
                        } else {
                            io_out.push_out(text);
                        }
                    };
                    let (_, d) = transport
                        .execute_command_io(&cmd, &check_fn, &mut sink_fn)
                        .await?;
                    d
                };
                io_out.push_err(&format!("OKAY [{:7.3}s]\n", dur.as_secs_f64()));
            }
            FastbootTask::WipeSuper(super_empty_opt) => {
                let meta_bytes = match super_empty_opt {
                    Some(p) => fs::read(resolve_path(cwd, p))?,
                    None => {
                        if let Ok(out_dir) = std::env::var("ANDROID_PRODUCT_OUT") {
                            let p = Path::new(&out_dir).join("super_empty.img");
                            if p.exists() {
                                fs::read(p)?
                            } else {
                                build_empty_super_metadata(65536, 2, 4096, 4 * 1024 * 1024 * 1024)
                            }
                        } else {
                            build_empty_super_metadata(65536, 2, 4096, 4 * 1024 * 1024 * 1024)
                        }
                    }
                };
                flash_buffer_to_partition(&mut transport, &cfg, "super", meta_bytes, io_out, abort)
                    .await?;
            }
            FastbootTask::CreateLogicalPartition { name, size } => {
                let cmd = format!("create-logical-partition:{name}:{size}");
                let dur = {
                    let mut sink_fn = |text: &str, is_err: bool| {
                        if is_err {
                            io_out.push_err(text);
                        } else {
                            io_out.push_out(text);
                        }
                    };
                    let (_, d) = transport
                        .execute_command_io(&cmd, &check_fn, &mut sink_fn)
                        .await?;
                    d
                };
                io_out.print_status(&format!("Creating '{name}'"), dur);
            }
            FastbootTask::DeleteLogicalPartition(name) => {
                let cmd = format!("delete-logical-partition:{name}");
                let dur = {
                    let mut sink_fn = |text: &str, is_err: bool| {
                        if is_err {
                            io_out.push_err(text);
                        } else {
                            io_out.push_out(text);
                        }
                    };
                    let (_, d) = transport
                        .execute_command_io(&cmd, &check_fn, &mut sink_fn)
                        .await?;
                    d
                };
                io_out.print_status(&format!("Deleting '{name}'"), dur);
            }
            FastbootTask::ResizeLogicalPartition { name, size } => {
                let cmd = format!("resize-logical-partition:{name}:{size}");
                let dur = {
                    let mut sink_fn = |text: &str, is_err: bool| {
                        if is_err {
                            io_out.push_err(text);
                        } else {
                            io_out.push_out(text);
                        }
                    };
                    let (_, d) = transport
                        .execute_command_io(&cmd, &check_fn, &mut sink_fn)
                        .await?;
                    d
                };
                io_out.print_status(&format!("Resizing '{name}'"), dur);
            }
            FastbootTask::SnapshotUpdate(sub) => {
                let cmd = format!("snapshot-update:{sub}");
                let dur = {
                    let mut sink_fn = |text: &str, is_err: bool| {
                        if is_err {
                            io_out.push_err(text);
                        } else {
                            io_out.push_out(text);
                        }
                    };
                    let (_, d) = transport
                        .execute_command_io(&cmd, &check_fn, &mut sink_fn)
                        .await?;
                    d
                };
                io_out.print_status(&format!("Snapshot {sub}"), dur);
            }
            FastbootTask::Fetch {
                partition,
                out_file,
                offset,
                size,
            } => {
                let targets = resolve_target_partitions(
                    &mut transport,
                    partition,
                    cfg.slot_override.as_deref(),
                    abort,
                )
                .await?;
                let part = targets.first().map(String::as_str).unwrap_or(partition);
                let (data, dur) =
                    fetch_partition_bytes(&mut transport, part, *offset, *size, io_out, abort)
                        .await?;
                io_out.print_status(&format!("Fetching {part}"), dur);
                fs::write(resolve_path(cwd, out_file), &data)?;
            }
            FastbootTask::Stage(in_file) => {
                let resolved = resolve_path(cwd, in_file);
                let data = fs::read(&resolved)?;
                let dur = {
                    let mut sink_fn = |text: &str, is_err: bool| {
                        if is_err {
                            io_out.push_err(text);
                        } else {
                            io_out.push_out(text);
                        }
                    };
                    transport
                        .download_data_with_progress(&data, &check_fn, &mut sink_fn, |_, _| {})
                        .await?
                };
                io_out.print_status(
                    &format!("Sending '{}' ({} KB)", in_file.display(), data.len().max(1024) / 1024),
                    dur,
                );
            }
            FastbootTask::GetStaged(out_file) => {
                let (data, dur) = {
                    let mut sink_fn = |text: &str, is_err: bool| {
                        if is_err {
                            io_out.push_err(text);
                        } else {
                            io_out.push_out(text);
                        }
                    };
                    transport
                        .upload_data_io("upload", &check_fn, &mut sink_fn)
                        .await?
                };
                io_out.print_status(&format!("Uploading '{}'", out_file.display()), dur);
                fs::write(resolve_path(cwd, out_file), &data)?;
            }
        }
    }

    if cfg.wipe_userdata {
        execute_format_partition(&mut transport, &cfg, "userdata", None, None, io_out, abort).await?;
        if transport
            .get_var_optional_io("partition-type:metadata", &check_fn)
            .await?
            .is_some()
        {
            execute_format_partition(&mut transport, &cfg, "metadata", None, None, io_out, abort)
                .await?;
        }
    }

    io_out.push_err(&format!(
        "Finished. Total time: {:.3}s\n",
        session_start.elapsed().as_secs_f64()
    ));
    Ok(())
}

async fn execute_set_active(
    transport: &mut AnyFastbootTransport,
    slot: &str,
    io_out: &mut OutputCollector,
    abort: AbortCtx<'_>,
) -> Result<(), FastbootError> {
    let check_fn = || abort.check();
    let slot_count_str = transport
        .get_var_optional_io("slot-count", &check_fn)
        .await?
        .unwrap_or_default();
    let slot_count: u32 = slot_count_str.trim().parse().unwrap_or(0);
    if slot_count < 2 {
        io_out.push_err("fastboot: error: Device does not support slots\n");
        return Err(FastbootError::InvalidArgument(
            "Device does not support slots".into(),
        ));
    }
    let clean_slot = slot.trim_start_matches('_');
    let dur = {
        let mut sink_fn = |text: &str, is_err: bool| {
            if is_err {
                io_out.push_err(text);
            } else {
                io_out.push_out(text);
            }
        };
        let (_, d) = transport
            .execute_command_io(&format!("set_active:{clean_slot}"), &check_fn, &mut sink_fn)
            .await?;
        d
    };
    io_out.print_status(&format!("Setting current slot to '{clean_slot}'"), dur);
    Ok(())
}

async fn execute_flash_from_dir(
    transport: &mut AnyFastbootTransport,
    cfg: &FastbootSessionConfig,
    dir: &Path,
    io_out: &mut OutputCollector,
    abort: AbortCtx<'_>,
) -> Result<(), FastbootError> {
    let info_path = dir.join("fastboot-info.txt");
    let steps = if !cfg.disable_fastboot_info && info_path.exists() {
        let mut txt = String::new();
        fs::File::open(&info_path)?.read_to_string(&mut txt)?;
        parse_fastboot_info(&txt)
    } else {
        let default_parts = [
            "boot",
            "init_boot",
            "vendor_boot",
            "dtbo",
            "vbmeta",
            "system",
            "vendor",
            "product",
        ];
        default_parts
            .iter()
            .map(|p| FastbootInfoStep::Flash {
                partition: (*p).to_string(),
                image_name: Some(format!("{p}.img")),
                slot_other: false,
                if_wipe: false,
            })
            .collect()
    };

    for step in steps {
        abort.check()?;
        match step {
            FastbootInfoStep::Version(_) => {}
            FastbootInfoStep::Flash {
                partition,
                image_name,
                slot_other,
                if_wipe,
            } => {
                if if_wipe && !cfg.wipe_userdata {
                    continue;
                }
                if slot_other && cfg.skip_secondary {
                    continue;
                }
                let img_file = image_name.unwrap_or_else(|| format!("{partition}.img"));
                let img_path = dir.join(&img_file);
                if !img_path.exists() {
                    continue;
                }
                let data = fs::read(&img_path)?;
                let slot_ov = if slot_other {
                    Some("other")
                } else {
                    cfg.slot_override.as_deref()
                };
                let targets =
                    resolve_target_partitions(transport, &partition, slot_ov, abort).await?;
                for part in targets {
                    flash_buffer_to_partition(
                        transport,
                        cfg,
                        &part,
                        data.clone(),
                        io_out,
                        abort,
                    )
                    .await?;
                }
            }
            FastbootInfoStep::Reboot(target) => {
                let wire_cmd = match target.as_deref() {
                    Some("bootloader") => "reboot-bootloader",
                    Some("fastboot") => "reboot-fastboot",
                    _ => "reboot",
                };
                let check_fn = || abort.check();
                let dur = {
                    let mut sink_fn = |text: &str, is_err: bool| {
                        if is_err {
                            io_out.push_err(text);
                        } else {
                            io_out.push_out(text);
                        }
                    };
                    let (_, d) = transport
                        .execute_command_io(wire_cmd, &check_fn, &mut sink_fn)
                        .await?;
                    d
                };
                io_out.print_status("Rebooting", dur);
            }
            FastbootInfoStep::UpdateSuper => {
                let super_empty = dir.join("super_empty.img");
                if super_empty.exists() {
                    let data = fs::read(super_empty)?;
                    let check_fn = || abort.check();
                    let dur = {
                        let mut sink_fn = |text: &str, is_err: bool| {
                            if is_err {
                                io_out.push_err(text);
                            } else {
                                io_out.push_out(text);
                            }
                        };
                        transport
                            .download_data_with_progress(&data, &check_fn, &mut sink_fn, |_, _| {})
                            .await?
                    };
                    io_out.print_status("Sending 'super_empty.img'", dur);
                    let u_dur = {
                        let mut sink_fn = |text: &str, is_err: bool| {
                            if is_err {
                                io_out.push_err(text);
                            } else {
                                io_out.push_out(text);
                            }
                        };
                        let (_, d) = transport
                            .execute_command_io("update-super:super", &check_fn, &mut sink_fn)
                            .await?;
                        d
                    };
                    io_out.print_status("Updating super partition", u_dur);
                }
            }
            FastbootInfoStep::Erase(part) => {
                let targets = resolve_target_partitions(
                    transport,
                    &part,
                    cfg.slot_override.as_deref(),
                    abort,
                )
                .await?;
                let check_fn = || abort.check();
                for t in targets {
                    let dur = {
                        let mut sink_fn = |text: &str, is_err: bool| {
                            if is_err {
                                io_out.push_err(text);
                            } else {
                                io_out.push_out(text);
                            }
                        };
                        let (_, d) = transport
                            .execute_command_io(&format!("erase:{t}"), &check_fn, &mut sink_fn)
                            .await?;
                        d
                    };
                    io_out.print_status(&format!("Erasing '{t}'"), dur);
                }
            }
        }
    }

    if !cfg.skip_reboot {
        let check_fn = || abort.check();
        let dur = {
            let mut sink_fn = |text: &str, is_err: bool| {
                if is_err {
                    io_out.push_err(text);
                } else {
                    io_out.push_out(text);
                }
            };
            let (_, d) = transport
                .execute_command_io("reboot", &check_fn, &mut sink_fn)
                .await?;
            d
        };
        io_out.print_status("Rebooting", dur);
    }

    Ok(())
}

/// Top-level CLI runner for `fastboot` writing directly to stdio. Returns process exit code.
pub async fn run_cli(args: &[String]) -> i32 {
    let mut io_out = OutputCollector::stdio();
    run_cli_with_io(args, None, None, None, &mut io_out).await
}

/// Captured/embedded CLI runner for `fastboot`. Zero stdio writes, with live progress & cancel support.
pub async fn run_cli_captured(
    args: &[String],
    cwd: Option<&Path>,
    timeout_ms: Option<u64>,
    cancel: Option<Arc<AtomicBool>>,
    on_event: Option<FastbootEventCallback>,
) -> FastbootRunResult {
    let mut io_out = OutputCollector::captured(on_event);
    let code = run_cli_with_io(args, cwd, timeout_ms, cancel.as_deref(), &mut io_out).await;
    io_out.into_result(code)
}

async fn run_cli_with_io(
    args: &[String],
    cwd: Option<&Path>,
    timeout_ms: Option<u64>,
    cancel: Option<&AtomicBool>,
    io_out: &mut OutputCollector,
) -> i32 {
    let deadline = timeout_ms
        .filter(|&ms| ms > 0)
        .map(|ms| Instant::now() + Duration::from_millis(ms));
    let abort = AbortCtx { cancel, deadline };
    if let Err(e) = abort.check() {
        io_out.push_err(&format!("fastboot: error: {e}\n"));
        return 1;
    }

    match parse_cli_args(args) {
        Ok(FastbootCliAction::ShowVersion) => {
            io_out.push_out(&version_output());
            0
        }
        Ok(FastbootCliAction::ShowHelp { exit_code }) => {
            io_out.push_out(help_output());
            exit_code
        }
        Ok(FastbootCliAction::ListDevices { long }) => match list_usb_devices().await {
            Ok(devs) => {
                for d in devs {
                    if long {
                        io_out.push_out(&format!(
                            "{:<22} {:<16} {}\n",
                            d.serial, d.mode, d.device_path
                        ));
                    } else {
                        io_out.push_out(&format!("{}\t{}\n", d.serial, d.mode));
                    }
                }
                0
            }
            Err(_) => 0,
        },
        Ok(FastbootCliAction::Execute(cfg)) => {
            let fut = execute_session_io(cfg, cwd, io_out, abort);
            let res = if let Some(ms) = timeout_ms.filter(|&m| m > 0) {
                match tokio::time::timeout(Duration::from_millis(ms), fut).await {
                    Ok(inner) => inner,
                    Err(_) => Err(FastbootError::TimedOut),
                }
            } else {
                fut.await
            };
            match res {
                Ok(()) => 0,
                Err(FastbootError::InvalidArgument(msg))
                    if msg == "Device does not support slots" =>
                {
                    1
                }
                Err(e) => {
                    io_out.push_err(&format!("fastboot: error: {e}\n"));
                    1
                }
            }
        }
        Err(e) => {
            io_out.push_err(&format!("fastboot: error: {e}\n"));
            1
        }
    }
}
