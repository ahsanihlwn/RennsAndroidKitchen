# Dependency licenses — Quill Native 0.3.0

This inventory comes from `cargo metadata --locked --features pyo3/extension-module` and the accompanying Cargo.lock. It includes build dependencies and target-specific dependencies; not every package below is linked into a Linux build.

The Floem MIT text was read from its published source revision. The Apache-2.0 text for Xi Rope was copied from the upstream repository; declared licenses and VCS references were also inspected in the published crate metadata:

- floem-editor-core 0.2.0: MIT, upstream Floem commit `574638db95a4cf898f6156fa1db375de7a1d49c3`.
- lapce-xi-rope 0.3.2: Apache-2.0 declared by the crate. Its VCS metadata reports `f9ae884eeec345f89b2e0c0f756455c3e28ee83b`; the included Apache-2.0 license text is from upstream repository revision `a16fa97b839dd4b04840b62b09d4b65b646776a9`.

Other entries record the SPDX declarations in the resolved package manifests. The selected branches below contain no mandatory GPL, AGPL, LGPL or MPL requirement. An `OR` expression means an alternative can be selected; for example r-efi also offers LGPL, but its MIT alternative is selected here. `unicode-ident` additionally requires Unicode-3.0 notices.

This Rust inventory does not relicense Qt/PySide. The UI continues to use the LGPL option for the applicable Qt/PySide community modules. Redistribution must separately satisfy the licenses of the actual Python/Qt modules and binaries shipped. See https://doc.qt.io/qtforpython-6/commercial/index.html and https://www.qt.io/development/open-source-lgpl-obligations.

| Package | Version | Declared license | Selected branch |
| --- | --- | --- | --- |
| aho-corasick | 1.1.5 | Unlicense OR MIT | MIT |
| autocfg | 1.5.1 | Apache-2.0 OR MIT | MIT |
| bitflags | 2.13.2 | MIT OR Apache-2.0 | MIT |
| bytecount | 0.6.9 | Apache-2.0/MIT | MIT |
| cfg-if | 1.0.5 | MIT OR Apache-2.0 | MIT |
| either | 1.19.0 | MIT OR Apache-2.0 | MIT |
| errno | 0.3.15 | MIT OR Apache-2.0 | MIT |
| fastrand | 2.5.0 | Apache-2.0 OR MIT | MIT |
| floem-editor-core | 0.2.0 | MIT | MIT |
| getrandom | 0.3.4 | MIT OR Apache-2.0 | MIT |
| heck | 0.5.0 | MIT OR Apache-2.0 | MIT |
| indoc | 2.0.8 | MIT OR Apache-2.0 | MIT |
| itertools | 0.12.1 | MIT OR Apache-2.0 | MIT |
| lapce-xi-rope | 0.3.2 | Apache-2.0 | Apache-2.0 |
| libc | 0.2.191 | MIT OR Apache-2.0 | MIT |
| linux-raw-sys | 0.12.1 | Apache-2.0 WITH LLVM-exception OR Apache-2.0 OR MIT | MIT |
| memchr | 2.8.3 | Unlicense OR MIT | MIT |
| memoffset | 0.9.1 | MIT | MIT |
| once_cell | 1.21.4 | MIT OR Apache-2.0 | MIT |
| portable-atomic | 1.15.0 | Apache-2.0 OR MIT | MIT |
| proc-macro2 | 1.0.107 | MIT OR Apache-2.0 | MIT |
| pyo3 | 0.27.2 | MIT OR Apache-2.0 | MIT |
| pyo3-build-config | 0.27.2 | MIT OR Apache-2.0 | MIT |
| pyo3-ffi | 0.27.2 | MIT OR Apache-2.0 | MIT |
| pyo3-macros | 0.27.2 | MIT OR Apache-2.0 | MIT |
| pyo3-macros-backend | 0.27.2 | MIT OR Apache-2.0 | MIT |
| quill-native-engine | 0.3.0 | MIT | MIT |
| quote | 1.0.47 | MIT OR Apache-2.0 | MIT |
| r-efi | 5.3.0 | MIT OR Apache-2.0 OR LGPL-2.1-or-later | MIT |
| regex | 1.13.1 | MIT OR Apache-2.0 | MIT |
| regex-automata | 0.4.18 | MIT OR Apache-2.0 | MIT |
| regex-syntax | 0.8.11 | MIT OR Apache-2.0 | MIT |
| rustix | 1.1.6 | Apache-2.0 WITH LLVM-exception OR Apache-2.0 OR MIT | MIT |
| rustversion | 1.0.23 | MIT OR Apache-2.0 | MIT |
| serde | 1.0.229 | MIT OR Apache-2.0 | MIT |
| serde_core | 1.0.229 | MIT OR Apache-2.0 | MIT |
| serde_derive | 1.0.229 | MIT OR Apache-2.0 | MIT |
| strum | 0.26.3 | MIT | MIT |
| strum_macros | 0.26.4 | MIT | MIT |
| syn | 2.0.119 | MIT OR Apache-2.0 | MIT |
| syn | 3.0.10 | MIT OR Apache-2.0 | MIT |
| target-lexicon | 0.13.5 | Apache-2.0 WITH LLVM-exception | Apache-2.0 WITH LLVM-exception |
| tempfile | 3.23.0 | MIT OR Apache-2.0 | MIT |
| unicode-ident | 1.0.27 | (MIT OR Apache-2.0) AND Unicode-3.0 | MIT AND Unicode-3.0 |
| unicode-segmentation | 1.12.0 | MIT OR Apache-2.0 | MIT |
| unindent | 0.2.4 | MIT OR Apache-2.0 | MIT |
| wasip2 | 1.0.4+wasi-0.2.12 | Apache-2.0 WITH LLVM-exception OR Apache-2.0 OR MIT | MIT |
| windows-link | 0.2.1 | MIT OR Apache-2.0 | MIT |
| windows-sys | 0.61.2 | MIT OR Apache-2.0 | MIT |
| wit-bindgen | 0.57.1 | Apache-2.0 WITH LLVM-exception OR Apache-2.0 OR MIT | MIT |

Cargo.lock SHA-256: `2e4fc8e7a795f793ec4d56b21364fdeccacdb3c7836c981b5a273bc6d01fea3e`.

MIT notices and Apache-2.0 terms still apply. This is a source/metadata inventory, not a certification of every future build option, grammar, plugin or distribution package.
