"""User-approved 11-second raw39 short diagnostic; no AI or automatic retry."""
from pathlib import Path
import json,time,subprocess,os,signal,sys,argparse
from control import read,atomic
from mosaic_core import BIT
ROOT=Path(__file__).resolve().parent
def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=['capture']);p.parse_args()
    c=read(ROOT/'window.approved.json');run=Path(c['run_dir'])
    if c['fpga_bit_sha256']!=BIT or c['boot_id']!=Path('/proc/sys/kernel/random/boot_id').read_text().strip():raise SystemExit('07 bit/boot differs')
    if c['expires_monotonic_ms']-time.monotonic_ns()//1000000<20000:raise SystemExit('Window too short; no capture')
    if (run/'SHORT_ONCE.token').exists()or(run/'CAPTURE_ONCE.token').exists():raise SystemExit('Already attempted; no retry')
    owner=subprocess.run(['fuser','-v','/dev/video22'],capture_output=True,text=True,timeout=3)
    if owner.returncode!=1 or owner.stdout or owner.stderr:raise SystemExit('Device occupied or query failed: '+owner.stdout+owner.stderr)
    print('07 five-source diagnostic ONLY. One video22 process, target11s, INT14s/KILL1s, no AI/retry. No countdown.',flush=True)
    print('Confirm same downloaded39 bit, uninterrupted power/no reset/reconfiguration, current wiring and THIS capture window.',flush=True)
    if input('Type CAPTURE_07 to approve ONE short capture: ').strip()!='CAPTURE_07':raise SystemExit('Cancelled')
    if c['expires_monotonic_ms']-time.monotonic_ns()//1000000<20000:raise SystemExit('Window elapsed while confirming; no capture')
    with(run/'SHORT_ONCE.token').open('x')as f:f.write(c['session_id'])
    c.update(approval_status='APPROVED_BY_USER',capture_seconds=11);atomic(ROOT/'window.approved.json',c);atomic(run/'capture_approval.json',c)
    def kernel(name):
        r=subprocess.run(['dmesg'],capture_output=True,timeout=3);(run/name).write_bytes(r.stdout)
    kernel('kernel_before.log');child=None
    try:
        with(run/'capture.stdout.log').open('xb')as out,(run/'capture.stderr.log').open('xb')as err:
            child=subprocess.Popen(['timeout','--signal=INT','--kill-after=1','14',sys.executable,'-B',str(ROOT/'qj5v_capture.py'),'--window',str(ROOT/'window.approved.json'),'--protocol',str(ROOT/'protocol.template.json'),'--execute-approved'],stdout=out,stderr=err,start_new_session=True)
            child.wait()
        print('CAPTURE_EXIT='+str(child.returncode),flush=True)
    except KeyboardInterrupt:
        print('USER_STOP',flush=True)
        if child and child.poll()is None:
            os.killpg(child.pid,signal.SIGINT)
            try:child.wait(timeout=1)
            except subprocess.TimeoutExpired:os.killpg(child.pid,signal.SIGKILL);child.wait()
    finally:
        kernel('kernel_after.log');r=subprocess.run(['fuser','-v','/dev/video22'],capture_output=True,text=True,timeout=3)
        (run/'fuser_after.exit_code').write_text(str(r.returncode));(run/'fuser_after.stdout.log').write_text(r.stdout);(run/'fuser_after.stderr.log').write_text(r.stderr)
        state=read(run/'run.json',{});print('FUSER_AFTER='+str(r.returncode));print('STATE='+str(state.get('state')));print('ERROR='+str(state.get('error')));print('RUN_DIR='+str(run));print('NO_AUTOMATIC_RETRY')
        if r.stdout or r.stderr:print(r.stdout+r.stderr)
if __name__=='__main__':main()
