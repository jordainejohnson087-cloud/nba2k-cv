"""Authenticated loopback bridge API with a persistent Gtuner UDP session.

The API is an app-side transport, not a replacement for Gtuner's USB host.
"""
import base64
import http.client
import http.server
import json
import math
import os
import queue
import shutil
import subprocess
import tempfile
import secrets
import socket
import threading
import time
from pathlib import Path
from urllib.parse import urlparse
from live_link import BridgeClient


def write_private_config(path,info):
    path=Path(path)
    descriptor,temporary=tempfile.mkstemp(prefix='.bridge-',dir=path.parent)
    try:
        with os.fdopen(descriptor,'w',encoding='utf-8') as file:json.dump(info,file)
        os.replace(temporary,path)
    finally:
        try:Path(temporary).unlink(missing_ok=True)
        except OSError:pass


def neutral_bridge_state():
    return dict(status='absent',fill=None,shot_id=None,trigger=dict(armed=False,green=False,
        valid=False,eligible=False,event_id=0,delay_ms=0,hold_ms=10,max_age_ms=100,direction='up',shot_mode='timing'))


def validate_bridge_state(state,stamp):
    if not isinstance(state,dict) or state.get('status') not in ('absent','predicted','detected'):
        raise ValueError('Invalid detector state')
    if type(stamp) not in (int,float) or not math.isfinite(stamp):raise ValueError('Invalid capture timestamp')
    fill=state.get('fill')
    if fill is not None and (type(fill) not in (int,float) or not math.isfinite(fill) or not 0<=fill<=1):
        raise ValueError('Invalid fill')
    trigger=state.get('trigger')
    if not isinstance(trigger,dict):raise ValueError('Trigger state required')
    for key in ('armed','green','valid','eligible'):
        if type(trigger.get(key)) is not bool:raise ValueError('Invalid trigger flag')
    for key,low,high in [('event_id',0,0xffffffff),('delay_ms',0,250),('hold_ms',10,500),('max_age_ms',25,250)]:
        if type(trigger.get(key)) is not int or not low<=trigger[key]<=high:raise ValueError('Invalid trigger timing')
    if trigger.get('direction') not in ('up','down'):raise ValueError('Invalid direction')
    if trigger.get('shot_mode','rhythm') not in ('rhythm','timing'):raise ValueError('Invalid shot mode')
    shot=state.get('shot_id')
    if shot is not None and (type(shot) is not int or not 0<=shot<=0xffffffff):raise ValueError('Invalid shot')


_KEEP_EXPECTED_SLOT=object()


class TriggerLatch:
    """One observed event, retaining its first source timestamp and deadline.

    Video state remains latest-only. A brief valid event must survive replacement
    by the next detected frame long enough for the slower GCV callback to read it.
    """
    def __init__(self):self.pending=None;self.last_id=None

    def clear(self):self.pending=None;self.last_id=None

    def offer(self,state,stamp):
        trigger=state['trigger']
        if not trigger['armed']:
            self.pending=None;return
        if self.pending is not None:
            old=self.pending[0]['trigger']
            if any(old.get(key,'rhythm')!=trigger.get(key,'rhythm') for key in ('shot_mode','direction','delay_ms','hold_ms','max_age_ms')):
                self.pending=None
        if (state['status']=='detected' and trigger['valid'] and trigger['eligible'] and trigger['green'] and trigger['event_id']
            and trigger['event_id']!=self.last_id):
            self.last_id=trigger['event_id'];self.pending=(state,stamp)

    def peek(self,now):
        if self.pending is not None:
            state,stamp=self.pending
            if not 0<=(now-stamp)*1000<=state['trigger']['max_age_ms']-25:self.pending=None
        return self.pending

    def pop(self,now):
        value=self.peek(now);self.pending=None;return value


class BridgeService:
    def __init__(self,gtuner_port=26740):
        self.client=BridgeClient(gtuner_port)
        self.client.seq=secrets.randbelow(0x7fffffff)+1
        self.first_sequence=self.client.seq+1
        self.token=secrets.token_urlsafe(32)
        self.enabled=False;self.output_allowed=False;self.expected_slot=None
        self.latest=None;self.error=None;self.events=TriggerLatch()
        self.lock=threading.Lock();self.wake=threading.Event();self.stopped=threading.Event()
        owner=self
        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version='HTTP/1.1'
            disable_nagle_algorithm=True
            def log_message(self,*args):pass
            def setup(self):
                super().setup();self.connection.settimeout(2)
            def reply(self,code,data):
                body=json.dumps(data,allow_nan=False).encode()
                self.send_response(code);self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
            def authorized(self):
                value=self.headers.get('Authorization','')
                if not secrets.compare_digest(value.encode('utf-8'),('Bearer '+owner.token).encode()):
                    self.close_connection=True;self.reply(403,{'error':'Unauthorized'});return False
                return True
            def do_GET(self):
                if not self.authorized():return
                self.reply(200,owner.status()) if self.path=='/status' else self.reply(404,{'error':'Unknown endpoint'})
            def do_POST(self):
                if not self.authorized():return
                try:
                    size=int(self.headers.get('Content-Length','0'))
                    if not 0<size<=16384:raise ValueError('Invalid body size')
                    body=json.loads(self.rfile.read(size))
                    if self.path=='/state':
                        owner.submit(body['state'],body['captured_at'])
                    elif self.path=='/configure':
                        if set(body)-{'enabled','expected_slot','allowed'}:raise ValueError('Unknown setting')
                        for key in ('enabled','allowed'):
                            if key in body and type(body[key]) is not bool:raise ValueError('Invalid boolean')
                        settings={key:body[key] for key in ('enabled','expected_slot') if key in body}
                        owner.configure(**settings)
                        if 'allowed' in body:owner.allow_output(body['allowed'])
                    elif self.path=='/verify':
                        timeout=body.get('timeout',1)
                        if type(timeout) not in (int,float) or not 0<timeout<=3:raise ValueError('Invalid verify timeout')
                        slot=body.get('expected_slot')
                        if slot is not None and (type(slot) is not int or not 1<=slot<=9):raise ValueError('Invalid slot')
                        self.reply(200,owner.verify(timeout,slot));return
                    elif self.path=='/clear':owner.clear()
                    else:self.reply(404,{'error':'Unknown endpoint'});return
                    self.reply(200,owner.status())
                except (ValueError,KeyError,TypeError,TimeoutError):
                    self.close_connection=True;self.reply(400,{'error':'Invalid bridge request'})
        self.server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.server.daemon_threads=True
        self.url=f'http://127.0.0.1:{self.server.server_port}'
        self.api_thread=threading.Thread(target=lambda:self.server.serve_forever(poll_interval=.1),daemon=True)
        self.sender=threading.Thread(target=self._send,daemon=True)
        self.api_thread.start();self.sender.start()

    def status(self):
        value=self.client.status()
        matching=self.expected_slot is None or value.get('active_slot')==self.expected_slot
        sequence=value.get('device_sequence')
        echo=type(sequence) is int and self.first_sequence<=sequence<=self.client.seq
        slot=value.get('active_slot')
        slot_valid=type(slot) is int and 1<=slot<=9
        compatible=value.get('worker_protocol')==3 and value.get('device_protocol')==3
        ready=compatible and value['hardware']=='heartbeat verified' and echo and slot_valid and matching
        if value['receiver']!='receiving':stage='Gtuner CV worker not receiving'
        elif not compatible:stage='Bridge version mismatch; restart the updated GCV worker and install Titan bridge 3.1'
        elif value['hardware']!='heartbeat verified' or not echo:stage='Titan script payload echo missing'
        elif not slot_valid:stage='Titan slot echo missing; install the current meter GPC'
        elif not matching:stage='Selected Titan slot does not match'
        else:stage='Titan bridge verified; console output unverified'
        return dict(value,api='running',enabled=self.enabled,output_allowed=self.output_allowed,ready=ready,stage=stage,last_error=self.error)

    def configure(self,enabled=None,expected_slot=_KEEP_EXPECTED_SLOT):
        if expected_slot is not _KEEP_EXPECTED_SLOT and expected_slot is not None and (type(expected_slot) is not int or not 1<=expected_slot<=9):
            raise ValueError('Expected Titan slot must be 1 through 9 or None')
        with self.lock:
            if enabled is not None:self.enabled=bool(enabled)
            if expected_slot is not _KEEP_EXPECTED_SLOT:self.expected_slot=expected_slot
            if not self.enabled:self.latest=None;self.output_allowed=False;self.events.clear()
        self.wake.set()

    def allow_output(self,allowed):
        with self.lock:
            self.output_allowed=bool(allowed)
            if not self.output_allowed:self.events.clear()
        self.wake.set()

    def clear(self):
        with self.lock:self.latest=None;self.events.clear()
        self.wake.set()

    def submit(self,state,stamp):
        validate_bridge_state(state,stamp)
        # Serialization gives the sender a private snapshot, never a caller-owned dict.
        with self.lock:
            snapshot=json.loads(json.dumps(state,allow_nan=False))
            self.latest=(snapshot,stamp);self.events.offer(snapshot,stamp)
        self.wake.set()

    def _send(self):
        while not self.stopped.is_set():
            self.wake.wait(.005);self.wake.clear()
            with self.lock:
                latest=self.latest;enabled=self.enabled;allowed=self.output_allowed
            state=neutral_bridge_state();stamp=time.time()
            if latest is not None:
                candidate,captured_at=latest
                age=(time.time()-captured_at)*1000
                if 0<=age<=candidate['trigger']['max_age_ms']:
                    state=candidate;stamp=captured_at
            status=self.status()
            with self.lock:
                if enabled and allowed and status['ready']:
                    event=self.events.peek(time.time())
                    if event is not None:state,stamp=event
                else:self.events.clear()
            if not (enabled and allowed and status['ready']):
                state=dict(state,trigger=dict(state['trigger'],armed=False,eligible=False))
            try:self.client.send(state,observed_at=stamp);self.error=None
            except OSError as exc:self.error=str(exc)
        try:self.client.send(neutral_bridge_state())
        except OSError:pass

    def verify(self,timeout=3,expected_slot=None):
        self.clear()
        first=self.client.seq+1
        self.wake.set();end=time.monotonic()+timeout
        while not self.stopped.is_set() and time.monotonic()<end:
            status=self.status();sequence=status.get('device_sequence')
            if (status['ready'] and type(sequence) is int
                    and first<=sequence<=self.client.seq
                    and (expected_slot is None or status.get('active_slot')==expected_slot)):
                return dict(status,ready=True)
            self.stopped.wait(.03)
        return dict(self.status(),ready=False)

    def write_config(self,path):
        write_private_config(path,dict(url=self.url,token=self.token))

    def close(self):
        if self.stopped.is_set():return
        self.enabled=False;self.output_allowed=False;self.clear()
        self.stopped.set();self.wake.set();self.sender.join(timeout=1)
        self.server.shutdown();self.server.server_close();self.api_thread.join(timeout=1)
        self.client.close()


class BridgeServiceClient:
    def __init__(self,config):
        info=json.loads(Path(config).read_text(encoding='utf-8'))
        address=urlparse(info['url'])
        if (address.scheme!='http' or address.hostname!='127.0.0.1' or not address.port
            or address.username or address.password or address.query or address.fragment or address.path not in ('','/')):
            raise ValueError('Bridge API must use loopback HTTP')
        self.host=address.hostname;self.port=address.port;self.token=info['token']
        if not isinstance(self.token,str) or not 20<=len(self.token)<=128:raise ValueError('Invalid bridge API token')
        self.connection=None;self.retry_at=0;self.last_status=None;self.last_status_at=0
        self._request('GET','/status')

    def _request(self,method,path,body=None):
        if time.monotonic()<self.retry_at:return self.status()
        try:
            if self.connection is None:
                self.connection=http.client.HTTPConnection(self.host,self.port,timeout=.3)
                self.connection.connect();self.connection.sock.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
            payload=json.dumps(body,allow_nan=False) if body is not None else None
            self.connection.request(method,path,payload,headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'})
            response=self.connection.getresponse();data=response.read(16385)
            if response.status!=200 or len(data)>16384:raise ValueError('Bridge API request rejected')
            self.last_status=json.loads(data);self.last_status_at=time.monotonic()
        except (OSError,ValueError,http.client.HTTPException):
            if self.connection:self.connection.close()
            self.connection=None;self.last_status=None;self.retry_at=time.monotonic()+.5
        return self.status()

    def status(self):
        if self.last_status and time.monotonic()-self.last_status_at<.75:return dict(self.last_status)
        return dict(receiver='local API unavailable',hardware='not verified',ready=False,active_slot=None,
                    device_sequence=None,device_event=None,rtt_ms=None,stage='Local bridge service unavailable')

    def send(self,state,observed_at=None):
        return self._request('POST','/state',dict(state=state,captured_at=time.time() if observed_at is None else observed_at))

    def close(self):
        self._request('POST','/clear',{})
        if self.connection:self.connection.close();self.connection=None


def run_bridge_host(config,lease,port=26740):
    """A model-free bridge process; exit if the supervising app disappears."""
    service=BridgeService(port)
    try:
        service.write_config(config)
        while True:
            try:
                if time.time()-Path(lease).stat().st_mtime>3:break
            except OSError:break
            time.sleep(.1)
    finally:service.close()


def unavailable_bridge(reason):
    return dict(receiver='unavailable',hardware='not verified',ready=False,active_slot=None,
                device_sequence=None,device_event=None,rtt_ms=None,stage=reason)


class BridgeProcess:
    """UI supervisor. All socket requests run on its control thread, never Tk."""
    @staticmethod
    def powershell_command(executable,arguments):
        # Fixed executable + individually quoted arguments; never shell=True.
        root=Path(os.environ.get('SystemRoot',r'C:\Windows'))
        installed=root/'System32'/'WindowsPowerShell'/'v1.0'/'powershell.exe'
        shell=str(installed) if installed.is_file() else shutil.which('powershell.exe') or shutil.which('pwsh')
        if not shell:raise OSError('PowerShell is unavailable')
        executable=Path(executable)
        if executable.name.lower()=='pythonw.exe':executable=executable.with_name('python.exe')
        def quote(value):return "'"+str(value).replace("'","''")+"'"
        command='& '+' '.join(quote(value) for value in [executable,*arguments])+'; exit $LASTEXITCODE'
        encoded=base64.b64encode(command.encode('utf-16-le')).decode('ascii')
        return [shell,'-NoProfile','-NonInteractive','-EncodedCommand',encoded]

    def __init__(self,executable,runtime,port=26740):
        self.folder=Path(tempfile.mkdtemp(prefix='nba2k-bridge-'))
        self.config=self.folder/'api.json';self.lease=self.folder/'owner.lease';self.lease.touch()
        self.commands=queue.Queue();self.stopped=threading.Event();self.lock=threading.Lock()
        self.cached=unavailable_bridge('Bridge process starting');self.updated=0
        self.process=None;self.thread=None;self.heartbeat=None
        try:
            arguments=[str(runtime),'--bridge-host',str(self.config),
                '--bridge-lease',str(self.lease),'--bridge-port',str(port)]
            self.transport='PowerShell → Python' if os.name=='nt' else 'Python'
            command=self.powershell_command(executable,arguments) if os.name=='nt' else [str(executable),*arguments]
            self.process=subprocess.Popen(command,
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0) if os.name=='nt' else 0)
            self.heartbeat=threading.Thread(target=self._heartbeat,daemon=True);self.heartbeat.start()
            self.thread=threading.Thread(target=self._control,daemon=True);self.thread.start()
        except Exception:
            shutil.rmtree(self.folder,ignore_errors=True);raise

    def _rpc(self,path,body=None,timeout=.4):
        info=json.loads(self.config.read_text())
        address=urlparse(info['url'])
        if address.scheme!='http' or address.hostname!='127.0.0.1' or not address.port:raise ValueError('Invalid bridge address')
        connection=http.client.HTTPConnection('127.0.0.1',address.port,timeout=timeout)
        try:
            connection.request('GET' if body is None else 'POST',path,
                None if body is None else json.dumps(body),
                headers={'Authorization':'Bearer '+info['token'],'Content-Type':'application/json'})
            response=connection.getresponse();data=response.read(16385)
            if response.status!=200 or len(data)>16384:raise ValueError('Bridge request rejected')
            return json.loads(data)
        finally:connection.close()

    def _heartbeat(self):
        while not self.stopped.is_set():
            try:self.lease.touch()
            except OSError:break
            self.stopped.wait(.2)

    def _control(self):
        pending=None
        while not self.stopped.is_set():
            try:
                if self.process.poll() is not None:raise OSError('Bridge process exited; reconnect to restart it')
                if not self.config.exists():self.stopped.wait(.02);continue
                if pending is None:
                    try:pending=self.commands.get_nowait()
                    except queue.Empty:pass
                if pending:
                    path,body,event,answer=pending
                    value=self._rpc(path,body,body.get('timeout',0)+.5 if path=='/verify' else .4)
                    if answer is not None:answer.update(value)
                    if event:event.set()
                    pending=None
                else:value=self._rpc('/status')
                with self.lock:self.cached=value;self.updated=time.monotonic()
            except (OSError,ValueError,http.client.HTTPException) as exc:
                with self.lock:self.cached=unavailable_bridge(str(exc));self.updated=time.monotonic()
                # Never replay control actions following an uncertain transport result.
                if pending:
                    if pending[3] is not None:pending[3].update(self.status())
                    if pending[2]:pending[2].set()
                    pending=None
            self.stopped.wait(.02)

    def status(self):
        with self.lock:
            if self.process.poll() is not None:return unavailable_bridge('Bridge process exited; reconnect to restart it')
            if time.monotonic()-self.updated>.5:return unavailable_bridge('Bridge process starting or status stale')
            return dict(self.cached)

    def _enqueue(self,path,body):
        if self.stopped.is_set():return
        self.commands.put((path,body,None,None))

    def configure(self,enabled=None,expected_slot=_KEEP_EXPECTED_SLOT):
        body={}
        if enabled is not None:
            if type(enabled) is not bool:raise ValueError('Invalid enabled value')
            body['enabled']=enabled
        if expected_slot is not _KEEP_EXPECTED_SLOT:
            if expected_slot is not None and (type(expected_slot) is not int or not 1<=expected_slot<=9):raise ValueError('Invalid slot')
            body['expected_slot']=expected_slot
        self._enqueue('/configure',body)

    def allow_output(self,allowed):
        if type(allowed) is not bool:raise ValueError('Invalid output setting')
        self._enqueue('/configure',dict(allowed=allowed))

    def clear(self):self._enqueue('/clear',{})

    def verify(self,timeout=3,expected_slot=None):
        if not 0<timeout<=3:raise ValueError('Invalid timeout')
        event=threading.Event();answer={}
        self.commands.put(('/verify',dict(timeout=timeout,expected_slot=expected_slot),event,answer))
        if not event.wait(timeout+1):return dict(self.status(),ready=False)
        return answer

    def write_config(self,path):
        # Only called when AI starts; never expose the controller token in a UI log.
        if not self.config.exists():raise OSError('Bridge process has not started; reconnect first')
        info=json.loads(self.config.read_text())
        write_private_config(path,info)

    def close(self):
        if self.stopped.is_set():return
        self.stopped.set()
        if self.heartbeat:self.heartbeat.join(timeout=.3)
        try:self.lease.unlink(missing_ok=True)
        except OSError:pass
        if self.thread:self.thread.join(timeout=.6)
        if self.process:
            try:self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                try:self.process.wait(timeout=1)
                except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=1)
        shutil.rmtree(self.folder,ignore_errors=True)


class AsyncBridgeServiceClient:
    """Detection never waits for a USB host or an HTTP timeout."""
    def __init__(self,config):
        self.config=config;self.lock=threading.Lock();self.latest=None;self.events=TriggerLatch()
        self.cached=unavailable_bridge('Bridge client starting');self.updated=0
        self.stop=threading.Event();self.wake=threading.Event()
        self.thread=threading.Thread(target=self._run,daemon=True);self.thread.start()

    def status(self):
        with self.lock:
            if time.monotonic()-self.updated>.75:return unavailable_bridge('Bridge API status stale')
            return dict(self.cached)

    def send(self,state,observed_at=None):
        stamp=time.time() if observed_at is None else observed_at
        validate_bridge_state(state,stamp)
        with self.lock:
            snapshot=json.loads(json.dumps(state,allow_nan=False))
            self.latest=(snapshot,stamp);self.events.offer(snapshot,stamp)
        self.wake.set();return self.status()

    def _run(self):
        client=None;signature=None
        try:
            while not self.stop.is_set():
                try:
                    info=Path(self.config).stat();current=(info.st_mtime_ns,info.st_size)
                    if client is None or current!=signature:
                        if client:client.close()
                        client=BridgeServiceClient(self.config);signature=current
                    self.wake.wait(.05);self.wake.clear()
                    with self.lock:
                        item=self.events.pop(time.time()) or self.latest
                        self.latest=None
                    value=client.send(item[0],item[1]) if item else client._request('GET','/status')
                    with self.lock:self.cached=value;self.updated=time.monotonic()
                except (OSError,ValueError) as exc:
                    with self.lock:self.cached=unavailable_bridge(str(exc));self.updated=time.monotonic()
                    self.stop.wait(.1)
        finally:
            if client:client.close()

    def close(self):
        self.stop.set();self.wake.set();self.thread.join(timeout=.7)
