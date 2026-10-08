"""Diagnostic replay of a detector CSV; no video/model or fill estimation."""
import argparse
import csv
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from meter_core import Detection, MeterTracker, Config


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--csv',type=Path,required=True)
    p.add_argument('--out',type=Path,default=Path('test_results/replay'))
    p.add_argument('--max-missed',type=int,default=2)
    args=p.parse_args()
    tracker=MeterTracker(Config(max_missed=args.max_missed))
    counts={'detected':0,'predicted':0,'absent':0}; shots=[]
    args.out.mkdir(parents=True,exist_ok=True)
    with args.csv.open(newline='',encoding='utf-8') as source, (args.out/'frames.csv').open('w',newline='',encoding='utf-8') as target:
        writer=csv.DictWriter(target,fieldnames=['frame','time_sec','shot_id','status','confidence','x1','y1','x2','y2','fill','stop_candidate'])
        writer.writeheader()
        last=-1
        for row in csv.DictReader(source):
            frame=int(row['frame'])
            if frame!=last+1: raise ValueError(f'Nonsequential frame {frame} after {last}')
            last=frame
            boxes=[Detection(tuple(float(row[k]) for k in ('x1','y1','x2','y2')),float(row['confidence']))] if int(row['detected']) else []
            state=tracker.update(frame,boxes)
            counts[state['status']]+=1
            if state['ended']: shots.append(state['ended'])
            box=state['box'] or (None,)*4
            writer.writerow(dict(frame=frame,time_sec=row['time_sec'],shot_id=state['shot_id'],status=state['status'],
                confidence=state['confidence'],x1=box[0],y1=box[1],x2=box[2],y2=box[3],fill='',stop_candidate=''))
    if tracker.active: shots.append(tracker.summary())
    result=dict(input_frames=last+1,counts=counts,candidate_shots=len(shots),shots=shots,
                caveat='Candidate shot counts include any false detections; no ground truth or fill measurement.')
    (args.out/'replay_summary.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='shots'},indent=2))


if __name__=='__main__': main()
