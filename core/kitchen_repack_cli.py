# SPDX-License-Identifier: MPL-2.0
"""Parse explicit rebuild output without assuming it is an archive."""
import argparse
import os
from core.cli_arguments import tokens


class Parser(argparse.ArgumentParser):
    def error(self,message):raise ValueError('repack: '+message)


def parse(rest,cwd):
    parser=Parser(add_help=False,allow_abbrev=False)
    parser.add_argument('path')
    parser.add_argument('-o','--to',dest='output',required=True)
    parser.add_argument('--json',action='store_true')
    options=vars(parser.parse_args(tokens('repack',rest)))
    for key in ('path','output'):options[key]=os.path.abspath(os.path.join(cwd,options[key]))
    return options
