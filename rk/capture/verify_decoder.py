"""Explicit saved-JPEG native decode check. No device, model, capture or network.
Creates a new proof directory. Never relabels an offline check as live acceptance.
"""
from pathlib import Path
import argparse,subprocess,time,json,hashlib,statistics
from jpeg_header import jpeg_size
from persistent_rgb import PersistentRGB
def digest(data):return hashlib.sha256(data).hexdigest()
def runtime_marker():return digest(subprocess.check_output(['gst-launch-1.0','--version'],timeout=3))
def verify_proof(path):
    value=json.loads(Path(path).read_bytes())
    if value.get('schema')!='qjzc.decoder.proof.v1'or value.get('accepted')is not True:raise ValueError('Native decoder proof required')
    if value.get('decoder_sha256')!=digest(Path(__file__).with_name('persistent_rgb.py').read_bytes())or value.get('runtime_marker')!=runtime_marker():raise ValueError('Decoder/runtime changed; repeat saved-JPEG check explicitly')
    return value
def main():
    p=argparse.ArgumentParser();p.add_argument('--jpeg',type=Path,required=True);p.add_argument('--record',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--repeat',type=int,default=3,choices=range(2,6));a=p.parse_args()
    if a.out.exists():raise SystemExit('Refuse overwrite or automatic retry')
    data=a.jpeg.read_bytes();record=json.loads(a.record.read_bytes())
    if digest(data)!=record['jpeg_sha256']or jpeg_size(data)!=(640,360)or record['preview_size']!=[640,360]:raise SystemExit('Saved input bytes/size differ')
    a.out.mkdir(parents=True)
    command=['gst-launch-1.0','-q','fdsrc','fd=0','!','jpegparse','!','jpegdec','!','videoconvert','!','video/x-raw,format=RGB','!','fdsink','fd=1','sync=false']
    slow=[];fast=[];reference=None;codec=None;value={'schema':'qjzc.decoder.proof.v1','accepted':False,'scope':'saved JPEG decode only; no inference/capture or live FPS','input_record_id':record['input_record_id'],'jpeg_sha256':record['jpeg_sha256']}
    try:
        for _ in range(a.repeat):
            t=time.monotonic();r=subprocess.run(command,input=data,capture_output=True,timeout=3);slow.append((time.monotonic()-t)*1000)
            if r.returncode or len(r.stdout)!=640*360*3:raise ValueError('Legacy decode failed')
            if reference is not None and r.stdout!=reference:raise ValueError('Legacy RGB is not stable')
            reference=r.stdout
        codec=PersistentRGB()
        for _ in range(a.repeat):
            t=time.monotonic();rgb=codec.decode_bytes(data,timeout=3);fast.append((time.monotonic()-t)*1000)
            if rgb!=reference:raise ValueError('RGB bytes differ; do not enable fast decoder')
        value.update(accepted=True,decoder_sha256=digest(Path(__file__).with_name('persistent_rgb.py').read_bytes()),runtime_marker=runtime_marker(),rgb_sha256=digest(reference),legacy_ms=slow,persistent_ms=fast,legacy_median_ms=statistics.median(slow),persistent_median_ms=statistics.median(fast))
    except Exception as e:value['error']=str(e);raise
    finally:
        try:
            if codec:codec.close()
        except Exception as e:
            value.update(accepted=False,error='Decoder cleanup failed: '+str(e));raise
        finally:(a.out/'decoder_proof.json').write_text(json.dumps(value,indent=2),encoding='utf-8')
    print(json.dumps(value,indent=2))
if __name__=='__main__':main()
