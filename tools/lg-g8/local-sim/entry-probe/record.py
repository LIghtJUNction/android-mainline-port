#!/usr/bin/env python3
"""Independent ecc=0 persistent RAM codec and fixed-size probe records."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import zlib

SIGNATURE = 0x43474244
CONSOLE = 0xB0000000
ZONE_SIZE = 0x40000
HEADER = struct.Struct("<III")
BODY = struct.Struct("<8s32s11Q")
RECORD = struct.Struct("<8s32s11QII")
MAGIC = b"LGG8EP2\0"
FIELDS = ("phase", "x0", "x1", "x2", "x3", "current_el", "sctlr", "daif",
          "entry", "source", "pc")


def encode(data: bytes, zone_size: int = ZONE_SIZE) -> bytes:
    if zone_size < HEADER.size or len(data) > zone_size - HEADER.size:
        raise ValueError("record exceeds persistent RAM capacity")
    return HEADER.pack(SIGNATURE, len(data), len(data)) + data + bytes(zone_size - HEADER.size - len(data))


def decode(zone: bytes) -> bytes:
    if len(zone) < HEADER.size:
        raise ValueError("truncated persistent RAM header")
    signature, start, size = HEADER.unpack_from(zone)
    if signature != SIGNATURE or size > len(zone) - HEADER.size or start > size:
        raise ValueError("invalid persistent RAM header")
    data = zone[HEADER.size:HEADER.size + size]
    return data[start:] + data[:start]


def template(nonce: str) -> bytes:
    if len(nonce) != 32 or any(c not in "0123456789abcdef" for c in nonce):
        raise ValueError("nonce must be exactly 32 lowercase hexadecimal characters")
    return RECORD.pack(MAGIC, nonce.encode("ascii"), *([0] * len(FIELDS)), 0, 0)


def records(data: bytes, nonce: str, *, target: int, stage2_offset: int) -> list[dict]:
    template(nonce)
    if not data or len(data) % RECORD.size:
        raise ValueError("incomplete probe records")
    result = []
    for offset in range(0, len(data), RECORD.size):
        magic, actual_nonce, *values, checksum, reserved = RECORD.unpack_from(data, offset)
        if magic != MAGIC or actual_nonce != nonce.encode("ascii"):
            raise ValueError("probe magic or nonce mismatch")
        if reserved or checksum != zlib.crc32(data[offset:offset + BODY.size]):
            raise ValueError("probe checksum or reserved word mismatch")
        item = dict(zip(FIELDS, values))
        if (item["current_el"] not in (4, 8, 12) or item["sctlr"] & ((1 << 25) | 1)
                or item["daif"] & ~0x3C0 or item["entry"] & 3
                or item["source"] != item["entry"] + stage2_offset
                or item["pc"] != (item["entry"] if item["phase"] == 1 else target)):
            raise ValueError("invalid captured state or code address")
        result.append(item)
    if [item["phase"] for item in result] not in ([1], [1, 2]):
        raise ValueError("unexpected probe phase sequence")
    if len(result) == 2 and any(result[0][key] != result[1][key]
                                for key in FIELDS if key not in ("phase", "pc")):
        raise ValueError("inconsistent ENTER/TARGET state")
    return result


def readback(data: bytes, manifest: dict, input_format: str) -> dict:
    """Decode exact EP2 bytes; exported pstore data has no persistent RAM header."""
    if not isinstance(manifest, dict) or not isinstance(manifest.get("nonce"), str):
        raise ValueError("manifest must be an object with a string nonce")
    if (manifest["console"] != CONSOLE or manifest["zone_size"] != ZONE_SIZE
            or manifest["target"] != 0xAA000000):
        raise ValueError("manifest does not describe the audited EP2 layout")
    stage2 = manifest["symbols"]["stage2"]
    if type(stage2) is not int or not 0 < stage2 < 4096 or stage2 & 3:
        raise ValueError("invalid stage2 offset")
    if input_format == "raw-zone":
        if len(data) != ZONE_SIZE:
            raise ValueError("raw console zone must be exactly 0x40000 bytes")
        payload = decode(data)
    elif input_format == "pstore-console":
        payload = data
    else:
        raise ValueError("explicit input format required")
    items = records(payload, manifest["nonce"], target=manifest["target"],
                    stage2_offset=stage2)
    return {"observation": "enter-target" if len(items) == 2 else "enter-only",
            "records": items,
            "scope": "EP2 payload only; no Linux execution, reset retention or recovery acceptance"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline EP2 readback; never contacts a device")
    parser.add_argument("input", type=Path)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--format", choices=["raw-zone", "pstore-console"], required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = {"input_path": str(args.input), "manifest_path": str(args.manifest),
              "input_sha256": None, "input_bytes": None, "format": args.format,
              "manifest_sha256": None,
              "decoder_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    try:
        data = args.input.read_bytes()
        result.update(input_sha256=hashlib.sha256(data).hexdigest(), input_bytes=len(data))
        manifest_bytes = args.manifest.read_bytes()
        result["manifest_sha256"] = hashlib.sha256(manifest_bytes).hexdigest()
        result.update(readback(data, json.loads(manifest_bytes), args.format))
        result["valid_record"] = True
    except (OSError, ValueError, KeyError, TypeError) as error:
        result.update(valid_record=False, observation="inconclusive", error=str(error))
    # Preserve previous evidence, including when --out accidentally names an input.
    with os.fdopen(os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
        stream.write(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))
    return 0 if result["valid_record"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
