# SPDX-License-Identifier: MPL-2.0
"""NeoBranch dialogs and editor-switch contract from FileTree RightClick."""
import os
from pathlib import Path

from PySide6.QtCore import QObject, Property, Slot

from core import ui_lang
from core.workspace_files import WorkspaceFiles
from main_ext.class_NeoBranchManager import BRANCH_COLORS, NeoBranchManager


def run_branch_operation(manager,root,action,name=None,color='blue',*,headless=False):
    from core.terminal_execution import library_operation
    with library_operation('NeoBranch '+action+' '+root) as output:
        def progress(message):
            output(message+'\n')
            if headless:print(message,flush=True)
        if action=='create':manager.create_checkout_branch(root,name,color,progress=progress)
        elif action=='switch':manager.checkout_branch(root,name,progress=progress)
        elif action=='delete':manager.delete_branch(root,name)
        else:raise ValueError('Unknown NeoBranch operation')
        from core.rak_agent_docs import update_all_agent_files
        update_all_agent_files(manager.tree_base)
    return name


class WorkspaceBranches(QObject):
    def __init__(self, session):
        super().__init__(session)
        self.session = session
        self._context = None

    @Property('QVariantList', constant=True)
    def colors(self):
        return [{'name': name, 'color': color} for name, color in BRANCH_COLORS.items()]

    def show(self, path):
        base = self.session.active_tree.base
        logical, root = WorkspaceFiles(base).logical(path)
        if '/' in root or root in ('config', 'neobranch') or not os.path.isdir(logical):
            raise ValueError('Select a partition root for NeoBranch')
        self._context = (base, root)
        self._show()

    def _manager(self):
        if self._context is None:
            raise ValueError('No partition selected')
        base, root = self._context
        return NeoBranchManager(base), root

    def _show(self, *, create=False):
        manager, root = self._manager()
        branches = manager.list_branches(root)
        active = manager.get_active_branch(root)
        self.session.presentationRequested.emit('neobranchCreate' if create or not branches else 'neobranchPicker',
                                              {'root': root, 'branches': branches,
                                               'active': active['name'] if active else ''})

    def _prepare_switch(self):
        session = self.session
        manager, root = self._manager()
        if session.task_queue.is_busy():
            session.message('Wait for the current filesystem task to finish.', 'NeoBranch busy')
            return False
        if Path(manager._source_dir(root), 'repack_manifest.json').is_file():
            session.message('Wait for the partition repack to finish.', 'NeoBranch busy')
            return False
        bridge = session.editor.bridge
        paths = bridge.neobranch_tabs(manager.tree_base, root)
        partition=os.path.join(manager.tree_base,root)
        from main_ext.class_NeoBranchManager import CONFIG_SUFFIXES
        metadata={os.path.normcase(os.path.join(manager.tree_base,'config',root+suffix)) for suffix in CONFIG_SUFFIXES}
        def belongs(tab):
            path=tab['path']
            if path.startswith('mount://'):return False
            if os.path.normcase(os.path.abspath(path)) in metadata:return True
            try:return os.path.commonpath([os.path.abspath(path),partition])==partition
            except ValueError:return False  # A second ROM tree can use another drive.
        tabs=[tab for tab in session._tab_data if belongs(tab)]
        paths=list(set(paths+[tab['path'] for tab in tabs if tab['kind']=='editor']))
        if any(bridge.is_unsaved(path) for path in paths) or any(tab.get('viewerDirty') for tab in tabs):
            session.message('Save or close the edited tabs for this partition before switching branches.',
                            'Unsaved NeoBranch tabs')
            return False
        for path in paths:
            session._close_tab(path)
        for tab in tabs:
            if tab['kind']!='editor':session._close_tab(tab['path'])
        return True

    def _refresh(self):
        for directory in self.session.directories:
            if directory.base and os.path.normcase(directory.base) == os.path.normcase(self._context[0]):
                directory.reloadMetadata()

    @Slot(str, str)
    def create(self, name, color):
        if not self._prepare_switch():
            return
        manager, root = self._manager()
        if color not in BRANCH_COLORS:
            return
        # Validate with the same manager before queueing a filesystem operation.
        try:manager._validate_branch_name(name)
        except ValueError as error:
            self.session.message(str(error), 'Create NeoBranch');return
        from ui.workspace_actions import operation
        def work():
            return run_branch_operation(manager,root,'create',name,color)
        operation(self.session, f'preparing-neobranch-{root}-{name}', work)

    @Slot(str)
    def choose(self, choice):
        manager, root = self._manager()
        if choice == '__NEW__':
            self._show(create=True);return
        if choice.startswith('__DEL__'):
            name = choice[len('__DEL__'):]
            if not any(row['name'] == name for row in manager.list_branches(root)):
                return
            def delete():
                active=manager.get_active_branch(root)
                if active and active['name']==name and not self._prepare_switch():return
                from ui.workspace_actions import operation
                operation(self.session, 'Delete NeoBranch '+name, lambda:run_branch_operation(manager,root,'delete',name))
            self.session.message(ui_lang.t('rc_delete_branch_msg',name=name),
                                 ui_lang.t('rc_delete_branch_title'),confirm=delete,
                                 danger=True,accept=ui_lang.t('rc_delete'))
            return
        name = None if choice == 'NONE' else choice
        if name is not None and not any(row['name'] == name for row in manager.list_branches(root)):
            return
        if not self._prepare_switch():
            return
        from ui.workspace_actions import operation
        def work():
            return run_branch_operation(manager,root,'switch',name)
        operation(self.session, 'Checkout NeoBranch '+(name or 'Source'), work)
