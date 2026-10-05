#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Splits Empire Earth / Empires SST files into their images and builds them again from
(replaced) image files.

SST layout as found in the Empires: Dawn of the Modern World archive (verified against
all 2875 texture entries of data.ssa):

  15 byte header
    0      revision, 0 = TGA images, 1 = DDS images
    1      number of resolutions (mip levels, biggest first)
    2      number of tiles
    3-5    zero
    6-9    width of the biggest image
    10-13  height of the biggest image
    14     revision 0: 0 (3 = JFIF, not supported here)
           revision 1: format code, 4 = DXT1, 5 = DXT1 with 1 bit alpha, 7 = DXT3, 9 = DXT5
  body: the images one after another, separated by one byte (revision 0: 0, revision 1: the format
        code from byte 14 again), no separator after the last one.

  Revision 0 with more than one resolution has an irregularity: the 2x2 image is stored with 5
  extra bytes after its pixels (instead of one). That 2x2 image together with the 1x1 image behind
  it is kept as an opaque "tail" blob, it is never changed.
"""

import os
import re
import struct
from dataclasses import dataclass, field
from typing import Optional

from . import dxt

SST_HEADER_LENGTH = 15
TGA_HEADER_LENGTH = 18
TGA_TAIL_LENGTH = {24: 57, 32: 62}  # 2x2 image (+5 extra bytes) and 1x1 image

KIND_TGA_RES = "tga-res"
KIND_TGA_TILES = "tga-tiles"
KIND_TGA_SINGLE = "tga-single"
KIND_DDS = "dds"


class SstError(Exception):
    pass


@dataclass
class SstLayout:
    name: str
    header: bytes
    kind: str
    images: list[bytes] = field(default_factory=list)  # image bytes without the separating 0 byte
    tail: bytes = b""

    @property
    def resolutions(self) -> int:
        return self.header[1]

    @property
    def tiles(self) -> int:
        return self.header[2]

    @property
    def width(self) -> int:
        return struct.unpack_from("<I", self.header, 6)[0]

    @property
    def height(self) -> int:
        return struct.unpack_from("<I", self.header, 10)[0]


def _tgaInfo(blob: bytes, offset: int = 0) -> tuple[int, int, int, int]:
    """type, width, height, bits per pixel"""
    if len(blob) - offset < TGA_HEADER_LENGTH:
        raise SstError("truncated TGA header")
    tgaType = blob[offset + 2]
    width, height = struct.unpack_from("<HH", blob, offset + 12)
    return tgaType, width, height, blob[offset + 16]


def _tgaImageLength(blob: bytes, offset: int) -> int:
    tgaType, width, height, bpp = _tgaInfo(blob, offset)
    if tgaType != 2 or bpp not in (24, 32) or width == 0 or height == 0 or blob[offset] != 0 or blob[offset + 1] != 0:
        raise SstError(f"unsupported TGA image at offset {offset}")
    return TGA_HEADER_LENGTH + width * height * bpp // 8


def parseSst(name: str, data: bytes) -> SstLayout:
    """splits an (already decompressed) SST file, raises SstError for everything unusual"""
    if len(data) < SST_HEADER_LENGTH:
        raise SstError("file too short")

    header = data[:SST_HEADER_LENGTH]
    revision, resolutions, tiles = header[0], header[1], header[2]
    body = data[SST_HEADER_LENGTH:]

    if resolutions < 1 or tiles < 1:
        raise SstError("no images according to header")
    if resolutions > 1 and tiles > 1:
        raise SstError("resolutions and tiles at once are not supported")

    if revision == 0 and header[14] == 0:
        images: list[bytes] = []
        pos = 0

        if resolutions > 1:
            # all images until the 2x2 image, which starts the irregular tail
            while True:
                tgaType, width, height, bpp = _tgaInfo(body, pos)
                if width <= 2 and height <= 2:
                    break
                length = _tgaImageLength(body, pos)
                images.append(body[pos:pos + length])
                pos += length + 1

            tail = body[pos:]
            if bpp not in TGA_TAIL_LENGTH or len(tail) != TGA_TAIL_LENGTH[bpp] or (width, height) != (2, 2):
                raise SstError("unexpected structure of the small images at the end")
            if len(images) + 2 != resolutions:
                raise SstError(f"header says {resolutions} resolutions, found {len(images) + 2}")

            _checkChain(images, header)
            return SstLayout(name, header, KIND_TGA_RES, images, tail)

        while pos < len(body):
            length = _tgaImageLength(body, pos)
            images.append(body[pos:pos + length])
            pos += length + 1

        if pos not in (len(body), len(body) + 1):  # the last image has no 0 byte behind it
            raise SstError("unexpected data after the last image")
        if len(images) != tiles:
            raise SstError(f"header says {tiles} tiles, found {len(images)}")

        return SstLayout(name, header, KIND_TGA_TILES if tiles > 1 else KIND_TGA_SINGLE, images)

    if revision == 1 and header[14] in (4, 5, 7, 9):
        magic = dxt.DDS_MAGIC + b"\x7c\x00\x00\x00"
        starts: list[int] = []
        pos = body.find(magic)
        while pos >= 0:
            starts.append(pos)
            pos = body.find(magic, pos + 1)

        if not starts or starts[0] != 0:
            raise SstError("body does not start with a DDS image")
        if len(starts) != resolutions * tiles:
            raise SstError(f"header says {resolutions * tiles} images, found {len(starts)}")

        starts.append(len(body) + 1)  # so that the last image has no 0 byte behind it, too
        images = []
        for begin, end in zip(starts, starts[1:]):
            blob = body[begin:end - 1]
            info = dxt.parseDDS(blob)
            if len(info.data) != dxt.dataSize(info.width, info.height, info.fourcc):
                raise SstError("size of a DDS image does not match its header")
            images.append(blob)

        # every image must be followed by exactly one 0 byte
        if any(body[end - 1] != header[14] for end in starts[1:-1]):
            raise SstError("unexpected separator between DDS images")

        _checkChain(images, header)
        return SstLayout(name, header, KIND_DDS, images)

    raise SstError(f"unsupported SST type (revision {revision}, format byte {header[14]})")


def _imageSize(image: bytes) -> tuple[int, int]:
    if image[:4] == dxt.DDS_MAGIC:
        info = dxt.parseDDS(image)
        return info.width, info.height
    _, width, height, _ = _tgaInfo(image)
    return width, height


def _checkChain(images: list[bytes], header: bytes) -> None:
    """every image has to be half the size of the previous one and the first one has to match the header"""
    sizes = [_imageSize(i) for i in images]
    if sizes[0] != struct.unpack_from("<II", header, 6):
        raise SstError("size of the first image does not match the header")
    for (w0, h0), (w1, h1) in zip(sizes, sizes[1:]):
        if (w1, h1) != (max(1, w0 // 2), max(1, h0 // 2)):
            raise SstError("resolutions are not halved from image to image")


# ----------------------------------------------------------------------------------------------
# part files (the images as single TGA / DDS files, named like EE Studio does it)
# ----------------------------------------------------------------------------------------------

_PART_MULTI = re.compile(r"^(?P<stem>.+)_(?P<i>\d+)-(?P<n>\d+)(?P<res>_RES)?\.(?P<ext>tga|dds)$", re.IGNORECASE)
_PART_SINGLE = re.compile(r"^(?P<stem>.+)\.(?P<ext>tga|dds)$", re.IGNORECASE)


def sstStem(filename: str) -> str:
    """name of an SST without extension, cut at the first dot exactly like EE Studio names its parts"""
    return os.path.basename(filename).split(".")[0]


class PartStore:
    """finds part files in one or more folders, earlier folders win"""

    def __init__(self, *folders: str):
        self.multi: dict[tuple[str, int, bool], str] = {}
        self.single: dict[str, str] = {}
        self.stems: set[str] = set()

        for folder in reversed(folders):  # later insertions win -> reverse order
            for entry in os.listdir(folder):
                m = _PART_MULTI.match(entry)
                path = os.path.join(folder, entry)
                if m:
                    self.multi[(m["stem"], int(m["i"]), bool(m["res"]))] = path
                    self.stems.add(m["stem"])
                    continue
                m = _PART_SINGLE.match(entry)
                if m:
                    self.single[m["stem"]] = path
                    self.stems.add(m["stem"])

    def find(self, layout: SstLayout, index: int) -> Optional[str]:
        """path of the part for image `index` (0 based) of an SST or None"""
        stem = sstStem(layout.name)
        if layout.kind == KIND_TGA_SINGLE:
            return self.single.get(stem)
        return self.multi.get((stem, index + 1, layout.kind != KIND_TGA_TILES))


# ----------------------------------------------------------------------------------------------
# building
# ----------------------------------------------------------------------------------------------

def replaceTga(original: bytes, part: bytes, scale: int) -> bytes:
    """
    new TGA image: header of the original (size changed), pixels of `part`.
    keeps orientation flag and the rest of the descriptor of the original.
    """
    _, ow, oh, obpp = _tgaInfo(original)
    ptype, pw, ph, pbpp = _tgaInfo(part)

    if ptype != 2 or part[0] != 0 or part[1] != 0:
        raise SstError(f"part is not an uncompressed truecolor TGA (type {ptype})")
    if pbpp != obpp:
        raise SstError(f"part has {pbpp} bit, original {obpp} bit")
    if (pw, ph) != (ow * scale, oh * scale):
        raise SstError(f"part is {pw}x{ph}, expected {ow * scale}x{oh * scale}")

    length = pw * ph * pbpp // 8
    pixels = part[TGA_HEADER_LENGTH:TGA_HEADER_LENGTH + length]
    if len(pixels) != length:
        raise SstError("part is truncated")

    if (part[17] & 0x20) != (original[17] & 0x20):  # other row order: turn the rows around
        row = pw * pbpp // 8
        pixels = b"".join(pixels[r * row:(r + 1) * row] for r in range(ph - 1, -1, -1))

    head = bytearray(original[:TGA_HEADER_LENGTH])
    struct.pack_into("<HH", head, 12, pw, ph)
    return bytes(head) + pixels


def replaceDds(original: bytes, part: bytes, scale: int) -> bytes:
    old = dxt.parseDDS(original)
    new = dxt.parseDDS(part)

    if new.fourcc != old.fourcc:
        raise SstError(f"part is {new.fourcc}, original {old.fourcc}")
    if (new.width, new.height) != (old.width * scale, old.height * scale):
        raise SstError(f"part is {new.width}x{new.height}, expected {old.width * scale}x{old.height * scale}")

    size = dxt.dataSize(new.width, new.height, new.fourcc)
    if len(new.data) < size:
        raise SstError("part is truncated")

    return dxt.withDimensions(old.header, new.width, new.height, new.fourcc) + new.data[:size]


def buildSst(layout: SstLayout, images: list[bytes], scale: int) -> bytes:
    """
    assembles an SST from new images (one for every image of the layout, same order).
    scale 1 keeps everything as it is, scale 2 doubles the size of all images and adds one
    image to the resolution chain (the original smallest one) so it still ends at 1x1.
    """
    if len(images) != len(layout.images):
        raise SstError("number of images does not match")

    header = bytearray(layout.header)
    struct.pack_into("<II", header, 6, layout.width * scale, layout.height * scale)

    chain = list(images)
    tail = layout.tail

    if scale > 1 and layout.kind in (KIND_TGA_RES, KIND_DDS):
        chain.append(layout.images[-1])
        header[1] += 1

    if layout.kind == KIND_TGA_RES:
        body = b"".join(image + b"\x00" for image in chain) + tail
    else:
        body = (bytes([layout.header[14]]) if layout.kind == KIND_DDS else b"\x00").join(chain)

    return bytes(header) + body
