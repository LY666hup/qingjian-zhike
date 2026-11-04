"""Existing SOF byte parser reused; no imaging dependency."""
def jpeg_size(data):
    if not isinstance(data, bytes) or not data.startswith(b'\xff\xd8') or not data.endswith(b'\xff\xd9'):
        raise ValueError('JPEG boundary missing')
    i = 2
    while i + 4 <= len(data):
        if data[i] != 255:
            raise ValueError('invalid JPEG marker')
        while i < len(data) and data[i] == 255:
            i += 1
        if i >= len(data): break
        marker = data[i]; i += 1
        if marker in (0xD8, 0xD9, 0x01) or 0xD0 <= marker <= 0xD7:
            continue
        if marker == 0xDA: break
        length = int.from_bytes(data[i:i+2], 'big')
        if length < 2 or i + length > len(data):
            raise ValueError('truncated JPEG segment')
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                      0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            if length < 8: raise ValueError('short SOF')
            h = int.from_bytes(data[i+3:i+5], 'big')
            w = int.from_bytes(data[i+5:i+7], 'big')
            if w <= 0 or h <= 0: raise ValueError('empty JPEG')
            return w, h
        i += length
    raise ValueError('JPEG SOF missing')
