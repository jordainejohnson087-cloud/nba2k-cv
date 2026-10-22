"""Synthetic meter -> gate -> real local API/UDP -> GCV payload.

The simulated GPC echo verifies software transport, not physical console action.
"""
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from bridge_service import BridgeService,BridgeServiceClient
from meter_green import GreenGate,green_config,measure_green
from run_video import estimate_fill
import test_green as green_fixtures
gcv=green_fixtures.gcv


def wait_for(predicate,timeout=2):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        if predicate():return True
        time.sleep(.005)
    return False


class GreenBridgeTests(unittest.TestCase):
    def test_observed_green_becomes_one_event_and_disarm_removes_output(self):
        with patch.object(gcv,'PORT',0):worker=gcv.GCVWorker(1920,1080)
        payloads=[];stopped=threading.Event();sequence=0;event=0;counter=0;errors=[]
        def echo():return f'T2CV:{counter}:{sequence}:{event}:2'
        def callbacks():
            nonlocal sequence,event,counter
            try:
                while not stopped.is_set():
                    _,data=worker.process(None);counter+=1
                    if len(data)==25:
                        sequence=int.from_bytes(data[19:23],'big')
                        if data[4] and data[5]:event=int.from_bytes(data[11:15],'big')
                        payloads.append(data)
                    stopped.wait(.005)
            except Exception as exc:errors.append(exc)
        with patch.dict(sys.modules,gtuner=SimpleNamespace(gpc_string=echo)):
            thread=threading.Thread(target=callbacks,daemon=True);thread.start()
            service=BridgeService(worker.socket.getsockname()[1]);sdk=None
            try:
                service.configure(enabled=True,expected_slot=2)
                self.assertTrue(service.verify(timeout=2,expected_slot=2)['ready'])
                service.allow_output(True)
                gate=GreenGate(green_config(dict(armed=True,max_age_ms=250)))
                fixture=green_fixtures.ColorTests();fixture.setUp()
                with tempfile.TemporaryDirectory() as folder:
                    config=Path(folder)/'bridge.json';service.write_config(config);sdk=BridgeServiceClient(config)
                    candidates=0
                    for front in (70,33,33):
                        captured=time.time();frame=fixture.frame(front=front)
                        fill=estimate_fill(frame,fixture.box)
                        measured=measure_green(frame,fixture.box,fill,gate.config)
                        state=dict(status='detected',confidence=.9,shot_id=1,fill=fill)
                        state['trigger']=gate.update(state,measured,captured)
                        candidates+=int(state['trigger']['candidate'])
                        sdk.send(state,observed_at=captured)
                        if front==70:
                            self.assertFalse(measured['in_green'])
                            self.assertFalse(any(p[5] for p in payloads))
                    self.assertEqual(candidates,1)
                    self.assertTrue(wait_for(lambda:any(p[4] and p[5] for p in payloads)),errors)
                    event_ids={int.from_bytes(p[11:15],'big') for p in payloads if p[4] and p[5]}
                    self.assertEqual(event_ids,{1})
                    self.assertTrue(wait_for(lambda:service.status().get('device_event')==1))
                    service.allow_output(False);service.clear()
                    self.assertTrue(wait_for(lambda:payloads and payloads[-1][4]==0))
                self.assertFalse(errors)
            finally:
                if sdk:sdk.close()
                service.close();stopped.set();thread.join(1);worker.__del__()
