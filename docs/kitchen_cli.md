# Kitchen commands for agents

`rak doc` and recognized agent launches in RAK Terminal synchronize the same
current instructions into AGENTS.md, CLAUDE.md, CODEX.md and GEMINI.md in the
workspace directories. The canonical command list comes from the same contract
as `rak list` and capabilities. Existing agent memory and manual rules outside
the `RAK_CLI_AUTO_GENERATED` START/END block are preserved, including UTF-8 BOM
and line endings. Incomplete markers or a file changed during synchronization
fail without overwriting that file; writes publish through a temporary file.

RAK is an Android filesystem kitchen. Its primary cycle is partition IMG ->
unpacked filesystem plus Kitchen metadata -> edited filesystem -> rebuilt IMG.
Unix-style commands operate on files within that cycle.

| Input | `rak unpack INPUT` produces | Mount |
| --- | --- | --- |
| EXT/EROFS partition IMG | Filesystem folder and `config/` metadata | Mount the supported IMG directly |
| `.img.br` | Decompressed IMG | Convert first, then mount the resulting filesystem IMG |
| `.new.dat` / `.new.dat.br` | IMG, using the matching `.transfer.list` | Convert first, then mount the resulting filesystem IMG |
| `payload.bin` / SUPER IMG | Partition IMG files | Extract first; mount a supported partition IMG |
| boot/vendor_boot IMG | Boot contents, ramdisk and preserved boot metadata | Filesystem mount does not apply |
| Android sparse IMG | Default unpack converts it to raw in place; inspect its new type | Supported inner EXT/EROFS can be mounted read-only |

```sh
rak unpack system.img
rak sed -i 's/old/new/g' system/build.prop
rak repack system                         # system_new.img, original filesystem type
rak repack system -o rebuilt.img
rak repack system -o rebuilt.img.br
rak repack system -o rebuilt.new.dat
rak repack system -o rebuilt.new.dat.br
```

Filesystem rebuilds use the Kitchen's existing EXT/EROFS engines, permissions,
SELinux contexts and Source/NeoBranch transaction. DAT output is a bundle:
renaming its output also moves the matching transfer.list and patch.dat.
Boot/vendor_boot rebuilds continue to use the boot/CPIO engine and produce IMG.

Mount exposes image contents virtually; it creates no extracted filesystem
folder. Supported direct edits to a writable mounted IMG update that image.
For adding/restructuring files, use unpack/edit/repack. When a partition has an
active NeoBranch, rebuild it and mount the new IMG; do not mount its original
Source image. BR, DAT, payload and SUPER containers are never offered as
filesystem mount targets by inspect.


Start with one discovery command when the input format or valid operation is
unclear:

```sh
rak inspect "ROM.zip" --json
```

The result supplies `operations[].argv` and a readable `command`. Execute that
argv directly. `unpack` extracts/decompiles, `mount` browses an image virtually,
and `repack` rebuilds it. Format-specific engine names are unnecessary for the
default operation. Unsupported formats are identified without offering an
unimplemented command. `rak list` shows this primary vocabulary; compatibility
actions remain under `rak list --all`.

Use familiar Unix operands with the `rak` prefix. RAK resolves logical paths in
the active NeoBranch, updates Android metadata, and returns the actual task
result. Ordinary shell binaries keep their usual host filesystem behavior.

`inspect` calls the existing `header_magic` detector on the current active-view
file, rather than trusting a cached tree badge or its extension. The result
includes `detected_by`, `header_magic` and `extension_hint`. Android image magic
takes precedence over filename hints. ZIP/APK use ZIP structure. BR/DAT have no
fixed signature in this detector; `filename_hint` explicitly reports that limit.

Android `!<symlink>` placeholders and native links keep `kind: SYMLINK` and their
stored target, representation, resolved logical path, status and `behaves_as`.
A valid folder link accepts child paths through RAK, including the target's
active NeoBranch. Both its parent listing and `ls LINK` announce the link:

```text
etc/ -> /vendor/etc [symlink:directory; resolved] [source]
```

```sh
rak ls system/etc                   # announces the traversed link, then contents
rak ls -ld system/etc               # the link entry itself
rak readlink system/etc             # stored target: /vendor/etc
rak readlink -e system/etc           # existing resolved logical project path
rak inspect system/etc --json       # link identity/status
rak inspect -L system/etc --json     # target format and usable operations
rak cat system/etc/build.prop
```

JSON inspection and revision-checked document reads include traversed `symlinks`
for child paths. Broken links, cycles and targets outside the workspace have
explicit statuses. Text edits follow the target; copy/archive preserve Android
placeholder bytes, and `rm LINK` unlinks the entry. Host shell `cd` still sees
an Android placeholder as a regular file; use RAK logical paths to access it.

```sh
rak ls -la system
rak grep -Rni ro.build system
rak cat -n system/build.prop
rak cp system/build.prop system/build.prop.backup
rak mv system/build.prop.backup system/build.prop.old
rak mkdir -p system/etc/example
rak touch system/etc/example/config.txt
rak chmod 0644 system/etc/example/config.txt
rak ln -s /system/bin/sh system/bin/example
rak rm -r system/etc/example
```

`cp -r`/`cp -a` copies and merges folders. File destinations use ordinary cp/mv
overwrite semantics; `-n` skips existing files while merging other entries.
`cp -a source/. destination` copies contents directly; `-T` treats DEST as the
exact destination. `mv` can replace an empty destination directory but rejects
a nonempty one. `rm` requires `-r` for directories; `-f` ignores absent paths. `chmod`
sets octal Android fs_config metadata, rather than Windows host permissions.
Legacy `rak fs` and action names remain compatible.

## Verification and large projects

```sh
rak doctor --json
rak stat vendor/etc/build.prop --json
rak sha256sum vendor/etc/build.prop
rak cmp original.prop vendor/etc/build.prop
rak diff -ur expected vendor
rak find vendor -name '*.prop' -type f
rak head -n 20 vendor/etc/build.prop
rak tail -n 20 vendor/etc/build.prop
rak wc -l vendor/etc/build.prop
rak find vendor -type f -print0 | rak wc -l --files0-from=-
```

These read the merged on-disk branch view. Unsaved editor content is available
through `ui read`. Stat reports Android fs_config mode/UID/GID and distinguishes
exact SELinux entries from matching candidate rules. Missing metadata stays
unknown. It does not substitute Windows permissions or claim an effective
on-device label from a candidate regex.

`cmp` and GNU `diff` return 0 for equality, 1 for differences, and 2 for a
usage/read/tool error. Differences are ordinary verification results and do not
create failed-operation logs. `sha256sum -c CHECKSUMS` verifies a manifest.
`find` supports `-name`, `-iname`, `-type`, `-mindepth`, `-maxdepth`, `-L`,
`-print`, and `-print0`; default traversal preserves links as links.

The NUL filename list handles spaces and avoids Windows command-line length
limits. A native terminal can pipe it to `wc --files0-from=-`; IPC callers can
save a UTF-8 NUL list and use `wc --files0-from FILE`. All listed paths still
resolve through the branch view. `cat` returns complete text without the old
256 KiB truncation; head/tail provide bounded views.

Doctor reports installed dependencies without executing them. Standalone
capabilities explicitly identify their mode; GUI buffers, events, inbox,
activity and virtual mounting require a live Kitchen session.

## Editing text, including active branches

```sh
rak sed -i 's/old/new/g' system/build.prop
rak sed -E -i 's/^(ro.example=).*/\1enabled/' system/build.prop
```

GNU sed is the engine, with its normal script syntax, basic regex by default,
`-E` for extended regex, `-n` for explicit printing, and repeated `-e` programs.
Line addresses, deletion, multiline and hold-space programs use GNU semantics.
For example: `rak sed -n '2,5p' FILE`, or `rak sed -i -e '1d' -e 's/old/new/g' FILE`.
GNU grep likewise owns BRE/ERE matching and status 1 means no matches, rather
than an operation crash. Both tools resolve from installed Kitchen packages,
PATH, or Git for Windows. Missing engines produce a specific dependency error.
No partial Python regex emulation is substituted.
Grep on a directory requires -r/-R. Standalone grep returns 0 for a match,
1 for no match, and 2 for a usage, read or engine error. Matching lines are
not silently truncated.

Omit `-i` to print the transformed text.
The live command reads the current Monaco buffer if open, applies undoable
model edits, waits for acknowledgment, then saves through the normal branch
service. `-i` requires Agent autosave. An unopened file is edited through the
revision-checked background service; BOM, EOL and encoding are preserved.
Unopened files edited by sed can exceed the UI read limit. A changed revision
or newly opened editor prevents a background save.
Sed scripts run with `--sandbox`: script-side `r`/`w`/`e` operations cannot
bypass workspace/editor writes. The bridge currently accepts one document per
invocation and `-i` without a backup suffix. Use `cp FILE FILE.bak` for a backup.
The transport carries actual argument arrays, preserving Windows quotes and
regex backslashes. Ordinary shell pipes/redirection remain the shell's job.

For an exact multiline replacement, put text in a UTF-8 JSON file:

```sh
rak fs replace vendor/etc/build.prop --from replacement.json
```

The schema is `{"old_text":"old","new_text":"first\nsecond","replace_all":false}`.
This preserves the file's BOM, encoding and EOL and writes to its active branch.
Open documents require revision-checked UI editing. File input avoids cmd/bat
multiline argument loss; malformed requests leave the target unchanged.

For an arbitrary rewrite:

```sh
rak ui read system/build.prop
rak ui edit --from request.json --wait 30
rak ui response REQUEST_ID
```

`request.json` contains `path`, the exact `revision` returned by `ui read`, and
the complete replacement `content`. Keep the returned logical path; never
construct a physical `neobranch/` path. An edit is complete only at `applied`
with `response.saved=true`. A stale revision, changed branch, or intervening
user edit is rejected. With autosave disabled, `ui edit` may modify an open
buffer without saving it. Background reads/edits are limited to 256 KiB.

## Archives, APK and Android containers

```sh
rak unpack ROM.zip
rak unpack app.apk
rak repack app.apk
rak repack extracted -o rebuilt.zip
rak unpack payload.bin
rak archive list ROM.7z --json
rak archive extract ROM.zip --to extracted --path system.img --path vendor.img
rak archive pack extracted --to rebuilt.7z
rak payload list OTA.zip --json
rak payload extract OTA.zip --to images --partition system --partition vendor --json
rak super extract super.img --to partitions --all --json
rak apk build-sign app.apk
rak br decompress system.img.br
rak dat to-image system.new.dat.br
```

ZIP, 7z, RAR, TAR, common compressed streams, TAR.LZ4 and PAC use their existing
library engines. Pack supports ZIP, 7z, TAR, TAR.GZ, TAR.BZ2 and TAR.XZ. Optional
codecs/tools must be installed. ISO and lzip extraction are not implemented.
Payload accepts BIN or OTA ZIP with exactly one payload.bin. Extraction rejects
unknown partition/member names and existing output files. Paths inside selected
archive folders are preserved; unrelated members are not extracted.

Archive pack reads the merged logical branch view. Container extraction and
legacy conversion/APK operations must target files outside active partition
branches; use managed cp/mv to stage or import them. Extraction checks every
output path before publishing, so a destination at project root cannot bypass
an active partition's protection.

For long operations in a live workspace:

```sh
rak payload extract payload.bin --to images --all --no-wait --json
rak activity show TASK_ID --json
rak events --since CURSOR --wait 30 --json
```

Retain task_id and inspect its actual result. `dispatched`, `applying`, and a
wait timeout never imply completion. `--timeout SECONDS` changes synchronous
waiting. Standalone CLI rejects `--no-wait`, since it has no persistent owner.
Library work reports its operation/result or traceback in Kitchen's terminal;
external tools retain actual command, stdout/stderr and exit status.
