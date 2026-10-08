"""Decode an entire capture without running the detector; fail on truncation."""
import argparse
import json
from pathlib import Path


def inspect(video):
    import cv2
    cap=cv2.VideoCapture(str(video))
    if not cap.isOpened(): raise RuntimeError(f'Cannot open capture: {video}')
    expected=int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps=float(cap.get(cv2.CAP_PROP_FPS))
    width=int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height=int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if fps<=0 or width<=0 or height<=0:
        cap.release(); raise RuntimeError('Invalid capture metadata')
    count=0; bad_shapes=[]; time_regressions=[]; duplicate_times=0; irregular_intervals=0
    prior_ms=None; first_ms=last_ms=None
    try:
        while True:
            ok,frame=cap.read()
            if not ok: break
            if frame is None or frame.shape != (height,width,3): bad_shapes.append(count)
            ms=float(cap.get(cv2.CAP_PROP_POS_MSEC))
            if first_ms is None: first_ms=ms
            if prior_ms is not None:
                if ms < prior_ms: time_regressions.append(count)
                elif ms == prior_ms: duplicate_times+=1
                elif not .5*(1000/fps) <= ms-prior_ms <= 1.5*(1000/fps): irregular_intervals+=1
            prior_ms=last_ms=ms
            count+=1
    finally:
        cap.release()
    result=dict(video=str(video),fps=fps,width=width,height=height,
                metadata_frames=expected,decoded_frames=count,bad_shape_frames=bad_shapes,
                timestamp_regression_frames=time_regressions,duplicate_timestamps=duplicate_times,
                irregular_timestamp_intervals=irregular_intervals,
                first_timestamp_ms=first_ms,last_timestamp_ms=last_ms,
                complete=(count>0 and (expected==0 or count==expected) and not bad_shapes and not time_regressions))
    return result


def main():
    p=argparse.ArgumentParser(description='Full-file OpenCV capture integrity check')
    p.add_argument('--video',type=Path,required=True)
    p.add_argument('--out',type=Path,default=Path('test_results/capture_check.json'))
    args=p.parse_args()
    if not args.video.is_file(): p.error(f'Missing video: {args.video}')
    try: report=inspect(args.video)
    except ImportError as e: p.error(f'Install requirements.txt first: {e}')
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))
    if not report['complete']: raise SystemExit(1)


if __name__=='__main__':main()
