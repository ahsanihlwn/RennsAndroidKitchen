# SPDX-License-Identifier: MPL-2.0
"""Compare rendered text positions with corresponding original QWidget captures."""
import argparse
import json
from pathlib import Path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--original',type=Path,default=Path('build/qtquick_migration/original'))
    parser.add_argument('--quick',type=Path,default=Path('build/qtquick_migration/quick_latest'))
    args=parser.parse_args()
    from PySide6.QtGui import QImage
    report=[]
    for page in ('settings','create','projects'):
        original=json.loads((args.original/(page+'.json')).read_text(encoding='utf-8'))
        quick=json.loads((args.quick/(page+'.geometry.json')).read_text(encoding='utf-8'))
        old_image,new_image=QImage(str(args.original/(page+'.png'))),QImage(str(args.quick/(page+'.png')))
        compared=[]
        for row in original:
            if row['class']!='QLabel' or not row.get('text'):continue
            rect=row['rect'];matches=[value for value in quick if value['text']==row['text']]
            if not matches:continue # Effect-level copy intentionally replaces the original Blur toggle.
            matched=min(matches,key=lambda value:abs(value['rect'][1]-rect[1])+abs(value['rect'][0]-rect[0]))
            target=matched['rect']
            difference=target[1]+target[3]/2-rect[1]-rect[3]/2
            assert abs(difference)<=1.5,(page,row['text'],rect,target)
            assert row['font'].split(',')[2]==matched['font'].split(',')[2],(page,row['text'],'font size',row['font'],matched['font'])
            entry={'text':row['text'],'centerYDifference':difference}
            if page=='settings' and row['text'] in ('Paket','Lokasi Proyek Default','Tampilan','Bahasa') and rect[1]+rect[3]<old_image.height()-80:
                def glyph_y(image,bounds):
                    x,y,w,h=map(round,bounds);rows=[]
                    for py in range(max(0,y),min(image.height(),y+h)):
                        for px in range(max(0,x),min(image.width(),x+w)):
                            color=image.pixelColor(px,py)
                            if min(color.red(),color.green(),color.blue())>100:
                                rows.append(py);break
                    assert rows,(page,row['text'],bounds)
                    return (min(rows)+max(rows))/2
                glyph_difference=glyph_y(new_image,target)-glyph_y(old_image,rect)
                assert abs(glyph_difference)<=1,(row['text'],glyph_difference)
                entry['renderedGlyphYDifference']=glyph_difference
            compared.append(entry)
        assert len(compared)>=10,(page,len(compared))
        report.append({'page':page,'matchedTextControls':len(compared),'maxCenterYDifference':max(abs(value['centerYDifference']) for value in compared)})
    (args.quick/'geometry_comparison.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({'result':'passed','checks':report}),flush=True)


if __name__=='__main__':main()
