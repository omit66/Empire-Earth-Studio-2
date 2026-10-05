#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Command line tool (no GUI, no PyQt5) for rebuilding SST textures and patching SSA archives.

  python eestool.py ssa-extract   <data.ssa> --out <folder> [--folder textures]
  python eestool.py sst-split     <data.ssa> --out <parts folder> [--sst-list names.txt]
  python eestool.py ssa-selftest  <data.ssa>
  python eestool.py sst-selftest  <data.ssa> --parts <original parts folder>
  python eestool.py preview       --orig <in> --out <folder> --model <model> [--model <model2>] --esrgan <exe>
  python eestool.py fix-parts     --orig <in> --upscaled <out> --fixed <fixed> [--dry-run]
  python eestool.py sst-rebuild   <data.ssa> --parts <out> [--parts <fixed>] --out-dir <new sst folder>
  python eestool.py ssa-patch     <data.ssa> <folder with textures\\*.sst> -o <new.ssa>

The DCL library (libDCL.dll / libDCL.so) is searched in lib/DCL and in $EES_LIBDCL.
"""

import argparse
import collections
import fnmatch
import hashlib
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.SSA.SSA import SSA  # noqa: E402
from lib.SSA import patch  # noqa: E402
from lib.SSTrebuild import preview, rebuild, sstparts, texfix, upscale  # noqa: E402


def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _textureSsts(ssa: SSA) -> list[tuple[str, bytes]]:
    """(file name, decompressed content) of all textures\\*.sst in an archive"""
    result = []
    for entry, data in zip(ssa.file_index, ssa.file_data):
        path = entry.getPath(ssa.encoding)
        folder, name = os.path.split(path)
        if folder.lower() == "textures" and name.lower().endswith(".sst"):
            result.append((name, data.getDecompressedData()))
    return result


def _readList(path: str) -> set[str]:
    with open(path, encoding="utf-8") as f:
        return {line.strip().lower() for line in f if line.strip()}


def cmdSsaExtract(args) -> int:
    """writes all files of an archive (decompressed) to a folder, keeping the folder structure"""
    ssa = SSA.parseFile(args.ssa)
    count = 0
    for entry, data in zip(ssa.file_index, ssa.file_data):
        path = entry.getPath(ssa.encoding)
        if args.folder and path.split(os.sep)[0].lower() != args.folder.lower():
            continue
        target = os.path.join(args.out, path)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as f:
            f.write(data.data if args.raw else data.getDecompressedData())
        count += 1
    print(f"{count} file(s) written to {args.out}")
    return 0


def _partNames(layout: sstparts.SstLayout) -> list[str]:
    stem, n = sstparts.sstStem(layout.name), len(layout.images)
    ext = ".dds" if layout.kind == sstparts.KIND_DDS else ".tga"
    if layout.kind == sstparts.KIND_TGA_SINGLE:
        return [stem + ext]
    suffix = "" if layout.kind == sstparts.KIND_TGA_TILES else "_RES"
    return [f"{stem}_{i + 1}-{n}{suffix}{ext}" for i in range(n)]


def cmdSstSplit(args) -> int:
    """splits the texture SSTs of an archive into one TGA / DDS file per resolution or tile"""
    ssa = SSA.parseFile(args.ssa)
    only = _readList(args.sst_list) if args.sst_list else None
    files = _textureSsts(ssa)
    ambiguous = rebuild.ambiguousStems(n for n, _ in files)
    os.makedirs(args.out, exist_ok=True)

    written, skipped = 0, collections.Counter()
    for name, data in files:
        if only is not None and name.lower() not in only:
            continue
        if sstparts.sstStem(name) in ambiguous:
            skipped["part name is not unique"] += 1
            continue
        try:
            layout = sstparts.parseSst(name, data)
        except sstparts.SstError as e:
            skipped[str(e)] += 1
            continue
        for partName, image in zip(_partNames(layout), layout.images):
            with open(os.path.join(args.out, partName), "wb") as f:
                f.write(image)
            written += 1

    print(f"{written} part(s) written to {args.out}")
    for reason, count in skipped.items():
        print(f"  skipped {count}x: {reason}")
    return 0


def cmdSsaSelftest(args) -> int:
    src = os.path.abspath(args.ssa)
    tmp = os.path.abspath(args.tmp or src + ".selftest.tmp")
    if tmp == src:
        print("temporary file must differ from the archive")
        return 2

    ssa = SSA.parseFile(src)
    print(f"{len(ssa.file_index)} files, metadata {ssa.getMetadata()}")

    ssa.assemble(tmp)
    same = _sha(src) == _sha(tmp)
    print("parse -> assemble:", "identical" if same else "DIFFERENT")

    ssa.offsetCalculationNeeded = True
    ssa.assemble(tmp)
    same2 = _sha(src) == _sha(tmp)
    print("parse -> recalculate offsets -> assemble:", "identical" if same2 else "DIFFERENT")

    os.remove(tmp)
    return 0 if same and same2 else 1


def cmdSstSelftest(args) -> int:
    ssa = SSA.parseFile(args.ssa)
    store = sstparts.PartStore(*args.parts)
    different, skipped = rebuild.selfTest(_textureSsts(ssa), store)
    for name in different[:20]:
        print("  different:", name)
    for result in skipped[:20]:
        print(f"  skipped: {result.name}: {result.reason}")
    return 0 if not different else 1


def cmdFixParts(args) -> int:
    actions = texfix.fixAll(args.orig, args.upscaled, args.fixed, magick=args.magick, workers=args.workers, dryRun=args.dry_run, factor=args.factor)
    counts = collections.Counter((a.action, a.detail) for a in actions)
    for (action, detail), count in sorted(counts.items()):
        print(f"{count:6d} x {action} {detail}")
    problems = [a for a in actions if a.action == "problem"]
    for a in problems[:50]:
        print(f"  PROBLEM {a.name}: {a.detail}")
    if args.dry_run:
        print("dry run, nothing written")
    return 1 if problems else 0


def _validPart(path: str) -> bool:
    """the slicer of EE Studio writes garbage parts for the smallest images of some SSTs, skip those"""
    if not path.lower().endswith(".tga"):
        return True
    with open(path, "rb") as f:
        h = f.read(18)
    return len(h) == 18 and h[2] == 2 and struct.unpack_from("<H", h, 12)[0] > 0 and h[16] in (24, 32)


def _stemOfPart(filename: str) -> str:
    m = sstparts._PART_MULTI.match(filename) or sstparts._PART_SINGLE.match(filename)
    return m["stem"].lower() if m else filename.lower()


def cmdUpscale(args) -> int:
    names = sorted(n for n in os.listdir(args.orig) if n.lower().endswith((".tga", ".dds")))
    if args.names:
        wanted = _readList(args.names)
        names = [n for n in names if n.lower() in wanted]
    if args.sst_list:
        stems = {sstparts.sstStem(n) for n in _readList(args.sst_list)}
        names = [n for n in names if _stemOfPart(n) in stems]
    names = [n for n in names if _validPart(os.path.join(args.orig, n))]
    failed = upscale.upscaleParts(args.orig, args.out, names, args.factor, args.model, args.esrgan,
                                  magick=args.magick, batch=args.batch, workers=args.workers, plain=args.plain)
    print(f"{len(failed)} part(s) failed")
    for name in failed[:50]:
        print("  ", name)
    return 1 if failed else 0


def cmdPreview(args) -> int:
    names = sorted(n for n in os.listdir(args.orig) if n.lower().endswith((".tga", ".dds")))
    if args.sst_list:
        stems = {sstparts.sstStem(n) for n in _readList(args.sst_list)}
        names = [n for n in names if _stemOfPart(n) in stems]
    names = [n for n in names if _validPart(os.path.join(args.orig, n))]
    if args.match:
        names = [n for n in names if args.match.lower() in n.lower()]

    sample = preview.pickSample(args.orig, names, args.count, args.min_size, args.max_size, args.seed)
    if not sample:
        print("no suitable parts found")
        return 1
    print(f"{len(sample)} part(s) in the sample")

    sheets = preview.makePreview(args.orig, args.out, sample, args.model, args.factor, args.esrgan,
                                 magick=args.magick, plain=args.plain)
    for sheet in sheets:
        print("  ", sheet)
    return 0


def cmdSstRebuild(args) -> int:
    ssa = SSA.parseFile(args.ssa)
    store = sstparts.PartStore(*args.parts)
    excluded = args.exclude or []
    only = None
    if args.only:
        with open(args.only, encoding="utf-8") as f:
            only = {line.strip().lower() for line in f if line.strip()}

    def include(name: str) -> bool:
        if only is not None and name.lower() not in only:
            return False
        return not any(fnmatch.fnmatch(name.lower(), pattern.lower()) for pattern in excluded)

    results = rebuild.rebuildAll(_textureSsts(ssa), store, args.scale, include=include)

    target = os.path.join(args.out_dir, "textures")
    os.makedirs(target, exist_ok=True)
    counts = collections.Counter()
    report = []
    for r in results:
        counts[r.status] += 1
        if r.status == "ok":
            with open(os.path.join(target, r.name), "wb") as f:
                f.write(r.data)
        elif r.status == "skipped":
            report.append(f"{r.name}\t{r.reason}")

    with open(os.path.join(args.out_dir, "skipped.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(report))

    print(dict(counts))
    reasons = collections.Counter(line.split("\t")[1].split(":")[0].split("(")[0] for line in report)
    for reason, count in reasons.most_common():
        print(f"  skipped {count}x: {reason}")
    print(f"details: {os.path.join(args.out_dir, 'skipped.txt')}")
    return 0


def cmdSsaPatch(args) -> int:
    src, dst = os.path.abspath(args.ssa), os.path.abspath(args.output)
    if src == dst:
        print("the output must not be the input archive")
        return 2
    if os.path.exists(dst) and not args.force:
        print(f"{dst} exists already (use --force to overwrite it)")
        return 2

    files = patch.readFolder(args.folder)
    print(f"{len(files)} replacement file(s) in {args.folder}")
    replacements = {}
    for path, filePath in files.items():
        with open(filePath, "rb") as f:
            replacements[path] = f.read()

    ssa = SSA.parseFile(src)
    replaced, unknown = patch.patchArchive(
        ssa, replacements, compress=not args.no_compress,
        progress=lambda n, total, path: print(f"  {n}/{total}") if n % 250 == 0 else None)
    if unknown:
        print(f"{len(unknown)} file(s) are not in the archive and were ignored, e.g. {unknown[:3]}")

    ssa.assemble(dst)
    print(f"written {dst} ({os.path.getsize(dst)} bytes), {len(replaced)} file(s) replaced")

    problems = patch.verifyArchive(dst, src, replacements, replaced)
    for p in problems[:20]:
        print("  PROBLEM:", p)
    print("verification:", "OK" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("ssa-extract", help="write the (decompressed) files of an archive to a folder")
    p.add_argument("ssa")
    p.add_argument("--out", required=True)
    p.add_argument("--folder", help="only this archive folder, e.g. textures")
    p.add_argument("--raw", action="store_true", help="keep compressed files compressed")
    p.set_defaults(func=cmdSsaExtract)

    p = sub.add_parser("sst-split", help="split texture SSTs into TGA / DDS parts (one per resolution or tile)")
    p.add_argument("ssa")
    p.add_argument("--out", required=True)
    p.add_argument("--sst-list", help="text file with SST names, everything else is skipped")
    p.set_defaults(func=cmdSstSplit)

    p = sub.add_parser("ssa-selftest", help="parse -> assemble must give the same file")
    p.add_argument("ssa")
    p.add_argument("--tmp")
    p.set_defaults(func=cmdSsaSelftest)

    p = sub.add_parser("sst-selftest", help="rebuild all SSTs from the ORIGINAL parts, result must be identical")
    p.add_argument("ssa")
    p.add_argument("--parts", nargs="+", required=True)
    p.set_defaults(func=cmdSstSelftest)

    p = sub.add_parser("fix-parts", help="restore alpha channels, convert DXT5 back to DXT3")
    p.add_argument("--orig", required=True, help="folder with the original parts (in)")
    p.add_argument("--upscaled", required=True, help="folder with the upscaled parts (out)")
    p.add_argument("--fixed", required=True, help="folder for the repaired parts")
    p.add_argument("--magick", default="magick")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--factor", type=int, default=2, choices=(1, 2), help="size of the upscaled parts relative to the originals")
    p.set_defaults(func=cmdFixParts)

    p = sub.add_parser("upscale", help="upscale part files with Real-ESRGAN (continues an interrupted run)")
    p.add_argument("--orig", required=True, help="folder with the original parts (in)")
    p.add_argument("--out", required=True)
    p.add_argument("--names", help="text file with part file names to process (default: all)")
    p.add_argument("--sst-list", help="text file with SST names: only their parts are processed")
    p.add_argument("--factor", type=int, default=2, choices=(1, 2))
    p.add_argument("--model", default="realesr-animevideov3-x4")
    p.add_argument("--plain", action="store_true", help="plain Lanczos resize, no AI (for normal / bump maps)")
    p.add_argument("--esrgan", required=True, help="path of realesrgan-ncnn-vulkan.exe (models folder next to it)")
    p.add_argument("--magick", default="magick")
    p.add_argument("--batch", type=int, default=200)
    p.add_argument("--workers", type=int, default=4)
    p.set_defaults(func=cmdUpscale)

    p = sub.add_parser("preview", help="compare a random sample of parts: original and one column per model")
    p.add_argument("--orig", required=True, help="folder with the original parts (in)")
    p.add_argument("--out", required=True, help="folder for the comparison pictures")
    p.add_argument("--model", action="append", required=True, help="Real-ESRGAN model, can be given several times")
    p.add_argument("--esrgan", required=True)
    p.add_argument("--sst-list", help="only parts of these SSTs")
    p.add_argument("--count", type=int, default=30)
    p.add_argument("--factor", type=int, default=2, choices=(1, 2))
    p.add_argument("--min-size", type=int, default=64, help="smallest edge length of a sampled part")
    p.add_argument("--max-size", type=int, default=512)
    p.add_argument("--seed", type=int, default=1, help="same seed = same sample")
    p.add_argument("--plain", action="store_true", help="add a column with a plain resize (no AI)")
    p.add_argument("--match", help="only parts whose name contains this text, e.g. _bm")
    p.add_argument("--magick", default="magick")
    p.set_defaults(func=cmdPreview)

    p = sub.add_parser("sst-rebuild", help="build new SSTs from (upscaled) parts")
    p.add_argument("ssa")
    p.add_argument("--parts", nargs="+", required=True, help="part folders, earlier ones win (e.g. fixed out)")
    p.add_argument("--out-dir", required=True)
    p.add_argument("--scale", type=int, default=2, choices=(1, 2))
    p.add_argument("--exclude", nargs="*", help="SST names to leave alone (wildcards, e.g. cur_*)")
    p.add_argument("--only", help="text file with SST names (one per line), everything else is left alone")
    p.set_defaults(func=cmdSstRebuild)

    p = sub.add_parser("ssa-patch", help="replace files of an archive and write a new archive")
    p.add_argument("ssa")
    p.add_argument("folder", help="folder with sub folders as in the archive (e.g. textures\\*.sst)")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--no-compress", action="store_true", help="store replaced files uncompressed")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmdSsaPatch)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
