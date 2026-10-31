# SPDX-License-Identifier: MPL-2.0
"""Discover installed CLIs without launching a shell or reading credentials."""
import os
from pathlib import Path
import re
import shutil


PROVIDERS = (
    {'id': 'codex', 'label': 'Codex', 'commands': ('codex',), 'protocol': 'codex'},
    {'id': 'opencode', 'label': 'OpenCode', 'commands': ('opencode',), 'protocol': 'acp'},
    {'id': 'claude', 'label': 'Claude Code', 'commands': ('claude',), 'protocol': 'claude'},
    {'id': 'agy', 'label': 'Antigravity', 'commands': ('agy',), 'protocol': 'agy'},
)


def resolve_cli(name, which=shutil.which):
    found = which(name)
    if not found: return None
    path = Path(found)
    if os.name != 'nt' or path.suffix.lower() not in ('.cmd', '.bat', '.ps1'):
        return [str(path)]
    # Bypass known npm shims, so user/model text never goes through cmd.exe.
    packages = {
        'claude': ('@anthropic-ai/claude-code/bin/claude.exe', '@anthropic-ai/claude-code/cli.js'),
        'opencode': ('@opencode/cli/bin/opencode.exe', 'opencode-ai/bin/opencode'),
        'codex': ('@openai/codex/bin/codex.js',),
    }
    for relative in packages.get(name, ()):
        candidate = path.parent / 'node_modules' / relative
        if candidate.is_file():
            if candidate.suffix.lower() == '.exe': return [str(candidate)]
            node = which('node')
            if node: return [node, str(candidate)]
    return None


def discover():
    result = []
    for provider in PROVIDERS:
        argv = next((resolved for name in provider['commands'] if (resolved := resolve_cli(name))), None)
        if argv: result.append({**provider, 'argv': argv, 'path': argv[-1]})
    return result


def launch_arguments(provider, model='', session='', *, effort='',workspace_dirs=(),instructions=''):
    kind = provider['protocol']
    if kind == 'codex': return ['app-server', '--listen', 'stdio://']
    if kind == 'acp': return ['acp']
    if kind == 'claude':
        args = ['--print', '--input-format', 'stream-json', '--output-format', 'stream-json',
                '--verbose', '--include-partial-messages', '--permission-prompt-tool', 'stdio']
        if instructions:args+=['--append-system-prompt',instructions]
        if model: args += ['--model', model]
        if session: args += ['--resume', session]
        if effort:args+=['--effort',effort]
        return args
    if kind!='agy':raise ValueError('Unsupported agent protocol: '+str(kind))
    args = ['--input-format', 'stream-json', '--output-format', 'stream-json', '--dangerously-skip-permissions']
    if workspace_dirs:
        if not session:args+=['--new-project']
        for path in dict.fromkeys(workspace_dirs):args+=['--add-dir',path]
    if model: args += ['--model', model]
    if session: args += ['--conversation', session]
    if effort:args+=['--effort',effort]
    return args


def parse_agy_models(text):
    rows = []
    seen = {}
    effort_rank = {'low': 1, 'medium': 2, 'high': 3, 'xhigh': 4, 'max': 5}
    for line in re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', text).splitlines():
        match = re.match(r'^\s*([a-zA-Z0-9][a-zA-Z0-9._/-]+)(?:\t+| {2,})(.+?)\s*$', line)
        if not match: continue
        raw_id, raw_label = match[1], match[2]
        effort_match = re.search(r'\s*\((low|medium|high|xhigh|max)\)$', raw_label, re.IGNORECASE)
        if effort_match:
            effort = effort_match.group(1).lower()
            clean_label = raw_label[:effort_match.start()].rstrip()
            clean_id = re.sub(rf'-{effort}$', '', raw_id, flags=re.IGNORECASE)
            if clean_id in seen:
                row = seen[clean_id]
                existing = [e['id'] for e in row.get('efforts', [])]
                if effort not in existing:
                    row.setdefault('efforts', []).append({'id': effort, 'label': effort})
                    row['efforts'].sort(key=lambda e: effort_rank.get(e['id'], 99))
            else:
                row = {'id': clean_id, 'label': clean_label, 'efforts': [{'id': effort, 'label': effort}]}
                seen[clean_id] = row
                rows.append(row)
        else:
            if raw_id not in seen:
                row = {'id': raw_id, 'label': raw_label}
                seen[raw_id] = row
                rows.append(row)
    return rows
