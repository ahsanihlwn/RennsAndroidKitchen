// SPDX-License-Identifier: MPL-2.0
//! Custom two-pass Gaussian shader on Qt's current desktop OpenGL context.
//! No compositor material, tint, noise, window, input handling or font code.
use std::cell::RefCell;
use std::ffi::{c_char, c_void, CString};
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::ptr;

thread_local! { static ERROR: RefCell<CString> = RefCell::new(CString::new("").unwrap()); }
fn error(message: &str) {
    ERROR.with(|slot| *slot.borrow_mut() = CString::new(message.replace('\0', " ")).unwrap());
}

#[cfg(windows)]
mod platform {
    use super::*;
    #[link(name = "opengl32")]
    extern "system" {
        fn wglGetProcAddress(name: *const c_char) -> *const c_void;
        pub fn wglGetCurrentContext() -> *const c_void;
    }
    #[link(name = "kernel32")]
    extern "system" {
        fn GetModuleHandleA(name: *const c_char) -> *const c_void;
        fn GetProcAddress(module: *const c_void, name: *const c_char) -> *const c_void;
    }
    pub unsafe fn load(name: &str) -> *const c_void {
        let name = CString::new(name).unwrap();
        let address = wglGetProcAddress(name.as_ptr());
        if address as usize > 3 && address as usize != usize::MAX {
            return address;
        }
        GetProcAddress(
            GetModuleHandleA(b"opengl32.dll\0".as_ptr().cast()),
            name.as_ptr(),
        )
    }
}

// Pair adjacent taps into one bilinear fetch, with an exact weighted offset.
// This reduces texture reads without replacing Gaussian with a box blur.
fn kernel(sigma: f32) -> (Vec<f32>, Vec<f32>) {
    if sigma <= 0.01 {
        return (vec![1.0], vec![0.0]);
    }
    let radius = (sigma * 3.0).ceil().min(64.0) as usize;
    let taps: Vec<f32> = (0..=radius)
        .map(|i| (-(i as f32).powi(2) / (2.0 * sigma * sigma)).exp())
        .collect();
    let total = taps[0] + 2.0 * taps.iter().skip(1).sum::<f32>();
    let mut weights = vec![taps[0] / total];
    let mut offsets = vec![0.0];
    for i in (1..=radius).step_by(2) {
        let second = taps.get(i + 1).copied().unwrap_or(0.0);
        let weight = taps[i] + second;
        weights.push(weight / total);
        offsets.push((i as f32 * taps[i] + (i + 1) as f32 * second) / weight);
    }
    (weights, offsets)
}

#[cfg(windows)]
struct Engine {
    context: usize,
    program: u32,
    vao: u32,
    fbo: u32,
    textures: [u32; 3],
    width: i32,
    height: i32,
    direction: i32,
    weights: i32,
    offsets: i32,
    pairs: i32,
}

#[cfg(windows)]
unsafe fn compile(kind: u32, source: &str) -> Result<u32, String> {
    let shader = gl::CreateShader(kind);
    let source = CString::new(source).unwrap();
    gl::ShaderSource(shader, 1, &source.as_ptr(), ptr::null());
    gl::CompileShader(shader);
    let mut ok = 0;
    gl::GetShaderiv(shader, gl::COMPILE_STATUS, &mut ok);
    if ok == 0 {
        let mut bytes = vec![0u8; 2048];
        gl::GetShaderInfoLog(
            shader,
            bytes.len() as i32,
            ptr::null_mut(),
            bytes.as_mut_ptr().cast(),
        );
        gl::DeleteShader(shader);
        return Err(String::from_utf8_lossy(&bytes)
            .trim_end_matches('\0')
            .to_owned());
    }
    Ok(shader)
}

#[cfg(windows)]
impl Engine {
    unsafe fn new() -> Result<Self, String> {
        let context = platform::wglGetCurrentContext() as usize;
        if context == 0 {
            return Err("No current Qt OpenGL context".into());
        }
        gl::load_with(|name| platform::load(name));
        if !gl::CreateShader::is_loaded() || !gl::GenVertexArrays::is_loaded() {
            return Err("OpenGL 3.3 shader functions unavailable".into());
        }
        let vertex = compile(gl::VERTEX_SHADER, include_str!("blur.vert"))?;
        let fragment = match compile(gl::FRAGMENT_SHADER, include_str!("blur.frag")) {
            Ok(shader) => shader,
            Err(e) => {
                gl::DeleteShader(vertex);
                return Err(e);
            }
        };
        let program = gl::CreateProgram();
        gl::AttachShader(program, vertex);
        gl::AttachShader(program, fragment);
        gl::LinkProgram(program);
        gl::DeleteShader(vertex);
        gl::DeleteShader(fragment);
        let mut linked = 0;
        gl::GetProgramiv(program, gl::LINK_STATUS, &mut linked);
        if linked == 0 {
            gl::DeleteProgram(program);
            return Err("Gaussian shader link failed".into());
        }
        let uniform = |name: &[u8]| gl::GetUniformLocation(program, name.as_ptr().cast());
        let mut engine = Self {
            context,
            program,
            vao: 0,
            fbo: 0,
            textures: [0; 3],
            width: 0,
            height: 0,
            direction: uniform(b"direction\0"),
            weights: uniform(b"weights\0"),
            offsets: uniform(b"offsets\0"),
            pairs: uniform(b"pairs\0"),
        };
        gl::GenVertexArrays(1, &mut engine.vao);
        gl::GenFramebuffers(1, &mut engine.fbo);
        gl::GenTextures(3, engine.textures.as_mut_ptr());
        Ok(engine)
    }

    unsafe fn run(
        &mut self,
        source: *const u8,
        output: *mut u8,
        width: i32,
        height: i32,
        stride: i32,
        sigma: f32,
    ) -> Result<(), String> {
        if platform::wglGetCurrentContext() as usize != self.context {
            return Err("Qt GPU context changed".into());
        }
        if source.is_null()
            || output.is_null()
            || width <= 0
            || height <= 0
            || width as i64 * height as i64 > 6_000_000
            || stride < width * 4
            || stride % 4 != 0
            || !sigma.is_finite()
        {
            return Err("Invalid or oversized Gaussian source".into());
        }
        let _state = State::save();
        gl::ActiveTexture(gl::TEXTURE0);
        gl::BindSampler(0, 0);
        gl::BindBuffer(gl::PIXEL_PACK_BUFFER, 0);
        gl::BindBuffer(gl::PIXEL_UNPACK_BUFFER, 0);
        gl::PixelStorei(gl::PACK_ALIGNMENT, 1);
        gl::PixelStorei(gl::PACK_ROW_LENGTH, 0);
        gl::PixelStorei(gl::UNPACK_ALIGNMENT, 1);
        gl::PixelStorei(gl::UNPACK_ROW_LENGTH, stride / 4);
        for name in [
            gl::PACK_SKIP_ROWS,
            gl::PACK_SKIP_PIXELS,
            gl::UNPACK_SKIP_ROWS,
            gl::UNPACK_SKIP_PIXELS,
        ] {
            gl::PixelStorei(name, 0);
        }
        if self.width != width || self.height != height {
            for &texture in &self.textures {
                gl::BindTexture(gl::TEXTURE_2D, texture);
                gl::TexParameteri(gl::TEXTURE_2D, gl::TEXTURE_MIN_FILTER, gl::LINEAR as i32);
                gl::TexParameteri(gl::TEXTURE_2D, gl::TEXTURE_MAG_FILTER, gl::LINEAR as i32);
                gl::TexParameteri(gl::TEXTURE_2D, gl::TEXTURE_WRAP_S, gl::CLAMP_TO_EDGE as i32);
                gl::TexParameteri(gl::TEXTURE_2D, gl::TEXTURE_WRAP_T, gl::CLAMP_TO_EDGE as i32);
                gl::TexImage2D(
                    gl::TEXTURE_2D,
                    0,
                    gl::RGBA8 as i32,
                    width,
                    height,
                    0,
                    gl::RGBA,
                    gl::UNSIGNED_BYTE,
                    ptr::null(),
                );
            }
            self.width = width;
            self.height = height;
        }
        gl::BindTexture(gl::TEXTURE_2D, self.textures[0]);
        gl::TexSubImage2D(
            gl::TEXTURE_2D,
            0,
            0,
            0,
            width,
            height,
            gl::RGBA,
            gl::UNSIGNED_BYTE,
            source.cast(),
        );
        for flag in [
            gl::BLEND,
            gl::DEPTH_TEST,
            gl::STENCIL_TEST,
            gl::SCISSOR_TEST,
            gl::CULL_FACE,
            gl::FRAMEBUFFER_SRGB,
            gl::DITHER,
            gl::RASTERIZER_DISCARD,
        ] {
            gl::Disable(flag);
        }
        gl::ColorMask(gl::TRUE, gl::TRUE, gl::TRUE, gl::TRUE);
        gl::PolygonMode(gl::FRONT_AND_BACK, gl::FILL);
        gl::Viewport(0, 0, width, height);
        gl::BindVertexArray(self.vao);
        gl::UseProgram(self.program);
        let (weights, offsets) = kernel(sigma.max(0.0));
        gl::Uniform1i(self.pairs, weights.len() as i32 - 1);
        gl::Uniform1fv(self.weights, weights.len() as i32, weights.as_ptr());
        gl::Uniform1fv(self.offsets, offsets.len() as i32, offsets.as_ptr());
        gl::BindFramebuffer(gl::FRAMEBUFFER, self.fbo);
        for pass in 0..2 {
            gl::FramebufferTexture2D(
                gl::FRAMEBUFFER,
                gl::COLOR_ATTACHMENT0,
                gl::TEXTURE_2D,
                self.textures[pass + 1],
                0,
            );
            if gl::CheckFramebufferStatus(gl::FRAMEBUFFER) != gl::FRAMEBUFFER_COMPLETE {
                return Err("Gaussian framebuffer incomplete".into());
            }
            gl::BindTexture(gl::TEXTURE_2D, self.textures[pass]);
            gl::Uniform2f(
                self.direction,
                if pass == 0 { 1.0 / width as f32 } else { 0.0 },
                if pass == 1 { 1.0 / height as f32 } else { 0.0 },
            );
            gl::DrawArrays(gl::TRIANGLES, 0, 3);
        }
        gl::ReadPixels(
            0,
            0,
            width,
            height,
            gl::RGBA,
            gl::UNSIGNED_BYTE,
            output.cast(),
        );
        let error = gl::GetError();
        if error != gl::NO_ERROR {
            return Err(format!("Gaussian OpenGL error 0x{error:X}"));
        }
        Ok(())
    }
}

#[cfg(windows)]
impl Drop for Engine {
    fn drop(&mut self) {
        unsafe {
            if platform::wglGetCurrentContext() as usize == self.context {
                gl::DeleteTextures(3, self.textures.as_ptr());
                gl::DeleteFramebuffers(1, &self.fbo);
                gl::DeleteVertexArrays(1, &self.vao);
                gl::DeleteProgram(self.program);
            }
        }
    }
}

#[cfg(windows)]
struct State {
    integers: Vec<(u32, i32)>,
    viewport: [i32; 4],
    mask: [u8; 4],
    enabled: Vec<(u32, bool)>,
    texture: i32,
    sampler: i32,
}
#[cfg(windows)]
impl State {
    unsafe fn save() -> Self {
        let mut integers = Vec::new();
        for name in [
            gl::DRAW_FRAMEBUFFER_BINDING,
            gl::READ_FRAMEBUFFER_BINDING,
            gl::CURRENT_PROGRAM,
            gl::VERTEX_ARRAY_BINDING,
            gl::ACTIVE_TEXTURE,
            gl::PACK_ALIGNMENT,
            gl::UNPACK_ALIGNMENT,
            gl::PACK_ROW_LENGTH,
            gl::UNPACK_ROW_LENGTH,
            gl::PACK_SKIP_ROWS,
            gl::PACK_SKIP_PIXELS,
            gl::UNPACK_SKIP_ROWS,
            gl::UNPACK_SKIP_PIXELS,
            gl::PIXEL_PACK_BUFFER_BINDING,
            gl::PIXEL_UNPACK_BUFFER_BINDING,
        ] {
            let mut value = 0;
            gl::GetIntegerv(name, &mut value);
            integers.push((name, value));
        }
        let mut viewport = [0; 4];
        gl::GetIntegerv(gl::VIEWPORT, viewport.as_mut_ptr());
        let mut mask = [0; 4];
        gl::GetBooleanv(gl::COLOR_WRITEMASK, mask.as_mut_ptr());
        let mut polygon = [0; 2];
        gl::GetIntegerv(gl::POLYGON_MODE, polygon.as_mut_ptr());
        integers.push((gl::POLYGON_MODE, polygon[0]));
        let enabled = [
            gl::BLEND,
            gl::DEPTH_TEST,
            gl::STENCIL_TEST,
            gl::SCISSOR_TEST,
            gl::CULL_FACE,
            gl::FRAMEBUFFER_SRGB,
            gl::DITHER,
            gl::RASTERIZER_DISCARD,
        ]
        .iter()
        .map(|&name| (name, gl::IsEnabled(name) != gl::FALSE))
        .collect();
        gl::ActiveTexture(gl::TEXTURE0);
        let mut texture = 0;
        gl::GetIntegerv(gl::TEXTURE_BINDING_2D, &mut texture);
        let mut sampler = 0;
        gl::GetIntegerv(gl::SAMPLER_BINDING, &mut sampler);
        Self {
            integers,
            viewport,
            mask,
            enabled,
            texture,
            sampler,
        }
    }
}
#[cfg(windows)]
impl Drop for State {
    fn drop(&mut self) {
        unsafe {
            gl::BindTexture(gl::TEXTURE_2D, self.texture as u32);
            gl::BindSampler(0, self.sampler as u32);
            gl::Viewport(
                self.viewport[0],
                self.viewport[1],
                self.viewport[2],
                self.viewport[3],
            );
            gl::ColorMask(self.mask[0], self.mask[1], self.mask[2], self.mask[3]);
            for &(name, value) in &self.integers {
                match name {
                    gl::DRAW_FRAMEBUFFER_BINDING => {
                        gl::BindFramebuffer(gl::DRAW_FRAMEBUFFER, value as u32)
                    }
                    gl::READ_FRAMEBUFFER_BINDING => {
                        gl::BindFramebuffer(gl::READ_FRAMEBUFFER, value as u32)
                    }
                    gl::CURRENT_PROGRAM => gl::UseProgram(value as u32),
                    gl::VERTEX_ARRAY_BINDING => gl::BindVertexArray(value as u32),
                    gl::ACTIVE_TEXTURE => gl::ActiveTexture(value as u32),
                    gl::PIXEL_PACK_BUFFER_BINDING => {
                        gl::BindBuffer(gl::PIXEL_PACK_BUFFER, value as u32)
                    }
                    gl::PIXEL_UNPACK_BUFFER_BINDING => {
                        gl::BindBuffer(gl::PIXEL_UNPACK_BUFFER, value as u32)
                    }
                    gl::POLYGON_MODE => gl::PolygonMode(gl::FRONT_AND_BACK, value as u32),
                    _ => gl::PixelStorei(name, value),
                }
            }
            for &(flag, enabled) in &self.enabled {
                if enabled {
                    gl::Enable(flag);
                } else {
                    gl::Disable(flag);
                }
            }
        }
    }
}

#[no_mangle]
pub extern "C" fn rak_blur_abi() -> u32 {
    1
}
#[no_mangle]
pub extern "C" fn rak_blur_error() -> *const c_char {
    ERROR.with(|slot| slot.borrow().as_ptr())
}
#[no_mangle]
pub unsafe extern "C" fn rak_blur_create() -> *mut c_void {
    #[cfg(windows)]
    {
        match catch_unwind(AssertUnwindSafe(|| Engine::new())) {
            Ok(Ok(engine)) => return Box::into_raw(Box::new(engine)).cast(),
            Ok(Err(message)) => error(&message),
            Err(_) => error("Gaussian initialization panicked"),
        }
    }
    #[cfg(not(windows))]
    error("Windows desktop OpenGL loader unavailable");
    ptr::null_mut()
}
#[no_mangle]
pub unsafe extern "C" fn rak_blur_run(
    handle: *mut c_void,
    source: *const u8,
    output: *mut u8,
    width: i32,
    height: i32,
    stride: i32,
    sigma: f32,
) -> i32 {
    #[cfg(windows)]
    {
        if handle.is_null() {
            error("Null Gaussian engine");
            return 0;
        }
        match catch_unwind(AssertUnwindSafe(|| {
            (&mut *handle.cast::<Engine>()).run(source, output, width, height, stride, sigma)
        })) {
            Ok(Ok(())) => return 1,
            Ok(Err(message)) => error(&message),
            Err(_) => error("Gaussian rendering panicked"),
        }
    }
    0
}
#[no_mangle]
pub unsafe extern "C" fn rak_blur_destroy(handle: *mut c_void) {
    #[cfg(windows)]
    if !handle.is_null() {
        let _ = catch_unwind(AssertUnwindSafe(|| {
            drop(Box::from_raw(handle.cast::<Engine>()))
        }));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn gaussian_preserves_constant_color_and_has_bounded_taps() {
        for sigma in [0.0, 0.1, 1.0, 4.5, 9.0, 14.0, 60.0] {
            let (weights, offsets) = kernel(sigma);
            assert!((weights[0] + 2.0 * weights.iter().skip(1).sum::<f32>() - 1.0).abs() < 1e-6);
            assert!(weights.len() <= 33);
            assert_eq!(weights.len(), offsets.len());
            assert!(offsets.windows(2).all(|pair| pair[0] < pair[1]));
        }
    }
    #[test]
    fn bilinear_pairs_reconstruct_discrete_gaussian_weights() {
        let sigma = 9.0;
        let (weights, offsets) = kernel(sigma);
        let center = weights[0];
        for i in 1..weights.len() {
            let left = offsets[i].floor();
            let blend = offsets[i] - left;
            for (at, weight) in [
                (left, weights[i] * (1.0 - blend)),
                (left + 1.0, weights[i] * blend),
            ] {
                let expected = center * (-at * at / (2.0 * sigma * sigma)).exp();
                if weight > 0.0 {
                    assert!((weight - expected).abs() < 1e-6);
                }
            }
        }
    }
}
