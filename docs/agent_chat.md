# Native agent chat

Kitchen's agent entry point is a docked Qt Quick panel. It shares the workspace
palette, controls, split layout and motion service. There is no embedded terminal
or terminal scraping. Installed providers appear in the backend dropdown; the
toolbar, editor and tree actions appear only when at least one supported CLI is
found on the application's PATH. Discovery runs in a worker and refreshes when
the workspace becomes active or the user clicks Refresh. It never starts a model
turn. Shell aliases/functions are not executable discovery sources.

## Provider adapters

| CLI | Transport | Models and commands | Stop and permissions |
| --- | --- | --- | --- |
| `codex` | `app-server --listen stdio://` | `model/list`, effective thread model; Kitchen command aliases | `turn/interrupt`, server-initiated approval/question requests |
| `opencode` | `acp`, JSON-RPC over stdio | ACP model/config options, modes and `available_commands_update` | `session/cancel`, exact advertised permission option IDs |
| `claude` | Bidirectional `--input-format stream-json --output-format stream-json` | Native initialize response models/commands; `set_model` control | Native interrupt and `can_use_tool` controls; AskUserQuestion replies |
| `agy` | `--input-format stream-json --output-format stream-json --dangerously-skip-permissions` | `agy models`; model changes restart the idle connection and resume its exact conversation ID | Unrestricted, with no permission selector. Stop terminates the owned CLI and waits for OS exit confirmation; there is no stream interrupt API |

Providers use their existing local authentication and configuration. Kitchen does
not read token files, copy credentials or change the global model. AGY always
adds `--dangerously-skip-permissions` to its connection, per the user's explicit
preference; deprecated saved Restricted settings are ignored. Other providers keep
their native approval interfaces. A found executable is not proof of authentication or model access.
Claude receives the Kitchen contract through its native `--append-system-prompt`
argument as well as the per-turn workspace payload. Every turn states the live
file-edit and UI-control permissions explicitly. File editing is independent of
UI access; the preferred edit transport passes a replacement text-file path,
never replacement contents embedded in a shell command. A malformed native Write
call (for example, missing `file_path`) is a tool argument failure, not a Kitchen
UI permission failure.
Provider-auth and unavailable-model failures appear as ordinary operation errors
in the panel, with the full process log available locally.

The Kitchen project container and opened tree roots are distinct. For example,
`Documents/baru` may contain an opened `Documents/baru/erf` tree: the backend's
OS cwd and default output directory are `.../erf`, not the container. Every turn
includes a fresh authoritative workspace block (tree roots, active tree, cwd and
default output directory), even after attachments are cleared. Frozen attached
buffers stay separate. The turn's output root does not follow later UI selection
changes. The inbox and user message retain the workspace used for that turn;
`/status` also reports the backend working directory.

AGY receives `--new-project` for a fresh native conversation and explicit
`--add-dir` roots, with the active root first. It waits for native `init` before
submitting a prompt and rejects a reported cwd mismatch. Native sessions are
bound to the container, cwd and opened roots. Legacy unbound sessions, or a
changed active root, get a fresh backend session; the earlier native session ID
is archived and UI history remains intact. This matters because AGY resumes a
conversation's associated project, independently of the launch cwd. Native
unrestricted permissions remain unchanged; workspace context is not an OS
filesystem sandbox. Existing files are not relocated by this migration.

On Windows, known npm shims resolve to their native executable or Node entry
point. No user prompt, model name or filename is interpolated into `cmd.exe` or
PowerShell. On Windows, agent processes and model queries attach to a dedicated
background ConPTY host (`core.agent_conpty.AgentTerminalService`) via
`PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE`: the console is headless at creation
(preventing `CREATE_NEW_CONSOLE` window flicker), and ordinary console
descendants inherit it. Standard I/O remains on isolated pipes via
`STARTF_USESTDHANDLES` and `PROC_THREAD_ATTRIBUTE_HANDLE_LIST`, rather than
passing JSON protocols through VT output or the user-facing RAK Terminal tabs.
Regular Kitchen tools still use the shared terminal console. Closing the
panel leaves its agent turn running; workspace closure requests a cooperative
stop, closes input and records unfinished work as interrupted. It does not claim
that a device process has been killed. A stopping request keeps the stop button
enabled: a second click explicitly terminates an unresponsive owned CLI. AGY
uses that route on the first click because closing its input only waits for the
current answer. Process exit is observed separately from stdout/stderr EOF, so
a child tool holding inherited pipes open cannot trap the UI in Stopping. Late
packets from the retired connection cannot complete a cancelled or newer turn.
Only the owned CLI is terminated; existing Kitchen/device jobs are not killed.

## Conversation and commands

The composer has compact assistant and model dropdowns, a selection attachment
button, and a circular SVG up-arrow send button (a stop icon during a turn).
The `+` opens an attachment menu: current selection, local files, local folder,
or clear attachments. Files can also be dropped anywhere in the chat. Drops
accept Copy only: existing files stay at their original paths. Chips show the
filename and icon or image thumbnail, with individual removal in the composer.
Explicitly attached external paths are allowed; their native tools, rather than
the project-only RAK filesystem API, must be used to read them. The prompt carries
path references and metadata, without embedding file bytes or thumbnail URLs.
Added paths merge into the frozen context; an existing unsaved buffer snapshot
is not silently refreshed by a file attachment.

Ctrl+V (or Shift+Insert) attaches clipboard images as lossless PNGs in the
project's AppData chat attachment folder. Clipboard file references keep their
original paths; ordinary text paste remains normal text input. Select an image
chip and press Ctrl+C to copy its pixels; other file chips copy a file reference.
Attachments can be sent without text. Sending clears the composer attachments
while retaining their references in the conversation and inbox history. Removing
a chip never deletes the source file or an image retained by sent history.
Opening the panel or switching assistants prepares the connection and model
catalog automatically without submitting a prompt. There is no Connect button.
A message sent during the handshake waits for the backend's real ready event.
The model dropdown is always beside the assistant selector; on narrow panels it
wraps to a second line. A model change applies to the next
turn, and is acknowledged by the native protocol where supported. The catalog is
not an account-entitlement check. OpenCode mode controls appear when advertised.
Unsupported controls are not silently translated to arbitrary TUI input.

The model popup includes per-model thinking levels and native usage/limits:

The popup separates models, thinking level, and account limits. Account limits
start collapsed each time the menu opens; clicking the row reveals and refreshes
the limits. Opening the model menu alone does not trigger a usage query. Model
choices and expanded limits have bounded scroll areas to keep the menu compact.
Quota windows
use localized durations (for example, 300 minutes becomes 5 hours; 10080 minutes
becomes Weekly), with a remaining-percentage bar and local reset date/time.
Shared limits remain account/group limits rather than claiming a separate quota
for each model. The expandable usage report is a fallback for backends that send
report content without structured quota groups. When quota bars are available,
the duplicate report control and body stay hidden, including for Antigravity.
Refresh updates the native metadata without submitting a turn.

| Backend | Thinking level | Usage and quota source |
| --- | --- | --- |
| Codex | Actual `supportedReasoningEfforts`; applied as `turn/start.effort` | `account/rateLimits/read` and update notifications; account buckets are labeled shared |
| OpenCode | Advertised ACP `thought_level` / effort config; persisted after `session/set_config_option` acknowledgment | `opencode stats --project . --models --cost --json`; project usage is not presented as account quota |
| Claude | Actual `supportedEffortLevels`; `--effort` on the next connection, with its existing session resumed | Standalone `--print /usage --output-format json`, resuming the idle conversation when available; quota is unavailable if the native response only reports usage |
| AGY | Read-only standalone `--print /effort --output-format json` for the selected model; applies using invocation-scoped `--effort` | Standalone `--print /usage --output-format json`, showing actual shared model-group buckets and reset times |

Opening the popup queries metadata without submitting a model prompt or changing
global provider settings. Unsupported tiers and missing quota numbers are never
fabricated. A catalog timeout is an optional discovery failure, preserved with
its actual command, output, exit status and traceback under AppData. It shows a
small inline fallback with Retry and diagnostic-log controls; chat still works
using the active/default model. Failed catalogs are cached until explicit retry,
and do not reopen a modal on every connection. Expected operation failures for
this job are handled by its callback; internal defects still use Fatal Crash and
unrelated AsyncJobs retain their normal error presentation.

The command picker merges these local aliases with commands advertised by the
connected backend:

* `/model [id]`: open the model picker or select an advertised model.
* `/status`: report this connection's actual session, model and policy.
* `/usage`: show the latest usage reported by the backend.
* `/effort [level]`: choose an advertised thinking level, or open its picker.
* `/new`: start a separate conversation; active turns must finish first.
* `/stop`: request stopping; completion is event-driven, not timer-driven.
* `/help`: show supported commands.
* `/inbox`: open the retained cooperative inbox for external agents.

Unknown slash commands remain in the draft with an explanatory error. Advertised
native slash commands are forwarded verbatim so their syntax stays intact; the
normal frozen-context preamble is not inserted ahead of them. AGY's interactive
slash commands are not sent into its stream: the CLI explicitly rejects those.
Kitchen aliases provide the corresponding UI functions where possible. A
backend-specific feature without a supported protocol API remains unavailable;
this is not a promise to reproduce every CLI TUI screen.

Messages are streamed into selectable native text, including Markdown responses.
User messages are right-aligned bubbles; assistant replies are unboxed, with a
small copy action below. Scrolling up suspends automatic following, including
while more text arrives. A centered down-arrow above the composer returns to the
latest message and resumes following. No timestamps or elapsed work durations
are invented from a backend's terminal status. A monotonic clock measures the
current turn from submission, including connection and approval waits. One
plain, non-clickable status follows the turn's text: **Working for …**,
**Worked for …**, **Failed after …**, or **Stopped after …**, localized with Kitchen.
A one-second UI timer updates elapsed time only; real result events determine
completion. Persisted active work is marked interrupted on restart.
Actual thinking phases and tool calls appear as separate chronological entries:
**Thought for …**, **Ran …**, **Analyzed …**, or failure entries. Each is collapsed
by default and opens only its own summary or tool output, command, working
directory and exit status when provided. There is no expand-all work status. Missing
tool completion events are labeled as unreported outcomes, never invented success.
Thinking is driven by native reasoning lifecycle/summary notifications (Codex),
`agent_thought_chunk` (ACP), or explicit thinking content-block lifecycle (Claude).
No thinking phase is inferred from silence, time, or cumulative thinking-token
usage. Codex exposes its readable summary; raw reasoning blocks are not rendered.
The current documented AGY headless stream does not expose a separate thinking
phase, so silent AGY turns show plain Working until real text/tool events arrive.
Empty assistant placeholders and generic completion banners are hidden. A terminal
result with no written reply is a failed request with a readable explanation,
including AGY's `SUCCESS` result with empty `response` and `denied_actions`.
Final text is shown even when an older empty placeholder exists. Old saved
conversations get the same explanation without rewriting their original log.
Permissions
use independent request IDs and exact native decisions. User questions collect
explicit answers; no default selection is submitted automatically. A process exit
of zero without a terminal turn result is a failure, not success. Final message
items replace their streamed preview rather than duplicating the response.

Permission cards put **Save / Yes / No** (**Simpen / Ya / Tidak** in Indonesian)
at the bottom right. Save approves and remembers through the native backend:
Codex `acceptForSession` (or session-scoped requested permissions), ACP's exact
advertised `allow_always` ID, and Claude's offered allow-rule suggestions scoped
to this session. If no remember option is available, Save is disabled rather
than relabeling Cancel or inventing a global policy. Yes approves once; No
declines. AGY does not present these tool approval prompts in unrestricted mode.
No Enter spam or blank NDJSON messages are injected, and global AGY settings are
unchanged. Greetings and connection tests are instructed to
reply directly without inspecting Kitchen files or calling tools.

## Kitchen state and shared inbox

The composer captures logical paths, selection, revision and unsaved excerpt
when opened. Focus changes do not refresh this snapshot. **+ Attach selection** explicitly
refreshes it, and the remove control clears it. The snapshot is data; backend guidance
requires rechecking `rak context` and `rak ui read` before acting. Existing
`WorkspaceAgent` and revision-checked Monaco edit/review/save services are reused;
no widget inspection or replacement CLI service was added.

Project Settings now includes Agent autosave (on), file activity highlighting
(on), and UI access (off). The host autosaves acknowledged edits, including
mounted text buffers; background text edits use the logical view without opening
tabs. Low-opacity rainbow fills crossfade over observed file paths and ancestor folders without
navigation. Native CLI events must expose a structured path to mark a file;
arbitrary shell text is not treated as evidence. The provider sees these
preferences in `rak ui settings` and live context. Opening editors/windows and
using registered controls requires UI access. Settings remain user-only; the
agent can use `rak ui guide` to scroll to/highlight a preference for the user.

Each sent task is recorded in `AgentInbox`, targeted to a unique local consumer
name, then atomically claimed using `next`. The host keeps the private claim
token and reports real start/completion/failure/cancellation back to the same
inbox. Tokens are not sent to the provider, transcript, public context or events.
Backend instructions tell it the host already owns this claim, so it must not
run a second inbox consumer. Existing external `rak inbox next` consumers continue
to work through the legacy inbox. Restart preserves queued work but never resumes
an agent/device task automatically.

Native provider file/shell tools continue to obey that provider's own policy.
The context preamble is guidance, not a new filesystem sandbox. Kitchen's own
live-buffer operations still require the existing revision checks, and saving
remains separate. Do not equate a generic backend file edit with a Monaco review.

## Local persistence

History, drafts and context live in
`%LOCALAPPDATA%/RennsAndroidKitchen/state/user/agent_chat/<project hash>/`.
History keeps up to 24 conversations and 400 message previews per conversation.
The visible/persisted preview is limited to 16,000 characters per item; full
stdout/stderr and exit status are recorded in each connection's JSONL process log.
When the total history reaches 24 MiB, older conversation previews are archived
while native session IDs and process logs remain available.
Corrupt history is preserved and reported, rather than overwritten. Native
provider session IDs allow an explicitly selected chat to resume when that
provider supports it. Switching backends starts a separate conversation.

`agent.chat.state` events are published into the existing cursor-based journal
from actual lifecycle transitions. Chat history and process logs stay outside ROM
trees. They may contain the user's attached excerpts and tool output; they are
local artifacts, not repository source files.

## Validation

* `python -m unittest tests.test_agent_chat`: protocol framing, capability mapping,
  permission decisions, terminal-result failures, atomic history, frozen context,
  claim privacy, interruption and process lifetime.
* `python -m unittest tests.test_agent_process`: real Windows parent/descendant
  console inheritance, asserting both console windows are hidden; real owned
  process termination is confirmed before a descendant releases inherited pipes.
* `python -m unittest tests.test_agent_metadata`: bounded catalog timeouts with
  preserved stdout/stderr, truthful quota versus token-usage mapping, and scoped
  optional-error handling that still presents internal defects and other GUI errors.
* `python tools/verify_qtquick_agent_chat.py`: real hidden fixture subprocess,
  native Qt Quick transcript, live Monaco unsaved selection, permission round trip,
  cooperative interruption, automatic provider connection, compact model picker,
  wheel scrolling / streaming position preservation / jump-to-latest, and zero
  new terminal sessions. Preview conversations are inert fixtures in a temporary
  project, not claims about work performed on a real ROM.
* `python tools/verify_qtquick_inbox.py`: retained external CLI inbox integration.
* `python tools/probe_agent_chat.py <provider>`: read-only native handshake.
  `--prompt "..."` explicitly runs an inference smoke test in an inert temp folder.

Development smoke tests established live Codex/OpenCode/AGY turns and a Claude
handshake. Claude's locally configured default model was unavailable; the adapter
preserved that configuration and reported the native error. Tests do not claim
that every provider/account/model combination is available.

## Protocol references and licenses

Adapters are RAK-authored MPL-2.0 clients, not copied provider implementations.
No provider runtime or SDK was added to the distribution, and provider CLIs remain
separately installed. No changes were made to private-module licensing.

* [Codex app-server](https://developers.openai.com/codex/app-server/)
* [OpenCode ACP](https://opencode.ai/docs/acp/)
* [Agent Client Protocol](https://agentclientprotocol.com/protocol/v1/session-setup)
* [Claude CLI reference](https://code.claude.com/docs/en/cli-reference)
* [OpenCode model variants](https://docs.opencode.ai/docs/models/)
* [Antigravity effort controls](https://antigravity.google/docs/cli/commands/effort/)
* [Antigravity quota groups](https://antigravity.google/docs/cli/commands/usage/)
* [Claude Agent SDK streaming](https://platform.claude.com/docs/en/agent-sdk/streaming-output)
* [Antigravity headless protocol and its limitations](https://antigravity.google/docs/cli/headless/)
* [Windows process creation flags](https://learn.microsoft.com/en-us/windows/win32/procthread/process-creation-flags)
* [Windows startup window visibility](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/ns-processthreadsapi-startupinfow)
