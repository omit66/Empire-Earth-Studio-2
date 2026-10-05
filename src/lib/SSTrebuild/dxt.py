#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Helpers for the (single level) DDS images stored inside Empire Earth / Empires SST files.
Only DXT1, DXT3 and DXT5 are handled. No third party libraries needed.
"""

import struct
from typing import NamedTuple

DDS_MAGIC = b"DDS "
DDS_HEADER_LENGTH = 128

# offsets inside the 128 byte header (magic included)
_OFF_HEIGHT = 12
_OFF_WIDTH = 16
_OFF_LINEARSIZE = 20
_OFF_MIPS = 28
_OFF_FOURCC = 84


class DDSInfo(NamedTuple):
    width: int
    height: int
    fourcc: str
    mips: int
    header: bytes
    data: bytes


def blockSize(fourcc: str) -> int:
    if fourcc == "DXT1":
        return 8
    if fourcc in ("DXT3", "DXT5"):
        return 16
    raise ValueError(f"unsupported DDS format {fourcc!r}")


def dataSize(width: int, height: int, fourcc: str) -> int:
    return max(1, (width + 3) // 4) * max(1, (height + 3) // 4) * blockSize(fourcc)


def parseDDS(blob: bytes) -> DDSInfo:
    if len(blob) < DDS_HEADER_LENGTH or blob[:4] != DDS_MAGIC:
        raise ValueError("not a DDS image")

    height, width = struct.unpack_from("<II", blob, _OFF_HEIGHT)
    mips = struct.unpack_from("<I", blob, _OFF_MIPS)[0]
    fourcc = blob[_OFF_FOURCC:_OFF_FOURCC + 4].decode("ascii", "replace")

    return DDSInfo(width, height, fourcc, mips, blob[:DDS_HEADER_LENGTH], blob[DDS_HEADER_LENGTH:])


def withDimensions(header: bytes, width: int, height: int, fourcc: str) -> bytes:
    """returns a copy of a DDS header with new size (and matching linear size), everything else untouched"""
    h = bytearray(header)
    struct.pack_into("<II", h, _OFF_HEIGHT, height, width)
    struct.pack_into("<I", h, _OFF_LINEARSIZE, dataSize(width, height, fourcc))
    return bytes(h)


def _decodeAlphaBlock(block: bytes) -> list[int]:
    a0, a1 = block[0], block[1]
    bits = int.from_bytes(block[2:8], "little")

    if a0 > a1:
        palette = [a0, a1] + [((7 - k) * a0 + k * a1) // 7 for k in range(1, 7)]
    else:
        palette = [a0, a1] + [((5 - k) * a0 + k * a1) // 5 for k in range(1, 5)] + [0, 255]

    return [palette[(bits >> (3 * i)) & 7] for i in range(16)]


def dxt5ToDxt3(data: bytes) -> bytes:
    """
    converts DXT5 block data to DXT3: the colour block is identical in both formats, only the
    alpha part differs (8 bit interpolated -> explicit 4 bit)
    """
    if len(data) % 16:
        raise ValueError("DXT5 data length is not a multiple of 16")

    out = bytearray(len(data))

    for pos in range(0, len(data), 16):
        alpha = _decodeAlphaBlock(data[pos:pos + 8])

        for i in range(8):
            lo = (alpha[2 * i] + 8) // 17
            hi = (alpha[2 * i + 1] + 8) // 17
            out[pos + i] = lo | (hi << 4)

        out[pos + 8:pos + 16] = data[pos + 8:pos + 16]

    return bytes(out)


def hasAlpha(fourcc: str, data: bytes, width: int, height: int) -> bool:
    """True if any pixel inside the image (not the padding of the last blocks) is not fully opaque"""
    bw = max(1, (width + 3) // 4)
    size = blockSize(fourcc)

    def inside(block: int, i: int) -> bool:
        return (block % bw) * 4 + (i & 3) < width and (block // bw) * 4 + (i >> 2) < height

    for block, pos in enumerate(range(0, len(data) - size + 1, size)):
        if fourcc == "DXT1":
            c0, c1, bits = struct.unpack_from("<HHI", data, pos)
            if c0 <= c1:  # three colour mode, index 3 means transparent
                if any((bits >> (2 * i)) & 3 == 3 and inside(block, i) for i in range(16)):
                    return True
        elif fourcc == "DXT3":
            if any(((data[pos + (i >> 1)] >> (4 * (i & 1))) & 15) != 15 and inside(block, i) for i in range(16)):
                return True
        else:
            if any(a != 255 and inside(block, i) for i, a in enumerate(_decodeAlphaBlock(data[pos:pos + 8]))):
                return True

    return False


def _pack565(r: int, g: int, b: int) -> int:
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def _unpack565(c: int) -> tuple[int, int, int]:
    r, g, b = (c >> 11) & 31, (c >> 5) & 63, c & 31
    return (r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)


def _encodeDxt1Block(pixels: list[tuple[int, int, int, int]]) -> bytes:
    """simple bounding box encoder for one block, 3 colour mode + transparent if a pixel is transparent"""
    opaque = [p[:3] for p in pixels if p[3] >= 128]
    transparent = len(opaque) != len(pixels)

    if not opaque:
        return struct.pack("<HHI", 0, 0, 0xFFFFFFFF)

    lo = [min(p[c] for p in opaque) for c in range(3)]
    hi = [max(p[c] for p in opaque) for c in range(3)]
    for c in range(3):
        inset = (hi[c] - lo[c]) >> 4
        lo[c] += inset
        hi[c] -= inset

    c0, c1 = _pack565(*hi), _pack565(*lo)
    if transparent:
        if c0 > c1:
            c0, c1 = c1, c0
    elif c0 < c1:
        c0, c1 = c1, c0

    p0, p1 = _unpack565(c0), _unpack565(c1)
    if transparent or c0 == c1:
        palette = [p0, p1, tuple((a + b) // 2 for a, b in zip(p0, p1))]
    else:
        palette = [p0, p1, tuple((2 * a + b) // 3 for a, b in zip(p0, p1)), tuple((a + 2 * b) // 3 for a, b in zip(p0, p1))]

    bits = 0
    for i, p in enumerate(pixels):
        if p[3] < 128:
            idx = 3
        else:
            idx = min(range(len(palette)), key=lambda k: sum((p[c] - palette[k][c]) ** 2 for c in range(3)))
        bits |= idx << (2 * i)

    if c0 == c1 and not transparent:
        bits = 0

    return struct.pack("<HHI", c0, c1, bits)


def dxt1WithAlpha(rgba: bytes, width: int, height: int, opaqueDxt1: bytes) -> bytes:
    """
    DXT1 block data with 1 bit alpha. `rgba` are the raw 8 bit pixels (alpha < 128 = transparent),
    `opaqueDxt1` is a good encoding of the same colours without alpha (e.g. from ImageMagick). Blocks
    without transparent pixels are taken from it, blocks with transparent pixels are encoded again
    from their opaque pixels only.
    """
    bw, bh = max(1, (width + 3) // 4), max(1, (height + 3) // 4)
    if len(opaqueDxt1) < bw * bh * 8 or len(rgba) < width * height * 4:
        raise ValueError("pixel data too short")

    out = bytearray()
    for by in range(bh):
        for bx in range(bw):
            pixels = []
            for y in range(by * 4, by * 4 + 4):
                for x in range(bx * 4, bx * 4 + 4):
                    o = (min(y, height - 1) * width + min(x, width - 1)) * 4  # edge pixels fill small images
                    pixels.append((rgba[o], rgba[o + 1], rgba[o + 2], rgba[o + 3]))

            base = opaqueDxt1[(by * bw + bx) * 8:(by * bw + bx + 1) * 8]
            if any(p[3] < 128 for p in pixels):
                out += _encodeDxt1Block(pixels)
                continue

            c0, c1, bits = struct.unpack("<HHI", base)
            if c0 > c1:
                out += base
            elif c0 == c1:
                out += struct.pack("<HHI", c0, c1, 0)
            else:  # would be read as three colour mode with transparency
                out += _encodeDxt1Block(pixels)

    return bytes(out)


def withFourCC(blob: bytes, fourcc: str) -> bytes:
    """returns a DDS image with another FourCC (data has to be converted by the caller)"""
    return blob[:_OFF_FOURCC] + fourcc.encode("ascii") + blob[_OFF_FOURCC + 4:]
