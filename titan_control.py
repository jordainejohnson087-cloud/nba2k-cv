"""Gtuner IV slot controls through Windows accessibility and documented shortcuts.

No USB protocol guessing, slot programming, or invented slot labels. Device
verification comes separately from the GPC heartbeat and v2 payload echo.
"""
import os
import re
import sys
from pathlib import Path


def slot_number(value):
    if isinstance(value,bool) or str(value) not in tuple(str(i) for i in range(1,10)):
        raise ValueError('Choose a Titan memory slot from 1 to 9')
    return int(value)


def parse_slot_rows(rows):
    """Accept only explicit slot rows, leaving missing or ambiguous names unknown."""
    found={}
    for row in rows:
        text='\n'.join(str(part).strip() for part in row if str(part).strip())
        match=re.fullmatch(r'(?:Slot\s*)?([1-9])\s*(?:[\n:–—-]\s*)([^\n]+)(?:\n[vV]?\d[\d.]*\s*)?',text)
        if not match:continue
        number=int(match[1]);name=match[2].strip()
        if name.lower() in ('run','configure','remove','delete','slot'):continue
        found.setdefault(number,set()).add(name)
    return {i:(next(iter(names)) if len(names)==1 else None) for i,names in found.items()}


def choose_meter_slot(slots):
    names={'nba2k cv meter bridge','titan_meter.gpc'}
    matches=[number for number,name in slots.items() if isinstance(name,str) and name.strip().lower() in names]
    if len(matches)!=1:
        raise RuntimeError('Cannot identify one meter script slot from Gtuner labels. Select its slot number explicitly in the app.')
    return slot_number(matches[0])


class GtunerController:
    def __init__(self, executable, window=None):
        self.executable=Path(executable)
        self.window=window  # Injectable accessibility wrapper for tests.
        self.com_initialized=False

    def connect(self):
        if self.window is not None:return self.window
        if sys.platform!='win32':raise RuntimeError('Titan slot control requires Windows and Gtuner IV.')
        if self.executable.name.lower()!='gtuner.exe' or not self.executable.is_file():
            raise ValueError('Select the installed Gtuner.exe first.')
        try:
            import psutil
            from pywinauto import Desktop
            import comtypes
            if not self.com_initialized:
                comtypes.CoInitializeEx(0);self.com_initialized=True
        except ImportError as exc:
            raise RuntimeError('Restart with Launch NBA2K CV.bat to install the Windows slot-control dependency.') from exc
        candidates=[]
        for window in Desktop(backend='uia').windows(title_re=r'.*Gtuner IV.*',visible_only=True):
            try:
                actual=psutil.Process(window.process_id()).exe()
                if os.path.normcase(os.path.realpath(actual))==os.path.normcase(str(self.executable.resolve())):
                    candidates.append(window)
            except (psutil.Error,OSError):continue
        if len(candidates)!=1:
            raise RuntimeError('Open exactly one Gtuner IV window for the selected executable, then reconnect.')
        self.window=candidates[0]
        return self.window

    def close(self):
        self.window=None
        if self.com_initialized:
            import comtypes
            comtypes.CoUninitialize();self.com_initialized=False

    def read_slots(self):
        window=self.connect()
        panes=[node for node in window.descendants() if node.window_text().strip()=='Device Memory Slots']
        if len(panes)!=1:
            return {i:None for i in range(1,10)}
        rows=[]
        for node in [panes[0],*panes[0].descendants()]:
            # Limit each candidate to its own label and direct Text children.
            parts=[node.window_text()]
            parts.extend(child.window_text() for child in node.children()
                         if child.element_info.control_type in ('Text','Button'))
            rows.append(list(dict.fromkeys(part for part in parts if part)))
        parsed=parse_slot_rows(rows)
        return {i:parsed.get(i) for i in range(1,10)}

    def select_slot(self, slot):
        slot=slot_number(slot)
        window=self.connect()
        window.set_focus()
        # A shortcut must never be typed into another application.
        if not self._is_foreground(window):
            raise RuntimeError('Gtuner could not receive focus; slot selection was not sent.')
        window.type_keys('^'+str(slot),set_foreground=False,pause=.05)
        return slot  # Request sent; this is NOT device confirmation.

    @staticmethod
    def _is_foreground(window):
        import win32gui
        import win32process
        return win32process.GetWindowThreadProcessId(win32gui.GetForegroundWindow())[1]==window.process_id()
