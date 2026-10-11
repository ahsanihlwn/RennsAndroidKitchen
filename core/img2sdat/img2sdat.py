#!/usr/bin/env python
# -*- coding: utf-8 -*-
#====================================================
#          FILE: img2sdat.py
#       AUTHORS: xpirt - luxi78 - howellzhu
#          DATE: 2018-05-25 12:19:12 CEST
#  ADAPTED FOR Python 3.14 (packaged, non-interactive)
#====================================================

import os
import tempfile

from . import blockimgdiff, sparse_img

__version__ = '1.7'

# Default transfer.list version 4 = Android 7.0+ (Nougat/Oreo/Pie/Q/R/S/T+).
# Caller boleh override via VERSION param.
DEFAULT_VERSION = 4


def main(INPUT_IMAGE, OUTDIR='.', VERSION=None, PREFIX='system'):
    """
    Convert a sparse image (INPUT_IMAGE) into block-OTA artifacts:
      <OUTDIR>/<PREFIX>.new.dat
      <OUTDIR>/<PREFIX>.transfer.list
      <OUTDIR>/<PREFIX>.patch.dat
    Caller is responsible for ensuring INPUT_IMAGE is in sparse format.
    """
    print('img2sdat binary - version: %s' % __version__)

    if not os.path.isdir(OUTDIR):
        os.makedirs(OUTDIR)

    OUTDIR_PREFIX = os.path.join(OUTDIR, PREFIX)

    if VERSION is None:
        VERSION = DEFAULT_VERSION

    image = sparse_img.SparseImage(INPUT_IMAGE, tempfile.mkstemp()[1], '0')
    b = blockimgdiff.BlockImageDiff(image, None, VERSION)
    b.Compute(OUTDIR_PREFIX)

    print('Done! Output files: %s' % OUTDIR)
    return OUTDIR_PREFIX


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Convert sparse image to block-OTA new.dat/transfer.list/patch.dat')
    parser.add_argument('image', help='input sparse system image')
    parser.add_argument('-o', '--outdir', default='.', help='output directory (default: cwd)')
    parser.add_argument('-v', '--version', type=int, default=None,
                        help='transfer.list version (1..4). Default: 4 (Android 7+).')
    parser.add_argument('-p', '--prefix', default='system', help='output basename (e.g. "system" -> system.new.dat)')

    args = parser.parse_args()
    main(args.image, args.outdir, args.version, args.prefix)
