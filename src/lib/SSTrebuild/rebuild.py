#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Rebuilds SST files from replaced part files (see sstparts.py).
"""

import collections
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

from . import sstparts
from .sstparts import PartStore, SstError


@dataclass
class RebuildResult:
    name: str
    status: str  # "ok", "unchanged", "skipped"
    reason: str = ""
    data: Optional[bytes] = None


def rebuildSst(name: str, original: bytes, store: PartStore, scale: int) -> RebuildResult:
    """builds a new version of one decompressed SST from the parts in `store`"""
    try:
        layout = sstparts.parseSst(name, original)
    except SstError as e:
        return RebuildResult(name, "skipped", f"cannot read original: {e}")

    images: list[bytes] = []
    missing: list[int] = []
    for index, image in enumerate(layout.images):
        path = store.find(layout, index)
        if path is None:
            missing.append(index + 1)
            continue

        try:
            with open(path, "rb") as f:
                part = f.read()
            if layout.kind == sstparts.KIND_DDS:
                images.append(sstparts.replaceDds(image, part, scale))
            else:
                images.append(sstparts.replaceTga(image, part, scale))
        except (SstError, ValueError) as e:
            return RebuildResult(name, "skipped", f"image {index + 1}: {e}")

    if missing:
        return RebuildResult(name, "skipped", f"missing part(s) {missing} of {len(layout.images)}")

    return RebuildResult(name, "ok", data=sstparts.buildSst(layout, images, scale))


def ambiguousStems(names: Iterable[str]) -> set[str]:
    """part names are cut at the first dot, so different SSTs can share one part name"""
    counts = collections.Counter(sstparts.sstStem(n) for n in names)
    return {stem for stem, count in counts.items() if count > 1}


def rebuildAll(files: Iterable[tuple[str, bytes]], store: PartStore, scale: int,
               include: Optional[Callable[[str], bool]] = None,
               progress: Optional[Callable[[int, str], None]] = None) -> list[RebuildResult]:
    results: list[RebuildResult] = []
    files = list(files)
    ambiguous = ambiguousStems(n for n, _ in files)

    for i, (name, data) in enumerate(files):
        if include is not None and not include(name):
            results.append(RebuildResult(name, "skipped", "excluded"))
            continue
        if sstparts.sstStem(name) in ambiguous:
            results.append(RebuildResult(name, "skipped", "part name is not unique"))
            continue
        if sstparts.sstStem(name) not in store.stems:
            results.append(RebuildResult(name, "unchanged", "no parts"))
            continue

        results.append(rebuildSst(name, data, store, scale))
        if progress:
            progress(i, name)

    return results


def selfTest(files: Iterable[tuple[str, bytes]], store: PartStore) -> tuple[list[str], list[RebuildResult]]:
    """
    rebuilds every SST at scale 1 from the given parts (which have to be the original, not upscaled
    parts) and compares it with the original. returns (differing names, skipped results)
    """
    different: list[str] = []
    skipped: list[RebuildResult] = []
    identical = 0
    files = list(files)
    ambiguous = ambiguousStems(n for n, _ in files)

    for name, data in files:
        if sstparts.sstStem(name) not in store.stems:
            continue
        if sstparts.sstStem(name) in ambiguous:
            skipped.append(RebuildResult(name, "skipped", "part name is not unique"))
            continue
        result = rebuildSst(name, data, store, 1)
        if result.status != "ok":
            skipped.append(result)
        elif result.data == data:
            identical += 1
        else:
            different.append(name)

    print(f"identical: {identical}, different: {len(different)}, skipped: {len(skipped)}")
    reasons = collections.Counter(r.reason.split(":")[0] if r.reason else "?" for r in skipped)
    for reason, count in reasons.most_common():
        print(f"  skipped ({count}x): {reason}")

    return different, skipped
