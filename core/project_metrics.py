# SPDX-License-Identifier: MPL-2.0
"""Project-card values shared independently of the old QWidget launcher."""
import math
import os
import time
from core import ui_lang


def format_size(total):
    if total<1024:return f'{total} B'
    if total<1024**2:return f'{total/1024:.1f} KB'
    if total<1024**3:return f'{total/1024**2:.1f} MB'
    return f'{total/1024**3:.2f} GB'


def folder_size_str(path,check_interrupted=None):
    if not path or not os.path.exists(path):return '0 B'
    total=0
    try:
        if os.path.isfile(path):total=os.path.getsize(path)
        else:
            pending=[path]
            while pending:
                if check_interrupted and check_interrupted():return None
                directory=pending.pop()
                try:
                    with os.scandir(directory) as entries:
                        for number,entry in enumerate(entries):
                            if number%128==0 and check_interrupted and check_interrupted():return None
                            try:
                                if entry.is_symlink() or (hasattr(entry,'is_junction') and entry.is_junction()):continue
                                if entry.is_dir(follow_symlinks=False):pending.append(entry.path)
                                elif entry.is_file(follow_symlinks=False):total+=entry.stat(follow_symlinks=False).st_size
                            except OSError:pass
                except OSError:pass
    except OSError:pass
    return format_size(total)


def trash_remaining_text(entry,now=None):
    if not entry.get('handle'):return ui_lang.t('trash_recovery_pending')
    remaining=max(0,entry['expires_at']-(time.time() if now is None else now))
    if remaining<=0:return ui_lang.t('trash_expired')
    if remaining<86400:return ui_lang.t('trash_hours_left',n=math.ceil(remaining/3600))
    return ui_lang.t('trash_days_left',n=math.ceil(remaining/86400))


def project_drive_fill(theme,special=False):
    from PySide6.QtGui import QColor
    base=QColor(theme.BG2) if theme.is_light else QColor(theme.BG4).lighter(120)
    if special:
        accent=QColor(theme.accent_hex);amount=.10 if theme.is_light else .18
        base=QColor(*(round(channel*(1-amount)+tint*amount) for channel,tint in zip(base.getRgb()[:3],accent.getRgb()[:3])))
    return base.name()
