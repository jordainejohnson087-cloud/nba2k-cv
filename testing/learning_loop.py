"""Triage live tests, then fine-tune only from explicitly reviewed labels.

No detector prediction is treated as truth. Validation uses a separate capture.
"""
import argparse
import csv
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def select_review_frames(rows, limit=120):
    """Rank observations likely to reveal detection errors; include no-meter frames."""
    priorities = {}
    def flag(index, priority, reason):
        if 0 <= index < len(rows):
            frame = int(rows[index]['frame'])
            old = priorities.get(frame)
            if old is None or priority > old[0]:
                priorities[frame] = (priority,reason)
    for i,row in enumerate(rows):
        status = row['status']
        previous = rows[i-1]['status'] if i else None
        following = rows[i+1]['status'] if i+1 < len(rows) else None
        if status != previous or status != following:
            for offset in (-2,-1,0,1,2): flag(i+offset,90,'detection transition')
        if status == 'predicted': flag(i,100,'detector gap during a track')
        if status == 'detected' and row.get('confidence') not in ('',None):
            if float(row['confidence']) < .45: flag(i,80,'low confidence')
        if status == 'detected' and i and previous == 'detected':
            old = rows[i-1]
            if row.get('fill') not in ('',None) and old.get('fill') not in ('',None):
                if abs(float(row['fill'])-float(old['fill'])) > .2:
                    flag(i,85,'large fill change')
        if status == 'absent' and i % 90 == 0: flag(i,30,'sampled no-meter frame')
    chosen = sorted(priorities, key=lambda f:(-priorities[f][0],f))[:limit]
    return [dict(frame=f,reason=priorities[f][1],priority=priorities[f][0])
            for f in sorted(chosen)]


def review_run(run_dir, limit=120, extract_raw=True):
    """Extract original frames; proposals are review hints, never training labels."""
    run_dir = Path(run_dir)
    with (run_dir/'live_frames.csv').open(newline='',encoding='utf-8') as file:
        rows = list(csv.DictReader(file))
    if any(int(r['frame']) != i for i,r in enumerate(rows)):
        raise ValueError('Frame log must have consecutive indices starting at zero')
    selected = select_review_frames(rows,limit)
    targets = {item['frame']:item for item in selected}
    for i,item in targets.items():
        row=rows[i]
        item['proposal_status']=row['status']
        item['proposal_box']=[float(row[k]) for k in ('x1','y1','x2','y2')] if row['status']=='detected' else None
    raw=run_dir/'raw_capture.mp4'
    if extract_raw and raw.is_file():
        import cv2
        cap = cv2.VideoCapture(str(raw))
        if not cap.isOpened(): raise RuntimeError('Cannot read raw_capture.mp4')
        count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if count and count != len(rows):
            cap.release()
            raise ValueError(f'Raw capture has {count} frames; CSV has {len(rows)}')
        frame_dir = run_dir/'review'/'frames'
        frame_dir.mkdir(parents=True,exist_ok=True)
        try:
            for i in range(len(rows)):
                ok, frame = cap.read()
                if not ok: raise RuntimeError(f'Raw capture ended at frame {i}')
                if i in targets:
                    path=frame_dir/f'frame_{i:06d}.jpg'
                    if not cv2.imwrite(str(path),frame):
                        raise RuntimeError(f'Could not save {path}')
                    targets[i]['image']=str(path.relative_to(run_dir))
        finally:
            cap.release()
    manifest=dict(raw_video='raw_capture.mp4' if extract_raw and raw.is_file() else None,observed_frames=len(rows),
                  selected=len(selected),items=selected,
                  instruction='Review original frames. Predictions are proposals only. Label every frame of a separate held-out validation capture before accepting a new weight.')
    path=run_dir/'review'/'queue.json'
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    return str(path)


def load_labels(run_dir, complete=False):
    run_dir=Path(run_dir)
    labels=json.loads((run_dir/'reviewed_labels.json').read_text(encoding='utf-8'))
    with (run_dir/'live_frames.csv').open(newline='',encoding='utf-8') as file:
        total=sum(1 for _ in csv.DictReader(file))
    anns=labels['frames']
    indices=[a['frame'] for a in anns]
    if not all(type(f) is int and 0 <= f < total for f in indices) or len(set(indices)) != len(indices):
        raise ValueError(f'Invalid or duplicate frame indices in {run_dir}')
    if complete and set(indices) != set(range(total)):
        raise ValueError(f'Validation run {run_dir} must label every captured frame, including negatives')
    for a in anns:
        if a.get('reviewed') is not True or type(a.get('meter')) is not bool:
            raise ValueError(f'Frame {a["frame"]} requires reviewed=true and boolean meter')
        b=a.get('box')
        if a['meter'] and (not isinstance(b,list) or len(b)!=4 or
                           not all(isinstance(v,(int,float)) for v in b) or
                           b[2]<=b[0] or b[3]<=b[1]):
            raise ValueError(f'Positive frame {a["frame"]} needs a corrected box')
        if not a['meter'] and b is not None:
            raise ValueError(f'Negative frame {a["frame"]} cannot have a box')
        if complete and a['meter'] and a.get('shot_id') is None:
            raise ValueError(f'Positive validation frame {a["frame"]} needs shot_id')
        if complete and a.get('fill') is not None and not 0 <= float(a['fill']) <= 1:
            raise ValueError(f'Invalid fill for frame {a["frame"]}')
    if complete:
        shots={}
        for a in anns:
            if a['meter']: shots.setdefault(str(a['shot_id']),[]).append(a)
        for shot,items in shots.items():
            items.sort(key=lambda a:a['frame'])
            if any(b['frame'] != a['frame']+1 for a,b in zip(items,items[1:])):
                raise ValueError(f'Shot {shot} needs every consecutive frame labeled')
            if items[-1].get('fill') is None:
                raise ValueError(f'Shot {shot} needs its final fill labeled')
    return anns,total


def build_dataset(train_runs, val_run, test_run, out):
    train_runs=[Path(p).resolve() for p in train_runs]
    val_run=Path(val_run).resolve()
    test_run=Path(test_run).resolve()
    if (len(set(train_runs+[val_run,test_run])) != len(train_runs)+2 or not train_runs):
        raise ValueError('Training, validation, and held-out test must be different captures')
    import cv2
    out=Path(out).resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError('Dataset output must be empty; keep prior experiments separate')
    totals={}
    for split,runs in (('train',train_runs),('val',[val_run]),('test',[test_run])):
        positives=negatives=0
        (out/'images'/split).mkdir(parents=True,exist_ok=True)
        (out/'labels'/split).mkdir(parents=True,exist_ok=True)
        for run_index,run in enumerate(runs):
            anns,total=load_labels(run,complete=split!='train')
            wanted={a['frame']:a for a in anns}
            cap=cv2.VideoCapture(str(run/'raw_capture.mp4'))
            if not cap.isOpened(): raise RuntimeError(f'Cannot read raw capture in {run}')
            try:
                for i in range(total):
                    ok,frame=cap.read()
                    if not ok: raise RuntimeError(f'Raw capture in {run} ended at frame {i}')
                    if i not in wanted: continue
                    a=wanted[i]; h,w=frame.shape[:2]
                    label=''
                    if a['meter']:
                        x1,y1,x2,y2=a['box']
                        if not (0 <= x1 < x2 <= w and 0 <= y1 < y2 <= h):
                            raise ValueError(f'Box outside frame {i} in {run}')
                        label=f'0 {(x1+x2)/(2*w):.8f} {(y1+y2)/(2*h):.8f} {(x2-x1)/w:.8f} {(y2-y1)/h:.8f}\n'
                        positives+=1
                    else: negatives+=1
                    stem=f'run{run_index:03d}_frame{i:06d}'
                    if not cv2.imwrite(str(out/'images'/split/f'{stem}.jpg'),frame):
                        raise RuntimeError(f'Could not export frame {i} from {run}')
                    (out/'labels'/split/f'{stem}.txt').write_text(label,encoding='utf-8')
            finally:
                cap.release()
        totals[split]=dict(positive=positives,negative=negatives)
    if (min(totals['train'].values()) < 1 or
            any(totals[s]['positive'] < 5 or totals[s]['negative'] < 10 for s in ('val','test'))):
        raise ValueError('Need train positives/negatives and at least 5 positives and 10 negatives in each evaluation split')
    data=out/'data.yaml'
    data.write_text(f'path: {out.as_posix()}\ntrain: images/train\nval: images/val\ntest: images/test\nnames:\n  0: meter\n',encoding='utf-8')
    (out/'manifest.json').write_text(json.dumps(dict(train_runs=[str(p) for p in train_runs],
        validation_run=str(val_run),test_run=str(test_run),counts=totals),indent=2),encoding='utf-8')
    return data


def train_and_compare(base_model, data, out, epochs=20, imgsz=1280):
    from ultralytics import YOLO
    out=Path(out).resolve()
    base=YOLO(str(base_model))
    trained=base.train(data=str(data),epochs=epochs,imgsz=imgsz,project=str(out),
                       name='candidate',exist_ok=True)
    candidate=out/'candidate'/'weights'/'best.pt'
    if not candidate.is_file(): raise RuntimeError('Training finished without best.pt')
    def score(model,name):
        metrics=YOLO(str(model)).val(data=str(data),split='test',imgsz=imgsz,
                                     project=str(out),name=name,exist_ok=True)
        return dict(map50=float(metrics.box.map50),precision=float(metrics.box.mp),
                    recall=float(metrics.box.mr))
    before=score(base_model,'baseline_validation')
    after=score(candidate,'candidate_validation')
    manifest=json.loads((Path(data).parent/'manifest.json').read_text(encoding='utf-8'))
    test_run=Path(manifest['test_run'])
    from testing.evaluate import evaluate
    def track_score(model,name):
        run_out=out/name
        subprocess.run([sys.executable,str(Path(__file__).resolve().parents[1]/'run_video.py'),
            '--model',str(model),'--video',str(test_run/'raw_capture.mp4'),
            '--out',str(run_out),'--imgsz',str(imgsz)],check=True)
        with (run_out/'frames.csv').open(newline='',encoding='utf-8') as f:
            rows=list(csv.DictReader(f))
        labels,_=load_labels(test_run,complete=True)
        return evaluate(rows,labels)
    before_tracking=track_score(base_model,'baseline_tracking')
    after_tracking=track_score(candidate,'candidate_tracking')
    def temporal_ok(a,b):
        if not a['shots'] or len(a['shots']) != len(b['shots']): return False
        for old,new in zip(a['shots'],b['shots']):
            if (old['shot_id'] != new['shot_id'] or
                new['coverage'] == 0 or new['final_localized_gap_frames'] is None or
                new['coverage'] < old['coverage'] or
                (new['onset_delay_frames'] if new['onset_delay_frames'] is not None else float('inf')) >
                (old['onset_delay_frames'] if old['onset_delay_frames'] is not None else float('inf')) or
                (new['final_localized_gap_frames'] if new['final_localized_gap_frames'] is not None else float('inf')) >
                (old['final_localized_gap_frames'] if old['final_localized_gap_frames'] is not None else float('inf'))):
                return False
        return True
    accepted=(after['map50'] >= before['map50']+.01 and
              after['precision'] >= before['precision'] and
              after['recall'] >= before['recall'] and
              after_tracking['precision'] is not None and before_tracking['precision'] is not None and
              after_tracking['precision'] >= before_tracking['precision'] and
              after_tracking['recall'] is not None and before_tracking['recall'] is not None and
              after_tracking['recall'] >= before_tracking['recall'] and
              after_tracking['box_iou_pass_rate'] is not None and before_tracking['box_iou_pass_rate'] is not None and
              after_tracking['box_iou_pass_rate'] >= before_tracking['box_iou_pass_rate'] and
              temporal_ok(before_tracking,after_tracking) and
              before_tracking['fill_mae'] is not None and after_tracking['fill_mae'] is not None and
              after_tracking['fill_mae'] <= before_tracking['fill_mae'])
    improved=out/'improved_best.pt'
    if accepted: shutil.copy2(candidate,improved)
    report=dict(base=str(base_model),candidate=str(candidate),baseline=before,
                candidate_metrics=after,baseline_tracking=before_tracking,
                candidate_tracking=after_tracking,accepted=accepted,
                improved_weight=str(improved) if accepted else None,
                note='Accepted only if held-out detection, per-shot tracking, and measured fill do not regress.')
    (out/'comparison.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report


def main():
    p=argparse.ArgumentParser(description='Review live failures and improve weights from corrected labels')
    sub=p.add_subparsers(dest='action',required=True)
    review=sub.add_parser('review'); review.add_argument('--run',type=Path,required=True)
    review.add_argument('--limit',type=int,default=120)
    train=sub.add_parser('train'); train.add_argument('--train-run',type=Path,action='append',required=True)
    train.add_argument('--validation-run',type=Path,required=True)
    train.add_argument('--test-run',type=Path,required=True)
    train.add_argument('--model',type=Path,required=True)
    train.add_argument('--out',type=Path,required=True)
    train.add_argument('--epochs',type=int,default=20); train.add_argument('--imgsz',type=int,default=1280)
    args=p.parse_args()
    if args.action=='review':
        if args.limit <= 0: p.error('limit must be positive')
        print(review_run(args.run,args.limit))
    else:
        if args.epochs <= 0 or args.imgsz <= 0 or not args.model.is_file():
            p.error('Invalid training settings or missing model')
        data=build_dataset(args.train_run,args.validation_run,args.test_run,args.out/'dataset')
        print(json.dumps(train_and_compare(args.model,data,args.out,args.epochs,args.imgsz),indent=2))


if __name__=='__main__': main()
