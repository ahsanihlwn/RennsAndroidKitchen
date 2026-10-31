# SPDX-License-Identifier: MPL-2.0
"""Device protocol and partition layout from the original Android Tools."""
import re

def _normalize_line(line):
    line = line.strip()
    if not line.startswith('(bootloader)'):
        return None
    rest = line[len('(bootloader)'):].strip()
    if ':' not in rest:
        return None
    idx = rest.rindex(':')
    return (rest[:idx].strip(), rest[idx + 1:].strip())

def _ep():
    return {'size_a': None, 'size_b': None, 'size': None, 'type': 'raw', 'has_slot': False, 'is_logical': False}

def _ss(name):
    if name.endswith('_a'):
        return (name[:-2], 'a')
    if name.endswith('_b'):
        return (name[:-2], 'b')
    return (name, None)

def parse_fastboot_output(raw):
    r = {'device': '', 'slot': None, 'has_slot': False, 'slot_count': 0, 'unlocked': False, 'secure': False, 'is_userspace': False, 'serialno': '', 'partitions': {}}
    p = r['partitions']
    for line in raw.splitlines():
        kv = _normalize_line(line)
        if not kv:
            continue
        k, v = kv
        if k == 'product':
            r['device'] = v
        elif k == 'current-slot':
            r['slot'] = v
            r['has_slot'] = True
        elif k == 'slot-count':
            try:
                r['slot_count'] = int(v)
            except:
                pass
        elif k == 'unlocked':
            r['unlocked'] = v.lower() == 'yes'
        elif k == 'secure':
            r['secure'] = v.lower() == 'yes'
        elif k == 'is-userspace':
            r['is_userspace'] = v.lower() == 'yes'
        elif k == 'serialno':
            r['serialno'] = v
        elif k.startswith('has-slot:'):
            pn = k[9:]
            p.setdefault(pn, _ep())['has_slot'] = v.lower() == 'yes'
        elif k.startswith('is-logical:'):
            pn, _ = _ss(k[11:])
            p.setdefault(pn, _ep())
            if v.lower() == 'yes':
                p[pn]['is_logical'] = True
        elif k.startswith('partition-size:'):
            pn, sl = _ss(k[15:])
            p.setdefault(pn, _ep())
            try:
                sz = int(v.strip(), 16)
            except:
                sz = 0
            if sl == 'a':
                p[pn]['size_a'] = sz
            elif sl == 'b':
                p[pn]['size_b'] = sz
            else:
                p[pn]['size'] = sz
        elif k.startswith('partition-type:'):
            pn, _ = _ss(k[15:])
            p.setdefault(pn, _ep())
            if v:
                p[pn]['type'] = v
    return r

def _fmt_size(n):
    if n is None:
        return '?'
    if n == 0:
        return 'dynamic'
    for u in ('B', 'KB', 'MB', 'GB'):
        if n < 1024:
            return f'{n:.0f} {u}'
        n /= 1024
    return f'{n:.1f} GB'

def _fmt_sz2(n):
    if n is None:
        return '?'
    if n == 0:
        return 'dynamic'
    v = float(n)
    for u in ('B', 'KB', 'MB', 'GB'):
        if v < 1024:
            return f'{v:.0f} {u}' if u == 'B' else f'{v:.2f} {u}'
        v /= 1024
    return f'{v:.2f} GB'

def _parse_size_arg(s: str) -> int:
    s = s.strip()
    if not s:
        return -1
    if s.startswith('0x') or s.startswith('0X'):
        try:
            return int(s, 16)
        except:
            return -1
    m = re.match('^([\\d.]+)\\s*(B|KB|MB|GB)?$', s, re.I)
    if not m:
        return -1
    try:
        val = float(m.group(1))
    except ValueError:
        return -1
    unit = (m.group(2) or 'B').upper()
    mult = {'B': 1, 'KB': 1024, 'MB': 1024 ** 2, 'GB': 1024 ** 3}.get(unit, 1)
    return int(val * mult)


def flow_layout(parsed):
    """Original 408px chart, including the 3px node glow margin."""
    parts = {k:v for k,v in parsed.get('partitions',{}).items() if v.get('is_logical')}
    pairs, singles, visited = [], [], set()
    for name in sorted(parts):
        if name in visited:continue
        base, slot = _ss(name)
        if slot and base+'_a' in parts and base+'_b' in parts:
            pairs.append((base+'_a',base+'_b'));visited.update(pairs[-1])
        else:singles.append(name);visited.add(name)
    nodes, edges, y = [], [], 76
    for group in [*pairs,*[(name,) for name in singles]]:
        for i,name in enumerate(group):
            cx = (73 if i==0 else 335) if len(group)==2 else 204
            info=parts[name]
            active=len(group)==2 and name.endswith('_'+(parsed.get('slot') or 'a'))
            if len(group)==2:
                slot='a' if i==0 else 'b'
                size=info.get('size_'+slot+'_fmt') or (_fmt_sz2(info['size_'+slot]) if info.get('size_'+slot) is not None else '')
            else:
                size=(info.get('size_fmt') or info.get('size_a_fmt') or info.get('size_b_fmt') or
                      (_fmt_sz2(info['size']) if info.get('size') is not None else
                       _fmt_sz2(info['size_a']) if info.get('size_a') is not None else ''))
            nodes.append({'key':name,'name':name,'x':cx-56,'y':y-3,'centerY':y+22,
                          'active':active,'size':size,'state':'idle','image':''})
            if len(group)==2:edges.append({'x':cx-53 if i==0 else cx+53,'y':y+22,'active':active})
        y+=62
    return {'nodes':nodes,'edges':edges,'width':408,'height':y+20,'spineBottom':max(58,y-9)}
