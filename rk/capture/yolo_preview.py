"""Same-origin preview extension; POST only selects AI input, never capture/mode."""
from pathlib import Path
import argparse,json,time,signal,threading
from http.server import ThreadingHTTPServer
from urllib.parse import urlsplit,parse_qs
from qj5v_preview import make_handler,profiles
from control import Control

def handler(c,origin,boot_reader=None):
 boot_reader=boot_reader or (lambda:Path('/proc/sys/kernel/random/boot_id').read_text().strip())
 doc=profiles(c,origin)
 for p in doc['inputs'].values():
  p['detection_url']=p['status_url'].replace('status.json','yolo.json')
  p['detection_bind_url']=origin+'/ai/bind'
  p['capture_seconds']=c.get('capture_seconds')
 for key,p in doc['inputs'].items():
  if key=='B_original':
   p['analysis_url']=origin+'/source/B_original/panel.json?session='+c['session_id'];p['analysis_schema']='qjzc.pose.multi_action.v1';p['action_url']=origin+'/ai/action';p['initial_action']='SQUAT';p['initial_action_revision']='initial'
  else:
   p.pop('detection_url',None);p.pop('detection_bind_url',None)
 base=make_handler(c,doc);control=Control(c);action_lock=threading.Lock()
 class H(base):
  def do_POST(self):
   if self.path=='/ai/action':
    try:
     if boot_reader()!=c['boot_id']:raise ValueError('boot changed')
     from action_control import select_action
     n=int(self.headers.get('Content-Length','0'))
     if not 0<n<=8192:raise ValueError('Action request bound')
     body=json.loads(self.rfile.read(n))
     with action_lock:value=select_action(c,body)
     return self.reply(202,dict(desired=value,applied=False))
    except (ValueError,KeyError,TypeError)as e:return self.reply(409,dict(error=str(e)))
   if self.path not in ('/ai/bind','/ai/unbind'):return self.reply(404,dict(error='unknown endpoint'))
   try:
    if boot_reader()!=c['boot_id']:raise ValueError('boot changed')
    n=int(self.headers.get('Content-Length','0'))
    if not 0<n<=8192:raise ValueError('request size')
    b=json.loads(self.rfile.read(n))
    if self.path=='/ai/unbind':return self.reply(200,dict(released=control.unbind(b)))
    r=control.bind(b);self.reply(202,dict(state='WAITING_EXECUTOR_ACK',request=r))
   except (ValueError,KeyError,TypeError)as e:self.reply(409,dict(error=str(e)))
  def do_GET(self):
   u=urlsplit(self.path);parts=u.path.strip('/').split('/')
   if u.path=='/ai/action':
    from control import read
    from action_control import desired
    if parse_qs(u.query).get('session')!=[c['session_id']]:return self.reply(409,dict(error='session mismatch'))
    if control.expired():return self.reply(410,dict(error='session ended'))
    return self.reply(200,dict(desired=desired(c),ack=read(Path(c['run_dir'])/'ai/action_ack.json')))
   if u.path=='/source/B_original/panel.json':
    from control import read
    from fusion_contract import live_bundle
    if parse_qs(u.query).get('session')!=[c['session_id']]:return self.reply(409,dict(error='session mismatch'))
    if boot_reader()!=c['boot_id']or control.expired()or read(Path(c['run_dir'])/'run.json',{}).get('state')=='ENDED':return self.reply(410,dict(error='session ended'))
    bundle=read(Path(c['run_dir'])/'ai/pose_bundle.json');request=read(Path(c['run_dir'])/'ai/request.json')
    from action_control import matches_action
    if not live_bundle(c,bundle,request)or not matches_action(c,bundle['panel']):return self.reply(425,dict(error='waiting same-frame pose'))
    return self.reply(200,bundle['panel'])
   if len(parts)==3 and parts[0]=='source'and parts[2]=='yolo.json':
    if parse_qs(u.query).get('session')!=[c['session_id']]:return self.reply(409,dict(error='session mismatch'))
    if boot_reader()!=c['boot_id']:return self.reply(410,dict(error='boot changed'))
    try:code,d=control.reply(parts[1]);return self.reply(code,d)
    except (ValueError,KeyError)as e:return self.reply(409,dict(error=str(e)))
   return super().do_GET()
 return H,doc

def main():
 p=argparse.ArgumentParser();p.add_argument('--window',type=Path,required=True);p.add_argument('--port',type=int,required=True);a=p.parse_args();c=json.loads(a.window.read_bytes())
 if c.get('approval_status')not in ('DISPLAY_PREPARATION_ONLY','APPROVED_BY_USER'):raise SystemExit('explicit prepared window required')
 origin='http://'+c['rk_ipv4']+':'+str(a.port);H,doc=handler(c,origin);root=Path(c['run_dir'])
 (root/'windows_inputs.json').write_text(json.dumps(doc,indent=2))
 class Server(ThreadingHTTPServer):
  request_queue_size=32
  daemon_threads=True
 server=Server((c['rk_ipv4'],a.port),H)
 for s in (signal.SIGINT,signal.SIGTERM):signal.signal(s,lambda *_:threading.Thread(target=server.shutdown,daemon=True).start())
 try:server.serve_forever()
 finally:server.server_close()
if __name__=='__main__':main()
