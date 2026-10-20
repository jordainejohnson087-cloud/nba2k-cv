"""Load in Gtuner IV Computer Vision; CUDA inference stays in the desktop app.

Select a video/display source OTHER than the app's capture-card device in
Gtuner so the two processes do not compete for exclusive device access.
"""
import json
import math
import socket
import struct
import time

PORT = 26740
STALE_SECONDS = .25


def encode(packet, fresh):
    status = {'absent':0, 'detected':1, 'predicted':2}.get(packet.get('status'),0) if fresh else 0
    fill = packet.get('fill') if status == 1 else None
    if fill is not None and (type(fill) not in (float,int) or not math.isfinite(fill) or not 0 <= fill <= 1):
        raise ValueError('Invalid fill')
    trigger=packet.get('trigger')
    if trigger is None:
        # Legacy telemetry stays non-triggering for the old Onyx byte-zero flag.
        return bytearray(struct.pack('>BBHI',0,status,65535 if fill is None else round(fill*10000),
                                     packet.get('seq',0)&0xffffffff))
    if not isinstance(trigger,dict):raise ValueError('Invalid trigger packet')
    for key in ('armed','green','valid','eligible'):
        if type(trigger.get(key)) is not bool:raise ValueError('Invalid trigger '+key)
    for key,low,high in [('delay_ms',0,250),('hold_ms',10,500),('max_age_ms',25,250),('event_id',0,0xffffffff)]:
        if type(trigger.get(key)) is not int or not low<=trigger[key]<=high:raise ValueError('Invalid trigger '+key)
    if trigger.get('direction') not in ('up','down'):raise ValueError('Invalid direction')
    shot=packet.get('shot_id') or 0
    if type(shot) is not int or not 0<=shot<=0xffffffff:raise ValueError('Invalid shot ID')
    observed_age=max(0,(time.time()-packet.get('sent_at',0))*1000)
    remaining=max(0,trigger['max_age_ms']-observed_age)
    fresh=fresh and remaining>=25
    armed=bool(fresh and trigger['armed'])
    green=bool(armed and status==1 and trigger['green'] and trigger['valid'] and trigger['eligible'])
    # 0, 'M', v2, observed status, arm, green, direction, delay, hold,
    # event ID, tracked shot ID, transport sequence, remaining lifetime.
    return bytearray(struct.pack('>7BHHIIIH',0,77,2,status if fresh else 0,int(armed),int(green),
        int(trigger['direction']=='down'),trigger['delay_ms'],trigger['hold_ms'],
        trigger['event_id'],shot,packet.get('seq',0)&0xffffffff,max(25,int(remaining))))


class GCVWorker:
    def __init__(self, width, height):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind(('127.0.0.1', PORT))
        self.socket.setblocking(False)
        self.packet = {}
        self.last_packet = 0
        self.hardware_counter = None
        self.last_hardware = None
        self.session = None
        self.device_sequence = None
        self.device_event = None
        self.active_slot = None

    def process(self, frame):
        import gtuner
        now = time.monotonic()
        message = gtuner.gpc_string()
        if message is not None:
            try:
                text = message.decode() if isinstance(message, bytes) else str(message)
                if text.startswith('T2CV:'):
                    parts=text[5:].strip().split(':')
                    counter = int(parts[0])
                    if len(parts) in (3,4):
                        self.device_sequence=int(parts[1]);self.device_event=int(parts[2])
                    self.active_slot=int(parts[3]) if len(parts)==4 and 1<=int(parts[3])<=9 else None
                    if counter != self.hardware_counter:
                        self.hardware_counter = counter
                        self.last_hardware = now
            except (ValueError, UnicodeError):
                pass
        for _ in range(128):
            try:
                data, peer = self.socket.recvfrom(4096)
            except BlockingIOError:
                break
            try:
                packet = json.loads(data)
                if (not isinstance(packet,dict) or packet.get('version') not in (1,2)
                    or type(packet.get('seq')) is not int or packet['seq'] < 1
                    or not isinstance(packet.get('session'),str) or len(packet['session']) != 32
                    or type(packet.get('sent_at')) not in (int,float)
                    or not 0 <= time.time()-packet['sent_at'] <= STALE_SECONDS
                    or packet.get('status') not in ('absent','detected','predicted')):
                    continue
                encode(packet, True)
                if packet['session'] != self.session:
                    self.session = packet['session']
                    self.packet = {}
                if packet['seq'] <= self.packet.get('seq',0):
                    continue
                self.packet = packet
                self.last_packet = now
                ack = dict(session=packet['session'],seq=packet['seq'],
                    hardware_counter=self.hardware_counter,active_slot=self.active_slot,device_sequence=self.device_sequence,device_event=self.device_event,
                    hardware_age_ms=(now-self.last_hardware)*1000 if self.last_hardware is not None else None)
                self.socket.sendto(json.dumps(ack).encode(),peer)
            except (ValueError,TypeError,AttributeError,OverflowError):
                continue
        return frame, encode(self.packet, now-self.last_packet < STALE_SECONDS and
                             0<=time.time()-self.packet.get('sent_at',0)<=STALE_SECONDS)

    def __del__(self):
        sock = getattr(self,'socket',None)
        if sock is not None:
            sock.close()
