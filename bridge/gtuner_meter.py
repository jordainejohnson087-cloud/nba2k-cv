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
import secrets
from collections import deque

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


def console_command(command):
    """Identical output packet for manual PowerShell and automatic X release."""
    return bytearray((84,49,command))


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
        self.auto_release_event = 0
        self.released_event = None
        self.release=None;self.pending_release=None;self.event_highwater={}
        self.next_token=secrets.randbelow(0x7fffffff)+1
        self.retired_sessions=deque(maxlen=16)

    def finish_release(self,state,reason,now):
        if self.pending_release is None:return
        self.pending_release.update(state=state,reason=reason,terminal_mono=now)
        self.release=self.pending_release;self.pending_release=None
        print('AI RELEASE RESULT: '+json.dumps(self.release,sort_keys=True),flush=True)

    def observe_event(self,packet,now):
        trigger=packet.get('trigger') or {}
        identity=(packet['session'],trigger.get('run_id'))
        if self.pending_release and (not trigger.get('armed') or trigger.get('shot_mode','timing')!='timing'
                or identity!=self.pending_release['identity']):
            self.finish_release('canceled','disarmed, mode changed, or detector restarted',now)
        event=trigger.get('event_id',0)
        if not event or event<=self.event_highwater.get(identity,0):return
        if not (packet['status']=='detected' and trigger.get('armed') and trigger.get('green')
                and trigger.get('valid') and trigger.get('eligible') and trigger.get('shot_mode','timing')=='timing'):return
        self.event_highwater[identity]=event
        # Bound run history. Neutral telemetry never resets deduplication.
        if len(self.event_highwater)>64:del self.event_highwater[next(iter(self.event_highwater))]
        if self.pending_release:self.finish_release('canceled','superseded by a new observed shot',now)
        age=max(0,(time.time()-packet['sent_at'])*1000)
        self.next_token=(self.next_token+1)&0x7fffffff or 1
        self.pending_release=dict(identity=identity,event_id=event,token=self.next_token,state='pending',
            reason='fresh observed green; immediate release policy',received_mono=now,
            source_age_ms=age,deadline=now+(trigger['max_age_ms']-age-25)/1000,
            sent_mono=None,ack_mono=None,attempts=0)
        self.release=self.pending_release
        if self.pending_release['deadline']<=now:self.finish_release('rejected','late observed event',now)

    def release_result(self):
        if not self.release:return None
        return {k:v for k,v in self.release.items() if k not in ('identity','deadline')}

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
                    if len(parts) in (3,4,5,7,8,10):
                        self.device_sequence=int(parts[1]);self.device_event=int(parts[2])
                    self.active_slot=int(parts[3]) if len(parts) in (4,5,7,8,10) and 1<=int(parts[3])<=9 else None
                    self.device_protocol=int(parts[4]) if len(parts) in (5,7,8,10) else None
                    self.device_test_count=int(parts[5]) if len(parts) in (7,8,10) else None
                    self.device_test_button=int(parts[6]) if len(parts) in (7,8,10) else None
                    self.device_gate=int(parts[7]) if len(parts) in (8,10) else None
                    if len(parts)==10 and self.release:
                        token,result=int(parts[8]),int(parts[9])
                        if token==self.release['token'] and result in (1,2,3,4) and self.release.get('ack_mono') is None:
                            self.release['ack_mono']=now
                            self.release['device_result']={1:'executed',2:'rejected',3:'rejected',4:'rejected'}[result]
                            self.release['device_reason']={1:'X output set to zero',2:'not armed or link stale',3:'physical X already released or shot blocked',4:'invalid lifetime'}[result]
                            if self.release.get('sent_mono') is not None:
                                self.release['ack_latency_ms']=(now-self.release['sent_mono'])*1000
                            if self.pending_release:self.finish_release(self.release['device_result'],self.release['device_reason'],now)
                            elif self.release['state'] in ('timed_out','canceled','rejected'):self.release['late_ack']=True
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
                if packet['session'] in self.retired_sessions:continue
                if packet['session'] != self.session:
                    if self.session:self.retired_sessions.append(self.session)
                    self.session = packet['session']
                    self.packet = {}
                if packet['seq'] <= self.packet.get('seq',0):
                    continue
                self.packet = packet
                self.observe_event(packet,now)
                if not self.packet_seen:
                    self.packet_seen=True
                    print('NBA2K CV bridge: valid app telemetry received; sending acknowledgments.',flush=True)
                self.last_packet = now
                ack = dict(session=packet['session'],seq=packet['seq'],worker_protocol=3,device_protocol=self.device_protocol,
                    output_path='console_release_ack',auto_release_event=self.auto_release_event,release_result=self.release_result(),
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
                        elif (button=='AI_HOLD_X' and not neutral and not command and armed
                              and self.packet.get('trigger',{}).get('shot_mode')=='timing'
                              and self.packet.get('status')=='absent' and now-self.last_test>=.8):command=5
                        elif (not neutral and not command and not armed and now-self.last_test>=.8
                              and button in ('A','X','HOLD_X')):
                            command={'A':1,'X':2,'HOLD_X':4}[button]
                except (ValueError,UnicodeError):pass
        payload=encode(self.packet, now-self.last_packet < STALE_SECONDS and
                       0<=time.time()-self.packet.get('sent_at',0)<=STALE_SECONDS)
        if len(payload)==26:payload[5]=0  # Only the identified command path may release.
        if command and self.pending_release:
            self.finish_release('canceled','manual command superseded AI release',now)
        pending=self.pending_release
        if pending:
            if now>=pending['deadline']:
                self.finish_release('timed_out','release acknowledgment missing before source deadline; execution unknown',now)
            elif self.last_hardware is None or now-self.last_hardware>1.5:
                self.finish_release('rejected','no live Titan heartbeat',now)
            elif self.device_gate is None or not self.device_gate & 16384:
                self.finish_release('rejected','install matching event-acknowledging Titan script',now)
            else:
                if pending['sent_mono'] is None:pending['sent_mono']=now
                pending['state']='sent';pending['attempts']+=1
                self.auto_release_event=pending['event_id']
                # Same RELEASE_X operation, with an idempotent token envelope.
                # Retry only within the original observation deadline.
                lifetime=max(1,int((pending['deadline']-now)*1000))
                return frame,bytearray(struct.pack('>BBBIH',84,50,3,pending['token'],lifetime))
        if command:
            self.last_test=now;self.test_reset=True
            print('CONSOLE TEST: forwarding one '+{1:'A tap',2:'X tap',3:'X release',4:'X hold',5:'AI-timed X hold'}[command]+' to Titan; Xbox receipt unverified.',flush=True)
            return frame,console_command(command)
        if neutral:return frame,console_command(0)
        return frame,payload

    def __del__(self):
        sock = getattr(self,'socket',None)
        if sock is not None:sock.close()
        test_sock=getattr(self,'test_socket',None)
        if test_sock is not None:test_sock.close()
