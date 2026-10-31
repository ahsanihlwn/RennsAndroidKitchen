# Kitchen agent UI API

The agent keeps running in RAK Terminal and calls the same local, authenticated IPC as `rak fs`, NeoBranch and activity. This does not install a separate model or require a specific agent vendor. Reopen the application after changing its Python/QML source.

## Discover and read

```powershell
rak capabilities --json
rak context --content --json
rak ui open "D:\ROM\PORT\vendor\build.prop" --line 12 --column 1 --end-column 20
rak ui read "D:\ROM\PORT\vendor\build.prop"
rak ui reveal "D:\ROM\PORT\vendor\build.prop"
```

Context includes the session ID, project, focused UI area, tree selection, current editor tab, each document's revision/dirty/read-only state and selected text, active branches, mounts, device state, activity and event cursor. Content is opt-in and limited to 262144 characters for the active document. `ui read` includes the current editor buffer with that same limit and an explicit `truncated` flag. Do not treat truncated content as a complete file. Read operations do not save or change focus.

`project` identifies the Kitchen container. Use `workspace.working_directory`
and `workspace.default_output_directory` for the opened active tree, alongside
`workspace.tree_roots` and `workspace.trees`. New files belong in that default
output root unless the user selects another destination. A tree's `location`
describes current navigation, not a replacement project root. The chat freezes
its output root per turn, separately from attached buffer snapshots and later UI
selection changes.

Use logical paths returned by context, including `mount://...` paths, rather than guessing physical NeoBranch storage paths. UI open/read/edit support mounted text documents. Mounted tree reveal is currently unsupported; use `ui open`.

## Ask the user or show a result

Write a UTF-8 JSON file:

```json
{"title":"Select target","message":"Which branch should receive this change?","choices":["Source","Work branch"]}
```

```powershell
rak ui ask --from question.json --wait 30
rak ui wait <request-id> --wait 30
rak ui response <request-id>
rak ui cancel <request-id>
```

Questions have no preselected answer; the user can select a choice or type another answer. Omit `choices` for free text. `ui notify --from result.json` uses `title` and `message` to show a result in the UI. Independent questions/notices/reviews are queued and retain their own IDs. The CLI can wait up to 30 seconds per call; expiry returns the current state and does not cancel the request. Wait again if it is still pending. Escape/Cancel and project closure return cancellation, not acceptance.

## Edit and review

Read the live document first. Submit its exact `path` and `revision`:

File reads/edits never require `agent.ui_triggers`. That setting only permits UI
navigation and registered controls. The effective boolean is exposed directly
in `ui_controls.enabled` and the per-turn workspace's `agent_permissions`.

The preferred CLI transport keeps replacement bytes out of a shell command:

```powershell
rak ui read "D:\ROM\PORT\vendor\build.prop"
rak ui edit "D:\ROM\PORT\vendor\build.prop" --revision "revision-from-read" --content-file "C:\scratch\replacement.txt" --wait 30
```

Use a native file-writing tool to put the full UTF-8 replacement in
`workspace.scratch_directory`, then pass its path as `--content-file`. Claude's
native Write schema requires both `file_path` and `content`. A missing tool
argument is a backend tool validation failure, not a Kitchen permission denial.
Never inline code or JSON into PowerShell. `--from` below accepts a JSON **file
path**, not the JSON object itself; the parser rejects that mistaken usage.

```json
{"path":"D:\\ROM\\PORT\\vendor\\build.prop","revision":"revision-from-ui-read","content":"complete proposed buffer text\n","title":"Review build.prop"}
```

```powershell
rak ui review --from edit.json
rak ui edit --from edit.json --wait 30
rak ui response <request-id>
```

`review` displays a unified diff and waits for Apply or Cancel. `edit` applies directly to an open Monaco buffer. Both check the revision, then check the current Monaco model content before editing; an edit remains one undo step. With **Autosave agent changes** enabled (default), the host saves only after Monaco acknowledges the exact edit, through the existing encoding, NeoBranch and mounted-image save pipeline. It returns `saved:true` only after the actual save acknowledgment. A failed save remains failed and leaves the buffer dirty. With autosave disabled, open-buffer edits remain unsaved.

For an unopened regular text file, `ui read` returns a `disk:` revision without opening a tab. `ui edit/review` rechecks it and uses a queued background transaction, preserving encoding, BOM, EOL and the logical NeoBranch overlay. Stale revisions, newly opened files and concurrent edits are rejected. Background text editing requires autosave and is limited to 256 KiB. Use `rak img` for unopened image contents.

`rak sed` can transform larger unopened text files through a local revision-checked transaction without sending the whole file through UI read. Open buffers keep the Monaco acknowledgment and autosave path. `stat`, `sha256sum`, `cmp`, `diff`, `find`, `head`, `tail`, and `wc` verify the merged on-disk branch; their results do not include unsaved buffer edits. `rak doctor --json` distinguishes installed tools and live-session requirements, and standalone capabilities report their actual scope.

For an open document, the local sed service reads the complete Monaco model,
including unsaved content above the public UI read limit. It still rechecks the
revision and waits for the actual editor acknowledgment before saving.

Request states are `pending`, `applying`, `answered`, `applied`, `activated`, `failed`, `cancelled`, or `conflict`. A returned request ID is not a completed edit. Read the actual final state. An edit already applying cannot be cancelled midway; wait for its acknowledgment and use Undo for an editor buffer if needed. Requests are bounded to 32 active and 128 retained entries.

## Project UI access and activity

Project settings JSON has an `agent` section with `autosave:true`, `highlight:true`, and `ui_triggers:false` by default. `rak ui settings` and context/capabilities expose the effective settings read-only. Opening/revealing an editor or invoking a registered control requires the user to enable **Agent UI access**. Direct file reads/edits do not open an editor. Legacy CLI actions that open project windows follow this same gate.

```powershell
rak ui settings
rak ui controls
rak ui trigger <control-id>
rak ui trigger <field-or-switch-id> --from value.json
rak ui scroll <control-id>
rak ui guide agent.ui_triggers
```

Controls register their own semantic IDs, labels, types and enabled/visible state from QML signals; agents do not scan widgets or use coordinates. A value payload is `{"value":true}`, a string for text fields, or a number for sliders. Controls acknowledge activation separately from queued operation completion. `ui scroll` reveals and highlights the target. Password fields, user approval/error dialogs and Settings cannot be operated by an agent. Settings remain user-only even when UI access is enabled; `ui guide` is the explicit exception that only opens, scrolls and highlights one of the three Agent preferences. It never toggles a value or applies the draft.

With highlighting enabled, observed RAK file operations and native CLI tool paths publish `agent.file` events. A moving rainbow fills the corresponding file and ancestor folder rows at 10% opacity, fading in over 220 ms and out over 420 ms. The tree header also reflects its descendant activity. Active tools and applying edits hold their paths until completion, failure, stop acknowledgment or turn cleanup; a shared folder stays highlighted while another tool still holds it. Very fast operations retain a visual tail until 800 ms after their first touch so completion before the first frame cannot hide the effect; their operation state is already complete. Completed one-shot CLI observations use a short visual pulse. Row/path mappings refresh from model and expansion signals. This feedback does not change selection, expand folders or open editors. Disabling highlighting starts its fade-out; reduced-motion mode applies the state immediately. Structured tool paths, streamed Claude Read/Write inputs, and simple literal read commands are recognized; arbitrary script bodies are not scanned or executed to infer file activity.

## Receive events

Bulk command output remains display text; its actual logical output paths travel
separately to activity rendering. Large output lists publish their individual
file events in bounded GUI turns (up to 32 paths / 4 ms per batch) with at most
256 recent visual rows. Visual bookkeeping performs no filesystem resolution,
rejects multiline/control-character input, and stops ancestor traversal at the
tree root. The actual file services still enforce branch, symlink and settings
protections before I/O; a visual pulse never grants access or delays task completion.

```powershell
rak events --since 123 --wait 30 --types document,request,task --json
rak events --since 123 --stream
```

Events originate from UI/worker signals, including `selection.changed`, `editor.selection`, `document.changed`, `document.saved`, `editor.tabs`, `tree.location`, `branch.changed`, `mounts.changed`, `device.changed`, `task.changed`, `task.finished`, and `request.finished`. Waiting is driven by condition notifications. `--stream` emits newline-delimited JSON until Ctrl+C or workspace closure.

Store the returned `cursor` and `session_id`. The journal retains 512 events. A `gap` or changed session means the agent must refresh context before relying on previous state. Filters accept exact types or prefixes such as `document`. Closure releases blocked event and request waiters.

The API currently exposes existing job/activity inspection, not universal job cancellation or a structured flashing command namespace; capabilities reports those omissions explicitly. Native kitchen commands remain available through the existing CLI and RAK Terminal.

## User-to-agent inbox

Use **Send to agent** beside the editor tabs or in the file-tree context menu. The toolbar's **Agent inbox** opens requests and replies. The composer freezes selected logical files, editor range/revision, unsaved text excerpt, branch and device context when it opens. Moving focus into the composer does not change the attachment. **Use current selection** replaces it; the rounded X removes file/text attachments. Sending does not save documents.

The panel displays waiting, received, working, completed, failed, cancelled and interrupted requests. Agent progress and answers update the same record. New replies add a dot to the inbox/send button. Results can link to project files. Agent questions still use the separate `rak ui ask` queue.

An agent must consume the inbox to receive work. When none is waiting, the panel offers **Copy setup instruction for agent**. Give this to the agent in RAK Terminal once. The API does not install a background model or inject text into an arbitrary terminal.

```powershell
rak inbox list --json
rak inbox next --agent kitchen-agent --wait 30
rak inbox get <message-id>
rak inbox start <message-id> --agent kitchen-agent --claim <token>
rak inbox progress <message-id> --agent kitchen-agent --claim <token> --from status.json
rak inbox reply <message-id> --agent kitchen-agent --claim <token> --from result.json
rak inbox fail <message-id> --agent kitchen-agent --claim <token> --from result.json
```

`next` waits on condition notifications, returning `message: null` on bounded wait expiry or `closed: true` on shutdown. A message is claimed once, with a private `claim` token. Other consumers cannot claim it or report results with another token. List/context/events never expose the token. Read fresh Kitchen context before acting: attachments are a timestamped snapshot of user intent.

Progress JSON uses `{"message":"Checking vendor services"}`. Result JSON uses `{"answer":"What actually happened","paths":["D:\\ROM\\PORT\\vendor.img"]}`. `fail` records an operation failure without claiming the application crashed. Wait for the actual kitchen task result before reporting completion.

**Cancel** removes a queued request before receipt. **Request stop** on received/running work sets `cancel_requested`; it does not kill a subprocess. Check `inbox get` between operations, stop further work safely, then run `rak inbox ack-cancel <message-id> --agent kitchen-agent --claim <token>`. An operation already completed may return its real result after a stop request. Never force-kill or retry a flash based on inbox cancellation.

History is stored atomically under `%LOCALAPPDATA%\RennsAndroidKitchen\state\user\agent_inbox`, keyed by canonical project path. Queued requests survive reopening Kitchen. Previously claimed/running requests become `interrupted`, with expired claims; they are not automatically resumed. The store retains 128 records and 32 unfinished requests. Attached editor content shares a 32768-character budget, with explicit `truncated` flags. Corrupt/unreadable history stays untouched; the panel reports fallback to memory without persistence.
