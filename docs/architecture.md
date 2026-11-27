# Renns Android Kitchen Architecture

Renns Android Kitchen (RAK) runs a pure **Qt Quick / QML** desktop interface backed by a **Python/PySide6** application host and a **Rust native extension (`rak_native`)**. Legacy QWidget, `QtWebEngine`, Monaco/WebChannel, and xterm.js layers have been removed.

## UI & Workspace Shell

- **Application entry**: `app.py` initializes `QGuiApplication`, registers QML types, and launches `ui.application.LauncherApplication`.
- **QML views & controls**: `ui/Launcher.qml`, `ui/LauncherContent.qml`, `ui/Workspace.qml`, `ui/pages/*`, and `ui/controls/*`.
- **Backdrop blur**: Live glass rendering in `ui/controls/LiveGlass.qml` backed by `native/rak_blur` (`core/native/rak_blur.dll`).
- **On-demand workspace tree**: `ui/directory.py` and `ui/directory_view.py` populate a `QStandardItemModel` lazily per expanded folder via the Rust directory enumerator (`docs/qtquick_tree_scanner.md`), with context menus and drag/drop in `docs/qtquick_context_menu.md`.
- **Code editor**: `ui/editor.py` and `ui/quill_editor.py` wrap **Quill Native** (`assets/quill-native/`, powered by `floem-editor-core` and `lapce-xi-rope` in Rust).
- **Viewers & builders**: Native viewers and dialogs for HEX (`ui/hex_viewer.py`), ELF/PE/Mach-O Binary Lab (`ui/so_viewer.py`, `ui/controls/BinaryCfgView.qml`), Markdown (`ui/markdown_viewer.py`), archives (`ui/archive_viewer.py`), partitions (`ui/partition_viewer.py`), boot/DTBO analysis (`ui/boot_analyzer.py`), `super.img` packing (`ui/pack_super.py`), and flashable ZIP creation (`ui/flashable.py`).

## Terminal & Native Command Routing

- **ConPTY terminal**: `ui/terminal.py`, `ui/controls/TerminalPane.qml`, and `core/terminal_pty.py` run native shells via `core/termcore.dll` (`core/terminal/core/`).
- **In-process native router**: `core/terminal_native_router.py` routes built-in tools (`adb`, `fastboot`, `erofs`, `ext_cli`, `make_ext4fs`, `lpunpack`, `lpmake`, `magiskboot`, `brotli`, `sdatimg`, `img2simg`, `simg2img`) directly to `rak_native`.
- **Agent chat & CLI**: Docked native agent panel (`docs/agent_chat.md`), authenticated IPC (`docs/agent_ui_api.md`), and `rak` CLI (`docs/kitchen_cli.md`).

## Native Rust Backend (`rak_native`)

All core ROM/filesystem/device tools are compiled into `rak_native` (`native/rak_native/`):

- **Filesystems & images**: `core/erofs_rs`, `core/ext_cli`, `core/make_ext4fs`, `core/lpunpack`, `core/sdatimg`, `core/brotli_rs`, `core/magiskboot`, `core/pac_extractor`, and `native/rak_native/src/dtbo.rs`.
- **Android platform tools**: `core/platform_tools_rs` (`adb`, `fastboot`, `mke2fs`, `make_f2fs`, `sqlite3`, `etc1tool`, `hprof-conv`).
- **Binary & debug info**: `native/rak_native/src/binary.rs`, `native/rak_native/src/dwarf.rs`, and `native/rak_native/src/markdown.rs`.
- **VDev engine**: `native/rak_native/src/vdev/` (AVB/boot, containers, DEX verify, ELF protobuf, SELinux CIL).

## Firmware Analysis, Kernel & Decompilation

- **VDev & QEMU**: `core/vdev/` validates unpacked ROM mounts, SELinux/VINTF/init/linker/runtime/APEX stages, diffs stock vs. port firmware (`core/vdev/diff.py`), and drives QEMU test boots (`core/vdev/qemu_runner.py`, `core/vdev/logcat_correlator.py`).
- **Kernel & boot**: `core/kernel_*`, `core/gki_checkout.py`, `core/dtbo_image.py`, and `core/boot_image.py` inspect boot/DTBO images, extract symbols/BTF/DWARF, fetch GKI sources, and rebuild kernels.
- **Binary Lab & Ghidra**: `core/binary_core.py`, `core/binary_xref.py`, `core/native_decompiler.py`, `core/ghidra_native/`, `core/ghidra_scripts/rea/`, and `core/rea_ghidra_client.py` (`docs/binary_lab.md`).

## External Package Registry

Because platform tools, filesystem tools, Brotli, Lpunpack, Magiskboot, and PAC extraction are built into `rak_native` (`core/native_tools.py`), the external package registry in [`package/package.json`](../package/package.json) manages only the **Apktool Suite** (`apktool.jar`, `apksigner.jar`, `APKEditor.jar`, `baksmali.jar`, `smali.jar`, `aapt.exe`, `aapt2.exe`, `zipalign.exe`, and test keys), fetched by `core/dependency_manager.py` with local fallback to the bundled `package/package.json`.

## Related Documentation

- [`agent_chat.md`](agent_chat.md) — Native Qt Quick agent chat panel and provider adapters
- [`agent_ui_api.md`](agent_ui_api.md) — Authenticated IPC UI API for `rak` agents
- [`binary_lab.md`](binary_lab.md) — Binary Lab, Xref scanner, and Ghidra decompiler bridge
- [`kitchen_cli.md`](kitchen_cli.md) — `rak` CLI reference and `rak doc` synchronization
- [`neobranch_checkout.md`](neobranch_checkout.md) — Physical NeoBranch overlay workspaces
- [`qtquick_context_menu.md`](qtquick_context_menu.md) — Workspace context menu and drag/drop transfers
- [`qtquick_tree_scanner.md`](qtquick_tree_scanner.md) — On-demand Qt Quick directory model
- [`terminal_experiments.md`](terminal_experiments.md) — Automated CLI verification harness
