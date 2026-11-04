"""Pure QJJC/2 receiver. No device/network I/O on import; no fabricated identity."""
from dataclasses import dataclass
import struct,zlib,hashlib

class CarrierError(ValueError):pass

def parse_carrier(raw,width=512,height=64,stride=None):
    stride=stride or width*2
    if stride<width*2 or len(raw)!=stride*height:raise CarrierError('incomplete carrier/stride')
    data=b''.join(raw[y*stride:y*stride+width*2] for y in range(height))
    h=data[:128]
    if h[:4]!=b'QJJC' or h[4]!=2 or int.from_bytes(h[6:8],'little')!=128:raise CarrierError('header/version')
    if zlib.crc32(h[:124])!=int.from_bytes(h[124:128],'little'):raise CarrierError('header CRC')
    flags=h[5]
    if flags&~7 or bool(flags&2)!=bool(flags&4):raise CarrierError('commit flags')
    seq,chunk,offset,length,crc,total_length,total_crc=struct.unpack_from('<7I',h,8)
    native_w,native_h,jpeg_w,jpeg_h=struct.unpack_from('<4H',h,36)
    if not 0<length<=len(data)-128:raise CarrierError('payload length')
    if bool(flags&1)!=(chunk==0) or (chunk==0 and offset!=0):raise CarrierError('first chunk identity')
    if h[45:48]!=b'\x01\x02\x08' or any(h[80:124]):raise CarrierError('format/reserved')
    if not (native_w==jpeg_w and 0<jpeg_h<=native_h and jpeg_w%16==0 and jpeg_h%8==0):raise CarrierError('image geometry')
    payload=data[128:128+length]
    if zlib.crc32(payload)!=crc:raise CarrierError('payload CRC')
    if any(data[128+length:]):raise CarrierError('nonzero padding')
    if not flags&4 and (total_length or total_crc):raise CarrierError('uncommitted total')
    return dict(record_sequence=seq,chunk_index=chunk,offset=offset,payload=payload,
                first=bool(flags&1),last=bool(flags&2),commit=bool(flags&4),
                total_length=total_length,total_crc32=total_crc,native_size=[native_w,native_h],jpeg_size=[jpeg_w,jpeg_h],
                source_ready=h[44],source_frames=list(struct.unpack_from('<8I',h,48)),carrier_sha256=hashlib.sha256(data).hexdigest())

class Assembler:
    """A bounded private in-progress record. Only final validated bytes publish.

    session/boot/bit identity must come from an external actual capture record;
    a local 32bit counter is never promoted to an exposure ID or boot identity.
    """
    def __init__(self,max_bytes=8*1024*1024):
        self.max_bytes=max_bytes;self.active=None;self.completed=None
    def feed(self,packet):
        if packet['first']:
            if self.active and self.active['chunks'].get(0)==packet['carrier_sha256']:return None
            if self.active and self.active['sequence']==packet['record_sequence']:raise CarrierError('changed first chunk for same record')
            self.active=dict(sequence=packet['record_sequence'],next_chunk=0,data=bytearray(),chunks={},
                             identity=(packet['native_size'],packet['jpeg_size'],packet['source_ready'],packet['source_frames']))
        r=self.active
        if r is None:raise CarrierError('no first chunk')
        if packet['record_sequence']!=r['sequence'] or (packet['native_size'],packet['jpeg_size'],packet['source_ready'],packet['source_frames'])!=r['identity']:
            raise CarrierError('mixed source identity')
        i=packet['chunk_index']
        if i in r['chunks']:
            if r['chunks'][i]!=packet['carrier_sha256']:raise CarrierError('changed retry')
            return None
        if i!=r['next_chunk'] or packet['offset']!=len(r['data']):raise CarrierError('missing/reordered chunk')
        if len(r['data'])+len(packet['payload'])>self.max_bytes:raise CarrierError('JPEG size budget')
        r['data'].extend(packet['payload']);r['chunks'][i]=packet['carrier_sha256'];r['next_chunk']+=1
        if not packet['commit']:return None
        raw=bytes(r['data'])
        if len(raw)!=packet['total_length'] or zlib.crc32(raw)!=packet['total_crc32']:raise CarrierError('whole JPEG CRC/length')
        result=dict(record_sequence=r['sequence'],jpeg_bytes=raw,jpeg_sha256=hashlib.sha256(raw).hexdigest(),
                    native_size=packet['native_size'],jpeg_size=packet['jpeg_size'],source_ready=packet['source_ready'],source_frames=packet['source_frames'])
        self.completed=result
        # Keep bounded chunk hashes so exact retries do not publish another frame.
        return result
