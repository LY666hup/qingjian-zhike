"""Read saved bytes only. QJDX is DDR-domain writer state, not sensor telemetry."""
from pathlib import Path
import argparse,json,struct,zlib
from parse_qj8v import parse_frame as parse_qj8

def decode_header(data,expected_sequence=None):
 if len(data)!=80 or data[:6]!=b'QJDX\x01\x50':raise ValueError('QJDX/1 header absent or invalid')
 if data[6]>4 or data[7]>7 or data[14]&192 or data[15] or any(data[72:76]):raise ValueError('QJDX state/reserved fields')
 if zlib.crc32(data[:76])!=int.from_bytes(data[76:80],'little'):raise ValueError('QJDX CRC32')
 sequence=int.from_bytes(data[8:12],'little')
 if expected_sequence is not None and sequence!=expected_sequence:raise ValueError('QJDX/QJ8V snapshot mismatch')
 count,source,published,dropped=struct.unpack_from('<4I',data,16)
 flags=data[14]
 return dict(protocol='QJDX/1',composite_sequence=sequence,writer_state=data[6],writer_state_name=('IDLE','AW','DATA','B','LOCK')[data[6]],
  writer_view=data[7],writer_view_active=data[6]!=0,source_fifo_valid_mask=data[12],source_fifo_ready_mask=data[13],
  axi_write=dict(awvalid=bool(flags&1),awready=bool(flags&2),wvalid=bool(flags&4),wready=bool(flags&8),bvalid=bool(flags&16),bready=bool(flags&32)),
  partial_frame_words=count,partial_frame_source_observation=source,published_total=published,dropped_total=dropped,
  source_empty_timeouts=list(struct.unpack_from('<8I',data,32)),source_empty_wait_cycles=int.from_bytes(data[64:68],'little'),
  write_progress_idle_cycles=int.from_bytes(data[68:72],'little'),
  scope='Writer fields sampled in DDR domain at accepted mosaic SOF; source FIFO heads are after CDC, not proof of sensor clock or exposure freshness.')

def parse_frame(raw,stride=3840):
 common=parse_qj8(raw,stride);base=360*stride+2*1384;block=raw[base:base+160]
 if len(block)!=160 or any(v!=128 for v in block[1::2]):raise ValueError('QJDX geometry/chroma')
 return dict(source_snapshot=common,writer_diagnostic=decode_header(block[::2],common['composite_sequence']))

if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('raw',type=Path);p.add_argument('--stride',type=int,default=3840)
 a=p.parse_args();print(json.dumps(parse_frame(a.raw.read_bytes(),a.stride),ensure_ascii=False,indent=2))
