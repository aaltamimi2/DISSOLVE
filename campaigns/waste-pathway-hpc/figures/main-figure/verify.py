#!/usr/bin/env python3
"""Compare a fresh output with the frozen v12 target; exit nonzero on data mismatch."""
import sys
sys.dont_write_bytecode=True
import argparse, json, hashlib, re
from pathlib import Path
from collections import Counter
import numpy as np
import pandas as pd
from PIL import Image
from lxml import etree as ET

def digest(p): return hashlib.sha256(p.read_bytes()).hexdigest()

def svg_content(path):
    root=ET.parse(str(path)).getroot()
    text=Counter(''.join(n.itertext()) for n in root.iter() if n.tag.endswith('}text'))
    # Ignore grouping, generated identifiers, comments and creation timestamps.
    # Compare each graphical primitive's attributes and text. Preserve transforms,
    # coordinates, path strings and styles; renamed ID references are normalized.
    geometry=[]
    tags={'path','rect','circle','ellipse','line','polyline','polygon','text','use','svg'}
    for n in root.iter():
        if not isinstance(n.tag,str) or ET.QName(n).localname not in tags: continue
        attrs={k:re.sub(r'url\(#[^)]+\)','url(#ID)',v) for k,v in n.attrib.items() if k!='id'}
        attrs={k:('#ID' if k.endswith('href') and v.startswith('#') else v) for k,v in attrs.items()}
        geometry.append((ET.QName(n).localname,attrs,n.text if n.tag.endswith('}text') else None))
    return text,geometry

def main():
    a=argparse.ArgumentParser(description=__doc__);a.add_argument('--out',type=Path,required=True)
    args=a.parse_args();out=args.out.resolve();ref=Path(__file__).resolve().parent/'reference'
    report={'data':{},'reference':'Frozen hpc-pathway-main-v12','notes':[]}
    for p in sorted(ref.glob('*.data.csv')):
        q=out/p.name
        try:
            left=pd.read_csv(p,float_precision='round_trip');right=pd.read_csv(q,float_precision='round_trip')
            pd.testing.assert_frame_equal(left,right,check_exact=True)
            report['data'][p.name]={'exact_values':True,'byte_identical':digest(p)==digest(q),'rows':len(left)}
        except Exception as e:
            report['data'][p.name]={'exact_values':False,'error':str(e)}
    stem='hpc-pathway-main'
    try:
        lt,lg=svg_content(ref/(stem+'.svg'));rt,rg=svg_content(out/(stem+'.svg'))
        report['svg']={'text_content_equal':lt==rt,'normalized_primitive_geometry_equal':lg==rg,
                       'reference_primitives':len(lg),'output_primitives':len(rg),
                       'text_removed':list((lt-rt).elements()),'text_added':list((rt-lt).elements())}
        # Detailed primitive differences let the recipient locate exactly what changed.
        diffs=[{'primitive':i,'reference':x,'output':y} for i,(x,y) in enumerate(zip(lg,rg)) if x!=y]
        (out/'svg-geometry-differences.json').write_text(json.dumps(diffs,indent=2,ensure_ascii=False)+'\n')
        report['svg']['different_paired_primitives']=len(diffs)
        l=np.asarray(Image.open(ref/(stem+'.png')).convert('RGBA'),dtype=np.int16)
        r=np.asarray(Image.open(out/(stem+'.png')).convert('RGBA'),dtype=np.int16)
        if l.shape!=r.shape:
            report['png']={'pixel_identical':False,'reference_shape':list(l.shape),'output_shape':list(r.shape)}
        else:
            delta=np.abs(l-r);mask=(delta!=0).any(axis=2)
            report['png']={'pixel_identical':not bool(mask.any()),'different_pixels':int(mask.sum()),
                           'total_pixels':int(mask.size),'different_pixel_fraction':float(mask.mean()),
                           'max_channel_difference':int(delta.max()),'mean_absolute_channel_difference':float(delta.mean())}
            diff=np.zeros_like(l,dtype=np.uint8);diff[:,:,:3]=np.minimum(delta[:,:,:3]*4,255);diff[:,:,3]=255
            Image.fromarray(diff).save(out/'pixel-difference.png')
        report['captions']={suffix:(ref/(stem+'.'+suffix)).read_text().strip()==(out/(stem+'.'+suffix)).read_text().strip()
                            for suffix in ['caption.txt','manuscript-caption.txt']}
    except Exception as e:
        report['render_error']=str(e)
    report['data_pass']=all(x['exact_values'] for x in report['data'].values())
    report['notes'].append('SVG primitive comparison ignores metadata, generated IDs and grouping. PNG comparison includes all pixels, so different grouping/clip transforms are not silently treated as visual equivalence. PDFs can differ in creation metadata. See README for known rendering differences.')
    (out/'verification.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps(report,indent=2,ensure_ascii=False))
    return 0 if report['data_pass'] and 'render_error' not in report else 1

if __name__=='__main__':sys.exit(main())
