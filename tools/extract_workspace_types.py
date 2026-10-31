# SPDX-License-Identifier: MPL-2.0
"""Copy the reference FileTree's literal extension catalogue without importing UI."""
import ast
from pathlib import Path
from pprint import pformat


def main():
    root=Path(__file__).resolve().parents[1]
    source=root.parent/'rennsandroidkitchen'/'main_ext'/'class_FileTree.py'
    tree=ast.parse(source.read_text(encoding='utf-8-sig'))
    cls=next(node for node in tree.body if isinstance(node,ast.ClassDef) and node.name=='FileTree')
    names=('TEXT_EXT','AUDIO_EXT','VIDEO_EXT','IMAGE_EXT','ARCHIVE_EXT')
    data={node.targets[0].id:ast.literal_eval(node.value) for node in cls.body
          if isinstance(node,ast.Assign) and isinstance(node.targets[0],ast.Name) and node.targets[0].id in names}
    assert set(data)==set(names)
    content='# SPDX-License-Identifier: MPL-2.0\n'
    content+='"""File-kind catalogue from the original FileTree, independent of widgets."""\n\n'
    content+='\n\n'.join(name+' = '+pformat(tuple(data[name]),width=105,compact=True) for name in names)+'\n'
    (root/'core'/'file_types.py').write_text(content,encoding='utf-8')
    print({name:len(data[name]) for name in names})


if __name__=='__main__':main()
