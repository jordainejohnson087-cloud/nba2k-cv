import importlib.util
import json
import struct
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from meter_green import GreenGate,green_config,measure_green
from run_video import estimate_fill
spec=importlib.util.spec_from_file_location('green_gcv',Path(__file__).resolve().parents[1]/'bridge/gtuner_meter.py')
gcv=importlib.util.module_from_spec(spec);spec.loader.exec_module(gcv)


def state(kind='detected',shot=1,confidence=.9):
    return dict(status=kind,shot_id=shot,confidence=confidence)


def measurement(inside):
    return dict(valid=True,in_green=inside,frontier_y=22 if inside else 60,zone_bottom=25)


class GateTests(unittest.TestCase):
    def test_requires_approach_and_only_one_event_across_fragmented_ids(self):
        gate=GreenGate(green_config())
        self.assertFalse(gate.update(state(),measurement(True),10,now=10)['candidate'])
        gate.update(state(),measurement(False),10,now=10)
        self.assertTrue(gate.update(state(),measurement(True),10,now=10)['candidate'])
        for shot in (1,2,3):
            result=gate.update(state(shot=shot),measurement(True),10,now=10)
            self.assertFalse(result['candidate']);self.assertEqual(result['event_id'],1)
        gate.update(state('absent'),{},11,now=11)
        gate.update(state(shot=4),measurement(False),11.01,now=11.01)
        self.assertTrue(gate.update(state(shot=4),measurement(True),11.02,now=11.02)['candidate'])

    def test_predictions_low_confidence_and_stale_frames_do_not_fire(self):
        for kind,confidence,captured in [('predicted',.9,10),('detected',.2,10),('detected',.9,9)]:
            gate=GreenGate(green_config())
            gate.update(state(),measurement(False),10,now=10)
            result=gate.update(state(kind,confidence=confidence),measurement(True),captured,now=10)
            self.assertFalse(result['candidate']);self.assertFalse(result['valid'])

    def test_uncertainty_breaks_confirmation_streak(self):
        gate=GreenGate(green_config(dict(confirmations=2)))
        gate.update(state(),measurement(False),10,now=10)
        self.assertFalse(gate.update(state(),measurement(True),10,now=10)['candidate'])
        gate.update(state(),dict(valid=False),10,now=10)
        self.assertFalse(gate.update(state(),measurement(True),10,now=10)['candidate'])
        self.assertTrue(gate.update(state(),measurement(True),10,now=10)['candidate'])

    def test_arming_requires_a_new_approach(self):
        gate=GreenGate(green_config())
        gate.update(state(),measurement(False),10,now=10)
        gate.update(state(),measurement(True),10,now=10)
        gate.configure(dict(gate.config,armed=True))
        result=gate.update(state(),measurement(True),10,now=10)
        self.assertFalse(result['eligible']);self.assertFalse(result['candidate'])

    def test_configuration_rejects_bad_and_nonfinite_values(self):
        for overrides in [dict(hue_low=100,hue_high=50),dict(armed='yes'),dict(delay_ms=-1),
                          dict(hold_ms=0),dict(row_coverage=float('nan')),dict(direction='left')]:
            with self.assertRaises(ValueError):green_config(overrides)


class ProtocolTests(unittest.TestCase):
    def packet(self,armed=True):
        trigger=dict(version=2,armed=armed,green=True,valid=True,eligible=True,event_id=42,
                     delay_ms=45,hold_ms=100,max_age_ms=100,direction='up')
        return dict(version=2,status='detected',fill=.8,seq=19,shot_id=8,sent_at=time.time(),trigger=trigger)

    def test_packet_offsets_match_gpc_and_byte_zero_cannot_trigger_onyx(self):
        packet=self.packet();data=gcv.encode(packet,True)
        self.assertEqual(len(data),25)
        self.assertEqual(list(data[:7]),[0,77,2,1,1,1,0])
        self.assertEqual(struct.unpack('>HHIII',data[7:23]),(45,100,42,8,19))
        self.assertTrue(25<=struct.unpack('>H',data[23:])[0]<=100)

    def test_dry_run_stale_predicted_or_unconfirmed_never_triggers(self):
        for change in ('dry','stale','predicted','unconfirmed'):
            packet=self.packet()
            if change=='dry':packet['trigger']['armed']=False
            if change=='stale':packet['sent_at']-=1
            if change=='predicted':packet['status']='predicted'
            if change=='unconfirmed':packet['trigger']['eligible']=False
            data=gcv.encode(packet,True)
            self.assertEqual(data[5],0)
        self.assertEqual(gcv.encode(self.packet(),False)[4:6],b'\x00\x00')

    def test_invalid_wire_settings_are_rejected(self):
        for key,value in [('delay_ms',10000),('hold_ms',True),('event_id',-1),('green',1)]:
            packet=self.packet();packet['trigger'][key]=value
            with self.assertRaises(ValueError):gcv.encode(packet,True)


class ColorTests(unittest.TestCase):
    def setUp(self):
        try:
            import cv2,numpy as np
        except ImportError:self.skipTest('OpenCV not installed')
        self.np=np
        self.box=(20,10,60,110)

    def frame(self,front=33,second_band=False):
        frame=self.np.zeros((120,80,3),dtype=self.np.uint8)
        frame[30:36,27:53]=(0,255,0)
        if second_band:frame[45:50,27:53]=(0,255,0)
        frame[front:101,31:49]=(255,255,255)
        return frame

    def test_synthetic_green_band_and_raw_fill_frontier(self):
        frame=self.frame()
        raw=estimate_fill(frame,self.box)
        result=measure_green(frame,self.box,raw,green_config())
        self.assertTrue(result['valid']);self.assertTrue(result['in_green'])
        self.assertEqual((result['zone_top'],result['zone_bottom']),(30,35))
        before=self.frame(front=70)
        result=measure_green(before,self.box,estimate_fill(before,self.box),green_config())
        self.assertTrue(result['valid']);self.assertFalse(result['in_green'])

    def test_missing_white_fill_or_multiple_green_bands_is_ambiguous(self):
        self.assertFalse(measure_green(self.frame(),self.box,None,green_config())['valid'])
        frame=self.frame(front=70,second_band=True)
        self.assertFalse(measure_green(frame,self.box,estimate_fill(frame,self.box),green_config())['valid'])


if __name__=='__main__':unittest.main()


class GpcReadContractTests(unittest.TestCase):
    def test_gpc_reads_bytes_through_a_typed_destination(self):
        import re
        source=(Path(__file__).resolve().parents[1]/'bridge'/'titan_meter.gpc').read_text()
        calls=re.findall(r'\bgcv_read\(([^)]*)\)',source)
        self.assertEqual(calls,['offset, &value'])
        offsets=[int(n) for n in re.findall(r'cv_byte\((\d+)\)',source)]
        self.assertTrue(offsets)
        self.assertTrue(all(0<=n<25 for n in offsets))
        self.assertIn('uint8 value;',source)
        self.assertIn('return value;',source)
