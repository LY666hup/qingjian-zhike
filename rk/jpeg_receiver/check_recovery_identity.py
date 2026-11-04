"""Reproduce old retry bug, then test current reset/retry/stale/wrap behavior."""
from pathlib import Path
import importlib.util
import hashlib
import json
import sys
import tempfile
import zlib

from jpeg_carrier_decode import CarrierError
from qjjc_receiver import CaptureIdentity, PlaneLayout, Receiver

BASE=Path(__file__).resolve().parent
OUT=Path(tempfile.mkdtemp(prefix='qjjc_recovery_check_'))
stream=(BASE/'fixtures/fullhd/carrier_stream.bin').read_bytes()
frames=[stream[i:i+65536]for i in range(0,len(stream),65536)]
identity=CaptureIdentity('offline-recovery-test','00000000-0000-0000-0000-000000000000','0'*64,'OFFLINE_RTL_REPLAY')
layout=PlaneLayout(512,64,'YUYV',1024,65536)

def feed(receiver,data,event):
    return receiver.feed_plane(data,bytesused=len(data),data_offset=0,v4l2_sequence=event,receive_monotonic_ns=event*1000000)

def damage(data):
    result=bytearray(data);result[130]^=1;return bytes(result)

def reseq(data,sequence):
    result=bytearray(data);result[8:12]=sequence.to_bytes(4,'little')
    result[124:128]=zlib.crc32(result[:124]).to_bytes(4,'little');return bytes(result)

def fault(call,fragment):
    try:call()
    except CarrierError as error:assert fragment in str(error),str(error);return str(error)
    raise AssertionError('Expected explicit rejection:'+fragment)

spec=importlib.util.spec_from_file_location('historical_receiver_v1',BASE/'historical_regression/qjjc_receiver_v1.py')
old=importlib.util.module_from_spec(spec);sys.modules[spec.name]=old;spec.loader.exec_module(old)
previous=old.Receiver(identity,layout)
assert feed(previous,frames[0],1)is None
first=feed(previous,frames[1],2)
fault(lambda:feed(previous,damage(frames[2]),3),'payload CRC')
assert feed(previous,frames[0],4)is None
duplicate=feed(previous,frames[1],5)
assert duplicate is not None and duplicate.input_record_id!=first.input_record_id
assert duplicate.record()['fpga_record_sequence']==first.record()['fpga_record_sequence']
results={'historical_bug_reproduced':dict(observed=True,verdict='EXPECTED_NEGATIVE_REPRODUCTION_NOT_CURRENT_PASS',
    detail='Published45 reissued a completed FPGA record under a new software ID after malformed input reset')}

current=Receiver(identity,layout)
assert feed(current,frames[0],1)is None
first=feed(current,frames[1],2);canonical=first.record_json_bytes
fault(lambda:feed(current,damage(frames[2]),3),'payload CRC')
assert feed(current,frames[0],4)is None and feed(current,frames[1],5)is None
assert current.completed_count==1 and current.latest is first and first.record_json_bytes==canonical
results['committed_retry_after_error']=dict(passed=True,no_new_id=True,canonical_record_unchanged=True)

current=Receiver(identity,layout)
for event,data in enumerate(frames,1):feed(current,data,event)
latest=current.latest
fault(lambda:feed(current,frames[0],5),'Stale FPGA record sequence')
assert current.completed_count==2 and current.latest is latest
results['old_completed_record_never_republished']=dict(passed=True)

current=Receiver(identity,layout);items=[]
for event,(data,seq)in enumerate(zip(frames,(0xffffffff,0xffffffff,0,0)),1):
    result=feed(current,reseq(data,seq),event)
    if result:items.append(result)
assert len(items)==2 and [item.record()['fpga_record_sequence']for item in items]==[0xffffffff,0]
assert [item.input_record_id for item in items]==[identity.session+':fpga-jpeg:1',identity.session+':fpga-jpeg:2']
results['serial32_wrap_admitted']=dict(passed=True)

current=Receiver(identity,layout)
assert feed(current,frames[0],1)is None
feed(current,frames[1],2)
changed=bytearray(frames[0]);changed[48]^=1
changed[124:128]=zlib.crc32(changed[:124]).to_bytes(4,'little')
fault(lambda:feed(current,bytes(changed),3),'Changed retry')
assert current.completed_count==1
results['changed_committed_snapshot_rejected']=dict(passed=True)

current=Receiver(identity,layout)
assert feed(current,frames[0],1)is None;feed(current,frames[1],2)
assert feed(current,frames[2],3)is None
assert feed(current,frames[0],4)is None
second=feed(current,frames[3],5)
assert second is not None and current.completed_count==2 and current.incomplete_records_discarded==0
results['late_verified_retry_preserves_new_prefix']=dict(passed=True)

summary=dict(passed=True,current_positive_checks=5,historical_negative_reproductions=1,checks=results,
    hardware_access=False,scope='Offline protocol replay;not source-epoch persistence across processes or board identity proof',
    current_source_sha256=hashlib.sha256((BASE/'qjjc_receiver.py').read_bytes()).hexdigest(),
    historical_source_sha256=hashlib.sha256((BASE/'historical_regression/qjjc_receiver_v1.py').read_bytes()).hexdigest())
(OUT/'RESULT.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps(dict(passed=True,current_positive_checks=5,historical_expected_failure_reproduced=True,output=str(OUT))))
