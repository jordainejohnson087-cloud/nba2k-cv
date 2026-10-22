"""Simple Windows desktop control panel for capture, detection, review, and updates."""
import json
import math
import os
import queue
import subprocess
import sys
import threading
import time
import traceback
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from app_update import (apply_update, configured_feed, current_version,
                        download_release, fetch_manifest, newest_download)
from app_model import import_model, managed_model, model_catalog
from run_live import green_config, RawMailbox, GtunerController, slot_number, choose_meter_slot, discover_gtuner, valid_gtuner_path, launch_gtuner_background, BridgeProcess, titan_hardware_inventory


PROJECT=Path(__file__).resolve().parent
SETTINGS=PROJECT/'app_settings.json'


def draw_meter_box(image, payload, frame_timestamp, now=None):
    """Scale source coordinates onto the preview; never paint a stale observation."""
    from PIL import ImageDraw
    now=time.time() if now is None else now
    try:
        observed=float(payload['captured_at'])
        status=payload['status']
        box=tuple(float(v) for v in payload['box'])
        width,height=payload['frame_size']
        values=(*box,observed,float(frame_timestamp),float(payload['timestamp']),width,height)
        if (len(box)!=4 or not all(math.isfinite(v) for v in values) or width<=0 or height<=0
                or status not in ('detected','predicted') or not 0<=now-payload['timestamp']<=.3
                or abs(frame_timestamp-observed)>.2):return image
        x1,y1,x2,y2=box
        if not 0<=x1<x2<=width or not 0<=y1<y2<=height:return image
        result=image.copy();draw=ImageDraw.Draw(result)
        scale_x,scale_y=result.width/width,result.height/height
        rectangle=(x1*scale_x,y1*scale_y,x2*scale_x,y2*scale_y)
        color='#00e5ff' if status=='detected' else '#ffbd59'
        draw.rectangle(rectangle,outline=color,width=2)
        draw.text((rectangle[0],max(0,rectangle[1]-14)),
                  'METER' if status=='detected' else 'PREDICTED',fill=color)
        return result
    except (KeyError,TypeError,ValueError,OverflowError):return image


class PreviewWorker:
    """Prepare only the newest frame off Tk's thread; no growing frame queue."""
    def __init__(self,path):
        self.path=path;self.size=(1280,720);self.latest=None;self.error=None
        self.lock=threading.Lock();self.stop=threading.Event();self.overlay_path=None
        self.thread=threading.Thread(target=self._run,daemon=True);self.thread.start()

    def _run(self):
        from PIL import Image
        mailbox=None;sequence=0
        try:
            while not self.stop.is_set() and mailbox is None:
                try:mailbox=RawMailbox(self.path)
                except (OSError,ValueError):self.stop.wait(.01)
            while not self.stop.is_set():
                sample=mailbox.read(sequence)
                if not sample:self.stop.wait(.002);continue
                started=time.perf_counter()
                image=Image.frombytes('RGB',(sample['width'],sample['height']),sample['bgr'],'raw','BGR')
                with self.lock:size=self.size;overlay_path=self.overlay_path
                scale=min(size[0]/image.width,size[1]/image.height)
                target=(max(1,round(image.width*scale)),max(1,round(image.height*scale)))
                if target!=image.size:image=image.resize(target,Image.Resampling.BILINEAR)
                if overlay_path:
                    try:
                        payload=json.loads(Path(overlay_path).read_text(encoding='utf-8'))
                        image=draw_meter_box(image,payload,sample['timestamp'])
                    except (OSError,ValueError):pass
                value=dict(image=image,sequence=sample['sequence'],timestamp=sample['timestamp'],
                           prepare_ms=(time.perf_counter()-started)*1000)
                sequence=sample['sequence']
                with self.lock:self.latest=value
        except (OSError,ValueError) as exc:self.error=str(exc)
        finally:
            if mailbox:mailbox.close()

    def read(self,after,size,overlay_path=None):
        with self.lock:
            self.size=size;self.overlay_path=overlay_path
            return self.latest if self.latest and self.latest['sequence']>after else None

    def close(self):
        self.stop.set();self.thread.join(timeout=.25)


def inspect_gtuner_python(executable):
    """Probe the chosen interpreter, not the app's interpreter or Gtuner itself."""
    script = r"""
import sys,struct,json,pathlib,importlib
result=dict(executable=sys.executable,version=sys.version.split()[0],bits=struct.calcsize('P')*8,
            prefix=sys.prefix,base_prefix=sys.base_prefix)
root=pathlib.Path(sys.base_prefix)
result['python_libraries']=[str(p) for p in root.glob('python3*.dll')]
try:
 import cv2
 result.update(opencv=cv2.__version__,opencv_file=cv2.__file__)
 result['opencv_binaries']=[str(p) for p in pathlib.Path(cv2.__file__).parent.rglob('*.pyd')]
except Exception as exc:result['opencv_error']=str(exc)
result['pywin32']={}
for name in ('win32api','pywintypes','pythoncom'):
 try:
  module=importlib.import_module(name);result['pywin32'][name]=dict(ok=True,path=module.__file__)
 except Exception as exc:result['pywin32'][name]=dict(ok=False,error=str(exc))
print(json.dumps(result))
"""
    completed=subprocess.run([str(executable),'-c',script],capture_output=True,text=True,timeout=12)
    if completed.returncode:raise RuntimeError((completed.stderr or 'Python check failed')[-2000:])
    result=json.loads(completed.stdout.strip().splitlines()[-1])
    result['gtuner_embedded_verified']=False
    return result


def prepare_gtuner_runtime(app_python):
    """Provision only CV/Win32 in the full 3.11 base used by the managed app."""
    probe=subprocess.run([str(app_python),'-c',
        "import sys,struct,pathlib,json; assert sys.version_info[:2]==(3,11) and struct.calcsize('P')==8, 'Python 3.11 x64 required'; print(json.dumps(str(pathlib.Path(sys.base_prefix)/'python.exe')))"],
        capture_output=True,text=True,timeout=12)
    if probe.returncode:raise RuntimeError(probe.stderr[-2000:] or 'Python 3.11 x64 required')
    executable=json.loads(probe.stdout.strip().splitlines()[-1])
    result=subprocess.run([executable,'-m','pip','install','--only-binary=:all:',
        'opencv-python>=4.8,<5','pywin32>=306,<400'],capture_output=True,text=True,timeout=300)
    if result.returncode:raise RuntimeError((result.stderr or result.stdout)[-3000:])
    report=inspect_gtuner_python(executable)
    report['installation_folder']=str(Path(executable).parent)
    report['app_environment']=str(app_python)
    return report


def capture_worker(cmd,project):
    """Request a bounded scheduling boost, falling back only on access denial."""
    flags=getattr(subprocess,'ABOVE_NORMAL_PRIORITY_CLASS',0) if sys.platform=='win32' else 0
    kwargs=dict(cwd=project,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1)
    try:
        process=subprocess.Popen(cmd,creationflags=flags,**kwargs)
        return process,dict(policy='app-first',requested='above-normal' if flags else 'normal',fallback=False)
    except PermissionError:
        if not flags:raise
        process=subprocess.Popen(cmd,creationflags=0,**kwargs)
        return process,dict(policy='app-first',requested='normal',fallback=True,reason='OS denied priority request')


def load_settings():
    try: return json.loads(SETTINGS.read_text(encoding='utf-8'))
    except (OSError,ValueError): return {}


def discover_model(project=PROJECT, downloads=None, deep=False):
    """Prefer the managed model, then previous runs and known Downloads layouts."""
    project=Path(project)
    downloads=Path(downloads) if downloads else Path.home()/'Downloads'
    local=managed_model(project)
    if local.is_file():return local
    reports=sorted((project/'test_results').rglob('live_run.json'),
                   key=lambda p:p.stat().st_mtime,reverse=True) if (project/'test_results').exists() else []
    for report in reports:
        try:
            model=Path(json.loads(report.read_text(encoding='utf-8'))['model'])
            if model.is_file():return model
        except (OSError,KeyError,ValueError):continue
    known=downloads/'2k_meter_v2_manual_labeling'/'2k_meter_v2_manual_labeling'/'runs'/'meter_v2_gpu_1280-6'/'weights'/'best.pt'
    if known.is_file():return known
    if deep and downloads.is_dir():
        candidates=list(downloads.rglob('best.pt'))
        candidates=[p for p in candidates if p.is_file()]
        if candidates:
            return max(candidates,key=lambda p:('weights' in p.parts,
                'meter_v2_gpu_1280-6' in p.parts,p.stat().st_mtime))
    return None


class Desktop(tk.Tk):
    def report_callback_exception(self, exc_type, value, tb):
        detail=''.join(traceback.format_exception(exc_type,value,tb))
        try:
            with (PROJECT/'app_errors.log').open('a',encoding='utf-8') as log:
                log.write(detail+'\n')
        except OSError:pass
        messagebox.showerror('NBA 2K CV error',f'{value}\n\nDetails saved to app_errors.log')

    def __init__(self):
        super().__init__()
        self.title(f'NBA 2K Meter CV  •  {current_version(PROJECT)}')
        self.geometry(f'{min(1800,max(1180,self.winfo_screenwidth()-24))}x{min(900,max(600,self.winfo_screenheight()-120))}+0+0')
        self.minsize(1180,600)
        self._theme()
        self.process=None
        self.process_out=None
        self.capture_process=None
        self.capture_active=False
        self.capture_out=None
        self.bridge_service=None
        self.bridge_tick_at=0
        self.hardware_scan_pending=False
        self.hardware_scan_after=None
        self.hardware_inventory={}
        self.hardware_autoconnect_id=None
        self.hardware_closing=False
        self.active_out=None
        self.live_active=False
        self.session_requested=False;self.session_deadline=0
        self.session_status=tk.StringVar(value='Start Session prepares capture, detection and the Titan bridge. Shots start disarmed.')
        self.preview_image=None
        self.last_preview_stamp=None
        self.importing_model=False
        self.preparing_bridge=False
        self.gtuner_launched=False
        self.finding_gtuner=False
        self.gtuner_discovery_generation=0
        self.raw_preview=None
        self.raw_preview_sequence=0
        self.raw_preview_photo_size=None
        self.display_count=0
        self.display_started=time.perf_counter()
        self.last_display_timestamp=None
        self.last_display_metric=0
        self.bridge_seen_ready=False
        self.pending_reconnect=False
        self.resume_after_reconnect=False
        self.reconnect_attempts=0
        self.capture_launch_requested=False
        self.messages=queue.Queue()
        saved=load_settings()
        self.known_models=list(saved.get('known_models',[]))
        self.known_environments=list(saved.get('known_environments',[]))
        self.model_choices={}
        self.catalog=[]
        saved_model=Path(saved.get('model','')) if saved.get('model') else None
        found=saved_model if saved_model and saved_model.is_file() else discover_model()
        self.model=tk.StringVar(value=str(found) if found else '')
        self.model_choice=tk.StringVar()
        self.model_detail=tk.StringVar(value='Add your trained .pt weight to begin.')
        self.input_hint=tk.StringVar()
        self.capture_fps_label=tk.StringVar(value='—')
        self.inference_label=tk.StringVar(value='—')
        self.observation_label=tk.StringVar(value='IDLE')
        self.model.trace_add('write',lambda *_:self._model_changed())
        self.source=tk.StringVar(value=saved.get('source','0'))
        self.width=tk.StringVar(value=saved.get('width','1920'))
        self.height=tk.StringVar(value=saved.get('height','1080'))
        self.fps=tk.StringVar(value=saved.get('fps','60'))
        self.capture_type=tk.StringVar(value=saved.get('capture_type','DirectShow / MJPG'))
        self.render_mode=tk.StringVar(value=saved.get('render_mode','Performance'))
        self.display_fps=tk.StringVar(value=saved.get('display_fps','120'))
        self.display_metrics=tk.StringVar(value='Display idle — capture and AI FPS are measured separately')
        self.imgsz=tk.StringVar(value=saved.get('imgsz','1280'))
        self.imgsz.trace_add('write',lambda *_:self._input_changed())
        self.seconds=tk.StringVar(value=saved.get('seconds','0'))
        self.environment=tk.StringVar(value=saved.get('environment',sys.executable))
        if saved.get('app_runtime_generation')!=311 and sys.platform=='win32':self.environment.set(sys.executable)
        self.compute=tk.StringVar(value=saved.get('compute','auto'))
        self.controller=tk.StringVar(value=saved.get('controller','Xbox / GameSir'))
        self.bridge_enabled=tk.BooleanVar(value=saved.get('bridge_enabled',False))
        self.gtuner_path=tk.StringVar(value=saved.get('gtuner_path',''))
        self.gtuner_python=tk.StringVar(value=saved.get('gtuner_python',''))
        self.gtuner_python_status=tk.StringVar(value='Choose the Python installation used in Gtuner Preferences → Computer Vision.')
        self.auto_gtuner=tk.BooleanVar(value=saved.get('auto_gtuner',True))
        self.titan_slot=tk.StringVar(value=saved.get('titan_slot','Auto (meter bridge)'))
        self.auto_slot=tk.BooleanVar(value=saved.get('auto_slot',True))
        self.bridge_setup_status=tk.StringVar(value='Not checked — install the script in Gtuner once')
        self.titan_usb_status=tk.StringVar(value='Checking Windows for Titan Two USB hardware...')
        self.bridge_status=tk.StringVar(value='Bridge off • Titan Two hardware not verified')
        self.bridge_next_step=tk.StringVar(value='Use Gtuner as the Titan host. Disconnect the Helios bridge before testing this backend.')
        self.component_status=tk.StringVar(value='Capture: stopped\nEngine: stopped\nModel: selectable weight\nBridge: starting independently')
        self.live_metrics=tk.StringVar(value='Preview idle')
        try:green=green_config(dict(saved.get('green',{}),shot_mode='timing'))
        except ValueError:green=green_config()
        self.trigger_armed=tk.BooleanVar(value=False)
        self.release_status=tk.StringVar(value='X override OFF — start capture and detection, then enable.')
        self.green_vars={key:tk.StringVar(value=str(value)) for key,value in green.items() if key!='armed'}
        self.green_status=tk.StringVar(value='Green detector: awaiting observed frames • trigger off')
        self.record=tk.BooleanVar(value=False)
        self.raw=tk.BooleanVar(value=False)
        self.preview=tk.BooleanVar(value=True)
        self.show_boxes=tk.BooleanVar(value=saved.get('show_boxes',True))
        self.log_follow=tk.BooleanVar(value=True)
        self.video_window=None;self.video_screen=None;self.output_window=None;self.output_log=None
        self.status=tk.StringVar(value='Ready')
        self.update_status=tk.StringVar(value='Update check pending')
        self.latest_release=None
        self.checking_updates=False
        self.updating=False
        self.notified_versions=set()
        self.update_notice=None
        self._build()
        try:
            self.bridge_service=BridgeProcess(sys.executable,PROJECT/'run_live.py',log_dir=PROJECT/'test_results'/'backend')
            self.bridge_service.configure(enabled=self.bridge_enabled.get())
        except OSError as exc:self.bridge_setup_status.set('Local bridge service unavailable: '+str(exc))
        self.after(100,self._poll)
        self.after(10,self._display_tick)
        self.after(500,lambda:self._check_online(silent=True))
        self.after(1000,self._startup_bridge)
        self.after(250,self._scan_titan_hardware)
        self.after(300000,self._periodic_update_check)
        self.protocol('WM_DELETE_WINDOW',self._close)

    def _theme(self):
        self.configure(background='#202124')
        style=ttk.Style(self)
        style.theme_use('clam')
        style.configure('.',font=('Segoe UI',10),background='#202124',foreground='#e6edf5')
        style.configure('TFrame',background='#202124')
        style.configure('Card.TFrame',background='#2b2d31')
        style.configure('TLabel',background='#202124',foreground='#e6edf5')
        style.configure('Card.TLabel',background='#2b2d31',foreground='#e6edf5')
        style.configure('Muted.TLabel',foreground='#9aabbf')
        style.configure('CardMuted.TLabel',background='#2b2d31',foreground='#9aabbf')
        style.configure('Brand.TLabel',font=('Segoe UI',22,'bold'))
        style.configure('Section.TLabel',font=('Segoe UI',11,'bold'),background='#2b2d31')
        style.configure('Metric.TLabel',font=('Segoe UI',23,'bold'),background='#2b2d31',foreground='#00d5df')
        style.configure('Video.TLabel',background='#111214',foreground='#8798aa',font=('Segoe UI',12))
        style.configure('TButton',background='#203044',foreground='#e6edf5',padding=(12,8),borderwidth=0)
        style.map('TButton',background=[('active','#2c425b')],foreground=[('disabled','#677689')])
        style.configure('Accent.TButton',background='#00cbd6',foreground='#082225',font=('Segoe UI',10,'bold'))
        style.map('Accent.TButton',background=[('active','#80eed1')],foreground=[('disabled','#677689')])
        style.configure('Stop.TButton',background='#4e2634',foreground='#ffb5c3')
        style.configure('TEntry',bordercolor='#30465c',lightcolor='#30465c',darkcolor='#30465c',fieldbackground='#25262a',foreground='#e6edf5',insertcolor='#e6edf5',padding=6)
        style.configure('TCombobox',bordercolor='#30465c',lightcolor='#30465c',darkcolor='#30465c',arrowcolor='#abc1d6',fieldbackground='#25262a',foreground='#e6edf5',background='#24364b',padding=6)
        style.map('TCombobox',fieldbackground=[('readonly','#25262a')],foreground=[('readonly','#e6edf5')],
                  selectbackground=[('readonly','#25262a')],selectforeground=[('readonly','#e6edf5')])
        style.configure('TCheckbutton',background='#2b2d31',foreground='#e6edf5',padding=(0,4))
        style.map('TCheckbutton',background=[('active','#2b2d31')])
        style.configure('TNotebook',background='#202124',borderwidth=0,bordercolor='#202124',lightcolor='#202124',darkcolor='#202124')
        style.configure('TNotebook.Tab',bordercolor='#263c51',lightcolor='#263c51',darkcolor='#263c51',background='#282a2e',foreground='#a5b6c8',padding=(18,11))
        style.map('TNotebook.Tab',background=[('selected','#22384a')],foreground=[('selected','#00d5df')])
        style.configure('TLabelframe',background='#2b2d31',bordercolor='#2a3b4f',padding=12)
        style.configure('TLabelframe.Label',background='#2b2d31',foreground='#00d5df')
        style.configure('Vertical.TScrollbar',background='#263c51',troughcolor='#202124',bordercolor='#202124',arrowcolor='#abc1d6',lightcolor='#263c51',darkcolor='#263c51')
        style.map('Vertical.TScrollbar',background=[('active','#35536f')])
        style.configure('Treeview',background='#2b2d31',fieldbackground='#2b2d31',foreground='#e6edf5',rowheight=34,borderwidth=0)
        style.configure('Treeview.Heading',background='#203044',foreground='#b8c7d7',padding=8)
        style.map('Treeview',background=[('selected','#235448')],foreground=[('selected','#ffffff')])
        self.option_add('*TCombobox*Listbox.background','#25262a')
        self.option_add('*TCombobox*Listbox.foreground','#e6edf5')
        self.option_add('*TCombobox*Listbox.selectBackground','#235448')

    def _card(self,parent,title):
        card=ttk.Frame(parent,style='Card.TFrame',padding=16)
        card.pack(fill='x',pady=(0,12))
        ttk.Label(card,text=title,style='Section.TLabel').pack(anchor='w',pady=(0,10))
        return card

    def _scroll_panel(self,parent,width=350):
        outer=ttk.Frame(parent,width=width)
        outer.grid_propagate(False)
        outer.columnconfigure(0,weight=1);outer.rowconfigure(0,weight=1)
        canvas=tk.Canvas(outer,background='#202124',highlightthickness=0,width=width)
        canvas.grid(row=0,column=0,sticky='nsew')
        bar=ttk.Scrollbar(outer,orient='vertical',command=canvas.yview)
        bar.grid(row=0,column=1,sticky='ns');canvas.configure(yscrollcommand=bar.set)
        content=ttk.Frame(canvas)
        item=canvas.create_window((0,0),window=content,anchor='nw')
        content.bind('<Configure>',lambda event:canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>',lambda event:canvas.itemconfigure(item,width=event.width))
        def wheel(event):
            current=self.winfo_containing(event.x_root,event.y_root)
            while current is not None:
                if current==content:
                    canvas.yview_scroll(-1 if event.delta>0 else 1,'units');return
                current=getattr(current,'master',None)
        self.bind_all('<MouseWheel>',wheel,add='+')
        return outer,content

    def _row(self,parent,title,var,browse=None):
        row=ttk.Frame(parent);row.pack(fill='x',pady=5)
        ttk.Label(row,text=title,width=20).pack(side='left')
        ttk.Entry(row,textvariable=var).pack(side='left',fill='x',expand=True)
        if browse: ttk.Button(row,text='Browse',command=browse).pack(side='left',padx=6)

    def _build(self):
        header=ttk.Frame(self,padding=(22,16,22,12));header.pack(fill='x')
        ttk.Label(header,text='METER LAB',style='Brand.TLabel').pack(side='left')
        ttk.Label(header,text='  NBA 2K  /  VISION WORKSPACE',style='Muted.TLabel').pack(side='left',padx=12)
        ttk.Label(header,text=f'v{current_version(PROJECT)}',style='Muted.TLabel').pack(side='right')
        self.update_banner=ttk.Frame(self,padding=(22,10))
        ttk.Label(self.update_banner,textvariable=self.update_status).pack(side='left',fill='x',expand=True)
        self.banner_install=ttk.Button(self.update_banner,text='Install Update',command=self._install_online)
        self.banner_install.pack(side='right')
        self.tabs=ttk.Notebook(self);self.tabs.pack(fill='both',expand=True,padx=18,pady=(0,12))
        live=ttk.Frame(self.tabs,padding=(0,16,0,0));self.tabs.add(live,text='Live Detection')
        live.columnconfigure(0,weight=0,minsize=285);live.columnconfigure(1,weight=1);live.columnconfigure(2,weight=0,minsize=285)
        live.rowconfigure(0,weight=1)
        sidebar,controls=self._scroll_panel(live,width=285)
        sidebar.grid(row=0,column=0,sticky='nsew',padx=(0,14))
        detector=self._card(controls,'01   DETECTOR')
        ttk.Label(detector,text='Trained weight',style='CardMuted.TLabel').pack(anchor='w')
        self.model_menu=ttk.Combobox(detector,textvariable=self.model_choice,state='readonly',width=26)
        self.model_menu.pack(fill='x',pady=(5,7))
        self.model_menu.bind('<<ComboboxSelected>>',self._select_model)
        ttk.Label(detector,textvariable=self.model_detail,style='CardMuted.TLabel',wraplength=235).pack(anchor='w',pady=4)
        choose=ttk.Frame(detector,style='Card.TFrame');choose.pack(fill='x',pady=6)
        ttk.Button(choose,text='Add weight',command=self._choose_model).pack(side='left')
        ttk.Button(choose,text='Find models',command=self._auto_find).pack(side='left',padx=6)
        ttk.Label(detector,text='Inference input size',style='CardMuted.TLabel').pack(anchor='w',pady=(9,4))
        self.input_menu=ttk.Combobox(detector,textvariable=self.imgsz,values=('640','960','1280'),state='readonly',width=12)
        self.input_menu.pack(fill='x')
        ttk.Label(detector,textvariable=self.input_hint,style='CardMuted.TLabel',wraplength=280).pack(anchor='w',pady=(7,0))
        capture=self._card(controls,'02   CAPTURE')
        ttk.Label(capture,text='Device index or stream URL',style='CardMuted.TLabel').pack(anchor='w')
        ttk.Combobox(capture,textvariable=self.source,values=('0','1','2','3'),width=14).pack(fill='x',pady=(5,8))
        sizes=ttk.Frame(capture,style='Card.TFrame');sizes.pack(fill='x')
        for col,(title,var) in enumerate([('Width',self.width),('Height',self.height),('FPS',self.fps)]):
            sizes.columnconfigure(col,weight=1)
            ttk.Label(sizes,text=title,style='CardMuted.TLabel').grid(row=0,column=col,sticky='w')
            widget=(ttk.Combobox(sizes,textvariable=var,values=('60','120','240'),state='readonly',width=7)
                    if title=='FPS' else ttk.Entry(sizes,textvariable=var,width=7))
            widget.grid(row=1,column=col,sticky='ew',padx=(0,5),pady=4)
        ttk.Label(capture,text='Capture backend / requested format',style='CardMuted.TLabel').pack(anchor='w',pady=(7,4))
        ttk.Combobox(capture,textvariable=self.capture_type,values=('DirectShow / MJPG','DirectShow / YUY2',
            'DirectShow / BGR3','Media Foundation / MJPG','Media Foundation / YUY2','Auto / Default'),state='readonly').pack(fill='x')
        ttk.Label(capture,text='Live BGR display preset',style='CardMuted.TLabel').pack(anchor='w',pady=(7,4))
        render=ttk.Combobox(capture,textvariable=self.render_mode,values=('Performance','Resolution'),state='readonly')
        render.pack(fill='x');render.bind('<<ComboboxSelected>>',self._render_preset)
        ttk.Label(capture,text='Display FPS limit (independent of capture)',style='CardMuted.TLabel').pack(anchor='w',pady=(7,4))
        display=ttk.Combobox(capture,textvariable=self.display_fps,values=('15','30','60','120','240'),state='readonly')
        display.pack(fill='x');display.bind('<<ComboboxSelected>>',self._display_changed)
        ttk.Label(capture,text='Duration in seconds  •  0 = until Stop',style='CardMuted.TLabel').pack(anchor='w',pady=(7,4))
        ttk.Entry(capture,textvariable=self.seconds,width=10).pack(fill='x')
        ttk.Label(capture,text='App-first: releases this app’s probe and requests Above Normal CPU priority on Windows. Close other software holding the card; use Null input in Gtuner.',style='CardMuted.TLabel',wraplength=245).pack(anchor='w',pady=(8,0))
        ttk.Checkbutton(capture,text='Show live preview',variable=self.preview,command=self._display_changed).pack(anchor='w',pady=(8,0))
        ttk.Checkbutton(capture,text='Save annotated replay',variable=self.record).pack(anchor='w')
        ttk.Checkbutton(capture,text='Save analyzed training frames',variable=self.raw).pack(anchor='w')
        runtime=self._card(controls,'03   RUNTIME & CONTROLLER')
        ttk.Label(runtime,text='Python environment',style='CardMuted.TLabel').pack(anchor='w')
        self.environment_menu=ttk.Combobox(runtime,textvariable=self.environment,width=26)
        self.environment_menu.pack(fill='x',pady=5)
        ttk.Button(runtime,text='Browse Python',command=self._choose_environment).pack(anchor='w',pady=(0,9))
        ttk.Label(runtime,text='Compute  •  0 = CUDA GPU 0',style='CardMuted.TLabel').pack(anchor='w')
        ttk.Combobox(runtime,textvariable=self.compute,values=('auto','0','cpu'),state='readonly').pack(fill='x',pady=5)
        ttk.Label(runtime,text='Controller profile',style='CardMuted.TLabel').pack(anchor='w',pady=(6,0))
        ttk.Combobox(runtime,textvariable=self.controller,values=('Xbox / GameSir','Xbox Elite','DualSense','DualShock 4','Generic HID'),state='readonly').pack(fill='x',pady=5)
        self.bridge_toggle=ttk.Checkbutton(runtime,text='Enable Titan Two bridge',variable=self.bridge_enabled,
            command=lambda:self._connect_bridge() if self.bridge_enabled.get() else self._disconnect_bridge())
        self.bridge_toggle.pack(anchor='w',pady=5)
        ttk.Button(runtime,text='Open bridge settings',command=lambda:self.tabs.select(self.bridge_tab)).pack(anchor='w')
        work=ttk.Frame(live);work.grid(row=0,column=1,sticky='nsew')
        work.columnconfigure(0,weight=1);work.rowconfigure(2,weight=1)
        actions=ttk.Frame(work);actions.grid(row=0,column=0,sticky='ew',pady=(0,12))
        session_actions=ttk.Frame(actions);session_actions.pack(fill='x',pady=(0,8))
        ttk.Button(session_actions,text='▶  Start Session',style='Accent.TButton',command=self._start_session).pack(side='left')
        ttk.Button(session_actions,text='■  Stop Session',command=self._stop_session).pack(side='left',padx=8)
        ttk.Label(session_actions,textvariable=self.session_status,style='Muted.TLabel',wraplength=360).pack(side='left',padx=5)
        capture_actions=ttk.Frame(actions);capture_actions.pack(fill='x')
        ttk.Button(capture_actions,text='▶  Start Capture',style='Accent.TButton',command=self._start_capture).pack(side='left')
        ttk.Button(capture_actions,text='■  Stop Capture',style='Stop.TButton',command=self._stop_capture).pack(side='left',padx=8)
        ttk.Label(capture_actions,text='App-first capture • one card owner',style='Muted.TLabel').pack(side='left')
        ai_actions=ttk.Frame(actions);ai_actions.pack(fill='x',pady=(8,0))
        ttk.Button(ai_actions,text='▶  Start AI Detection',command=self._live).pack(side='left')
        ttk.Button(ai_actions,text='■  Stop AI',command=self._stop).pack(side='left',padx=8)
        ttk.Button(ai_actions,text='Check Setup',command=self._setup).pack(side='right')
        ttk.Button(ai_actions,text='Probe Card',command=self._probe).pack(side='right',padx=8)
        metrics=ttk.Frame(work);metrics.grid(row=1,column=0,sticky='ew',pady=(0,12))
        for col,(title,var) in enumerate([('PROCESSED FPS',self.capture_fps_label),('INFERENCE',self.inference_label),('METER STATUS',self.observation_label)]):
            metrics.columnconfigure(col,weight=1)
            metric=ttk.Frame(metrics,style='Card.TFrame',padding=12)
            metric.grid(row=0,column=col,sticky='nsew',padx=(0,8 if col<2 else 0))
            ttk.Label(metric,text=title,style='CardMuted.TLabel').pack(anchor='w')
            ttk.Label(metric,textvariable=var,style='Metric.TLabel').pack(anchor='w',pady=(5,0))
        self.live_panes=ttk.Panedwindow(work,orient='vertical')
        self.live_panes.grid(row=2,column=0,sticky='nsew')
        video=ttk.Frame(self.live_panes,style='Card.TFrame',padding=14)
        self.live_panes.add(video,weight=4)
        video.columnconfigure(0,weight=1);video.rowconfigure(1,weight=1)
        video_tools=ttk.Frame(video);video_tools.grid(row=0,column=0,sticky='ew',pady=(0,10))
        ttk.Label(video_tools,text='LIVE CAPTURE',style='Section.TLabel').pack(side='left')
        ttk.Checkbutton(video_tools,text='Meter boxes',variable=self.show_boxes).pack(side='left',padx=10)
        ttk.Button(video_tools,text='Windowed',command=self._open_video).pack(side='right')
        ttk.Button(video_tools,text='Fullscreen',command=lambda:self._open_video(True)).pack(side='right',padx=5)
        self.screen=ttk.Label(video,text='Start Capture to view your console.\n\nStart AI Detection separately when you are ready.',style='Video.TLabel',anchor='center')
        self.screen.grid(row=1,column=0,sticky='nsew')
        ttk.Label(video,textvariable=self.display_metrics,style='CardMuted.TLabel',wraplength=520).grid(row=3,column=0,sticky='w',pady=(0,4))
        ttk.Label(video,textvariable=self.live_metrics,style='CardMuted.TLabel',wraplength=650).grid(row=2,column=0,sticky='w',pady=(10,0))
        ttk.Label(work,textvariable=self.bridge_status,style='Muted.TLabel',wraplength=700).grid(row=3,column=0,sticky='w',pady=10)
        output=ttk.Frame(self.live_panes,padding=(0,8,0,0));self.live_panes.add(output,weight=1)
        output_tools=ttk.Frame(output);output_tools.pack(fill='x')
        ttk.Label(output_tools,text='OUTPUT — drag the divider above to resize',style='Muted.TLabel').pack(side='left')
        ttk.Button(output_tools,text='Pop out',command=self._open_output).pack(side='right')
        ttk.Checkbutton(output_tools,text='Follow output',variable=self.log_follow).pack(side='right',padx=8)
        self.log=self._new_output(output,height=7)
        self.log.pack(fill='both',expand=True,pady=(5,0))
        bridge_sidebar,bridge_controls=self._scroll_panel(live,width=285)
        bridge_sidebar.grid(row=0,column=2,sticky='nsew',padx=(14,0))
        device_card=self._card(bridge_controls,'TITAN BRIDGE')
        ttk.Label(device_card,text='Backend: Gtuner IV',style='Card.TLabel').pack(anchor='w')
        ttk.Label(device_card,textvariable=self.titan_usb_status,style='CardMuted.TLabel',wraplength=235).pack(anchor='w',pady=8)
        ttk.Button(device_card,text='Connect / Reconnect',style='Accent.TButton',command=self._connect_bridge).pack(fill='x',pady=4)
        ttk.Button(device_card,text='Disconnect bridge',command=self._disconnect_bridge).pack(fill='x',pady=4)
        ttk.Button(device_card,text='Scan USB',command=self._scan_titan_hardware).pack(fill='x',pady=4)
        ttk.Separator(device_card).pack(fill='x',pady=8)
        ttk.Button(device_card,text='Arm Meter Shots',style='Accent.TButton',command=self._arm_shots).pack(fill='x',pady=4)
        ttk.Button(device_card,text='Start AI X Shot',command=self._start_ai_x_shot).pack(fill='x',pady=4)
        ttk.Button(device_card,text='Disarm Shots',command=self._disarm_shots).pack(fill='x',pady=4)
        ttk.Label(device_card,text='X JUMPSHOTS: hold X through green. The AI releases X output; physically release X afterward. RT, movement and RS pass through.',
            style='CardMuted.TLabel',wraplength=235).pack(anchor='w',pady=6)
        ttk.Label(device_card,textvariable=self.release_status,style='CardMuted.TLabel',wraplength=235).pack(anchor='w',pady=6)
        ttk.Label(device_card,textvariable=self.green_status,style='CardMuted.TLabel',wraplength=235).pack(anchor='w',pady=6)
        ttk.Label(device_card,text='X jumpshot control').pack(anchor='w')
        self.shot_mode_menu=mode_menu=ttk.Combobox(device_card,textvariable=self.green_vars['shot_mode'],values=('timing',),state='readonly')
        mode_menu.pack(fill='x',pady=4)
        mode_menu.bind('<<ComboboxSelected>>',lambda event:self._shot_mode_changed())
        ttk.Button(device_card,text='Apply Shot Settings',command=self._apply_green).pack(fill='x',pady=4)
        ttk.Label(device_card,textvariable=self.bridge_status,style='CardMuted.TLabel',wraplength=235).pack(anchor='w',pady=8)
        ttk.Label(device_card,textvariable=self.bridge_next_step,style='CardMuted.TLabel',wraplength=235).pack(anchor='w',pady=8)
        test_card=self._card(bridge_controls,'CONSOLE OUTPUT TEST')
        ttk.Label(test_card,text='PowerShell can hold X, then release it after the selected time. Use the updated test pair 1.1 or meter bridge 3.5 in practice.',style='CardMuted.TLabel',wraplength=235).pack(anchor='w')
        ttk.Button(test_card,text='Open original working test pair',command=self._open_original_test).pack(fill='x',pady=4)
        ttk.Button(test_card,text='Test A via PowerShell',command=lambda:self._console_tap('A')).pack(fill='x',pady=4)
        self.console_hold_ms=tk.StringVar(value='750')
        ttk.Label(test_card,text='X hold duration (ms)',style='CardMuted.TLabel').pack(anchor='w')
        ttk.Spinbox(test_card,from_=100,to=2000,increment=50,textvariable=self.console_hold_ms).pack(fill='x',pady=4)
        ttk.Button(test_card,text='Hold X → Release via PowerShell',command=lambda:self._console_tap('HOLD_X')).pack(fill='x',pady=4)
        ttk.Button(test_card,text='Release X via PowerShell',command=lambda:self._console_tap('RELEASE_X')).pack(fill='x',pady=4)
        ttk.Button(test_card,text='Test X via PowerShell',command=lambda:self._console_tap('X')).pack(fill='x',pady=4)
        self.console_test_status=tk.StringVar(value='No console test requested')
        ttk.Label(test_card,textvariable=self.console_test_status,style='CardMuted.TLabel',wraplength=235).pack(anchor='w',pady=4)
        slots_card=self._card(bridge_controls,'MEMORY SLOTS')
        ttk.Combobox(slots_card,textvariable=self.titan_slot,values=('Auto (meter bridge)',*tuple(str(i) for i in range(1,10))),state='readonly',width=21).pack(fill='x',pady=4)
        ttk.Button(slots_card,text='Select & verify slot',command=self._bridge_slot_changed).pack(fill='x',pady=4)
        self.live_slots=ttk.Treeview(slots_card,columns=('label',),show='tree headings',height=5)
        self.live_slots.heading('#0',text='Slot');self.live_slots.column('#0',width=40,stretch=False)
        self.live_slots.heading('label',text='Gtuner script');self.live_slots.column('label',width=170)
        for i in range(1,10):self.live_slots.insert('','end',iid=str(i),text=str(i),values=('Not read',))
        self.live_slots.pack(fill='x',pady=6)
        ttk.Label(slots_card,text='Names require Gtuner access. Console output remains unverified.',style='CardMuted.TLabel',wraplength=235).pack(anchor='w')
        ttk.Button(slots_card,text='Full bridge setup',command=lambda:self.tabs.select(self.bridge_tab)).pack(fill='x',pady=8)
        components=self._card(bridge_controls,'INDEPENDENT COMPONENTS')
        ttk.Label(components,textvariable=self.component_status,style='CardMuted.TLabel',wraplength=235).pack(anchor='w')
        models=ttk.Frame(self.tabs,padding=20);self.tabs.add(models,text='Models')
        ttk.Label(models,text='Your model library',style='Brand.TLabel').pack(anchor='w')
        ttk.Label(models,text='Choose a real .pt weight. Input size is selected separately in Live Detection.',style='Muted.TLabel').pack(anchor='w',pady=(6,16))
        model_actions=ttk.Frame(models);model_actions.pack(fill='x',pady=(0,12))
        for title,command in [('Add weight',self._choose_model),('Find models',self._auto_find),('Import to App',self._import_model),('Refresh',self._refresh_choices)]:
            ttk.Button(model_actions,text=title,command=command).pack(side='left',padx=(0,8))
        self.model_tree=ttk.Treeview(models,columns=('size','path'),show='tree headings',selectmode='browse')
        self.model_tree.heading('#0',text='Model');self.model_tree.column('#0',width=300)
        self.model_tree.heading('size',text='Size');self.model_tree.column('size',width=90,stretch=False)
        self.model_tree.heading('path',text='File location');self.model_tree.column('path',width=450)
        self.model_tree.pack(fill='both',expand=True,pady=8)
        self.model_tree.bind('<Double-1>',self._use_library_model)
        ttk.Button(models,text='Use selected weight',style='Accent.TButton',command=self._use_library_model).pack(anchor='w',pady=10)
        ttk.Label(models,textvariable=self.model_detail,style='Muted.TLabel',wraplength=950).pack(anchor='w')

        self.bridge_tab=ttk.Frame(self.tabs,padding=20)
        self.tabs.add(self.bridge_tab,text='Titan Two')
        bridge_scroll,bridge_body=self._scroll_panel(self.bridge_tab,width=960)
        bridge_scroll.pack(fill='both',expand=True)
        ttk.Label(bridge_body,text='TITAN CONTROL CENTER',font=('Segoe UI',18,'bold')).pack(anchor='w',pady=(4,8))
        ttk.Label(bridge_body,text='Connect the device → verify the script → start detection',style='Muted.TLabel').pack(anchor='w',pady=(0,12))
        device=ttk.LabelFrame(bridge_body,text='01   DEVICE & CONNECTION',padding=14)
        device.pack(fill='x',pady=6)
        ttk.Label(device,textvariable=self.titan_usb_status,wraplength=880).pack(anchor='w',pady=5)
        connection=ttk.Frame(device);connection.pack(fill='x',pady=8)
        ttk.Button(connection,text='Scan USB',command=self._scan_titan_hardware).pack(side='left')
        ttk.Button(connection,text='Connect / Reconnect',style='Accent.TButton',command=self._connect_bridge).pack(side='left',padx=8)
        ttk.Button(connection,text='Disconnect App Bridge',command=self._disconnect_bridge).pack(side='left')
        ttk.Label(device,textvariable=self.bridge_status,wraplength=880).pack(anchor='w',pady=5)
        ttk.Label(device,textvariable=self.bridge_setup_status,wraplength=880).pack(anchor='w',pady=5)
        ttk.Label(device,text='Console output: unverified • USB presence and script echo do not confirm console input.',style='Muted.TLabel',wraplength=880).pack(anchor='w')
        self.bridge_pages=ttk.Notebook(bridge_body)
        self.bridge_pages.pack(fill='both',expand=True,pady=12)
        setup_page=ttk.Frame(self.bridge_pages,padding=12)
        slots_page=ttk.Frame(self.bridge_pages,padding=12)
        calibration_page=ttk.Frame(self.bridge_pages,padding=12)
        self.bridge_pages.add(setup_page,text='Setup & runtime')
        self.bridge_pages.add(slots_page,text='Scripts & slots')
        self.bridge_pages.add(calibration_page,text='Detection calibration')
        ttk.Label(setup_page,text='FIRST CONNECTION',font=('Segoe UI',11,'bold')).pack(anchor='w',pady=6)
        for instruction in (
            '1. Disconnect the Titan bridge in Helios or other USB hosts first. Connect PROG to this PC, OUTPUT to the console, and the controller to INPUT-A.',
            '2. Open Bridge Files. Install titan_meter.gpc into an unused slot using Gtuner IV.',
            '3. In Gtuner, enable Recall loaded memory slot between power cycles.',
            '4. Start gtuner_meter.py in Gtuner Computer Vision with a Null source. Leave the capture card for this app.',
            '5. Choose the installed slot in Scripts & slots, then Connect / Reconnect. Keep the trigger off until verified.'):
            ttk.Label(setup_page,text=instruction,wraplength=850,justify='left').pack(anchor='w',pady=5)
        ttk.Button(setup_page,text='Open matching bridge scripts',command=self._open_bridge).pack(anchor='w',pady=8)
        ttk.Label(setup_page,text='Controller profile').pack(anchor='w')
        ttk.Combobox(setup_page,textvariable=self.controller,values=('Xbox / GameSir','Xbox Elite','DualSense','DualShock 4','Generic HID'),state='readonly',width=26).pack(anchor='w',pady=5)
        ttk.Label(setup_page,text='Profile is shared with Live Detection. Gtuner handles device programming; this app verifies the bridge and requests slots.',style='Muted.TLabel',wraplength=850).pack(anchor='w',pady=5)
        launcher=ttk.LabelFrame(setup_page,text='Gtuner runtime',padding=10)
        launcher.pack(fill='x',pady=6)
        row=ttk.Frame(launcher);row.pack(fill='x')
        ttk.Label(row,text='Gtuner IV executable').pack(side='left',padx=(0,8))
        ttk.Entry(row,textvariable=self.gtuner_path).pack(side='left',fill='x',expand=True)
        ttk.Button(row,text='Find Gtuner',command=self._find_gtuner).pack(side='left',padx=8)
        ttk.Button(row,text='Browse',command=self._browse_gtuner).pack(side='left')
        ttk.Checkbutton(launcher,text='Open Gtuner on app startup when Titan bridge is enabled',
            variable=self.auto_gtuner,command=self._save).pack(anchor='w',pady=5)
        python_row=ttk.Frame(launcher);python_row.pack(fill='x',pady=8)
        ttk.Label(python_row,text='Gtuner CV Python').pack(side='left',padx=(0,8))
        ttk.Entry(python_row,textvariable=self.gtuner_python).pack(side='left',fill='x',expand=True)
        ttk.Button(python_row,text='Browse',command=self._choose_gtuner_python).pack(side='left',padx=6)
        ttk.Button(python_row,text='Check CV Python',command=self._check_gtuner_python).pack(side='left')
        ttk.Button(launcher,text='Prepare Gtuner Python 3.11',command=self._prepare_gtuner_python).pack(anchor='w',pady=5)
        ttk.Label(launcher,textvariable=self.gtuner_python_status,wraplength=850).pack(anchor='w',pady=5)
        ttk.Label(launcher,text='Use this installation’s folder in Gtuner Preferences → Computer Vision. Confirm its library and OpenCV checks, then run gtuner_meter.py with Null Video Input. CUDA detection stays in the app environment.',style='Muted.TLabel',wraplength=850).pack(anchor='w',pady=5)
        slot_controls=ttk.Frame(slots_page);slot_controls.pack(fill='x',pady=6)
        ttk.Label(slot_controls,text='Default / selected slot').pack(side='left')
        slot_menu=ttk.Combobox(slot_controls,textvariable=self.titan_slot,values=('Auto (meter bridge)',*tuple(str(i) for i in range(1,10))),
            state='readonly',width=20)
        slot_menu.pack(side='left',padx=8)
        slot_menu.bind('<<ComboboxSelected>>',lambda event:self._bridge_slot_changed())
        ttk.Button(slot_controls,text='Switch Slot',command=lambda:self._prepare_bridge(force_slot=True)).pack(side='left')
        ttk.Checkbutton(slots_page,text='Automatically select this slot on startup / reconnect / before capture',
            variable=self.auto_slot,command=self._bridge_slot_changed).pack(anchor='w')
        ttk.Button(slots_page,text='Refresh Slot Names',command=lambda:self._prepare_bridge(read_only=True)).pack(anchor='w',pady=8)
        self.slot_table=ttk.Treeview(slots_page,columns=('name','state'),show='tree headings',height=9)
        self.slot_table.heading('#0',text='Slot');self.slot_table.column('#0',width=70,stretch=False)
        self.slot_table.heading('name',text='Script label read from Gtuner');self.slot_table.column('name',width=400)
        self.slot_table.heading('state',text='Device confirmation');self.slot_table.column('state',width=180)
        self.slot_table.pack(fill='x',pady=6)
        for i in range(1,10):self.slot_table.insert('', 'end',iid=str(i),text=str(i),values=('Unavailable — refresh with Gtuner open','Not verified'))
        ttk.Label(slots_page,text='Script names come from Gtuner. A fresh payload echo must confirm the active slot before output is enabled.',style='Muted.TLabel',wraplength=850).pack(anchor='w',pady=8)
        calibration=ttk.LabelFrame(calibration_page,text='Green detection and controller X release',padding=10)
        calibration.pack(fill='x',pady=8)
        fields=[('Hue low','hue_low'),('Hue high','hue_high'),('Saturation min','saturation_min'),
            ('Brightness min','value_min'),('Green row fraction','row_coverage'),('Confidence min','min_confidence'),
            ('Confirm frames','confirmations'),('Maximum age ms','max_age_ms'),('Rearm absence ms','rearm_ms')]
        for index,(title,key) in enumerate(fields):
            row,column=divmod(index,3)
            ttk.Label(calibration,text=title).grid(row=row,column=column*2,sticky='w',padx=4,pady=3)
            ttk.Entry(calibration,textvariable=self.green_vars[key],width=9).grid(row=row,column=column*2+1,padx=4)
        self.x_release_toggle=ttk.Checkbutton(calibration,text='Enable AI override of held X',
            variable=self.trigger_armed,command=self._toggle_x_release)
        self.x_release_toggle.grid(row=5,column=0,columnspan=6,sticky='w',pady=6)
        ttk.Label(calibration,textvariable=self.release_status,wraplength=950).grid(row=8,column=0,columnspan=6,sticky='w',pady=4)
        ttk.Button(calibration,text='Apply X Shot Settings',command=self._apply_green).grid(row=6,column=0,columnspan=6,sticky='w')
        ttk.Label(calibration,textvariable=self.green_status,wraplength=950).grid(row=7,column=0,columnspan=6,sticky='w',pady=4)
        results=ttk.Frame(self.tabs,padding=14);self.tabs.add(results,text='Results')
        ttk.Button(results,text='Refresh Runs',command=self._refresh).pack(anchor='w')
        self.runs=ttk.Treeview(results,columns=('device','fps','frames'),show='tree headings',height=13)
        self.runs.heading('#0',text='Run');self.runs.column('#0',width=400)
        for column in ('device','fps','frames'):
            self.runs.heading(column,text=column.title());self.runs.column(column,width=100)
        self.runs.pack(fill='both',expand=True,pady=8)
        self.runs.bind('<<TreeviewSelect>>',self._show_result)
        ttk.Button(results,text='Open Selected Folder',command=self._open_result).pack(anchor='w')
        self.detail=scrolledtext.ScrolledText(results,height=11,state='disabled',background='#0b141f',foreground='#b0c3d8',relief='flat',font=('Consolas',10))
        self.detail.pack(fill='both',expand=True,pady=8)

        update=ttk.Frame(self.tabs,padding=20);self.tabs.add(update,text='Updates')
        ttk.Label(update,text=f'Installed version: {current_version(PROJECT)}',
                  font=('Segoe UI',13,'bold')).pack(anchor='w',pady=10)
        ttk.Label(update,text='The app checks its release feed on startup. When a newer version is available, '
                  'Install Update downloads it, verifies the package, backs up code, and restarts. '
                  'Your model, .venv, and test results stay in place.',
                  wraplength=760).pack(anchor='w',pady=10)
        ttk.Label(update,textvariable=self.update_status,wraplength=760).pack(anchor='w',pady=10)
        ttk.Button(update,text='Check Online',command=self._check_online).pack(anchor='w',pady=8)
        self.install_button=ttk.Button(update,text='Install Update',command=self._install_online,state='disabled')
        self.install_button.pack(anchor='w',pady=8)
        ttk.Button(update,text='Install Downloaded ZIP (fallback)',command=self._update_local).pack(anchor='w',pady=8)

        train=ttk.Frame(self.tabs,padding=16);self.tabs.add(train,text='Improve Model')
        ttk.Label(train,text='Training uses corrected labels from separate captures. '
                  'The current weight is never overwritten.',wraplength=800).pack(anchor='w',pady=5)
        self.train_run=tk.StringVar();self.val_run=tk.StringVar();self.test_run=tk.StringVar()
        for title,var in [('Reviewed training run',self.train_run),('Validation run',self.val_run),
                          ('Held-out test run',self.test_run)]:
            self._row(train,title,var,lambda v=var:self._choose_dir(v))
        ttk.Button(train,text='Fine-tune and Compare',command=self._train).pack(anchor='w',pady=8)
        ttk.Label(train,text='Each run needs raw_capture.mp4, live_frames.csv, and reviewed_labels.json. '
                  'Validation and test runs need labels for every frame, including no-meter frames. '
                  'See README.md for the label format.',wraplength=800).pack(anchor='w',pady=8)
        ttk.Button(train,text='Open README',command=lambda:self._open_path(PROJECT/'README.md')).pack(anchor='w')
        ttk.Label(self,textvariable=self.status,anchor='w').pack(fill='x',padx=15,pady=(0,8))
        self._refresh()
        self._refresh_choices()
        self._input_changed()

    def _export_bridge(self):
        # Source checkouts have these files; release builds embed them to stay
        # compatible with the first desktop updater's fixed filename allowlist.
        folder=PROJECT/'bridge'
        if hasattr(__import__('run_live'),'METER_GPC_SOURCE'):
            from run_live import GTUNER_SOURCE, HEARTBEAT_SOURCE, METER_GPC_SOURCE, CONSOLE_TEST_SOURCE, CONSOLE_TEST_GPC_SOURCE
            folder.mkdir(exist_ok=True)
            (folder/'gtuner_meter.py').write_text(GTUNER_SOURCE,encoding='utf-8')
            (folder/'titan_heartbeat.gpc').write_text(HEARTBEAT_SOURCE,encoding='utf-8')
            (folder/'titan_meter.gpc').write_text(METER_GPC_SOURCE,encoding='utf-8')
            (folder/'console_button_check.py').write_text(CONSOLE_TEST_SOURCE,encoding='utf-8')
            (folder/'console_button_check.gpc').write_text(CONSOLE_TEST_GPC_SOURCE,encoding='utf-8')
        if not (folder/'titan_meter.gpc').is_file() or not (folder/'gtuner_meter.py').is_file():
            raise FileNotFoundError('The matching bridge scripts are missing; install the latest app update.')
        diagnostic=folder/'console_button_check.py'
        if not diagnostic.is_file() and (PROJECT/'testing/console_button_check.py').is_file():
            diagnostic.write_text((PROJECT/'testing/console_button_check.py').read_text(encoding='utf-8'),encoding='utf-8')
        original_gpc=folder/'console_button_check.gpc'
        if not original_gpc.is_file() and (PROJECT/'testing/console_button_check.gpc').is_file():
            original_gpc.write_text((PROJECT/'testing/console_button_check.gpc').read_text(encoding='utf-8'),encoding='utf-8')
        return folder

    def _open_bridge(self):
        self._open_path(self._export_bridge())

    def _browse_gtuner(self):
        chosen=filedialog.askopenfilename(title='Select Gtuner IV executable',filetypes=[('Gtuner IV','Gtuner.exe'),('Executable','*.exe')])
        if chosen:
            self.gtuner_path.set(chosen);self.gtuner_launched=False;self._save()

    def _scan_titan_hardware(self):
        if self.hardware_scan_pending or self.hardware_closing:return
        if self.hardware_scan_after:
            self.after_cancel(self.hardware_scan_after);self.hardware_scan_after=None
        self.hardware_scan_pending=True
        def work():
            try:value=titan_hardware_inventory()
            except Exception as exc:value=dict(state='unavailable',devices=[],auto_connect=False,message='USB discovery failed: '+str(exc))
            self.messages.put(('titan_hardware',value))
        threading.Thread(target=work,daemon=True).start()

    def _handle_titan_hardware(self,value):
        previous=getattr(self,'hardware_inventory',{})
        self.hardware_scan_pending=False
        self.hardware_inventory=value
        self.titan_usb_status.set(value['message'])
        try:
            path=PROJECT/'test_results'/'titan_hardware.json';path.parent.mkdir(exist_ok=True)
            path.write_text(json.dumps(dict(timestamp=time.time(),**value),indent=2))
        except OSError:pass
        bridge_verified=bool(self.bridge_service and self.bridge_service.status().get('ready') is True)
        if value.get('auto_connect'):
            key=value['devices'][0]['id']
            if (self.bridge_enabled.get() and not self.preparing_bridge and not bridge_verified
                    and self.hardware_autoconnect_id!=key):
                self.hardware_autoconnect_id=key
                self._connect_bridge()
        elif bridge_verified:
            self.titan_usb_status.set(value['message']+' • Live Titan bridge is verified; keeping current connection.')
        elif value.get('state') not in ('unavailable','unsupported'):
            self.hardware_autoconnect_id=None
            if previous.get('state')!=value.get('state') or self.trigger_armed.get():
                Desktop._cancel_pending_bridge(self)
                if self.bridge_service:self.bridge_service.allow_output(False);self.bridge_service.clear()
                self.trigger_armed.set(False);self._apply_green()
                Desktop._release_feedback(self,'X override OFF — USB discovery: '+value['message'])
        if not self.hardware_closing:self.hardware_scan_after=self.after(3000,self._scan_titan_hardware)

    def _startup_bridge(self):
        if self.bridge_enabled.get():self._prepare_bridge()
        else:self._find_gtuner()

    def _find_gtuner(self,prepare=False,prepare_args=None):
        if self.finding_gtuner:return
        self.finding_gtuner=True
        self.bridge_setup_status.set('Finding Gtuner IV automatically...')
        saved=self.gtuner_path.get()
        generation=self.gtuner_discovery_generation
        def work():
            try:
                found=discover_gtuner(saved)
                self.messages.put(('gtuner_found',(str(found) if found else None,prepare,prepare_args,None,generation)))
            except Exception as exc:self.messages.put(('gtuner_found',(None,prepare,prepare_args,str(exc),generation)))
        threading.Thread(target=work,daemon=True).start()

    def _launch_gtuner(self):
        folder=self._export_bridge()
        if not self.auto_gtuner.get():return
        executable=Path(self.gtuner_path.get())
        if sys.platform!='win32':raise RuntimeError('Gtuner startup is supported on Windows only.')
        if not executable.is_file() or executable.name.lower()!='gtuner.exe':
            raise ValueError('Select Gtuner.exe in the Titan Two tab, then Prepare / Check Bridge.')
        try:import psutil
        except ImportError as exc:raise RuntimeError('Restart with Launch NBA2K CV.bat to install Windows controls.') from exc
        matching=[]
        for process in psutil.process_iter(['exe']):
            try:
                if process.info['exe'] and os.path.normcase(str(Path(process.info['exe']).resolve()))==os.path.normcase(str(executable.resolve())):
                    matching.append(process.pid)
            except (psutil.Error,OSError):pass
        if len(matching)>1:raise RuntimeError('Close extra Gtuner instances before connecting.')
        if matching:self.gtuner_launched=True;return
        subprocess.Popen([str(executable),str(folder/'titan_meter.gpc'),str(folder/'gtuner_meter.py')],cwd=executable.parent)
        self.gtuner_launched=True

    def _connect_bridge(self):
        service=getattr(self,'bridge_service',None)
        if service is None or (hasattr(service,'process') and service.process.poll() is not None):
            if service:service.close()
            try:self.bridge_service=BridgeProcess(sys.executable,PROJECT/'run_live.py',log_dir=PROJECT/'test_results'/'backend')
            except OSError as exc:
                self.bridge_setup_status.set('Bridge restart failed: '+str(exc));return
        inventory=getattr(self,'hardware_inventory',{})
        if inventory.get('auto_connect'):self.hardware_autoconnect_id=inventory['devices'][0]['id']
        self.bridge_enabled.set(True);self.trigger_armed.set(False)
        self._apply_green()
        if self.bridge_service:self.bridge_service.configure(enabled=True)
        self.pending_reconnect=True;self.reconnect_attempts=0
        self._prepare_bridge()

    def _disconnect_bridge(self):
        self.pending_reconnect=False;self.resume_after_reconnect=False;self.capture_launch_requested=False
        Desktop._cancel_pending_bridge(self)
        self.bridge_enabled.set(False);self.trigger_armed.set(False);self._apply_green()
        if self.bridge_service:self.bridge_service.configure(enabled=False)
        self.bridge_setup_status.set('App bridge disconnected. Capture and AI can continue.')
        self.bridge_seen_ready=False;self._show_slots(None,None)

    def _show_slots(self,slots,active):
        for i in range(1,10):
            name=slots.get(i,slots.get(str(i))) if slots else None
            current=self.slot_table.item(str(i),'values')
            label=name or (current[0] if current and slots is None else 'Unavailable — Gtuner did not expose a label')
            self.slot_table.item(str(i),values=(label,'Active — heartbeat verified' if active==i else 'Not verified'))
            if hasattr(self,'live_slots'):self.live_slots.item(str(i),text=('● ' if active==i else '')+str(i),values=(label,))

    def _retry_bridge(self):
        if self.pending_reconnect and not self.preparing_bridge:
            if self.reconnect_attempts<3:
                self.reconnect_attempts+=1;self._prepare_bridge()
            else:
                self.pending_reconnect=False
                self.bridge_setup_status.set('Reconnect attempts exhausted. Check USB PROG and the Gtuner CV worker, then Connect / Reconnect.')

    def _desired_bridge_slot(self):
        if self.auto_slot.get() and self.titan_slot.get()!='Auto (meter bridge)':return slot_number(self.titan_slot.get())
        return getattr(self,'verified_titan_slot',None)

    def _bridge_slot_changed(self):
        Desktop._cancel_pending_bridge(self)
        self.verified_titan_slot=None
        self.trigger_armed.set(False)
        if self.bridge_service:
            self.bridge_service.allow_output(False);self.bridge_service.clear()
            self.bridge_service.configure(expected_slot=Desktop._desired_bridge_slot(self))
        self._apply_green()
        if self.bridge_enabled.get():self._prepare_bridge()

    def _cancel_pending_bridge(self):
        self.gtuner_discovery_generation=getattr(self,'gtuner_discovery_generation',0)+1
        self.preparing_bridge=False

    def _bridge_prepare_timeout(self,generation):
        if generation!=self.gtuner_discovery_generation or not self.preparing_bridge:return
        Desktop._cancel_pending_bridge(self)
        if self.bridge_service:self.bridge_service.allow_output(False);self.bridge_service.clear()
        self.trigger_armed.set(False)
        self.bridge_setup_status.set('Bridge setup timed out. Output is disabled; check Gtuner and the USB/CV worker before reconnecting.')
        if self.pending_reconnect and self.bridge_enabled.get():self.after(3000,self._retry_bridge)

    def _prepare_bridge(self,for_capture=False,force_slot=False,read_only=False):
        if self._busy(allow_capture=True,allow_detection=True):return
        if self.bridge_service is None:
            self.bridge_setup_status.set('Local bridge service is unavailable. Capture and AI remain usable.');return
        inventory=getattr(self,'hardware_inventory',{})
        if inventory.get('state') in ('missing','bootloader','multiple','driver_problem','not_started'):
            self.bridge_setup_status.set(inventory['message']);return
        try:selected=None if self.titan_slot.get()=='Auto (meter bridge)' else slot_number(self.titan_slot.get())
        except ValueError as exc:self.bridge_setup_status.set(str(exc));return
        self.trigger_armed.set(False);self._apply_green()
        self.gtuner_discovery_generation=getattr(self,'gtuner_discovery_generation',0)+1
        generation=self.gtuner_discovery_generation
        self.preparing_bridge=True
        self.bridge_setup_status.set('Checking the live bridge before opening any Gtuner window...')
        executable=self.gtuner_path.get();auto_host=self.auto_gtuner.get()
        switch=self.auto_slot.get() or force_slot
        service=self.bridge_service
        service.configure(expected_slot=selected if switch else None)
        try:folder=self._export_bridge()
        except (OSError,ValueError) as exc:
            self.preparing_bridge=False;self.bridge_setup_status.set(str(exc));return
        def still_current():return generation==self.gtuner_discovery_generation and not getattr(self,'hardware_closing',False)
        def work():
            controller=None;slots=None;result=None;error=None;found=None
            try:
                if not still_current():return
                target=selected if switch else None
                fast=service.verify(timeout=1,expected_slot=target)
                if fast['ready'] and not force_slot and not read_only:
                    result=fast
                else:
                    found=discover_gtuner(executable)
                    if not still_current():return
                    if found and auto_host:launch_gtuner_background(found,folder)
                    # Receiver availability and slot selection are independent.
                    # A missing CV worker must not skip loading the selected slot.
                    current=service.verify(timeout=2,expected_slot=target)
                    if current['ready'] and not force_slot and not read_only:
                        result=current
                    elif found:
                        if not still_current():return
                        controller=GtunerController(found);controller.connect()
                        try:slots=controller.read_slots()
                        except Exception:slots={}
                        if switch and not read_only:
                            if target is None:target=choose_meter_slot(slots)
                            if not still_current():return
                            service.configure(expected_slot=target)
                            controller.select_slot(target)
                        cv_error=None
                        if current.get('receiver')!='receiving' and not read_only:
                            try:controller.start_cv(active=still_current)
                            except Exception as exc:cv_error=str(exc)
                        result=service.verify(timeout=3,expected_slot=target if switch and not read_only else None)
                        if cv_error and not result.get('ready'):result=dict(result,stage=result['stage']+'. '+cv_error)
                        if read_only:result=dict(result,read_only=True)
                    else:
                        result=current
                    if not result['ready'] and not read_only:
                        error=result['stage']+'. Connect Titan PROG to this PC, compile and install the updated X-only bridge 3.4 titan_meter.gpc in the selected slot, and start bridge/gtuner_meter.py in Gtuner Computer Vision with Null Video Input. Keep that worker running; opening the files alone does not start it. Console OUTPUT/authentication is separate.'
                if found and result is not None:result=dict(result,gtuner_path=str(found))
            except Exception as exc:
                error=str(exc);result=service.status()
                if not force_slot and not read_only and result.get('ready'):error=None
                else:result=dict(result,ready=False)
            finally:
                if controller is not None:controller.close()
            if still_current():self.messages.put(('bridge_prepared',(False,dict(result or {},prepare_generation=generation),error,slots)))
        self.after(12000,lambda:self._bridge_prepare_timeout(generation))
        threading.Thread(target=work,daemon=True).start()

    def _open_original_test(self):
        # Use the actual standalone pair, not the meter GPC's newer test branch.
        self._disconnect_bridge()
        try:
            folder=self._export_bridge()
            if not (folder/'console_button_check.gpc').is_file():raise OSError('Original test GPC is missing')
            found=discover_gtuner(self.gtuner_path.get())
            if found:
                subprocess.Popen([str(found),str(folder/'console_button_check.gpc'),str(folder/'console_button_check.py')],cwd=Path(found).parent)
            else:self._open_path(folder)
            self.console_test_status.set('Original files opened. Select the original test GPC slot and run console_button_check in Gtuner CV with Null Input, then Test A. The app has not programmed or selected a slot.')
        except (OSError,ValueError) as exc:self.console_test_status.set(str(exc))

    def _console_tap(self,button):
        if button not in ('A','X','RELEASE_X','HOLD_X') or getattr(self,'console_test_busy',False):return
        if sys.platform!='win32':
            self.console_test_status.set('PowerShell console tests require Windows.');return
        if time.monotonic()-getattr(self,'console_test_at',-10)<1:
            self.console_test_status.set('Wait one second between console tests.');return
        service=self.bridge_service
        status=service.status() if service else {}
        hold_ms=750
        if button=='HOLD_X':
            try:
                hold_ms=int(self.console_hold_ms.get())
                if not 100<=hold_ms<=2000:raise ValueError()
            except (ValueError,TypeError):
                self.console_test_status.set('X hold duration must be 100–2000 ms.');return
        # Diagnostic sending must work with the original standalone receiver,
        # which has no app API, version handshake, heartbeat or execution counter.
        self._disarm_shots()
        if not status.get('ready') or type(status.get('device_test_count')) is not int:
            # Keep auto-reconnect from selecting the meter slot during a legacy test.
            self._disconnect_bridge()
        try:
            script=self._export_bridge()/'console_button_check.py'
            if not script.is_file():raise OSError('Matching console test sender is missing')
            configured=getattr(self,'gtuner_python',None)
            sender=configured.get() if configured else ''
            if not sender or not Path(sender).is_file():sender=sys.executable
            arguments=['--hold-x-ms',str(hold_ms)] if button=='HOLD_X' else ['--tap',button]
            command=BridgeProcess.powershell_command(sender,[str(script),*arguments])
        except OSError as exc:
            self.console_test_status.set(str(exc));return
        self.console_test_busy=True;self.console_test_at=time.monotonic()
        self.console_test_status.set('PowerShell: holding X for '+str(hold_ms)+' ms, then releasing...' if button=='HOLD_X' else 'PowerShell: sending '+button+'...')
        baseline=status.get('device_test_count')
        def send():
            result='';log=''
            try:
                outcome=subprocess.run(command,capture_output=True,text=True,timeout=8,
                    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                log=outcome.stdout+outcome.stderr
                if outcome.returncode:raise OSError('PowerShell sender failed: '+log.strip())
                deadline=time.monotonic()+2
                while service is not None and type(baseline) is int and time.monotonic()<deadline:
                    current=service.status()
                    if (current.get('ready') and type(current.get('device_test_count')) is int
                        and current['device_test_count']!=baseline and current.get('device_test_button')=={'A':1,'X':2,'RELEASE_X':3,'HOLD_X':3}[button]):
                        result=('Titan reports executing X release.' if button in ('RELEASE_X','HOLD_X') else 'Titan reports issuing '+button+' (100 ms).')+' Check the Xbox screen; Xbox receipt is unverified.';break
                    time.sleep(.025)
                if not result and button in ('RELEASE_X','HOLD_X'):result='PowerShell X '+('hold/release' if button=='HOLD_X' else 'release')+' request sent. No Titan acknowledgment observed; the standalone test has none. Check Gtuner for holding/releasing X and watch Xbox. Install test pair 1.1 or meter bridge 3.5. No retry was sent.'
                if not result:result='Original manual '+button+' request sent once. No execution acknowledgment was observed; the standalone test does not provide one. Check Gtuner for CONSOLE TEST: Titan issuing '+button+' and watch Xbox. Sending does not confirm delivery.'
            except (OSError,subprocess.SubprocessError) as exc:result=str(exc)
            self.messages.put(('console_test_done',(result,log)))
        threading.Thread(target=send,daemon=True).start()

    def _start_ai_x_shot(self):
        if getattr(self,'ai_shot_starting',False):return
        blocked=self._arming_blocker()
        status=self.bridge_service.status() if self.bridge_service else {}
        gate=status.get('device_gate')
        if not blocked and (type(gate) is not int or not gate & 2048):
            blocked='Install meter bridge 3.6 and restart gtuner_meter for AI X shots.'
        if not blocked and (self._detector_status() or {}).get('status')!='absent':
            blocked='Wait for the current meter to disappear before starting an AI X shot.'
        if not blocked and gate & (512|4096):
            blocked='Release physical X and wait for the current AI hold to finish.'
        if blocked:self.session_status.set(blocked);return
        if sys.platform!='win32':
            self.session_status.set('AI X shot launch requires Windows PowerShell.');return
        self._arm_shots()
        if not self.trigger_armed.get():return
        try:
            script=self._export_bridge()/'console_button_check.py'
            configured=getattr(self,'gtuner_python',None)
            sender=configured.get() if configured else ''
            if not sender or not Path(sender).is_file():sender=sys.executable
            command=BridgeProcess.powershell_command(sender,[str(script),'--ai-x-shot'])
        except OSError as exc:
            self._disarm_shots();self.session_status.set(str(exc));return
        self.ai_shot_starting=True
        self.session_status.set('Waiting for Titan to arm, then starting one AI-timed X hold…')
        service=self.bridge_service
        def start():
            result='';log=''
            try:
                deadline=time.monotonic()+3
                while time.monotonic()<deadline:
                    current=service.status();word=current.get('device_gate')
                    if not current.get('ready') or not current.get('output_allowed'):
                        raise OSError('AI X shot cancelled: bridge or output permission changed.')
                    if type(word) is int and word & 2048 and (word & 15) in (0,5,6):break
                    time.sleep(.025)
                else:raise OSError('Titan did not become armed and ready. No X hold was sent.')
                baseline=current.get('device_test_count')
                outcome=subprocess.run(command,capture_output=True,text=True,timeout=8,
                    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                log=outcome.stdout+outcome.stderr
                if outcome.returncode:raise OSError('AI X sender failed: '+log.strip())
                deadline=time.monotonic()+2
                while time.monotonic()<deadline:
                    current=service.status()
                    if (current.get('ready') and type(baseline) is int
                        and current.get('device_test_count')!=baseline and current.get('device_test_button')==5):
                        result='Titan accepted AI X hold. Fresh green controls release; watch AI release sent, bridge command and Xbox.';break
                    time.sleep(.025)
                if not result:result='AI X hold was sent once but not acknowledged. Check the meter worker and script; no retry was sent.'
            except (OSError,subprocess.SubprocessError) as exc:result=str(exc)
            self.messages.put(('ai_shot_started',(result,log)))
        threading.Thread(target=start,daemon=True).start()

    def _green_values(self):
        defaults=green_config()
        values={key:(str(var.get()) if key in ('direction','shot_mode') else
            float(var.get()) if type(defaults[key]) is float else int(var.get()))
            for key,var in self.green_vars.items()}
        return green_config(dict(values,armed=self.trigger_armed.get(),shot_mode='timing'))

    def _shot_mode_changed(self):
        # A different controller mechanic always requires explicit re-arming.
        self._disarm_shots()
        self.session_status.set('Shot mode changed — release X, then arm when ready.')

    def _release_feedback(self,text):
        indicator=getattr(self,'release_status',None)
        changed=indicator is None or indicator.get()!=text
        if indicator is not None:indicator.set(text)
        session=getattr(self,'session_status',None)
        if session is not None:session.set(text)
        logger=getattr(self,'_log',None)
        if changed and logger:logger('X release: '+text+'\n')

    def _toggle_x_release(self):
        if self.trigger_armed.get():self._arm_shots()
        else:self._disarm_shots()

    def _apply_green(self):
        try:config=self._green_values()
        except ValueError as exc:
            self.trigger_armed.set(False)
            if self.bridge_service:self.bridge_service.allow_output(False);self.bridge_service.clear()
            Desktop._release_feedback(self,'X override OFF — invalid green settings: '+str(exc))
            messagebox.showerror('Invalid green settings',str(exc));return
        blocked=None
        if config['armed']:
            blocked=self._arming_blocker()
            if blocked:self.trigger_armed.set(False);config=dict(config,armed=False)
        if self.live_active and self.active_out:
            from run_live import atomic_write
            try:atomic_write(self.active_out/'green_settings.json',json.dumps(config).encode())
            except OSError as exc:
                self.trigger_armed.set(False);config=dict(config,armed=False)
                blocked='Shots disarmed — settings could not be applied: '+str(exc)
                if self.bridge_service:self.bridge_service.clear()
        if self.bridge_service:self.bridge_service.allow_output(config["armed"] and self.bridge_enabled.get())
        self._save()
        self.status.set(blocked or ('Green settings applied • '+('X override enabled' if config['armed'] else 'dry run')))
        Desktop._release_feedback(self,('X override OFF — '+blocked) if blocked else
            ('X override ARMED — release X once, then hold X through green; Titan releases its output.' if config['armed'] else 'X override OFF — capture and detection can continue.'))

    def _detector_status(self):
        if not self.live_active or not self.active_out:return None
        value=None
        try:
            value=json.loads((self.active_out/'live_status.json').read_text(encoding='utf-8'))
            if 0<=time.time()-value['timestamp']<=.5:
                self.detector_status_cache=(self.active_out,value)
                return value
        except (OSError,ValueError,KeyError,TypeError):pass
        cached=getattr(self,'detector_status_cache',None)
        if cached and cached[0]==self.active_out and 0<=time.time()-cached[1]['timestamp']<=.5:return cached[1]
        return None

    def _arming_blocker(self):
        if getattr(self,'console_test_busy',False):return 'Wait for the console output test to finish before arming.'
        if self.preparing_bridge:return 'Wait for bridge preparation before arming.'
        if not self.capture_active or not self._capture_ready():return 'Start a live capture before arming.'
        if not self.live_active or not self._detector_status():return 'Wait for fresh AI detections before arming.'
        if not self.bridge_enabled.get() or not self.bridge_service:
            return 'Enable and connect the Titan bridge before arming.'
        bridge=self.bridge_service.status()
        if not bridge.get('ready'):
            return 'Titan bridge is not ready: '+str(bridge.get('stage','connect and verify the bridge.'))
        if bridge.get('output_path')!='console_release':
            return 'Restart the updated gtuner_meter CV worker to use the tested console X-release path.'
        if type(bridge.get('device_gate')) is not int or not (bridge['device_gate'] & 256):
            return 'Install this app’s exported titan_meter.gpc, then restart its matching gtuner_meter worker.'
        return None

    def _arm_shots(self):
        self.trigger_armed.set(True);self._apply_green()
        if self.trigger_armed.get():Desktop._release_feedback(self,'X override ARMED — release X once, then hold X through green; Titan releases its output.')

    def _disarm_shots(self):
        self.trigger_armed.set(False)
        if self.bridge_service:
            self.bridge_service.allow_output(False);self.bridge_service.clear()
        self._apply_green()
        Desktop._release_feedback(self,'X override OFF — capture and detection can continue.')

    def _start_session(self):
        if self.session_requested:return
        if self._busy(allow_capture=True,allow_detection=self.live_active,allow_bridge=True):return
        if not Path(self.model.get()).is_file():
            messagebox.showinfo('Choose a model','Select your trained meter weight before starting a session.');return
        if not self._settings() or not self._python():return
        self.session_requested=True;self.session_deadline=time.monotonic()+30;self.session_engine_started=self.live_active
        self.trigger_armed.set(False)
        if self.compute.get()=='cpu':self.compute.set('auto')
        self.session_status.set('Preparing capture, GPU detector and bridge…')
        self._connect_bridge()
        if not self.capture_active:self._start_capture()
        self.after(100,self._session_tick)

    def _session_tick(self):
        if not self.session_requested:return
        if time.monotonic()>=self.session_deadline:
            self.session_requested=False
            self.trigger_armed.set(False);self._apply_green()
            self.session_status.set('Setup incomplete — shots disarmed. Check the capture and bridge status below.')
            return
        if self.capture_active and self._capture_ready():
            if not self.live_active:
                if self.session_engine_started:
                    self.session_requested=False
                    self.session_status.set('Detector stopped — shots disarmed. Check the output panel before restarting.')
                    return
                self.session_status.set('Starting detector; checking the Gtuner receiver…')
                self._live()
                if not self.live_active:
                    self.session_requested=False;return
                self.session_engine_started=True
            data=self._detector_status()
            if data and self.bridge_service and self.bridge_service.status().get('ready') and not self.preparing_bridge:
                self.session_requested=False
                self.session_status.set('Session ready — preview, AI and Titan connected. Arm Meter Shots when ready.')
                return
        elif not self.capture_active and not getattr(self,'capture_handoff_pending',False):
            self.session_requested=False
            self.session_status.set('Capture could not start — check the output panel.')
            return
        self.after(150,self._session_tick)

    def _stop_session(self):
        self.session_requested=False
        self._disarm_shots();self._stop_capture()
        self.session_status.set('Stopping session — controller output disarmed.')

    def _choose_environment(self):
        chosen=filedialog.askopenfilename(parent=self,title="Choose environment Python executable",
            filetypes=[("Python executable","python.exe"),("All files","*.*")])
        if chosen:self.environment.set(chosen);self._refresh_choices();self._save()

    def _input_changed(self):
        notes={'1280':'1280 • current baseline for the small meter.',
               '960':'960 • compare speed and missed frames against 1280.',
               '640':'640 • speed trial; validate small-meter recall before switching.'}
        self.input_hint.set(notes.get(self.imgsz.get(),'Custom input size. Validate on held-out footage.'))

    def _model_changed(self):
        value=self.model.get()
        try:resolved=str(Path(value).resolve()) if value else ''
        except (OSError,ValueError):resolved=value
        entry=next((item for item in self.catalog if os.path.normcase(item['path'])==os.path.normcase(resolved)),None)
        if entry:
            self.model_choice.set(entry['label'])
            self.model_detail.set(f"{entry['size_bytes']/1048576:.1f} MB  •  {entry['path']}")
        elif value:
            self.model_choice.set(Path(value).name)
            self.model_detail.set(value)
        else:
            self.model_choice.set('No weights added')
            self.model_detail.set('Add your trained .pt weight to begin.')

    def _select_model(self,event=None):
        if self._busy():self._model_changed();return
        chosen=self.model_choices.get(self.model_choice.get())
        if chosen:
            self.model.set(chosen);self._save()
            self.status.set('Selected weight: '+self.model_choice.get())

    def _use_library_model(self,event=None):
        selection=self.model_tree.selection()
        if not selection:return
        self.model_choice.set(self.model_tree.item(selection[0],'text'))
        self._select_model()

    def _refresh_choices(self):
        candidates=[self.environment.get(),sys.executable,
            str(PROJECT/'.venv311'/('Scripts/python.exe' if sys.platform=='win32' else 'bin/python')),*self.known_environments,
            str(PROJECT/'.venv'/('Scripts/python.exe' if sys.platform=='win32' else 'bin/python'))]
        self.known_environments=list(dict.fromkeys(p for p in candidates if p))
        self.environment_menu.configure(values=[p for p in self.known_environments if Path(p).is_file()])
        self.known_models=list(dict.fromkeys([self.model.get(),*self.known_models]))
        self.catalog=model_catalog(PROJECT,self.known_models)
        self.model_choices={item['label']:item['path'] for item in self.catalog}
        self.known_models=list(dict.fromkeys([*self.known_models,*(item['path'] for item in self.catalog)]))
        self.model_menu.configure(values=list(self.model_choices))
        for row in self.model_tree.get_children():self.model_tree.delete(row)
        for index,item in enumerate(self.catalog):
            self.model_tree.insert('','end',iid=str(index),text=item['label'],
                values=(f"{item['size_bytes']/1048576:.1f} MB",item['path']))
        if not Path(self.model.get()).is_file() and self.catalog:self.model.set(self.catalog[0]['path'])
        self._model_changed()

    def _python(self):
        executable=Path(self.environment.get())
        if not executable.is_file():
            messagebox.showerror("Environment missing","Choose an existing Python executable first.");return None
        return str(executable)

    def _choose_gtuner_python(self):
        chosen=filedialog.askopenfilename(parent=self,title='Choose Gtuner CV Python executable',filetypes=[('Python','python.exe'),('All files','*.*')])
        if chosen:self.gtuner_python.set(chosen);self._save()

    def _prepare_gtuner_python(self):
        if getattr(self,'checking_gtuner_python',False):return
        self.checking_gtuner_python=True
        self.gtuner_python_status.set('Preparing OpenCV / PyWin32 in the app’s full Python 3.11 base installation…')
        def work():
            try:self.messages.put(('gtuner_python_checked',prepare_gtuner_runtime(sys.executable)))
            except Exception as exc:self.messages.put(('gtuner_python_checked',dict(error=str(exc))))
        threading.Thread(target=work,daemon=True).start()

    def _check_gtuner_python(self):
        executable=self.gtuner_python.get()
        if not Path(executable).is_file():
            self.gtuner_python_status.set('Choose an existing Python executable first.');return
        if getattr(self,'checking_gtuner_python',False):return
        self.checking_gtuner_python=True;self.gtuner_python_status.set('Checking Python and OpenCV in the selected installation…')
        def check():
            try:self.messages.put(('gtuner_python_checked',inspect_gtuner_python(executable)))
            except Exception as exc:self.messages.put(('gtuner_python_checked',dict(error=str(exc))))
        threading.Thread(target=check,daemon=True).start()

    def _choose_model(self):
        if self._busy():return
        initial=Path(self.model.get()).parent if Path(self.model.get()).is_file() else Path.home()/'Downloads'
        self.lift()
        path=filedialog.askopenfilename(parent=self,title='Add trained meter weight',initialdir=str(initial),
            filetypes=[('PyTorch weights','*.pt'),('All files','*.*')])
        if path:self.model.set(path);self._refresh_choices();self._save()

    def _auto_find(self):
        if getattr(self,'finding_models',False):return
        self.finding_models=True
        self.status.set('Searching Downloads for trained weights...')
        def find():
            try:
                files=[str(p) for p in (Path.home()/'Downloads').rglob('*.pt') if p.is_file()]
                self.messages.put(('models_found',files))
            except OSError as exc:self.messages.put(('models_error',str(exc)))
        threading.Thread(target=find,daemon=True).start()

    def _import_model(self):
        if self._busy() or self.importing_model:return
        initial=Path(self.model.get()).parent if Path(self.model.get()).is_file() else Path.home()/'Downloads'
        chosen=filedialog.askopenfilename(parent=self,title='Import trained model into app',
            initialdir=str(initial),filetypes=[('PyTorch weights','*.pt')])
        if not chosen:return
        self.importing_model=True
        self.status.set('Importing model into app...')
        def work():
            try:self.messages.put(('model_imported',import_model(chosen,PROJECT)))
            except Exception as exc:self.messages.put(('model_error',str(exc)))
        threading.Thread(target=work,daemon=True).start()

    def _choose_dir(self,var):
        path=filedialog.askdirectory(title='Choose labeled run folder')
        if path:var.set(path)

    def _settings(self):
        try:
            width,height,fps,imgsz,seconds=(int(self.width.get()),int(self.height.get()),
                float(self.fps.get()),int(self.imgsz.get()),float(self.seconds.get()))
            if min(width,height,fps,imgsz)<=0 or seconds<0 or fps>240: raise ValueError()
            return width,height,fps,imgsz,seconds
        except ValueError:
            messagebox.showerror('Invalid settings','Width, height, FPS and model input must be positive. Seconds can be 0 for an untimed run.')
            return None

    def _save(self):
        try:green=self._green_values()
        except ValueError:green=load_settings().get('green',green_config())
        self.known_models=list(dict.fromkeys([self.model.get(),*self.known_models]))
        self.known_environments=list(dict.fromkeys([self.environment.get(),*self.known_environments]))
        SETTINGS.write_text(json.dumps(dict(app_runtime_generation=311,model=self.model.get(),known_models=self.known_models,
            known_environments=self.known_environments,source=self.source.get(),
            width=self.width.get(),height=self.height.get(),fps=self.fps.get(),
            imgsz=self.imgsz.get(),seconds=self.seconds.get(),environment=self.environment.get(),
            compute=self.compute.get(),controller=self.controller.get(),bridge_enabled=self.bridge_enabled.get(),
            gtuner_python=self.gtuner_python.get(),gtuner_path=self.gtuner_path.get(),auto_gtuner=self.auto_gtuner.get(),
            titan_slot=self.titan_slot.get(),auto_slot=self.auto_slot.get(),
            capture_type=self.capture_type.get(),render_mode=self.render_mode.get(),display_fps=self.display_fps.get(),
            show_boxes=self.show_boxes.get(),
            green=dict(green,armed=False)),indent=2),encoding='utf-8')

    def _out(self,prefix):
        base=PROJECT/'test_results'/f'{prefix}_{time.strftime("%Y%m%d_%H%M%S")}'
        out=base
        suffix=2
        while out.exists():
            out=Path(f'{base}_{suffix}');suffix+=1
        out.mkdir(parents=True)
        return out

    def _busy(self,allow_capture=False,allow_detection=False,allow_bridge=False):
        if self.capture_active and not allow_capture:
            messagebox.showinfo("Capture active","Stop Capture before this operation.")
            return True
        if self.finding_gtuner and not allow_bridge:
            messagebox.showinfo('Finding Gtuner','Wait for automatic Gtuner discovery to finish first.')
            return True
        if self.preparing_bridge and not allow_bridge:
            messagebox.showinfo('Bridge check active','Wait for the bridge check to finish first.')
            return True
        if self.updating:
            messagebox.showinfo("Update in progress","Wait for the update to finish first.")
            return True
        if self.importing_model:
            messagebox.showinfo('Model import active','Wait for the model copy to finish first.')
            return True
        if self.process and self.process.poll() is None and not allow_detection:
            messagebox.showinfo('Already running','Stop or finish the current run first.')
            return True
        return False

    def _capture_mode(self):
        modes={'DirectShow / MJPG':('dshow','MJPG'),'DirectShow / YUY2':('dshow','YUY2'),
            'DirectShow / BGR3':('dshow','BGR3'),'Media Foundation / MJPG':('msmf','MJPG'),
            'Media Foundation / YUY2':('msmf','YUY2'),'Auto / Default':('auto','')}
        return modes.get(self.capture_type.get(),('auto',''))

    def _render_preset(self,event=None):
        self.display_fps.set('120' if self.render_mode.get()=='Performance' else '60')
        self._display_changed()

    def _display_changed(self,event=None):
        self._save()
        destination=self.capture_out if self.capture_active else self.active_out if self.live_active else None
        if destination:
            settings=dict(fps=int(self.display_fps.get()),mode=self.render_mode.get().lower(),enabled=self.preview.get())
            from run_live import atomic_write
            atomic_write(destination/'display_settings.json',json.dumps(settings).encode())

    def _common(self,values):
        width,height,fps,imgsz,seconds=values
        backend,fourcc=self._capture_mode()
        return ['--source',self.source.get(),'--width',str(width),'--height',str(height),
            '--capture-fps',str(fps),'--fourcc',fourcc,'--backend',backend,'--seconds',str(seconds)]

    def _capture_ready(self):
        if not self.capture_active or not self.capture_out:return False
        try:
            value=json.loads((self.capture_out/'capture_status.json').read_text())
            if value.get('state')=='stopped':return False
            if value.get('state')=='capturing' and value.get('frames',0)>0 and 0<=time.time()-value['timestamp']<=2:return True
        except (OSError,ValueError,KeyError):pass
        monitor=None
        try:
            monitor=RawMailbox(self.capture_out/'source_frames.bin')
            progress=monitor.progress()
            return bool(progress and 0<=time.time()-progress['timestamp']<=.75)
        except (OSError,ValueError):return False
        finally:
            if monitor:monitor.close()

    def _start_capture(self):
        if self.capture_active:
            self.status.set('Capture is already running. Start AI Detection separately.');return
        if self.updating:
            messagebox.showinfo('Update active','Wait for the update to finish.');return
        try:
            width,height,fps=int(self.width.get()),int(self.height.get()),float(self.fps.get())
            if min(width,height,fps)<=0 or fps>240:raise ValueError()
        except ValueError:
            messagebox.showerror('Capture settings','Choose positive dimensions and a capture FPS up to 240.');return
        probe=getattr(self,'capture_probe_process',None)
        if probe is not None and probe.poll() is None:
            deadline=getattr(self,'capture_handoff_deadline',0)
            if not deadline:
                self.capture_handoff_deadline=time.monotonic()+5
                probe.terminate()
            elif time.monotonic()>=deadline:
                probe.kill()
            self.status.set('Giving Preview Capture the card — releasing the app capture probe...')
            self.capture_handoff_pending=True
            self.after(100,self._resume_capture_handoff);return
        self.capture_handoff_deadline=0
        out=self._out('capture_view')
        settings=dict(fps=int(self.display_fps.get()),mode=self.render_mode.get().lower(),enabled=self.preview.get())
        (out/'display_settings.json').write_text(json.dumps(settings))
        # The app environment already has OpenCV; CUDA/model settings are irrelevant here.
        cmd=[sys.executable,str(PROJECT/'run_live.py'),'--capture-only','--headless',
            '--out',str(out),'--stop-file',str(out/'capture_stop.request'),
            '--display-fps',self.display_fps.get(),'--render-mode',self.render_mode.get().lower(),
            '--display-settings',str(out/'display_settings.json')]+self._common((width,height,fps,1280,0))
        try:
            process,priority=capture_worker(cmd,PROJECT)
            try:(out/'capture_priority.json').write_text(json.dumps(priority),encoding='utf-8')
            except OSError:pass
        except OSError as exc:
            messagebox.showerror('Capture failed',str(exc));return
        self._close_raw_preview();self.capture_process=process;self.capture_out=out;self.capture_active=True
        self.capture_progress_at=time.monotonic();self.capture_progress_frames=0;self.capture_watchdog_fired=False
        self.display_count=0;self.display_started=time.perf_counter();self.status.set('Opening capture card — AI is stopped')
        def read_output():
            for line in process.stdout:self.messages.put(('text','Capture: '+line))
            self.messages.put(('capture_done',(process.wait(),str(out))))
        threading.Thread(target=read_output,daemon=True).start()

    def _resume_capture_handoff(self):
        if not getattr(self,'capture_handoff_pending',False):return
        self.capture_handoff_pending=False
        self._start_capture()

    def _stop_capture(self):
        self.capture_handoff_pending=False;self.capture_handoff_deadline=0
        self._stop()
        if self.capture_active and self.capture_out:
            (self.capture_out/'capture_stop.request').touch()
            self.status.set('Stopping capture and saving capture diagnostics...')

    def _live(self,bridge_ready=False):
        if self._busy(allow_capture=True,allow_bridge=True):return
        if not self._capture_ready():
            messagebox.showinfo('Start Capture first','Start Capture and wait for the console image before starting AI detection.');return
        values=self._settings()
        if not values:return
        try:green=self._green_values()
        except ValueError as exc:
            messagebox.showerror('Invalid green settings',str(exc));return
        model=Path(self.model.get())
        if not model.is_file():
            messagebox.showerror('Model missing','Select your trained best.pt file first.');return
        self._save()
        out=self._out('live')
        executable=self._python()
        if not executable:return
        cmd=[executable,str(PROJECT/'run_live.py'),'--model',str(model),
             '--out',str(out),'--imgsz',str(values[3])] + self._common(values)
        (out/'green_settings.json').write_text(json.dumps(green),encoding='utf-8')
        cmd += ['--green-settings',str(out/'green_settings.json')]
        cmd += ['--headless','--ui-status','--device',self.compute.get(),'--stop-file',str(out/'stop.request')]
        cmd += ['--capture-session',str(self.capture_out)]
        config=out/'bridge_service.json'
        # Keep the SDK attached even while disabled, so bridge reconnect is independent of AI.
        cmd += ['--bridge-service',str(config)]
        if self.bridge_service:
            self.bridge_service.configure(enabled=self.bridge_enabled.get(),expected_slot=Desktop._desired_bridge_slot(self))
            try:
                self.bridge_service.write_config(config)
                self.bridge_config_host=id(self.bridge_service)
            except OSError as exc:self.bridge_setup_status.set('AI running; bridge client waiting for host: '+str(exc))
        # Slot policy is enforced by the bridge host and can change without restarting AI.
        (out/'app_profile.json').write_text(json.dumps(dict(environment=executable,
            compute=self.compute.get(),controller=self.controller.get(),model=str(model))),encoding='utf-8')
        self.live_active=True
        self.bridge_seen_ready=False
        self.active_out=out
        self.last_preview_stamp=None
        self.bridge_status.set('Waiting for Gtuner • Titan Two hardware not verified' if self.bridge_enabled.get() else 'Bridge off')
        if self.record.get():cmd.append('--record')
        if self.raw.get():cmd.append('--record-raw')
        self._start(cmd,out)

    def _probe(self):
        if self._busy():return
        values=self._settings()
        if not values:return
        if values[4] == 0:
            messagebox.showerror('Capture probe duration','Capture-only probes need a positive number of seconds.');return
        self._save()
        out=self._out('capture')
        executable=self._python()
        if not executable:return
        cmd=[executable,str(PROJECT/'run_live.py'),'--probe-only','--out',str(out)] + self._common(values)
        self._start(cmd,out)
        self.capture_probe_process=self.process

    def _setup(self):
        if self._busy():return
        self._save()
        out=self._out('setup')
        executable=self._python()
        if not executable:return
        cmd=[executable,str(PROJECT/'testing'/'self_check.py'),
             '--model',self.model.get(),'--out',str(out/'setup_check.json')]
        self._start(cmd,out)

    def _train(self):
        if self._busy():return
        paths=[Path(v.get()) for v in (self.train_run,self.val_run,self.test_run)]
        if not self.model.get() or not Path(self.model.get()).is_file() or any(not p.is_dir() for p in paths):
            messagebox.showerror('Missing inputs','Choose the model and three separate labeled run folders.');return
        out=self._out('learning')
        executable=self._python()
        if not executable:return
        cmd=[executable,str(PROJECT/'testing'/'learning_loop.py'),'train',
             '--train-run',str(paths[0]),'--validation-run',str(paths[1]),
             '--test-run',str(paths[2]),'--model',self.model.get(),
             '--out',str(out),'--imgsz',self.imgsz.get()]
        self._start(cmd,out)

    def _start(self,cmd,out):
        if self.process and self.process.poll() is None:
            messagebox.showinfo('Already running','Stop or finish the current run first.');return
        self.status.set(f'Running • {out}')
        self._log(f'Output: {out}\n')
        try:
            self.process=subprocess.Popen(cmd,cwd=PROJECT,stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,text=True,bufsize=1)
        except OSError as exc:
            self.live_active=False
            self.status.set('Could not start');messagebox.showerror('Start failed',str(exc));return
        process=self.process
        self.process_out=out
        def read_output():
            for line in process.stdout:self.messages.put(('text',line))
            self.messages.put(('done',(process.wait(),str(out))))
        threading.Thread(target=read_output,daemon=True).start()

    def _stop(self,reconnect=False):
        self.session_requested=False
        if self.bridge_service:
            self.bridge_service.allow_output(False);self.bridge_service.clear()
        self.trigger_armed.set(False)
        if not reconnect:
            Desktop._cancel_pending_bridge(self)
            self.pending_reconnect=False;self.resume_after_reconnect=False;self.capture_launch_requested=False
        if self.process and self.process.poll() is None:
            if self.live_active and self.active_out:
                (self.active_out/'stop.request').touch()
                self.status.set('Stopping gracefully; saving test results...')
            else:
                self.process.terminate();self.status.set('Stopping...')

    def _poll(self):
        try:
            # Bound log/worker bursts so Tk can continue rendering and accepting input.
            for _ in range(64):
                kind,value=self.messages.get_nowait()
                if kind=='text':self._log(value)
                elif kind=='ai_shot_started':
                    self.ai_shot_starting=False
                    result,log=value;self.session_status.set(result)
                    self._log('\nAI X shot: '+result+'\n'+log+'\n')
                elif kind=='console_test_done':
                    self.console_test_busy=False
                    result,log=value;self.console_test_status.set(result)
                    self._log('\nConsole test: '+result+'\n'+log+'\n')
                elif kind=='gtuner_python_checked':
                    self.checking_gtuner_python=False
                    if value.get('error'):self.gtuner_python_status.set(value['error'])
                    else:
                        summary=f"Python {value['version']} / {value['bits']}-bit | OpenCV {value.get('opencv','MISSING')}"
                        win32=value.get('pywin32',{})
                        if win32:summary+=' | PyWin32 '+('OK' if all(item.get('ok') for item in win32.values()) else 'MISSING / import failure')
                        if value.get('installation_folder'):
                            self.gtuner_python.set(value['executable']);self._save()
                            summary+=' | Select this folder in Gtuner: '+value['installation_folder']
                        if value.get('opencv_error'):summary+=' | '+value['opencv_error']
                        if not value.get('python_libraries'):summary+=' | Python DLL not found in base installation'
                        if value.get('bits')!=64:summary+=' | Match Python architecture to your Gtuner build'
                        self.gtuner_python_status.set(summary+' | Gtuner embedded compatibility still requires its own checks.')
                    try:
                        root=PROJECT/'test_results';root.mkdir(exist_ok=True)
                        (root/'gtuner_python_check.json').write_text(json.dumps(value,indent=2),encoding='utf-8')
                    except OSError:pass
                elif kind=='models_found':
                    self.finding_models=False
                    self.known_models.extend(value);self._refresh_choices();self._save()
                    self.status.set(f'{len(self.catalog)} available weights in the model library')
                elif kind=='models_error':
                    self.finding_models=False;self.status.set('Model search failed: '+value)
                elif kind=='gtuner_found':
                    self.finding_gtuner=False
                    found,prepare,prepare_args,error,generation=value
                    if valid_gtuner_path(self.gtuner_path.get()):found=self.gtuner_path.get()
                    if found:
                        if self.gtuner_path.get()!=found:self.gtuner_launched=False
                        self.gtuner_path.set(found);self._save()
                        self.bridge_setup_status.set('Gtuner IV found: '+found)
                        if prepare and generation==self.gtuner_discovery_generation and not self.live_active:
                            self._prepare_bridge(**(prepare_args or {}))
                    else:
                        self.bridge_setup_status.set('Gtuner IV not found. Install/extract Gtuner IV, then Find Gtuner or Browse.'+(' '+error if error else ''))
                elif kind=='titan_hardware':
                    self._handle_titan_hardware(value)
                elif kind=='bridge_prepared':
                    capture,result,error,slots=value
                    if result and result.get('prepare_generation',self.gtuner_discovery_generation)!=self.gtuner_discovery_generation:continue
                    self.preparing_bridge=False
                    ready=bool(result and result.get('ready'))
                    read_only=bool(result and result.get('read_only'))
                    self._show_slots(slots,result.get('active_slot') if ready else None)
                    detail=('Bridge ready — Titan slot '+str(result.get('active_slot') or 'unknown')+' and payload echo verified; console unverified.' if ready else
                        'Slot labels refreshed; device not checked.' if read_only else
                        'Bridge not ready — '+(error or result.get('stage','Device echo missing')))
                    self.bridge_setup_status.set(detail)
                    if result and result.get('gtuner_path'):self.gtuner_path.set(result['gtuner_path']);self._save()
                    try:
                        diagnostic=PROJECT/'test_results'/'bridge_latest.json';diagnostic.parent.mkdir(exist_ok=True)
                        diagnostic.write_text(json.dumps(dict(timestamp=time.time(),result=result,error=error),indent=2))
                    except OSError:pass
                    if ready:
                        self.verified_titan_slot=result.get('active_slot')
                        if self.bridge_service:self.bridge_service.configure(enabled=self.bridge_enabled.get(),expected_slot=self.verified_titan_slot)
                        self.pending_reconnect=False;self.resume_after_reconnect=False;self.reconnect_attempts=0
                    elif self.pending_reconnect:self.after(3000,self._retry_bridge)
                elif kind=='capture_done':
                    code,out=value
                    if str(self.capture_out)!=out:continue
                    self.capture_active=False;self._close_raw_preview()
                    if self.live_active:self._stop()
                    self.status.set(f'Capture stopped (exit {code}) • {out}')
                    self._log(f'Capture results: {out}\n')
                    if code:
                        try:reason=json.loads((Path(out)/'capture_status.json').read_text()).get('error')
                        except (OSError,ValueError):reason=None
                        if reason:self.status.set(reason);self._log(reason+'\n')
                elif kind=='release':
                    self.checking_updates=False
                    self._handle_release(value)
                elif kind=='update_error':
                    self.checking_updates=False
                    self.update_status.set(f'Update check failed; will retry: {value}')
                elif kind=='install_error':
                    self._update_failed(value)
                elif kind=='model_imported':
                    self.importing_model=False
                    self.model.set(str(value));self._refresh_choices();self._save()
                    self.status.set(f'Model ready in app: {value}')
                    messagebox.showinfo('Model imported','The model is stored in the app folder. Run Check Setup before live detection.')
                elif kind=='model_error':
                    self.importing_model=False
                    self.status.set('Model import failed')
                    messagebox.showerror('Model import failed',value)
                elif kind=='downloaded':
                    self._finish_update(value)
                else:
                    code,out=value
                    if self.process_out is not None and str(self.process_out)!=out:continue
                    self.live_active=False
                    if not self.capture_active:self._close_raw_preview()
                    if self.pending_reconnect:self.after(1000,self._retry_bridge)
                    self.bridge_status.set('Run stopped • bridge inactive')
                    self.status.set(f'Finished (exit {code}) • {out}')
                    self._log(f'\nFinished with exit code {code}. Results: {out}\n')
                    self._refresh()
        except queue.Empty:pass
        self._capture_tick()
        if hasattr(self,'component_status'):
            def health(process):
                return ('running / PID '+str(getattr(process,'pid','—'))) if process and process.poll() is None else 'stopped'
            bridge=getattr(self,'bridge_service',None)
            self.component_status.set('Capture: '+health(self.capture_process)+'\nEngine: '+health(self.process if self.live_active else None)+'\nModel: '+Path(self.model.get()).name+'\nBridge: '+health(getattr(bridge,'process',None))+' / '+getattr(bridge,'transport','Python'))
        self._bridge_tick()
        self._preview_tick()
        self.after(100,self._poll)

    def _capture_tick(self):
        if not self.capture_active or not self.capture_out:return
        try:
            value=json.loads((self.capture_out/'capture_status.json').read_text())
            if value.get('frames',0)>getattr(self,'capture_progress_frames',0):
                self.capture_progress_frames=value['frames'];self.capture_progress_at=time.monotonic()
            if value.get('error'):self.status.set(value['error'])
            if not self.live_active:
                self.capture_fps_label.set('—');self.inference_label.set('Off');self.observation_label.set('CAPTURE ONLY')
                self.live_metrics.set(f"Capture {value['capture_fps']:.1f} FPS | AI stopped | card reports {value['reported_capture']['fps']:.1f} FPS")
        except (OSError,ValueError,KeyError):pass

        # Status JSON may be locked on Windows while the frame publisher is healthy.
        try:
            monitor=getattr(self,'capture_monitor',None)
            if monitor is None and (self.capture_out/'source_frames.bin').exists():
                monitor=self.capture_monitor=RawMailbox(self.capture_out/'source_frames.bin')
            progress=monitor.progress() if monitor else None
            if progress and progress['frames']>getattr(self,'capture_progress_frames',0):
                self.capture_progress_frames=progress['frames'];self.capture_progress_at=time.monotonic()
        except (OSError,ValueError):pass
        process=self.capture_process
        if (process is not None and process.poll() is None and
                not getattr(self,'capture_watchdog_fired',False) and
                time.monotonic()-getattr(self,'capture_progress_at',time.monotonic())>15):
            self.capture_watchdog_fired=True
            self._stop_capture()
            error='Capture driver produced no frames for 15 seconds. Stopped this app capture worker; release the card in other software and retry Preview Capture.'
            from run_live import atomic_write
            try:atomic_write(self.capture_out/'capture_status.json',json.dumps(dict(state='stopped',frames=self.capture_progress_frames,timestamp=time.time(),error=error)).encode())
            except OSError:pass
            process.terminate()
            self.after(2000,lambda:process.kill() if process.poll() is None else None)
            self.status.set(error)

    def _bridge_tick(self):
        if self.bridge_service is None or time.monotonic()-self.bridge_tick_at<.5:return
        self.bridge_tick_at=time.monotonic();value=self.bridge_service.status()
        reader=getattr(self.bridge_service,'read_output',None)
        if callable(reader):
            output=reader()
            if isinstance(output,str) and output:self._log('[Python backend] '+output)
        backend_stage=(value.get('backend_pid'),value.get('backend_exit_code'),value.get('api'))
        if backend_stage!=getattr(self,'last_backend_stage',None):
            self.last_backend_stage=backend_stage
            if value.get('backend_log'):self._log('Backend: '+str(value.get('backend_transport'))+' | '+str(value.get('stage'))+' | Log: '+value['backend_log']+'\n')
        if (self.live_active and self.active_out and hasattr(self.bridge_service,'config')
                and self.bridge_service.config.exists() and getattr(self,'bridge_config_host',None)!=id(self.bridge_service)):
            try:
                self.bridge_service.write_config(self.active_out/'bridge_service.json')
                self.bridge_config_host=id(self.bridge_service)
            except OSError:pass
        self._show_slots(None,value.get('active_slot') if value['ready'] else None)
        summary=('Bridge disabled • ' if not self.bridge_enabled.get() else '')+value['stage']
        if value.get('rtt_ms') is not None:summary+=f" | CV acknowledgments {value.get('acknowledged',0)} | RTT {value['rtt_ms']:.1f} ms"
        self.bridge_status.set(summary)
        if self.trigger_armed.get() and (not self.live_active or not self.capture_active or not self._detector_status()):
            self._disarm_shots()
            Desktop._release_feedback(self,'X override OFF — capture or detection stopped updating. Start Session, wait for live frames, then enable again.')
        if hasattr(self,'bridge_next_step'):
            if value['ready']:next_step='Transport verified. Test console input separately in an offline practice session; console output is unverified.'
            elif value.get('receiver')!='receiving':next_step='Next: start gtuner_meter.py in Gtuner Computer Vision with Null Video Input. Check its Python/OpenCV indicators. Disconnect other Titan hosts first.'
            elif 'slot' in value['stage'].lower():next_step='Next: select the slot containing the matching titan_meter.gpc and verify it again.'
            else:next_step='Next: compile/install the matching titan_meter.gpc, select its slot, and check the PROG connection in Gtuner.'
            self.bridge_next_step.set(next_step)
        if value['ready']:self.bridge_seen_ready=True
        elif self.bridge_seen_ready and self.bridge_enabled.get() and not self.pending_reconnect:
            self.trigger_armed.set(False);self._apply_green()
            self.pending_reconnect=True;self.reconnect_attempts=0;self.bridge_seen_ready=False
            self.bridge_setup_status.set('Bridge lost. Trigger disabled; capture and AI continue while reconnecting.')
            Desktop._release_feedback(self,'X override OFF — Titan connection lost: '+str(value.get('stage','no live acknowledgment')))
            self.after(1000,self._retry_bridge)

    @staticmethod
    def _new_output(parent,**options):
        return scrolledtext.ScrolledText(parent,state='disabled',background='#0b141f',foreground='#9eb4ca',
            insertbackground='#e6edf5',relief='flat',font=('Consolas',10),**options)

    def _open_output(self):
        if self.output_window is not None:
            self.output_window.deiconify();self.output_window.lift();return
        window=self.output_window=tk.Toplevel(self)
        window.title('NBA2K CV — Output');window.geometry('1000x600');window.minsize(400,200)
        self.output_log=self._new_output(window)
        self.output_log.pack(fill='both',expand=True)
        self.output_log.configure(state='normal')
        self.output_log.insert('end',self.log.get('1.0','end-1c'))
        self.output_log.configure(state='disabled');self.output_log.see('end')
        def close():
            self.output_log=None;self.output_window=None;window.destroy()
        window.protocol('WM_DELETE_WINDOW',close)

    def _video_target(self):
        return self.video_screen if self.video_window is not None else self.screen

    def _open_video(self,fullscreen=False):
        if self.video_window is None:
            window=self.video_window=tk.Toplevel(self)
            window.title('NBA2K CV — Live capture');window.geometry('1280x760');window.minsize(480,300)
            tools=ttk.Frame(window,padding=6);tools.pack(fill='x')
            ttk.Button(tools,text='Dock video',command=self._dock_video).pack(side='left')
            ttk.Button(tools,text='Toggle fullscreen (F11)',command=self._toggle_video_fullscreen).pack(side='left',padx=8)
            ttk.Label(tools,text='Esc exits fullscreen',style='Muted.TLabel').pack(side='left')
            self.video_screen=ttk.Label(window,anchor='center',style='Video.TLabel',text='Waiting for live capture…')
            self.video_screen.pack(fill='both',expand=True)
            window.protocol('WM_DELETE_WINDOW',self._dock_video)
            window.bind('<Escape>',lambda event:window.attributes('-fullscreen',False))
            window.bind('<F11>',lambda event:self._toggle_video_fullscreen())
            self.screen.configure(image='',text='Video is in a separate window. Close it to dock again.')
            self.raw_preview_photo_size=None;self.last_preview_stamp=None
        self.video_window.attributes('-fullscreen',bool(fullscreen))
        self.video_window.deiconify();self.video_window.lift()

    def _toggle_video_fullscreen(self):
        if self.video_window is not None:
            self.video_window.attributes('-fullscreen',not bool(self.video_window.attributes('-fullscreen')))

    def _dock_video(self):
        window=self.video_window
        self.video_window=None;self.video_screen=None
        self.raw_preview_photo_size=None;self.last_preview_stamp=None
        if window is not None:window.destroy()

    def _present_image(self,image):
        from PIL import ImageTk
        target=self._video_target()
        if self.raw_preview_photo_size==image.size and hasattr(self.preview_image,'paste'):
            self.preview_image.paste(image)
        else:
            self.preview_image=ImageTk.PhotoImage(image)
            self.raw_preview_photo_size=image.size
        target.configure(image=self.preview_image,text='')

    def _preview_tick(self):
        if not self.live_active or not self.active_out:return
        try:
            report=self.active_out/"live_status.json"
            data=json.loads(report.read_text(encoding="utf-8"))
            age=max(0,time.time()-data["timestamp"])
            self.capture_fps_label.set(f"{data['fps']:.1f}")
            self.inference_label.set(f"{data['inference_ms']:.1f} ms / {data.get('device','?')}")
            self.observation_label.set(data['status'].upper() if age<=1 else 'STALE')
            self.live_metrics.set(f"Capture {data.get('capture_fps',0):.1f} FPS | AI {data['fps']:.1f} FPS | skipped {data.get('skipped_inference_frames',0)} | {data.get('device','unknown device')} inference {data['inference_ms']:.1f} ms | "
                f"{data['status']} | preview age {age:.1f}s" + (" • STALE" if age>1 else ""))
            if 'green' in data:
                measurement=data['green'];trigger=data.get('trigger',{})
                gate=self.bridge_service.status().get('device_gate') if self.bridge_service else None
                reasons={0:'ready',1:'disarmed',2:'link timeout',3:'R3 cancel held',4:'release X to reset',5:'waiting for physical hold',6:'waiting for green command',7:'green command expired',8:'shot action started'}
                device_note=('Timing' if gate & 16 else 'Rhythm')+' / '+reasons.get(gate & 15,'unknown') if type(gate) is int else 'install X-only meter GPC 3.4'
                if type(gate) is int:
                    device_note+=' / X input: '+('HELD' if gate & 512 else 'released')
                    if gate & 4096:device_note+=' / AI X HELD — waiting for fresh green'
                    elif gate & 8192:device_note+=' / AI X TIMEOUT — released without a green event'
                self.green_status.set(f"{'ARMED' if trigger.get('armed') else 'DRY RUN'} | "
                    f"in green: {measurement.get('in_green',False)} | event {trigger.get('event_id',0)} | "
                    f"AI release sent {self.bridge_service.status().get('auto_release_event',0) if self.bridge_service else 0} | "
                    f"Bridge command {self.bridge_service.status().get('device_test_button') if self.bridge_service else None} (3=X release) | {device_note} | "
                    f"{measurement.get('reason','')}" + (' • STALE' if age>.25 else ''))
            if self.capture_active or (self.active_out/'live_bgr.bin').exists():return
            image=self.active_out/'preview.png'
            stamp=image.stat().st_mtime_ns
            if stamp!=self.last_preview_stamp:
                from PIL import Image
                target=self._video_target()
                with Image.open(image) as source:photo=source.convert('RGB')
                photo.thumbnail((max(1,target.winfo_width()),max(1,target.winfo_height())))
                self._present_image(photo)
                self.last_preview_stamp=stamp
        except (OSError,ValueError,KeyError,tk.TclError):pass

    def _close_raw_preview(self):
        if getattr(self,'capture_monitor',None):
            self.capture_monitor.close();self.capture_monitor=None
        if self.raw_preview is not None:
            self.raw_preview.close();self.raw_preview=None
        samples=getattr(self,'preview_timings',[])
        out=getattr(self,'capture_out',None)
        if samples and out:
            summary={}
            for key in ('prepare_ms','tk_upload_ms','frame_age_ms'):
                values=sorted(sample[key] for sample in samples)
                summary[key]=dict(p50=values[len(values)//2],p95=values[int(.95*(len(values)-1))],maximum=values[-1])
            try:(out/'preview_timing.json').write_text(json.dumps(dict(samples=len(samples),last_window_only=True,stages=summary),indent=2))
            except OSError:pass
        self.preview_timings=[]
        self.raw_preview_sequence=0
        self.raw_preview_photo_size=None
        self.last_display_timestamp=None
        self.display_metrics.set('Display stopped')

    def _display_tick(self):
        started=time.perf_counter()
        try:
            display_out=self.capture_out if self.capture_active else self.active_out if self.live_active else None
            if display_out and self.preview.get():
                if self.raw_preview is None and (display_out/'live_bgr.bin').exists():
                    self.raw_preview=PreviewWorker(display_out/'live_bgr.bin')
                target=self._video_target()
                size=(max(1,target.winfo_width()),max(1,target.winfo_height()))
                overlay=(self.active_out/'live_status.json') if self.live_active and self.active_out and self.show_boxes.get() else None
                sample=self.raw_preview.read(self.raw_preview_sequence,size,overlay) if self.raw_preview else None
                if sample:
                    image=sample['image'];upload_start=time.perf_counter()
                    self._present_image(image)
                    self.preview_upload_ms=(time.perf_counter()-upload_start)*1000
                    self.preview_prepare_ms=sample['prepare_ms']
                    self.raw_preview_sequence=sample['sequence'];self.display_count+=1
                    self.last_display_timestamp=sample['timestamp']
                    if not hasattr(self,'preview_timings'):self.preview_timings=[]
                    self.preview_timings.append(dict(prepare_ms=self.preview_prepare_ms,tk_upload_ms=self.preview_upload_ms,
                        frame_age_ms=max(0,time.time()-sample['timestamp'])*1000))
                    if len(self.preview_timings)>600:del self.preview_timings[:-600]
                if self.last_display_timestamp is not None and time.perf_counter()-self.last_display_metric>=.1:
                    self.last_display_metric=time.perf_counter()
                    elapsed=max(.001,time.perf_counter()-self.display_started)
                    age=max(0,time.time()-self.last_display_timestamp)
                    self.display_metrics.set(f"Direct card BGR | rendered {self.display_count/elapsed:.1f} FPS / limit {self.display_fps.get()} | frame age {age*1000:.0f} ms | prep {getattr(self,'preview_prepare_ms',0):.1f} ms | Tk upload {getattr(self,'preview_upload_ms',0):.1f} ms"+(' • STALE' if age>.5 else ''))
        except (OSError,ValueError,tk.TclError):pass
        try:fps=int(self.display_fps.get())
        except ValueError:fps=60
        interval=1/max(1,fps)
        self.after(max(1,round(1000*(interval-(time.perf_counter()-started)))),self._display_tick)

    def _log(self,value):
        for widget in (self.log,self.output_log):
            if widget is None:continue
            widget.configure(state='normal');widget.insert('end',value)
            if int(widget.index('end-1c').split('.')[0])>2000:widget.delete('1.0','501.0')
            if self.log_follow.get():widget.see('end')
            widget.configure(state='disabled')

    def _refresh(self):
        for item in self.runs.get_children():self.runs.delete(item)
        root=PROJECT/'test_results'
        if not root.exists():return
        for folder in sorted(root.iterdir(),key=lambda p:p.stat().st_mtime,reverse=True):
            if not folder.is_dir():continue
            report=folder/'live_run.json'
            try:data=json.loads(report.read_text(encoding='utf-8'))
            except (OSError,ValueError):continue
            self.runs.insert('', 'end',iid=str(folder),text=folder.name,
                values=(data.get('device',''),f"{data.get('measured_fps',0):.1f}",data.get('frames','')))

    def _selected(self):
        selection=self.runs.selection()
        return Path(selection[0]) if selection else None

    def _show_result(self,event=None):
        folder=self._selected()
        if not folder:return
        report=json.loads((folder/'live_run.json').read_text(encoding='utf-8'))
        queue_path=folder/'review'/'queue.json'
        review=json.loads(queue_path.read_text(encoding='utf-8')) if queue_path.is_file() else None
        value=json.dumps(dict(run=report,review_summary=dict(selected=review['selected']) if review else None),indent=2)
        self.detail.configure(state='normal');self.detail.delete('1.0','end')
        self.detail.insert('end',value);self.detail.configure(state='disabled')

    def _open_path(self,path):
        if sys.platform=='win32':os.startfile(str(path))
        else:subprocess.Popen(['xdg-open',str(path)])

    def _open_result(self):
        folder=self._selected()
        if folder:self._open_path(folder)

    def _periodic_update_check(self):
        self._check_online(silent=True)
        self.after(300000,self._periodic_update_check)

    def _dismiss_update_notice(self):
        if self.update_notice is not None:
            self.update_notice.destroy()
            self.update_notice=None

    def _handle_release(self,info):
        self.latest_release=info
        if not info:
            self.update_status.set('You have the latest version.')
            self.install_button.configure(state='disabled')
            self.update_banner.pack_forget()
            self._dismiss_update_notice()
            return
        self.update_status.set(f"Update {info['version']} available. Stop any run before installing.")
        self.install_button.configure(state='normal')
        self.banner_install.configure(state='normal')
        self.update_banner.pack(fill='x',before=self.tabs,padx=18,pady=(0,10))
        if info['version'] in self.notified_versions:return
        self.notified_versions.add(info['version'])
        self._dismiss_update_notice()
        notice=tk.Toplevel(self)
        self.update_notice=notice
        notice.title('NBA2K CV — Update available')
        notice.transient(self)
        notice.resizable(False,False)
        body=ttk.Frame(notice,padding=24);body.pack(fill='both',expand=True)
        ttk.Label(body,text='An update is available',font=('Segoe UI',16,'bold')).pack(anchor='w')
        ttk.Label(body,text=f"Installed: {current_version(PROJECT)}   Available: {info['version']}").pack(anchor='w',pady=(12,6))
        ttk.Label(body,text='Install downloads the update, verifies it, and restarts the app.\nYour models, settings, and results stay in place.').pack(anchor='w')
        buttons=ttk.Frame(body);buttons.pack(fill='x',pady=(18,0))
        ttk.Button(buttons,text='Later',command=self._dismiss_update_notice).pack(side='right')
        ttk.Button(buttons,text='Install Update',command=self._install_online).pack(side='right',padx=8)
        notice.protocol('WM_DELETE_WINDOW',self._dismiss_update_notice)

    def _check_online(self,silent=False):
        if self.checking_updates or self.updating:return
        feed=configured_feed(PROJECT)
        if not feed:
            self.update_status.set('Online update feed has not been configured.')
            return
        self.checking_updates=True
        self.update_status.set('Checking for updates...')
        def work():
            try:self.messages.put(('release',fetch_manifest(feed,PROJECT)))
            except Exception as exc:self.messages.put(('update_error',str(exc)))
        threading.Thread(target=work,daemon=True).start()

    def _install_online(self):
        if self._busy() or self.checking_updates or not self.latest_release:return
        release=dict(self.latest_release)
        self.updating=True
        self._dismiss_update_notice()
        self.install_button.configure(state='disabled')
        self.banner_install.configure(state='disabled')
        self.update_status.set('Downloading and verifying update...')
        def work():
            try:self.messages.put(('downloaded',download_release(release,PROJECT)))
            except Exception as exc:self.messages.put(('install_error',str(exc)))
        threading.Thread(target=work,daemon=True).start()

    def _update_failed(self,error):
        self.updating=False
        self.update_status.set(f'Update failed; you can retry: {error}')
        if self.latest_release:
            self.install_button.configure(state='normal')
            self.banner_install.configure(state='normal')

    def _finish_update(self,archive):
        try:result=apply_update(archive,PROJECT)
        except Exception as exc:
            self._update_failed(str(exc))
            messagebox.showerror('Update failed',str(exc));return
        messagebox.showinfo('Updated',f"Updated to version {result['version']}. The app will restart.")
        if sys.platform=='win32':
            subprocess.Popen(['cmd.exe','/c',str(PROJECT/'Launch NBA2K CV.bat')],cwd=PROJECT)
        else:subprocess.Popen([sys.executable,str(PROJECT/'app.py')],cwd=PROJECT)
        self.destroy()

    def _update_local(self):
        if self._busy():return
        if self.importing_model:
            messagebox.showinfo('Model import active','Wait for the model copy to finish first.');return
        if self.process and self.process.poll() is None:
            messagebox.showinfo('Run active','Finish the current run before updating.');return
        archive=newest_download(Path.home()/'Downloads',PROJECT)
        if archive is None:
            chosen=filedialog.askopenfilename(title='Select NBA2K_CV_Update.zip',
                filetypes=[('ZIP update','*.zip')])
            if not chosen:return
            archive=Path(chosen)
        if not messagebox.askyesno('Update project',f'Install {archive.name}?\nYour data and model stay in place.'):
            return
        self._finish_update(archive)

    def _close(self):
        if self.updating:
            messagebox.showinfo('Update in progress','Wait for the update to finish before closing.');return
        if self.importing_model:
            messagebox.showinfo('Model import active','Wait for the model copy to finish first.');return
        if self.process and self.process.poll() is None:
            if not messagebox.askyesno('Run active','Stop the current test and close?'):return
            if self.live_active:
                self._stop()
                self.status.set('Saving results before closing...')
                self.after(100,self._wait_close)
                return
            self.process.terminate()
        if self.capture_active:
            self._stop_capture();self.after(100,self._wait_close);return
        self._close_raw_preview()
        self.destroy()

    def _wait_close(self):
        if self.capture_active and self.capture_out:(self.capture_out/"capture_stop.request").touch()
        if (self.process and self.process.poll() is None) or (self.capture_process and self.capture_process.poll() is None):
            self.after(100,self._wait_close)
        else:self.destroy()

    def destroy(self):
        self.session_requested=False
        Desktop._cancel_pending_bridge(self)
        self.hardware_closing=True
        self.capture_handoff_pending=False
        if getattr(self,'capture_active',False) and self.capture_out:
            (self.capture_out/'capture_stop.request').touch()
        if getattr(self,'bridge_service',None):
            self.bridge_service.close();self.bridge_service=None
        self._close_raw_preview()
        super().destroy()


def bootstrap_windows_controls():
    """Migrate old updater restarts through the managed 3.11 launcher."""
    if sys.platform!='win32':return False
    import importlib.util
    import struct
    managed=PROJECT/'.venv311'/'Scripts'/'python.exe'
    correct=(sys.version_info[:2]==(3,11) and struct.calcsize('P')==8 and Path(sys.executable).resolve().parent==managed.parent.resolve())
    if not correct or any(importlib.util.find_spec(name) is None for name in ('cv2','win32api','pywintypes','pythoncom','pywinauto','psutil')):
        subprocess.Popen(['cmd.exe','/c',str(PROJECT/'Launch NBA2K CV.bat')],cwd=PROJECT)
        return True
    return False


if __name__=='__main__':
    if not bootstrap_windows_controls():Desktop().mainloop()
