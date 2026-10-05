#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Small binary helpers without any GUI dependency, so that SSA/DCL/SST code
can be used from command line tools without PyQt5 installed.
"""

from io import BufferedReader, BufferedWriter


def readInt(f: BufferedReader) -> int:
    return int.from_bytes(f.read(4), byteorder="little", signed=False)

def writeInt(f: BufferedWriter, value: int) -> int:
    return f.write(value.to_bytes(4, byteorder="little", signed=False))

def checkNullTerminator(data: bytes) -> bytes:
    if not data.endswith(b"\0"):
        data += b"\0"
    return data
