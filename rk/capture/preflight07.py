"""Read-only current boot/network/media/plugin check; never STREAMON or AI."""
from pathlib import Path
import subprocess,json,sys
from mosaic_core import BIT,Protocol
ROOT=Path(__file__).resolve().parent
def main():
    report={'scope':'read-only; no capture/NPU/MediaPipe','bit_expected':BIT,'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),'checks':[]};ok=True
    commands=[['ip','-j','-4','addr','show','dev','end0'],['fuser','-v','/dev/video22'],['v4l2-ctl','-d','/dev/video22','-D'],['v4l2-ctl','-d','/dev/video22','--get-fmt-video'],['v4l2-ctl','-d','/dev/video22','--try-fmt-video=width=1920,height=1080,pixelformat=YUYV,field=none'],['media-ctl','-d','/dev/media2','-p'],['df','-B1',str(ROOT)],['gst-launch-1.0','--version']]
    for cmd in commands:
        print('CHECK '+' '.join(cmd),flush=True);p=subprocess.run(cmd,capture_output=True,text=True,timeout=5);row=dict(command=cmd,rc=p.returncode,out=p.stdout,err=p.stderr);report['checks'].append(row)
        if cmd[0]=='fuser':ok&=p.returncode==1 and not(p.stdout or p.stderr)
        else:ok&=p.returncode==0
        if cmd[0]=='ip':
            data=json.loads(p.stdout);ok&=any(v.get('local')=='169.254.67.238'for dev in data for v in dev.get('addr_info',[]))
        if cmd[-1]=='-D':ok&='platform:rkcif-mipi-lvds3'in p.stdout
        if '--try-fmt-video' in cmd[-1]:ok&=all(v in p.stdout for v in('1920/1080','YUYV','3840','4147200'))
    for name in('fdsrc','rawvideoparse','videoconvert','jpegenc','fdsink'):
        p=subprocess.run(['gst-inspect-1.0',name],capture_output=True,text=True,timeout=5);report['checks'].append(dict(plugin=name,rc=p.returncode));ok&=p.returncode==0
    Protocol(json.loads((ROOT/'protocol.template.json').read_bytes()))
    import shutil
    report['free_disk_bytes']=shutil.disk_usage(ROOT).free;ok&=report['free_disk_bytes']>=1536*1024**2
    report.update(ready=bool(ok),note='TRY_FMT is not committed G_FMT; actual S_FMT/G_FMT/stride/field checked inside separately approved capture before STREAMON.')
    (ROOT/'preflight07.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('PREFLIGHT07_READY_NO_CAPTURE_OR_AI'if ok else'PREFLIGHT07_FAILED_NO_CAPTURE',flush=True)
    return 0 if ok else 2
if __name__=='__main__':raise SystemExit(main())
