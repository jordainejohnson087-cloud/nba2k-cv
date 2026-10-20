import unittest
from unittest.mock import Mock,patch
from titan_control import GtunerController,parse_slot_rows,slot_number,choose_meter_slot


class TitanControlTests(unittest.TestCase):
    def test_reads_only_explicit_unique_slot_labels(self):
        names=parse_slot_rows([['1','NBA2K CV Meter Bridge','2.0'],['Slot 2: Practice'],
                              ['3','Run'],['4','First'],['4','Conflicting'],['9','Empty']])
        self.assertEqual(names[1],'NBA2K CV Meter Bridge')
        self.assertEqual(names[2],'Practice')
        self.assertNotIn(3,names)
        self.assertIsNone(names[4])
        self.assertNotIn(8,names)

    def test_selection_checks_target_focus_and_uses_documented_shortcut(self):
        window=Mock()
        controller=GtunerController('Gtuner.exe',window=window)
        with patch.object(controller,'_is_foreground',return_value=True):
            self.assertEqual(controller.select_slot('9'),9)
        window.type_keys.assert_called_once_with('^9',set_foreground=False,pause=.05)
        window.reset_mock()
        with patch.object(controller,'_is_foreground',return_value=False):
            with self.assertRaisesRegex(RuntimeError,'focus'):controller.select_slot(1)
        window.type_keys.assert_not_called()
        for value in (0,10,True,'1;anything','01'):
            with self.assertRaises(ValueError):slot_number(value)

    def test_auto_selector_requires_one_identified_meter_script(self):
        self.assertEqual(choose_meter_slot({1:'Other script',4:'NBA2K CV Meter Bridge'}),4)
        for names in ({},{1:None},{1:'NBA2K CV Meter Bridge',2:'titan_meter.gpc'}):
            with self.assertRaises(RuntimeError):choose_meter_slot(names)

    def test_missing_accessibility_labels_are_not_invented(self):
        window=Mock();window.descendants.return_value=[]
        self.assertEqual(GtunerController('Gtuner.exe',window).read_slots(),dict.fromkeys(range(1,10)))
