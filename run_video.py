"""Run a YOLO meter detector, temporal tracker, and cautious fill estimator."""
import argparse
import csv
import json
from pathlib import Path

from meter_core import Config, Detection, MeterTracker


def estimate_fill(frame, box, top_fraction=.04, bottom_fraction=.90):
    """Return a provisional white-fill fraction, or None when pixels are ambiguous.

    This color heuristic requires validation on each meter style and HDR setting.
    It deliberately rejects unanchored bright patches instead of inventing a fill.
    """
    import cv2
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = [round(v) for v in box]
    x1, x2 = max(0, x1), min(w, x2)
    y1, y2 = max(0, y1), min(h, y2)
    if x2-x1 < 5 or y2-y1 < 12:
        return None
    if not 0 <= top_fraction < bottom_fraction <= 1:
        raise ValueError('Invalid fill calibration fractions')
    crop = frame[y1:y2, x1:x2]
    left, right = int(crop.shape[1]*.28), max(int(crop.shape[1]*.72), int(crop.shape[1]*.28)+1)
    hsv = cv2.cvtColor(crop[:, left:right], cv2.COLOR_BGR2HSV)
    white = ((hsv[:,:,1] <= 75) & (hsv[:,:,2] >= 165)) | ((hsv[:,:,1] <= 55) & (hsv[:,:,2] >= 135))
    good = white.mean(axis=1) >= .35
    runs, start = [], None
    for i, yes in enumerate(good):
        if yes and start is None: start = i
        if start is not None and (not yes or i == len(good)-1):
            end = i if yes else i-1
            runs.append((start,end))
            start = None
    if not runs: return None
    top, bottom = max(runs, key=lambda pair: pair[1]-pair[0])
    height = y2-y1
    if bottom-top+1 < max(3, int(.02*height)) or bottom < int(.75*height):
        return None
    # The detector box includes a pointed top and bottom; normalize the white
    # column to the interior track. Fractions are calibrated on the provided
    # meter style and should be validated again after changing UI settings.
    return max(0.0,min(1.0,(bottom_fraction-top/height)/(bottom_fraction-top_fraction)))


def main():
    p = argparse.ArgumentParser(description="Frame-by-frame NBA 2K shot-meter analysis")
    p.add_argument("--model", type=Path, required=True, help="Trained best.pt")
    p.add_argument("--video", type=Path, required=True)
    p.add_argument("--out", type=Path, default=Path("test_results/meter_pipeline"))
    p.add_argument("--imgsz", type=int, default=1280)
    p.add_argument("--conf", type=float, default=.25)
    p.add_argument("--max-missed", type=int, default=2)
    p.add_argument("--max-frames", type=int, default=0)
    p.add_argument("--fill-top", type=float, default=.04, help="Interior track top as fraction of meter box")
    p.add_argument("--fill-bottom", type=float, default=.90, help="Interior track bottom as fraction of meter box")
    args = p.parse_args()
    for path in (args.model, args.video):
        if not path.is_file(): p.error(f"Missing input file: {path}")
    if not 0 < args.conf < 1 or args.imgsz <= 0 or args.max_missed < 0 or args.max_frames < 0:
        p.error("Require 0 < conf < 1, imgsz > 0, max-missed >= 0, max-frames >= 0")
    if not 0 <= args.fill_top < args.fill_bottom <= 1:
        p.error('Require 0 <= fill-top < fill-bottom <= 1')
    try:
        import cv2
        import torch
        from ultralytics import YOLO
    except ImportError as e:
        p.error(f"Install requirements.txt first: {e}")
    cap = cv2.VideoCapture(str(args.video))
    if not cap.isOpened(): p.error(f"Cannot open video: {args.video}")
    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps <= 0: p.error("Video FPS unavailable; remux the video first")
    expected_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    width, height = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if width <= 0 or height <= 0: p.error("Invalid video dimensions")
    model = YOLO(str(args.model))
    device = 0 if torch.cuda.is_available() else "cpu"
    tracker = MeterTracker(Config(max_missed=args.max_missed))
    args.out.mkdir(parents=True, exist_ok=True)
    shots = []
    count = 0
    last_timestamp_ms = None
    timestamp_regressions = 0
    timestamp_duplicates = 0
    early_eof = False
    try:
        with (args.out/"frames.csv").open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["frame","time_sec","shot_id","status","confidence",
                    "x1","y1","x2","y2","fill","stop_candidate"])
            writer.writeheader()
            while True:
                if args.max_frames and count >= args.max_frames: break
                ok, frame = cap.read()
                if not ok:
                    early_eof = bool(expected_frames and count < expected_frames)
                    break
                if frame is None or frame.shape != (height, width, 3):
                    raise RuntimeError(f"Invalid decoded frame shape at frame {count}")
                timestamp_ms = float(cap.get(cv2.CAP_PROP_POS_MSEC))
                if last_timestamp_ms is not None:
                    if timestamp_ms < last_timestamp_ms: timestamp_regressions += 1
                    if timestamp_ms == last_timestamp_ms: timestamp_duplicates += 1
                last_timestamp_ms = timestamp_ms
                result = model.predict(frame, imgsz=args.imgsz, conf=args.conf, device=device, verbose=False)[0]
                detections = [Detection(tuple(float(v) for v in box), float(conf)) for box, conf in zip(
                    result.boxes.xyxy.cpu().tolist(), result.boxes.conf.cpu().tolist())] if result.boxes is not None else []
                state = tracker.update(count, detections, lambda box: estimate_fill(
                    frame, box, args.fill_top, args.fill_bottom))
                if state["ended"]: shots.append(state["ended"])
                box = state["box"] or (None,)*4
                writer.writerow(dict(frame=count,time_sec=round(timestamp_ms/1000,6),shot_id=state["shot_id"],
                    status=state["status"],confidence=state["confidence"],
                    x1=box[0],y1=box[1],x2=box[2],y2=box[3],
                    fill=state["fill"],stop_candidate=state["stop_candidate"]))
                count += 1
    finally:
        cap.release()
    if tracker.active: shots.append(tracker.summary())
    complete = (not early_eof and not timestamp_regressions and not timestamp_duplicates
                and (expected_frames == count if expected_frames else not args.max_frames))
    (args.out/"shots.json").write_text(json.dumps(shots,indent=2),encoding="utf-8")
    (args.out/"run.json").write_text(json.dumps(dict(video=str(args.video),model=str(args.model),
            frames=count,metadata_frames=expected_frames,fps=fps,imgsz=args.imgsz,conf=args.conf,
            device=str(device),early_eof=early_eof,timestamp_regressions=timestamp_regressions,
            duplicate_timestamps=timestamp_duplicates,complete=complete,
            fill_top=args.fill_top,fill_bottom=args.fill_bottom),indent=2),encoding="utf-8")
    if early_eof or timestamp_regressions or timestamp_duplicates:
        raise RuntimeError("Capture failed integrity checks; see run.json and discard partial results")
    print(f"Processed {count} frames; {len(shots)} candidate shots. Outputs: {args.out}")


if __name__ == "__main__": main()
