"""Preserve an observed green pulse without extending its source deadline."""
import unittest
from bridge_service import TriggerLatch,neutral_bridge_state


def packet(event=5,green=True,armed=True,mode='timing',status='detected'):
    state=neutral_bridge_state();state.update(status=status,shot_id=1,fill=.8)
    state['trigger'].update(event_id=event,green=green,eligible=green,valid=True,
                            armed=armed,shot_mode=mode,max_age_ms=100)
    return state


class TriggerDeliveryTests(unittest.TestCase):
    def test_non_green_frame_does_not_erase_pending_observed_event(self):
        latch=TriggerLatch();latch.offer(packet(),10)
        latch.offer(packet(green=False),10.016)
        state,stamp=latch.peek(10.067)
        self.assertTrue(state['trigger']['green']);self.assertEqual(stamp,10)

    def test_repeated_event_cannot_refresh_its_original_source_deadline(self):
        latch=TriggerLatch();latch.offer(packet(),10)
        latch.offer(packet(),10.06)
        self.assertEqual(latch.peek(10.07)[1],10)
        self.assertIsNone(latch.peek(10.076))
        latch.offer(packet(),10.12)
        self.assertIsNone(latch.peek(10.12))
        latch.offer(packet(event=6),10.13)
        self.assertEqual(latch.peek(10.14)[0]['trigger']['event_id'],6)

    def test_disarm_and_mode_change_cancel_pending_event(self):
        for replacement in (packet(green=False,armed=False),packet(green=False,mode='rhythm')):
            latch=TriggerLatch();latch.offer(packet(),10);latch.offer(replacement,10.01)
            self.assertIsNone(latch.peek(10.02))

    def test_final_absence_or_prediction_does_not_erase_an_already_observed_event(self):
        for status in ('predicted','absent'):
            latch=TriggerLatch();latch.offer(packet(),10)
            latch.offer(packet(green=False,status=status),10.02)
            state,stamp=latch.peek(10.06)
            self.assertEqual(state['status'],'detected');self.assertEqual(stamp,10)
            fresh=TriggerLatch();fresh.offer(packet(status=status),10)
            self.assertIsNone(fresh.peek(10.01))

    def test_async_hop_takes_event_once_with_unchanged_timestamp(self):
        latch=TriggerLatch();latch.offer(packet(),10);latch.offer(packet(green=False),10.01)
        self.assertEqual(latch.pop(10.02)[1],10)
        self.assertIsNone(latch.pop(10.03))

    def test_unconfirmed_or_unarmed_green_cannot_create_pending_command(self):
        for flag in ('armed','eligible','valid','green'):
            state=packet();state['trigger'][flag]=False
            latch=TriggerLatch();latch.offer(state,10)
            self.assertIsNone(latch.peek(10.01))
