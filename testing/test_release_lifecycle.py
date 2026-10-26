"""Real worker UDP ingress; no physical Xbox claim."""
import json
import socket
import sys
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import test_green as fixtures
gcv=fixtures.gcv

class ReleaseLifecycleTests(unittest.TestCase):
    def setUp(self):
        with patch.object(gcv,'PORT',0),patch.object(gcv,'TEST_PORT',0):self.worker=gcv.GCVWorker(1,1)
        self.addCleanup(self.worker.__del__)
        self.sender=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);self.addCleanup(self.sender.close)
        self.seq=0;self.reply="T2CV:1:1:0:2:3:0:0:17168:0:0"
        self.context=patch.dict(sys.modules,gtuner=SimpleNamespace(gpc_string=lambda:self.reply))
        self.context.start();self.addCleanup(self.context.stop)

    def send(self,event=1,green=True,armed=True,run='a'*32):
        self.seq+=1;p=fixtures.ProtocolTests().packet(armed)
        p.update(session='f'*32,seq=self.seq)
        p['trigger'].update(event_id=event,green=green,run_id=run)
        self.sender.sendto(json.dumps(p).encode(),self.worker.socket.getsockname())

    def test_green_survives_later_non_green_in_same_udp_drain(self):
        self.send(green=True);self.send(green=False)
        data=self.worker.process(None)[1]
        self.assertIn(data[:3],(bytes((84,49,3)),bytes((84,50,3))))

    def test_neutral_does_not_replay_a_completed_event(self):
        self.send();self.worker.process(None)
        self.send(event=0,green=False,armed=False);self.worker.process(None)
        self.send();data=self.worker.process(None)[1]
        self.assertNotIn(data[:3],(bytes((84,49,3)),bytes((84,50,3))))

    def test_retry_keeps_token_and_device_ack_finishes_exact_event(self):
        self.send();first=self.worker.process(None)[1]
        again=self.worker.process(None)[1]
        self.assertEqual(first[3:7],again[3:7])
        token=int.from_bytes(first[3:7],'big')
        # An unrelated manual counter never acknowledges the AI event.
        self.reply='T2CV:2:1:0:2:3:20:3:17168:0:0'
        self.worker.process(None);self.assertEqual(self.worker.release['state'],'sent')
        self.reply=f'T2CV:3:1:0:2:3:20:3:17168:{token}:1'
        self.assertEqual(len(self.worker.process(None)[1]),26)
        self.assertEqual(self.worker.release['state'],'executed')
        self.assertGreaterEqual(self.worker.release['ack_latency_ms'],0)
        self.send();self.assertEqual(len(self.worker.process(None)[1]),26)

    def test_deadline_is_not_refreshed_and_unacknowledged_event_times_out(self):
        self.send();self.worker.process(None)
        deadline=self.worker.pending_release['deadline']
        self.send();self.worker.process(None)
        self.assertEqual(self.worker.pending_release['deadline'],deadline)
        with patch.object(gcv.time,'monotonic',return_value=deadline+.001):
            self.assertEqual(len(self.worker.process(None)[1]),26)
        self.assertEqual(self.worker.release['state'],'timed_out')
        self.send();self.assertEqual(len(self.worker.process(None)[1]),26)

    def test_new_detector_run_can_start_at_event_one_without_neutral_reset(self):
        self.send();self.worker.process(None);old=self.worker.release['token']
        self.send(run='b'*32);self.worker.process(None)
        self.assertNotEqual(self.worker.release['token'],old)
        self.assertEqual(self.worker.release['event_id'],1)

    def test_manual_release_cancels_pending_but_preserves_original_packet(self):
        self.send();self.worker.process(None)
        self.sender.sendto(('T2TEST:RELEASE_X:'+str(time.time())).encode(),self.worker.test_socket.getsockname())
        self.assertEqual(self.worker.process(None)[1],bytes((84,49,3)))
        self.assertEqual(self.worker.release['state'],'canceled')

    def test_old_script_is_explicitly_rejected(self):
        self.reply='T2CV:1:1:0:2:3:0:0:272'
        self.send();self.assertEqual(len(self.worker.process(None)[1]),26)
        self.assertEqual(self.worker.release['state'],'rejected')
        self.assertIn('matching',self.worker.release['reason'])

    def test_100_events_with_dropped_deliveries_and_mixed_terminal_results(self):
        for event in range(1,101):
            self.send(event=event);data=self.worker.process(None)[1]
            self.assertEqual(data[:3],bytes((84,50,3)))
            token=int.from_bytes(data[3:7],'big')
            self.assertEqual(self.worker.process(None)[1][3:7],data[3:7])
            result=3 if event%7==0 else 1
            self.reply=f'T2CV:{event+2}:1:0:2:3:0:0:17168:{token}:{result}'
            self.worker.process(None)
            self.assertEqual(self.worker.release['state'],'rejected' if result==3 else 'executed')
            self.send(event=event);self.assertEqual(len(self.worker.process(None)[1]),26)
