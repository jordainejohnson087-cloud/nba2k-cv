"""Simple Windows desktop control panel for capture, detection, review, and updates."""
import json
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
from run_live import green_config, RawMailbox, GtunerController, slot_number, choose_meter_slot


PROJECT=Path(__file__).resolve().parent
SETTINGS=PROJECT/'app_settings.json'


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
        self.geometry(f'{min(1280,max(1000,self.winfo_screenwidth()-24))}x{min(900,max(600,self.winfo_screenheight()-120))}+0+0')
        self.minsize(1000,600)
        self._theme()
        self.process=None
        self.active_out=None
        self.live_active=False
        self.preview_image=None
        self.last_preview_stamp=None
        self.importing_model=False
        self.preparing_bridge=False
        self.gtuner_launched=False
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
        self.compute=tk.StringVar(value=saved.get('compute','auto'))
        self.controller=tk.StringVar(value=saved.get('controller','Xbox / GameSir'))
        self.bridge_enabled=tk.BooleanVar(value=saved.get('bridge_enabled',False))
        self.gtuner_path=tk.StringVar(value=saved.get('gtuner_path',''))
        self.auto_gtuner=tk.BooleanVar(value=saved.get('auto_gtuner',True))
        self.titan_slot=tk.StringVar(value=saved.get('titan_slot','Auto (meter bridge)'))
        self.auto_slot=tk.BooleanVar(value=saved.get('auto_slot',True))
        self.bridge_setup_status=tk.StringVar(value='Not checked — install the script in Gtuner once')
        self.bridge_status=tk.StringVar(value='Bridge off • Titan Two hardware not verified')
        self.live_metrics=tk.StringVar(value='Preview idle')
        try:green=green_config(saved.get('green',{}))
        except ValueError:green=green_config()
        self.trigger_armed=tk.BooleanVar(value=False)
        self.green_vars={key:tk.StringVar(value=str(value)) for key,value in green.items() if key!='armed'}
        self.green_status=tk.StringVar(value='Green detector: awaiting observed frames • trigger off')
        self.record=tk.BooleanVar(value=False)
        self.raw=tk.BooleanVar(value=False)
        self.preview=tk.BooleanVar(value=True)
        self.status=tk.StringVar(value='Ready')
        self.update_status=tk.StringVar(value='Update check pending')
        self.latest_release=None
        self.checking_updates=False
        self.updating=False
        self.notified_versions=set()
        self.update_notice=None
        self._build()
        self.after(100,self._poll)
        self.after(10,self._display_tick)
        self.after(500,lambda:self._check_online(silent=True))
        self.after(1000,self._startup_bridge)
        self.after(300000,self._periodic_update_check)
        self.protocol('WM_DELETE_WINDOW',self._close)

    def _theme(self):
        self.configure(background='#0b1118')
        style=ttk.Style(self)
        style.theme_use('clam')
        style.configure('.',font=('Segoe UI',10),background='#0b1118',foreground='#e6edf5')
        style.configure('TFrame',background='#0b1118')
        style.configure('Card.TFrame',background='#131e2b')
        style.configure('TLabel',background='#0b1118',foreground='#e6edf5')
        style.configure('Card.TLabel',background='#131e2b',foreground='#e6edf5')
        style.configure('Muted.TLabel',foreground='#9aabbf')
        style.configure('CardMuted.TLabel',background='#131e2b',foreground='#9aabbf')
        style.configure('Brand.TLabel',font=('Segoe UI',22,'bold'))
        style.configure('Section.TLabel',font=('Segoe UI',11,'bold'),background='#131e2b')
        style.configure('Metric.TLabel',font=('Segoe UI',23,'bold'),background='#131e2b',foreground='#55ddbc')
        style.configure('Video.TLabel',background='#080d13',foreground='#8798aa',font=('Segoe UI',12))
        style.configure('TButton',background='#203044',foreground='#e6edf5',padding=(12,8),borderwidth=0)
        style.map('TButton',background=[('active','#2c425b')],foreground=[('disabled','#677689')])
        style.configure('Accent.TButton',background='#52ddba',foreground='#07231c',font=('Segoe UI',10,'bold'))
        style.map('Accent.TButton',background=[('active','#80eed1')],foreground=[('disabled','#677689')])
        style.configure('Stop.TButton',background='#4e2634',foreground='#ffb5c3')
        style.configure('TEntry',bordercolor='#30465c',lightcolor='#30465c',darkcolor='#30465c',fieldbackground='#1a293a',foreground='#e6edf5',insertcolor='#e6edf5',padding=6)
        style.configure('TCombobox',bordercolor='#30465c',lightcolor='#30465c',darkcolor='#30465c',arrowcolor='#abc1d6',fieldbackground='#1a293a',foreground='#e6edf5',background='#24364b',padding=6)
        style.map('TCombobox',fieldbackground=[('readonly','#1a293a')],foreground=[('readonly','#e6edf5')],
                  selectbackground=[('readonly','#1a293a')],selectforeground=[('readonly','#e6edf5')])
        style.configure('TCheckbutton',background='#131e2b',foreground='#e6edf5',padding=(0,4))
        style.map('TCheckbutton',background=[('active','#131e2b')])
        style.configure('TNotebook',background='#0b1118',borderwidth=0,bordercolor='#0b1118',lightcolor='#0b1118',darkcolor='#0b1118')
        style.configure('TNotebook.Tab',bordercolor='#263c51',lightcolor='#263c51',darkcolor='#263c51',background='#142131',foreground='#a5b6c8',padding=(18,11))
        style.map('TNotebook.Tab',background=[('selected','#22384a')],foreground=[('selected','#61e5c5')])
        style.configure('TLabelframe',background='#131e2b',bordercolor='#2a3b4f',padding=12)
        style.configure('TLabelframe.Label',background='#131e2b',foreground='#61e5c5')
        style.configure('Vertical.TScrollbar',background='#263c51',troughcolor='#0b1118',bordercolor='#0b1118',arrowcolor='#abc1d6',lightcolor='#263c51',darkcolor='#263c51')
        style.map('Vertical.TScrollbar',background=[('active','#35536f')])
        style.configure('Treeview',background='#131e2b',fieldbackground='#131e2b',foreground='#e6edf5',rowheight=34,borderwidth=0)
        style.configure('Treeview.Heading',background='#203044',foreground='#b8c7d7',padding=8)
        style.map('Treeview',background=[('selected','#235448')],foreground=[('selected','#ffffff')])
        self.option_add('*TCombobox*Listbox.background','#1a293a')
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
        canvas=tk.Canvas(outer,background='#0b1118',highlightthickness=0,width=width)
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
        live.columnconfigure(0,weight=0,minsize=350);live.columnconfigure(1,weight=1)
        live.rowconfigure(0,weight=1)
        sidebar,controls=self._scroll_panel(live)
        sidebar.grid(row=0,column=0,sticky='nsew',padx=(0,14))
        detector=self._card(controls,'01   DETECTOR')
        ttk.Label(detector,text='Trained weight',style='CardMuted.TLabel').pack(anchor='w')
        self.model_menu=ttk.Combobox(detector,textvariable=self.model_choice,state='readonly',width=26)
        self.model_menu.pack(fill='x',pady=(5,7))
        self.model_menu.bind('<<ComboboxSelected>>',self._select_model)
        ttk.Label(detector,textvariable=self.model_detail,style='CardMuted.TLabel',wraplength=280).pack(anchor='w',pady=4)
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
        ttk.Checkbutton(runtime,text='Enable Titan Two bridge',variable=self.bridge_enabled).pack(anchor='w',pady=5)
        ttk.Button(runtime,text='Open bridge settings',command=lambda:self.tabs.select(self.bridge_tab)).pack(anchor='w')
        work=ttk.Frame(live);work.grid(row=0,column=1,sticky='nsew')
        work.columnconfigure(0,weight=1);work.rowconfigure(2,weight=1)
        actions=ttk.Frame(work);actions.grid(row=0,column=0,sticky='ew',pady=(0,12))
        ttk.Button(actions,text='▶  Start Live Test',style='Accent.TButton',command=self._live).pack(side='left')
        ttk.Button(actions,text='■  Stop',style='Stop.TButton',command=self._stop).pack(side='left',padx=8)
        ttk.Button(actions,text='Check Setup',command=self._setup).pack(side='right')
        ttk.Button(actions,text='Test Capture Only',command=self._probe).pack(side='right',padx=8)
        metrics=ttk.Frame(work);metrics.grid(row=1,column=0,sticky='ew',pady=(0,12))
        for col,(title,var) in enumerate([('PROCESSED FPS',self.capture_fps_label),('INFERENCE',self.inference_label),('METER STATUS',self.observation_label)]):
            metrics.columnconfigure(col,weight=1)
            metric=ttk.Frame(metrics,style='Card.TFrame',padding=12)
            metric.grid(row=0,column=col,sticky='nsew',padx=(0,8 if col<2 else 0))
            ttk.Label(metric,text=title,style='CardMuted.TLabel').pack(anchor='w')
            ttk.Label(metric,textvariable=var,style='Metric.TLabel').pack(anchor='w',pady=(5,0))
        video=ttk.Frame(work,style='Card.TFrame',padding=14);video.grid(row=2,column=0,sticky='nsew')
        video.columnconfigure(0,weight=1);video.rowconfigure(1,weight=1)
        ttk.Label(video,text='LIVE CAPTURE',style='Section.TLabel').grid(row=0,column=0,sticky='w',pady=(0,10))
        self.screen=ttk.Label(video,text='Ready for your capture\n\nChoose a weight, then start live detection.',style='Video.TLabel',anchor='center')
        self.screen.grid(row=1,column=0,sticky='nsew')
        ttk.Label(video,textvariable=self.display_metrics,style='CardMuted.TLabel').grid(row=3,column=0,sticky='w',pady=(0,4))
        ttk.Label(video,textvariable=self.live_metrics,style='CardMuted.TLabel',wraplength=650).grid(row=2,column=0,sticky='w',pady=(10,0))
        ttk.Label(work,textvariable=self.bridge_status,style='Muted.TLabel',wraplength=700).grid(row=3,column=0,sticky='w',pady=10)
        self.log=scrolledtext.ScrolledText(work,height=4,state='disabled',background='#0b141f',foreground='#9eb4ca',insertbackground='#e6edf5',relief='flat',font=('Consolas',9))
        self.log.grid(row=4,column=0,sticky='ew')
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
        ttk.Label(bridge_body,text='Titan Two / Gtuner bridge',font=('Segoe UI',14,'bold')).pack(anchor='w',pady=8)
        ttk.Label(bridge_body,text=(
            'Connect Titan Two PROG to the PC, OUTPUT to the console, and the controller to INPUT-A.\n'
            'One-time setup: install titan_meter.gpc to an unused Gtuner memory slot and select it. '
            'Enable “Recall loaded memory slot between power cycles” in Gtuner Device Configuration.\n'
            'Start gtuner_meter.py in Gtuner Computer Vision with a Null or separate display source. '
            'Do not open the same capture card in both apps.\n'
            'This app opens Gtuner and requests the chosen slot with its documented shortcut. Slot programming still uses Gtuner. '
            'Before bridged capture, it checks a fresh, unarmed v2 payload echo from the Titan script. '
            'This proves the bridge round trip, not console action or shot accuracy.'),
            wraplength=960,justify='left').pack(anchor='w',pady=10)
        launcher=ttk.LabelFrame(bridge_body,text='Startup and bridge readiness',padding=10)
        launcher.pack(fill='x',pady=6)
        row=ttk.Frame(launcher);row.pack(fill='x')
        ttk.Label(row,text='Gtuner IV executable').pack(side='left',padx=(0,8))
        ttk.Entry(row,textvariable=self.gtuner_path).pack(side='left',fill='x',expand=True)
        ttk.Button(row,text='Browse',command=self._browse_gtuner).pack(side='left',padx=8)
        ttk.Checkbutton(launcher,text='Open Gtuner on app startup when Titan bridge is enabled',
            variable=self.auto_gtuner,command=self._save).pack(anchor='w',pady=5)
        slot_controls=ttk.Frame(launcher);slot_controls.pack(fill='x',pady=6)
        ttk.Label(slot_controls,text='Default / selected slot').pack(side='left')
        ttk.Combobox(slot_controls,textvariable=self.titan_slot,values=('Auto (meter bridge)',*tuple(str(i) for i in range(1,10))),
            state='readonly',width=20).pack(side='left',padx=8)
        ttk.Button(slot_controls,text='Switch Slot',command=lambda:self._prepare_bridge(force_slot=True)).pack(side='left')
        ttk.Checkbutton(launcher,text='Automatically select this slot on startup / reconnect / before capture',
            variable=self.auto_slot,command=self._save).pack(anchor='w')
        connection=ttk.Frame(launcher);connection.pack(fill='x',pady=6)
        ttk.Button(connection,text='Connect / Reconnect',command=self._connect_bridge).pack(side='left')
        ttk.Button(connection,text='Disconnect App Bridge',command=self._disconnect_bridge).pack(side='left',padx=8)
        ttk.Button(connection,text='Refresh Slot Names',command=lambda:self._prepare_bridge(read_only=True)).pack(side='left')
        self.slot_table=ttk.Treeview(launcher,columns=('name','state'),show='tree headings',height=9)
        self.slot_table.heading('#0',text='Slot');self.slot_table.column('#0',width=70,stretch=False)
        self.slot_table.heading('name',text='Script label read from Gtuner');self.slot_table.column('name',width=400)
        self.slot_table.heading('state',text='Device confirmation');self.slot_table.column('state',width=180)
        self.slot_table.pack(fill='x',pady=6)
        for i in range(1,10):self.slot_table.insert('', 'end',iid=str(i),text=str(i),values=('Unavailable — refresh with Gtuner open','Not verified'))
        ttk.Label(launcher,textvariable=self.bridge_setup_status,wraplength=940).pack(anchor='w',pady=6)
        calibration=ttk.LabelFrame(bridge_body,text='Green detection and stick flick',padding=10)
        calibration.pack(fill='x',pady=8)
        fields=[('Hue low','hue_low'),('Hue high','hue_high'),('Saturation min','saturation_min'),
            ('Brightness min','value_min'),('Green row fraction','row_coverage'),('Confidence min','min_confidence'),
            ('Confirm frames','confirmations'),('Maximum age ms','max_age_ms'),('Rearm absence ms','rearm_ms'),
            ('Flick delay ms','delay_ms'),('Flick hold ms','hold_ms')]
        for index,(title,key) in enumerate(fields):
            row,column=divmod(index,3)
            ttk.Label(calibration,text=title).grid(row=row,column=column*2,sticky='w',padx=4,pady=3)
            ttk.Entry(calibration,textvariable=self.green_vars[key],width=9).grid(row=row,column=column*2+1,padx=4)
        ttk.Label(calibration,text='Flick direction').grid(row=4,column=0,sticky='w',padx=4)
        ttk.Combobox(calibration,textvariable=self.green_vars['direction'],values=('up','down'),
            state='readonly',width=9).grid(row=4,column=1)
        ttk.Checkbutton(calibration,text='Enable calibrated green trigger (hold shoot button)',
            variable=self.trigger_armed).grid(row=5,column=0,columnspan=6,sticky='w',pady=6)
        ttk.Button(calibration,text='Apply Detection / Flick Settings',command=self._apply_green).grid(row=6,column=0,columnspan=6,sticky='w')
        ttk.Label(calibration,textvariable=self.green_status,wraplength=950).grid(row=7,column=0,columnspan=6,sticky='w',pady=4)
        ttk.Label(bridge_body,textvariable=self.bridge_status).pack(anchor='w',pady=10)
        ttk.Button(bridge_body,text='Open Bridge Files',command=self._open_bridge).pack(anchor='w')
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
            from run_live import GTUNER_SOURCE, HEARTBEAT_SOURCE, METER_GPC_SOURCE
            folder.mkdir(exist_ok=True)
            (folder/'gtuner_meter.py').write_text(GTUNER_SOURCE,encoding='utf-8')
            (folder/'titan_heartbeat.gpc').write_text(HEARTBEAT_SOURCE,encoding='utf-8')
            (folder/'titan_meter.gpc').write_text(METER_GPC_SOURCE,encoding='utf-8')
        if not (folder/'titan_meter.gpc').is_file() or not (folder/'gtuner_meter.py').is_file():
            raise FileNotFoundError('The matching bridge scripts are missing; install the latest app update.')
        return folder

    def _open_bridge(self):
        self._open_path(self._export_bridge())

    def _browse_gtuner(self):
        chosen=filedialog.askopenfilename(title='Select Gtuner IV executable',filetypes=[('Gtuner IV','Gtuner.exe'),('Executable','*.exe')])
        if chosen:
            self.gtuner_path.set(chosen);self._save()

    def _startup_bridge(self):
        if self.bridge_enabled.get() and (self.auto_gtuner.get() or self.auto_slot.get()):self._prepare_bridge()

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
        self.bridge_enabled.set(True)
        self.pending_reconnect=True
        self.reconnect_attempts=0
        if self.live_active:
            self.trigger_armed.set(False);self._apply_green()
            self.resume_after_reconnect=True;self._stop(reconnect=True)
        else:self._prepare_bridge()

    def _disconnect_bridge(self):
        self.pending_reconnect=False;self.resume_after_reconnect=False;self.capture_launch_requested=False
        self.bridge_enabled.set(False);self.trigger_armed.set(False)
        if self.live_active:self._apply_green();self._stop()
        self._save()
        self.bridge_setup_status.set('App bridge disconnected; Gtuner and USB remain connected.')
        self.bridge_seen_ready=False
        self._show_slots({},None)

    def _show_slots(self,slots,active):
        for i in range(1,10):
            name=slots.get(i,slots.get(str(i))) if slots else None
            current=self.slot_table.item(str(i),'values')
            label=name or (current[0] if current and slots is None else 'Unavailable — Gtuner did not expose a label')
            self.slot_table.item(str(i),values=(label,'Active — heartbeat verified' if active==i else 'Not verified'))

    def _retry_bridge(self):
        if self.pending_reconnect and not self.preparing_bridge and not self.live_active:
            if self.reconnect_attempts<3:
                self.reconnect_attempts+=1;self._prepare_bridge()
            else:
                self.pending_reconnect=False
                self.bridge_setup_status.set('Reconnect attempts exhausted. Check USB PROG and the Gtuner CV worker, then Connect / Reconnect.')

    def _prepare_bridge(self,for_capture=False,force_slot=False,read_only=False):
        if self._busy():return
        try:selected=None if self.titan_slot.get()=="Auto (meter bridge)" else slot_number(self.titan_slot.get())
        except ValueError as exc:
            self.bridge_setup_status.set(str(exc));return
        self._save()
        try:
            if not self.gtuner_launched:self._launch_gtuner()
        except (OSError,ValueError,RuntimeError) as exc:
            self.bridge_setup_status.set(str(exc))
            if for_capture:messagebox.showerror('Bridge setup needed',str(exc))
            return
        self.preparing_bridge=True
        self.capture_launch_requested=for_capture
        self.bridge_setup_status.set('Reading Gtuner slots and checking the Titan payload echo...')
        executable=self.gtuner_path.get()
        switch=(self.auto_slot.get() or force_slot) and not read_only
        def work():
            client=None;controller=None;slots={}
            try:
                controller=GtunerController(executable)
                deadline=time.monotonic()+5
                while True:
                    try:controller.connect();break
                    except RuntimeError:
                        if time.monotonic()>=deadline:raise
                        time.sleep(.2)
                try:slots=controller.read_slots()
                except Exception:slots={}
                target=selected
                if switch:
                    if target is None:target=choose_meter_slot(slots)
                    controller.select_slot(target)
                if read_only:
                    result=dict(ready=False,receiver='not checked',hardware='not checked',read_only=True)
                else:
                    from run_live import BridgeClient
                    client=BridgeClient(26740)
                    result=client.probe(expected_slot=target if switch else None)
                self.messages.put(('bridge_prepared',(for_capture,result,None,slots)))
            except Exception as exc:self.messages.put(('bridge_prepared',(for_capture,None,str(exc),slots)))
            finally:
                if client is not None:client.close()
                if controller is not None:controller.close()
        threading.Thread(target=work,daemon=True).start()

    def _green_values(self):
        defaults=green_config()
        values={key:(str(var.get()) if key=='direction' else
            float(var.get()) if type(defaults[key]) is float else int(var.get()))
            for key,var in self.green_vars.items()}
        return green_config(dict(values,armed=self.trigger_armed.get()))

    def _apply_green(self):
        try:config=self._green_values()
        except ValueError as exc:
            messagebox.showerror('Invalid green settings',str(exc));return
        if self.live_active and self.active_out:
            from run_live import atomic_write
            atomic_write(self.active_out/'green_settings.json',json.dumps(config).encode())
        self._save()
        self.status.set('Green settings applied • '+('trigger enabled' if config['armed'] else 'dry run'))

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
        candidates=[self.environment.get(),*self.known_environments,sys.executable,
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
        SETTINGS.write_text(json.dumps(dict(model=self.model.get(),known_models=self.known_models,
            known_environments=self.known_environments,source=self.source.get(),
            width=self.width.get(),height=self.height.get(),fps=self.fps.get(),
            imgsz=self.imgsz.get(),seconds=self.seconds.get(),environment=self.environment.get(),
            compute=self.compute.get(),controller=self.controller.get(),bridge_enabled=self.bridge_enabled.get(),
            gtuner_path=self.gtuner_path.get(),auto_gtuner=self.auto_gtuner.get(),
            titan_slot=self.titan_slot.get(),auto_slot=self.auto_slot.get(),
            capture_type=self.capture_type.get(),render_mode=self.render_mode.get(),display_fps=self.display_fps.get(),
            green=dict(green,armed=False)),indent=2),encoding='utf-8')

    def _out(self,prefix):
        base=PROJECT/'test_results'/f'{prefix}_{time.strftime("%Y%m%d_%H%M%S")}'
        out=base
        suffix=2
        while out.exists():
            out=Path(f'{base}_{suffix}');suffix+=1
        out.mkdir(parents=True)
        return out

    def _busy(self):
        if self.preparing_bridge:
            messagebox.showinfo('Bridge check active','Wait for the bridge check to finish first.')
            return True
        if self.updating:
            messagebox.showinfo("Update in progress","Wait for the update to finish first.")
            return True
        if self.importing_model:
            messagebox.showinfo('Model import active','Wait for the model copy to finish first.')
            return True
        if self.process and self.process.poll() is None:
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
        if self.live_active and self.active_out:
            settings=dict(fps=int(self.display_fps.get()),mode=self.render_mode.get().lower(),enabled=self.preview.get())
            from run_live import atomic_write
            atomic_write(self.active_out/'display_settings.json',json.dumps(settings).encode())

    def _common(self,values):
        width,height,fps,imgsz,seconds=values
        backend,fourcc=self._capture_mode()
        return ['--source',self.source.get(),'--width',str(width),'--height',str(height),
            '--capture-fps',str(fps),'--fourcc',fourcc,'--backend',backend,'--seconds',str(seconds)]

    def _live(self,bridge_ready=False):
        if self._busy():return
        values=self._settings()
        if not values:return
        try:green=self._green_values()
        except ValueError as exc:
            messagebox.showerror('Invalid green settings',str(exc));return
        model=Path(self.model.get())
        if not model.is_file():
            messagebox.showerror('Model missing','Select your trained best.pt file first.');return
        if self.bridge_enabled.get() and not bridge_ready:
            self._prepare_bridge(for_capture=True)
            return
        self._save()
        out=self._out('live')
        executable=self._python()
        if not executable:return
        cmd=[executable,str(PROJECT/'run_live.py'),'--model',str(model),
             '--out',str(out),'--imgsz',str(values[3])] + self._common(values)
        (out/'green_settings.json').write_text(json.dumps(green),encoding='utf-8')
        cmd += ['--green-settings',str(out/'green_settings.json')]
        cmd += ['--headless','--ui-status','--device',self.compute.get(),'--stop-file',str(out/'stop.request')]
        cmd += ['--live-direct','--display-fps',self.display_fps.get(),'--render-mode',self.render_mode.get().lower()]
        (out/'display_settings.json').write_text(json.dumps(dict(fps=int(self.display_fps.get()),mode=self.render_mode.get().lower(),enabled=self.preview.get())))
        cmd += ['--display-settings',str(out/'display_settings.json')]
        if self.preview.get():cmd.append('--ui-preview')
        if self.bridge_enabled.get():
            cmd += ['--bridge-port','26740']
            active=getattr(self,'verified_titan_slot',None)
            if active:cmd += ['--expected-slot',str(active)]
        (out/'app_profile.json').write_text(json.dumps(dict(environment=executable,
            compute=self.compute.get(),controller=self.controller.get(),model=str(model))),encoding='utf-8')
        self._close_raw_preview()
        self.live_active=True
        self.bridge_seen_ready=False
        self.display_count=0;self.display_started=time.perf_counter()
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
        def read_output():
            for line in process.stdout:self.messages.put(('text',line))
            self.messages.put(('done',(process.wait(),str(out))))
        threading.Thread(target=read_output,daemon=True).start()

    def _stop(self,reconnect=False):
        if not reconnect:
            self.pending_reconnect=False;self.resume_after_reconnect=False;self.capture_launch_requested=False
        if self.process and self.process.poll() is None:
            if self.live_active and self.active_out:
                (self.active_out/'stop.request').touch()
                self.status.set('Stopping gracefully; saving test results...')
            else:
                self.process.terminate();self.status.set('Stopping...')

    def _poll(self):
        try:
            while True:
                kind,value=self.messages.get_nowait()
                if kind=='text':self._log(value)
                elif kind=='models_found':
                    self.finding_models=False
                    self.known_models.extend(value);self._refresh_choices();self._save()
                    self.status.set(f'{len(self.catalog)} available weights in the model library')
                elif kind=='models_error':
                    self.finding_models=False;self.status.set('Model search failed: '+value)
                elif kind=='bridge_prepared':
                    self.preparing_bridge=False
                    capture,result,error,slots=value
                    ready=bool(result and result.get('ready'))
                    read_only=bool(result and result.get('read_only'))
                    self._show_slots(slots,result.get('active_slot') if ready else None)
                    detail=('Bridge ready — Titan slot '+str(result.get('active_slot') or 'unknown')+' and payload echo verified; console unverified.' if ready else
                        'Slot labels refreshed; device not checked.' if read_only else
                        'Bridge not ready — '+(error or f"{result['receiver']}; {result['hardware']}. Install the latest meter GPC in the chosen slot and start its CV worker, then retry."))
                    self.bridge_setup_status.set(detail)
                    if ready:
                        self.verified_titan_slot=result.get('active_slot')
                        resume=self.resume_after_reconnect
                        self.pending_reconnect=False;self.resume_after_reconnect=False;self.reconnect_attempts=0
                        if self.bridge_enabled.get() and ((capture and self.capture_launch_requested) or resume):
                            self.capture_launch_requested=False;self._live(bridge_ready=True)
                    elif capture:messagebox.showerror('Capture not started',detail)
                    elif self.pending_reconnect:self.after(3000,self._retry_bridge)
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
                    self.live_active=False
                    self._close_raw_preview()
                    if self.pending_reconnect:self.after(1000,self._retry_bridge)
                    self.bridge_status.set('Run stopped • bridge inactive')
                    self.status.set(f'Finished (exit {code}) • {out}')
                    self._log(f'\nFinished with exit code {code}. Results: {out}\n')
                    self._refresh()
        except queue.Empty:pass
        self._preview_tick()
        self.after(100,self._poll)

    def _preview_tick(self):
        if not self.live_active or not self.active_out:return
        try:
            report=self.active_out/"live_status.json"
            data=json.loads(report.read_text(encoding="utf-8"))
            age=max(0,time.time()-data["timestamp"])
            self.capture_fps_label.set(f"{data['fps']:.1f}")
            self.inference_label.set(f"{data['inference_ms']:.1f} ms")
            self.observation_label.set(data['status'].upper() if age<=1 else 'STALE')
            self.live_metrics.set(f"Capture {data.get('capture_fps',0):.1f} FPS | AI {data['fps']:.1f} FPS | skipped {data.get('skipped_inference_frames',0)} | inference {data['inference_ms']:.1f} ms | "
                f"{data['status']} | preview age {age:.1f}s" + (" • STALE" if age>1 else ""))
            bridge=data.get("bridge")
            if bridge:
                self._show_slots(None,bridge.get('active_slot') if age<=.75 else None)
                verified=age<=.75 and bridge.get('hardware')=='heartbeat verified'
                if verified:self.bridge_seen_ready=True
                elif self.bridge_seen_ready and self.bridge_enabled.get() and not self.pending_reconnect:
                    self.trigger_armed.set(False);self._apply_green()
                    self.pending_reconnect=True;self.resume_after_reconnect=True;self.reconnect_attempts=0
                    self.bridge_setup_status.set('Bridge lost — trigger disabled; stopping capture before automatic reconnect.')
                    self._stop(reconnect=True)
                self.bridge_status.set("Bridge stale • hardware not verified" if age>.75 else
                    f"Gtuner: {bridge['receiver']} | Titan Two: {bridge['hardware']} | "
                    f"local ACK {bridge.get('rtt_ms') or 0:.1f} ms | device event {bridge.get('device_event')} (not console confirmation)")
            if 'green' in data:
                measurement=data['green'];trigger=data.get('trigger',{})
                self.green_status.set(f"{'ARMED' if trigger.get('armed') else 'DRY RUN'} | "
                    f"in green: {measurement.get('in_green',False)} | event {trigger.get('event_id',0)} | "
                    f"{measurement.get('reason','')}" + (' • STALE' if age>.25 else ''))
            if (self.active_out/'live_bgr.bin').exists():return
            image=self.active_out/'preview.png'
            stamp=image.stat().st_mtime_ns
            if stamp!=self.last_preview_stamp:
                photo=tk.PhotoImage(file=str(image))
                width=max(1,self.screen.winfo_width());height=max(1,self.screen.winfo_height())
                factor=max(1,(photo.width()+width-1)//width,(photo.height()+height-1)//height)
                self.preview_image=photo.subsample(factor,factor)
                self.screen.configure(image=self.preview_image,text="")
                self.last_preview_stamp=stamp
        except (OSError,ValueError,KeyError,tk.TclError):pass

    def _close_raw_preview(self):
        if self.raw_preview is not None:
            self.raw_preview.close();self.raw_preview=None
        self.raw_preview_sequence=0
        self.raw_preview_photo_size=None
        self.last_display_timestamp=None
        self.display_metrics.set('Display stopped')

    def _display_tick(self):
        started=time.perf_counter()
        try:
            if self.live_active and self.active_out and self.preview.get():
                if self.raw_preview is None and (self.active_out/'live_bgr.bin').exists():
                    self.raw_preview=RawMailbox(self.active_out/'live_bgr.bin')
                sample=self.raw_preview.read(self.raw_preview_sequence) if self.raw_preview else None
                if sample:
                    from PIL import Image,ImageTk
                    image=Image.frombytes('RGB',(sample['width'],sample['height']),sample['bgr'],'raw','BGR')
                    image.thumbnail((max(1,self.screen.winfo_width()),max(1,self.screen.winfo_height())))
                    if self.raw_preview_photo_size==image.size and hasattr(self.preview_image,'paste'):
                        self.preview_image.paste(image)
                    else:
                        self.preview_image=ImageTk.PhotoImage(image)
                        self.raw_preview_photo_size=image.size
                        self.screen.configure(image=self.preview_image,text='')
                    self.raw_preview_sequence=sample['sequence'];self.display_count+=1
                    self.last_display_timestamp=sample['timestamp']
                if self.last_display_timestamp is not None and time.perf_counter()-self.last_display_metric>=.1:
                    self.last_display_metric=time.perf_counter()
                    elapsed=max(.001,time.perf_counter()-self.display_started)
                    age=max(0,time.time()-self.last_display_timestamp)
                    self.display_metrics.set(f"Direct card BGR | rendered {self.display_count/elapsed:.1f} FPS / limit {self.display_fps.get()} | frame age {age*1000:.0f} ms"+(' • STALE' if age>.5 else ''))
        except (OSError,ValueError,tk.TclError):pass
        try:fps=int(self.display_fps.get())
        except ValueError:fps=60
        interval=1/max(1,fps)
        self.after(max(1,round(1000*(interval-(time.perf_counter()-started)))),self._display_tick)

    def _log(self,value):
        self.log.configure(state='normal');self.log.insert('end',value)
        self.log.see('end');self.log.configure(state='disabled')

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
        self._close_raw_preview()
        self.destroy()

    def _wait_close(self):
        if self.process and self.process.poll() is None:
            self.after(100,self._wait_close)
        else:self.destroy()


if __name__=='__main__':Desktop().mainloop()
