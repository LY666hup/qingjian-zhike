"""Parse metadata from exactly one native 1920x1080 packed-YUYV frame.
No capture, encoding, network access, or fabricated source identity.
"""
from pathlib import Path
import argparse,json

VIEWS=('A_original','A_gray','A_gaussian','A_histogram','B_original')
ROIS=((0,0,640,360),(640,0,640,360),(1280,0,640,360),(0,360,640,360),(640,360,640,360))

def parse_frame(raw, stride=3840):
    if type(stride) is not int or stride<3840 or stride%4:
        raise ValueError('stride must be actual aligned YUYV bytes/line, >=3840')
    if len(raw)!=stride*1080:
        raise ValueError('expected exactly one complete frame: stride*1080 bytes')
    base=360*stride+2560
    message=bytes(raw[base+2*i] for i in range(40))
    if any(raw[base+2*i+1]!=128 for i in range(40)):
        raise ValueError('diagnostic chroma mismatch; check actual format/stride/frame')
    if message[:4]!=b'QJ5V' or message[4]!=1:
        raise ValueError('QJ5V/version mismatch; do not assume bit/layout is verified')
    checksum=0
    for x in message[:36]:checksum^=x
    if checksum!=message[36]:raise ValueError('metadata XOR mismatch')
    if message[5]&0xe0 or message[6:8]!=b'\0\0' or message[37:40]!=b'\0\0\0':
        raise ValueError('reserved fields do not match version1')
    u32=lambda i:int.from_bytes(message[i:i+4],'little')
    mask=message[5]
    return {'protocol':'QJ5V/1','metadata_valid':True,'file_length_matches_frame':True,
            'driver_error_status':'NOT_CHECKED_BY_FILE_PARSER','stride':stride,
            'metadata_y_byte_offset':base,'composite_sequence':u32(8),'ready_mask':mask,
            'fault_mask':u32(32),'views':[
                {'view':name,'ready':bool(mask&(1<<i)),'observed_frame_id':u32(12+4*i),'roi':list(ROIS[i])}
                for i,name in enumerate(VIEWS)],
            'scope':'Length/metadata checks only; not image quality, fresh exposure, FPS or board acceptance'}

if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('raw',type=Path);ap.add_argument('--stride',type=int,default=3840)
    a=ap.parse_args()
    print(json.dumps(parse_frame(a.raw.read_bytes(),a.stride),ensure_ascii=False,indent=2))
