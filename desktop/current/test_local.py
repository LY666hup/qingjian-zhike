"""Bounded real-loopback integration tests. All profiles are freshly generated."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
from datetime import datetime,timezone,timedelta
import base64
import hashlib
import json
import socket
import threading
import time
import unittest
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage
from connection import SessionReceiver,EventLog,classify
from profiles import validate_document,ConfigError
from pose_input import PoseReceiver,validate_pose
from local_mock import MockService,sample_person
from exercise_events import EventStore,ExerciseReceiver,ACTION_NAMES

ROOT=Path(__file__).resolve().parent
ARTIFACTS=ROOT/'validation'/datetime.now().strftime('run_%Y%m%d_%H%M%S_%f');ARTIFACTS.mkdir(parents=True,exist_ok=True)
qt=QApplication.instance() or QApplication([])

def until(predicate,seconds=4):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        qt.processEvents()
        if predicate():return
        time.sleep(.01)
    raise AssertionError('condition not reached within '+str(seconds)+' seconds')

def count_requests(server,kind):
    return sum(path.endswith(kind) for _,path,_ in server.requests)

class ConnectionTests(unittest.TestCase):
    def setUp(self):self.services=[];self.workers=[]
    def tearDown(self):
        for w in self.workers:w.stop()
        for w in self.workers:w.join(3);self.assertFalse(w.is_alive(),'worker leaked')
        for s in self.services:s.stop()
    def service(self):
        s=MockService().start();self.services.append(s);return s
    def worker(self,p,**kwargs):
        w=SessionReceiver(p,EventLog(ARTIFACTS/(self._testMethodName+'.jsonl')),
                          backoff=kwargs.pop('backoff',(.05,.1,.2,.3,.4)),
                          poll_interval=.1,timeout=.35,**kwargs)
        self.workers.append(w);w.start();return w
    def test_late_start_real_backoff(self):
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        s=MockService(port);self.services.append(s);p=s.profile(seconds=15)
        w=self.worker(p,backoff=(1,2,4,8,10))
        until(lambda:w.counts['attempts']>=3,5)
        rows=[json.loads(x) for x in w.log.path.read_text(encoding='utf-8').splitlines()]
        schedule=[r['seconds'] for r in rows if r['event']=='RETRY_SCHEDULED']
        self.assertEqual(schedule[:2],[1,2]);s.start()
        until(lambda:w.counts['decoded']>0,6);self.assertEqual(s.peak,1)
    def test_network_drop_recovers(self):
        s=self.service();w=self.worker(s.profile());until(lambda:w.counts['decoded']>=2)
        s.drop=True;until(lambda:w.counts['retries']>=1);s.drop=False
        until(lambda:w.counts['video_connections']>=2 and w.state=='receiving')
        self.assertEqual(s.peak,1);self.assertEqual(w.peak_video_connections,1)
    def test_socket_timeout_recovers(self):
        s=self.service();s.status_delay=.6;w=self.worker(s.profile());until(lambda:w.counts['retries']>=1)
        s.status_delay=0;until(lambda:w.counts['decoded']>0);self.assertEqual(s.peak,1)
    def test_unreachable_error_retries(self):
        s=self.service();w=SessionReceiver(s.profile(),EventLog(ARTIFACTS/'unreachable.jsonl'),backoff=(.05,.1),poll_interval=.1,timeout=.35)
        request=w.request;first=[True]
        def unreachable(*args):
            if first[0]:first[0]=False;raise OSError(10065,'模拟网络暂时不可达')
            return request(*args)
        w.request=unreachable;self.workers.append(w);w.start();until(lambda:w.counts['decoded']>0)
        self.assertEqual(w.counts['retries'],1)
    def test_armed_never_gets_video_then_capture(self):
        s=self.service();s.state='ARMED';w=self.worker(s.profile());until(lambda:w.state=='waiting')
        time.sleep(.25);self.assertEqual(count_requests(s,'stream.mjpg'),0)
        s.state='CAPTURING';until(lambda:w.counts['decoded']>0)
    def test_armed_no_capture_never_gets_video(self):
        s=self.service();s.state='ARMED_NO_CAPTURE';w=self.worker(s.profile());until(lambda:w.state=='waiting')
        time.sleep(.2);self.assertEqual(count_requests(s,'stream.mjpg'),0)
    def assert_stopped_no_requests(self,s,w,state):
        until(lambda:w.terminal);self.assertEqual(w.state,state)
        n=len(s.requests);time.sleep(.25);self.assertEqual(len(s.requests),n)
    def test_ended_before_video(self):
        s=self.service();s.state='ENDED';w=self.worker(s.profile());self.assert_stopped_no_requests(s,w,'ended')
        self.assertEqual(count_requests(s,'stream.mjpg'),0)
    def test_ended_during_video(self):
        s=self.service();w=self.worker(s.profile());until(lambda:w.counts['decoded']>0);s.state='ENDED'
        self.assert_stopped_no_requests(s,w,'ended');self.assertEqual(w.counts['video_connections'],1)
    def test_status_410(self):
        s=self.service();s.status_code=410;w=self.worker(s.profile());self.assert_stopped_no_requests(s,w,'ended')
    def test_video_410(self):
        s=self.service();s.video_code=410;w=self.worker(s.profile());self.assert_stopped_no_requests(s,w,'ended')
    def test_expiry_during_armed(self):
        s=self.service();s.state='ARMED';w=self.worker(s.profile(seconds=.4));self.assert_stopped_no_requests(s,w,'ended')
    def test_expiry_during_stream(self):
        s=self.service();w=self.worker(s.profile(seconds=.6));until(lambda:w.counts['decoded']>0)
        self.assert_stopped_no_requests(s,w,'ended')
    def test_every_identity_field_stops(self):
        for field in ('session_id','boot_id','camera_id','processing_mode','fpga_bit_sha256','processor'):
            s=self.service();p=s.profile();s.overrides[field]='wrong'
            w=self.worker(p);self.assert_stopped_no_requests(s,w,'mismatch');self.assertEqual(count_requests(s,'stream.mjpg'),0)
    def test_identity_change_during_stream(self):
        s=self.service();w=self.worker(s.profile());until(lambda:w.counts['decoded']>0);s.overrides['boot_id']='new-boot'
        self.assert_stopped_no_requests(s,w,'mismatch')
    def test_user_disconnect_no_restart(self):
        s=self.service();w=self.worker(s.profile());until(lambda:w.counts['decoded']>0);w.stop()
        self.assert_stopped_no_requests(s,w,'offline')
    def test_http_permission_stops(self):
        s=self.service();s.status_code=403;w=self.worker(s.profile());self.assert_stopped_no_requests(s,w,'permission')
    def test_winerror10013_stops(self):
        s=self.service();w=SessionReceiver(s.profile(),EventLog(ARTIFACTS/'permission.jsonl'))
        def fail(*args):
            e=PermissionError('WinError 10013');e.winerror=10013;raise e
        w.request=fail;self.workers.append(w);w.start();until(lambda:w.terminal)
        self.assertEqual(w.state,'permission');self.assertEqual(w.counts['retries'],0);self.assertFalse(s.requests)
    def test_retries_capped_and_only_get(self):
        s=self.service();s.status_code=503;w=self.worker(s.profile(),backoff=(.02,.04,.08,.1))
        until(lambda:w.counts['retries']>=6)
        rows=[json.loads(x) for x in w.log.path.read_text(encoding='utf-8').splitlines()]
        self.assertEqual([r['seconds'] for r in rows if r['event']=='RETRY_SCHEDULED'][:6],[.02,.04,.08,.1,.1,.1])
        self.assertTrue(all(method=='GET' for _,_,method in s.requests));self.assertEqual(count_requests(s,'stream.mjpg'),0)
    def test_invalid_profile_no_network(self):
        s=self.service();p=s.profile(seconds=-1)
        with self.assertRaises(ConfigError):SessionReceiver(p,EventLog(ARTIFACTS/'invalid.jsonl'))
        p=s.profile();p['video_url']='http://127.0.0.1:1/other'
        with self.assertRaises(ConfigError):validate_document({'schema':'qjzc.windows.inputs.v1','inputs':{'x':p}})
        self.assertFalse(s.requests)
    def test_pose_roundtrip_and_absent(self):
        s=self.service();p=s.profile(analysis=True)
        w=PoseReceiver(p,EventLog(ARTIFACTS/'pose.jsonl'),poll_interval=.1,timeout=.35)
        self.workers.append(w);w.start();until(lambda:w.counts['decoded']>0)
        packet=w.take();self.assertIsNotNone(packet);self.assertEqual(packet[0]['persons'][0]['rep_count'],3)
        self.assertFalse(packet[1].isNull());self.assertEqual(count_requests(s,'stream.mjpg'),0)
        absent=dict(s.status(),schema='qjzc.analysis.v1',kind='absent',pose_present=False,persons=[],frame_id=10)
        self.assertEqual(validate_pose(p,absent)[0]['persons'],[])
        absent['rep_count']=0
        with self.assertRaises(ConfigError):validate_pose(p,absent)

class GuiTests(unittest.TestCase):
    def test_offline_and_camera_swap_and_counts(self):
        from app import Monitor
        win=Monitor();win.show();qt.processEvents()
        self.assertFalse(win.workers);self.assertEqual(win.overall.text(),'离线')
        self.assertEqual(win.keys()[-1],('B','original'))
        win.camera_combo.setCurrentIndex(1);qt.processEvents();self.assertEqual(win.keys()[-1],('A','original'))
        win.update_cards({'persons':[sample_person('01',3,2),sample_person('02',8,None)]})
        self.assertIn('3',win.card_widgets['01'].squat.text());self.assertIn('2',win.card_widgets['01'].pushup.text())
        self.assertIn('--',win.card_widgets['02'].pushup.text());win.close()
    def test_double_connect_and_disconnect(self):
        from app import Monitor
        s=MockService().start();path=ARTIFACTS/'fresh_local_profile.json'
        path.write_text(json.dumps({'schema':'qjzc.windows.inputs.v1','inputs':{'a':s.profile()}}),encoding='utf-8')
        win=Monitor();win.load_profiles(path);self.assertFalse(s.requests)
        try:
            win.connect_sources();win.connect_sources();until(lambda:bool(win.images))
            self.assertEqual(count_requests(s,'stream.mjpg'),1)
            win.disconnect_sources();until(lambda:not any(w.is_alive() for w in win.all_workers()))
            n=len(s.requests);win.connect_sources();time.sleep(.2);self.assertEqual(len(s.requests),n)
            self.assertTrue(win.images);win.refresh();self.assertTrue(win.tiles[1].canvas.stopped)
        finally:win.close();s.stop()

class ExerciseTests(unittest.TestCase):
    def setUp(self):
        self.server=MockService().start();self.profile=self.server.profile(events=True)
        self.path=ARTIFACTS/(self._testMethodName+'_'+self.server.session+'.sqlite3');self.store=EventStore(self.path)
    def tearDown(self):self.store.close();self.server.stop()
    def event(self,seq=1,eid='event-1',action='SQUAT',person='01'):
        return dict(event_id=eid,event_seq=seq,person_id=person,action_type=action,event_type='rep_completed',delta=1)
    def batch(self,events,training='training-1'):
        return dict(self.server.status(),schema='qjzc.exercise.events.v1',training_id=training,events=events)
    def test_duplicate_does_not_increment(self):
        b=self.batch([self.event()]);first=self.store.apply(self.profile,b);again=self.store.apply(self.profile,b)
        self.assertEqual(first['persons'][0]['counts_by_action']['SQUAT'],1)
        self.assertEqual(again['persons'][0]['counts_by_action']['SQUAT'],1);self.assertEqual(again['applied_events'],0)
    def test_restart_deduplicates(self):
        b=self.batch([self.event()]);self.store.apply(self.profile,b);self.store.close();self.store=EventStore(self.path)
        self.assertEqual(self.store.apply(self.profile,b)['persons'][0]['counts_by_action']['SQUAT'],1)
    def test_multiple_sports_and_people(self):
        events=[self.event(i,'e'+str(i),action,'01' if i%2 else '02') for i,action in enumerate(ACTION_NAMES,1)]
        value=self.store.apply(self.profile,self.batch(events));self.assertEqual(len(value['persons']),2)
        self.assertEqual(sum(sum(p['counts_by_action'].values()) for p in value['persons']),len(ACTION_NAMES))
    def test_duration_from_rk_only(self):
        event=dict(event_id='duration',event_seq=1,person_id='01',action_type='PLANK',event_type='duration_updated',duration_ms=12300)
        value=self.store.apply(self.profile,self.batch([event]));self.assertEqual(value['persons'][0]['durations_ms']['PLANK'],12300)
        self.assertFalse(value['persons'][0]['counts_by_action'])
    def test_sequence_gap_rolls_back(self):
        with self.assertRaises(ConfigError):self.store.apply(self.profile,self.batch([self.event(),self.event(3,'e3')]))
        self.assertEqual(self.store.cursor(self.profile,'training-1'),0)
    def test_snapshot_catches_up_without_double_count(self):
        b=self.batch([self.event(11,'e11')]);b['snapshot']=dict(through_seq=10,persons=[dict(person_id='01',counts_by_action={'SQUAT':5})])
        self.assertEqual(self.store.apply(self.profile,b)['persons'][0]['counts_by_action']['SQUAT'],6)
        self.assertEqual(self.store.apply(self.profile,b)['persons'][0]['counts_by_action']['SQUAT'],6)
    def test_training_isolation_and_identity(self):
        self.store.apply(self.profile,self.batch([self.event()]))
        value=self.store.apply(self.profile,self.batch([self.event()],training='new-training'))
        self.assertEqual(value['persons'][0]['counts_by_action']['SQUAT'],1)
        b=self.batch([self.event()]);b['camera_id']='B'
        with self.assertRaises(ConfigError):self.store.apply(self.profile,b)
    def test_changed_event_id_rejected(self):
        self.store.apply(self.profile,self.batch([self.event()]))
        with self.assertRaises(ConfigError):self.store.apply(self.profile,self.batch([self.event(action='PUSHUP')]))
    def test_poll_replay_and_reconnect_no_duplicate(self):
        w=ExerciseReceiver(self.profile,EventLog(ARTIFACTS/'exercise_poll.jsonl'),self.store,backoff=(.05,.1),timeout=.35)
        w.start()
        try:
            until(lambda:w.counts['decoded']==1);time.sleep(.7)
            self.assertEqual(w.counts['decoded'],1);self.assertEqual(count_requests(self.server,'stream.mjpg'),0)
            w.stop();w.join(2)
            w=ExerciseReceiver(self.profile,EventLog(ARTIFACTS/'exercise_poll.jsonl'),self.store,timeout=.35);w.start()
            until(lambda:w.counts['received']>0);self.assertEqual(w.counts['decoded'],0)
        finally:w.stop();w.join(2)
    def test_dynamic_gui_count_without_pose(self):
        from app import Monitor
        win=Monitor()
        value=self.store.apply(self.profile,self.batch([self.event(action='CURL'),self.event(2,'e2','HIGHKNEE')]))
        win.update_cards(value)
        self.assertIn('CURL',win.card_widgets['01'].value_labels);self.assertIn('HIGHKNEES',win.card_widgets['01'].value_labels)
        self.assertTrue(win.pose_canvas.image.isNull());win.close()
    def test_gui_receives_events_without_inference(self):
        from app import Monitor
        p=ARTIFACTS/('event_profile_'+self.server.session+'.json')
        p.write_text(json.dumps({'schema':'qjzc.windows.inputs.v1','inputs':{'A_gray':self.profile}}),encoding='utf-8')
        win=Monitor();win.load_profiles(p)
        try:
            win.connect_sources();until(lambda:'01' in win.card_widgets)
            self.assertIn('1',win.card_widgets['01'].value_labels['SQUAT'].text())
            self.assertFalse(win.pose_workers);self.assertEqual(len(win.event_workers),1)
        finally:win.close()

class StartupTests(unittest.TestCase):
    def test_missing_dependency_is_explicit(self):
        import launch
        messages=[]
        with patch.object(launch.importlib,'import_module',side_effect=ImportError('missing PySide6')),patch.object(launch,'notify',side_effect=messages.append):
            self.assertEqual(launch.main(),2)
        self.assertIn('PySide6',messages[0]);self.assertIn('缺少必要依赖',messages[0])
    def test_windows_has_no_inference_imports(self):
        import ast
        forbidden={'mediapipe','cv2','numpy','rknn','rknnlite','onnxruntime','torch','tensorflow'}
        for path in ROOT.glob('*.py'):
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8-sig'))):
                names=[a.name for a in node.names] if isinstance(node,ast.Import) else [node.module or ''] if isinstance(node,ast.ImportFrom) else []
                self.assertFalse(any(n.split('.')[0] in forbidden for n in names),str(path))

if __name__=='__main__':
    suite=unittest.defaultTestLoader.loadTestsFromModule(__import__(__name__))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    report={'at':datetime.now(timezone.utc).isoformat(),'scope':'127.0.0.1 only; fresh UUID sessions; no RK/hardware',
            'tests_run':result.testsRun,'failures':[(str(t),e) for t,e in result.failures],
            'errors':[(str(t),e) for t,e in result.errors],'passed':result.wasSuccessful(),
            'production_backoff_seconds':[1,2,4,8,10]}
    (ARTIFACTS/'RESULT.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    (ROOT/'validation'/'RESULT.json').write_text(json.dumps(dict(report,evidence_directory=str(ARTIFACTS.relative_to(ROOT))),ensure_ascii=False,indent=2),encoding='utf-8')
    raise SystemExit(0 if result.wasSuccessful() else 1)
