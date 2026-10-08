"""Stateful, model-independent shot-meter tracking.

Coordinates are in source-video pixels. Predicted boxes never count as detections.
"""
from dataclasses import dataclass
from math import hypot, isfinite
from typing import Optional

Box = tuple[float, float, float, float]


def iou(a: Box, b: Box) -> float:
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    area_a = max(0.0, a[2]-a[0]) * max(0.0, a[3]-a[1])
    area_b = max(0.0, b[2]-b[0]) * max(0.0, b[3]-b[1])
    return intersection / (area_a + area_b - intersection) if area_a + area_b > intersection else 0.0


def center(b: Box) -> tuple[float, float]:
    return ((b[0]+b[2])/2, (b[1]+b[3])/2)


@dataclass(frozen=True)
class Detection:
    box: Box
    confidence: float


@dataclass
class Config:
    max_missed: int = 2
    box_alpha: float = 0.45
    fill_alpha: float = 0.4
    min_iou: float = 0.15
    max_center_shift: float = 1.0  # multiples of prior box diagonal
    max_size_ratio: float = 2.0
    full_threshold: float = 0.98
    stop_window: int = 6
    stop_delta: float = 0.0075
    min_stop_progress: float = 0.1


class MeterTracker:
    def __init__(self, config: Config | None = None):
        self.cfg = config or Config()
        if (self.cfg.max_missed < 0 or not 0 < self.cfg.box_alpha <= 1
                or not 0 < self.cfg.fill_alpha <= 1 or self.cfg.stop_window < 2
                or self.cfg.max_center_shift <= 0 or self.cfg.max_size_ratio < 1):
            raise ValueError("Invalid tracking configuration")
        self.shot_id = 0
        self.active = False
        self.last_box: Optional[Box] = None
        self.raw_box: Optional[Box] = None
        self.velocity: Box = (0, 0, 0, 0)
        self.missed = 0
        self.fill: Optional[float] = None
        self.history: list[float] = []
        self.peak_fill = 0.0
        self.start_frame: Optional[int] = None
        self.last_observed_frame: Optional[int] = None
        self.last_observed_fill: Optional[float] = None
        self.last_fill_frame: Optional[int] = None
        self.stop_candidate = False
        self.last_stop_candidate_frame: Optional[int] = None
        self.full_frame: Optional[int] = None
        self.previous_frame: Optional[int] = None

    def _new_shot(self, frame: int) -> None:
        self.shot_id += 1
        self.active = True
        self.last_box = self.raw_box = None
        self.velocity = (0, 0, 0, 0)
        self.missed = 0
        self.fill = None
        self.history = []
        self.peak_fill = 0.0
        self.start_frame = frame
        self.last_observed_frame = None
        self.last_observed_fill = None
        self.last_fill_frame = None
        self.stop_candidate = False
        self.last_stop_candidate_frame = None
        self.full_frame = None

    def _matches(self, box: Box) -> bool:
        if self.last_box is None:
            return True
        old = self.last_box
        distance = hypot(*(a-b for a,b in zip(center(box), center(old))))
        diagonal = hypot(old[2]-old[0], old[3]-old[1])
        old_area = (old[2]-old[0])*(old[3]-old[1])
        area = (box[2]-box[0])*(box[3]-box[1])
        ratio = max(area/old_area, old_area/area) if old_area > 0 and area > 0 else float('inf')
        return (ratio <= self.cfg.max_size_ratio and
                (iou(box, old) >= self.cfg.min_iou or distance <= self.cfg.max_center_shift * diagonal))

    def update(self, frame: int, detections: list[Detection], fill_reader=None) -> dict:
        """Consume one sequential frame. fill_reader(box) returns 0..1 or None.

        The reader is invoked only on a real detector box; predictions are never
        allowed to create fill evidence or a final measurement.
        """
        if self.previous_frame is not None and frame != self.previous_frame + 1:
            raise ValueError("Frames must be sequential with no skipped indices")
        self.previous_frame = frame
        candidates = [d for d in detections if len(d.box) == 4 and
                      all(isfinite(v) for v in d.box) and isfinite(d.confidence) and
                      0 <= d.confidence <= 1 and d.box[2] > d.box[0] and d.box[3] > d.box[1]]
        if self.active and self.last_box is not None:
            candidates = [d for d in candidates if self._matches(d.box)]
            # Prefer a consistent track over an unrelated, higher-confidence box.
            chosen = max(candidates, key=lambda d: (.65*iou(d.box,self.last_box)+.35*d.confidence)) if candidates else None
        else:
            chosen = max(candidates, key=lambda d: d.confidence) if candidates else None
        ended = None
        if not self.active and chosen:
            self._new_shot(frame)
        if self.active and chosen is None:
            self.missed += 1
            self.history = []  # a plateau must be supported by contiguous observations
            self.stop_candidate = False
            if self.missed > self.cfg.max_missed:
                ended = self.summary()
                self.active = False
                self.last_box = None
                return dict(frame=frame, shot_id=None, status="absent", box=None,
                            confidence=None, fill=None, stop_candidate=False, ended=ended)
            predicted = tuple(a + v for a,v in zip(self.last_box, self.velocity))
            self.last_box = predicted
            return dict(frame=frame, shot_id=self.shot_id, status="predicted", box=predicted,
                        confidence=None, fill=None, stop_candidate=False, ended=None)
        if chosen is None:
            return dict(frame=frame, shot_id=None, status="absent", box=None,
                        confidence=None, fill=None, stop_candidate=False, ended=None)
        raw = chosen.box
        old = self.last_box
        if old is not None:
            smooth = tuple(self.cfg.box_alpha * a + (1-self.cfg.box_alpha) * b for a,b in zip(raw,old))
            # Velocity is per frame, so normalize after any missed frames.
            elapsed = self.missed + 1
            self.velocity = tuple((a-b)/elapsed for a,b in zip(raw,self.raw_box)) if self.raw_box else (0,0,0,0)
        else:
            smooth = raw
        self.last_box, self.raw_box, self.missed = smooth, raw, 0
        self.last_observed_frame = frame
        raw_fill = fill_reader(raw) if fill_reader else None
        if raw_fill is not None and 0 <= raw_fill <= 1:
            self.fill = raw_fill if self.fill is None else self.cfg.fill_alpha*raw_fill + (1-self.cfg.fill_alpha)*self.fill
            self.last_observed_fill = self.fill
            self.last_fill_frame = frame
            self.history.append(self.fill)
            self.peak_fill = max(self.peak_fill, self.fill)
            if self.full_frame is None and self.fill >= self.cfg.full_threshold:
                self.full_frame = frame
            window = self.history[-self.cfg.stop_window:]
            self.stop_candidate = (len(window) >= self.cfg.stop_window
                                   and self.peak_fill >= self.cfg.min_stop_progress
                                   and max(window)-min(window) <= self.cfg.stop_delta)
            if self.stop_candidate:
                self.last_stop_candidate_frame = frame
        else:
            self.history = []
            self.stop_candidate = False
        return dict(frame=frame, shot_id=self.shot_id, status="detected", box=smooth,
                    confidence=chosen.confidence, fill=self.last_observed_fill if self.last_fill_frame == frame else None,
                    stop_candidate=self.stop_candidate, ended=None)

    def summary(self) -> dict | None:
        if not self.active:
            return None
        return dict(shot_id=self.shot_id, start_frame=self.start_frame,
                    last_observed_frame=self.last_observed_frame,
                    final_observed_fill=self.last_observed_fill,
                    final_fill_frame=self.last_fill_frame,
                    peak_fill=self.peak_fill,
                    reached_full=self.peak_fill >= self.cfg.full_threshold,
                    first_full_frame=self.full_frame,
                    last_stop_candidate_frame=self.last_stop_candidate_frame)
