"""Single-owner live capture and a two-buffer raw BGR display mailbox."""
import json
import hashlib
import os
import sys
import tempfile
import mmap
import struct
import threading
import time
from pathlib import Path


class RawMailbox:
    MAGIC=b'2KCVRAW1'
    HEADER=struct.Struct('<QIIIdQ')
    CAPACITY=1920*1080*3

    def __init__(self,path,write=False,capacity=None):
        self.file=Path(path).open('w+b' if write else 'rb')
        self.writeable=write
        if write:self.capacity=capacity or self.CAPACITY
        else:
            header=self.file.read(24)
            if len(header)!=24 or header[:8]!=self.MAGIC:
                self.file.close();raise ValueError('Raw capture buffer is not ready')
            self.capacity=struct.unpack('<8sQQ',header)[1]
        if not 0<self.capacity<=3840*2160*3:
            self.file.close();raise ValueError('Invalid raw buffer capacity')
        size=64+2*(64+self.capacity)
        if write:self.file.truncate(size)
        self.map=mmap.mmap(self.file.fileno(),0,access=mmap.ACCESS_WRITE if write else mmap.ACCESS_READ)
        self.sequence=0
        if write:self.map[:24]=struct.pack('<8sQQ',self.MAGIC,self.capacity,0)
        elif len(self.map)!=size or self.map[:8]!=self.MAGIC:
            self.close();raise ValueError('Unsupported raw preview buffer')

    def publish(self,frame,cv2,source_sequence,timestamp,width=1280,height=720):
        h,w=frame.shape[:2]
        scale=min(width/w,height/h,1)
        if scale<1:frame=cv2.resize(frame,(max(1,round(w*scale)),max(1,round(h*scale))))
        h,w=frame.shape[:2]
        data=frame.tobytes()
        if len(data)>self.capacity or len(data)!=w*h*3:raise ValueError('Preview exceeds raw BGR buffer capacity')
        self.sequence+=1;seq=self.sequence
        offset=64+(seq%2)*(64+self.capacity)
        # Invalidate before touching pixels; publish only after the complete copy.
        self.map[offset:offset+8]=struct.pack('<Q',0)
        self.map[offset+64:offset+64+len(data)]=data
        self.map[offset:offset+self.HEADER.size]=self.HEADER.pack(seq,w,h,len(data),timestamp,source_sequence)
        self.map[16:24]=struct.pack('<Q',seq)

    def read(self,after=0):
        seq=struct.unpack_from('<Q',self.map,16)[0]
        if seq<=after:return None
        offset=64+(seq%2)*(64+self.capacity)
        first=self.map[offset:offset+self.HEADER.size]
        actual,w,h,size,stamp,source=self.HEADER.unpack(first)
        if actual!=seq or not 0<w<=3840 or not 0<h<=2160 or size!=w*h*3 or size>self.capacity:return None
        data=self.map[offset+64:offset+64+size]
        if first!=self.map[offset:offset+self.HEADER.size]:return None
        return dict(sequence=seq,width=w,height=h,timestamp=stamp,source_frame=source,bgr=data)

    def progress(self):
        """Read only publication metadata for watchdogs; never copy image pixels."""
        seq=struct.unpack_from('<Q',self.map,16)[0]
        if not seq:return None
        offset=64+(seq%2)*(64+self.capacity)
        header=self.map[offset:offset+self.HEADER.size]
        actual,w,h,size,stamp,source=self.HEADER.unpack(header)
        if actual!=seq or header!=self.map[offset:offset+self.HEADER.size]:return None
        if not 0<w<=3840 or not 0<h<=2160 or size!=w*h*3 or size>self.capacity:return None
        return dict(sequence=seq,frames=source+1,timestamp=stamp)

    def close(self):
        self.map.close();self.file.close()


class LatestCapture:
    """Keep reading the card while inference works; retain only the newest frame."""
    def __init__(self,cap,display=None):
        self.cap=cap;self.display=display
        self.condition=threading.Condition();self.stopped=threading.Event()
        self.latest=None;self.frames=0;self.failures=0;self.finished=False;self.error=None
        self.started=time.perf_counter();self.last_read=None;self.intervals=[]
        self.thread=threading.Thread(target=self._work,daemon=True)
        self.thread.start()

    def _work(self):
        try:
            while not self.stopped.is_set():
                before=time.perf_counter();ok,frame=self.cap.read();now=time.perf_counter()
                if self.stopped.is_set():break
                if not ok or frame is None:self.failures+=1;break
                if frame.ndim!=3 or frame.shape[2]!=3:raise RuntimeError('Capture did not deliver decoded 3-channel BGR')
                self.frames+=1
                if self.last_read is not None:
                    self.intervals.append((now-self.last_read)*1000)
                    if len(self.intervals)>10000:del self.intervals[:5000]
                self.last_read=now
                sample=dict(frame=frame,source_frame=self.frames-1,read_at=now,
                            captured_at=time.time(),read_ms=(now-before)*1000)
                if self.display:self.display(sample)
                with self.condition:
                    self.latest=sample;self.condition.notify_all()
        except Exception as exc:self.error=str(exc)
        finally:
            self.cap.release()
            with self.condition:self.finished=True;self.condition.notify_all()

    def next(self,after=-1,timeout=.1):
        with self.condition:
            self.condition.wait_for(lambda:(self.latest is not None and self.latest['source_frame']>after)
                                    or self.finished or self.stopped.is_set(),timeout)
            if self.latest is not None and self.latest['source_frame']>after:return self.latest
            return None

    def close(self):
        self.stopped.set()
        with self.condition:self.condition.notify_all()
        self.thread.join(timeout=2)
        return not self.thread.is_alive()


class SharedCapture:
    """Read full-resolution card frames without opening or owning the device."""
    def __init__(self,session,cv2):
        self.session=Path(session);self.cv2=cv2;self.mailbox=None;self.sequence=0
        self.first_source=None;self.sample_source_frame=-1;self.captured_at=0
        self.source_span=0;self.error=None;self.status_read_failures=0;self.status_error=None
        self.status_refresh_at=0
        try:
            self.mailbox=RawMailbox(self.session/'source_frames.bin')
            deadline=time.monotonic()+1
            while True:
                try:
                    self.info=self._status()
                    if not isinstance(self.info,dict):raise ValueError('Invalid capture status')
                    break
                except (OSError,ValueError) as exc:
                    self.status_read_failures+=1;self.status_error=str(exc)
                    if time.monotonic()>=deadline:raise
                    time.sleep(.01)
            progress=self.mailbox.progress()
            fresh=progress and 0<=time.time()-progress['timestamp']<=.75
            if self.info.get('state')!='capturing' or (not fresh and time.time()-self.info['timestamp']>2):
                raise ValueError('Start Capture and wait for a live console image first')
            self.status_error=None
        except (OSError,ValueError,KeyError,TypeError) as exc:
            self.error=str(exc)
            if self.mailbox:self.mailbox.close();self.mailbox=None

    def _status(self):return json.loads((self.session/'capture_status.json').read_text())
    def isOpened(self):return self.mailbox is not None and self.error is None
    def set(self,*args):return False
    def get(self,prop):
        mode=self.info['reported_capture']
        return {self.cv2.CAP_PROP_FRAME_WIDTH:mode['width'],self.cv2.CAP_PROP_FRAME_HEIGHT:mode['height'],
                self.cv2.CAP_PROP_FPS:mode['fps']}.get(prop,0)

    def read(self):
        end=time.monotonic()+.75
        while time.monotonic()<end:
            # JSON is optional telemetry after opening. A Windows reader lock must
            # not turn a healthy shared frame stream into a device disconnect.
            if time.monotonic()>=self.status_refresh_at:
                self.status_refresh_at=time.monotonic()+.1
                try:
                    info=self._status()
                    if not isinstance(info,dict):raise ValueError('Invalid capture status')
                    if info.get('state')=='stopped':
                        self.error=info.get('error') or 'Capture stopped';return False,None
                    if info.get('state')=='capturing':self.info=info
                    self.status_error=None
                except (OSError,ValueError) as exc:
                    self.status_read_failures+=1;self.status_error=str(exc)
            try:
                sample=self.mailbox.read(self.sequence)
                if sample and 0<=time.time()-sample['timestamp']<=.75:
                    import numpy as np
                    self.sequence=sample['sequence'];self.captured_at=sample['timestamp']
                    if self.first_source is None:self.first_source=sample['source_frame']
                    self.sample_source_frame=sample['source_frame']-self.first_source
                    self.source_span=self.sample_source_frame+1
                    return True,np.frombuffer(sample['bgr'],dtype=np.uint8).reshape(sample['height'],sample['width'],3).copy()
            except (OSError,ValueError,KeyError) as exc:self.error=str(exc);return False,None
            time.sleep(.002)
        self.error='No fresh capture frame arrived for 750 ms'
        if self.status_error:self.error+='; last status read: '+self.status_error
        return False,None

    def release(self):
        if self.mailbox:self.mailbox.close();self.mailbox=None


class CaptureLease:
    """Cross-process ownership among app captures, probes and detector CLIs."""
    def __init__(self,source,directory=None):
        root=Path(directory or tempfile.gettempdir())/'nba2k-cv-capture'
        root.mkdir(exist_ok=True)
        key=hashlib.sha256(str(source).encode()).hexdigest()[:24]
        self.file=(root/(key+'.lock')).open('a+b')
        try:
            self.file.seek(0,2)
            if self.file.tell()==0:self.file.write(b'0');self.file.flush()
            self.file.seek(0)
            if sys.platform=='win32':
                import msvcrt
                msvcrt.locking(self.file.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.file.close();self.file=None
            raise RuntimeError('This card is already owned by another NBA2K CV capture or probe. Stop it, then retry Preview Capture.') from None

    def close(self):
        if self.file is None:return
        try:
            self.file.seek(0)
            if sys.platform=='win32':
                import msvcrt
                msvcrt.locking(self.file.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(),fcntl.LOCK_UN)
        finally:self.file.close();self.file=None


class OwnedCapture:
    def __init__(self,capture,lease):self.capture=capture;self.lease=lease
    def __getattr__(self,name):return getattr(self.capture,name)
    def release(self):
        lease=self.lease
        if lease is None:return
        self.lease=None
        try:self.capture.release()
        finally:lease.close()
    def __del__(self):
        try:self.release()
        except Exception:pass
