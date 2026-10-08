"""Independent validation: annotations never enter the production pipeline.

Annotation JSON: {"frames": [{"frame": 12, "meter": true,
"box": [x1,y1,x2,y2], "fill": 0.4, "shot_id": 1}, ...]}
Include *every* evaluated frame, including meter=false negatives. Omit unknown
boxes/fill; these are excluded from the corresponding metric.
"""
import argparse
import csv
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from meter_core import iou


def evaluate(rows, annotations, iou_threshold=.5):
    if not 0 < iou_threshold <= 1: raise ValueError("iou_threshold must be between 0 and 1")
    truth = {int(a['frame']): a for a in annotations}
    if len(truth) != len(annotations): raise ValueError("Duplicate annotation frame")
    preds = {int(r['frame']): r for r in rows}
    if len(preds) != len(rows): raise ValueError("Duplicate prediction frame")
    tp=fp=fn=tn=0
    overlaps=[]; fill_errors=[]; shot_frames={}
    for frame,a in truth.items():
        if frame not in preds: raise ValueError(f"Missing prediction for annotated frame {frame}")
        if not isinstance(a.get('meter'),bool): raise ValueError(f"Frame {frame} needs a boolean meter label")
        row=preds[frame]
        if row['status'] not in ('detected','predicted','absent'):
            raise ValueError(f"Invalid status at frame {frame}: {row['status']}")
        observed=row['status']=='detected'; present=a['meter']
        if a.get('fill') is not None and not 0 <= float(a['fill']) <= 1:
            raise ValueError(f"Invalid fill at frame {frame}")
        if a.get('box') is not None:
            b=a['box']
            if len(b)!=4 or b[2]<=b[0] or b[3]<=b[1]: raise ValueError(f"Invalid box at frame {frame}")
        if not present and (a.get('box') is not None or a.get('fill') is not None or a.get('shot_id') is not None):
            raise ValueError(f"Negative frame {frame} cannot have positive shot annotations")
        if observed and present: tp += 1
        elif observed: fp += 1
        elif present: fn += 1
        else: tn += 1
        if present and a.get('shot_id') is not None:
            shot_frames.setdefault(str(a['shot_id']),[]).append((frame,observed,row,a))
        if observed and present and a.get('box') is not None:
            box=[float(row[k]) for k in ('x1','y1','x2','y2')]
            overlaps.append(iou(tuple(box),tuple(a['box'])))
        if observed and present and a.get('fill') is not None and row.get('fill') not in ('',None):
            fill_errors.append(abs(float(row['fill'])-float(a['fill'])))
    per_shot=[]
    for shot,items in sorted(shot_frames.items()):
        items.sort(key=lambda x:x[0]); detected=[x for x in items if x[1]]
        if any(b[0] != a[0]+1 for a,b in zip(items,items[1:])):
            raise ValueError(f"Shot {shot} has unlabeled frames; annotate every frame through its final state")
        localized=[]
        for frame,observed,row,a in items:
            if observed and a.get('box') is not None:
                box=tuple(float(row[k]) for k in ('x1','y1','x2','y2'))
                if iou(box,tuple(a['box'])) >= iou_threshold:
                    localized.append(frame)
        final_truth=next((x for x in reversed(items) if x[3].get('fill') is not None),None)
        final_error=None
        if final_truth and detected:
            last=detected[-1][2]
            if last.get('fill') not in ('',None):
                final_error=abs(float(last['fill'])-float(final_truth[3]['fill']))
        per_shot.append(dict(shot_id=shot, annotated_frames=len(items), detected_frames=len(detected),
            coverage=len(detected)/len(items), onset_delay_frames=detected[0][0]-items[0][0] if detected else None,
            final_detection_gap_frames=items[-1][0]-detected[-1][0] if detected else None,
            localized_frames=len(localized), box_labeled_frames=sum(x[3].get('box') is not None for x in items),
            final_localized_gap_frames=items[-1][0]-localized[-1] if localized else None,
            final_fill_abs_error=final_error))
    return dict(annotated_frames=len(truth),tp=tp,fp=fp,fn=fn,tn=tn,
        precision=tp/(tp+fp) if tp+fp else None,
        recall=tp/(tp+fn) if tp+fn else None,
        box_iou_mean=sum(overlaps)/len(overlaps) if overlaps else None,
        box_iou_pass_rate=sum(v>=iou_threshold for v in overlaps)/len(overlaps) if overlaps else None,
        box_evaluated=len(overlaps),fill_mae=sum(fill_errors)/len(fill_errors) if fill_errors else None,
        fill_evaluated=len(fill_errors),shots=per_shot)


def main():
    p=argparse.ArgumentParser(description="Evaluate frame detections and full-shot coverage")
    p.add_argument('--frames',type=Path,required=True)
    p.add_argument('--annotations',type=Path,required=True)
    p.add_argument('--out',type=Path,default=Path('test_results/evaluation.json'))
    args=p.parse_args()
    with args.frames.open(newline='',encoding='utf-8') as f: rows=list(csv.DictReader(f))
    anns=json.loads(args.annotations.read_text(encoding='utf-8'))['frames']
    result=evaluate(rows,anns)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))


if __name__=='__main__': main()
