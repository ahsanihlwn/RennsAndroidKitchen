# SPDX-License-Identifier: MPL-2.0
"""Literal paths advertised by native tools; never execute or scan script bodies."""
import os
import shlex

_FIELDS={'path','file_path','filepath','absolutepath','targetfile','filename','directorypath','searchdirectory'}


def extract(inputs,*,cwd='',locations=()):
    inputs=inputs if isinstance(inputs,dict) else {}
    paths=[value for key,value in inputs.items() if key.lower() in _FIELDS and isinstance(value,str) and value]
    paths.extend(location['path'] for location in (locations or ()) if isinstance(location,dict) and isinstance(location.get('path'),str))
    command=inputs.get('command') or inputs.get('CommandLine') or ''
    if isinstance(command,str) and not any(char in command for char in ('\n','\r',';','|','&','`')):
        try:tokens=[token.strip('\"\'') for token in shlex.split(command,posix=False)]
        except ValueError:tokens=[]
        lower=[token.lower() for token in tokens]
        if lower[:3] in (['rak','ui','read'],['rak','fs','cat'],['rak','fs','read']):
            if len(tokens)>3 and not tokens[3].startswith('-'):paths.append(tokens[3])
        elif lower and lower[0] in ('get-content','get-childitem','cat','type'):
            flag=next((i for i,token in enumerate(lower) if token in ('-literalpath','-path')),None)
            if flag is not None and flag+1<len(tokens):paths.append(tokens[flag+1])
            elif len(tokens)>1 and not tokens[1].startswith('-'):paths.append(tokens[1])
    root=inputs.get('cwd') or inputs.get('Cwd') or inputs.get('WorkingDirectory') or cwd
    if not isinstance(root,str):root=cwd
    result=[]
    for path in paths:
        if any(char in path for char in ('\n','\r','$','`','*','?')):continue
        if path.startswith('mount://'):value=path
        elif '://' in path:continue
        else:value=os.path.normpath(path if os.path.isabs(path) else os.path.join(root,path)) if root else path
        if value not in result:result.append(value)
    return result
