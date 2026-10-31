# SPDX-License-Identifier: MPL-2.0
"""Recover Source transactions and migrate branches before exposing a workspace."""
import os

from main_ext.class_NeoBranchManager import NeoBranchManager


def prepare_sources(bases, *, progress=None, cancelled=None):
    unique=list(dict.fromkeys(os.path.normpath(base) for base in bases))
    for base in unique:
        if not os.path.isdir(base):raise FileNotFoundError(base)
    restored={}
    for base in unique:
        manager=NeoBranchManager(base)
        restored[base]=manager.recover_interrupted_repacks(progress=progress, cancelled=cancelled)
        store=os.path.join(base,'neobranch')
        if os.path.isdir(store):
            from core.neobranch_checkout import recover
            for root in os.listdir(store):
                if not os.path.isdir(os.path.join(store,root)):continue
                with manager.repack_transaction(root,progress=progress,cancelled=cancelled):
                    recover(manager,root,progress=progress)
                    active=manager.get_active_branch(root)
                    if active and not manager.is_physical_checkout(root):
                        manager.checkout_branch(root,active['name'],progress=progress,cancelled=cancelled)
    return True,restored
