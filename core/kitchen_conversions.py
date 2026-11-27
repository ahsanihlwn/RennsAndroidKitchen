# SPDX-License-Identifier: MPL-2.0
"""Original DAT/Brotli conversion chains, shared by native menu and CLI."""
import os
import tempfile
from core.command_context import OperationError


def checked(result):
    if isinstance(result,tuple):
        if not result[0]:raise OperationError(str(result[1]))
        return result[1]
    return result


def repack_targets(path):
    suffix=next((suffix for suffix in ('.new.dat.br','.new.dat') if path.lower().endswith(suffix)),None)
    return [path,path[:-len(suffix)]+'.transfer.list',path[:-len(suffix)]+'.patch.dat'] if suffix else [path]


def publish_repack(actual,requested):
    sources=repack_targets(actual);targets=repack_targets(requested)
    if len(sources)!=len(targets):raise ValueError('Repack output format does not match its requested destination')
    if not os.path.isfile(actual):raise OperationError('Repack produced no output: '+actual)
    if len(sources)>1 and not os.path.isfile(sources[1]):raise OperationError('DAT output has no transfer.list: '+sources[1])
    moves=[(source,target) for source,target in zip(sources,targets) if os.path.isfile(source)]
    for source,target in moves:
        if os.path.normcase(source)!=os.path.normcase(target) and os.path.lexists(target):raise FileExistsError(target)
    completed=[]
    try:
        for source,target in moves:
            if os.path.normcase(source)==os.path.normcase(target):continue
            os.rename(source,target);completed.append((source,target))
    except Exception:
        for source,target in reversed(completed):os.rename(target,source)
        raise
    return [target for _,target in moves]


def image_to_dat(path,*,brotli=False):
    from core import native_backend
    from core.threader_control import report_progress
    folder=os.path.dirname(path);prefix=os.path.splitext(os.path.basename(path))[0]
    report_progress(25,'Converting to .new.dat')
    native_backend.img2sdat(path, folder, version=4, prefix=prefix)
    output=os.path.join(folder,prefix+'.new.dat')
    if brotli:
        from core.brotli_tool import compress_to_br
        report_progress(65,'Compressing Brotli (.dat.br)')
        output=checked(compress_to_br(output,keep_source=False))
    report_progress(100,'Done')
    return output


def unpack_image(path):
    from core.header_magic import check_magic
    kind=check_magic(path)
    if kind=='EROFS':
        from core.erofs import unpack_erofs
        return checked(unpack_erofs(path,os.path.dirname(path)))
    if kind in ('EXT2','EXT3','EXT4'):
        from core.unpack_ext4 import ext4_x
        return checked(ext4_x(path,os.path.dirname(path)))
    raise ValueError('Unknown filesystem in image: '+(kind or 'unrecognized'))


def dat_to_image(path,*,folder=False):
    from core import native_backend
    from core.threader_control import report_progress
    compressed=path.lower().endswith('.new.dat.br')
    suffix='.new.dat.br' if compressed else '.new.dat'
    if not path.lower().endswith(suffix):raise ValueError('Expected a .new.dat or .new.dat.br file')
    prefix=path[:-len(suffix)];transfer=prefix+'.transfer.list';output=prefix+'.img'
    if not os.path.isfile(transfer):raise FileNotFoundError('transfer.list not found: '+transfer)
    dat=path
    if compressed:
        from core.brotli_tool import decompress_from_br
        report_progress(10,'Decompressing .dat.br')
        dat=checked(decompress_from_br(path,keep_source=True))
    report_progress(35 if not folder else 25,'Reconstructing .img from .new.dat')
    native_backend.sdat2img(transfer, dat, output)
    if compressed:os.remove(dat)
    if folder:
        report_progress(50,'Unpacking reconstructed image')
        result=unpack_image(output);os.remove(output);return result
    report_progress(100,'Done')
    return output


def brotli_to_image(path,*,folder=False):
    from core.brotli_tool import decompress_from_br
    from core.threader_control import report_progress
    report_progress(15,'Decompressing Brotli image')
    output=checked(decompress_from_br(path,keep_source=True))
    if folder:
        report_progress(45,'Unpacking image')
        result=unpack_image(output);os.remove(output);return result
    report_progress(100,'Done')
    return output
