"""Exercise the actual model-free child process and failure isolation."""
import json
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from bridge_service import (BridgeProcess, AsyncBridgeServiceClient, neutral_bridge_state,
                            unavailable_bridge)

RUNTIME=Path(__file__).resolve().parents[1]/'run_live.py'


def wait_for(check,seconds=4):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        if check():return True
        time.sleep(.01)
    return False


class BridgeProcessTests(unittest.TestCase):
    def test_independent_host_round_trip_and_slot_guard(self):
        udp=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);udp.bind(('127.0.0.1',0));udp.settimeout(.1)
        stopped=threading.Event();packets=[]
        def receiver():
            counter=0
            while not stopped.is_set():
                try:data,peer=udp.recvfrom(4096)
                except socket.timeout:continue
                packet=json.loads(data);packets.append(packet);counter+=1
                ack=dict(session=packet['session'],seq=packet['seq'],hardware_counter=counter,
                    hardware_age_ms=0,device_sequence=packet['seq'],device_event=0,active_slot=2)
                udp.sendto(json.dumps(ack).encode(),peer)
        receiver_thread=threading.Thread(target=receiver,daemon=True);receiver_thread.start()
        host=BridgeProcess(sys.executable,RUNTIME,udp.getsockname()[1])
        try:
            host.configure(enabled=True,expected_slot=2)
            self.assertTrue(host.verify(timeout=2,expected_slot=2)['ready'])
            self.assertTrue(all(not p['trigger']['armed'] for p in packets))
            self.assertNotEqual(host.process.pid,__import__('os').getpid())
            with tempfile.TemporaryDirectory() as folder:
                config=Path(folder)/'client.json';host.write_config(config)
                sdk=AsyncBridgeServiceClient(config)
                try:
                    self.assertTrue(wait_for(lambda:sdk.status()['ready']))
                    host.allow_output(True)
                    state=neutral_bridge_state();state.update(status='detected',fill=.9,shot_id=1)
                    state['trigger'].update(armed=True,green=True,eligible=True,valid=True,event_id=1,max_age_ms=250)
                    sdk.send(state)
                    self.assertTrue(wait_for(lambda:any(packet['trigger']['armed'] for packet in packets)))
                    self.assertTrue(wait_for(lambda:packets and not packets[-1]['trigger']['armed']))
                finally:sdk.close()
            host.allow_output(False)
            # A long verification must not starve the process-owner lease.
            self.assertFalse(host.verify(timeout=3,expected_slot=3)['ready'])
            self.assertIsNone(host.process.poll())
            host.configure(expected_slot=3)
            self.assertTrue(wait_for(lambda:'does not match' in host.status()['stage']))
            self.assertFalse(host.status()['ready'])
            host.process.kill();host.process.wait(timeout=2)
            self.assertFalse(host.status()['ready'])
            self.assertIn('exited',host.status()['stage'])
        finally:
            host.close();stopped.set();receiver_thread.join(1);udp.close()
        self.assertFalse(host.folder.exists())

    def test_host_exits_after_parent_lease_is_lost(self):
        host=BridgeProcess(sys.executable,RUNTIME)
        try:
            self.assertTrue(wait_for(host.config.exists))
            # Simulate a supervisor crash: stop its threads without terminating child.
            host.stopped.set();host.thread.join(1);host.heartbeat.join(1)
            host.lease.unlink(missing_ok=True)
            host.process.wait(timeout=3)
            self.assertEqual(host.process.returncode,0)
        finally:
            host.stopped.clear();host.close()

    def test_engine_reloads_restarted_host_configuration(self):
        seen=[]
        class Client:
            def __init__(self,config):seen.append(Path(config).read_text())
            def _request(self,*args):return unavailable_bridge('test')
            def send(self,*args):return unavailable_bridge('test')
            def close(self):pass
        with tempfile.TemporaryDirectory() as folder, patch('bridge_service.BridgeServiceClient',Client):
            config=Path(folder)/'config.json'
            client=AsyncBridgeServiceClient(config)
            try:
                config.write_text('first-host')
                self.assertTrue(wait_for(lambda:'first-host' in seen))
                config.write_text('replacement-host')
                self.assertTrue(wait_for(lambda:'replacement-host' in seen))
                self.assertTrue(client.thread.is_alive())
            finally:client.close()

    def test_inference_sender_never_waits_for_blocked_http(self):
        entered=threading.Event();release=threading.Event()
        class BlockedClient:
            def __init__(self,config):entered.set();release.wait(2)
            def _request(self,*args):return unavailable_bridge('test')
            def send(self,*args):return unavailable_bridge('test')
            def close(self):pass
        with tempfile.TemporaryDirectory() as folder, patch('bridge_service.BridgeServiceClient',BlockedClient):
            config=Path(folder)/'config.json';config.write_text('{}')
            client=AsyncBridgeServiceClient(config)
            try:
                self.assertTrue(entered.wait(1))
                state=neutral_bridge_state()
                client.send(state)  # Must complete while constructor is still blocked.
                self.assertFalse(release.is_set())
                for i in range(30):client.send(state)
                self.assertIsNotNone(client.latest)
                self.assertEqual(len(client.latest),2)  # One state and timestamp, not a FIFO.
            finally:release.set();client.close()
