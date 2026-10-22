"""Conservative, configurable color calibration and observed-frame trigger gating.

This is a vertical white-fill/green-band heuristic, not a trained green detector.
Validate on raw gameplay before enabling controller output.
"""
import math
import time

DEFAULT_GREEN = dict(armed=False, hue_low=35, hue_high=90, saturation_min=90,
    value_min=70, row_coverage=.12, min_confidence=.5, confirmations=1,
    max_age_ms=100, rearm_ms=500, delay_ms=45, hold_ms=100, direction='up', shot_mode='rhythm')


def green_config(overrides=None):
    result=dict(DEFAULT_GREEN)
    if overrides:
        unknown=set(overrides)-set(result)
        if unknown:raise ValueError('Unknown green settings: '+', '.join(sorted(unknown)))
        result.update(overrides)
    if type(result['armed']) is not bool:raise ValueError('Armed must be true or false')
    bounds=dict(hue_low=(0,179),hue_high=(0,179),saturation_min=(0,255),value_min=(0,255),
        confirmations=(1,5),max_age_ms=(25,250),rearm_ms=(250,2000),delay_ms=(0,250),hold_ms=(10,500))
    for key,(low,high) in bounds.items():
        value=result[key]
        if type(value) is not int or not low<=value<=high:raise ValueError('Invalid '+key)
    for key in ('row_coverage','min_confidence'):
        value=result[key]
        if type(value) not in (int,float) or not math.isfinite(value) or not 0<value<=1:
            raise ValueError('Invalid '+key)
    if result['hue_low']>result['hue_high']:raise ValueError('Green hue low exceeds high')
    if result['direction'] not in ('up','down'):raise ValueError('Invalid flick direction')
    if result['shot_mode'] not in ('rhythm','timing'):raise ValueError('Invalid shot mode')
    return result


def measure_green(frame, box, raw_fill, config, top_fraction=.04, bottom_fraction=.90):
    import cv2
    empty=dict(valid=False,in_green=False,zone_top=None,zone_bottom=None,reason='no measured fill')
    if raw_fill is None or not math.isfinite(raw_fill) or not 0<=raw_fill<=1:return empty
    h,w=frame.shape[:2]
    x1,y1,x2,y2=[round(v) for v in box]
    x1,x2=max(0,x1),min(w,x2);y1,y2=max(0,y1),min(h,y2)
    if x2-x1<5 or y2-y1<12:return dict(empty,reason='meter too small')
    height=y2-y1
    left=x1+int((x2-x1)*.18);right=x1+max(1,int((x2-x1)*.82))
    hsv=cv2.cvtColor(frame[y1:y2,left:right],cv2.COLOR_BGR2HSV)
    mask=((hsv[:,:,0]>=config['hue_low']) & (hsv[:,:,0]<=config['hue_high']) &
          (hsv[:,:,1]>=config['saturation_min']) & (hsv[:,:,2]>=config['value_min']))
    rows=mask.mean(axis=1)>=config['row_coverage']
    runs=[];start=None
    top=max(0,math.ceil(height*top_fraction));bottom=min(height-1,math.floor(height*bottom_fraction))
    for row in range(top,bottom+2):
        yes=row<=bottom and bool(rows[row])
        if yes and start is None:start=row
        if not yes and start is not None:
            if 2<=row-start<=height*.35:runs.append((start,row-1))
            start=None
    if len(runs)!=1:return dict(empty,reason='green band missing or ambiguous')
    zone_top,zone_bottom=runs[0]
    frontier=height*(bottom_fraction-raw_fill*(bottom_fraction-top_fraction))
    return dict(valid=True,in_green=zone_top<=frontier<=zone_bottom,
        zone_top=y1+zone_top,zone_bottom=y1+zone_bottom,frontier_y=y1+frontier,
        raw_fill=raw_fill,reason='observed green band and white-fill edge')


class GreenGate:
    def __init__(self, config):
        self.config=green_config(config)
        self.event=0
        self.latched=False
        self.approaching=False
        self.consecutive=0
        self.last_meter=None

    def configure(self, config):
        updated=green_config(config)
        if any(updated[key]!=self.config[key] for key in ('armed','shot_mode','direction')):
            self.latched=False;self.approaching=False;self.consecutive=0
        self.config=updated

    def update(self,state,measurement,captured_at,now=None):
        now=time.time() if now is None else now
        cfg=self.config
        age=(now-captured_at)*1000
        observed=state['status']=='detected'
        if observed:self.last_meter=now
        # A changed tracker shot_id is NOT enough to rearm: gaps can fragment shots.
        if state['status']=='absent' and self.last_meter is not None and (now-self.last_meter)*1000>=cfg['rearm_ms']:
            self.latched=False;self.approaching=False;self.consecutive=0
        valid=(observed and 0<=age<=cfg['max_age_ms'] and
            (state.get('confidence') or 0)>=cfg['min_confidence'] and measurement.get('valid',False))
        inside=bool(valid and measurement.get('in_green'))
        if valid and not inside and measurement.get('frontier_y',0)>measurement['zone_bottom']:
            self.approaching=True
        self.consecutive=self.consecutive+1 if inside else 0
        candidate=inside and self.approaching and not self.latched and self.consecutive>=cfg['confirmations']
        if candidate:
            self.event=(self.event+1)&0xffffffff or 1
            self.latched=True
        return dict(version=3,shot_mode=cfg['shot_mode'],armed=cfg['armed'],green=inside,eligible=bool(inside and self.latched),
            event_id=self.event,candidate=bool(candidate),valid=bool(valid),
            delay_ms=cfg['delay_ms'],hold_ms=cfg['hold_ms'],direction=cfg['direction'],
            max_age_ms=cfg['max_age_ms'])
