# SPDX-License-Identifier: MPL-2.0
"""Report actual local dependencies and session limits without launching tools."""
import json
import os
from core.cli_contract import PRIMARY


def describe(cwd,owner=None):
    from core.unix_tools import resolve_tool
    from core.binary_handler import resolve_binary
    from core.app_paths import BIN_DIR
    from core.rak_neobranch_view import project_root,ActiveProjectView
    tools={}
    for name in ('sed','grep','diff','head','tail','wc'):
        path=resolve_tool(name);tools[name]={'available':bool(path),'path':path}
    for name in ('make_ext4fs.exe','mkfs.erofs.exe','dump.erofs.exe','lpunpack.exe','brotli.exe','magiskboot.exe'):
        path=resolve_binary(BIN_DIR,name);tools[name]={'available':bool(path),'path':path}
    from core.apktool_tool import resolve_java,is_apktool_available,is_signer_available
    java=resolve_java();tools['java']={'available':bool(java),'path':java}
    for name in ('apktool.jar','apksigner.jar'):
        path=resolve_binary(BIN_DIR,name);tools[name]={'available':bool(path),'path':path}
    try:root=project_root(cwd)
    except ValueError:root=None
    live=bool(owner is not None and not getattr(owner,'headless',False))
    view=ActiveProjectView(root) if root else None
    branches=view.active_branches() if view else {}
    modes={partition:'physical' if view.manager.is_physical_checkout(partition) else 'legacy_overlay' for partition in branches}
    return {'version':1,'mode':'live' if live else 'standalone','live_session':live,'primary_commands':PRIMARY,
            'project':{'root':root,'active_branches':branches,'branch_modes':modes},
            'tools':tools,'verification':{'paths':'merged on-disk NeoBranch view','metadata':'Android fs_config and file_contexts; not host ACLs',
                'hash':'SHA-256, streaming','difference_status':1,'difference_is_operation_failure':False,'head_tail_diff_wc':'installed GNU tools'},
            'requires_live_session':['context','events','ui','inbox','activity','mount','unmount'],
            'ui_buffers_available':bool(live and getattr(owner,'_agent',None)),
            'apk':{'decompile_build_available':is_apktool_available(),'signing_available':is_signer_available()},
            'shell':{'arbitrary_host_commands_isolated':False,'project_reads_and_edits':'native agent tools; physical checkout uses ordinary paths',
                     'legacy_overlay':'switch/open project to migrate before using native tools'},
            'notes':['Tool availability reports discovery, not a guarantee that a particular input is valid.',
                     'Inspect each input for actual file type and usable operation argv.']}


def render(value,as_json=False):
    if as_json:return json.dumps(value,ensure_ascii=False)
    lines=['RAK environment: '+value['mode'],'Project: '+str(value['project']['root'])]
    lines.extend(name+': '+(info['path'] if info['available'] else 'unavailable') for name,info in value['tools'].items())
    lines.append('Verification uses the active on-disk branch. GUI buffers require a live session.')
    return '\n'.join(lines)
