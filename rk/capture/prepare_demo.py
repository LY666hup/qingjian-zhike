"""Operator-only, one-shot raw07 display diagnostic. No AI, installs or JTAG."""
from pathlib import Path
from datetime import datetime,timezone,timedelta
import argparse,json,os,signal,subprocess,sys,time,uuid,socket,shutil,urllib.request
from mosaic_core import BIT,Protocol
from jpeg_header import jpeg_size
import qj5v_capture
from window_time import explicit_window_time
ROOT=Path(__file__).resolve().parent
WINDOW=ROOT/'window.approved.json'
def atomic(p,v):
 t=p.with_suffix(p.suffix+'.tmp');t.write_text(json.dumps(v,indent=2),encoding='utf-8');os.replace(t,p)
def boot():return Path('/proc/sys/kernel/random/boot_id').read_text().strip()
def idle():
 p=subprocess.run(['fuser','-v','/dev/video22'],capture_output=True,text=True,timeout=3)
 if p.returncode!=1:raise SystemExit('video22 busy or fuser error; do not stop unknown owner: '+p.stdout+p.stderr)
def prepare(host_utc,host_until,reviewed_boot_id,port=8098):
 duration,authorized_until=explicit_window_time(host_utc,host_until)
 if WINDOW.exists():raise SystemExit('Window already exists. No automatic replacement; inspect existing window first.')
 if boot()!=reviewed_boot_id:raise SystemExit('Reviewed boot differs; stop')
 print('BOOT='+boot());print('REQUIRED_BIT='+BIT)
 print('Confirm THIS reviewed download, continuous power/no reset/reconfiguration, and current camera wiring.')
 if input('Type PREPARE_DEMO for display preparation ONLY: ').strip()!='PREPARE_DEMO':raise SystemExit('Cancelled')
 idle()
 if shutil.disk_usage(ROOT).free<1536*1024**2:raise SystemExit('Need 1.5GiB free')
 net=json.loads(subprocess.check_output(['ip','-j','-4','addr','show','dev','end0']))
 if not any(a.get('local')=='169.254.67.238' for n in net for a in n['addr_info']):raise SystemExit('RK address changed')
 for name in ('videoconvert','jpegenc','rawvideoparse','fdsrc','fdsink'):
  p=subprocess.run(['gst-inspect-1.0',name],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=5)
  if p.returncode:raise SystemExit('Missing GStreamer plugin: '+name)
 with socket.socket() as s:s.bind(('169.254.67.238',port))
 spec=json.loads((ROOT/'protocol.template.json').read_bytes());Protocol(spec)
 run=ROOT/'runs'/('session_'+uuid.uuid4().hex);run.mkdir(parents=True,exist_ok=False)
 c=dict(session_id=uuid.uuid4().hex,boot_id=boot(),fpga_bit_sha256=BIT,rk_ipv4='169.254.67.238',run_dir=str(run),capture_seconds=11,approval_status='DISPLAY_PREPARATION_ONLY',expires_monotonic_ms=time.monotonic_ns()//1000000+int(duration*1000),authorized_until=authorized_until,FPGA_after_download_continuity='OPERATOR_CONFIRMED_REVIEWED_DOWNLOAD',diagnostic_only=True,FPGA_timing_closed=False,physical_camera_mapping='operator_current_wiring_not_automatically_inferred',identity_level='file_SHA_download_logs_operator_continuity_not_chip_readback')
 atomic(WINDOW,c);atomic(run/'capture_context.json',c)
 with (run/'preview.log').open('xb')as log:
  p=subprocess.Popen([sys.executable,'-B',str(ROOT/'yolo_preview.py'),'--window',str(WINDOW),'--port',str(port)],stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
 (run/'preview.pid').write_text(str(p.pid));time.sleep(1)
 if p.poll()is not None:raise SystemExit('Preview failed; inspect '+str(run/'preview.log'))
 print('DISPLAY_READY_NO_CAPTURE_OR_AI');print('CONFIG_URL=http://169.254.67.238:'+str(port)+'/windows_inputs.json');print('PREPARATION_SECONDS='+str(duration));print('AUTHORIZED_UNTIL='+authorized_until);print('SESSION='+c['session_id']);print('LATER_CAPTURE_COMMAND=python3 -B '+str(ROOT/'operator07.py')+' capture'+'')
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--host-utc',required=True);p.add_argument('--host-until',required=True);p.add_argument('--reviewed-boot-id',required=True);p.add_argument('--port',type=int,default=8098);a=p.parse_args();prepare(a.host_utc,a.host_until,a.reviewed_boot_id,a.port)
