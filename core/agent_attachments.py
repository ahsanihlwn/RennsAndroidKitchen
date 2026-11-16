# SPDX-License-Identifier: MPL-2.0
"""Local attachment references; only clipboard pixels are copied to AppData."""
import os
from pathlib import Path
import uuid

from PySide6.QtCore import QIODevice,QSaveFile,QUrl

MAX_ATTACHMENTS=20
IMAGE_SUFFIXES={'.png','.jpg','.jpeg','.webp','.bmp','.gif','.tif','.tiff','.avif','.svg','.ico'}


def local_path(value,project):
    value=str(value)
    if value.startswith('mount://'):return value
    if os.path.isabs(value):return os.path.normpath(value)
    url=QUrl(value)
    if url.isLocalFile():return os.path.normpath(url.toLocalFile())
    if url.scheme():raise ValueError('Only local files and mounted project paths can be attached')
    return os.path.abspath(os.path.join(project,value))


def path_key(path):return path if path.startswith('mount://') else os.path.normcase(os.path.abspath(path))


def reference(path,*,external=False,source='file'):
    if path.startswith('mount://'):
        return {'path':path,'name':path.rstrip('/').rsplit('/',1)[-1],'kind':'file','size':0,'external':False,'source':source}
    stat=os.stat(path)
    folder=os.path.isdir(path)
    if not folder and not os.path.isfile(path):raise ValueError('Attachment must be a regular file or folder')
    return {'path':path,'name':Path(path).name or path,'kind':'folder' if folder else 'image' if Path(path).suffix.lower() in IMAGE_SUFFIXES else 'file',
            'size':0 if folder else stat.st_size,'external':external,'source':source}


def save_clipboard_image(image,folder):
    if image.isNull():raise ValueError('Clipboard does not contain an image')
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    path=folder/('clipboard-'+uuid.uuid4().hex[:12]+'.png')
    output=QSaveFile(str(path))
    if not output.open(QIODevice.WriteOnly):raise OSError(output.errorString())
    if not image.save(output,'PNG'):
        output.cancelWriting();raise OSError('Could not encode clipboard image')
    if not output.commit():raise OSError(output.errorString())
    return str(path)


def preview(row):
    """UI-only data. No file bytes or image URLs are sent to the agent."""
    path=row['path'];kind=row.get('kind') or ('image' if Path(path).suffix.lower() in IMAGE_SUFFIXES else 'file')
    local=not path.startswith('mount://')
    if local and os.path.isdir(path):kind='folder'
    from ui.file_icons import file_icon
    icon='folder' if kind=='folder' else 'image' if kind=='image' else file_icon(path)
    size=row.get('size',0)
    caption=(f'{size/1048576:.1f} MB' if size>=1048576 else f'{size/1024:.0f} KB' if size>=1024 else f'{size} B') if size else ''
    if row.get('label') and ' · line' in row['label']:
        caption = row['label'].split(' · ')[1]
    return {**row,'name':row.get('name') or Path(path).name or path,'kind':kind,'icon':'image://rak/'+icon,'sizeLabel':caption,
        'previewUrl':QUrl.fromLocalFile(path).toString() if kind=='image' and local and os.path.isfile(path) else ''}
