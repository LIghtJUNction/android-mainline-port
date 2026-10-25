#!/usr/bin/env python3
"""Run an existing Android boot v2 kernel/initramfs locally, without USB access.

Preserves the source image. First reproduces its init failure, then adds missing
musl files and boots the same kernel through both QEMU direct boot and UEFI.
This verifies software on QEMU virt, not the LG G8 SoC or physical USB PHY.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import selectors
import shutil
import stat
import struct
import subprocess
import time


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def align(value: int, unit: int = 4) -> int:
    return (value + unit - 1) // unit * unit


def unpack_boot(image: bytes) -> tuple[bytes, bytes, dict]:
    if image[:8] != b"ANDROID!" or len(image) < 1660:
        raise ValueError("not an Android boot image v2")
    kernel_size, kernel_addr, initrd_size, initrd_addr = struct.unpack_from("<4I", image, 8)
    page, version = struct.unpack_from("<2I", image, 36)
    if version != 2 or page not in (2048, 4096, 8192, 16384):
        raise ValueError(f"unsupported boot ABI: v{version}, page={page}")
    kernel_offset = page
    initrd_offset = page + align(kernel_size, page)
    if initrd_offset + initrd_size > len(image):
        raise ValueError("truncated kernel or ramdisk")
    kernel = image[kernel_offset:kernel_offset + kernel_size]
    initrd = image[initrd_offset:initrd_offset + initrd_size]
    if kernel[:2] == b"\x1f\x8b":
        kernel = gzip.decompress(kernel)
    if initrd[:2] == b"\x1f\x8b":
        initrd = gzip.decompress(initrd)
    if kernel[:2] != b"MZ":
        raise ValueError("kernel lacks an EFI-stub PE header")
    pe = struct.unpack_from("<I", kernel, 60)[0]
    if kernel[pe:pe + 4] != b"PE\0\0" or struct.unpack_from("<H", kernel, pe + 4)[0] != 0xAA64:
        raise ValueError("not an AArch64 PE kernel")
    return kernel, initrd, dict(header_version=version, page_size=page,
        kernel_addr=hex(kernel_addr), initrd_addr=hex(initrd_addr),
        kernel_size=len(kernel), image_sha256=digest(image),
        kernel_sha256=digest(kernel), original_initramfs_sha256=digest(initrd))


def read_cpio(data: bytes) -> dict[str, tuple[int, bytes, int, int]]:
    entries = {}
    offset = 0
    while offset + 110 <= len(data):
        if data[offset:offset + 6] not in (b"070701", b"070702"):
            raise ValueError(f"invalid newc header at {offset}")
        fields = [int(data[offset + 6 + i * 8:offset + 14 + i * 8], 16) for i in range(13)]
        mode, size, name_size = fields[1], fields[6], fields[11]
        name_start = offset + 110
        name = data[name_start:name_start + name_size - 1].decode()
        start = align(name_start + name_size)
        if not name_size or start + size > len(data):
            raise ValueError("truncated cpio record")
        if name == "TRAILER!!!":
            return entries
        # Parse data in memory: never extract untrusted archive paths to the host.
        name = name.removeprefix("./")
        if name.startswith("/") or ".." in name.split("/"):
            raise ValueError(f"unsafe cpio path: {name}")
        entries[name] = mode, data[start:start + size], fields[9], fields[10]
        offset = align(start + size)
    raise ValueError("cpio trailer missing")


def write_cpio(entries: dict[str, tuple[int, bytes, int, int]]) -> bytes:
    out = bytearray()
    records = list(entries.items()) + [("TRAILER!!!", (0, b"", 0, 0))]
    for inode, (name, (mode, content, major, minor)) in enumerate(records, 1):
        raw_name = name.encode() + b"\0"
        fields = [inode, mode, 0, 0, 2 if stat.S_ISDIR(mode) else 1, 0,
                  len(content), 0, 0, major, minor, len(raw_name), 0]
        out += b"070701" + b"".join(f"{value:08x}".encode() for value in fields) + raw_name
        out += b"\0" * (-len(out) % 4)
        out += content
        out += b"\0" * (-len(out) % 4)
    out += b"\0" * (-len(out) % 512)
    return bytes(out)


def interpreter(elf: bytes) -> str | None:
    if elf[:6] != b"\x7fELF\x02\x01":
        raise ValueError("BusyBox must be little-endian ELF64")
    if struct.unpack_from("<H", elf, 18)[0] != 183:
        raise ValueError("BusyBox is not AArch64")
    offset = struct.unpack_from("<Q", elf, 32)[0]
    entry_size, count = struct.unpack_from("<HH", elf, 54)
    for i in range(count):
        hdr = offset + i * entry_size
        if struct.unpack_from("<I", elf, hdr)[0] == 3:
            start = struct.unpack_from("<Q", elf, hdr + 8)[0]
            size = struct.unpack_from("<Q", elf, hdr + 32)[0]
            return elf[start:start + size].rstrip(b"\0").decode()
    return None


SIM_INIT = b'''#!/bin/sh
if [ -f /sim/dummy_hcd.ko ]; then
    /bin/busybox insmod /sim/dummy_hcd.ko || exit 1
    /bin/busybox insmod /sim/cdc-acm.ko || exit 1
fi
(/bin/sleep 2; /bin/busybox sh /sim-assert) &
exec /init
'''

SIM_ASSERT = b'''#!/bin/sh
exec >/dev/console 2>&1
fail() { echo "LOCAL-SIM FAIL: $*"; /bin/busybox poweroff -f; exit 1; }
[ -d /proc/1 ] || fail "proc not mounted"
[ -d /sys/kernel/config/usb_gadget ] || fail "configfs USB unavailable"
/bin/busybox dmesg | /bin/busybox grep -q LGG8-UEFI-V2-INIT || fail "original init marker absent"
echo "LOCAL-SIM ORIGINAL_INIT_MARKER_OK"
echo "LOCAL-SIM ARCH=$(/bin/busybox uname -m) KERNEL=$(/bin/busybox uname -r)"
echo "LOCAL-SIM PID1=$(/bin/busybox tr '\\000' ' ' </proc/1/cmdline)"
if [ -d /sys/firmware/efi ]; then
    echo "LOCAL-SIM UEFI_PRESENT"
else
    echo "LOCAL-SIM DIRECT_BOOT"
fi
if [ -r /sys/kernel/config/usb_gadget/lgg8/idVendor ]; then
    echo "LOCAL-SIM GADGET_CONFIG_OK"
else
    fail "original init did not configure gadget"
fi
UDC=$(/bin/busybox cat /sys/kernel/config/usb_gadget/lgg8/UDC)
if [ -n "$UDC" ]; then
    echo "LOCAL-SIM GADGET_BOUND=$UDC"
else
    echo "LOCAL-SIM NO_UDC: QEMU virt has no LG USB controller"
fi
if [ -f /sim/dummy_hcd.ko ]; then
    [ -n "$UDC" ] || fail "dummy UDC was not bound"
    i=0
    while [ ! -c /dev/ttyACM0 ] && [ "$i" -lt 20 ]; do
        /bin/sleep 1
        i=$((i+1))
    done
    [ -c /dev/ttyACM0 ] || fail "virtual USB host did not enumerate ACM"
    /bin/busybox stty -F /dev/ttyACM0 raw -echo -hupcl || fail "host termios"
    /bin/busybox stty -F /dev/ttyGS0 raw -echo -hupcl || fail "gadget termios"
    exec 3<>/dev/ttyACM0 4<>/dev/ttyGS0
    payload=LGG8_HOST_TO_GADGET
    /bin/busybox timeout 5 /bin/busybox dd bs=1 count="${#payload}" <&4 >/run/usb-rx-device 2>/dev/null &
    receiver=$!
    /bin/sleep 1
    printf '%s' "$payload" >&3
    wait "$receiver" || fail "host-to-gadget transfer timed out"
    [ "$(/bin/cat /run/usb-rx-device)" = "$payload" ] || fail "host-to-gadget data mismatch"
    echo "LOCAL-SIM USB_HOST_TO_GADGET_OK"
    payload=LGG8_GADGET_TO_HOST
    /bin/busybox timeout 5 /bin/busybox dd bs=1 count="${#payload}" <&3 >/run/usb-rx-host 2>/dev/null &
    receiver=$!
    /bin/sleep 1
    printf '%s' "$payload" >&4
    wait "$receiver" || fail "gadget-to-host transfer timed out"
    [ "$(/bin/cat /run/usb-rx-host)" = "$payload" ] || fail "gadget-to-host data mismatch"
    echo "LOCAL-SIM USB_GADGET_TO_HOST_OK"
    exec 3>&- 4>&-
fi
echo "LOCAL-SIM PASS"
/bin/busybox poweroff -f
'''


def fix_initrd(original: bytes, musl_root: Path) -> tuple[bytes, dict]:
    entries = read_cpio(original)
    original_init = entries["init"]
    _, busybox, _, _ = entries["bin/busybox"]
    interp = interpreter(busybox)
    missing = bool(interp and interp.lstrip("/") not in entries)
    libraries = {}
    if interp:
        if interp != "/lib/ld-musl-aarch64.so.1":
            raise ValueError(f"unexpected interpreter {interp}; provide a suitable root")
        entries.setdefault("lib", (stat.S_IFDIR | 0o755, b"", 0, 0))
        for name in ("ld-musl-aarch64.so.1", "libc.musl-aarch64.so.1"):
            lib = musl_root / "lib" / name
            blob = lib.read_bytes()
            if (blob[:6] != b"\x7fELF\x02\x01" or len(blob) < 20
                    or struct.unpack_from("<H", blob, 18)[0] != 183):
                raise ValueError(f"invalid musl file {lib}")
            entries[f"lib/{name}"] = stat.S_IFREG | 0o755, blob, 0, 0
            libraries[f"lib/{name}"] = digest(blob)
    # Only the runtime dependency fix belongs in the repaired initramfs.
    entries["dev/console"] = stat.S_IFCHR | 0o600, b"", 5, 1
    entries["dev/null"] = stat.S_IFCHR | 0o666, b"", 1, 3
    assert entries["init"] == original_init
    return write_cpio(entries), dict(busybox_interpreter=interp,
        missing_interpreter_in_original=missing, init_sha256=digest(entries["init"][1]),
        busybox_sha256=digest(busybox), original_init_unchanged=True,
        runtime_libraries=libraries)


def test_initrd(repaired: bytes, modules: Path | None = None) -> bytes:
    entries = read_cpio(repaired)
    for name, content in (("sim-init", SIM_INIT), ("sim-assert", SIM_ASSERT)):
        entries[name] = stat.S_IFREG | 0o755, content, 0, 0
    if modules:
        entries["sim"] = stat.S_IFDIR | 0o755, b"", 0, 0
        for name in ("dummy_hcd.ko", "cdc-acm.ko"):
            entries[f"sim/{name}"] = stat.S_IFREG | 0o644, (modules / name).read_bytes(), 0, 0
    return write_cpio(entries)


def compile_loader(edk2: Path, output: Path) -> None:
    here = Path(__file__).resolve().parent
    includes = edk2 / "MU_BASECORE" / "MdePkg" / "Include"
    if not includes.exists():
        raise ValueError(f"MdePkg includes not found under {edk2}")
    subprocess.run(["clang", "--target=aarch64-unknown-windows-msvc", "-ffreestanding",
        "-fno-stack-protector", "-fno-builtin", "-fshort-wchar", "-Wall", "-Wextra", "-Werror",
        "-Os", "-I", str(includes), "-I", str(includes / "AArch64"),
        "-c", str(here / "efi_loader.c"), "-o", str(output / "loader.obj")], check=True)
    subprocess.run(["lld-link", "/subsystem:efi_application", "/entry:EfiMain", "/nodefaultlib",
        "/machine:arm64", str(output / "loader.obj"), f"/out:{output / 'BOOTAA64.EFI'}"], check=True)


def run_qemu(args: list[str], log: Path, timeout: float, expect: str) -> dict:
    started = time.monotonic()
    cmd = ["qemu-system-aarch64", "-machine", "virt,gic-version=3,acpi=off", "-cpu", "cortex-a76",
        "-accel", "tcg", "-m", "512", "-smp", "1", "-display", "none", "-serial", "stdio",
        "-monitor", "none", "-nic", "none", "-no-reboot"] + args
    selector = selectors.DefaultSelector()
    process = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    selector.register(process.stdout, selectors.EVENT_READ)
    transcript = bytearray()
    matched = False
    try:
        with log.open("wb") as dest:
            while time.monotonic() - started < timeout:
                if process.poll() is not None:
                    remaining = process.stdout.read()
                    transcript += remaining
                    dest.write(remaining)
                    break
                for key, _ in selector.select(timeout=0.2):
                    chunk = key.fileobj.read1(16384)
                    transcript += chunk
                    dest.write(chunk)
                    dest.flush()
                if expect.encode() in transcript:
                    matched = True
                    break
    finally:
        selector.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        process.stdout.close()
    text = transcript.decode(errors="replace")
    matched = matched or expect in text
    return dict(command=cmd, seconds=round(time.monotonic() - started, 2),
                matched=matched, expected=expect, log=str(log), exit_code=process.returncode,
                markers=[line for line in text.splitlines() if "LOCAL-SIM" in line
                         or "No working init" in line or "Failed to execute /init" in line])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--boot-image", type=Path, required=True)
    parser.add_argument("--musl-root", type=Path, required=True)
    parser.add_argument("--edk2-source", type=Path, required=True)
    parser.add_argument("--firmware", type=Path, default=Path("/usr/share/edk2/aarch64/QEMU_EFI.fd"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=70)
    parser.add_argument("--usb-module-dir", type=Path,
                        help="Optional matching dummy_hcd and cdc-acm modules for local USB traffic tests")
    opts = parser.parse_args()
    out = opts.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    (out / "result.json").unlink(missing_ok=True)
    kernel, original, info = unpack_boot(opts.boot_image.read_bytes())
    repaired, correction = fix_initrd(original, opts.musl_root)
    fixed = test_initrd(repaired)
    (out / "kernel.efi").write_bytes(kernel)
    (out / "initramfs-original.cpio").write_bytes(original)
    (out / "initramfs-repaired.cpio").write_bytes(repaired)
    (out / "initramfs-fixed.cpio").write_bytes(fixed)
    report = dict(platform="QEMU virt (not an emulated LG G8)", image=info, correction=correction,
                  repaired_initramfs_sha256=digest(repaired),
                  test_initramfs_sha256=digest(fixed), tests={})
    base = ["-kernel", str(out / "kernel.efi")]
    report["tests"]["original"] = run_qemu(base + ["-initrd", str(out / "initramfs-original.cpio"),
        "-append", "console=ttyAMA0,115200 rdinit=/init panic=0"], out / "original.log", opts.timeout,
        "Kernel panic - not syncing: No working init found")
    report["tests"]["fixed_direct"] = run_qemu(base + ["-initrd", str(out / "initramfs-fixed.cpio"),
        "-append", "console=ttyAMA0,115200 rdinit=/sim-init panic=0"], out / "fixed-direct.log", opts.timeout,
        "LOCAL-SIM PASS")
    compile_loader(opts.edk2_source, out)
    esp = out / "esp"
    (esp / "EFI" / "BOOT").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(out / "BOOTAA64.EFI", esp / "EFI" / "BOOT" / "BOOTAA64.EFI")
    shutil.copyfile(out / "kernel.efi", esp / "kernel.efi")
    shutil.copyfile(out / "initramfs-fixed.cpio", esp / "initramfs.cpio")
    # The folder is an emulated FAT disk. No host block devices or USB passthrough.
    report["tests"]["fixed_uefi"] = run_qemu(["-bios", str(opts.firmware.resolve()),
        "-drive", f"if=none,id=esp,format=raw,readonly=on,file=fat:ro:{esp}", "-device", "virtio-blk-device,drive=esp,bootindex=1"],
        out / "fixed-uefi.log", opts.timeout, "LOCAL-SIM PASS")
    report["tests"]["fixed_uefi"]["uefi_marker"] = "LOCAL-SIM UEFI_PRESENT" in (out / "fixed-uefi.log").read_text(errors="replace")
    if opts.usb_module_dir:
        usb_ramdisk = test_initrd(repaired, opts.usb_module_dir)
        (out / "initramfs-usb-test.cpio").write_bytes(usb_ramdisk)
        shutil.copyfile(out / "initramfs-usb-test.cpio", esp / "initramfs.cpio")
        usb_result = run_qemu(["-bios", str(opts.firmware.resolve()),
            "-drive", f"if=none,id=esp,format=raw,readonly=on,file=fat:ro:{esp}",
            "-device", "virtio-blk-device,drive=esp,bootindex=1"],
            out / "fixed-uefi-usb.log", opts.timeout, "LOCAL-SIM PASS")
        usb_text = (out / "fixed-uefi-usb.log").read_text(errors="replace")
        usb_result["both_directions"] = all(marker in usb_text for marker in
            ("LOCAL-SIM USB_HOST_TO_GADGET_OK", "LOCAL-SIM USB_GADGET_TO_HOST_OK"))
        usb_result["matched"] &= usb_result["both_directions"]
        report["tests"]["fixed_uefi_usb"] = usb_result
        shutil.copyfile(out / "initramfs-fixed.cpio", esp / "initramfs.cpio")
    report["passed"] = (all(test["matched"] for test in report["tests"].values())
                        and report["tests"]["fixed_uefi"]["uefi_marker"])
    (out / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    for name, result in report["tests"].items():
        print(name, "PASS" if result["matched"] else "FAIL", result["seconds"], "s")
        for line in result["markers"]:
            print(" ", line)
    print("Report:", out / "result.json", flush=True)
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
