#!/usr/bin/env python3
"""Patch the known Android kernel's bootloader reboot request to the LAF route."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import struct
import subprocess

KERNEL_SHA256 = "233d4a248c28fd55fce3926652fbc4155b8a6883fdf60dfc14906e2ba88e1a4e"
BOOT_SHA256 = "951ad1eb3dd360e17a47e0fcdb8575a2df8119690325c1e72e203fd02a4a7bf2"
SELECTOR = 0xC1DCD8
PON_SETTER = 0xAA5DE8
PATCHES = [(0xC1DE94, 0x52800040, 0x528004E0), (0xC1DEB8, 0x528AA008, 0x528AAD08)]


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def patched(kernel: bytes) -> bytes:
    if sha(kernel) != KERNEL_SHA256:
        raise ValueError("unsupported kernel: exact stock Image hash required")
    result = bytearray(kernel)
    for offset, before, after in PATCHES:
        if struct.unpack_from("<I", kernel, offset)[0] != before:
            raise ValueError(f"expected instruction missing at {offset:#x}")
        struct.pack_into("<I", result, offset, after)
    return bytes(result)


def update_unsigned_boot_hash(image: bytearray, vbmeta_offset: int) -> dict:
    if image[vbmeta_offset:vbmeta_offset + 4] != b"AVB0":
        raise ValueError("missing inline vbmeta header")
    auth_size = struct.unpack_from(">Q", image, vbmeta_offset + 12)[0]
    algorithm = struct.unpack_from(">I", image, vbmeta_offset + 28)[0]
    if algorithm != 0 or auth_size != 0:
        raise ValueError("refusing to modify a signed vbmeta image")
    descriptor_offset, descriptor_size = struct.unpack_from(">QQ", image, vbmeta_offset + 96)
    cursor = vbmeta_offset + 256 + descriptor_offset
    end = cursor + descriptor_size
    found = []
    while cursor < end:
        tag, following = struct.unpack_from(">QQ", image, cursor)
        if following % 8 or cursor + 16 + following > end:
            raise ValueError("invalid AVB descriptor bounds")
        if tag == 2:
            size = struct.unpack_from(">Q", image, cursor + 16)[0]
            digest_algorithm = bytes(image[cursor + 24:cursor + 56]).rstrip(b"\0").decode()
            name_size, salt_size, digest_size = struct.unpack_from(">III", image, cursor + 56)
            name_start = cursor + 132
            name = bytes(image[name_start:name_start + name_size]).decode()
            if name != "boot" or size > vbmeta_offset:
                raise ValueError("unexpected hash descriptor or coverage")
            salt_start = name_start + name_size
            digest_start = salt_start + salt_size
            salt = bytes(image[salt_start:digest_start])
            digest = hashlib.new(digest_algorithm, salt + image[:size]).digest()
            if len(digest) != digest_size:
                raise ValueError("unexpected AVB digest length")
            image[digest_start:digest_start + digest_size] = digest
            found.append({"partition": name, "covered_bytes": size, "algorithm": digest_algorithm,
                          "digest": digest.hex(), "signing_algorithm": "NONE"})
        cursor += 16 + following
    if cursor != end or len(found) != 1:
        raise ValueError("expected exactly one boot hash descriptor")
    return found[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("boot_image", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    original = args.boot_image.read_bytes()
    if sha(original) != BOOT_SHA256:
        raise ValueError("unsupported boot image: re-audit newer ROMs before patching")
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    ksize, _, rsize, _, second, _, _, page, version, _ = struct.unpack_from("<10I", original, 8)
    if version != 2 or second or struct.unpack_from("<I", original, 1632)[0]:
        raise ValueError("unexpected Android image layout")
    align = lambda value: (value + page - 1) // page * page
    rstart = page + align(ksize)
    dstart = rstart + align(rsize)
    dsize = struct.unpack_from("<I", original, 1648)[0]
    kernel = gzip.decompress(original[page:page + ksize])
    fixed = patched(kernel)
    compressed = gzip.compress(fixed, compresslevel=9, mtime=0)
    ramdisk = original[rstart:rstart + rsize]
    dtb = original[dstart:dstart + dsize]
    footer = struct.unpack_from(">4sIIQQQ", original, len(original) - 64)
    magic, _, _, logical_size, vbmeta_offset, vbmeta_size = footer
    if magic != b"AVBf" or logical_size != vbmeta_offset:
        raise ValueError("unexpected footer or metadata boundary")
    body = b"".join(data + b"\0" * (align(len(data)) - len(data)) for data in [compressed, ramdisk, dtb])
    if page + len(body) > logical_size:
        raise ValueError("new components would overlap the retained vbmeta image")
    image = bytearray(original)
    image[page:logical_size] = body + b"\0" * (logical_size - page - len(body))
    struct.pack_into("<I", image, 8, len(compressed))
    cmdline = original[64:576].split(b"\0")[0]
    extra = original[608:1632].split(b"\0")[0]
    marker = b"lgg8_reboot_fix=pon27-imem68-v1"
    if extra or len(cmdline) + len(marker) + 2 > 512:
        raise ValueError("unexpected command line: refuse to truncate boot parameters")
    image[64:576] = (cmdline + b" " + marker + b"\0").ljust(512, b"\0")
    identifier = hashlib.sha1()
    for data in [compressed, ramdisk, b"", b"", dtb]:
        identifier.update(data)
        identifier.update(struct.pack("<I", len(data)))
    image[576:608] = identifier.digest() + b"\0" * 12
    descriptor = update_unsigned_boot_hash(image, vbmeta_offset)
    new_rstart = page + align(len(compressed))
    new_dstart = new_rstart + align(rsize)
    assert gzip.decompress(image[page:page + len(compressed)]) == fixed
    assert image[new_rstart:new_rstart + rsize] == ramdisk
    assert image[new_dstart:new_dstart + dsize] == dtb
    assert image[-64:] == original[-64:] and len(image) == len(original)
    (out / "Image.original").write_bytes(kernel)
    (out / "Image.patched").write_bytes(fixed)
    (out / "boot.img").write_bytes(image)
    check = subprocess.run(["avbtool", "verify_image", "--image", str(out / "boot.img")],
                           capture_output=True, text=True)
    (out / "avb-verify.txt").write_text(check.stdout + check.stderr)
    if check.returncode:
        raise ValueError("official avbtool verification failed: " + check.stderr)
    report = {"source_boot_sha256": BOOT_SHA256, "source_kernel_sha256": KERNEL_SHA256,
              "patched_kernel_sha256": sha(fixed), "output_boot_sha256": sha(image),
              "kernel_patches": [{"offset": off, "before": hex(a), "after": hex(b)} for off, a, b in PATCHES],
              "ramdisk_sha256_unchanged": sha(ramdisk), "dtb_sha256_unchanged": sha(dtb),
              "kernel_compressed_before": ksize, "kernel_compressed_after": len(compressed),
              "cmdline_marker": marker.decode(),
              "unsigned_boot_descriptor": descriptor, "official_avb_verification_passed": True,
              "partition_writes": False, "device_booted": False}
    (out / "manifest.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
