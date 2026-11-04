"""One approved diagnostic capture, five bounded software JPEG encoders.

NEVER execute before protocol/continuity and user capture approval are complete.
Does not import or launch AI, an existing mode service, or any JTAG tool.
"""
from pathlib import Path
import argparse,hashlib,json,os,queue,shutil,signal,subprocess,threading,time
from mosaic_core import RawFrame,Protocol,GlobalFault,ROIS,LABELS,BIT,diagnostics,offer_latest
from v4l2_once import OnceCapture
from jpeg_retention import JPEGRetention
from jpeg_header import jpeg_size

def atomic(path,data):
 path=Path(path);temp=path.with_name('.'+path.name+'.tmp')
 with temp.open('wb')as f:f.write(data);f.flush()
 os.replace(temp,path)
def js(value):return json.dumps(value,ensure_ascii=True,separators=(',',':')).encode()

def make_branch_record(frame,config,key,jpeg,seq,observed,diag):
 camera,mode,roi=ROIS[key];rid=frame.source_frame_id+'-'+key
 parent=frame.manifest()
 record=dict(schema='qjzc.mosaic.branch.v1',session_id=config['session_id'],boot_id=config['boot_id'],
   camera_id=camera,processing_mode=mode,fpga_bit_sha256=BIT,processor='fpga',
   input_record_id=rid,branch_record_id=rid,source_frame_id=frame.source_frame_id,
   source_frame_id_kind='rk_received_mosaic_record',camera_source_frame_id=None,
   jpeg_sha256=hashlib.sha256(jpeg).hexdigest(),width=640,height=360,preview_size=[640,360],
   preview_sequence=seq,timestamp_ms=frame.timestamp_ms,time_source='rk_yuyv_receive_monotonic',
   ROI=list(roi),stride=frame.stride,parent_raw_sha256=parent['raw_sha256'],
   observed_frame_id=observed['observed_frame_id'],observed_frame_id_meaning=observed['observed_meaning'],
      composite_sequence=observed.get('composite_sequence'),qj5v_ready_mask=observed.get('ready_mask',0)&31,qj5v_fault_mask=observed.get('fault_mask'),
   ready=observed['ready'],jpeg_encoder='RK software GStreamer jpegenc',diagnostics=diag,
   bit_identity_evidence='Ly-reported BitWriter SRAM download + local file SHA; not chip readback',diagnostic_only=True)
 return record

class Run:
 def __init__(self,config,protocol):
  self.config=config;self.protocol=protocol;self.root=Path(config['run_dir']);self.stop=threading.Event();self.lock=threading.RLock()
  self.queues={key:queue.Queue(maxsize=1)for key in ROIS};self.raw_queue=queue.Queue(maxsize=2)
  self.retention=JPEGRetention(self.root/'jpeg');self.written={key:0 for key in ROIS};self.raw_written=0;self.raw_count=0;self.log_bytes=0
  self.raw_categories=dict(first=0,representative=0,anomaly=0,last=0,anchor=0)
  self.stats={key:dict(label=LABELS[key],encoded=0,encode_errors=0,queue_drops=0,ready=False,state='WAITING')for key in ROIS}
  self.writer_done=threading.Event();self.phase='ARMED';self.error=None;self.capture=None;self.events=(self.root/'diagnostics.jsonl').open('xb')
  self.raw_seen=set();self.threads=[];self.anchor=None

 def log(self,**value):
  raw=js(dict(log_monotonic_ns=time.monotonic_ns(),**value))+b'\n'
  with self.lock:
   if self.log_bytes+len(raw)>512*1024*1024:self.stop.set();raise RuntimeError('diagnostic log budget exceeded')
   self.events.write(raw);self.events.flush();self.log_bytes+=len(raw)

 def status(self):
  with self.lock:
   atomic(self.root/'run.json',js(dict(state=self.phase,error=self.error,branches=self.stats,
     session_id=self.config['session_id'],boot_id=self.config['boot_id'],fpga_bit_sha256=BIT,
     heartbeat_ns=time.monotonic_ns(),diagnostic_only=True,FPGA_timing_closed=False)))

 def save_raw(self,plane,metadata,reason):
  parent=metadata['source_frame_id']
  if parent in self.raw_seen:return
  self.raw_seen.add(parent)
  category=reason if reason in self.raw_categories else 'anomaly'
  item=(plane,dict(metadata,save_reason=reason,save_category=category))
  try:self.raw_queue.put_nowait(item)
  except queue.Full:
   if category!='anomaly':
    try:
     old_plane,dropped=self.raw_queue.get_nowait()
     if dropped['save_category']=='anchor':
      self.raw_queue.put_nowait((old_plane,dropped));self.log(event='raw_save_dropped',source_frame_id=parent,reason='preserve_common_anchor_raw');return
     self.log(event='raw_save_dropped',source_frame_id=dropped['source_frame_id'],reason='priority_retention_eviction');self.raw_queue.put_nowait(item)
    except(queue.Full,queue.Empty):self.log(event='raw_save_dropped',source_frame_id=parent,reason='bounded_raw_writer_queue_full')
   else:self.log(event='raw_save_dropped',source_frame_id=parent,reason='bounded_raw_writer_queue_full')

 def writer(self):
  try:
   while not self.writer_done.is_set() or not self.raw_queue.empty():
    try:plane,meta=self.raw_queue.get(timeout=.05)
    except queue.Empty:continue
    category=meta['save_category'];limits=dict(first=1,representative=5,anomaly=8,last=1,anchor=1)
    if self.raw_count>=16 or self.raw_written+len(plane)>128*1024*1024 or self.raw_categories[category]>=limits[category]:
     self.log(event='raw_save_dropped',source_frame_id=meta['source_frame_id'],reason='raw_retention_cap');continue
    if shutil.disk_usage(self.root).free<1024**3:self.stop.set();raise OSError('disk reserve below 1GiB')
    path=self.root/'raw'/meta['source_frame_id'];path.mkdir()
    with(path/'plane.yuyv').open('xb')as f:f.write(plane);f.flush();os.fsync(f.fileno())
    with(path/'manifest.json').open('xb')as f:f.write(js(meta));f.flush();os.fsync(f.fileno())
    self.raw_count+=1;self.raw_written+=len(plane);self.raw_categories[category]+=1
  except Exception as exc:
   self.error='raw writer failed: '+str(exc);self.stop.set()

 def encoder(self,key):
  camera,mode,roi=ROIS[key];seq=0
  from persistent_jpeg import PersistentJPEG
  try:session=PersistentJPEG()
  except Exception as exc:
   with self.lock:self.stats[key].update(state='ENCODE_ERROR',encode_errors=1)
   self.log(event='encode_error',branch=key,source_frame_id=None,error=str(exc));return
  try:
   while not self.stop.is_set():
    try:frame,packed,observed,diag=self.queues[key].get(timeout=.05)
    except queue.Empty:continue
    try:
     remaining=self.capture.end-time.monotonic()
     if remaining<1:continue
     jpeg=session.encode(packed,timeout=min(.8,remaining-.2))
     # Exact SOF size, not just JPEG delimiters.
     from jpeg_header import jpeg_size
     if jpeg_size(jpeg)!=(640,360):raise RuntimeError('JPEG dimensions differ')
     if shutil.disk_usage(self.root).free<1024**3:self.stop.set();raise OSError('disk reserve below 1GiB')
     seq+=1;rid=frame.source_frame_id+'-'+key
     record=make_branch_record(frame,self.config,key,jpeg,seq,observed,diag)
     record['common_anchor']=frame.source_frame_id==self.anchor
     folder=self.root/'jpeg'/rid;folder.mkdir()
     with(folder/'frame.jpg').open('xb')as f:f.write(jpeg)
     with(folder/'record.json').open('xb')as f:f.write(js(record))
     atomic(self.root/'latest'/ (key+'.json'),js(dict(record=record,jpeg_path=str((folder/'frame.jpg').resolve()))))
     self.retention.keep(key,folder,pin=record['common_anchor'] or seq==1)
     self.written[key]+=len(jpeg)
     with self.lock:self.stats[key].update(state='RECEIVING'if self.stats[key]['ready']else'NOT_READY',encoded=seq,input_record_id=rid,source_frame_id=frame.source_frame_id)
     self.log(event='jpeg',branch=key,input_record_id=rid,source_frame_id=frame.source_frame_id,jpeg_sha256=record['jpeg_sha256'],bytes=len(jpeg))
    except Exception as exc:
     with self.lock:self.stats[key]['encode_errors']+=1;self.stats[key]['state']='ENCODE_ERROR'
     self.log(event='encode_error',branch=key,source_frame_id=frame.source_frame_id,error=str(exc))

  finally:session.close()

 def execute(self):
  for fn,args in [(self.writer,())]+[(self.encoder,(key,))for key in ROIS]:
   t=threading.Thread(target=fn,args=args,daemon=True);t.start();self.threads.append(t)
  self.capture=OnceCapture('/dev/video22',self.stop,deadline_seconds=float(self.config['capture_seconds']))
  counter=0;previous={};last=None;last_meta=None;last_saved_ms=-100000;anomaly_types=set();previous_composite=None
  try:
   self.capture.open();self.phase='CAPTURING';self.log(event='actual_format_committed',before=self.capture.before,format=self.capture.format);self.status()
   while not self.stop.is_set():
    packet=self.capture.next()
    if packet is None:break
    plane,offset,driver_seq,ts=packet;counter+=1
    parent=self.config['session_id']+'-r'+str(counter).zfill(8)
    frame=RawFrame(parent,plane,self.capture.format['stride'],len(plane),offset,ts,driver_seq);last=frame;last_meta=None
    meta=frame.manifest()
    meta.update(V4L2_format=self.capture.format,v4l2_buffer_flags=self.capture.last_packet['flags'],v4l2_error_flag_checked=True)
    try:
     observations=self.protocol.decode(frame);meta['qj5v']=self.protocol.last_metadata;last_meta=meta
    except GlobalFault as exc:
     meta['qj5v']=self.protocol.last_metadata;self.error=str(exc)
     self.log(event='FPGA_STICKY_FAULT',frame=meta,error=str(exc));self.save_raw(plane,meta,'FPGA_STICKY_FAULT');break
    except Exception as exc:
     self.log(event='global_frame_invalid',frame=meta,error=str(exc));self.save_raw(plane,meta,'QJ5V_GLOBAL_INVALID');continue
    if self.anchor is None and meta['qj5v']['ready_mask']&0x1f==0x1f:
     self.anchor=parent;self.log(event='common_anchor_selected',source_frame_id=parent,qj5v=meta['qj5v']);self.save_raw(plane,meta,'anchor')
    branch_diag={}
    for key,(camera,mode,roi)in ROIS.items():
     observed=observations[key];packed=frame.crop(roi)
     diag=diagnostics(packed,previous.get(key),observed,ts);previous[key]=diag;branch_diag[key]=diag
     with self.lock:self.stats[key]['ready']=observed['ready'];self.stats[key]['last_diagnostics']=diag
     failures=[name for name in ('black_suspect','row_stripe_suspect','observed_stall_suspect','observed_backward_or_large_jump_suspect')if diag[name]]
     if not observed['ready']:failures.append('ready_false')
     for failure in failures:
      token=(key,failure)
      if token not in anomaly_types:anomaly_types.add(token);self.save_raw(plane,meta,key+':'+failure)
     if not observed['ready']:
      with self.lock:self.stats[key]['state']='NOT_READY'
      continue
     accepted,drop_reason=offer_latest(self.queues[key],(frame,packed,observed,diag),self.anchor)
     if drop_reason:
      with self.lock:self.stats[key]['queue_drops']+=1
      self.log(event='queue_drop',branch=key,source_frame_id=parent,reason=drop_reason,new_accepted=accepted)
    composite=meta['qj5v']['composite_sequence'];old=previous_composite
    composite_since=old[1]if old and old[0]==composite else ts
    self.log(event='raw_frame',frame=meta,branches=branch_diag,
      composite_delta_modulo=None if old is None else(composite-old[0])%2**32,
      composite_stall_suspect=old is not None and ts-composite_since>=1000)
    previous_composite=(composite,composite_since)
    if counter==1 or ts-last_saved_ms>=2000:self.save_raw(plane,meta,'first'if counter==1 else'representative');last_saved_ms=ts
    self.status()
  except Exception as exc:
   self.error=type(exc).__name__+': '+str(exc)
   bad=getattr(self.capture,'last_packet',None)
   if bad:
    raw=bad['raw'];self.save_raw(raw,dict(source_frame_id=self.config['session_id']+'-invalid-'+str(counter+1),raw_sha256=hashlib.sha256(raw).hexdigest(),bytesused_reported=bad['bytesused'],bytes_saved=len(raw),data_offset=bad['data_offset'],v4l2_sequence=bad['sequence'],flags=bad['flags'],timestamp_ms=bad['timestamp_ms'],time_source='rk_yuyv_receive_monotonic',format=self.capture.format,error=self.error),'CAPTURE_ERROR')
  finally:
   # Device release is before all disk/encoder drains.
   cleanup=self.capture.close()
   if cleanup:self.error=self.error or 'device cleanup failed: '+'; '.join(cleanup)
   if last:self.save_raw(last.plane,last_meta or last.manifest(),'last')
   self.stop.set();self.writer_done.set()
   drain_deadline=time.monotonic()+.85
   for t in self.threads:t.join(timeout=max(0,drain_deadline-time.monotonic()))
   if any(t.is_alive()for t in self.threads):self.error=self.error or 'encoder/raw writer did not finish within cleanup budget'
   self.phase='ENDED'
   missing=[key for key,item in self.stats.items()if not item['encoded']]
   anchor_files=[]if not self.anchor else[key for key in ROIS if(self.root/'jpeg'/(self.anchor+'-'+key)/'record.json').exists()]
   anchor_raw=bool(self.anchor and(self.root/'raw'/self.anchor/'manifest.json').exists())
   if not counter:self.error=self.error or 'No raw frames received'
   missing_required=[k for k in missing if k.startswith('B_')]
   if missing_required:self.error=self.error or 'No JPEG for required B branches: '+','.join(missing_required)
   if not all(k in anchor_files for k in ROIS if k.startswith('B_')) or not anchor_raw:self.error=self.error or 'No complete common-parent raw plus B original JPEG set'
   with self.lock:
    for item in self.stats.values():
     item['state_at_end']=item['state'];item['state']='ENDED';item['frame_stopped']=True
     item['transport_result']='JPEG_PRODUCED'if item['encoded']else'FAIL_NO_JPEG'
     item['image_quality_result']='DIAGNOSTIC_REVIEW_REQUIRED'
   self.log(event='ended',error=self.error,cleanup_errors=cleanup,raw_frames=counter,
     common_anchor_source_frame_id=self.anchor,
     common_anchor_JPEG_branches=anchor_files,common_anchor_raw_saved=anchor_raw,
     device_open_ns=self.capture.open_ns,device_close_ns=self.capture.close_ns,
     device_seconds=None if not self.capture.close_ns else (self.capture.close_ns-self.capture.open_ns)/1e9,
     note='No branch is board-PASS until diagnostics and evidence are reviewed')
   self.status();self.events.flush();os.fsync(self.events.fileno());self.events.close()

def main():
 p=argparse.ArgumentParser();p.add_argument('--window',type=Path,required=True);p.add_argument('--protocol',type=Path,required=True);p.add_argument('--execute-approved',action='store_true');a=p.parse_args()
 c=json.loads(a.window.read_bytes());spec=json.loads(a.protocol.read_bytes());protocol=Protocol(spec)
 if not a.execute_approved or c.get('approval_status')!='APPROVED_BY_USER':raise SystemExit('Capture approval absent; no device opened')
 if c.get('fpga_bit_sha256')!=BIT or c.get('FPGA_after_download_continuity')!='OPERATOR_CONFIRMED_REVIEWED_DOWNLOAD':raise SystemExit('bit/power continuity not confirmed')
 if c.get('boot_id')!=Path('/proc/sys/kernel/random/boot_id').read_text().strip():raise SystemExit('RK boot changed')
 if not isinstance(c.get('session_id'),str) or not c['session_id'].isalnum()or not 8<=len(c['session_id'])<=48:raise SystemExit('explicit new session ID required')
 if time.monotonic_ns()//1000000>=c.get('expires_monotonic_ms',0):raise SystemExit('window expired')
 if type(c.get('capture_seconds'))is not int or not 1<=c['capture_seconds']<=3600:raise SystemExit('explicit approved capture duration 1..3600 seconds required')
 root=Path(c['run_dir']);root.mkdir(exist_ok=True)
 if shutil.disk_usage(root).free<1536*1024*1024:raise SystemExit('require 1.5GiB free disk')
 busy=subprocess.run(['fuser','/dev/video22'],capture_output=True,timeout=2)
 if busy.returncode!=1:raise SystemExit('video22 occupied or fuser failed; coordinate, do not stop owner')
 # One-shot token is consumed even if format setup fails. No restart is permitted.
 with(root/'CAPTURE_ONCE.token').open('x')as f:f.write(c['session_id'])
 for sub in ('raw','jpeg','latest'):(root/sub).mkdir(exist_ok=True)
 run=Run(c,protocol)
 for s in (signal.SIGINT,signal.SIGTERM):signal.signal(s,lambda *_:run.stop.set())
 run.status();run.execute()
 if run.error:raise SystemExit(1)

if __name__=='__main__':main()
