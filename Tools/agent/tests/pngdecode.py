"""Minimal PNG decoder for 8-bit RGB and RGBA images, standard library only."""

import struct
import zlib

SIGNATURE = b"\x89PNG\r\n\x1a\n"
CHANNELS = {2: 3, 6: 4}


def decode(path):
    """Return (width, height, channels, rows) where rows is a list of bytes per row."""
    with open(path, "rb") as f:
        data = f.read()
    if not data.startswith(SIGNATURE):
        raise ValueError(f"{path} is not a PNG")

    pos = len(SIGNATURE)
    header = None
    idat = b""
    while pos < len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        chunk = data[pos + 8:pos + 8 + length]
        crc = struct.unpack(">I", data[pos + 8 + length:pos + 12 + length])[0]
        if zlib.crc32(kind + chunk) & 0xFFFFFFFF != crc:
            raise ValueError(f"{path} has a corrupt {kind!r} chunk")
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", chunk)
        elif kind == b"IDAT":
            idat += chunk
        elif kind == b"IEND":
            break
        pos += 12 + length

    if header is None:
        raise ValueError(f"{path} has no IHDR chunk")
    width, height, depth, color, _, _, interlace = header
    if depth != 8 or color not in CHANNELS or interlace:
        raise ValueError(f"{path} uses an unsupported PNG layout")

    channels = CHANNELS[color]
    stride = width * channels
    raw = zlib.decompress(idat)
    if len(raw) != height * (stride + 1):
        raise ValueError(f"{path} has truncated image data")

    rows = []
    prev = bytearray(stride)
    for y in range(height):
        start = y * (stride + 1)
        kind = raw[start]
        row = bytearray(raw[start + 1:start + 1 + stride])
        for i in range(stride):
            a = row[i - channels] if i >= channels else 0
            b = prev[i]
            c = prev[i - channels] if i >= channels else 0
            if kind == 1:
                row[i] = (row[i] + a) & 0xFF
            elif kind == 2:
                row[i] = (row[i] + b) & 0xFF
            elif kind == 3:
                row[i] = (row[i] + (a + b) // 2) & 0xFF
            elif kind == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                row[i] = (row[i] + pred) & 0xFF
            elif kind != 0:
                raise ValueError(f"{path} uses unknown filter {kind}")
        rows.append(bytes(row))
        prev = row
    return width, height, channels, rows


def is_flat(width, channels, rows):
    """True when every pixel has the same value."""
    first = rows[0][:channels] * width
    return all(row == first for row in rows)


def difference(rows_a, rows_b):
    """Fraction of bytes that differ between two decoded images of equal size."""
    total = changed = 0
    for a, b in zip(rows_a, rows_b):
        total += len(a)
        changed += sum(1 for x, y in zip(a, b) if x != y)
    return changed / total if total else 0.0
