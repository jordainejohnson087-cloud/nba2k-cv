"""Transport failure, freshness, evidence and GUI launch-command checks."""
import importlib.util
import json
import socket
import struct
import sys
import tempfile
import time
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from live_link import BridgeClient, SnapshotPublisher, snapshot
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

    def test_slow_preview_keeps_only_latest_pending_frame(self):
        started=threading.Event();release=threading.Event();written=[]
        def slow(out,payload,cv2,frame):
            if payload['frame']==0:
                started.set();release.wait(2)
            written.append(payload['frame'])
        with patch('live_link.snapshot',side_effect=slow):
            publisher=SnapshotPublisher('.',None)
            try:
                publisher.submit(dict(frame=0))
                self.assertTrue(started.wait(1))
                for i in range(1,50):publisher.submit(dict(frame=i))
                self.assertEqual(publisher.pending.qsize(),1)
            finally:
                release.set();publisher.close()
            self.assertEqual(written,[0,49])

    def test_explicit_cuda_does_not_fall_back(self):
        from test_live import FakeCapture,FakeYOLO,LiveTests
        cap=FakeCapture(5)
        cv=SimpleNamespace(VideoCapture=lambda _:cap,CAP_PROP_BUFFERSIZE=1,CAP_PROP_FPS=2,
                           CAP_PROP_FRAME_WIDTH=3,CAP_PROP_FRAME_HEIGHT=4)
        torch=SimpleNamespace(cuda=SimpleNamespace(is_available=lambda:False))
        with tempfile.TemporaryDirectory() as folder:
            args=LiveTests().args(Path(folder),'0');args.device='0'
            with self.assertRaisesRegex(RuntimeError,'cannot access CUDA'):
                run_live.run(args,cv,torch,FakeYOLO)
            self.assertTrue(cap.released)

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


class BridgePreflightTests(unittest.TestCase):
    def test_probe_requires_device_echo_of_new_unarmed_payload(self):
        client=BridgeClient(26740)
        ticks=[0.0]
        client.clock=lambda:ticks[0]
        packets=[]
        def send(state):
            packets.append(state)
            client.seq+=1
            return dict(receiver='receiving',hardware='heartbeat verified',
                        device_sequence=client.seq if len(packets)>1 else 1)
        try:
            with patch.object(client,'send',side_effect=send):
                result=client.probe(timeout=1,sleep=lambda delay:ticks.__setitem__(0,ticks[0]+delay))
            self.assertTrue(result['ready'])
            self.assertEqual(len(packets),2)
            self.assertTrue(all(p['status']=='absent' and p['trigger']['armed'] is False
                                and p['trigger']['eligible'] is False for p in packets))
            self.assertEqual(gcv.encode(dict(packets[0],seq=42,sent_at=time.time()),True)[4:6],b'\x00\x00')
        finally:client.close()

    def test_receiver_or_old_heartbeat_alone_cannot_pass(self):
        for hardware,sequence in [('not verified',None),('heartbeat verified',1)]:
            client=BridgeClient(26740);ticks=[0.0];client.clock=lambda:ticks[0]
            try:
                with patch.object(client,'send',return_value=dict(receiver='receiving',
                        hardware=hardware,device_sequence=sequence)):
                    result=client.probe(timeout=.2,sleep=lambda delay:ticks.__setitem__(0,ticks[0]+delay))
                self.assertFalse(result['ready'])
            finally:client.close()


class SlotEvidenceTests(unittest.TestCase):
    def test_preflight_rejects_wrong_slot_even_with_payload_echo(self):
        client=BridgeClient(26740);ticks=[0.0];client.clock=lambda:ticks[0]
        def wrong(state):
            client.seq+=1
            return dict(receiver='receiving',hardware='heartbeat verified',device_sequence=client.seq,active_slot=3)
        try:
            with patch.object(client,'send',side_effect=wrong):
                result=client.probe(timeout=.2,expected_slot=4,sleep=lambda delay:ticks.__setitem__(0,ticks[0]+delay))
            self.assertFalse(result['ready'])
            ticks[0]=0
            def correct(state):return dict(wrong(state),active_slot=4)
            with patch.object(client,'send',side_effect=correct):
                self.assertTrue(client.probe(expected_slot=4)['ready'])
        finally:client.close()

    def test_worker_reports_slot_from_gpc_and_clears_it_for_old_script(self):
        with patch.object(gcv,'PORT',0):worker=gcv.GCVWorker(1,1)
        try:
            gtuner=SimpleNamespace(gpc_string=lambda:b'T2CV:1:42:0:4')
            with patch.dict(sys.modules,{'gtuner':gtuner}):worker.process(None)
            self.assertEqual(worker.active_slot,4)
            gtuner.gpc_string=lambda:b'T2CV:2:43:0'
            with patch.dict(sys.modules,{'gtuner':gtuner}):worker.process(None)
            self.assertIsNone(worker.active_slot)
        finally:worker.socket.close()
