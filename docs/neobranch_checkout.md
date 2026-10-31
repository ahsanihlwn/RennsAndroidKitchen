# Physical NeoBranch workspaces

Ordinary agent file tools operate on the selected branch at the normal project
paths, for example `vendor/build.prop` and `config/vendor_file_contexts`.
Read/Write/Edit, Python, PowerShell and normal editors require no RAK filesystem
adapter. Native Windows is sufficient. RAK remains the interface for Kitchen
tools, branch changes, Android metadata and virtual image mounts.

`rak branch create vendor port` creates a Source-based branch and checks it out.
`rak branch switch vendor source` checkpoints the complete active worktree,
including external additions, deletions, renames and owned config files, before
restoring Source. Switching back to `port` restores those changes. Creating a
second branch starts from the saved Source, rather than inheriting the first
branch. Source itself remains editable while explicitly selected.

The manager stores immutable snapshots under `neobranch/<partition>/checkout`.
Live files never share hardlinks with snapshots. Unchanged files may share
hardlinks between immutable snapshot generations; unsupported hardlinks fall
back to copies. Successful switches retire unreferenced completed generations.
Repack checkpoints and builds the physical worktree while retaining the active
branch. It does not restore Source over that worktree afterward.

A cross-process partition lock covers checkpoint, switch and image build. A
journal retains staged replacements and displaced files until the partition,
per-partition config, NeoCat cache and selected branch state agree. Recovery
resumes the same staged transaction, including a process exit between rename
and journal update. Project loading and the task queue use SourceRecovery before
exposing the workspace. Legacy interrupted repacks recover first; legacy sparse
branches then migrate without overwriting their Source baseline.

External writers must finish before switching or repacking. Hash checks detect
writes during snapshot/staging, but this is not an OS sandbox and cannot lock
arbitrary external programs out of every file. Unsaved Kitchen tabs for the
partition and its config block switching. Native tools see disk; use the
revision-checked UI API when editing an unsaved Monaco model.

Android `!<symlink>` placeholders retain their existing representation. They are
regular files to Windows, so inspect/readlink and operate on the resolved target
when needed. Snapshotting preserves actual symlinks without following them;
unsupported junction/reparse points are rejected before copying.

Android labels and modes live in config, independently of host permissions.
Use `rak fs context PATH LABEL`, `rak fs permission PATH OCTAL` and verify with
`rak stat PATH --json`, or edit the authoritative config using native tools.
Native copies do not automatically map donor SELinux rules. The existing Kitchen
metadata parsers cannot represent whitespace in paths; metadata setters reject
such paths without writing malformed entries. Ordinary files with spaces remain
usable by native tools.

AGENTS.md, CLAUDE.md, CODEX.md and GEMINI.md advertise this workflow. Synchronizing
instructions updates only the marked generated block and preserves agent notes
outside it. Compatibility RAK file commands remain available for older clients.

Validation: `python -m pytest -q tests/test_neobranch_checkout.py
tests/test_quick_cli.py tests/test_source_recovery.py tests/test_editor_document.py
tests/test_agent_docs.py`. The independent native-tool experiment is recorded in
`build/physical_checkout_luna_report.md`.
