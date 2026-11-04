"""QJJC/2 ingestion adapter. No device/network access, encoding or time sampling.

The caller owns real format negotiation and provides copied, immutable V4L2
plane bytes plus actual identity/timestamps. This module cannot establish board
identity or make a fixed1920x1080 kernel driver accept512x64.
"""
from dataclasses import dataclass
import json
import re
import struct
import uuid

from jpeg_carrier_decode import Assembler, CarrierError, parse_carrier


@dataclass(frozen=True)
class CaptureIdentity:
    session: str
    boot_id: str
    bit_sha256: str
    evidence_level: str

    def __post_init__(self):
        if not self.session or any(ord(c) < 32 for c in self.session):
            raise ValueError('A real capture session is required')
        if str(uuid.UUID(self.boot_id)) != self.boot_id.lower():
            raise ValueError('Canonical actual RK boot UUID required')
        if re.fullmatch(r'[0-9a-f]{64}', self.bit_sha256) is None:
            raise ValueError('Full lowercase bit SHA256 required')
        if self.evidence_level not in ('BOARD_CAPTURE', 'OFFLINE_RTL_REPLAY'):
            raise ValueError('Explicit evidence level required')


@dataclass(frozen=True)
class PlaneLayout:
    width: int
    height: int
    pixel_format: str
    stride: int
    sizeimage: int
    field: str = 'NONE'
    planes: int = 1

    def __post_init__(self):
        if (self.width, self.height, self.pixel_format, self.field, self.planes) != (512, 64, 'YUYV', 'NONE', 1):
            raise ValueError('Expected negotiated512x64 YUYV/fieldNONE/single-plane carrier layout')
        if type(self.stride) is not int or type(self.sizeimage) is not int or self.stride < 1024 or self.stride % 4 or self.sizeimage < self.stride * 64:
            raise ValueError('Invalid actual carrier stride/sizeimage')


@dataclass(frozen=True)
class CompletedJPEG:
    input_record_id: str
    jpeg_bytes: bytes
    record_json_bytes: bytes

    def record(self):
        """Return a copy; consumer mutations cannot change the canonical record."""
        return json.loads(self.record_json_bytes)


def jpeg_header_size(data):
    """Validate baseline JPEG framing/SOF dimensions, not entropy/visual quality."""
    if len(data) < 4 or data[:2] != b'\xff\xd8' or data[-2:] != b'\xff\xd9':
        raise CarrierError('JPEG SOI/EOI missing')
    offset = 2
    size = None
    while offset < len(data) - 2:
        if data[offset] != 255:
            raise CarrierError('Invalid JPEG marker framing before scan')
        while offset < len(data) and data[offset] == 255:
            offset += 1
        if offset >= len(data):
            raise CarrierError('Truncated JPEG marker')
        marker = data[offset]
        offset += 1
        if marker in (0, 0xd8, 0xd9) or 0xd0 <= marker <= 0xd7:
            raise CarrierError('Unexpected standalone JPEG marker before scan')
        if offset + 2 > len(data):
            raise CarrierError('Truncated JPEG segment length')
        length = int.from_bytes(data[offset:offset + 2], 'big')
        if length < 2 or offset + length > len(data):
            raise CarrierError('Invalid JPEG segment length')
        if marker == 0xc0:
            if length != 17 or data[offset + 2] != 8 or data[offset + 7] != 3:
                raise CarrierError('Expected8bit three-component baseline JPEG')
            height, width = struct.unpack_from('>HH', data, offset + 3)
            if size is not None:
                raise CarrierError('Multiple JPEG SOF markers')
            size = (width, height)
        elif marker == 0xda:
            if size is None:
                raise CarrierError('JPEG scan before baseline SOF')
            return size
        offset += length
    raise CarrierError('JPEG scan absent')


class Receiver:
    """Bounded assembly, exact retries do not republish or rotate record identity."""
    def __init__(self, identity, layout, max_jpeg_bytes=8 * 1024 * 1024):
        self._identity = identity
        self._layout = layout
        self.max_jpeg_bytes = max_jpeg_bytes
        self.assembler = Assembler(max_bytes=max_jpeg_bytes)
        self._latest = None
        self._completed_count = 0
        self.invalid_events = 0
        self.incomplete_records_discarded = 0
        self._last_receive_ns = None
        self._pending = None
        self._last_committed = None

    @property
    def identity(self):
        return self._identity

    @property
    def layout(self):
        return self._layout

    @property
    def latest(self):
        return self._latest

    @property
    def completed_count(self):
        return self._completed_count

    def _discard_pending(self):
        if self._pending and not self._pending['committed']:
            self.incomplete_records_discarded += 1
        self._pending = None
        self.assembler = Assembler(max_bytes=self.max_jpeg_bytes)

    def feed_plane(self, copied_plane, *, bytesused, data_offset, v4l2_sequence, receive_monotonic_ns, buffer_flags=0):
        """Called only AFTER copying a DQBUF plane and freezing its real timestamp.

        bytesused follows V4L2: includes data_offset. The adapter strips physical
        row padding, and never color-converts carrier bytes. A raised error
        discards the speculative record; the next first chunk starts recovery.
        """
        try:
            if type(copied_plane) is not bytes:
                raise CarrierError('Caller must provide immutable copied bytes before QBUF')
            if any(type(value) is not int for value in (bytesused, data_offset, v4l2_sequence, receive_monotonic_ns, buffer_flags)):
                raise CarrierError('Integer capture metadata required')
            if (data_offset < 0 or data_offset + self.layout.stride * 64 > bytesused or
                    bytesused > len(copied_plane) or len(copied_plane) < self.layout.sizeimage):
                raise CarrierError('Incomplete/invalid plane or data_offset')
            if not 0 <= v4l2_sequence <= 0xffffffff or receive_monotonic_ns < 0:
                raise CarrierError('Invalid driver sequence or receive timestamp')
            if self._last_receive_ns is not None and receive_monotonic_ns < self._last_receive_ns:
                raise CarrierError('Receive monotonic time moved backwards')
            self._last_receive_ns = receive_monotonic_ns
            if buffer_flags & 0x40:
                raise CarrierError('V4L2_BUF_FLAG_ERROR')
            image = copied_plane[data_offset:data_offset + self.layout.stride * 64]
            packet = parse_carrier(image, stride=self.layout.stride)
            if packet['source_ready'] == 0:
                raise CarrierError('No real source ready in a completed image path')
            # Preserve completed-record deduplication across speculative resets.
            # RFC1982-style half-range comparison admits32bit wrap, rejects stale
            # completed records and detects an uncoordinated source reset.
            packet_identity=(tuple(packet['native_size']),tuple(packet['jpeg_size']),
                             packet['source_ready'],tuple(packet['source_frames']))
            previous=self._last_committed
            if previous is not None:
                distance=(packet['record_sequence']-previous['sequence'])&0xffffffff
                if distance==0:
                    if packet_identity!=previous['identity'] or previous['chunks'].get(packet['chunk_index'])!=packet['carrier_sha256']:
                        raise CarrierError('Changed retry of a committed input record')
                    return None
                if distance>=0x80000000:
                    raise CarrierError('Stale FPGA record sequence;new FPGA epoch requires a new capture session')
            pending = self._pending
            switched = False
            if packet['first']:
                retry = (self._pending is not None and self._pending['chunks'].get(0) == packet['carrier_sha256'])
                if not retry:
                    pending = dict(first_ns=receive_monotonic_ns, first_sequence=v4l2_sequence,
                                   first_data_offset=data_offset, chunks={}, committed=False, capture_events=0)
                    switched = True
            if pending is None:
                raise CarrierError('No accepted first carrier')
            completed = self.assembler.feed(packet)
            if switched and self._pending and not self._pending['committed']:
                self.incomplete_records_discarded += 1
            self._pending = pending
            pending['capture_events'] += 1
            pending['chunks'].setdefault(packet['chunk_index'], packet['carrier_sha256'])
            if completed is None:
                return None
            data = completed['jpeg_bytes']
            width, height = jpeg_header_size(data)
            if [width, height] != completed['jpeg_size']:
                raise CarrierError('JPEG SOF and committed carrier geometry differ')
            next_count = self.completed_count + 1
            record_id = self.identity.session + ':fpga-jpeg:' + str(next_count)
            raw_bytes = width * height * 2
            transport_bytes = len(pending['chunks']) * 65536
            record = dict(schema='qjzc.fpga_jpeg.record.v1', input_record_id=record_id,
                evidence_level=self.identity.evidence_level, session=self.identity.session, boot=self.identity.boot_id,
                bit_sha256=self.identity.bit_sha256, camera='mosaic', processing_mode='eight_source_mosaic', processor='fpga_jpeg',
                jpeg_sha256=completed['jpeg_sha256'], jpeg_bytes=len(data), jpeg_width=width, jpeg_height=height,
                native_size=completed['native_size'], source_ready_mask=completed['source_ready'], source_observation_ids=completed['source_frames'],
                fpga_record_sequence=completed['record_sequence'], software_completed_record_index=next_count,
                first_carrier_receive_monotonic_ns=pending['first_ns'], complete_carrier_receive_monotonic_ns=receive_monotonic_ns,
                timestamp_ms=receive_monotonic_ns // 1000000,
                time_source='rk_complete_carrier_receive_monotonic' if self.identity.evidence_level=='BOARD_CAPTURE' else 'offline_fixture_monotonic',
                first_v4l2_sequence=pending['first_sequence'], last_v4l2_sequence=v4l2_sequence,
                carrier_layout=dict(width=512,height=64,pixel_format='YUYV',field='NONE',stride=self.layout.stride,sizeimage=self.layout.sizeimage),
                first_data_offset=pending['first_data_offset'], complete_data_offset=data_offset,
                unique_carriers=len(pending['chunks']), receive_events_through_commit=pending['capture_events'],
                compression=dict(raw_equivalent_yuyv_bytes=raw_bytes, jpeg_payload_bytes=len(data),
                    raw_over_jpeg_ratio=raw_bytes / len(data), jpeg_size_reduction_percent=(1-len(data)/raw_bytes)*100,
                    carrier_image_payload_bytes=transport_bytes, raw_over_carrier_payload_ratio=raw_bytes/transport_bytes,
                    carrier_size_reduction_percent=(1-transport_bytes/raw_bytes)*100,
                    scope='Complete encoded mosaic including reserved/unready areas;carrier payload excludes CSI packet headers/CRC/PHY overhead.'),
                validations=dict(carrier_headers=True, carrier_payload_crc=True, whole_jpeg_length_crc=True,
                    source_snapshot_consistent=True, jpeg_sof_geometry=True, jpeg_entropy_decode=False),
                interpretation='Software input record,not exposure frame number or person tracking identity;no same-frame raw reference exported.')
            canonical = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
            result = CompletedJPEG(record_id, data, canonical)
            self._completed_count = next_count
            self._latest = result
            pending['committed'] = True
            self._last_committed=dict(sequence=completed['record_sequence'],identity=packet_identity,
                                      chunks=dict(pending['chunks']))
            return result
        except (CarrierError, ValueError, TypeError, IndexError, struct.error):
            self.invalid_events += 1
            self._discard_pending()
            raise
