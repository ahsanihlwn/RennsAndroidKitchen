use std::fs::File;
use std::io::{self, BufWriter, Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};

use crc32fast::Hasher;
use flate2::write::GzEncoder;
use flate2::Compression;

use super::{
    BackedKind, SparseFile, CHUNK_HEADER_LEN, CHUNK_TYPE_CRC32, CHUNK_TYPE_DONT_CARE,
    CHUNK_TYPE_FILL, CHUNK_TYPE_RAW, SPARSE_HEADER_LEN, SPARSE_HEADER_MAGIC,
    SPARSE_HEADER_MAJOR_VER, SPARSE_HEADER_MINOR_VER,
};

const IO_BUF_SIZE: usize = 1024 * 1024;
const ZERO_BUF_SIZE: usize = 64 * 1024;

trait RawSink: Write {
    fn skip(&mut self, bytes: u64) -> io::Result<()>;
    fn finish_len(&mut self, total_len: u64) -> io::Result<()>;
}

struct SeekFileSink {
    writer: BufWriter<File>,
    pos: u64,
}

impl SeekFileSink {
    fn new(file: File) -> Self {
        Self {
            writer: BufWriter::with_capacity(IO_BUF_SIZE, file),
            pos: 0,
        }
    }
}

impl Write for SeekFileSink {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        let n = self.writer.write(buf)?;
        self.pos += n as u64;
        Ok(n)
    }

    fn flush(&mut self) -> io::Result<()> {
        self.writer.flush()
    }
}

impl RawSink for SeekFileSink {
    fn skip(&mut self, bytes: u64) -> io::Result<()> {
        if bytes > 0 {
            self.writer.flush()?;
            self.writer
                .get_mut()
                .seek(SeekFrom::Current(bytes as i64))?;
            self.pos += bytes;
        }
        Ok(())
    }

    fn finish_len(&mut self, total_len: u64) -> io::Result<()> {
        self.writer.flush()?;
        self.writer.get_mut().set_len(total_len)
    }
}

struct StreamSink<W: Write> {
    writer: W,
    pos: u64,
    zeros: Vec<u8>,
}

impl<W: Write> StreamSink<W> {
    fn new(writer: W) -> Self {
        Self {
            writer,
            pos: 0,
            zeros: vec![0u8; ZERO_BUF_SIZE],
        }
    }
}

impl<W: Write> Write for StreamSink<W> {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        let n = self.writer.write(buf)?;
        self.pos += n as u64;
        Ok(n)
    }

    fn flush(&mut self) -> io::Result<()> {
        self.writer.flush()
    }
}

impl<W: Write> RawSink for StreamSink<W> {
    fn skip(&mut self, mut bytes: u64) -> io::Result<()> {
        while bytes > 0 {
            let chunk = (bytes as usize).min(self.zeros.len());
            self.writer.write_all(&self.zeros[..chunk])?;
            self.pos += chunk as u64;
            bytes -= chunk as u64;
        }
        Ok(())
    }

    fn finish_len(&mut self, total_len: u64) -> io::Result<()> {
        if self.pos < total_len {
            let rem = total_len - self.pos;
            self.skip(rem)?;
        }
        self.writer.flush()
    }
}

struct CachedSourceFile {
    path: Option<PathBuf>,
    file: Option<File>,
}

impl CachedSourceFile {
    fn new() -> Self {
        Self {
            path: None,
            file: None,
        }
    }

    fn open_at(&mut self, path: &Path, offset: u64) -> io::Result<&mut File> {
        let need_open = match &self.path {
            Some(p) => p != path,
            None => true,
        };
        if need_open {
            let f = File::open(path).map_err(|e| {
                io::Error::new(e.kind(), format!("failed to open {}: {}", path.display(), e))
            })?;
            self.path = Some(path.to_path_buf());
            self.file = Some(f);
        }
        let f = self.file.as_mut().unwrap();
        f.seek(SeekFrom::Start(offset))?;
        Ok(f)
    }
}

fn write_chunk_header(w: &mut dyn Write, chunk_type: u16, chunk_sz: u32, total_sz: u32) -> io::Result<()> {
    let mut hdr = [0u8; 12];
    hdr[0..2].copy_from_slice(&chunk_type.to_le_bytes());
    hdr[4..8].copy_from_slice(&chunk_sz.to_le_bytes());
    hdr[8..12].copy_from_slice(&total_sz.to_le_bytes());
    w.write_all(&hdr)
}

fn write_zeros_with_crc(
    w: &mut dyn Write,
    mut len: usize,
    zero_buf: &[u8],
    crc: Option<&mut Hasher>,
) -> io::Result<()> {
    let mut crc = crc;
    while len > 0 {
        let chunk = len.min(zero_buf.len());
        w.write_all(&zero_buf[..chunk])?;
        if let Some(ref mut hasher) = crc {
            hasher.update(&zero_buf[..chunk]);
        }
        len -= chunk;
    }
    Ok(())
}

impl SparseFile {
    pub fn write_to_file(&mut self, file: File, gz: bool, sparse: bool, crc: bool) -> io::Result<()> {
        if gz {
            let buf = BufWriter::with_capacity(IO_BUF_SIZE, file);
            let mut gz_writer = GzEncoder::new(buf, Compression::best());
            {
                let mut sink = StreamSink::new(&mut gz_writer);
                self.write_to_sink(&mut sink, sparse, crc)?;
            }
            let mut inner = gz_writer.finish()?;
            inner.flush()?;
            Ok(())
        } else {
            let mut sink = SeekFileSink::new(file);
            self.write_to_sink(&mut sink, sparse, crc)?;
            sink.flush()
        }
    }

    pub fn write_to_stream<W: Write>(
        &mut self,
        writer: W,
        gz: bool,
        sparse: bool,
        crc: bool,
    ) -> io::Result<()> {
        if gz {
            let buf = BufWriter::with_capacity(IO_BUF_SIZE, writer);
            let mut gz_writer = GzEncoder::new(buf, Compression::best());
            {
                let mut sink = StreamSink::new(&mut gz_writer);
                self.write_to_sink(&mut sink, sparse, crc)?;
            }
            let mut inner = gz_writer.finish()?;
            inner.flush()?;
            Ok(())
        } else {
            let buf = BufWriter::with_capacity(IO_BUF_SIZE, writer);
            let mut sink = StreamSink::new(buf);
            self.write_to_sink(&mut sink, sparse, crc)?;
            sink.flush()
        }
    }

    fn write_to_sink(&mut self, sink: &mut dyn RawSink, sparse: bool, crc: bool) -> io::Result<()> {
        self.finalize();

        let bs = self.block_size;
        let total_len = self.len as u64;

        if sparse {
            let mut total_chunks = self.count_chunks();
            if crc {
                total_chunks += 1;
            }
            let total_blks = ((total_len + bs as u64 - 1) / bs as u64) as u32;

            let mut hdr = [0u8; 28];
            hdr[0..4].copy_from_slice(&SPARSE_HEADER_MAGIC.to_le_bytes());
            hdr[4..6].copy_from_slice(&SPARSE_HEADER_MAJOR_VER.to_le_bytes());
            hdr[6..8].copy_from_slice(&SPARSE_HEADER_MINOR_VER.to_le_bytes());
            hdr[8..10].copy_from_slice(&SPARSE_HEADER_LEN.to_le_bytes());
            hdr[10..12].copy_from_slice(&CHUNK_HEADER_LEN.to_le_bytes());
            hdr[12..16].copy_from_slice(&bs.to_le_bytes());
            hdr[16..20].copy_from_slice(&total_blks.to_le_bytes());
            hdr[20..24].copy_from_slice(&total_chunks.to_le_bytes());
            hdr[24..28].copy_from_slice(&0u32.to_le_bytes());
            sink.write_all(&hdr)?;
        }

        let mut hasher = if sparse && crc { Some(Hasher::new()) } else { None };
        let mut last_block = 0u32;
        let mut io_buf = vec![0u8; IO_BUF_SIZE];
        let zero_buf = vec![0u8; ZERO_BUF_SIZE];
        let mut fill_buf: Vec<u8> = Vec::new();
        let mut cached_src = CachedSourceFile::new();

        for bb in &self.blocks {
            if bb.block > last_block {
                let skip_blocks = bb.block - last_block;
                let skip_bytes = skip_blocks as u64 * bs as u64;
                if sparse {
                    write_chunk_header(sink, CHUNK_TYPE_DONT_CARE, skip_blocks, CHUNK_HEADER_LEN as u32)?;
                } else {
                    sink.skip(skip_bytes)?;
                }
            }

            let rnd_up_len = ((bb.len + bs - 1) / bs) * bs;
            let zero_len = (rnd_up_len - bb.len) as usize;

            match &bb.kind {
                BackedKind::Data { buf: shared, start } => {
                    let data = shared.borrow();
                    let slice = &data[*start..*start + bb.len as usize];
                    if sparse {
                        write_chunk_header(
                            sink,
                            CHUNK_TYPE_RAW,
                            rnd_up_len / bs,
                            CHUNK_HEADER_LEN as u32 + rnd_up_len,
                        )?;
                        sink.write_all(slice)?;
                        if zero_len > 0 {
                            write_zeros_with_crc(sink, zero_len, &zero_buf, None)?;
                        }
                        if let Some(ref mut h) = hasher {
                            h.update(slice);
                            if zero_len > 0 {
                                let mut rem = zero_len;
                                while rem > 0 {
                                    let c = rem.min(zero_buf.len());
                                    h.update(&zero_buf[..c]);
                                    rem -= c;
                                }
                            }
                        }
                    } else {
                        sink.write_all(slice)?;
                        if zero_len > 0 {
                            sink.skip(zero_len as u64)?;
                        }
                    }
                }
                BackedKind::File { path, offset } => {
                    if sparse {
                        write_chunk_header(
                            sink,
                            CHUNK_TYPE_RAW,
                            rnd_up_len / bs,
                            CHUNK_HEADER_LEN as u32 + rnd_up_len,
                        )?;
                    }
                    let f = cached_src.open_at(path, *offset)?;
                    let mut remaining = bb.len as usize;
                    while remaining > 0 {
                        let to_read = remaining.min(io_buf.len());
                        f.read_exact(&mut io_buf[..to_read])?;
                        sink.write_all(&io_buf[..to_read])?;
                        if let Some(ref mut h) = hasher {
                            h.update(&io_buf[..to_read]);
                        }
                        remaining -= to_read;
                    }
                    if zero_len > 0 {
                        if sparse {
                            write_zeros_with_crc(sink, zero_len, &zero_buf, hasher.as_mut())?;
                        } else {
                            sink.skip(zero_len as u64)?;
                        }
                    }
                }
                BackedKind::OpenFile { file, offset } => {
                    if sparse {
                        write_chunk_header(
                            sink,
                            CHUNK_TYPE_RAW,
                            rnd_up_len / bs,
                            CHUNK_HEADER_LEN as u32 + rnd_up_len,
                        )?;
                    }
                    let mut f = file.borrow_mut();
                    f.seek(SeekFrom::Start(*offset))?;
                    let mut remaining = bb.len as usize;
                    while remaining > 0 {
                        let to_read = remaining.min(io_buf.len());
                        f.read_exact(&mut io_buf[..to_read])?;
                        sink.write_all(&io_buf[..to_read])?;
                        if let Some(ref mut h) = hasher {
                            h.update(&io_buf[..to_read]);
                        }
                        remaining -= to_read;
                    }
                    if zero_len > 0 {
                        if sparse {
                            write_zeros_with_crc(sink, zero_len, &zero_buf, hasher.as_mut())?;
                        } else {
                            sink.skip(zero_len as u64)?;
                        }
                    }
                }
                BackedKind::Fill(val) => {
                    if sparse {
                        write_chunk_header(
                            sink,
                            CHUNK_TYPE_FILL,
                            rnd_up_len / bs,
                            CHUNK_HEADER_LEN as u32 + 4,
                        )?;
                        let val_bytes = val.to_le_bytes();
                        sink.write_all(&val_bytes)?;
                        if let Some(ref mut h) = hasher {
                            let count = bs / 4;
                            for _ in 0..count {
                                h.update(&val_bytes);
                            }
                        }
                    } else {
                        if fill_buf.len() != bs as usize {
                            fill_buf.resize(bs as usize, 0);
                        }
                        let val_bytes = val.to_le_bytes();
                        for chunk in fill_buf.chunks_exact_mut(4) {
                            chunk.copy_from_slice(&val_bytes);
                        }
                        let mut rem = bb.len as usize;
                        while rem > 0 {
                            let wlen = rem.min(bs as usize);
                            sink.write_all(&fill_buf[..wlen])?;
                            rem -= wlen;
                        }
                        if zero_len > 0 {
                            sink.skip(zero_len as u64)?;
                        }
                    }
                }
            }

            last_block = bb.block + (bb.len + bs - 1) / bs;
        }

        let tail_bytes = total_len.saturating_sub(last_block as u64 * bs as u64);
        if sparse {
            if tail_bytes > 0 {
                let skip_blocks = (tail_bytes / bs as u64) as u32;
                write_chunk_header(sink, CHUNK_TYPE_DONT_CARE, skip_blocks, CHUNK_HEADER_LEN as u32)?;
            }
            if let Some(h) = hasher {
                let crc_val = h.finalize();
                write_chunk_header(sink, CHUNK_TYPE_CRC32, 0, CHUNK_HEADER_LEN as u32 + 4)?;
                sink.write_all(&crc_val.to_le_bytes())?;
            }
        } else {
            sink.finish_len(total_len)?;
        }

        Ok(())
    }
}
