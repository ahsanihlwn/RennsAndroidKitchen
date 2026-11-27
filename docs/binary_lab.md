# Binary Lab

Binary Lab extends the SO viewer into a shared, read-only analysis workbench.
The existing internal viewer kind remains `elf` for compatibility. Rust/Goblin
parses ELF32/64, PE32/64 and single-architecture Mach-O. Original ELF resources,
DWARF paths, embedded previews, export and symbol comparison remain available.
The UI uses Kitchen's palette and controls, with sidebar navigation, overview
metrics, function browser and separate pseudocode/assembly/reference/call/notes views.

## Engine responsibilities

* `native/rak_native/src/binary.rs`: structural parsing, symbols, dependencies,
  relocations (ELF), section entropy, SHA-256, offset-bearing ASCII strings and
  format-specific mitigation indicators. C++ names demangle in-process.
* `core/binary_core.py`: shared schema, resource adapter, fallback and Capstone
  disassembly (section and virtual-address windows). Python/pyelftools remains the ELF fallback when Rust is absent.
* `core/binary_xref.py`: local virtual-address string indexer, ARM64 (`ADRP+ADD`/`ADRP+LDR`),
  x86_64 (`LEA [RIP]`) and pointer-table cross-reference scanner, plus path-to-code reconstruction context.
* `core/binary_session.py`: content-addressed local sessions, snapshot validation,
  cross-process locks, cache, Xref merging and revision-checked annotations.
* `core/native_decompiler.py`: preparation of mapped byte ranges for Ghidra's C++
  decompiler. RAK combines overlapping windows with the same mapping and uses
  `rak_native` to encode the resulting ranges without holding the Python GIL.
* `core/ghidra_scripts/RakBinaryExport.java`: RAK-authored adapter to Ghidra's
  public API for one-shot JSON export.
* `core/ghidra_scripts/rea/` & `core/rea_ghidra_client.py`: MIT-licensed resident
  Ghidra headless bridge adapted from `morluto/rea`, providing persistent TCP/UDS
  sessions, High P-Code value-flow, byte search, type inspection and sub-second function dossiers.

This is a workbench for static analysis, Xref reconstruction and decompilation.
Basic-block edges are rendered in the **CFG** tab of Functions.
PE/Mach-O symbols without reliable sizes/types are not guessed to be functions; use Ghidra discovery.
Universal Mach-O requires extracting a slice first. DEX/APK/ART and firmware
containers are extension points, not implemented binary loaders here.

Analysis does not execute the inspected program. Structural analysis accepts
regular files up to 512 MiB and caps returned metadata/ASCII-string rows at
200,000. A section preview is limited to 32 MiB, disassembly to 64 KiB. These are
resource guards, not a sandbox for adversarial inputs. Native parser limits are
checked after format parsing; use an isolated environment for untrusted samples.

## Setup and usage

Build the Rust extension with `python tools/build_native.py --dev`.
Open an ELF, EXE/DLL, dylib or Mach-O in Binary Lab. Unknown extensions also use
magic detection; raw data continues to use Hex. In Functions, choose **Ghidra
folder** to point at a separately installed Ghidra release. Alternatively set
`GHIDRA_INSTALL_DIR` or `GHIDRA_HOME` (environment settings take precedence).
Install the JDK required by that Ghidra release. Configuration validates the
launcher path; a successful analysis is the runtime/JDK health check.

Click **Analyze with Ghidra** once. Double-click a function or press Enter to
request its decompilation. Ghidra's project and completed exports are reused;
subsequent function requests use `-process -noanalysis -readOnly`. Calls still
launch a JVM on cache misses; this is not a resident Ghidra service.
Failed imports keep the snapshot, project and complete process/script logs.
Timeouts and export failures are operation failures, not application crashes.

The same interface works for humans and agents:

```text
rak binary engines
rak binary configure-ghidra "C:\Tools\ghidra"
rak binary inspect "C:\ROM\system\lib64\libexample.so"
rak binary strings "C:\ROM\system\lib64\libexample.so" --paths --query sha2
rak binary xref "C:\ROM\system\lib64\libexample.so" --query sha2-ce-glue.c
rak binary search-bytes "C:\ROM\system\lib64\libexample.so" --pattern "c0 03 5f d6" --hex
rak binary disasm "C:\ROM\system\lib64\libexample.so" --address 0x1000 --bytes 256
rak binary functions "C:\ROM\system\lib64\libexample.so" --ghidra --resident
rak binary function "C:\ROM\system\lib64\libexample.so" --address 0x1000 --resident
rak binary decompile "C:\ROM\system\lib64\libexample.so" --address 0x1000 --resident
rak binary rea-rpc "C:\ROM\system\lib64\libexample.so" --method list_segments --params "{}"
rak binary notes "C:\ROM\system\lib64\libexample.so"
rak binary annotate "C:\ROM\system\lib64\libexample.so" --address 0x1000 --name read_config --comment "Evidence from caller" --revision 0
```

Commands return JSON and a nonzero exit code for failures. `inspect` and
`functions` without `--ghidra` use the structural engine. `function` imports and
analyzes once if needed, then reads the cached function export. Addresses are
hexadecimal virtual addresses, not file offsets. Paths are resolved from the CLI
working directory; existing `rak ui` remains the semantic API for live logical
workspace/editor paths. This command does not replace the UI API.

## Kernel source reconstruction

```text
rak binary reconstruct-tree boot.img --output kernel_src --system-map System.map --btf vmlinux.btf --annotated-asm
rak binary reconstruct-tree vmlinux --output kernel_src --annotated-asm
```

The command extracts the kernel from Android boot headers v0–v4 and accepts
standalone compressed kernels and raw little-endian ARM64 Images. The manifest
records the payload hash and the original file hash. Start with `--max-files 20`
or repeat `--source-file drivers/path.c` to inspect named source files with
binary evidence. `--btf` takes a raw BTF blob from
the same build. For an ELF with embedded `.BTF`, that section is used automatically.
For an Image with a matching `System.map`, RAK can also read the embedded BTF
range delimited by `__start_BTF` and `__stop_BTF`.
If no map is supplied, RAK can fetch a public GKI `System.map` after the official
`Image` matches the extracted payload byte for byte. The result is cached by
build ID and payload digest and is available offline.

- **Symbols.** ELF symbols or `--system-map` locate the compressed kallsyms tables.
  The decoder validates the token index, markers, compressed names and address
  order, then writes the recovered table to `System.map.recovered`. Existing ELF
  names take precedence in the decompiler. A raw Image uses `_text` from the
  supplied map or a synthetic base if no map is available. For an unlabelled
  Image, locating the table would require a separate candidate scanner with
  layout checks and an independent address anchor. Ksymtab provides exported
  symbols and a syscall table can map syscall slots using the matching ABI.
  The available symbol set depends on kernel configuration and stripping.
  [Linux 5.10 table generator](https://raw.githubusercontent.com/torvalds/linux/v5.10/scripts/kallsyms.c)
  · [Android Common 6.12 table generator](https://android.googlesource.com/kernel/common/%2B/refs/tags/android16-6.12-2025-06_r35/scripts/kallsyms.c)
- **C and headers.** A token parser matches nested, constant byte offsets
  rooted in a verified parameter. BTF supplies the prototype, pointer chain,
  field offsets and scalar representation. The pass checks all uses of the
  parameter before changing its declaration. Recovered prototypes and forward
  declarations are emitted in headers. Upstream line and string matches appear
  as reference comments. A buildable translation unit also needs the exact
  Kbuild configuration, generated headers and include ownership. The
  compilation stage below takes those inputs from the original build.
  [Linux BTF specification](https://docs.kernel.org/bpf/btf.html)
- **Kernel idioms.** Use typed P-code and control flow to extend the pass.
  `container_of` needs a proven member pointer and the member's offset.
  `list_for_each_entry` needs the list type, cursor updates and sentinel
  condition. `IS_ERR` and `PTR_ERR` need the pointer origin, target word size,
  and the exact comparison or conversion defined by the build's kernel headers.
  These idioms currently remain in the original pseudocode.
- **Rust.** `--emit-rust` produces `rust/<path>.rs` from C units that pass
  compilation. A translated module is an unsafe C2Rust baseline. An Android
  kernel port needs target-specific Rust kernel bindings and equivalence checks.
  Original binary paths ending in `.rs` retain their inspection comments.
  [C2Rust transpiler documentation](https://github.com/immunant/c2rust/blob/master/README.md)
- **Assembly.** AArch64 listings include instruction bytes, branch labels,
  path-string xrefs and ELF relocation records when present. Listings are
  appended to recovered `.s` files or written as `.asm` sidecars with
  `--annotated-asm`. Unknown function sizes use a bounded address window.

The manifest records per-file rewrite counts and failed exports. A failed
function or listing marks the operation failed and leaves the partial tree and
manifest available for inspection.

### Check what a new computer needs

In the workspace, right-click an Android boot image and choose **Unpack +
Decompile**, or choose **Decompile Kernel** on `kernel` inside an unpacked boot
folder. Both flows put their exports in `<boot-folder>/kernel_src/`. Standard
**Unpack BOOT** still unpacks the image for editing and repacking.
RAK uses system Git in Linux or WSL to fetch the pinned kernel commit and the
source files referenced by reconstruction. A verified build adds a `GKI`
flag to the boot folder's `config/<name>_bootconf`. The badge appears on that
boot folder and its direct `kernel_src` child once there is an export manifest.

To compile, right-click the reconstructed source folder and choose **Compile
Kernel Sources…**. **Prepare Kernel Build…** on `kernel_src` can download the
full build environment first. RAK matches an arm64
kernel's version, build ID and commit against the public AOSP GKI release
catalog and verifies the pinned CI manifest before offering **Download**.
For older builds that are absent from the catalog, RAK checks the public CI
manifest for the banner's build ID and verifies its kernel commit. Both GKI
Clang layouts (`prebuilts` and `prebuilts-master`) are supported. If a pinned
project's upstream ref has expired, RAK uses system Git to fetch its commit.
The download syncs the original source, build scripts and toolchain
repositories. Repo progress and errors appear in the Kitchen Terminal.

Install Python 3, Git and the current Repo launcher in Linux x86-64. Windows
uses the WSL distribution which owns the file, or the default distribution
for a Windows file. Checkouts are under
`~/.cache/renn-android-kitchen/gki/<build-id>-<commit>/` in that environment.
RAK stores the verified manifest in its existing app cache under `cache/gki`.
**Prepare Kernel Build…** reuses a verified complete checkout and resumes a
partial download. **Manage GKI Cache…** shows the matching checkout path and
can remove it after confirmation. The cache protects active syncs from removal.
Repo uses the installed system Git to fetch the pinned repositories. Use the release build
recipe to create the Kbuild output before compiling recovered C.
[AOSP kernel build instructions](https://source.android.com/docs/setup/build/building-kernels)

When no published GKI manifest matches, the dialog lists the original
vendor source, toolchain, build config and generated files needed to compile.

`kernel-check` reads the boot image or kernel locally and reports the version,
architecture, commit, Android build number and embedded config digest when
available. The response's `result.ready` tells scripts whether the local
compile inputs are present. Each check includes a detail or a step to take.

```sh
rak binary kernel-check boot.img --emit-rust
python3 -m core.binary_cli kernel-check /path/boot.img \
  --kernel-source /path/kernel --build-dir /path/build --emit-rust
```

Windows can inspect and reconstruct locally. Run the second command in Linux
or WSL when the original build uses Linux paths. Run it from an RAK checkout
if `rak` is unavailable there. The check verifies that the database maps into
the local checkout, that its build directories contain the config and generated
header, and that a recorded Clang executable and the requested C2Rust
executable are available. Actual compilation verifies each selected unit.

When using the CLI from source, install `lz4` or `zstandard` into the same
Python environment for images using those codecs. The check also accepts
an already decoded `Image` or `vmlinux`.

Reconstruction saves embedded IKCONFIG as `kernel.config` when available.
It uses files from `--kernel-source` first and can fetch individual reference
files on demand. For a public Android common kernel it tries the banner's
commit before the branch. Cache entries are scoped to the origin and ref.
`reconstruct-tree --offline` uses the local checkout and existing cache only.
Optional same-build `System.map`, BTF, or an unstripped `vmlinux` improve
reconstruction when available.

Use **Decompile Kernel with vmlinux** from the unpacked kernel's menu, or pass
`--dwarf-reference vmlinux` to `reconstruct-tree`. RAK checks the entire Image
SHA-256 against the reference before using DWARF to assign emitted functions
to C files. Set **Functions per file** or `--functions-per-file` to zero to
include all supported functions found for each file.

For a full GKI checkout and toolchain, use the manifest and build recipe for
the detected release. For a vendor kernel use its published source, config
and build recipe. The per-file fetch supplies source references, while the
full checkout, generated headers and build toolchain come from those build
inputs. [AOSP GKI releases](https://source.android.com/docs/core/architecture/kernel/gki-releases)

### Compile and transpile using the original build

Use the same Clang toolchain and working paths as the recorded build. Generate
`compile_commands.json` from the original Kbuild output with the checkout's
`make compile_commands.json` target. The output directory must contain
`.config` and `include/generated/autoconf.h`. The compiler stage compares
`.config` with embedded IKCONFIG when the reconstruction manifest contains its
digest. [Linux compilation database generator](https://github.com/torvalds/linux/blob/master/scripts/clang-tools/gen_compile_commands.py)

Older builds with `CONFIG_TRIM_UNUSED_KSYMS=y` can also need the original KMI
symbol list. The embedded whitelist path may point to the CI filesystem.
Use the release's `abi_symbollist` and its pinned build recipe to generate the
local raw symbol list. Keep the extracted `.config` as the verification input.

```sh
python3 -m core.binary_cli compile-tree /path/kernel_src/reconstruction_manifest.json \
  --kernel-source /path/kernel --build-dir /path/build

python3 -m core.binary_cli compile-tree /path/kernel_src/reconstruction_manifest.json \
  --kernel-source /path/kernel --build-dir /path/build --emit-rust
```

Run this stage under Linux or WSL when the compilation database uses Linux
paths. A manifest produced by Windows RAK can be passed to that stage through
its WSL path. `reconstruct-tree` accepts the same build options to run both
stages in one command when the decompiler and compiler share an environment.
Install C2Rust in that environment for `--emit-rust` or pass `--c2rust` with
its executable path.

For an unstripped `vmlinux` from the original build, use the optional vmlinux
field in **Compile Kernel Sources** or pass `--vmlinux`. RAK checks the kernel
SHA-256 before using DWARF to find the owning C file. This can resolve an
inlined function whose diagnostic refers to a helper in another file.
`compile-report.json` records each reassignment.

Clang identifies the active definition in the original translation unit. RAK
keeps the signature, directives, includes and other source, then overlays the
recovered body under the original path. When the body fits the original span,
RAK preserves downstream file offsets and line numbers for compiler debug and
inline assembly metadata. A unit is published under `compiled-c/`
after Clang produces an object. C2Rust first tries the verified overlay. If
its AST exporter rejects a kernel header, RAK can retry with just the recovered
functions when Clang can compile their bodies and declarations in isolation.
For simple comparisons of pointer fields, Clang supplies the record size,
alignment, and field offset used by the isolated declaration.
`compile-report.json` records the scope of the Rust export.
`compile-report.json` records the compiler diagnostics, baseline and reconstructed
object hashes, and the original build object's hash when available. Generate a
fresh reconstruction manifest to pick up named functions from older exports.
Keep the original source license notices when distributing the mixed
translation units.

### Trace from kernel C to AArch64 instruction bits

`boot.img` carries the kernel payload. On Android's modern GKI layout
`vendor_boot.img` carries vendor ramdisks and the DTB, with a fragment table
and bootconfig on v4. Use `boot.img`, a decoded `Image` or `vmlinux` for the
kernel executable. [Android vendor boot layout](https://source.android.com/docs/core/architecture/partitions/vendor-boot-partitions)

The Android platform tree consumes prebuilt kernels. The kernel checkout
supplies the pinned toolchain and build rules. Recent GKI releases use
Kleaf/Bazel to invoke Kbuild. Kbuild selects source units and flags from the
target configuration. Clang preprocesses macros and headers, builds a
typed AST, emits optimized LLVM IR, and selects target instructions.
LLVM schedules instructions and allocates registers. The integrated
assembler encodes them into an ELF object. The linker fixes relocations and
section layout in `vmlinux`. The build derives `Image` from linked output
before boot packaging. With LTO, final instruction generation can take
place at link time. [AOSP kernel build](https://source.android.com/docs/setup/build/building-kernels)
· [Clang compiler stages](https://clang.llvm.org/docs/CommandGuide/clang.html#stage-selection-options)
· [LLVM instruction selection](https://llvm.org/docs/CodeGenerator.html#instruction-selection)

```sh
python3 -m core.binary_cli trace-compile lib/math/int_sqrt.c \
  --function int_sqrt --kernel-source /path/kernel --build-dir /path/build \
  --output /path/new-trace
```

Use the original build's compiler and `llvm-objdump`. The trace records the
working directory and flags, config digest, C after preprocessing, AST,
LLVM IR and object. An ELF object also produces target assembly and
disassembly with relocations. For little-endian AArch64 the trace includes
32-bit instruction words and their byte order in the object.

In GKI build 14355190, the C guard `x <= 1` lowered to IR
`icmp ult i64 %0, 2`. Immediately after instruction selection, LLVM
represented the compare as `SUBSXri` with immediate `2` and a definition
of the condition flags `NZCV`. The final AArch64 assembly printed
`cmp x0, #2`, the alias of `subs xzr, x0, #2`. The encoding has immediate
`2` in bits 21–10, input register `x0` in bits 9–5, and destination
`xzr` in bits 4–0. The resulting word `f100081f` is stored little-endian
as `1f 08 00 f1`. [Arm A64 instruction overview](https://developer.arm.com/-/media/Files/pdf/graphics-and-multimedia/ARMv8_InstructionSetOverview.pdf)

All 84 bytes for the compiled `int_sqrt` matched the official decoded
`Image` at offset `0x7d1e88`. A second compile with the guard written
as `x < 2` produced the same 84 instruction bytes under the recorded
Kbuild command. The source files had different SHA-256 digests.

Disassembly recovers machine instructions. Optimization can merge source
expressions, inline functions, and discard unused code. Kernels often ship
without DWARF line tables. RAK records uncorroborated path strings in the
reconstruction manifest. Direct code references with uncertain file
ownership produce pseudocode under `unattributed/`. Embedded
`__bug_table` locations support a specific diagnostic path and line.
Source obtained from a checkout or upstream remains a reference, with
its actual origin recorded when it matches an anchor.

### Measure a rebuilt boot image

The successful C units are listed in `compiled-c/vfs-overlay.json`. Use that
overlay with `-ivfsoverlay /absolute/path/vfs-overlay.json` in the Clang flags
of the full kernel build. Keep the original source paths and build inputs.
Run that build's normal kernel link, compression and boot packaging steps to
produce the candidate. A full build can also require the release's linker
and host tools, such as kernel Rust and bindgen for `CONFIG_RUST`, and
`pahole` for `CONFIG_DEBUG_INFO_BTF`. C2Rust handles the separate Rust
source export step. Compare the complete image and its parts with:

```text
rak binary compare-boot original-boot.img rebuilt-boot.img
```

The comparison includes the header page, stored kernel, decoded kernel,
ramdisk, and version-specific components. File SHA-256 equality is the
byte-identity test. Decoded-kernel equality can hold when compression,
padding or container metadata differ. The comparison also reports matching
4 KiB blocks at the same offsets in the decoded kernel.
[Android boot header layout](https://android.googlesource.com/platform/system/tools/mkbootimg/+/refs/heads/main/include/bootimg/bootimg.h)

## Shared work and private data

Sessions live in `%LOCALAPPDATA%/RennsAndroidKitchen/data/binary_lab/<sha256>`.
They contain a local binary snapshot, Ghidra project, JSON exports and research
notes; no upload service is used. They stay outside ROM trees and are not source
files. Cache identity also includes Ghidra installation/version and adapter hash.
Updating either creates a separate engine session. The original binary's digest
must match before a GUI analysis request; stale snapshots cannot silently analyze
a newer binary. Identical binary contents intentionally share notes and cache.

Annotations are local aliases and comments, not changes to target bytes or
Ghidra's database. Every save requires the revision returned by `notes`. A stale
save fails without overwriting the other writer. In the UI, **Refresh** fetches
the latest revision; **Load selected** explicitly replaces the notes draft.
Switching functions preserves a dirty draft until it is saved or explicitly
replaced. An annotation save acknowledges its actual persisted revision.

## Open-source research and license boundary

The integration follows Goblin's format-aware parsing, including section-linked
symbol/string tables when `PT_DYNAMIC` is absent, and Ghidra's documented
headless import/process workflow plus DecompInterface. No upstream implementation
was copied into Kitchen source.

* [Goblin](https://github.com/m4b/goblin) is MIT; used as a Rust dependency.
* [Ghidra headless workflow](https://github.com/NationalSecurityAgency/ghidra/blob/master/Ghidra/RuntimeScripts/support/analyzeHeadlessREADME.md)
  and [DecompInterface](https://ghidra.re/ghidra_docs/api/ghidra/app/decompiler/DecompInterface.html)
  define the external integration.
* [Rizin](https://github.com/rizinorg/rizin) and
  [rz-ghidra](https://github.com/rizinorg/rz-ghidra) were evaluated. Their source
  and libraries are not dependencies of this implementation. rz-ghidra uses the
  C++ decompiler subset rather than the full Ghidra project/analyzer workflow.

The newly added Rust dependencies and their transitive additions use MIT or
MIT/Apache dual licensing; the MIT option is selected and original notices are
preserved in `licenses/BINARY-LAB-THIRD-PARTY.txt`. Existing dependencies retain
their existing licensing. RAK-authored adapter files carry MPL-2.0.

Ghidra itself uses [Apache-2.0](https://github.com/NationalSecurityAgency/ghidra/blob/master/LICENSE).
Its [NOTICE](https://github.com/NationalSecurityAgency/ghidra/blob/master/NOTICE)
also identifies third-party component licenses and separate GPL support tools.
This implementation does not bundle Ghidra or those tools. Bundling later needs
a distribution-specific audit and all relevant licenses/attributions; a process
boundary alone does not resolve every licensing obligation.

MPL is file-level copyleft. Separate private files in a larger work can have
their own terms, while distributed changes to MPL-covered files remain subject
to MPL requirements. This architecture does not change or relabel private code.
See [Mozilla's MPL FAQ](https://www.mozilla.org/en-US/MPL/2.0/FAQ/), especially
the larger-work and proprietary-file questions, before a release audit.

## Verification

`tests/test_binary_lab.py` covers Rust/Python ELF compatibility, actual PE and
Mach-O parsing, offsets, shared annotations, stale revision rejection,
cross-process locks and the headless command/cache/failure contract using a
fixture runner. `tests/test_so_document.py` covers the retained ELF workflows.
`tools/verify_qtquick_so.py` exercises the Qt Quick viewer.

A Ghidra installation was not available during initial development. Real JVM
script compilation, analysis and decompilation remain an integration check to
run against the chosen Ghidra release. Fixture-backed headless tests must not be
reported as a real Ghidra end-to-end pass.
