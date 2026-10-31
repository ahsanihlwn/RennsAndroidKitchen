# RAK Terminal agent experiments

The experimenter was GPT-6 Luna. It used the generated agent instructions and
the production CLI against owned temporary projects, without reading or editing
the implementation. The parent agent patched failures and independently checked
Source bytes. The harness disables discovery of a user's live Kitchen IPC so
the experiment cannot accidentally target an open ROM through that endpoint.
This is a test harness, not an OS security sandbox.

## Reproduce

```powershell
python tools/agent_cli_lab.py create --scale 6400
```

The last output line names the new project. Its `rak.cmd` invokes the production
CLI and records every command's arguments, complete stdout/stderr, exit code,
and duration in `transcript.jsonl`. Installed Kitchen binaries build the EXT,
BR and DAT fixtures. Payloads embed real EXT filesystems. No tool download is
performed. Projects include malformed inputs and unsafe archive paths.

```powershell
Set-Location <reported-project>
.\rak.cmd doctor --json
.\rak.cmd find vendor/stress -type f -print0 | .\rak.cmd wc -l --files0-from=-
python <repository>\tools\agent_cli_lab.py audit --root <reported-project> --expected-scale 6400
```

For byte-preserving NUL pipes, use a native external-command pipe (for example
cmd.exe); a shell's text conversion can change an external program's bytes.
Audit checks the original Source files and the deterministic contents of every
declared stress file. It does not treat expected negative tests as successes.

## Failures found and corrected

| Finding | Change and check |
| --- | --- |
| Successful CLI exit could still print a deleted Qt signal traceback | Standalone shutdown keeps Qt alive until owned workers and queued callbacks finish; repeated real inspections were clean |
| Agents lacked hashes, file metadata and comparison tools | Added branch-aware stat, SHA-256, cmp, GNU diff, find, GNU head/tail/wc |
| Cat silently stopped at 256 KiB | Complete text output; 100,001 lines and the final marker were checked |
| Sed could not edit a 2.5 MB unopened file | Local revision-checked background edits preserve BOM/EOL/encoding; open buffers use their complete unsaved model and keep Monaco/autosave checks |
| Nested unpacked filesystems were not recognized for repack | Detect adjacent Kitchen metadata in nested output directories and verify the rebuilt IMG |
| Directory traversal and comparison repeated expensive Windows lookups | Reuse validated directory entries and directory-level root resolution; preserve reparse checks and branch deletion rules |
| Filename relabeling grew quadratically | Relabel wc output once per line and use NUL manifests rather than huge Windows command lines |
| Recursive diff copied every identical file | Compare bytes first and stage only differences; GNU diff still renders the result and owns ignore flags |
| Inline multiline arguments through cmd/bat could lose line breaks | Added JSON-file replacement input; preserved direct IPC argv, BOM/EOL, branch destination and open-buffer protection |
| Case variants could expose an inherited Source entry beside its overlay on Windows | Merge Windows names consistently and apply deletion/undelete comparisons with the same case policy |
| Standalone agents could not discover their actual environment | Doctor and standalone capabilities report installed tools, active branches and live-only functions explicitly |
| A standalone owner inferred its project container from the parent directory | Keep standalone project ownership at the opened root; documentation refresh no longer includes an unrelated parent |

The first real-filesystem payload fixture was missing the metadata-signature
placeholder expected by the existing parser. That was corrected in the fixture;
it was not counted as a successful extraction or a production parser fix.
The initial lab stdout forwarding also needed UTF-8 configuration. Its false
failure was separated from the successful underlying sed operation.

Round 1 deliberately ran `rak img write labfs.img ...` as a separate in-place
image-edit test. Its original image hash therefore changed. The audit retains
that mismatch and classifies it as expected only when explicitly named with
`--expected-in-place labfs.img` and backed by the successful recorded command.
It does not reset the baseline or exempt the protected vendor Source files.

## Agent-verified results

- Source/NeoBranch edits through Android directory and file links; original
  Source bytes, comment, link target and mode remained intact.
- Header magic over misleading filename extensions; BR/DAT filename hints
  remained explicitly distinguished from proven image magic.
- ZIP list, selected extraction and repack; BR and DAT conversion to EXT IMG.
- Payload selected extraction, byte comparison to the original EXT fixture,
  nested unpack/edit/repack, then content and metadata reads from the new IMG.
- A 25,600,000-byte binary hash and checksum manifest, 100,001-line text output,
  a late-file text edit, and comparisons that detected that late difference.
- NUL pipelines, copies, equality and deletion differences at 1,600 and 6,400
  files; invalid scripts/checksums/paths/links and unsafe archives failed visibly.
- All four generated instruction files updated while preserving separate-line
  manual notes outside the managed markers.

### Largest independent rerun

On the same 6,400-file fixture after the performance patches:

| Operation | Before patch | After patch |
| --- | ---: | ---: |
| NUL find/wc pipeline | 76.1 s | 8.9 s |
| Recursive diff, equal trees | 69.8 s | 6.4 s |
| Recursive diff, one missing copied file | 21.5 s | 6.7 s |

The pipeline returned 6,401 output rows and 262,400 total lines. Equality
returned status 0; the missing-file comparison returned status 1. Timings are
observations on this machine, not performance guarantees.

## Evidence and scope

Reports, full command transcripts, Source audits and the final regression log
are exported under `build/agent_terminal_experiments/`. Rounds retain failures
and the subsequent successful reruns; the audit is independent of the agent's
written report.

Manual experiments were headless. GUI buffer acknowledgments, mount tasks,
Source recovery, transfers and error classification were checked by the
regression suite. The experiments did not flash devices, prove Android boot,
or establish arbitrary Windows-command containment. Full positive APK signing,
SUPER and boot/vendor_boot round trips are not claimed by these manual rounds.
Doctor reports dependency availability rather than promising every input will
be accepted. The installed GNU tools continue to own sed/grep/diff/head/tail/wc
semantics; find advertises its supported traversal flags explicitly.
