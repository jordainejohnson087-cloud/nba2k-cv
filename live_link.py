"""Local UI snapshots and bounded, acknowledged Gtuner telemetry transport."""
import json
import math
import os
import secrets
import queue
import threading
import socket
import time
from pathlib import Path


def atomic_write(path, content):
    path = Path(path)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_bytes(content)
    os.replace(temp, path)


def snapshot(out, payload, cv2=None, frame=None):
    """At most one small preview is retained; no frame backlog for the UI."""
    out = Path(out)
    if frame is not None:
        height, width = frame.shape[:2]
        ratio = min(640 / width, 360 / height, 1)
        preview = cv2.resize(frame, (max(1, round(width*ratio)), max(1, round(height*ratio))))
        ok, encoded = cv2.imencode('.png', preview, [cv2.IMWRITE_PNG_COMPRESSION, 1])
        if ok:
            try:
                atomic_write(out/'preview.png', encoded.tobytes())
            except PermissionError:
                pass  # A Windows image reader may briefly hold the previous snapshot.
    try:
        atomic_write(out/'live_status.json', json.dumps(payload, allow_nan=False).encode())
    except PermissionError:
        pass


class SnapshotPublisher:
    """Encode/write on a worker with one pending update; never queue old video."""
    def __init__(self, out, cv2):
        self.out, self.cv2 = out, cv2
        self.pending = queue.Queue(maxsize=1)
        self.stopped = threading.Event()
        self.error = None
        self.thread = threading.Thread(target=self._work, daemon=True)
        self.thread.start()

    def submit(self, payload, frame=None):
        try:
            self.pending.put_nowait((payload, frame))
        except queue.Full:
            try:self.pending.get_nowait()
            except queue.Empty:pass
            try:self.pending.put_nowait((payload, frame))
            except queue.Full:pass

    def _work(self):
        while not self.stopped.is_set() or not self.pending.empty():
            try:payload, frame = self.pending.get(timeout=.1)
            except queue.Empty:continue
            try:snapshot(self.out, payload, self.cv2, frame)
            except Exception as exc:self.error = str(exc)

    def close(self):
        self.stopped.set()
        self.thread.join(timeout=2)


class BridgeClient:
    def __init__(self, port, clock=time.monotonic):
        self.clock = clock
        self.peer = ('127.0.0.1', port)
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind(('127.0.0.1', 0))
        self.socket.setblocking(False)
        self.session = secrets.token_hex(16)
        self.seq = 0
        self.sent = self.acked = 0
        self.last_ack = self.last_hardware = None
        self.rtt_ms = None
        self.pending = {}
        self.hardware_counter = None
        self.device_sequence = self.device_event = None
        self.active_slot = None

    def send(self, state, observed_at=None):
        now = self.clock()
        self.seq += 1
        packet = dict(version=1, session=self.session, seq=self.seq,
                      status=state['status'], fill=state['fill'] if state['status']=='detected' else None,
                      shot_id=state['shot_id'], sent_at=time.time() if observed_at is None else observed_at)
        if 'trigger' in state:
            packet['version']=2
            packet['trigger']=state['trigger']
        try:
            self.socket.sendto(json.dumps(packet).encode(), self.peer)
        except (ConnectionResetError, ConnectionRefusedError):
            return self.status()
        self.sent += 1
        self.pending[self.seq] = now
        self.pending = {seq:stamp for seq,stamp in self.pending.items() if now-stamp < 1}
        for _ in range(64):
            try:
                data, address = self.socket.recvfrom(4096)
            except (BlockingIOError, ConnectionResetError):
                break
            if address != self.peer:
                continue
            try:
                ack = json.loads(data)
                seq = ack.get('seq')
                if ack.get('session') != self.session or type(seq) is not int or seq not in self.pending:
                    continue
                self.rtt_ms = (now-self.pending.pop(seq))*1000
                self.last_ack = now
                self.acked += 1
                counter = ack.get('hardware_counter')
                age = ack.get('hardware_age_ms')
                if (type(counter) is int and type(age) in (int,float) and math.isfinite(age)
                        and 0 <= age <= 1500 and counter != self.hardware_counter):
                    self.hardware_counter = counter
                    self.last_hardware = now-age/1000
                    slot=ack.get('active_slot')
                    self.active_slot=slot if type(slot) is int and 1<=slot<=9 else None
                    for key in ('device_sequence','device_event'):
                        value=ack.get(key)
                        if type(value) is int and 0<=value<=0xffffffff:setattr(self,key,value)
            except (ValueError, TypeError, AttributeError):
                continue
        return self.status()

    def status(self):
        now = self.clock()
        linked = self.last_ack is not None and now-self.last_ack < .75
        hardware = linked and self.last_hardware is not None and now-self.last_hardware < 1.5
        return dict(receiver='receiving' if linked else 'waiting for Gtuner',
                    hardware='heartbeat verified' if hardware else 'not verified',
                    active_slot=self.active_slot if hardware else None,
                    rtt_ms=self.rtt_ms, sent=self.sent, acknowledged=self.acked,
                    device_sequence=self.device_sequence if hardware else None,
                    device_event=self.device_event if hardware else None)

    def probe(self, timeout=5, sleep=time.sleep, expected_slot=None):
        """Verify a v2 payload echo using an unarmed challenge before capture opens."""
        self.seq=secrets.randbelow(0x7fffffff)+1
        first=self.seq+1
        deadline=self.clock()+timeout
        neutral=dict(status='absent',fill=None,shot_id=None,trigger=dict(
            armed=False,green=False,valid=False,eligible=False,event_id=0,
            delay_ms=0,hold_ms=10,max_age_ms=100,direction='up'))
        status=self.status()
        while self.clock()<deadline:
            status=self.send(neutral)
            sequence=status.get('device_sequence')
            if (status['hardware']=='heartbeat verified' and type(sequence) is int
                    and first<=sequence<=self.seq
                    and (expected_slot is None or status.get('active_slot')==expected_slot)):
                return dict(status,ready=True)
            sleep(.05)
        return dict(status,ready=False)

    def close(self):
        self.socket.close()
