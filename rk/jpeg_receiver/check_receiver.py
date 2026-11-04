"""Offline tests of an actual RTL CSI fixture plus transport/identity negatives."""
from dataclasses import FrozenInstanceError
from pathlib import Path
import argparse
import hashlib
import json
import struct
import tempfile
import zlib

from PIL import Image
from jpeg_carrier_decode import CarrierError, parse_carrier
from qjjc_receiver import CaptureIdentity, PlaneLayout, Receiver

BASE = Path(__file__).resolve().parent
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--fixtures',type=Path)
parser.add_argument('--output',type=Path)
args=parser.parse_args()
REFERENCE = args.fixtures or (BASE/'fixtures/fullhd' if (BASE/'fixtures/fullhd').is_dir() else BASE.parent/'jpeg_dc_only_20261103_42/verification/fullhd_candidate')
if args.output:
    OUT=args.output;OUT.mkdir(exist_ok=False)
else:
    OUT=Path(tempfile.mkdtemp(prefix='qjjc_offline_check_'))
stream = (REFERENCE / 'carrier_stream.bin').read_bytes()
assert len(stream) % 65536 == 0
carriers = [stream[i:i+65536] for i in range(0, len(stream), 65536)]
assert len(carriers) == 4
expected = [(REFERENCE / f'frame{i}.jpg').read_bytes() for i in range(2)]
identity = CaptureIdentity('offline-rtl-fixture42', '00000000-0000-0000-0000-000000000000', '0'*64, 'OFFLINE_RTL_REPLAY')
layout = PlaneLayout(512,64,'YUYV',1024,65536)
results = {}

def receiver():
    return Receiver(identity,layout)

def feed(target,data,event,**kwargs):
    return target.feed_plane(data,bytesused=len(data),data_offset=0,v4l2_sequence=event,
        receive_monotonic_ns=event*1000000,**kwargs)

def rejected(name,call,reason):
    try:
        call()
    except (ValueError,TypeError,CarrierError,AttributeError) as error:
        assert reason in str(error),(name,str(error))
        results[name]=dict(passed=True,rejected_as=str(error))
    else:
        raise AssertionError('Invalid input accepted:'+name)

target=receiver();published=[]
for event,data in enumerate(carriers,1):
    result=feed(target,data,event)
    if result:published.append(result)
assert len(published)==2 and [item.jpeg_bytes for item in published]==expected
for index,item in enumerate(published):
    path=OUT/f'frame{index}.jpg';path.write_bytes(item.jpeg_bytes)
    with Image.open(path)as picture:picture.load();assert picture.size==(1920,1080)
    record=item.record()
    assert record['evidence_level']=='OFFLINE_RTL_REPLAY' and record['unique_carriers']==2
    assert record['compression']['carrier_image_payload_bytes']==131072
    assert record['compression']['raw_equivalent_yuyv_bytes']==4147200
    (OUT/f'frame{index}.record.json').write_bytes(item.record_json_bytes)
saved=published[-1].record_json_bytes
copied_record=published[-1].record();copied_record['source_observation_ids'][0]=999999
assert published[-1].record_json_bytes==saved
assert feed(target,carriers[-1],10)is None and target.latest is published[-1]
assert target.latest.record_json_bytes==saved and target.completed_count==2
results['actual_fullHD_RTL_and_immutable_retry']=dict(passed=True,decoded_images=2,unique_carriers_per_JPEG=2)

target=receiver();published=[];event=0
for data in carriers:
    for repeat in range(2):
        event+=1
        result=feed(target,data,event)
        if result:published.append(result)
assert len(published)==2 and [item.jpeg_bytes for item in published]==expected
results['repeat_every_carrier_no_republish']=dict(passed=True,completed_records=target.completed_count)

padded_layout=PlaneLayout(512,64,'YUYV',1056,12+1056*64)
target=Receiver(identity,padded_layout);published=[]
for event,data in enumerate(carriers,1):
    padded=b'\xa5'*12+b''.join(data[y*1024:(y+1)*1024]+b'\xcc'*32 for y in range(64))
    result=target.feed_plane(padded,bytesused=len(padded),data_offset=12,v4l2_sequence=event,receive_monotonic_ns=event*1000000)
    if result:published.append(result)
assert len(published)==2 and [item.jpeg_bytes for item in published]==expected
assert published[0].record()['first_data_offset']==12
results['padded_stride_and_offset']=dict(passed=True,jpeg_bytes_unchanged=True)

target=receiver();assert feed(target,carriers[0],1)is None
broken=bytearray(carriers[1]);broken[130]^=1
rejected('payload_corruption',lambda:feed(target,bytes(broken),2),'payload CRC')
assert target.latest is None and target.incomplete_records_discarded==1
assert feed(target,carriers[2],3)is None
assert feed(target,carriers[3],4).jpeg_bytes==expected[1]
results['recover_on_next_valid_first']=dict(passed=True,invalid_events=target.invalid_events)

target=receiver();assert feed(target,carriers[0],1)is None
mixed=bytearray(carriers[1]);mixed[48]^=1
mixed[124:128]=zlib.crc32(mixed[:124]).to_bytes(4,'little')
rejected('mixed_source_snapshot',lambda:feed(target,bytes(mixed),2),'mixed source identity')
assert target.latest is None

target=receiver();assert feed(target,carriers[0],1)is None
assert feed(target,carriers[2],2)is None
result=feed(target,carriers[3],3)
assert result.jpeg_bytes==expected[1]and target.incomplete_records_discarded==1
results['missing_chunk_drops_old_record']=dict(passed=True,published_only_second_JPEG=True)

target=receiver();bad=bytearray(carriers[0]);bad[36:44]=struct.pack('<4H',1920,1080,1904,1080)
bad[124:128]=zlib.crc32(bad[:124]).to_bytes(4,'little')
rejected('mismatched_carrier_geometry',lambda:feed(target,bytes(bad),1),'image geometry')
rejected('raw39_layout_is_not_carrier',lambda:PlaneLayout(1920,1080,'YUYV',3840,4147200),'negotiated512x64')
rejected('mutable_mmap_view_rejected',lambda:feed(receiver(),bytearray(carriers[0]),1),'immutable copied bytes')
rejected('V4L2_ERROR_rejected',lambda:feed(receiver(),carriers[0],1,buffer_flags=0x40),'V4L2_BUF_FLAG_ERROR')
target=receiver();assert feed(target,carriers[0],2)is None
rejected('backwards_monotonic_rejected',lambda:feed(target,carriers[1],1),'moved backwards')

target=receiver();assert feed(target,carriers[0],1)is None
rejected('identity_not_replaceable_mid_record',lambda:setattr(target,'identity',identity),'has no setter')
assert feed(target,carriers[1],2).jpeg_bytes==expected[0]
target=receiver()
for event,carrier in enumerate(carriers[:2],1):
    changed=bytearray(carrier)
    changed[36:44]=struct.pack('<4H',1904,1080,1904,1080)
    changed[124:128]=zlib.crc32(changed[:124]).to_bytes(4,'little')
    if event==1:assert feed(target,bytes(changed),event)is None
    else:rejected('JPEG_SOF_geometry_matches_commit',lambda:feed(target,bytes(changed),event),'JPEG SOF')
assert target.latest is None

summary=dict(passed=True,checks=results,hardware_access=False,kernel_or_service_modified=False,
    fixture_scope='Two synthetic full-HD RTL JPEGs from42 recovered after actual RTL CSI packetizer;not actual RK/PHY/board capture',
    fixture_stream_sha256=hashlib.sha256(stream).hexdigest(),
    fixture_actual_capture_identity=False,fixture_boot_and_bit_fields='Explicit all-zero placeholders ONLY under OFFLINE_RTL_REPLAY',
    codec_JPEG_images=[dict(bytes=len(data),sha256=hashlib.sha256(data).hexdigest())for data in expected],
    current_sources={name:hashlib.sha256((BASE/name).read_bytes()).hexdigest()for name in ('qjjc_receiver.py','jpeg_carrier_decode.py')})
(OUT/'RESULT.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps(dict(passed=True,checks=len(results),hardware_access=False,decoded_images=2,output=str(OUT))))
