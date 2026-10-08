"""Live preview and diagnostics for a capture card, camera, or stream.

The source is read once per iteration. A slow detector may cause the device to
drop frames before OpenCV returns them; the timing report makes this visible.
"""
import argparse
import csv
import json
import statistics
import time
from pathlib import Path

from meter_core import Config, Detection, MeterTracker
from run_video import estimate_fill


def open_capture(cv2, source, windows_dshow=False):
    index = int(source) if source.isdecimal() else source
    if isinstance(index, int) and windows_dshow:
        return cv2.VideoCapture(index, cv2.CAP_DSHOW)
    return cv2.VideoCapture(index)


def configure_capture(cv2, cap, args):
    """Request a camera mode; returned metadata is the backend's actual mode."""
    if args.fourcc:
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*args.fourcc))
    for name, prop in (('width',cv2.CAP_PROP_FRAME_WIDTH),
                       ('height',cv2.CAP_PROP_FRAME_HEIGHT),
                       ('capture_fps',cv2.CAP_PROP_FPS)):
        value = getattr(args,name,0)
        if value:
            cap.set(prop,value)
    return dict(width=int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                height=int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                fps=float(cap.get(cv2.CAP_PROP_FPS)))


def probe_capture(args, cv2, clock=time.perf_counter):
    """Measure capture delivery without loading the model or rendering UI."""
    cap = open_capture(cv2,str(args.source),args.dshow)
    if not cap.isOpened():
        raise RuntimeError(f'Cannot open capture source {args.source}')
    try:
        reported = configure_capture(cv2,cap,args)
        cap.set(cv2.CAP_PROP_BUFFERSIZE,1)
        stamps, shapes = [], set()
        started = clock()
        while clock()-started < args.seconds:
            ok, frame = cap.read()
            now = clock()
            if not ok or frame is None:
                break
            stamps.append(now)
            shapes.add((int(frame.shape[1]),int(frame.shape[0])))
        elapsed = clock()-started
    finally:
        cap.release()
    intervals = [(b-a)*1000 for a,b in zip(stamps,stamps[1:])]
    result = dict(requested=dict(width=args.width,height=args.height,fps=args.capture_fps,
                                 fourcc=args.fourcc),reported=reported,
                  decoded_sizes=[list(s) for s in sorted(shapes)],frames=len(stamps),
                  elapsed_sec=elapsed,read_fps=len(stamps)/elapsed if elapsed else 0,
                  median_read_interval_ms=statistics.median(intervals) if intervals else None,
                  p95_read_interval_ms=(sorted(intervals)[int(.95*(len(intervals)-1))]
                                        if intervals else None),
                  note='Read FPS measures OpenCV delivery without inference; it does not prove unique frames or monitor refresh.')
    args.out.mkdir(parents=True,exist_ok=True)
    (args.out/'capture_probe.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))
    return result


def draw_preview(cv2, frame, state, frame_no, processing_ms, budget_ms):
    output = frame.copy()
    status = state['status']
    color = (80, 220, 80) if status == 'detected' else (0, 170, 255)
    if state['box'] is not None:
        x1,y1,x2,y2 = (round(v) for v in state['box'])
        cv2.rectangle(output, (x1,y1), (x2,y2), color, 3)
    label = f"Frame {frame_no}  {status.upper()}  {processing_ms:.1f} ms / {budget_ms:.1f} ms"
    if state['fill'] is not None:
        label += f"  fill {state['fill']*100:.0f}%"
    cv2.rectangle(output, (0,0), (min(output.shape[1],max(760,len(label)*12)),48), (20,20,20), -1)
    cv2.putText(output, label, (12,32), cv2.FONT_HERSHEY_SIMPLEX, .65, color, 2)
    return output


class WallClockRecorder:
    """Record at real elapsed time, holding the last analyzed frame during gaps."""
    def __init__(self, cv2, path, size, fps):
        self.fps = min(30.0, fps)
        self.writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'mp4v'),
                                      self.fps, size)
        if not self.writer.isOpened():
            raise RuntimeError('Could not start live preview recording')
        self.last = None
        self.frames = 0

    def write(self, frame, elapsed):
        # Slot zero shows the first analyzed frame. Subsequent slots hold the
        # previous result until a newer analyzed frame is available.
        due = int(elapsed * self.fps) + 1
        if self.last is None:
            self.last = frame
        while self.frames < due - 1:
            self.writer.write(self.last)
            self.frames += 1
        if self.frames < due:
            self.writer.write(frame)
            self.frames += 1
        self.last = frame

    def close(self, elapsed):
        if self.last is not None:
            while self.frames < int(elapsed * self.fps):
                self.writer.write(self.last)
                self.frames += 1
        self.writer.release()


def run(args, cv2, torch, YOLO):
    source = str(args.source)
    cap = open_capture(cv2, source, args.dshow)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open capture source {source}; check its device index and that another app is not using it")
    mode = configure_capture(cv2,cap,args)
    # Some backends ignore buffer size; timing diagnostics remain authoritative.
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    fps = mode['fps']
    if not 1 <= fps <= 240:
        fps = args.assumed_fps
    budget_ms = 1000/fps
    model = YOLO(str(args.model))
    device = 0 if torch.cuda.is_available() else 'cpu'
    tracker = MeterTracker(Config(max_missed=args.max_missed))
    args.out.mkdir(parents=True, exist_ok=True)
    writer_video = None
    raw_writer = None
    shots = []
    count = 0
    frame_size = None
    over_budget = 0
    stage_times = {'read_ms':[],'raw_write_ms':[],'overlay_ms':[],'preview_write_ms':[]}
    inference_over_budget = 0
    late_reads = 0
    missing_reads = 0
    reason = 'stopped'
    start = time.perf_counter()
    last_read = None
    csv_path = args.out/'live_frames.csv'
    try:
        with csv_path.open('w',newline='',encoding='utf-8') as file:
            fieldnames = ['frame','elapsed_sec','shot_id','status','confidence','x1','y1','x2','y2',
                          'fill','stop_candidate','read_interval_ms','read_ms','raw_write_ms',
                          'inference_ms','overlay_ms','preview_write_ms','loop_ms']
            log = csv.DictWriter(file,fieldnames=fieldnames)
            log.writeheader()
            while True:
                if args.max_frames and count >= args.max_frames:
                    reason = 'frame_limit'; break
                if args.seconds and time.perf_counter()-start >= args.seconds:
                    reason = 'time_limit'; break
                read_start = time.perf_counter()
                ok, frame = cap.read()
                read_at = time.perf_counter()
                read_ms = (read_at-read_start)*1000
                stage_times['read_ms'].append(read_ms)
                if not ok or frame is None:
                    missing_reads += 1
                    reason = 'source_ended_or_disconnected'; break
                if frame.ndim != 3 or frame.shape[2] != 3:
                    raise RuntimeError(f'Unexpected capture frame shape at frame {count}: {frame.shape}')
                if frame_size is None:
                    frame_size = [int(frame.shape[1]),int(frame.shape[0])]
                elif frame_size != [int(frame.shape[1]),int(frame.shape[0])]:
                    raise RuntimeError(f'Capture dimensions changed at frame {count}: {frame.shape}')
                raw_start = time.perf_counter()
                if args.record_raw:
                    if raw_writer is None:
                        raw_writer = cv2.VideoWriter(str(args.out/'raw_capture.mp4'),
                            cv2.VideoWriter_fourcc(*'mp4v'),fps,tuple(frame_size))
                        if not raw_writer.isOpened():
                            raise RuntimeError('Could not start raw capture recording')
                    raw_writer.write(frame)
                raw_write_ms = (time.perf_counter()-raw_start)*1000 if args.record_raw else 0.0
                stage_times['raw_write_ms'].append(raw_write_ms)
                interval_ms = None if last_read is None else (read_at-last_read)*1000
                last_read = read_at
                if interval_ms is not None and interval_ms > 1.5*budget_ms:
                    late_reads += 1
                inference_start = time.perf_counter()
                result = model.predict(frame,imgsz=args.imgsz,conf=args.conf,device=device,verbose=False)[0]
                inference_ms = (time.perf_counter()-inference_start)*1000
                if inference_ms > budget_ms:
                    inference_over_budget += 1
                detections = [Detection(tuple(float(v) for v in box),float(conf)) for box,conf in zip(
                    result.boxes.xyxy.cpu().tolist(),result.boxes.conf.cpu().tolist())] if result.boxes is not None else []
                state = tracker.update(count,detections,lambda box:estimate_fill(frame,box,args.fill_top,args.fill_bottom))
                if state['ended']:
                    shots.append(state['ended'])
                stop_requested = False
                overlay_ms = preview_write_ms = 0.0
                if not args.headless or args.record:
                    overlay_start = time.perf_counter()
                    overlay = draw_preview(cv2,frame,state,count,inference_ms,budget_ms)
                    overlay_ms = (time.perf_counter()-overlay_start)*1000
                    if args.record:
                        preview_start = time.perf_counter()
                        if writer_video is None:
                            h,w = overlay.shape[:2]
                            writer_video = WallClockRecorder(cv2,args.out/'live_preview.mp4',(w,h),fps)
                        writer_video.write(overlay,read_at-start)
                        preview_write_ms = (time.perf_counter()-preview_start)*1000
                    if not args.headless:
                        cv2.imshow('NBA 2K meter detection (Q to stop)',overlay)
                        if cv2.waitKey(1) & 0xFF == ord('q'):
                            stop_requested = True
                stage_times['overlay_ms'].append(overlay_ms)
                stage_times['preview_write_ms'].append(preview_write_ms)
                loop_ms = (time.perf_counter()-read_at)*1000
                if loop_ms > budget_ms:
                    over_budget += 1
                box = state['box'] or (None,)*4
                log.writerow(dict(frame=count,elapsed_sec=round(read_at-start,6),shot_id=state['shot_id'],
                    status=state['status'],confidence=state['confidence'],
                    x1=box[0],y1=box[1],x2=box[2],y2=box[3],fill=state['fill'],
                    stop_candidate=state['stop_candidate'],read_interval_ms=interval_ms,
                    read_ms=read_ms,raw_write_ms=raw_write_ms,inference_ms=inference_ms,
                    overlay_ms=overlay_ms,preview_write_ms=preview_write_ms,loop_ms=loop_ms))
                count += 1
                if count % 60 == 0:
                    file.flush()
                if stop_requested:
                    reason = 'user_stopped'; break
    finally:
        cap.release()
        if writer_video is not None:
            writer_video.close(time.perf_counter()-start)
        if raw_writer is not None:
            raw_writer.release()
        if not args.headless:
            cv2.destroyAllWindows()
    if tracker.active:
        shots.append(tracker.summary())
    elapsed = time.perf_counter()-start
    report = dict(source=source,model=str(args.model),device=str(device),frames=count,
        elapsed_sec=elapsed,measured_fps=count/elapsed if elapsed else 0,
        source_fps=fps,budget_ms=budget_ms,processing_over_budget_frames=over_budget,
        requested_capture=dict(width=args.width,height=args.height,fps=args.capture_fps,
                               fourcc=args.fourcc),reported_capture=mode,decoded_frame_size=frame_size,
        inference_over_budget_frames=inference_over_budget,
        late_read_intervals=late_reads,read_failures=missing_reads,stop_reason=reason,
        candidate_shots=len(shots),max_missed=args.max_missed,
        median_stage_ms={k:statistics.median(v) if v else None for k,v in stage_times.items()},
        recording_fps=writer_video.fps if writer_video is not None else None,
        recording_frames=writer_video.frames if writer_video is not None else None,
        raw_capture_frames=count if raw_writer is not None else None,
        realtime_target_met=over_budget == 0 and late_reads == 0,
        shots_full_at_first_detection=sum(s['first_full_frame'] == s['start_frame']
                                          for s in shots if s['first_full_frame'] is not None),
        performance_warning=('CPU inference could not keep up with the source; test CUDA or a smaller '
                             'imgsz and validate detection quality before relying on live tracking.'
                             if device == 'cpu' and over_budget else None),
        note='Late reads suggest missed capture deadlines; they do not prove an exact count of device-dropped frames.')
    (args.out/'live_run.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    (args.out/'live_shots.json').write_text(json.dumps(shots,indent=2),encoding='utf-8')
    if count:
        from testing.learning_loop import review_run
        report['review_queue'] = review_run(args.out,extract_raw=args.record_raw)
        (args.out/'live_run.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))
    if reason == 'source_ended_or_disconnected' and source.isdecimal():
        raise RuntimeError('Live capture stopped unexpectedly; inspect live_run.json')
    return report


def main():
    p = argparse.ArgumentParser(description='Preview and measure live NBA 2K meter detection')
    p.add_argument('--model',type=Path,help='Trained model; not needed for --probe-only')
    p.add_argument('--probe-only',action='store_true',help='Measure capture speed without loading AI')
    p.add_argument('--source',default='0',help='Capture card/camera index (0, 1, ...), stream URL, or test video')
    p.add_argument('--out',type=Path,default=Path('test_results/live'))
    p.add_argument('--imgsz',type=int,default=1280)
    p.add_argument('--conf',type=float,default=.25)
    p.add_argument('--max-missed',type=int,default=4,
                   help='Bridge short detector gaps; predicted frames have no fill (default: 4)')
    p.add_argument('--assumed-fps',type=float,default=60)
    p.add_argument('--width',type=int,default=0,help='Request capture width (device may refuse)')
    p.add_argument('--height',type=int,default=0,help='Request capture height (device may refuse)')
    p.add_argument('--capture-fps',type=float,default=0,help='Request device capture FPS')
    p.add_argument('--fourcc',default='',help='Request four-character device format, e.g. MJPG')
    p.add_argument('--fill-top',type=float,default=.04)
    p.add_argument('--fill-bottom',type=float,default=.90)
    p.add_argument('--seconds',type=float,default=0)
    p.add_argument('--max-frames',type=int,default=0)
    p.add_argument('--headless',action='store_true',help='Log results without a preview window')
    p.add_argument('--record',action='store_true',help='Save an annotated MP4')
    p.add_argument('--record-raw',action='store_true',help='Save unannotated observed frames and an automatic review queue for learning')
    p.add_argument('--dshow',action='store_true',help='Use DirectShow for a Windows camera index')
    args = p.parse_args()
    if not args.probe_only and (args.model is None or not args.model.is_file()):
        p.error(f'Model not found: {args.model}')
    if not 0 < args.conf < 1 or args.imgsz <= 0 or args.max_missed < 0 or args.max_frames < 0 or args.seconds < 0:
        p.error('Invalid detection or duration settings')
    if not 0 <= args.fill_top < args.fill_bottom <= 1 or args.assumed_fps <= 0:
        p.error('Invalid fill calibration or assumed FPS')
    if args.width < 0 or args.height < 0 or args.capture_fps < 0 or args.capture_fps > 240 or (args.fourcc and len(args.fourcc) != 4):
        p.error('Invalid capture mode')
    if args.probe_only and args.seconds <= 0:
        p.error('--probe-only requires --seconds greater than zero')
    try:
        import cv2
    except ImportError as e:
        p.error(f'Install requirements.txt first: {e}')
    if args.probe_only:
        probe_capture(args,cv2)
        return
    try:
        import torch
        from ultralytics import YOLO
    except ImportError as e:
        p.error(f'Install requirements.txt first: {e}')
    run(args,cv2,torch,YOLO)


if __name__ == '__main__': main()
