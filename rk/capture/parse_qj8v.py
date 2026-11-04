"""Read native raw bytes only; no capture, encoding, clocks or identity generation."""
from pathlib import Path
import argparse,json,struct,zlib

VIEWS=('A_original','A_gray','A_gaussian','A_histogram','B_original','B_gray','B_gaussian','B_histogram')
ROIS=((0,0,640,360),(640,0,640,360),(1280,0,640,360),(0,360,640,360),(640,360,640,360),
      (0,720,640,360),(640,720,640,360),(1280,720,640,360))

def parse_frame(raw,stride=3840):
 if type(stride) is not int or stride<3840 or stride%4 or len(raw)!=stride*1080:
  raise ValueError('Expected one native1920x1080 YUYV frame and actual aligned stride')
 base=360*stride+2560
 pixels=raw[base:base+208]
 if len(pixels)!=208 or any(value!=128 for value in pixels[1::2]):raise ValueError('Metadata chroma/geometry')
 data=pixels[::2];legacy=data[:40];extended=data[40:]
 if legacy[:5]!=b'QJ5V\x01' or legacy[5]&224 or legacy[6:8]!=bytes(2) or legacy[37:]!=bytes(3):
  raise ValueError('Legacy QJ5V/1 missing or invalid')
 xor=0
 for byte in legacy[:36]:xor^=byte
 if xor!=legacy[36]:raise ValueError('Legacy metadata XOR')
 if extended[:5]!=b'QJ8V\x01' or extended[6:8]!=b'\x08\0' or extended[48:60]!=bytes(12):
  raise ValueError('QJ8V/1 extension/format/reserved')
 if zlib.crc32(extended[:60])!=int.from_bytes(extended[60:],'little'):raise ValueError('QJ8V metadata CRC')
 seq=int.from_bytes(extended[8:12],'little');ids=struct.unpack_from('<8I',extended,12)
 fault=int.from_bytes(extended[44:48],'little');ready=extended[5]
 if (seq!=int.from_bytes(legacy[8:12],'little') or ready&31!=legacy[5] or
     ids[:5]!=struct.unpack_from('<5I',legacy,12) or fault!=int.from_bytes(legacy[32:36],'little')):
  raise ValueError('Legacy/extended snapshot identity mismatch')
 return dict(protocol='QJ5V/1+QJ8V/1',composite_sequence=seq,ready_mask=ready,fault_mask=fault,stride=stride,
  metadata_valid=True,views=[dict(view=name,roi=list(ROIS[i]),ready=bool(ready&(1<<i)),observed_frame_id=ids[i]) for i,name in enumerate(VIEWS)],
  scope='Raw length and metadata only; not whole-image CRC, sensor frame rate, exposure synchronization or image-quality acceptance.')

if __name__=='__main__':
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('raw',type=Path);parser.add_argument('--stride',type=int,default=3840)
 args=parser.parse_args();print(json.dumps(parse_frame(args.raw.read_bytes(),args.stride),ensure_ascii=False,indent=2))
