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
  disassembly. Python/pyelftools remains the ELF fallback when Rust is absent.
* `core/binary_session.py`: content-addressed local sessions, snapshot validation,
  cross-process locks, cache and revision-checked annotations.
* `core/ghidra_scripts/RakBinaryExport.java`: RAK-authored adapter to Ghidra's
  public API. Ghidra's own analyzers discover functions; its DecompInterface
  returns pseudocode, listing, cross references, callers/callees and basic blocks.

This is a first workbench implementation, not a replacement for every Ghidra
feature. There is no source reconstruction/build system, binary patching,
debugging, automatic type recovery editing, or interactive graph view yet.
Basic-block edges are exported for future graph rendering. PE/Mach-O symbols
without reliable sizes/types are not guessed to be functions; use Ghidra discovery.
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
rak binary functions "C:\ROM\system\lib64\libexample.so" --ghidra
rak binary function "C:\ROM\system\lib64\libexample.so" --address 0x1000
rak binary notes "C:\ROM\system\lib64\libexample.so"
rak binary annotate "C:\ROM\system\lib64\libexample.so" --address 0x1000 --name read_config --comment "Evidence from caller" --revision 0
```

Commands return JSON and a nonzero exit code for failures. `inspect` and
`functions` without `--ghidra` use the structural engine. `function` imports and
analyzes once if needed, then reads the cached function export. Addresses are
hexadecimal virtual addresses, not file offsets. Paths are resolved from the CLI
working directory; existing `rak ui` remains the semantic API for live logical
workspace/editor paths. This command does not replace the UI API.

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
