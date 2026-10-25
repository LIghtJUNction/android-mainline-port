#!/usr/bin/env python3
"""Build a folder-backed ESP using the existing standard ARM64 UEFI Shell."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_efi(path: Path) -> None:
    data = path.read_bytes()
    if len(data) < 64 or data[:2] != b"MZ":
        raise ValueError(f"{path}: not an EFI PE executable")
    offset = struct.unpack_from("<I", data, 60)[0]
    if (offset + 94 > len(data) or data[offset:offset + 4] != b"PE\0\0"
            or struct.unpack_from("<H", data, offset + 4)[0] != 0xAA64
            or struct.unpack_from("<H", data, offset + 24)[0] != 0x20B
            or struct.unpack_from("<H", data, offset + 24 + 68)[0] != 10):
        raise ValueError(f"{path}: expected an AArch64 PE32+ EFI application")


def build_esp(shell: Path, kernel: Path, initrd: Path, out: Path,
              cmdline: str, dtb: Path | None = None) -> dict:
    if any(char in cmdline for char in "\r\n\0") or not cmdline.isascii():
        raise ValueError("cmdline must be one ASCII line")
    if any(word.startswith(("initrd=", "dtb=")) for word in cmdline.split()):
        raise ValueError("initrd/dtb paths are added by the ESP builder")
    check_efi(shell)
    check_efi(kernel)
    if not initrd.is_file():
        raise ValueError(f"missing initramfs: {initrd}")
    if dtb:
        data = dtb.read_bytes()
        if len(data) < 40 or data[:4] != b"\xd0\x0d\xfe\xed" or struct.unpack_from(">I", data, 4)[0] != len(data):
            raise ValueError("DTB is not a complete raw FDT")
        checked = subprocess.run(["dtc", "-I", "dtb", "-O", "dts"], input=data,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if checked.returncode:
            raise ValueError("DTB failed dtc validation")
    out.mkdir(parents=True, exist_ok=True)
    boot = out / "EFI/BOOT"
    boot.mkdir(parents=True, exist_ok=True)
    copies = [(shell, boot / "BOOTAA64.EFI"), (kernel, out / "kernel.efi"),
              (initrd, out / "initramfs.cpio")]
    if dtb:
        copies.append((dtb, out / "board.dtb"))
    for source, dest in copies:
        if source.resolve() == dest.resolve():
            raise ValueError(f"source and output must differ: {source}")
        shutil.copyfile(source, dest)
    options = cmdline.strip() + " initrd=\\initramfs.cpio"
    if dtb:
        options += " dtb=\\board.dtb"
    # Shell sets homefilesystem to the volume from which it loaded startup.nsh.
    # Keep the script next to Shell itself, avoiding guessed FS0/FS1 mappings.
    script = ("@echo -off\n"
              "echo LGG8 STANDARD_EFI_SHELL\n"
              f"%homefilesystem%\\kernel.efi {options}\n"
              "echo LGG8 EFI_KERNEL_RETURNED %lasterror%\n")
    (boot / "startup.nsh").write_bytes(script.replace("\n", "\r\n").encode("ascii"))
    manifest = {"launcher": "unmodified standard EDK2 AArch64 Shell",
                "cmdline": options, "external_dtb": bool(dtb),
                "files": {str(dest.relative_to(out)): sha(dest) for _, dest in copies},
                "startup_sha256": sha(boot / "startup.nsh"),
                "device_installation": "not performed",
                "hardware_acceptance": "not established by constructing this ESP"}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shell", required=True, type=Path)
    parser.add_argument("--kernel", required=True, type=Path)
    parser.add_argument("--initrd", required=True, type=Path)
    parser.add_argument("--dtb", type=Path)
    parser.add_argument("--cmdline", required=True)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    build_esp(args.shell, args.kernel, args.initrd, args.out, args.cmdline, args.dtb)
    print("Standard Shell ESP folder ready:", args.out.resolve())


if __name__ == "__main__":
    main()
