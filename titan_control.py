"""Gtuner IV slot controls through Windows accessibility and documented shortcuts.

No USB protocol guessing, slot programming, or invented slot labels. Device
verification comes separately from the GPC heartbeat and v2 payload echo.
"""
import os
import re
import sys
from pathlib import Path


def valid_gtuner_path(value):
    try:
        path=Path(value).expanduser()
        return path.name.lower()=='gtuner.exe' and path.is_file()
    except (OSError,TypeError,ValueError):return False


def discover_gtuner(saved='',roots=None,process_paths=None,registry_paths=None):
    """Prefer user choice, then a running copy, then bounded common locations."""
    if valid_gtuner_path(saved):return Path(saved).resolve()
    if process_paths is None:
        process_paths=[]
        if sys.platform=='win32':
            try:
                import psutil
                for process in psutil.process_iter(['name','exe']):
                    try:
                        if (process.info.get('name') or '').lower()=='gtuner.exe':
                            process_paths.append(process.info.get('exe') or '')
                    except (psutil.Error,OSError):pass
            except ImportError:pass
    for value in process_paths:
        if valid_gtuner_path(value):return Path(value).resolve()
    if registry_paths is None:
        registry_paths=[]
        if sys.platform=='win32':
            import winreg
            for hive in (winreg.HKEY_CURRENT_USER,winreg.HKEY_LOCAL_MACHINE):
                for access in (winreg.KEY_READ|winreg.KEY_WOW64_64KEY,winreg.KEY_READ|winreg.KEY_WOW64_32KEY):
                    try:
                        with winreg.OpenKey(hive,r'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\Gtuner.exe',0,access) as key:
                            value,_=winreg.QueryValueEx(key,'')
                            registry_paths.append(os.path.expandvars(value.strip().strip('"')))
                    except OSError:pass
    for value in registry_paths:
        if valid_gtuner_path(value):return Path(value).resolve()
    if roots is None:
        home=Path.home()
        roots=[home/'Downloads',home/'Desktop',home/'Documents',Path(__file__).resolve().parent]
        roots += [Path(value) for key in ('ProgramFiles','ProgramFiles(x86)','LOCALAPPDATA')
                  if (value:=os.environ.get(key))]
    candidates=[]
    for root in map(Path,roots):
        for relative in ('Gtuner.exe','GtunerIV/Gtuner.exe','Gtuner IV/Gtuner.exe',
                         'Gtuner/Gtuner.exe','ConsoleTuner/GtunerIV/Gtuner.exe','Programs/GtunerIV/Gtuner.exe'):
            candidate=root/relative
            if valid_gtuner_path(candidate):candidates.append(candidate)
        # Portable ZIPs commonly extract into a versioned Gtuner folder.
        # Never walk every file in Downloads or Program Files.
        try:
            import itertools
            folders=[p for p in itertools.islice(root.iterdir(),500)
                     if p.is_dir() and ('gtuner' in p.name.lower() or p.name.lower()=='consoletuner')]
        except OSError:continue
        visited=0
        while folders and visited<200:
            folder=folders.pop();visited+=1
            try:
                if len(folder.relative_to(root).parts)>4:continue
                for child in itertools.islice(folder.iterdir(),200):
                    if valid_gtuner_path(child):candidates.append(child)
                    elif child.is_dir():folders.append(child)
            except OSError:continue
    def modified(path):
        try:return path.stat().st_mtime
        except OSError:return 0
    return max(candidates,key=modified).resolve() if candidates else None


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

    def start_cv(self,script='gtuner_meter',active=lambda:True):
        """Request CV startup only through unambiguous accessible controls.

        Qt accessibility varies between Gtuner builds. Missing labels are a
        setup limitation, never a reason to guess screen coordinates or keys.
        """
        window=self.connect()
        panes=[node for node in window.descendants()
               if node.window_text().strip()=='Computer Vision' and
               node.element_info.control_type in ('Pane','Group')]
        if len(panes)!=1:raise RuntimeError('Open Gtuner Computer Vision once; its panel is not exposed to the app.')
        nodes=panes[0].descendants()
        combos=[]
        for node in nodes:
            if node.element_info.control_type!='ComboBox':continue
            labels=[text.strip() for text in node.texts()]
            if script in labels or script+'.py' in labels or 'Video Input Check' in labels:combos.append(node)
        if len(combos)!=1:raise RuntimeError('Choose gtuner_meter in Gtuner CV once; its script selector is not identifiable.')
        buttons=[node for node in nodes if node.element_info.control_type=='Button' and
            node.window_text().strip().lower() in ('start','run','start computer vision','run computer vision','start gcv script')
            and node.is_enabled()]
        if len(buttons)!=1:raise RuntimeError('Start gtuner_meter in Gtuner CV once; the app cannot identify its play control.')
        if not active():raise RuntimeError('CV startup cancelled')
        labels=[text.strip() for text in combos[0].texts()]
        combos[0].select(script+'.py' if script+'.py' in labels and script not in labels else script)
        if not active():raise RuntimeError('CV startup cancelled')
        buttons[0].click()
        return True  # Request only. The UDP receiver and Titan echo verify success.

    @staticmethod
    def _is_foreground(window):
        import win32gui
        import win32process
        return win32process.GetWindowThreadProcessId(win32gui.GetForegroundWindow())[1]==window.process_id()


def launch_gtuner_background(executable,folder):
    """Reuse a running host; only a missing host is launched, minimized."""
    import subprocess
    if sys.platform!='win32':raise RuntimeError('Gtuner host startup requires Windows')
    if not valid_gtuner_path(executable):raise ValueError('Gtuner IV executable was not found')
    import psutil
    selected=os.path.normcase(str(Path(executable).resolve()))
    matches=[]
    for process in psutil.process_iter(['exe']):
        try:
            if process.info['exe'] and os.path.normcase(str(Path(process.info['exe']).resolve()))==selected:
                matches.append(process.pid)
        except (psutil.Error,OSError):pass
    if len(matches)>1:raise RuntimeError('More than one matching Gtuner instance is open')
    if matches:return False
    startup=subprocess.STARTUPINFO();startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow=7  # SW_SHOWMINNOACTIVE, without stealing foreground focus.
    folder=Path(folder)
    subprocess.Popen([str(executable),str(folder/'titan_meter.gpc'),str(folder/'gtuner_meter.py')],
                     cwd=Path(executable).parent,startupinfo=startup)
    return True


# Titan Two's documented normal/bootloader product IDs. Presence is not a
# verified console connection or permission to overwrite a memory slot.
TITAN_USB_IDS={'0032':'normal','8032':'bootloader'}


def _present_usb_nodes():
    """Read present Windows USB devnodes without opening a HID/device handle."""
    import ctypes as c
    from ctypes import wintypes as w
    class GUID(c.Structure):
        _fields_=[('Data1',c.c_uint32),('Data2',c.c_uint16),('Data3',c.c_uint16),('Data4',c.c_ubyte*8)]
    class DEVINFO(c.Structure):
        _fields_=[('cbSize',c.c_uint32),('ClassGuid',GUID),('DevInst',c.c_uint32),('Reserved',c.c_size_t)]
    setup=c.WinDLL('setupapi',use_last_error=True)
    cfg=c.WinDLL('cfgmgr32',use_last_error=True)
    setup.SetupDiGetClassDevsW.argtypes=[c.c_void_p,w.LPCWSTR,w.HWND,w.DWORD]
    setup.SetupDiGetClassDevsW.restype=w.HANDLE
    setup.SetupDiEnumDeviceInfo.argtypes=[w.HANDLE,w.DWORD,c.POINTER(DEVINFO)]
    setup.SetupDiEnumDeviceInfo.restype=w.BOOL
    setup.SetupDiGetDeviceInstanceIdW.argtypes=[w.HANDLE,c.POINTER(DEVINFO),w.LPWSTR,w.DWORD,c.POINTER(w.DWORD)]
    setup.SetupDiGetDeviceInstanceIdW.restype=w.BOOL
    setup.SetupDiDestroyDeviceInfoList.argtypes=[w.HANDLE]
    setup.SetupDiDestroyDeviceInfoList.restype=w.BOOL
    cfg.CM_Get_DevNode_Status.argtypes=[c.POINTER(w.ULONG),c.POINTER(w.ULONG),w.ULONG,w.ULONG]
    cfg.CM_Get_DevNode_Status.restype=w.ULONG
    handle=setup.SetupDiGetClassDevsW(None,'USB',None,0x02|0x04)  # PRESENT | ALLCLASSES
    if handle==c.c_void_p(-1).value:raise c.WinError(c.get_last_error())
    rows=[]
    try:
        index=0
        while True:
            info=DEVINFO();info.cbSize=c.sizeof(info)
            if not setup.SetupDiEnumDeviceInfo(handle,index,c.byref(info)):
                error=c.get_last_error()
                if error==259:break  # ERROR_NO_MORE_ITEMS
                raise c.WinError(error)
            index+=1
            instance=c.create_unicode_buffer(4096);needed=w.DWORD()
            if not setup.SetupDiGetDeviceInstanceIdW(handle,c.byref(info),instance,len(instance),c.byref(needed)):
                raise c.WinError(c.get_last_error())
            if not re.match(r'^USB\\VID_2508&PID_(0032|8032)(?=[&\\])',instance.value,re.I):continue
            flags=w.ULONG();problem=w.ULONG()
            result=cfg.CM_Get_DevNode_Status(c.byref(flags),c.byref(problem),info.DevInst,0)
            rows.append(dict(instance_id=instance.value,present=True,
                problem_code=(int(problem.value) if flags.value&0x400 else 0) if result==0 else None,
                started=bool(flags.value&0x08) if result==0 else None))
    finally:setup.SetupDiDestroyDeviceInfoList(handle)
    return rows


def titan_hardware_inventory(rows=None):
    """Only live USB roots count; serials/instance paths never leave the parser."""
    import hashlib
    if rows is None:
        if sys.platform!='win32':
            return dict(state='unsupported',devices=[],auto_connect=False,
                        message='USB hardware discovery requires Windows; bridge status is separate.')
        try:rows=_present_usb_nodes()
        except (OSError,ImportError) as exc:
            return dict(state='unavailable',devices=[],auto_connect=False,
                        message='Windows USB discovery failed: '+str(exc))
    devices={}
    for row in rows:
        instance=str(row.get('instance_id','')).upper()
        match=re.match(r'^USB\\VID_2508&PID_(0032|8032)(?=[&\\])',instance)
        # Ignore composite interfaces: their parent already represents the unit.
        parts=instance.split('\\')
        if not match or row.get('present') is not True or len(parts)!=3 or '&MI_' in parts[1]:continue
        key=hashlib.sha256(instance.encode()).hexdigest()[:16]
        problem=row.get('problem_code')
        if type(problem) is not int or problem<0:problem=None
        devices[key]=dict(id=key,usb_id='2508:'+match[1],mode=TITAN_USB_IDS[match[1]],
                          problem_code=problem,started=row.get('started') is True)
    devices=sorted(devices.values(),key=lambda d:d['id'])
    if not devices:
        state='missing';message='Titan Two not found by Windows. Connect PROG with a USB data cable; try a direct PC USB port.'
    elif len(devices)>1:
        state='multiple';message='Multiple Titan Two devices found. Keep one connected before automatic setup; no device or slot will be guessed.'
    elif devices[0]['mode']=='bootloader':
        state='bootloader';message='Titan Two detected in bootloader mode. Complete firmware recovery in Gtuner IV before connecting the app bridge.'
    elif devices[0]['problem_code'] not in (0,None):
        state='driver_problem';message=f"Titan Two detected; Windows reports device problem {devices[0]['problem_code']}. Check Device Manager and Windows driver installation."
    elif devices[0]['problem_code'] is None or not devices[0]['started']:
        state='not_started';message='Titan Two is present, but Windows has not confirmed its driver started. Wait for device setup or check Device Manager.'
    else:
        state='available';message='Titan Two USB detected; Windows device started. GPC echo and console output still require separate checks.'
    return dict(state=state,devices=devices,auto_connect=state=='available',message=message)
