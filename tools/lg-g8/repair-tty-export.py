#!/usr/bin/env python3
"""Undo verified LF-to-CRLF damage in local binary exports, without a device.

Original files are never changed. A candidate must round-trip through the
transport transform and pass standard format checks before it is saved.
AVB descriptor verification is reported separately, including mismatches.
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "local-sim"))
from run_linux import read_cpio  # In-memory parsing; no extraction to the host.


def sha(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def align(size: int, page: int) -> int:
    return (size + page - 1) // page * page


def span(data: bytes, start: int, size: int) -> bytes:
    if start < 0 or size < 0 or start + size > len(data):
        raise ValueError(f"range outside file: {start:#x}+{size:#x}")
    return data[start:start + size]


class Validator:
    def __init__(self, logs: Path):
        self.logs = logs
        self.logs.mkdir(parents=True, exist_ok=True)
        library = ctypes.util.find_library("fdt")
        if not library:
            raise ValueError("libfdt is required")
        self.libfdt = ctypes.CDLL(library)
        self.libfdt.fdt_check_full.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        self.libfdt.fdt_check_full.restype = ctypes.c_int
        self.fdts = {}

    def fdt(self, data: bytes, name: str) -> dict:
        if len(data) < 40 or data[:4] != b"\xd0\x0d\xfe\xed":
            raise ValueError(f"{name}: not an FDT")
        size = struct.unpack_from(">I", data, 4)[0]
        if size != len(data):
            raise ValueError(f"{name}: FDT totalsize={size} differs from slice={len(data)}")
        key = sha(data)
        if key not in self.fdts:
            buffer = ctypes.create_string_buffer(data)
            code = self.libfdt.fdt_check_full(buffer, len(data))
            if code:
                raise ValueError(f"{name}: libfdt_check_full returned {code}")
            proc = subprocess.run(["dtc", "-I", "dtb", "-O", "dts"], input=data,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            log = self.logs / f"{name}.dtc.log"
            log.write_bytes(proc.stderr)
            if proc.returncode:
                raise ValueError(f"{name}: dtc failed; see {log}")
            self.fdts[key] = {"size": size, "sha256": key, "libfdt_check_full": 0,
                              "dtc_returncode": 0, "dtc_warnings_log": str(log)}
        return dict(self.fdts[key])

    def gzip(self, data: bytes, name: str) -> tuple[bytes, dict]:
        if not data.startswith(b"\x1f\x8b"):
            raise ValueError(f"{name}: expected a gzip member")
        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
        decoded = decoder.decompress(data, 128 * 1024 * 1024)
        if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise ValueError(f"{name}: incomplete, oversized or trailing gzip data")
        return decoded, {"crc_and_isize_verified": True, "decoded_size": len(decoded),
                         "decoded_sha256": sha(decoded)}

    def footer(self, data: bytes) -> dict:
        if data[-64:-60] != b"AVBf":
            raise ValueError("AVB footer is not at the standard end-of-partition offset")
        _, major, minor, original, offset, size = struct.unpack(">4sIIQQQ28x", data[-64:])
        vbmeta = span(data, offset, size)
        if major != 1 or original > offset or vbmeta[:4] != b"AVB0" or offset + size > len(data) - 64:
            raise ValueError("invalid standard AVB footer/vbmeta bounds")
        return {"version": [major, minor], "original_image_size": original,
                "vbmeta_offset": offset, "vbmeta_size": size, "standard_footer_bounds": True}

    def boot(self, data: bytes, name: str) -> dict:
        if len(data) < 1660:
            raise ValueError("truncated Android boot header")
        kernel_size, kernel_addr, initrd_size, initrd_addr, second_size, second_addr, tags, page, version = struct.unpack_from("<9I", data, 8)
        header_size, dtb_size, dtb_addr = struct.unpack_from("<IIQ", data, 1644)
        recovery_size = struct.unpack_from("<I", data, 1632)[0]
        if version != 2 or header_size != 1660 or page not in (2048, 4096, 8192, 16384) or recovery_size:
            raise ValueError("expected standard Android v2 header with no recovery_dtbo")
        kernel_start = page
        initrd_start = page + align(kernel_size, page)
        dtb_start = initrd_start + align(initrd_size, page) + align(second_size, page)
        kernel, kernel_report = self.gzip(span(data, kernel_start, kernel_size), name + "-kernel")
        if kernel[56:60] != b"ARM\x64":
            raise ValueError("decoded kernel lacks ARM64 Image magic")
        initrd, initrd_report = self.gzip(span(data, initrd_start, initrd_size), name + "-ramdisk")
        initrd_report["cpio_entries"] = len(read_cpio(initrd))
        section = span(data, dtb_start, dtb_size)
        cursor, trees = 0, []
        while cursor < len(section):
            size = struct.unpack_from(">I", span(section, cursor, 8), 4)[0]
            if size < 40:
                raise ValueError("invalid concatenated FDT size")
            trees.append(self.fdt(span(section, cursor, size), f"{name}-dtb-{len(trees)}"))
            cursor += size
        return {"format": "android-boot-v2", "page_size": page, "header_size": header_size,
                "kernel_addr": hex(kernel_addr), "ramdisk_addr": hex(initrd_addr),
                "second_addr": hex(second_addr), "tags_addr": hex(tags), "dtb_addr": hex(dtb_addr),
                "kernel_gzip": kernel_report, "ramdisk_gzip": initrd_report,
                "dtb_offset": dtb_start, "dtb_size": dtb_size, "dtbs": trees,
                "avb_footer": self.footer(data)}

    def dtbo(self, data: bytes, name: str) -> dict:
        magic, size, header, entry_size, count, offset, page, version = struct.unpack_from(">8I", data)
        if header != 32 or entry_size != 32 or not 0 < count < 1024 or offset < header or size > len(data):
            raise ValueError("invalid Android DT table bounds")
        span(data[:size], offset, count * entry_size)
        trees, ranges = [], []
        for index in range(count):
            length, start = struct.unpack_from(">2I", data, offset + index * entry_size)
            if start < offset + count * entry_size:
                raise ValueError("DTBO entry overlaps table")
            for low, high in ranges:
                if start < high and low < start + length:
                    raise ValueError("DTBO entries overlap")
            ranges.append((start, start + length))
            trees.append(self.fdt(span(data[:size], start, length), f"{name}-entry-{index}"))
        return {"format": "android-dt-table", "total_size": size, "entry_count": count,
                "version": version, "page_size": page, "entries": trees,
                "avb_footer": self.footer(data)}

    def validate(self, data: bytes, name: str) -> dict:
        if data.startswith(b"ANDROID!"):
            return self.boot(data, name)
        if data.startswith(b"\xd7\xb7\xab\x1e"):
            return self.dtbo(data, name)
        if data.startswith(b"\xd0\x0d\xfe\xed"):
            return {"format": "fdt", "tree": self.fdt(data, name)}
        if data.startswith(b"AVB0"):
            return {"format": "vbmeta", "note": "Partition contents are not supplied for chain verification"}
        raise ValueError("unrecognized candidate format")


def avb_check(path: Path, kind: str, logs: Path) -> dict | None:
    if kind == "fdt":
        return None
    info = subprocess.run(["avbtool", "info_image", "--image", str(path)], capture_output=True)
    (logs / f"{path.name}.avb-info.log").write_bytes(info.stdout + info.stderr)
    if info.returncode:
        raise ValueError(f"avbtool cannot parse {path}")
    result = {"info_returncode": info.returncode, "algorithm": next(
        (line.strip() for line in info.stdout.decode().splitlines() if "Algorithm:" in line), "unknown")}
    if kind != "vbmeta":
        # avbtool resolves a descriptor's partition name relative to the image.
        # Give each slot its own exact alias, rather than verifying a different slot.
        partition = "boot" if kind == "android-boot-v2" else "dtbo"
        with tempfile.TemporaryDirectory(prefix="lg-avb-check-") as temp:
            alias = Path(temp) / f"{partition}.img"
            alias.symlink_to(path.resolve())
            proc = subprocess.run(["avbtool", "verify_image", "--image", str(alias)], capture_output=True)
        (logs / f"{path.name}.avb-verify.log").write_bytes(proc.stdout + proc.stderr)
        result.update(verify_returncode=proc.returncode, descriptor_verifies=proc.returncode == 0)
    else:
        result["descriptor_verifies"] = None
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, nargs="+")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    report_path = out / "report.json"
    report_path.unlink(missing_ok=True)
    for tool in ("dtc", "avbtool"):
        if not shutil.which(tool):
            parser.error(f"{tool} is required")
    validator = Validator(out / "logs")
    report = {"transform": "inverse-LF-to-CRLF", "device_access": False,
              "bootable_on_phone": "not tested", "files": [], "validated": False}
    names = set()
    for source in args.input:
        source = source.resolve()
        dest = out / source.name
        if source == dest or source.name in names:
            parser.error(f"refusing to overwrite or reuse destination: {dest}")
        names.add(source.name)
        raw = source.read_bytes()
        crlf, lf = raw.count(b"\r\n"), raw.count(b"\n")
        if not crlf or crlf != lf:
            parser.error(f"{source}: not a complete LF-to-CRLF export; refusing conversion")
        decoded = raw.replace(b"\r\n", b"\n")
        if decoded.replace(b"\n", b"\r\n") != raw:
            parser.error(f"{source}: inverse round trip failed")
        validation = validator.validate(decoded, source.name)
        created = not dest.exists()
        if not created and dest.read_bytes() != decoded:
            parser.error(f"refusing to overwrite different output: {dest}")
        if created:
            dest.write_bytes(decoded)
        try:
            avb = avb_check(dest, validation["format"], out / "logs")
        except Exception:
            if created:
                dest.unlink()
            raise
        record = {"input": str(source), "output": str(dest), "input_size": len(raw),
                  "output_size": len(decoded), "inserted_CR_bytes": crlf, "input_bare_LF": lf - crlf,
                  "input_sha256": sha(raw), "output_sha256": sha(decoded),
                  "inverse_roundtrip_exact": True, "validation": validation, "avb": avb}
        report["files"].append(record)
        report_path.write_text(json.dumps(report, indent=2) + "\n")
        avb_status = f" AVB descriptor={avb.get('descriptor_verifies')}" if avb else ""
        print(f"VALID {source.name}: {len(raw)} -> {len(decoded)} bytes; {validation['format']}{avb_status}", flush=True)
    report["validated"] = True
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    print("Original exports preserved. Format validation is separate from AVB authenticity or device boot.")
    print("Report:", report_path)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, zlib.error) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
