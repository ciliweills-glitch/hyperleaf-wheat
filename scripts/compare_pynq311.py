"""Compare all ten samples against actual AX7020 PhiPS2, independently of hardware."""
import argparse,csv,json,math
from pathlib import Path
IDS=['00001','00002','00014','00022','00034','00047','00048','00059','00064','00070']

def load(path,mode=None):
    result={}
    with Path(path).open(encoding='utf-8-sig',newline='') as source:
        for row in csv.DictReader(source):
            if mode and 'mode' in row and row['mode']!=mode: continue
            text=row['ImageId'].strip()
            if not text.isdigit(): raise ValueError('Invalid ImageId: '+text)
            ident=text.zfill(5);value=float(row['PhiPS2'])
            if ident in result: raise ValueError('Duplicate sample: '+ident)
            if not math.isfinite(value): raise ValueError('Non-finite PhiPS2: '+ident)
            result[ident]=value
    if set(result)!=set(IDS): raise ValueError('Sample set mismatch: missing=%s extra=%s'%(sorted(set(IDS)-set(result)),sorted(set(result)-set(IDS))))
    return result

def compare(reference,prediction,output,mode='raw',tolerance=1e-6):
    if not math.isfinite(tolerance) or tolerance<0: raise ValueError('Invalid tolerance')
    expected,actual=load(reference),load(prediction,mode)
    rows=[dict(ImageId=i,mode=mode,ax7020_PhiPS2=expected[i],PhiPS2=actual[i],
               signed_diff=actual[i]-expected[i],abs_diff=abs(actual[i]-expected[i]),
               exact=actual[i]==expected[i]) for i in IDS]
    summary=dict(mode=mode,reference_kind='AX7020 actual board PhiPS2',count=10,
                 max_abs_diff=max(r['abs_diff'] for r in rows),
                 mean_abs_diff=sum(r['abs_diff'] for r in rows)/10,
                 exact_predictions=sum(r['exact'] for r in rows),tolerance=tolerance,
                 pass_test=all(r['abs_diff']<=tolerance for r in rows),hardware_accessed=False)
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    with (output/'differences.csv').open('w',encoding='utf-8',newline='') as target:
        writer=csv.DictWriter(target,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    (output/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    return summary

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference',type=Path,default=Path(__file__).resolve().parents[1]/'data/reference/ax7020_10.csv')
    parser.add_argument('--prediction',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--mode',choices=('raw','int16'),default='raw')
    parser.add_argument('--tolerance',type=float,default=1e-6)
    args=parser.parse_args();report=compare(args.reference,args.prediction,args.output,args.mode,args.tolerance)
    print(json.dumps(report,ensure_ascii=False));raise SystemExit(0 if report['pass_test'] else 1)
