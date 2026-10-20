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
    # Version, observed/predicted/absent, fill in 0..10000 (65535 unknown), sequence.
    return bytearray(struct.pack('>BBHI', 1, status, 65535 if fill is None else round(fill*10000),
                                 packet.get('seq',0) & 0xffffffff))


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

    def process(self, frame):
        import gtuner
        now = time.monotonic()
        message = gtuner.gpc_string()
        if message is not None:
            try:
                text = message.decode() if isinstance(message, bytes) else str(message)
                if text.startswith('T2CV:'):
                    counter = int(text[5:].strip())
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
                if (not isinstance(packet,dict) or packet.get('version') != 1
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
                    hardware_counter=self.hardware_counter,
                    hardware_age_ms=(now-self.last_hardware)*1000 if self.last_hardware is not None else None)
                self.socket.sendto(json.dumps(ack).encode(),peer)
            except (ValueError,TypeError,AttributeError,OverflowError):
                continue
        return frame, encode(self.packet, now-self.last_packet < STALE_SECONDS)

    def __del__(self):
        sock = getattr(self,'socket',None)
        if sock is not None:
            sock.close()
