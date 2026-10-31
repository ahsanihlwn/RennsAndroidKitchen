# SPDX-License-Identifier: MPL-2.0
"""Keep the Kitchen project container distinct from its opened workspace trees."""
import os


def describe(owner):
    trees=[]
    for index,directory in enumerate(owner.directories[:2]):
        if not directory.base:continue
        root=os.path.abspath(directory.base)
        trees.append({'id':index,'name':os.path.basename(root),'path':root,'location':directory.location})
    active=getattr(owner,'activeTreeId',0)
    tree=next((row for row in trees if row['id']==active),trees[0] if trees else None)
    root=tree['path'] if tree else ''
    from core.agent_policy import settings
    from core.agent_chat_store import project_folder
    preferences=settings(owner)
    return {'project_container':owner.project,'trees':trees,'tree_roots':[row['path'] for row in trees],
            'active_tree':tree['id'] if tree else None,'working_directory':root,'default_output_directory':root,
            'agent_permissions':{'project_file_read':True,'project_file_edit':True,'file_edit_requires_ui_triggers':False,
                                 'ui_triggers':preferences['ui_triggers'],'autosave':preferences['autosave'],'highlight':preferences['highlight'],'settings_write':False},
            'scratch_directory':str(project_folder(owner.project)/'requests'),
            'path_policy':'Create project files under default_output_directory unless the user explicitly names another project tree. '
                          'project_container is not the output directory. Attached external files are references, not output folders.'}


def path_key(path):return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def binding(workspace):
    if not workspace['working_directory'] or not os.path.isdir(workspace['working_directory']):
        raise ValueError('Wait for an open project tree before starting an agent')
    return {'version':1,'container':path_key(workspace['project_container']),
            'cwd':path_key(workspace['working_directory']),'roots':sorted(path_key(path) for path in workspace['tree_roots'])}
