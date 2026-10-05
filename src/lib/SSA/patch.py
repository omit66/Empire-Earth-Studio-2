#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Replaces files inside an SSA archive and keeps everything else (order of the index, metadata,
all other files byte for byte) as it is.
"""

import os
from typing import Callable, Optional

from lib.DCL import DCL
from lib.SSA.SSA import SSA, FileData


def _key(path: str) -> str:
    return path.replace("/", "\\").lower()


def compressData(data: bytes) -> bytes:
    """DCL (PK01) container as used by the game archives"""
    dcl = DCL(len(data), data)
    dcl.compress()
    return dcl.getRawData()


def readFolder(folder: str) -> dict[str, str]:
    """archive path (folder\\file) -> file path, for a folder with one level of sub folders"""
    files: dict[str, str] = {}
    for sub in sorted(os.listdir(folder)):
        subPath = os.path.join(folder, sub)
        if not os.path.isdir(subPath):
            continue
        for name in sorted(os.listdir(subPath)):
            if os.path.isfile(os.path.join(subPath, name)):
                files[f"{sub}\\{name}"] = os.path.join(subPath, name)
    return files


def patchArchive(ssa: SSA, replacements: dict[str, bytes], compress: bool = True,
                 progress: Optional[Callable[[int, int, str], None]] = None) -> tuple[list[str], list[str]]:
    """
    replaces files in `ssa` (in memory). `replacements` maps archive paths to the new, decompressed content.
    Files that were compressed in the archive are compressed again when `compress` is set.
    returns (replaced paths, paths that are not part of the archive)
    """
    index = {_key(e.getPath(ssa.encoding)): i for i, e in enumerate(ssa.file_index)}
    replaced: list[str] = []
    unknown: list[str] = []

    for n, (path, data) in enumerate(replacements.items()):
        i = index.get(_key(path))
        if i is None:
            unknown.append(path)
            continue

        wasCompressed = ssa.file_data[i].isCompressed()
        blob = compressData(data) if (compress and wasCompressed) else data

        ssa.file_data[i] = FileData(blob)
        ssa.file_index[i].size = len(blob)
        replaced.append(path)

        if progress:
            progress(n + 1, len(replacements), path)

    ssa.offsetCalculationNeeded = True
    return replaced, unknown


def verifyArchive(path: str, originalPath: str, replacements: dict[str, bytes], replaced: list[str]) -> list[str]:
    """
    reads the written archive again and checks: same files in the same order, replaced files decompress to
    the new content, all other files are byte for byte the same as before. returns a list of problems.
    """
    problems: list[str] = []
    original = SSA.parseFile(originalPath)
    new = SSA.parseFile(path)

    if new.getFileList() != original.getFileList():
        return ["file list differs from the original archive"]
    if new.getMetadata() != original.getMetadata():
        problems.append("metadata differs")

    replacedKeys = {_key(p) for p in replaced}
    lookup = {_key(k): v for k, v in replacements.items()}

    for entry, oldData, newData in zip(new.file_index, original.file_data, new.file_data):
        name = entry.getPath(new.encoding)
        if _key(name) in replacedKeys:
            if newData.getDecompressedData() != lookup[_key(name)]:
                problems.append(f"{name}: content differs after writing")
            if newData.isCompressed() != oldData.isCompressed():
                problems.append(f"{name}: compression changed")
        elif newData.data != oldData.data:
            problems.append(f"{name}: changed although it was not replaced")

    return problems
