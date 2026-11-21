use std::io::{self, Read, Seek, SeekFrom, Write};

use crate::crc32::{sparse_crc32, sparse_crc32_fill, sparse_crc32_zeros};
use crate::format::{
    ChunkHeader, ChunkType, FileHeader, SparseError, CHUNK_HEADER_SIZE, FILE_HEADER_SIZE,
};

const IO_BUF_SIZE: usize = 64 * 1024;

/// A parsed chunk header and inline metadata yielded by [`SparseReader::next_chunk`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ParsedChunk {
    /// Raw block data (`data_len` bytes) remains to be read from the reader.
    Raw { header: ChunkHeader, data_len: u64 },
    /// Fill `header.chunk_blocks` blocks with the repeated 32-bit little-endian `value`.
    Fill { header: ChunkHeader, value: u32 },
    /// Skip `header.chunk_blocks` blocks (don't care / implicit zeros).
    DontCare { header: ChunkHeader },
    /// Expected running CRC-32 checksum up to this point.
    Crc32 { header: ChunkHeader, checksum: u32 },
}

/// Streaming reader for Android sparse images.
pub struct SparseReader<R> {
    reader: R,
    header: FileHeader,
    extra_chunk_header_bytes: usize,
    chunks_read: u32,
    current_block: u32,
    running_crc: u32,
    verify_crc: bool,
    remaining_raw_bytes: u64,
}

impl<R: Read> SparseReader<R> {
    /// Opens a sparse stream by reading and validating its file header.
    pub fn new(mut reader: R, verify_crc: bool) -> Result<Self, SparseError> {
        let mut hdr_bytes = [0u8; FILE_HEADER_SIZE];
        reader.read_exact(&mut hdr_bytes)?;
        let (header, file_hdr_sz, chunk_hdr_sz) = FileHeader::from_bytes_ext(&hdr_bytes)?;
        let extra_file = usize::from(file_hdr_sz) - FILE_HEADER_SIZE;
        if extra_file > 0 {
            skip_exact(&mut reader, extra_file)?;
        }
        Ok(Self {
            reader,
            header,
            extra_chunk_header_bytes: usize::from(chunk_hdr_sz) - CHUNK_HEADER_SIZE,
            chunks_read: 0,
            current_block: 0,
            running_crc: 0,
            verify_crc,
            remaining_raw_bytes: 0,
        })
    }

    /// Returns the parsed [`FileHeader`].
    pub fn header(&self) -> &FileHeader {
        &self.header
    }

    /// Returns the current expanded output block index.
    pub fn current_block(&self) -> u32 {
        self.current_block
    }

    /// Returns the running CRC-32 checksum computed so far (when `verify_crc` is enabled).
    pub fn running_crc(&self) -> u32 {
        self.running_crc
    }

    /// Reads and validates the next chunk header and inline payload (for Fill/Crc32).
    pub fn next_chunk(&mut self) -> Result<Option<ParsedChunk>, SparseError> {
        if self.remaining_raw_bytes > 0 {
            return Err(SparseError::UnconsumedChunkData);
        }
        if self.chunks_read == self.header.total_chunks {
            if self.current_block != self.header.total_blocks {
                return Err(SparseError::BlockCountMismatch {
                    header_blocks: self.header.total_blocks,
                    actual_blocks: self.current_block,
                });
            }
            if self.verify_crc
                && self.header.image_checksum != 0
                && self.running_crc != self.header.image_checksum
            {
                return Err(SparseError::ChecksumMismatch {
                    expected: self.header.image_checksum,
                    actual: self.running_crc,
                });
            }
            return Ok(None);
        }

        let mut hdr_bytes = [0u8; CHUNK_HEADER_SIZE];
        self.reader.read_exact(&mut hdr_bytes)?;
        let chunk = ChunkHeader::from_bytes(&hdr_bytes)?;
        if self.extra_chunk_header_bytes > 0 {
            skip_exact(&mut self.reader, self.extra_chunk_header_bytes)?;
        }

        let hdr_sz = CHUNK_HEADER_SIZE as u64 + self.extra_chunk_header_bytes as u64;
        let out_bytes = chunk.output_size(self.header.block_size);
        let next_block = self
            .current_block
            .checked_add(chunk.chunk_blocks)
            .ok_or(SparseError::OutOfBounds)?;
        if next_block > self.header.total_blocks {
            return Err(SparseError::BlockCountMismatch {
                header_blocks: self.header.total_blocks,
                actual_blocks: next_block,
            });
        }

        self.chunks_read += 1;
        self.current_block = next_block;

        match chunk.chunk_type {
            ChunkType::Raw => {
                let expected = hdr_sz.checked_add(out_bytes).ok_or(SparseError::InputTooBig)?;
                if u64::from(chunk.total_size) != expected {
                    return Err(SparseError::InvalidChunkSize {
                        chunk_type: ChunkType::Raw,
                        total_size: chunk.total_size,
                        expected,
                    });
                }
                self.remaining_raw_bytes = out_bytes;
                Ok(Some(ParsedChunk::Raw {
                    header: chunk,
                    data_len: out_bytes,
                }))
            }
            ChunkType::Fill => {
                let expected = hdr_sz + 4;
                if u64::from(chunk.total_size) != expected {
                    return Err(SparseError::InvalidChunkSize {
                        chunk_type: ChunkType::Fill,
                        total_size: chunk.total_size,
                        expected,
                    });
                }
                let mut val_bytes = [0u8; 4];
                self.reader.read_exact(&mut val_bytes)?;
                let value = u32::from_le_bytes(val_bytes);
                if self.verify_crc {
                    self.running_crc = sparse_crc32_fill(self.running_crc, value, out_bytes);
                }
                Ok(Some(ParsedChunk::Fill {
                    header: chunk,
                    value,
                }))
            }
            ChunkType::DontCare => {
                if u64::from(chunk.total_size) != hdr_sz {
                    return Err(SparseError::InvalidChunkSize {
                        chunk_type: ChunkType::DontCare,
                        total_size: chunk.total_size,
                        expected: hdr_sz,
                    });
                }
                if self.verify_crc {
                    self.running_crc = sparse_crc32_zeros(self.running_crc, out_bytes);
                }
                Ok(Some(ParsedChunk::DontCare { header: chunk }))
            }
            ChunkType::Crc32 => {
                let expected = hdr_sz + 4;
                if chunk.chunk_blocks != 0 || u64::from(chunk.total_size) != expected {
                    return Err(SparseError::InvalidChunkSize {
                        chunk_type: ChunkType::Crc32,
                        total_size: chunk.total_size,
                        expected,
                    });
                }
                let mut crc_bytes = [0u8; 4];
                self.reader.read_exact(&mut crc_bytes)?;
                let checksum = u32::from_le_bytes(crc_bytes);
                if self.verify_crc && self.running_crc != checksum {
                    return Err(SparseError::ChecksumMismatch {
                        expected: checksum,
                        actual: self.running_crc,
                    });
                }
                Ok(Some(ParsedChunk::Crc32 {
                    header: chunk,
                    checksum,
                }))
            }
        }
    }

    /// Streams the raw payload of the active [`ParsedChunk::Raw`] chunk into `writer`.
    pub fn copy_raw_data<W: Write>(&mut self, writer: &mut W) -> Result<(), SparseError> {
        if self.remaining_raw_bytes == 0 {
            return Err(SparseError::NoActiveRawChunk);
        }
        let mut buf = vec![0u8; IO_BUF_SIZE];
        while self.remaining_raw_bytes > 0 {
            let want = (self.remaining_raw_bytes as usize).min(buf.len());
            self.reader.read_exact(&mut buf[..want])?;
            if self.verify_crc {
                self.running_crc = sparse_crc32(self.running_crc, &buf[..want]);
            }
            writer.write_all(&buf[..want])?;
            self.remaining_raw_bytes -= want as u64;
        }
        Ok(())
    }

    /// Reads the raw payload of the active [`ParsedChunk::Raw`] chunk into a `Vec<u8>`.
    pub fn read_raw_data(&mut self) -> Result<Vec<u8>, SparseError> {
        if self.remaining_raw_bytes == 0 {
            return Err(SparseError::NoActiveRawChunk);
        }
        let len = usize::try_from(self.remaining_raw_bytes).map_err(|_| SparseError::InputTooBig)?;
        let mut buf = vec![0u8; len];
        self.reader.read_exact(&mut buf)?;
        if self.verify_crc {
            self.running_crc = sparse_crc32(self.running_crc, &buf);
        }
        self.remaining_raw_bytes = 0;
        Ok(buf)
    }

    /// Consumes and discards the raw payload of the active [`ParsedChunk::Raw`] chunk.
    pub fn skip_raw_data(&mut self) -> Result<(), SparseError> {
        if self.remaining_raw_bytes == 0 {
            return Err(SparseError::NoActiveRawChunk);
        }
        let mut buf = vec![0u8; IO_BUF_SIZE];
        while self.remaining_raw_bytes > 0 {
            let want = (self.remaining_raw_bytes as usize).min(buf.len());
            self.reader.read_exact(&mut buf[..want])?;
            if self.verify_crc {
                self.running_crc = sparse_crc32(self.running_crc, &buf[..want]);
            }
            self.remaining_raw_bytes -= want as u64;
        }
        Ok(())
    }
}

fn skip_exact<R: Read>(reader: &mut R, count: usize) -> io::Result<()> {
    let mut left = count;
    let mut scratch = [0u8; 256];
    while left > 0 {
        let step = left.min(scratch.len());
        reader.read_exact(&mut scratch[..step])?;
        left -= step;
    }
    Ok(())
}

/// Peeks the first 28 bytes of `reader` (then rewinds) to check if it starts with a valid
/// Android sparse file header.
pub fn is_sparse_image<R: Read + Seek>(reader: &mut R) -> Result<bool, SparseError> {
    let pos = reader.stream_position()?;
    let mut hdr = [0u8; FILE_HEADER_SIZE];
    let res = match reader.read_exact(&mut hdr) {
        Ok(()) => Ok(FileHeader::from_bytes_ext(&hdr).is_ok()),
        Err(err) if err.kind() == io::ErrorKind::UnexpectedEof => Ok(false),
        Err(err) => Err(SparseError::Io(err)),
    };
    reader.seek(SeekFrom::Start(pos))?;
    res
}

/// Expands a sparse image stream into a seekable writer.
pub fn unsparse<R: Read, W: Write + Seek>(
    reader: R,
    writer: &mut W,
    verify_crc: bool,
) -> Result<FileHeader, SparseError> {
    let mut sr = SparseReader::new(reader, verify_crc)?;
    let header = sr.header().clone();
    let block_size = header.block_size.get();
    let mut fill_buf = vec![0u8; IO_BUF_SIZE];

    while let Some(chunk) = sr.next_chunk()? {
        match chunk {
            ParsedChunk::Raw { data_len, .. } => {
                if data_len > 0 {
                    sr.copy_raw_data(writer)?;
                }
            }
            ParsedChunk::Fill {
                header: ch,
                value,
            } => {
                let total_bytes = ch.output_size(header.block_size);
                write_fill(writer, value, total_bytes, &mut fill_buf)?;
            }
            ParsedChunk::DontCare { header: ch } => {
                let skip = i64::try_from(ch.output_size(header.block_size))
                    .map_err(|_| SparseError::InputTooBig)?;
                if skip > 0 {
                    // Ensure sparse/truncated files reach full expanded size if DontCare is last.
                    let cur = writer.stream_position()?;
                    let target = cur
                        .checked_add(skip as u64)
                        .ok_or(SparseError::InputTooBig)?;
                    let end = writer.seek(SeekFrom::End(0))?;
                    if end < target {
                        writer.seek(SeekFrom::Start(target - 1))?;
                        writer.write_all(&[0])?;
                    } else {
                        writer.seek(SeekFrom::Start(target))?;
                    }
                }
            }
            ParsedChunk::Crc32 { .. } => {}
        }
    }

    let expected_size = u64::from(header.total_blocks) * u64::from(block_size);
    let end = writer.seek(SeekFrom::End(0))?;
    if end < expected_size {
        writer.seek(SeekFrom::Start(expected_size - 1))?;
        writer.write_all(&[0])?;
    }

    Ok(header)
}

fn write_fill<W: Write>(
    writer: &mut W,
    value: u32,
    mut byte_len: u64,
    buf: &mut [u8],
) -> io::Result<()> {
    if byte_len == 0 {
        return Ok(());
    }
    let pattern = value.to_le_bytes();
    for chunk in buf.chunks_exact_mut(4) {
        chunk.copy_from_slice(&pattern);
    }
    while byte_len >= buf.len() as u64 {
        writer.write_all(buf)?;
        byte_len -= buf.len() as u64;
    }
    if byte_len > 0 {
        writer.write_all(&buf[..byte_len as usize])?;
    }
    Ok(())
}
