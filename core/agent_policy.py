# SPDX-License-Identifier: MPL-2.0
"""Project preferences shared by the agent API and its UI."""
DEFAULTS={'autosave':True,'highlight':True,'ui_triggers':False}
SETTING_KEYS={'agent.'+key for key in DEFAULTS}


def normalize(values):return {**values,**{key:values.get(key,default) if isinstance(values.get(key,default),bool) else default for key,default in DEFAULTS.items()}}


def settings(owner):
    values=getattr(owner,'projectSettings',{}).get('agent',{})
    values=normalize(values)
    return {key:values[key] for key in DEFAULTS}


def require_ui(owner):
    if not settings(owner)['ui_triggers']:
        raise ValueError('Agent UI access is disabled. Ask the user to enable Project settings > Agent > UI access. '
                         'Use rak ui guide agent.ui_triggers to show the setting; do not change settings yourself.')


def protected_control(name):
    return (name.startswith(('workspaceSettings','agentChatApproval_','workspaceAgentAnswer','workspaceAgentReview','workspaceFatal','agentSettings'))
            or name=='workspaceAction_settings' or name.endswith('_settings'))


UI_ACTIONS={'open_in_explorer','explorer','open_super','open_payload','openHex','inspect_bin','openText','newFile','newFolder',
            'symlink','create_symlink','neobranch','make_filesystem','android','find','automation','compare','create_flashable','import','settings'}
