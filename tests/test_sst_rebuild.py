#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tests with synthetic data only (run: python -m unittest discover -s tests, from the repository root).
No archive, no ImageMagick and no PyQt5 needed.
"""

import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from lib.SSA import patch  # noqa: E402
from lib.SSA.SSA import SSA, FileData, FileEntry, Header, Intermediate  # noqa: E402
from lib.SSTrebuild import dxt, rebuild, sstparts  # noqa: E402


def tga(width: int, height: int, bpp: int = 32, fill: int = 0x80, desc: int = 0x20) -> bytes:
    head = bytearray(18)
    head[2] = 2
    struct.pack_into("<HH", head, 12, width, height)
    head[16], head[17] = bpp, desc
    return bytes(head) + bytes([fill]) * (width * height * bpp // 8)


def sstHeader(revision: int, resolutions: int, tiles: int, width: int, height: int, code: int) -> bytes:
    return bytes([revision, resolutions, tiles, 0, 0, 0]) + struct.pack("<II", width, height) + bytes([code])


def tgaMultiRes(size: int, bpp: int = 32) -> bytes:
    """128 -> 1 like the real files: normal images, 2x2 image with 5 extra bytes, 1x1 image"""
    images = []
    s = size
    while s > 2:
        images.append(tga(s, s, bpp, fill=s % 251))
        s //= 2
    # tail as in the real files: 2x2 image + 5 extra bytes, 1x1 image + 1 byte
    tail = tga(2, 2, bpp, fill=7) + b"\x00" * 5 + tga(1, 1, bpp, fill=9) + b"\x00"
    assert len(tail) == {24: 57, 32: 62}[bpp], len(tail)
    resolutions = len(images) + 2
    return sstHeader(0, resolutions, 1, size, size, 0) + b"".join(i + b"\x00" for i in images) + tail


def ddsImage(width: int, height: int, fourcc: str = "DXT1", fill: int = 0x11) -> bytes:
    head = bytearray(128)
    head[:4] = b"DDS "
    struct.pack_into("<I", head, 4, 124)
    struct.pack_into("<I", head, 8, 0x81007)
    struct.pack_into("<II", head, 12, height, width)
    struct.pack_into("<I", head, 20, dxt.dataSize(width, height, fourcc))
    head[84:88] = fourcc.encode()
    return bytes(head) + bytes([fill]) * dxt.dataSize(width, height, fourcc)


def ddsSst(size: int, fourcc: str = "DXT1", code: int = 4) -> bytes:
    images, s = [], size
    while True:
        images.append(ddsImage(s, s, fourcc, fill=s % 200))
        if s == 1:
            break
        s //= 2
    return sstHeader(1, len(images), 1, size, size, code) + bytes([code]).join(images)


class DxtTest(unittest.TestCase):
    def test_dxt5_to_dxt3(self):
        color = bytes(range(100, 108))
        # a0 = 255, a1 = 0 (a0 > a1: eight steps); all indices 0 -> alpha 255 everywhere -> nibble 15
        opaque = bytes([255, 0]) + b"\x00" * 6
        out = dxt.dxt5ToDxt3(opaque + color)
        self.assertEqual(out[:8], b"\xff" * 8)
        self.assertEqual(out[8:], color)  # colour block is copied unchanged

        # all 16 indices 1 -> alpha 0 everywhere
        clear = bytes([255, 0]) + int("001" * 16, 2).to_bytes(6, "little")
        self.assertEqual(dxt.dxt5ToDxt3(clear + color)[:8], b"\x00" * 8)

        # index 4 is (4 * 255 + 3 * 0) / 7 = 145 -> 145 / 17 rounded = nibble 9
        mid = bytes([255, 0]) + int("100" * 16, 2).to_bytes(6, "little")
        self.assertEqual(dxt.dxt5ToDxt3(mid + color)[:8], b"\x99" * 8)

    def test_has_alpha(self):
        opaque = struct.pack("<HHI", 0xFFFF, 0x0000, 0)
        punch = struct.pack("<HHI", 0x0000, 0xFFFF, 0xFFFFFFFF)  # three colour mode, all index 3
        self.assertFalse(dxt.hasAlpha("DXT1", opaque, 4, 4))
        self.assertTrue(dxt.hasAlpha("DXT1", punch, 4, 4))
        self.assertFalse(dxt.hasAlpha("DXT3", b"\xff" * 8 + b"\x00" * 8, 4, 4))
        self.assertTrue(dxt.hasAlpha("DXT3", b"\xf0" + b"\xff" * 7 + b"\x00" * 8, 4, 4))
        self.assertFalse(dxt.hasAlpha("DXT5", bytes([255, 255]) + b"\x00" * 6 + b"\x00" * 8, 4, 4))

    def test_padding_pixels_are_ignored(self):
        # a 1x1 DXT1 image: pixel 0 is opaque, the other 15 (padding) are index 3 in three colour mode
        bits = 0xFFFFFFFC
        block = struct.pack("<HHI", 0x0000, 0xFFFF, bits)
        self.assertFalse(dxt.hasAlpha("DXT1", block, 1, 1))
        self.assertTrue(dxt.hasAlpha("DXT1", block, 4, 4))

    def test_dxt1_with_alpha_keeps_transparency(self):
        w = h = 4
        rgba = b"".join(bytes([200, 100, 50, 0 if x < 2 else 255]) for y in range(h) for x in range(w))
        base = struct.pack("<HHI", 0xFFFF, 0x0000, 0)
        data = dxt.dxt1WithAlpha(rgba, w, h, base)
        c0, c1, bits = struct.unpack("<HHI", data)
        self.assertLessEqual(c0, c1)
        for i in range(16):
            idx = (bits >> (2 * i)) & 3
            self.assertEqual(idx == 3, (i & 3) < 2)


class SstTest(unittest.TestCase):
    def check_roundtrip(self, data: bytes, kind: str):
        layout = sstparts.parseSst("x.sst", data)
        self.assertEqual(layout.kind, kind)
        self.assertEqual(sstparts.buildSst(layout, layout.images, 1), data)
        return layout

    def test_multires_tga(self):
        for bpp in (24, 32):
            layout = self.check_roundtrip(tgaMultiRes(32, bpp), sstparts.KIND_TGA_RES)
            self.assertEqual(len(layout.images), 4)  # 32, 16, 8, 4

    def test_tiles_and_single_tga(self):
        single = sstHeader(0, 1, 1, 8, 8, 0) + tga(8, 8)
        self.check_roundtrip(single, sstparts.KIND_TGA_SINGLE)

        tiles = sstHeader(0, 1, 2, 8, 8, 0) + tga(8, 8) + b"\x00" + tga(8, 8, fill=3)
        self.check_roundtrip(tiles, sstparts.KIND_TGA_TILES)

    def test_dds(self):
        for fourcc, code in (("DXT1", 4), ("DXT3", 7), ("DXT5", 9)):
            layout = self.check_roundtrip(ddsSst(16, fourcc, code), sstparts.KIND_DDS)
            self.assertEqual(len(layout.images), 5)  # 16 .. 1

    def test_broken_files_are_rejected(self):
        with self.assertRaises(sstparts.SstError):
            sstparts.parseSst("x.sst", tgaMultiRes(32)[:-3])
        with self.assertRaises(sstparts.SstError):
            sstparts.parseSst("x.sst", sstHeader(2, 1, 1, 4, 4, 0) + b"abc")

    def test_scale_2_adds_the_original_smallest_image(self):
        data = ddsSst(8, "DXT3", 7)
        layout = sstparts.parseSst("x.sst", data)
        doubled = [ddsImage(img_w * 2, img_h * 2, "DXT3", fill=0x55)
                   for img_w, img_h in (sstparts._imageSize(i) for i in layout.images)]
        new = sstparts.buildSst(layout, [sstparts.replaceDds(o, n, 2) for o, n in zip(layout.images, doubled)], 2)

        again = sstparts.parseSst("x.sst", new)
        self.assertEqual(again.resolutions, layout.resolutions + 1)
        self.assertEqual((again.width, again.height), (16, 16))
        self.assertEqual(new[14], 7)
        sizes = [sstparts._imageSize(i)[0] for i in again.images]
        self.assertEqual(sizes, [16, 8, 4, 2, 1])
        self.assertEqual(again.images[-1], layout.images[-1])  # original 1x1 image is untouched

    def test_scale_2_multires_tga_keeps_tail(self):
        data = tgaMultiRes(32)
        layout = sstparts.parseSst("x.sst", data)
        parts = [tga(i_w * 2, i_h * 2, 32, fill=0xAA, desc=0x28) for i_w, i_h in (sstparts._imageSize(i) for i in layout.images)]
        new = sstparts.buildSst(layout, [sstparts.replaceTga(o, p, 2) for o, p in zip(layout.images, parts)], 2)

        again = sstparts.parseSst("x.sst", new)
        self.assertEqual(again.tail, layout.tail)
        self.assertEqual(again.resolutions, layout.resolutions + 1)
        self.assertEqual([sstparts._imageSize(i)[0] for i in again.images], [64, 32, 16, 8, 4])
        self.assertEqual(again.images[-1], layout.images[-1])
        self.assertEqual(again.images[0][17], 0x20)  # descriptor of the original is kept

    def test_replace_tga_turns_rows_when_orientation_differs(self):
        original = tga(2, 2, 24, fill=0, desc=0x20)
        part = bytearray(tga(4, 4, 24, desc=0x00))
        row = 4 * 3
        for y in range(4):
            part[18 + y * row:18 + (y + 1) * row] = bytes([y]) * row
        out = sstparts.replaceTga(original, bytes(part), 2)
        self.assertEqual(out[17], 0x20)
        self.assertEqual(out[18], 3)  # first row of the top-left image is the last row of the bottom-left part
        self.assertEqual(out[-1], 0)

    def test_replace_rejects_wrong_parts(self):
        original = tga(4, 4)
        with self.assertRaises(sstparts.SstError):
            sstparts.replaceTga(original, tga(4, 4), 2)
        with self.assertRaises(sstparts.SstError):
            sstparts.replaceTga(original, tga(8, 8, 24), 2)
        with self.assertRaises(sstparts.SstError):
            sstparts.replaceDds(ddsImage(4, 4, "DXT3"), ddsImage(8, 8, "DXT5"), 2)


class SplitTest(unittest.TestCase):
    def test_part_names_are_found_again(self):
        import eestool

        for name, data in (("res.sst", tgaMultiRes(16)), ("dd.sst", ddsSst(8)),
                           ("one.sst", sstHeader(0, 1, 1, 8, 8, 0) + tga(8, 8)),
                           ("tiles.sst", sstHeader(0, 1, 2, 8, 8, 0) + tga(8, 8) + b"\x00" + tga(8, 8, fill=3))):
            layout = sstparts.parseSst(name, data)
            with tempfile.TemporaryDirectory() as tmp:
                for partName, image in zip(eestool._partNames(layout), layout.images):
                    with open(os.path.join(tmp, partName), "wb") as f:
                        f.write(image)
                result = rebuild.rebuildSst(name, data, sstparts.PartStore(tmp), 1)
                self.assertEqual(result.status, "ok", name)
                self.assertEqual(result.data, data, name)


class RebuildTest(unittest.TestCase):
    def test_parts_from_folder(self):
        data = tgaMultiRes(16)
        layout = sstparts.parseSst("my tex.sst", data)

        with tempfile.TemporaryDirectory() as tmp:
            for i, image in enumerate(layout.images):
                with open(os.path.join(tmp, f"my tex_{i + 1}-6_RES.tga"), "wb") as f:
                    f.write(image)
            store = sstparts.PartStore(tmp)
            result = rebuild.rebuildSst("my tex.sst", data, store, 1)
            self.assertEqual(result.status, "ok")
            self.assertEqual(result.data, data)

            os.remove(os.path.join(tmp, "my tex_2-6_RES.tga"))
            result = rebuild.rebuildSst("my tex.sst", data, sstparts.PartStore(tmp), 1)
            self.assertEqual(result.status, "skipped")
            self.assertIn("missing", result.reason)

    def test_ambiguous_names_are_skipped(self):
        files = [("a.b.sst", b""), ("a.sst", b"")]
        self.assertEqual(rebuild.ambiguousStems(n for n, _ in files), {"a"})


class PatchTest(unittest.TestCase):
    def make_archive(self, path: str, files: dict[str, bytes]):
        entries, blobs = [], []
        for name, data in files.items():
            entries.append(FileEntry(name.encode("iso-8859-15"), 0, 0, len(data)))
            blobs.append(FileData(data))
        ssa = SSA(Header(0), entries, Intermediate.fromTuples([("Timestamp", "1")], "iso-8859-15"), blobs)
        ssa.offsetCalculationNeeded = True
        ssa.assemble(path)

    def test_patch_keeps_everything_else(self):
        files = {"textures\\a.sst": b"aaaa", "db\\x.dat": b"xyz", "textures\\b.sst": b"bbbbbbbb"}
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = os.path.join(tmp, "in.ssa"), os.path.join(tmp, "out.ssa")
            self.make_archive(src, files)

            ssa = SSA.parseFile(src)
            ssa.offsetCalculationNeeded = True
            ssa.assemble(dst)
            with open(src, "rb") as a, open(dst, "rb") as b:
                self.assertEqual(a.read(), b.read())  # unchanged archive is identical

            replacements = {"Textures\\B.SST": b"new data that is longer", "textures\\missing.sst": b"?"}
            replaced, unknown = patch.patchArchive(ssa, replacements, compress=False)
            self.assertEqual(replaced, ["Textures\\B.SST"])
            self.assertEqual(unknown, ["textures\\missing.sst"])
            ssa.assemble(dst)

            self.assertEqual(patch.verifyArchive(dst, src, replacements, replaced), [])
            new = SSA.parseFile(dst)
            self.assertEqual(new.getFileList(), list(files))
            self.assertEqual([d.data for d in new.file_data], [b"aaaa", b"xyz", b"new data that is longer"])


if __name__ == "__main__":
    unittest.main()
