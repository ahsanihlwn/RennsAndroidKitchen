# SPDX-License-Identifier: MPL-2.0
"""Compare popup frames, baselines and surface colors with the original UI."""
import argparse
import json
from pathlib import Path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--original',type=Path,default=Path('build/qtquick_migration/original_exposed'))
    parser.add_argument('--quick',type=Path,default=Path('build/qtquick_migration/quick_latest'))
    parser.add_argument('--legacy-card-colors',action='store_true',help='Check the superseded inside-card darkening baseline')
    args=parser.parse_args()
    from PySide6.QtGui import QImage
    checked=[]
    for state in ('create_popup','create_popup_typed','create_popup_dual'):
        original=json.loads((args.original/(state+'.json')).read_text(encoding='utf-8'))
        quick=json.loads((args.quick/(state+'.geometry.json')).read_text(encoding='utf-8'))
        labels=0
        for row in original:
            if row['class']!='QLabel' or row['rect'][1]>420:
                continue
            options=[item for item in quick if item['text']==row['text']]
            assert options,(state,row['text'],'missing')
            old=row['rect']
            new=min(options,key=lambda item:abs(item['rect'][1]-old[1])+abs(item['rect'][0]-old[0]))
            bounds=new['rect']
            error=bounds[1]+bounds[3]/2-old[1]-old[3]/2
            assert abs(error)<=.5,(state,row['text'],'text center',error)
            assert row['font'].split(',')[2:5]==new['font'].split(',')[2:5],(state,row['text'],'font')
            labels+=1
        for row in (item for item in original if item['class']=='QLineEdit'):
            match=min((item for item in quick if item['class']=='Field'),key=lambda item:abs(item['rect'][1]-row['rect'][1]))
            assert row['rect']==match['rect'],(state,'textbox frame',row['rect'],match['rect'])
        checked.append({'state':state,'labelCentersAndFonts':labels})

    def glyph_rows(image,state):
        threshold=190 if state.endswith('typed') else 70
        return [y for y in range(133,163)
                if sum(image.pixelColor(x,y).red()>threshold for x in range(64,145))>2]
    baselines=[]
    for state in ('create_popup','create_popup_typed'):
        old=QImage(str(args.original/(state+'.screen.png')))
        new=QImage(str(args.quick/(state+'.png')))
        assert not old.isNull() and not new.isNull()
        # Exclude the original blinking cursor; compare actual rendered glyphs.
        a,b=glyph_rows(old,state),glyph_rows(new,state)
        assert a and a==b,(state,'textbox glyph rows',a,b)
        baselines.append({'state':state,'glyphRows':[min(b),max(b)],'difference':0})

    old=QImage(str(args.original/'create_popup.screen.png'))
    new=QImage(str(args.quick/'create_popup.png'))
    samples=[(200,30),(300,50),(25,100),(200,105),(400,105),(200,190),
             (30,300),(200,310),(400,310),(25,460),(130,495)]
    channel_error=max(abs(a-b) for xy in samples
                      for a,b in zip(old.pixelColor(*xy).getRgb()[:3],new.pixelColor(*xy).getRgb()[:3]))
    if args.legacy_card_colors:assert channel_error<=2,('legacy popup surface colors',channel_error)
    # The user moved the black layer outside the glass card. Its old darkened
    # color samples are historical; the dedicated backdrop probe checks the
    # current material/placement, while source layout and glyphs stay exact.
    report={'result':'passed','checks':checked,'textboxBaselines':baselines,'legacyCardColorDelta':channel_error,'legacyCardColorsRequired':args.legacy_card_colors}
    (args.quick/'popup_comparison.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report),flush=True)


if __name__=='__main__':main()
