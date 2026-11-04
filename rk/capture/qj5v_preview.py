"""Five-source read-only JPEG HTTP publisher. NEVER opens a video device.

Separate from the single capture process; no POST control or AI endpoints.
"""
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlsplit,parse_qs,urlencode
import argparse,base64,hashlib,json,threading,time,signal
from mosaic_core import ROIS,LABELS,BIT

def profiles(config,origin):
 inputs={}
 for key,(camera,mode,roi)in ROIS.items():
  base=origin+'/source/'+key
  inputs[key]=dict(session_id=config['session_id'],boot_id=config['boot_id'],camera_id=camera,
   processing_mode=mode,fpga_bit_sha256=BIT,processor='fpga',authorized_until=config['authorized_until'],
   status_url=base+'/status.json?'+urlencode({'session':config['session_id']}),
   video_url=base+'/stream.mjpg?'+urlencode({'session':config['session_id']}),video_size=[640,360],
   mosaic_region=list(roi),analysis_url=None,label=LABELS[key])
 return dict(schema='qjzc.windows.inputs.v1',inputs=inputs,diagnostic_only=True)

def make_handler(config,profile_document=None):
 root=Path(config['run_dir']);active=set();lock=threading.Lock()
 def state(key):
  camera,mode,roi=ROIS[key]
  result=dict(schema='qjzc.preview.v2',camera_id=camera,processing_mode=mode,session_id=config['session_id'],boot_id=config['boot_id'],
   fpga_bit_sha256=BIT,processor='fpga',state='ARMED',diagnostic_only=True,FPGA_timing_closed=False)
  if Path('/proc/sys/kernel/random/boot_id').exists()and Path('/proc/sys/kernel/random/boot_id').read_text().strip()!=config['boot_id']:
   result.update(state='ENDED',error='RK boot changed');return result
  if time.monotonic_ns()//1000000>=config['expires_monotonic_ms']:
   result.update(state='ENDED',error='window expired');return result
  p=root/'run.json'
  if p.exists():
   run=json.loads(p.read_bytes());branch=run.get('branches',{}).get(key,{})
   result.update(state=run['state'],branch=branch,error=run.get('error'))
   if run['state']=='CAPTURING'and time.monotonic_ns()-run.get('heartbeat_ns',0)>2_000_000_000:
    result.update(state='ENDED',error='capture heartbeat stopped; check device cleanup')
   elif run['state']=='CAPTURING'and not branch.get('ready'):
    result.update(state='ARMED',error='QJ8V branch ready is false or has not been observed')
  return result
 def latest(key):
  p=root/'latest'/(key+'.json')
  if not p.exists():return None
  item=json.loads(p.read_bytes());record=item['record'];path=Path(item['jpeg_path']).resolve()
  if not path.is_relative_to(root.resolve()):raise ValueError('JPEG outside window')
  if time.monotonic_ns()//1000000-record['timestamp_ms']>1500:return None
  data=path.read_bytes()
  if hashlib.sha256(data).hexdigest()!=record['jpeg_sha256']:raise ValueError('JPEG SHA changed')
  return record,data
 def headers(record):
  return {'Content-Type':'image/jpeg','X-Input-Record-Id':record['input_record_id'],
   'X-Source-Frame-Id':record['source_frame_id'],'X-Observed-Frame-Id':str(record['observed_frame_id']),
   'X-JPEG-SHA256':record['jpeg_sha256'],'X-Preview-Sequence':str(record['preview_sequence']),
   'X-Frame-Record':base64.urlsafe_b64encode(json.dumps(record,separators=(',',':')).encode()).decode()}
 class Handler(BaseHTTPRequestHandler):
  def reply(self,code,data,ctype='application/json',extra=None):
   data=data if isinstance(data,bytes)else json.dumps(data).encode()
   self.send_response(code);self.send_header('Content-Type',ctype);self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store')
   for k,v in(extra or {}).items():self.send_header(k,v)
   self.end_headers();self.wfile.write(data)
  def do_GET(self):
   u=urlsplit(self.path);parts=u.path.strip('/').split('/');q=parse_qs(u.query)
   if u.path=='/windows_inputs.json'and profile_document is not None:
    if time.monotonic_ns()//1000000>=config['expires_monotonic_ms']:return self.reply(410,dict(error='window expired'))
    return self.reply(200,profile_document)
   if len(parts)!=3 or parts[0]!='source'or parts[1]not in ROIS:return self.reply(404,dict(error='unknown source'))
   key=parts[1]
   if q.get('session',[''])[0]!=config['session_id']:return self.reply(409,dict(error='session mismatch'))
   try:
    s=state(key)
    if parts[2]=='status.json':return self.reply(200,s)
    if s['state']=='ENDED':return self.reply(410,s)
    if s['state']!='CAPTURING':return self.reply(425,s)
    if parts[2]=='latest.jpg':
     item=latest(key)
     if not item:return self.reply(425,dict(error='branch JPEG unavailable/stale'))
     record,data=item;return self.reply(200,data,'image/jpeg',{k:v for k,v in headers(record).items()if k!='Content-Type'})
    if parts[2]!='stream.mjpg':return self.reply(404,dict(error='unknown endpoint'))
    with lock:
     if key in active:return self.reply(429,dict(error='one video connection per branch'))
     active.add(key)
    try:
     self.send_response(200);self.send_header('Content-Type','multipart/x-mixed-replace; boundary=qj5v');self.send_header('Cache-Control','no-store');self.end_headers();last=None
     while state(key)['state']=='CAPTURING':
      item=latest(key)
      if not item:break
      record,data=item
      if record['input_record_id']==last:time.sleep(.03);continue
      h=dict(headers(record),**{'Content-Length':str(len(data))})
      self.wfile.write(b'--qj5v\r\n'+b''.join((k+': '+v+'\r\n').encode('ascii')for k,v in h.items())+b'\r\n'+data+b'\r\n');self.wfile.flush();last=record['input_record_id']
    finally:
     with lock:active.discard(key)
   except(BrokenPipeError,ConnectionResetError,TimeoutError):pass
   except Exception as exc:
    # After a stream has started, EOF forces existing Windows reconnect/status validation.
    if parts[2]!='stream.mjpg':self.reply(503,dict(error=str(exc)))
  def log_message(self,*args):pass
 return Handler

def main():
 p=argparse.ArgumentParser();p.add_argument('--window',type=Path,required=True);p.add_argument('--host');p.add_argument('--port',type=int,default=8090);a=p.parse_args()
 config=json.loads(a.window.read_bytes())
 if config.get('approval_status') not in ('DISPLAY_PREPARATION_ONLY','APPROVED_BY_USER'):raise SystemExit('approved window config required')
 root=Path(config['run_dir']);root.mkdir(exist_ok=True)
 host=a.host or config['rk_ipv4']
 profile=profiles(config,'http://'+host+':'+str(a.port));(root/'windows_inputs.json').write_text(json.dumps(profile,ensure_ascii=False,indent=2),encoding='utf-8')
 server=ThreadingHTTPServer((host,a.port),make_handler(config,profile))
 for sig in (signal.SIGINT,signal.SIGTERM):signal.signal(sig,lambda *_:threading.Thread(target=server.shutdown,daemon=True).start())
 try:server.serve_forever()
 except KeyboardInterrupt:pass
 finally:server.server_close()

if __name__=='__main__':main()
