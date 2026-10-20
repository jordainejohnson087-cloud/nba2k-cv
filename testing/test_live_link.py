"""Transport failure, freshness, evidence and GUI launch-command checks."""
import importlib.util
import json
import socket
import struct
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from live_link import BridgeClient, snapshot
import run_live
spec=importlib.util.spec_from_file_location('gtuner_meter',ROOT/'bridge'/'gtuner_meter.py')
gcv=importlib.util.module_from_spec(spec)
spec.loader.exec_module(gcv)


class LinkTests(unittest.TestCase):
    def test_no_receiver_is_not_connected(self):
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as unused:
            unused.bind(('127.0.0.1',0));port=unused.getsockname()[1]
        client=BridgeClient(port)
        try:
            for _ in range(3):
                status=client.send(dict(status='detected',fill=.5,shot_id=1))
            self.assertEqual(status['hardware'],'not verified')
            self.assertEqual(status['receiver'],'waiting for Gtuner')
        finally:client.close()

    def test_ack_hardware_heartbeat_and_timeout(self):
        # A software receiver alone cannot establish a hardware connection.
        with patch.object(gcv,'PORT',0):worker=gcv.GCVWorker(640,480)
        now=[10.]
        client=BridgeClient(worker.socket.getsockname()[1],clock=lambda:now[0])
        gtuner=SimpleNamespace(gpc_string=lambda:None)
        try:
            with patch.dict(sys.modules,{'gtuner':gtuner}):
                client.send(dict(status='detected',fill=.5,shot_id=1))
                _,payload=worker.process(None)
                self.assertEqual(struct.unpack('>BBHI',payload)[1:3],(1,5000))
                status=client.send(dict(status='predicted',fill=.9,shot_id=1))
                self.assertEqual(status['receiver'],'receiving')
                self.assertEqual(status['hardware'],'not verified')
                gtuner.gpc_string=lambda:'T2CV:4'
                _,payload=worker.process(None)
                self.assertEqual(struct.unpack('>BBHI',payload)[1:3],(2,65535))
                status=client.send(dict(status='detected',fill=.6,shot_id=1))
                self.assertEqual(status['hardware'],'heartbeat verified')
                now[0]+=2
                self.assertEqual(client.status()['receiver'],'waiting for Gtuner')
                self.assertEqual(client.status()['hardware'],'not verified')
        finally:client.close();worker.socket.close()

    def test_receiver_expires_data_and_ignores_old_packets(self):
        with patch.object(gcv,'PORT',0):worker=gcv.GCVWorker(1,1)
        sender=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
        peer=worker.socket.getsockname()
        packet=dict(version=1,session='a'*32,seq=1,status='detected',fill=.8,sent_at=time.time())
        try:
            sender.sendto(json.dumps(packet).encode(),peer)
            with patch.dict(sys.modules,{'gtuner':SimpleNamespace(gpc_string=lambda:None)}):
                worker.process(None)
                worker.last_packet=time.monotonic()-1
                self.assertEqual(struct.unpack('>BBHI',worker.process(None)[1])[1:3],(0,65535))
                packet.update(seq=2,sent_at=time.time()-2)
                sender.sendto(json.dumps(packet).encode(),peer)
                worker.process(None)
                self.assertEqual(worker.packet['seq'],1)
                sender.sendto(b'not json',peer)
                worker.process(None)
        finally:sender.close();worker.socket.close()

    def test_wrong_session_ack_cannot_mark_connection(self):
        server=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
        server.bind(('127.0.0.1',0))
        client=BridgeClient(server.getsockname()[1])
        try:
            client.send(dict(status='absent',fill=None,shot_id=None))
            _,peer=server.recvfrom(4096)
            server.sendto(json.dumps(dict(session='wrong',seq=1,hardware_counter=1,hardware_age_ms=0)).encode(),peer)
            status=client.send(dict(status='absent',fill=None,shot_id=None))
            self.assertEqual(status['receiver'],'waiting for Gtuner')
        finally:client.close();server.close()

    def test_snapshot_replaces_status(self):
        with tempfile.TemporaryDirectory() as folder:
            snapshot(folder,dict(frame=1));snapshot(folder,dict(frame=2))
            self.assertEqual(json.loads((Path(folder)/'live_status.json').read_text()),dict(frame=2))

    def test_graceful_stop_preserves_report(self):
        from test_live import FakeCapture,FakeYOLO,LiveTests
        cap=FakeCapture(5)
        cv=SimpleNamespace(VideoCapture=lambda _:cap,CAP_PROP_BUFFERSIZE=1,CAP_PROP_FPS=2,
                           CAP_PROP_FRAME_WIDTH=3,CAP_PROP_FRAME_HEIGHT=4)
        torch=SimpleNamespace(cuda=SimpleNamespace(is_available=lambda:False))
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);stop=root/'stop.request';stop.touch()
            args=LiveTests().args(root,'0');args.stop_file=stop
            report=run_live.run(args,cv,torch,FakeYOLO)
            self.assertEqual(report['stop_reason'],'user_stopped')
            self.assertEqual(report['frames'],0)
            self.assertTrue((root/'live_run.json').is_file())
            self.assertTrue(cap.released)


if __name__=='__main__':unittest.main()
