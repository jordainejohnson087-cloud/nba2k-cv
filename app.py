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
from app_model import import_model, managed_model


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
        self.geometry('1000x740')
        self.minsize(820,600)
        self.process=None
        self.importing_model=False
        self.messages=queue.Queue()
        saved=load_settings()
        saved_model=Path(saved.get('model','')) if saved.get('model') else None
        found=saved_model if saved_model and saved_model.is_file() else discover_model()
        self.model=tk.StringVar(value=str(found) if found else '')
        self.source=tk.StringVar(value=saved.get('source','0'))
        self.width=tk.StringVar(value=saved.get('width','1920'))
        self.height=tk.StringVar(value=saved.get('height','1080'))
        self.fps=tk.StringVar(value=saved.get('fps','60'))
        self.imgsz=tk.StringVar(value=saved.get('imgsz','1280'))
        self.seconds=tk.StringVar(value=saved.get('seconds','30'))
        self.record=tk.BooleanVar(value=True)
        self.raw=tk.BooleanVar(value=False)
        self.preview=tk.BooleanVar(value=True)
        self.status=tk.StringVar(value='Ready')
        self.update_status=tk.StringVar(value='Update check pending')
        self.latest_release=None
        self._build()
        self.after(100,self._poll)
        self.after(500,lambda:self._check_online(silent=True))
        self.protocol('WM_DELETE_WINDOW',self._close)

    def _row(self,parent,title,var,browse=None):
        row=ttk.Frame(parent);row.pack(fill='x',pady=5)
        ttk.Label(row,text=title,width=20).pack(side='left')
        ttk.Entry(row,textvariable=var).pack(side='left',fill='x',expand=True)
        if browse: ttk.Button(row,text='Browse',command=browse).pack(side='left',padx=6)

    def _build(self):
        self.tabs=ttk.Notebook(self);self.tabs.pack(fill='both',expand=True,padx=12,pady=10)
        live=ttk.Frame(self.tabs,padding=14);self.tabs.add(live,text='Live Detection')
        model_row=ttk.Frame(live);model_row.pack(fill='x',pady=5)
        ttk.Label(model_row,text='Model best.pt',width=20).pack(side='left')
        ttk.Entry(model_row,textvariable=self.model).pack(side='left',fill='x',expand=True)
        ttk.Button(model_row,text='Auto Find',command=self._auto_find).pack(side='left',padx=4)
        ttk.Button(model_row,text='Browse',command=self._choose_model).pack(side='left',padx=4)
        ttk.Button(model_row,text='Import to App',command=self._import_model).pack(side='left',padx=4)
        self._row(live,'Capture source',self.source)
        mode=ttk.Frame(live);mode.pack(fill='x',pady=5)
        for title,var in [('Width',self.width),('Height',self.height),('Capture FPS',self.fps),
                          ('Model input',self.imgsz),('Seconds',self.seconds)]:
            ttk.Label(mode,text=title).pack(side='left',padx=(8,3))
            ttk.Entry(mode,textvariable=var,width=8).pack(side='left')
        flags=ttk.Frame(live);flags.pack(fill='x',pady=8)
        ttk.Checkbutton(flags,text='Show preview',variable=self.preview).pack(side='left',padx=8)
        ttk.Checkbutton(flags,text='Save annotated replay',variable=self.record).pack(side='left',padx=8)
        ttk.Checkbutton(flags,text='Save raw training footage',variable=self.raw).pack(side='left',padx=8)
        buttons=ttk.Frame(live);buttons.pack(fill='x',pady=6)
        ttk.Button(buttons,text='Start Live Test',command=self._live).pack(side='left',padx=4)
        ttk.Button(buttons,text='Test Capture Only',command=self._probe).pack(side='left',padx=4)
        ttk.Button(buttons,text='Check Setup',command=self._setup).pack(side='left',padx=4)
        ttk.Button(buttons,text='Stop',command=self._stop).pack(side='left',padx=4)
        ttk.Label(live,text='Raw recording may slow live detection. Each run creates its own results folder.',
                  wraplength=800).pack(anchor='w',pady=6)
        self.log=scrolledtext.ScrolledText(live,height=17,state='disabled')
        self.log.pack(fill='both',expand=True,pady=6)

        results=ttk.Frame(self.tabs,padding=14);self.tabs.add(results,text='Results')
        ttk.Button(results,text='Refresh Runs',command=self._refresh).pack(anchor='w')
        self.runs=ttk.Treeview(results,columns=('device','fps','frames'),show='tree headings',height=13)
        self.runs.heading('#0',text='Run');self.runs.column('#0',width=400)
        for column in ('device','fps','frames'):
            self.runs.heading(column,text=column.title());self.runs.column(column,width=100)
        self.runs.pack(fill='both',expand=True,pady=8)
        self.runs.bind('<<TreeviewSelect>>',self._show_result)
        ttk.Button(results,text='Open Selected Folder',command=self._open_result).pack(anchor='w')
        self.detail=scrolledtext.ScrolledText(results,height=11,state='disabled')
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

    def _choose_model(self):
        initial=Path(self.model.get()).parent if Path(self.model.get()).is_file() else Path.home()/'Downloads'
        self.lift()
        path=filedialog.askopenfilename(parent=self,title='Select best.pt',initialdir=str(initial),
            filetypes=[('PyTorch weights','*.pt'),('All files','*.*')])
        if path:self.model.set(path);self._save()

    def _auto_find(self):
        found=discover_model(deep=True)
        if found:
            self.model.set(str(found));self._save()
            self.status.set(f'Model found: {found}')
        else:
            messagebox.showinfo('Model not found','No best.pt was found in prior run logs or Downloads. Paste its full path in the model field.')

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
            if min(width,height,fps,imgsz,seconds)<=0 or fps>240: raise ValueError()
            return width,height,fps,imgsz,seconds
        except ValueError:
            messagebox.showerror('Invalid settings','Width, height, FPS, model input, and seconds must be positive numbers.')
            return None

    def _save(self):
        SETTINGS.write_text(json.dumps(dict(model=self.model.get(),source=self.source.get(),
            width=self.width.get(),height=self.height.get(),fps=self.fps.get(),
            imgsz=self.imgsz.get(),seconds=self.seconds.get()),indent=2),encoding='utf-8')

    def _out(self,prefix):
        base=PROJECT/'test_results'/f'{prefix}_{time.strftime("%Y%m%d_%H%M%S")}'
        out=base
        suffix=2
        while out.exists():
            out=Path(f'{base}_{suffix}');suffix+=1
        out.mkdir(parents=True)
        return out

    def _busy(self):
        if self.importing_model:
            messagebox.showinfo('Model import active','Wait for the model copy to finish first.')
            return True
        if self.process and self.process.poll() is None:
            messagebox.showinfo('Already running','Stop or finish the current run first.')
            return True
        return False

    def _common(self,values):
        width,height,fps,imgsz,seconds=values
        return (['--source',self.source.get(),'--width',str(width),'--height',str(height),
                 '--capture-fps',str(fps),'--fourcc','MJPG','--seconds',str(seconds)] +
                (['--dshow'] if sys.platform=='win32' else []))

    def _live(self):
        if self._busy():return
        values=self._settings()
        if not values:return
        model=Path(self.model.get())
        if not model.is_file():
            messagebox.showerror('Model missing','Select your trained best.pt file first.');return
        self._save()
        out=self._out('live')
        cmd=[sys.executable,str(PROJECT/'run_live.py'),'--model',str(model),
             '--out',str(out),'--imgsz',str(values[3])] + self._common(values)
        if not self.preview.get():cmd.append('--headless')
        if self.record.get():cmd.append('--record')
        if self.raw.get():cmd.append('--record-raw')
        self._start(cmd,out)

    def _probe(self):
        if self._busy():return
        values=self._settings()
        if not values:return
        self._save()
        out=self._out('capture')
        cmd=[sys.executable,str(PROJECT/'run_live.py'),'--probe-only','--out',str(out)] + self._common(values)
        self._start(cmd,out)

    def _setup(self):
        if self._busy():return
        self._save()
        out=self._out('setup')
        cmd=[sys.executable,str(PROJECT/'testing'/'self_check.py'),
             '--model',self.model.get(),'--out',str(out/'setup_check.json')]
        self._start(cmd,out)

    def _train(self):
        if self._busy():return
        paths=[Path(v.get()) for v in (self.train_run,self.val_run,self.test_run)]
        if not self.model.get() or not Path(self.model.get()).is_file() or any(not p.is_dir() for p in paths):
            messagebox.showerror('Missing inputs','Choose the model and three separate labeled run folders.');return
        out=self._out('learning')
        cmd=[sys.executable,str(PROJECT/'testing'/'learning_loop.py'),'train',
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
            self.status.set('Could not start');messagebox.showerror('Start failed',str(exc));return
        process=self.process
        def read_output():
            for line in process.stdout:self.messages.put(('text',line))
            self.messages.put(('done',(process.wait(),str(out))))
        threading.Thread(target=read_output,daemon=True).start()

    def _stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate();self.status.set('Stopping...')

    def _poll(self):
        try:
            while True:
                kind,value=self.messages.get_nowait()
                if kind=='text':self._log(value)
                elif kind=='release':
                    self.latest_release=value
                    if value:
                        self.update_status.set(f"Version {value['version']} is available. Click Install Update.")
                        self.install_button.configure(state='normal')
                    else:self.update_status.set('You have the latest version.')
                elif kind=='update_error':
                    self.update_status.set(f'Update check failed: {value}')
                elif kind=='model_imported':
                    self.importing_model=False
                    self.model.set(str(value));self._save()
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
                    self.status.set(f'Finished (exit {code}) • {out}')
                    self._log(f'\nFinished with exit code {code}. Results: {out}\n')
                    self._refresh()
        except queue.Empty:pass
        self.after(100,self._poll)

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

    def _check_online(self,silent=False):
        feed=configured_feed(PROJECT)
        if not feed:
            self.update_status.set('Online update feed has not been published yet; downloaded ZIP updates remain available.')
            return
        self.update_status.set('Checking for updates...')
        def work():
            try:self.messages.put(('release',fetch_manifest(feed,PROJECT)))
            except Exception as exc:self.messages.put(('update_error',str(exc)))
        threading.Thread(target=work,daemon=True).start()

    def _install_online(self):
        if self._busy() or not self.latest_release:return
        self.install_button.configure(state='disabled')
        self.update_status.set('Downloading and verifying update...')
        def work():
            try:self.messages.put(('downloaded',download_release(self.latest_release,PROJECT)))
            except Exception as exc:self.messages.put(('update_error',str(exc)))
        threading.Thread(target=work,daemon=True).start()

    def _finish_update(self,archive):
        try:result=apply_update(archive,PROJECT)
        except Exception as exc:
            self.update_status.set(f'Update failed: {exc}')
            messagebox.showerror('Update failed',str(exc));return
        messagebox.showinfo('Updated',f"Updated to version {result['version']}. The app will restart.")
        subprocess.Popen([sys.executable,str(PROJECT/'app.py')],cwd=PROJECT)
        self.destroy()

    def _update_local(self):
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
        if self.importing_model:
            messagebox.showinfo('Model import active','Wait for the model copy to finish first.');return
        if self.process and self.process.poll() is None:
            if not messagebox.askyesno('Run active','Stop the current test and close?'):return
            self.process.terminate()
        self.destroy()


if __name__=='__main__':Desktop().mainloop()
