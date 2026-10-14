# RAK Gaussian GPU blur

This library implements a custom Gaussian shader in Rust/OpenGL. It does not
enable Windows Acrylic, system materials, tint, noise or native blur windows.
Qt remains the foreground font renderer. The shader uses Qt/Skia's existing
desktop OpenGL context, so it creates no second device or persistent GL widget.

The filter has horizontal and vertical passes. Adjacent Gaussian taps share a
bilinear texture fetch, with normalized weights and clamp-to-edge sampling.
Premultiplied RGBA, physical pixel radius and OpenGL state are preserved.
Textures/FBO/programs are reused and released while the owning context is current.

Build with `python tools/build_gpu_blur.py`. Test the Gaussian weights with:

```powershell
cargo test --locked --manifest-path native/rak_blur/Cargo.toml --target-dir build/rak_blur
$env:RAK_TEST_SKIA_GPU = '1'
python -m pytest tests/test_gpu_blur.py -q
```

`core/rust_blur.py` checks ABI 1 before using `core/native/rak_blur.dll`.
The ABI requires a current OpenGL 3.3 Windows context, a live engine handle,
premultiplied RGBA source rows and a writable width*height*4 output buffer.
Run/destroy must use the creating context. The caller owns all pixel buffers.
Unsupported machines fall back to the existing Skia GPU or Qt filter.

The source is still Qt pixels. High retains a scene, captures damaged regions in
short GUI slices, and caches Qt label layers. Moving a panel reuses the scene.
There is no claim of zero CPU source work or direct GPU texture sharing for the
entire QWidget hierarchy. The Qt native popup requires the filtered image back
in Qt memory. A Rust shader does not remove those two boundaries by itself.

The DLL is bundled through Nuitka's documented
[DLL package configuration](https://nuitka.net/user-documentation/nuitka-package-config.html#dlls).
