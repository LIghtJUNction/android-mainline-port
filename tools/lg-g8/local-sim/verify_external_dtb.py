#!/usr/bin/env python3
"""Execute standard Shell -> EFI stub -> external DTB on QEMU virt only."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import stat
import subprocess

from run_linux import build_esp, read_cpio, run_qemu, test_initrd, write_cpio

MODEL = "LGG8-QEMU-EXTERNAL-DTB-PROBE"
ASSERT_MODEL = b'''#!/bin/sh
exec >/dev/console 2>&1
actual=$(/bin/busybox tr -d '\\000' </proc/device-tree/model)
if [ "$actual" != "LGG8-QEMU-EXTERNAL-DTB-PROBE" ]; then
    echo "LOCAL-SIM FAIL: external DTB model is $actual"
    /bin/busybox poweroff -f
    exit 1
fi
echo "LOCAL-SIM EXTERNAL_DTB_SELECTED"
exec /bin/busybox sh /sim-assert-base
'''


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel", required=True, type=Path)
    parser.add_argument("--base-initrd", required=True, type=Path)
    parser.add_argument("--modules", required=True, type=Path)
    parser.add_argument("--shell", required=True, type=Path)
    parser.add_argument("--firmware", type=Path, default=Path("/usr/share/edk2/aarch64/QEMU_EFI.fd"))
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    report_path = out / "report.json"
    report_path.unlink(missing_ok=True)
    dtb = out / "qemu-virt-external.dtb"
    command = ["qemu-system-aarch64", "-machine", f"virt,gic-version=3,acpi=off,dumpdtb={dtb}",
               "-cpu", "cortex-a76", "-accel", "tcg", "-m", "512", "-smp", "1",
               "-display", "none", "-serial", "none", "-monitor", "none", "-nic", "none",
               "-bios", str(args.firmware.resolve())]
    dumped = subprocess.run(command, capture_output=True, timeout=20)
    (out / "dumpdtb.log").write_bytes(dumped.stdout + dumped.stderr)
    dumped.check_returncode()
    subprocess.run(["fdtput", "-t", "s", str(dtb), "/", "model", MODEL], check=True)
    entries = read_cpio(test_initrd(args.base_initrd.read_bytes(), args.modules))
    entries["sim-assert-base"] = entries["sim-assert"]
    entries["sim-assert"] = stat.S_IFREG | 0o755, ASSERT_MODEL, 0, 0
    initrd = out / "external-dtb-test.cpio"
    initrd.write_bytes(write_cpio(entries))
    esp = out / "esp"
    manifest = build_esp(args.shell, args.kernel, initrd, esp,
        "console=ttyAMA0,115200 earlycon=pl011,0x09000000 acpi=off rdinit=/sim-init panic=0 loglevel=7", dtb)
    result = run_qemu(["-bios", str(args.firmware.resolve()),
        "-drive", f"if=none,id=esp,format=raw,readonly=on,file=fat:ro:{esp}",
        "-device", "virtio-blk-device,drive=esp,bootindex=1"], out / "serial.log", 90, "LOCAL-SIM PASS")
    serial = (out / "serial.log").read_text(errors="replace")
    checks = {"completed": result["matched"],
              "standard_shell": "LGG8 STANDARD_EFI_SHELL" in serial,
              "external_model_in_running_kernel": "LOCAL-SIM EXTERNAL_DTB_SELECTED" in serial,
              "kernel_reports_external_model": f"Machine model: {MODEL}" in serial,
              "uefi_present": "LOCAL-SIM UEFI_PRESENT" in serial,
              "usb_host_to_gadget": "LOCAL-SIM USB_HOST_TO_GADGET_OK" in serial,
              "usb_gadget_to_host": "LOCAL-SIM USB_GADGET_TO_HOST_OK" in serial}
    report = {"scope": "QEMU virt external-DTB loading mechanism; this is not the LG DTB or LG hardware",
              "qemu_dtb_generation": command, "kernel_sha256": sha(args.kernel),
              "shell_sha256": sha(args.shell), "external_qemu_dtb_sha256": sha(dtb),
              "efi_stub_info_log_present": "Using DTB from command line" in serial,
              "esp": manifest, "run": result, "checks": checks, "passed": all(checks.values())}
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    for name, ok in checks.items():
        print("PASS" if ok else "FAIL", name)
    print("Report:", report_path)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
