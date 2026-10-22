"""Load in Gtuner IV Computer Vision; CUDA inference stays in the desktop app.

Select a video/display source OTHER than the app's capture-card device in
Gtuner so the two processes do not compete for exclusive device access.
"""
import json
import math
import socket
import struct
import time
import sys

PORT = 26740
TEST_PORT = 26741
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
    mode=trigger.get('shot_mode','timing')
    if mode not in ('rhythm','timing'):raise ValueError('Invalid shot mode')
    shot=packet.get('shot_id') or 0
    if type(shot) is not int or not 0<=shot<=0xffffffff:raise ValueError('Invalid shot ID')
    observed_age=max(0,(time.time()-packet.get('sent_at',0))*1000)
    remaining=max(0,trigger['max_age_ms']-observed_age)
    observed_fresh=fresh and remaining>=25
    armed=bool(fresh and remaining>0 and trigger['armed'])
    green=bool(observed_fresh and armed and status==1 and trigger['green'] and trigger['valid'] and trigger['eligible'])
    # 0, 'M', v3, observed status, arm, green, direction, delay, hold,
    # event ID, tracked shot ID, transport sequence, remaining lifetime, shot mode.
    # v2 GPC rejects this version rather than using the wrong shooting mechanics.
    return bytearray(struct.pack('>7BHHIIIHB',0,77,3,status if observed_fresh else 0,int(armed),int(green),
        int(trigger['direction']=='down'),trigger['delay_ms'],trigger['hold_ms'],
        trigger['event_id'],shot,packet.get('seq',0)&0xffffffff,max(25,int(remaining)),int(mode=='timing')))


class GCVWorker:
    def __init__(self, width, height):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind(('127.0.0.1', PORT))
        self.socket.setblocking(False)
        self.test_socket=None;self.test_reset=False;self.last_test=-1.0
        self.device_test_count=None;self.device_test_button=None
        candidate=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
        try:
            candidate.bind(('127.0.0.1',TEST_PORT));candidate.setblocking(False)
            self.test_socket=candidate
        except OSError as exc:
            candidate.close();print('Console test port unavailable: '+str(exc),flush=True)
        self.callback_seen=False;self.packet_seen=False
        print('NBA2K CV bridge v3: Python '+sys.version.split()[0]+'; UDP 127.0.0.1:'+str(PORT)+' bound. Waiting for GCV callbacks.',flush=True)
        self.packet = {}
        self.last_packet = 0
        self.hardware_counter = None
        self.last_hardware = None
        self.session = None
        self.device_sequence = None
        self.device_event = None
        self.active_slot = None
        self.device_protocol = None
        self.device_gate = None

    def process(self, frame):
        import gtuner
        now = time.monotonic()
        if not self.callback_seen:
            self.callback_seen=True
            print('NBA2K CV bridge: GCV callbacks active. Waiting for app telemetry.',flush=True)
        message = gtuner.gpc_string()
        if message is not None:
            try:
                text = message.decode() if isinstance(message, bytes) else str(message)
                if text.startswith('T2CV:'):
                    parts=text[5:].strip().split(':')
                    counter = int(parts[0])
                    if len(parts) in (3,4,5,7,8):
                        self.device_sequence=int(parts[1]);self.device_event=int(parts[2])
                    self.active_slot=int(parts[3]) if len(parts) in (4,5,7,8) and 1<=int(parts[3])<=9 else None
                    self.device_protocol=int(parts[4]) if len(parts) in (5,7,8) else None
                    self.device_test_count=int(parts[5]) if len(parts) in (7,8) else None
                    self.device_test_button=int(parts[6]) if len(parts) in (7,8) else None
                    self.device_gate=int(parts[7]) if len(parts)==8 else None
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
                if not self.packet_seen:
                    self.packet_seen=True
                    print('NBA2K CV bridge: valid app telemetry received; sending acknowledgments.',flush=True)
                self.last_packet = now
                ack = dict(session=packet['session'],seq=packet['seq'],worker_protocol=3,device_protocol=self.device_protocol,
                    device_gate=self.device_gate,test_available=self.test_socket is not None,device_test_count=self.device_test_count,device_test_button=self.device_test_button,
                    hardware_counter=self.hardware_counter,active_slot=self.active_slot,device_sequence=self.device_sequence,device_event=self.device_event,
                    hardware_age_ms=(now-self.last_hardware)*1000 if self.last_hardware is not None else None)
                self.socket.sendto(json.dumps(ack).encode(),peer)
            except (ValueError,TypeError,AttributeError,OverflowError):
                continue
        # Exact T2TEST command used by the working manual PowerShell sender.
        # One callback command + one neutral callback, never retry a tap.
        command=0;neutral=self.test_reset;self.test_reset=False
        if self.test_socket is not None:
            for _ in range(8):
                try:data,_=self.test_socket.recvfrom(128)
                except BlockingIOError:break
                try:
                    prefix,button,stamp=data.decode('ascii').split(':')
                    age=time.time()-float(stamp)
                    armed=self.packet.get('trigger',{}).get('armed',False) and now-self.last_packet<STALE_SECONDS
                    if prefix=='T2TEST' and 0<=age<=.5:
                        # A release must never be dropped by tap cooldown, neutral
                        # callback or a concurrent arm transition.
                        if button=='RELEASE_X':command=3
                        elif (not neutral and not command and not armed and now-self.last_test>=.8
                              and button in ('A','X','HOLD_X')):
                            command={'A':1,'X':2,'HOLD_X':4}[button]
                except (ValueError,UnicodeError):pass
        if command:
            self.last_test=now;self.test_reset=True
            print('CONSOLE TEST: forwarding one '+{1:'A tap',2:'X tap',3:'X release',4:'X hold'}[command]+' to Titan; Xbox receipt unverified.',flush=True)
            return frame,bytearray((84,49,command))
        if neutral:return frame,bytearray((84,49,0))
        return frame, encode(self.packet, now-self.last_packet < STALE_SECONDS and
                             0<=time.time()-self.packet.get('sent_at',0)<=STALE_SECONDS)

    def __del__(self):
        sock = getattr(self,'socket',None)
        if sock is not None:sock.close()
        test_sock=getattr(self,'test_socket',None)
        if test_sock is not None:test_sock.close()
