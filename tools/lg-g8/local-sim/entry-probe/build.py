#!/usr/bin/env python3
"""Build an offline-only LG G8 ARM64 entry probe; never contacts a device."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import subprocess

HERE = Path(__file__).resolve().parent
TARGET = 0xAA000000
CONSOLE = 0xB0000000
ZONE_SIZE = 0x40000
RUNTIME_SHA256 = "75af820a2032ff3b8043e2507ebab2d0dc38169feb56539997c00292f36f25bf"


def module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


record = module("probe_record", HERE / "record.py")
board = module("probe_board", HERE.parent / "dtb/verify.py")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check_layout(path: Path) -> None:
    if sha(path.read_bytes()) != RUNTIME_SHA256:
        raise ValueError("requires the audited clean LG G8 runtime FDT")
    nodes = board.tree(path)
    props = nodes["/soc/ramoops"]
    expected = {"mem-address": CONSOLE, "mem-size": 0x280000, "record-size": 0x1000,
                "console-size": ZONE_SIZE, "ftrace-size": 0x40000,
                "pmsg-size": 0x200000, "ecc-size": 0}
    for name, value in expected.items():
        if props[name] != struct.pack(">I", value):
            raise ValueError(f"unexpected ramoops {name}")
    if board.ranges(nodes["/reserved-memory/ramoops_region@b0000000"]["reg"]) != [(CONSOLE, CONSOLE + 0x300000)]:
        raise ValueError("unexpected ramoops reservation")
    if nodes["/psci"]["method"] != b"smc\0":
        raise ValueError("unexpected PSCI conduit")
    if not any(low <= TARGET and TARGET + 4096 <= high for low, high in board.memory(nodes)):
        raise ValueError("stage2 target outside RAM")
    reservations = board.reserved(nodes) + board.memreserve(path)
    for name, low, high in reservations:
        if max(low, TARGET) < min(high, TARGET + 4096):
            raise ValueError(f"stage2 target overlaps {name}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--memory-dtb", type=Path, required=True)
    parser.add_argument("--nonce", required=True)
    parser.add_argument("--reset", choices=["halt", "psci"], default="halt")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    template = record.template(args.nonce)
    check_layout(args.memory_dtb)
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    (out / "record.bin").write_bytes(template)
    commands = [
        ["clang", "--target=aarch64-none-elf", "-c", str(HERE / "probe.S"),
         f"-DCONSOLE_ADDRESS={CONSOLE}", f"-DCONSOLE_END={CONSOLE + ZONE_SIZE}",
         f"-DTARGET_ADDRESS={TARGET}", f"-DRESET_PSCI={int(args.reset == 'psci')}",
         "-o", "probe.o"],
        ["ld.lld", "--image-base=0", "-Ttext=0", "--entry=_start", "probe.o", "-o", "probe.elf"],
        ["llvm-objcopy", "-O", "binary", "--only-section=.text", "probe.elf", "probe.Image"],
    ]
    for command in commands:
        subprocess.run(command, cwd=out, check=True)
    symbols = {}
    for line in subprocess.check_output(["llvm-nm", "--defined-only", "probe.elf"], cwd=out, text=True).splitlines():
        address, kind, name = line.split()
        if kind.upper() == "T":
            symbols[name] = int(address, 16)
    image = (out / "probe.Image").read_bytes()
    if image[56:60] != b"ARM\x64" or len(image) != symbols["image_end"]:
        raise ValueError("invalid ARM64 image header or extent")
    if symbols["stage2_end"] - symbols["stage2"] > 4096:
        raise ValueError("stage2 exceeds checked target page")
    manifest = {"scope": "offline-only tiny probe; no Linux, boot.img, watchdog or device operations",
                "nonce": args.nonce, "reset": args.reset, "target": TARGET,
                "console": CONSOLE, "zone_size": ZONE_SIZE, "symbols": symbols,
                "runtime_fdt_sha256": RUNTIME_SHA256, "image_sha256": sha(image),
                "sources": {name: sha((HERE / name).read_bytes())
                            for name in ["probe.S", "record.py", "build.py"]},
                "dtb_parser_sha256": sha((HERE.parent / "dtb/verify.py").read_bytes()),
                "commands": commands}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"image_sha256": manifest["image_sha256"], "reset": args.reset,
                      "image_size": len(image), "stage2_size": symbols["stage2_end"] - symbols["stage2"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
