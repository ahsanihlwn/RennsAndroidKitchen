# Error reporting

- Internal application defects and unsafe failed Source recovery use Fatal Crash, debug output, and `crash.log`. Ordinary GUI operation failures use the Operation failed dialog with See Log; they are not crashes. Failures started in RAK CLI go to CLI output and debug/log files; internal application defects still use Fatal Crash and return their traceback to the CLI.
- Fatal classification requires a real internal Python exception/traceback or its preserved typed failure. A plain message, logging.ERROR severity, process exit code, or `(False, text)` is not evidence of a crash. Generic message APIs default to normal dialogs. Access/auth state notifications must never enter Fatal Crash, and operation-error delivery must never fall back to a fatal presenter.
- Preserve the per-command `CommandContext` across the Qt bridge, workers, and callbacks. Never suppress errors using a process-wide CLI flag: unrelated GUI errors must still appear normally. Keep command failure types and task identities through queue completion so a failed worker cannot return CLI success.
- Use `core.error_reporting.report_fatal(...)` or standard logging for application errors. Operation failure reports set `application_error=False`; the shared handler applies the command's origin when deciding whether to display a dialog.
- Preserve the full tool output or exception traceback, with the operation name. Keep failed operations marked as failed in the task queue and activity log.
- Reuse the queued fatal dialog and its See Log / Copy controls. Use `error_checkpoint` and `error_reported_since` when forwarding a failure that its callback may already have reported.
- Successful notifications use `error=False`; confirmations and intentional cancellation keep their normal UI flow.
- Android device discovery runs ADB and fastboot probes concurrently. Empty device lists, waiting-for-device output, and probe timeouts are normal discovery outcomes: show `No device connected` when neither probe finds a device, without Fatal Crash.
- Device flashing starts immediately, bypassing the filesystem task queue, but shares the main loading/activity service. Do not run device probes during a flash. Resolve ADB/fastboot through the installed package registry, as the QWidget version did; never retry a failed flash automatically.
- Kitchen tool subprocesses use `core.kitchen_process` so their actual command, stdout/stderr, and exit status appear in RAK Terminal while preserving subprocess APIs. GUI QProcesses start through `WorkspaceSession.runTerminalProcess`. Keep normal interactive shell sessions separate from the shared Kitchen process console. Closing that console view must not kill an active tool; explicit process cancellation or workspace shutdown controls process lifetime.

# Source recovery

- Project loading and the task queue share `SourceRecovery`. Use its progress and finished signals to update loading, clear waiting flags, and resume queued operations. Do not infer completion from timers or impose an elapsed startup deadline on a running recovery.
- Keep the cross-process Source transaction locked across prepare, image build, and restore. Preserve the backup, branch edits, recovery journal, and completed staging copy on failure so retry can recover without reloading the project or copying the whole Source again.

# Agent UI API

- `WorkspaceAgent` exposes semantic UI actions through the authenticated RAK CLI IPC. Read live context from the workspace and Monaco buffers; preserve logical NeoBranch and mount paths. Do not inspect widgets or replace existing CLI command services to infer state.
- Editor edits require the revision returned by `rak ui read`, then a second content check inside Monaco. Apply with undoable model operations, acknowledge the actual result, and keep disk saving separate. Reviews recheck the revision after the user accepts; a stale review must preserve the user's buffer.
- UI questions, notices, and reviews use independent request IDs and a queued agent dialog. Cancellation, workspace closure, conflict, and pending/applying states must be explicit. Do not replace a filesystem confirmation or Fatal Crash dialog with an agent prompt.
- Publish workspace events from actual signals into the bounded, cursor-based `AgentEvents` journal. Wait with condition notifications, release waiters on session closure, and report event gaps/session IDs so agents can refresh context. Never infer completion from a delay.
- Freeze the inbox's attached logical paths, selection, buffer revision and unsaved excerpt when its composer opens. Keep the draft across focus changes and persist history under AppData, outside ROM trees.
- Claim inbox work atomically with an agent name and private token. Do not expose tokens through list/context/events. Expire claims and mark abandoned work interrupted on restart; preserve queued requests without auto-resuming device operations.
- Inbox delivery uses a cooperative `rak inbox next` consumer. Show actual waiting/working state. Do not inject messages into arbitrary terminals. A requested stop must wait for agent acknowledgment and must not pretend a device command was killed.
