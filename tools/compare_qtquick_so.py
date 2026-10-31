# SPDX-License-Identifier: MPL-2.0
"""Compare stable ELF viewer glyphs and execute original text-building handlers."""
import ast
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace


def main():
    repo=Path(__file__).resolve().parents[1];sys.path.insert(0,str(repo))
    import numpy as np
    from PIL import Image
    from core import so_core,so_document,ui_lang
    from tools.audit_so_viewer import fixture_bytes
    ui_lang.init()
    source=repo.parent/'rennsandroidkitchen/main_ext/class_SoViewer.py'
    tree=ast.parse(source.read_text(encoding='utf-8'))
    original=next(node for node in tree.body if isinstance(node,ast.ClassDef) and node.name=='SoViewer')
    functions=[node for node in original.body if isinstance(node,ast.FunctionDef) and node.name in ('_refresh_overview','_refresh_string_list')]
    namespace={'t':ui_lang.t,'so_core':so_core}
    exec(compile(ast.Module(body=functions,type_ignores=[]),str(source),'exec'),namespace)
    class Text:
        def setPlainText(self,value):self.value=value
    with tempfile.TemporaryDirectory(prefix='rak-so-text-') as temporary:
        path=Path(temporary)/'fixture.so';path.write_bytes(fixture_bytes());result=so_core.analyze_file(str(path))
        for query in ('','libnative','STACK_CHK','does not exist'):
            text=Text();owner=SimpleNamespace(result=result,overview_text=text,string_list=text,string_filter=SimpleNamespace(text=lambda:query))
            namespace['_refresh_overview'](owner);assert text.value==so_document.overview(result)
            namespace['_refresh_string_list'](owner);assert text.value==so_document.strings_text(result,query)
    artifacts=repo/'build/qtquick_migration'
    # These contain stable text. Temporary paths and filter-specific/dialog
    # content are excluded; this is not a whole-window pixel-parity claim.
    regions={'symbols':(48,121,300,146),'paths':(53,121,650,371),'sections':(48,115,330,146),'segments':(48,115,330,146),'relocations':(48,121,300,146),'security':(24,82,270,100),'strings':(18,92,300,114)}
    errors={}
    for name,box in regions.items():
        original=np.asarray(Image.open(artifacts/'original_so'/(name+'.png')).convert('RGB').crop(box)).astype(float)
        native=np.asarray(Image.open(artifacts/'quick_so'/(name+'.png')).convert('RGB').crop(box)).astype(float)
        errors[name]=round(float(abs(original-native).mean()),6)
    assert max(errors.values())<.5,errors
    report={'result':'passed','originalTextHandlers':True,'meanGlyphCropError255':errors,'maximumAllowed':.5}
    (artifacts/'quick_so/reference_report.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report))


if __name__=='__main__':main()
