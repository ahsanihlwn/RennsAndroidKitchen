//! Idiomatic `std::io::{Read, Write}` streaming adapters and convenience helpers.

use std::io::{self, Error, ErrorKind, Read, Write};

use crate::common::constants::BROTLI_MAX_WINDOW_BITS;
use crate::common::shared_dictionary::BrotliSharedDictionaryType;
use crate::dec::decode::brotli_decoder_decompress;
use crate::dec::state::{BrotliDecoderParameter, BrotliDecoderResult, BrotliDecoderState};
use crate::enc::encode::{
    BrotliEncoderState, brotli_encoder_compress, brotli_encoder_compress_with_params,
};
use crate::enc::params::{
    BrotliEncoderMode, BrotliEncoderOperation, BrotliEncoderParameter, BrotliEncoderParams,
};

const DEFAULT_IO_BUFFER_SIZE: usize = 65536;

/// Convenience one-shot decompression returning owned `Vec<u8>`.
pub fn decompress(input: &[u8]) -> io::Result<Vec<u8>> {
    brotli_decoder_decompress(input)
        .map_err(|e| Error::new(ErrorKind::InvalidData, e.as_str()))
}

/// Convenience one-shot compression with quality, window bits, and mode.
pub fn compress(
    input: &[u8],
    quality: i32,
    lgwin: i32,
    mode: BrotliEncoderMode,
) -> io::Result<Vec<u8>> {
    brotli_encoder_compress(quality, lgwin, mode, input)
        .map_err(|_| Error::new(ErrorKind::InvalidInput, "brotli compression failed"))
}

/// Convenience one-shot compression with full `BrotliEncoderParams`.
pub fn compress_with_params(input: &[u8], params: &BrotliEncoderParams) -> io::Result<Vec<u8>> {
    brotli_encoder_compress_with_params(params, input)
        .map_err(|_| Error::new(ErrorKind::InvalidInput, "brotli compression failed"))
}

/// Streaming Brotli decompressor implementing `std::io::Read`.
pub struct BrotliReader<R> {
    inner: R,
    state: BrotliDecoderState,
    in_buf: Vec<u8>,
    in_pos: usize,
    in_len: usize,
    eof_reached: bool,
    finished: bool,
}

impl<R: Read> BrotliReader<R> {
    pub fn new(inner: R) -> Self {
        Self::with_buffer_size(inner, DEFAULT_IO_BUFFER_SIZE)
    }

    pub fn with_buffer_size(inner: R, buf_size: usize) -> Self {
        Self {
            inner,
            state: BrotliDecoderState::new(),
            in_buf: vec![0u8; buf_size.max(64)],
            in_pos: 0,
            in_len: 0,
            eof_reached: false,
            finished: false,
        }
    }

    pub fn set_parameter(&mut self, param: BrotliDecoderParameter, value: u32) -> bool {
        self.state.set_parameter(param, value)
    }

    pub fn attach_raw_dictionary(&mut self, dict: &[u8]) -> bool {
        self.state
            .attach_dictionary(BrotliSharedDictionaryType::Raw, dict)
    }

    pub fn get_ref(&self) -> &R {
        &self.inner
    }

    pub fn get_mut(&mut self) -> &mut R {
        &mut self.inner
    }

    pub fn into_inner(self) -> R {
        self.inner
    }
}

impl<R: Read> Read for BrotliReader<R> {
    fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
        if buf.is_empty() || self.finished {
            return Ok(0);
        }

        loop {
            let mut out_pos = 0usize;
            let res = self.state.decompress_stream(
                &self.in_buf[..self.in_len],
                &mut self.in_pos,
                buf,
                &mut out_pos,
                None,
            );

            match res {
                BrotliDecoderResult::Success => {
                    self.finished = true;
                    return Ok(out_pos);
                }
                BrotliDecoderResult::NeedsMoreOutput => {
                    return Ok(out_pos);
                }
                BrotliDecoderResult::NeedsMoreInput => {
                    if out_pos > 0 {
                        return Ok(out_pos);
                    }
                    if self.eof_reached {
                        return Err(Error::new(
                            ErrorKind::UnexpectedEof,
                            "unexpected EOF in brotli stream",
                        ));
                    }
                    self.in_pos = 0;
                    self.in_len = 0;
                    loop {
                        match self.inner.read(&mut self.in_buf) {
                            Ok(0) => {
                                self.eof_reached = true;
                                break;
                            }
                            Ok(n) => {
                                self.in_len = n;
                                break;
                            }
                            Err(ref e) if e.kind() == ErrorKind::Interrupted => continue,
                            Err(e) => return Err(e),
                        }
                    }
                }
                BrotliDecoderResult::Error => {
                    return Err(Error::new(
                        ErrorKind::InvalidData,
                        self.state.error_code.as_str(),
                    ));
                }
            }
        }
    }
}

/// Streaming Brotli compressor implementing `std::io::Write`.
pub struct BrotliWriter<W: Write> {
    inner: Option<W>,
    state: BrotliEncoderState,
    out_buf: Vec<u8>,
    finished: bool,
}

impl<W: Write> BrotliWriter<W> {
    pub fn new(inner: W) -> Self {
        Self::with_params(inner, 11, 22, BrotliEncoderMode::Generic)
    }

    pub fn with_params(inner: W, quality: i32, lgwin: i32, mode: BrotliEncoderMode) -> Self {
        Self::with_buffer_size(inner, DEFAULT_IO_BUFFER_SIZE, quality, lgwin, mode)
    }

    pub fn with_buffer_size(
        inner: W,
        buf_size: usize,
        quality: i32,
        lgwin: i32,
        mode: BrotliEncoderMode,
    ) -> Self {
        let mut state = BrotliEncoderState::new();
        state.set_parameter(BrotliEncoderParameter::Quality, quality as u32);
        state.set_parameter(BrotliEncoderParameter::Lgwin, lgwin as u32);
        state.set_parameter(BrotliEncoderParameter::Mode, mode as u32);
        if lgwin > BROTLI_MAX_WINDOW_BITS {
            state.set_parameter(BrotliEncoderParameter::LargeWindow, 1);
        }
        Self {
            inner: Some(inner),
            state,
            out_buf: vec![0u8; buf_size.max(1024)],
            finished: false,
        }
    }

    pub fn from_params(inner: W, params: BrotliEncoderParams) -> Self {
        let mut state = BrotliEncoderState::new();
        state.params = params;
        if state.params.lgwin > BROTLI_MAX_WINDOW_BITS {
            state.params.large_window = true;
        }
        Self {
            inner: Some(inner),
            state,
            out_buf: vec![0u8; DEFAULT_IO_BUFFER_SIZE],
            finished: false,
        }
    }

    pub fn set_parameter(&mut self, param: BrotliEncoderParameter, value: u32) -> bool {
        self.state.set_parameter(param, value)
    }

    pub fn get_ref(&self) -> Option<&W> {
        self.inner.as_ref()
    }

    pub fn get_mut(&mut self) -> Option<&mut W> {
        self.inner.as_mut()
    }

    fn drive_compress(&mut self, op: BrotliEncoderOperation, buf: &[u8]) -> io::Result<usize> {
        let mut in_pos = 0usize;
        let inner = self
            .inner
            .as_mut()
            .ok_or_else(|| Error::new(ErrorKind::BrokenPipe, "writer already consumed"))?;
        loop {
            let mut out_pos = 0usize;
            if !self.state.compress_stream(
                op,
                buf,
                &mut in_pos,
                &mut self.out_buf,
                &mut out_pos,
                None,
            ) {
                return Err(Error::new(
                    ErrorKind::InvalidInput,
                    "brotli compression error",
                ));
            }
            if out_pos > 0 {
                inner.write_all(&self.out_buf[..out_pos])?;
            }
            let done = match op {
                BrotliEncoderOperation::Process => {
                    in_pos == buf.len() && !self.state.has_more_output()
                }
                BrotliEncoderOperation::Flush | BrotliEncoderOperation::EmitMetadata => {
                    in_pos == buf.len() && !self.state.has_more_output()
                }
                BrotliEncoderOperation::Finish => self.state.is_finished(),
            };
            if done {
                return Ok(in_pos);
            }
        }
    }

    fn finish_inner(&mut self) -> io::Result<()> {
        if !self.finished {
            self.drive_compress(BrotliEncoderOperation::Finish, &[])?;
            if let Some(inner) = self.inner.as_mut() {
                inner.flush()?;
            }
            self.finished = true;
        }
        Ok(())
    }

    /// Finishes the brotli stream and returns the underlying writer.
    pub fn finish(mut self) -> io::Result<W> {
        self.finish_inner()?;
        self.inner
            .take()
            .ok_or_else(|| Error::new(ErrorKind::BrokenPipe, "writer already taken"))
    }
}

impl<W: Write> Write for BrotliWriter<W> {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        if self.finished {
            return Err(Error::new(
                ErrorKind::BrokenPipe,
                "cannot write to finished BrotliWriter",
            ));
        }
        if buf.is_empty() {
            return Ok(0);
        }
        self.drive_compress(BrotliEncoderOperation::Process, buf)
    }

    fn flush(&mut self) -> io::Result<()> {
        if self.finished {
            return Ok(());
        }
        self.drive_compress(BrotliEncoderOperation::Flush, &[])?;
        if let Some(inner) = self.inner.as_mut() {
            inner.flush()?;
        }
        Ok(())
    }
}

impl<W: Write> Drop for BrotliWriter<W> {
    fn drop(&mut self) {
        if self.inner.is_some() && !self.finished {
            let _ = self.finish_inner();
        }
    }
}
