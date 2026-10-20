"""Single-owner live capture and a two-buffer raw BGR display mailbox."""
import mmap
import struct
import threading
import time
from pathlib import Path


class RawMailbox:
    MAGIC=b'2KCVRAW1'
    HEADER=struct.Struct('<QIIIdQ')
    CAPACITY=1920*1080*3

    def __init__(self,path,write=False):
        self.file=Path(path).open('w+b' if write else 'rb')
        self.writeable=write
        size=64+2*(64+self.CAPACITY)
        if write:self.file.truncate(size)
        self.map=mmap.mmap(self.file.fileno(),0,access=mmap.ACCESS_WRITE if write else mmap.ACCESS_READ)
        self.sequence=0
        if write:self.map[:24]=struct.pack('<8sQQ',self.MAGIC,self.CAPACITY,0)
        elif len(self.map)!=size or self.map[:8]!=self.MAGIC:
            self.close();raise ValueError('Unsupported raw preview buffer')

    def publish(self,frame,cv2,source_sequence,timestamp,width=1280,height=720):
        h,w=frame.shape[:2]
        scale=min(width/w,height/h,1)
        if scale<1:frame=cv2.resize(frame,(max(1,round(w*scale)),max(1,round(h*scale))))
        h,w=frame.shape[:2]
        data=frame.tobytes()
        if len(data)>self.CAPACITY or len(data)!=w*h*3:raise ValueError('Preview exceeds raw BGR buffer capacity')
        self.sequence+=1;seq=self.sequence
        offset=64+(seq%2)*(64+self.CAPACITY)
        # Invalidate before touching pixels; publish only after the complete copy.
        self.map[offset:offset+8]=struct.pack('<Q',0)
        self.map[offset+64:offset+64+len(data)]=data
        self.map[offset:offset+self.HEADER.size]=self.HEADER.pack(seq,w,h,len(data),timestamp,source_sequence)
        self.map[16:24]=struct.pack('<Q',seq)

    def read(self,after=0):
        seq=struct.unpack_from('<Q',self.map,16)[0]
        if seq<=after:return None
        offset=64+(seq%2)*(64+self.CAPACITY)
        first=self.map[offset:offset+self.HEADER.size]
        actual,w,h,size,stamp,source=self.HEADER.unpack(first)
        if actual!=seq or not 0<w<=1920 or not 0<h<=1080 or size!=w*h*3 or size>self.CAPACITY:return None
        data=self.map[offset+64:offset+64+size]
        if first!=self.map[offset:offset+self.HEADER.size]:return None
        return dict(sequence=seq,width=w,height=h,timestamp=stamp,source_frame=source,bgr=data)

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
