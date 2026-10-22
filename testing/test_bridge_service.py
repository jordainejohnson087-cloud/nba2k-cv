import http.client
import json
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from bridge_service import BridgeService,BridgeServiceClient,neutral_bridge_state


def wait_until(predicate,timeout=2):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        if predicate():return True
        time.sleep(.01)
    return False


class BridgeApiTests(unittest.TestCase):
    def test_live_api_does_not_claim_a_missing_gtuner_worker_is_connected(self):
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as unused:
            unused.bind(('127.0.0.1',0));port=unused.getsockname()[1]
        service=BridgeService(port)
        try:
            self.assertFalse(service.verify(timeout=.1)['ready'])
            self.assertEqual(service.status()['stage'],'Gtuner CV worker not receiving')
            connection=http.client.HTTPConnection('127.0.0.1',service.server.server_port,timeout=1)
            connection.request('GET','/status',headers={'Authorization':'Bearer incorrect'})
            response=connection.getresponse();response.read()
            self.assertEqual(response.status,403);connection.close()
        finally:service.close()

    def test_persistent_session_idle_safety_and_sdk_round_trip(self):
        udp=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);udp.bind(('127.0.0.1',0));udp.settimeout(.1)
        stopped=threading.Event();received=[]
        def device_simulator():
            counter=0
            while not stopped.is_set():
                try:data,peer=udp.recvfrom(4096)
                except socket.timeout:continue
                packet=json.loads(data);received.append(packet);counter+=1
                ack=dict(session=packet['session'],seq=packet['seq'],hardware_counter=counter,
                         hardware_age_ms=0,worker_protocol=3,device_protocol=3,device_sequence=packet['seq'],device_event=0,active_slot=4)
                udp.sendto(json.dumps(ack).encode(),peer)
        thread=threading.Thread(target=device_simulator,daemon=True);thread.start()
        service=BridgeService(udp.getsockname()[1]);sdk=None
        try:
            service.configure(enabled=True,expected_slot=4)
            self.assertTrue(service.verify(expected_slot=4)['ready'])
            self.assertTrue(all(not packet['trigger']['armed'] for packet in received))
            self.assertFalse(service.verify(timeout=.1,expected_slot=5)['ready'])
            with tempfile.TemporaryDirectory() as folder:
                config=Path(folder)/'bridge.json';service.write_config(config);sdk=BridgeServiceClient(config)
                self.assertTrue(sdk.status()['ready'])
                state=neutral_bridge_state();state.update(status='detected',fill=.9,shot_id=1)
                state['trigger'].update(armed=True,green=True,eligible=True,valid=True,event_id=1)
                service.allow_output(True);sdk.send(state)
                self.assertTrue(wait_until(lambda:any(p['trigger']['armed'] for p in received)))
                self.assertTrue(wait_until(lambda:received and not received[-1]['trigger']['armed']))
                self.assertEqual(len({p['session'] for p in received}),1)
                connection=http.client.HTTPConnection('127.0.0.1',service.server.server_port,timeout=1)
                connection.request('POST','/state',json.dumps({'state':state,'captured_at':float('nan')}),
                                   headers={'Authorization':'Bearer '+service.token})
                response=connection.getresponse();response.read();self.assertEqual(response.status,400);connection.close()
        finally:
            if sdk:sdk.close()
            service.close();stopped.set();thread.join(1);udp.close()

    def test_sdk_rejects_non_loopback_api_configuration(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'config.json';path.write_text(json.dumps(dict(url='http://example.com',token='x'*32)))
            with self.assertRaises(ValueError):BridgeServiceClient(path)

    def test_old_worker_or_device_revision_cannot_enable_output(self):
        service=BridgeService()
        try:
            service.client.seq=service.first_sequence
            for worker,device in ((None,None),(2,3),(3,2),(3,None)):
                service.client.status=lambda:dict(receiver='receiving',hardware='heartbeat verified',
                    worker_protocol=worker,device_protocol=device,active_slot=2,
                    device_sequence=service.first_sequence,device_event=0)
                self.assertFalse(service.status()['ready'])
                self.assertIn('version mismatch',service.status()['stage'])
        finally:service.close()

    def test_old_device_sequence_cannot_verify_a_new_session(self):
        service=BridgeService()
        try:
            service.client.status=lambda:dict(receiver='receiving',hardware='heartbeat verified',
                worker_protocol=3,device_protocol=3,active_slot=4,device_sequence=service.first_sequence-1,device_event=0)
            self.assertFalse(service.status()['ready'])
            self.assertEqual(service.status()['stage'],'Titan script payload echo missing')
            service.client.seq=service.first_sequence
            service.client.status=lambda:dict(receiver='receiving',hardware='heartbeat verified',
                worker_protocol=3,device_protocol=3,active_slot=4,device_sequence=service.first_sequence,device_event=0)
            self.assertTrue(service.status()['ready'])
        finally:service.close()

    def test_enable_changes_preserve_slot_and_missing_slot_never_becomes_ready(self):
        service=BridgeService()
        try:
            service.configure(expected_slot=4)
            service.configure(enabled=True)
            self.assertEqual(service.expected_slot,4)
            service.configure(enabled=False)
            self.assertEqual(service.expected_slot,4)
            service.configure(expected_slot=None)
            service.client.status=lambda:dict(receiver='receiving',hardware='heartbeat verified',
                worker_protocol=3,device_protocol=3,active_slot=None,device_sequence=service.client.seq,device_event=0)
            self.assertFalse(service.status()['ready'])
            self.assertFalse(service.verify(timeout=.1)['ready'])
            for value in (0,10,True,'4'):
                with self.assertRaises(ValueError):service.configure(expected_slot=value)
        finally:service.close()
