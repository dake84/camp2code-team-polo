"""Persistente, begrenzte Frame-Aufzeichnung unabhängig vom Browser."""
import csv
import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
import cv2
from replay_hud import encode_lanes

FIELDS = ('frame_id','camera_timestamp_ns','camera_monotonic_ns','driving_timestamp_ns','image_filename','steering_angle','steering_angle_car','speed','lane_count','lanes_json','event_codes','data_status')

class SessionRecorder:
    """Schreibt nur neue Frames aus dem vorhandenen Ringbuffer auf die SD-Karte."""
    def __init__(self, car, root='Data_AI/sessions', interval=0.12, event_source=None):
        self.car=car
        self.event_source=event_source
        self._event_keys=set()
        self.root=Path(root)
        self.interval=interval
        self._lock=threading.RLock()
        self._stop=threading.Event()
        self._thread=None
        self._last_id=-1
        self.count=0
        self.path=None
        self.error=None
        self._log=logging.getLogger(__name__)

    def is_running(self):
        return self._thread is not None and self._thread.is_alive() and not self._stop.is_set()

    def start(self):
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                if not self._stop.is_set(): return
                raise RuntimeError('Recorder beendet noch den vorherigen Thread')
            self.root.mkdir(parents=True,exist_ok=True)
            self.path=self.root/('session_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
            (self.path/'frames').mkdir(parents=True)
            with (self.path/'events.csv').open('w',newline='',encoding='utf-8') as event_file:
                csv.DictWriter(event_file,fieldnames=('timestamp_ns','frame_id','code','detail')).writeheader()
            self._event_keys=set()
            with (self.path/'frames.csv').open('w',newline='',encoding='utf-8') as f:
                csv.DictWriter(f,fieldnames=FIELDS).writeheader()
            (self.path/'manifest.json').write_text(json.dumps({'schema_version':1,'created_at':datetime.now().isoformat(),'status':'recording'},indent=2),encoding='utf-8')
            self._last_id=-1;self.count=0;self.error=None;self._stop.clear()
            self._thread=threading.Thread(target=self._loop,name='session-recorder',daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()
        thread=self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=5)
            if thread.is_alive():
                raise RuntimeError('Recorder-Thread konnte nicht beendet werden')
        if self.path:
            manifest=self.path/'manifest.json'
            manifest.write_text(json.dumps({'schema_version':1,'status':'error' if self.error else 'stopped','frames':self.count,'error':self.error},indent=2),encoding='utf-8')

    def _loop(self):
        while not self._stop.is_set():
            try: self.capture_once()
            except Exception as exc:
                self.error=str(exc)
                self._log.exception('Session-Aufzeichnung fehlgeschlagen')
                self._stop.set()
            self._stop.wait(self.interval)

    def _new_events(self):
        if self.event_source is None:return []
        events=[]
        for event in self.event_source():
            key=(event.timestamp_ns,event.frame_id,event.code,event.detail)
            if key not in self._event_keys:
                events.append((key,event))
        return events

    def capture_once(self):
        pending=self._new_events()
        if pending:
            with (self.path/'events.csv').open('a',newline='',encoding='utf-8') as f:
                writer=csv.DictWriter(f,fieldnames=('timestamp_ns','frame_id','code','detail'))
                for key,event in pending:
                    writer.writerow(dict(timestamp_ns=event.timestamp_ns,frame_id=event.frame_id,
                                         code=event.code,detail=event.detail))
                    self._event_keys.add(key)
        records=self.car.frame_history.snapshot()
        for record in records:
            cam=record.camera
            if cam.frame_id<=self._last_id:continue
            image=cam.raw_image
            if image is None or not getattr(image,'size',0):
                self._last_id=cam.frame_id;continue
            drive=record.driving if record.driving is not None and record.driving.frame_id==cam.frame_id else None
            filename=f'frames/frame_{cam.frame_id:010d}.jpg'
            target=self.path/filename
            if not cv2.imwrite(str(target),image):
                raise OSError(f'JPEG konnte nicht geschrieben werden: {target}')
            row=dict(frame_id=cam.frame_id,camera_timestamp_ns=cam.timestamp,
                camera_monotonic_ns=cam.monotonic_ns,driving_timestamp_ns=drive.timestamp_ns if drive else '',
                image_filename=filename,steering_angle=drive.steering_angle if drive else '',
                steering_angle_car=drive.steering_angle_car if drive else '',
                speed=drive.speed if drive else '',lane_count=len(cam.lanes) if cam.lanes is not None else 0,
                lanes_json=encode_lanes(cam.lanes),
                event_codes='|'.join(event.code for _,event in pending if event.frame_id==cam.frame_id),
                data_status='complete' if drive else 'no_driving_state')
            try:
                with (self.path/'frames.csv').open('a',newline='',encoding='utf-8') as f:
                    csv.DictWriter(f,fieldnames=FIELDS).writerow(row)
            except Exception:
                target.unlink(missing_ok=True)
                raise
            self._last_id=cam.frame_id
            self.count+=1


def list_sessions(root='Data_AI/sessions'):
    base=Path(root)
    if not base.is_dir():return []
    return sorted((p.name for p in base.iterdir() if p.is_dir() and (p/'frames.csv').is_file()),reverse=True)


def load_session(name,root='Data_AI/sessions'):
    base=Path(root).resolve()
    path=(base/name).resolve()
    if path.parent!=base or not (path/'frames.csv').is_file():
        raise ValueError('Ungültige Session')
    with (path/'frames.csv').open(newline='',encoding='utf-8') as f:
        rows=list(csv.DictReader(f))
    return path,rows
