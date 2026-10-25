#!/usr/bin/env python3
"""Run the LG G8 software checks locally, without accessing any phone."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
DEFAULT_WORK = Path.home() / "Documents/Codex/2026-10-25-lg-g8-omarchy"
DEFAULT_EDK2 = REPO.parent / "lg-g8-sources-20261025/mu_aloha_platforms"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=DEFAULT_WORK)
    parser.add_argument("--edk2-source", type=Path, default=DEFAULT_EDK2)
    parser.add_argument("--boot-image", type=Path)
    parser.add_argument("--kernel-source", type=Path)
    parser.add_argument("--kernel-build", type=Path)
    parser.add_argument("--musl-root", type=Path)
    parser.add_argument("--bootshim-image", type=Path, action="append")
    parser.add_argument("--firmware", type=Path, default=Path("/usr/share/edk2/aarch64/QEMU_EFI.fd"))
    parser.add_argument("--out", type=Path, default=REPO / "out/local-sim")
    args = parser.parse_args()
    work, edk2, out = (p.resolve() for p in (args.workspace, args.edk2_source, args.out))
    out.mkdir(parents=True, exist_ok=True)
    report_path = out / "suite.json"
    report_path.unlink(missing_ok=True)
    boot = (args.boot_image or work / "audit/direct-efi-stub-probe-20261025/lg-g8-direct-efi-stub-probe.img").resolve()
    source = (args.kernel_source or work / "kernel/linux").resolve()
    build = (args.kernel_build or work / "out/kernel-uefi-tiny").resolve()
    musl = (args.musl_root or work / "out/initramfs-alpine").resolve()
    images = args.bootshim_image or [
        work / "audit/usbconfig-stock-v7-20261025/lg-alpha-usbconfig-stock-v7.img",
        work / "audit/uefi-bootcmd-20261025-v1/lg-alpha-bootcmd.img",
    ]
    required_tools = ["qemu-system-aarch64", "clang", "lld-link", "llvm-objcopy", "llvm-objdump", "make", "uv"]
    missing = [name for name in required_tools if not shutil.which(name)]
    if missing:
        parser.error("Missing tools: " + ", ".join(missing))
    for path in [boot, source / "Makefile", build / ".config", build / "arch/arm64/boot/Image",
                 musl / "lib/ld-musl-aarch64.so.1", edk2 / "BootShim/BootShim.S",
                 args.firmware, *images]:
        if not path.is_file():
            parser.error(f"Missing input: {path}")
    report = {"started_at_utc": datetime.now(timezone.utc).isoformat(),
              "scope": "local CPU emulation, QEMU virt Linux/UEFI/virtual USB, native firmware boundary mocks",
              "phone_access": False, "lg_hardware_acceptance": False,
              "steps": [], "passed": False}

    def save() -> None:
        report_path.write_text(json.dumps(report, indent=2) + "\n")

    def run(name: str, command: list[str | Path]) -> bool:
        command = [str(value) for value in command]
        print(f"\n=== {name} ===", flush=True)
        start = time.monotonic()
        result = subprocess.run(command, cwd=REPO)
        report["steps"].append({"name": name, "command": command, "returncode": result.returncode,
                                "seconds": round(time.monotonic() - start, 2)})
        save()
        return result.returncode == 0

    py = sys.executable
    commands = [
        ("virtual-usb-modules", [py, HERE / "build_usb_modules.py", "--kernel-source", source,
                                "--kernel-build", build, "--out", out / "usb-modules"]),
        ("linux", [py, HERE / "run_linux.py", "--boot-image", boot, "--musl-root", musl,
                   "--edk2-source", edk2, "--firmware", args.firmware, "--usb-module-dir", out / "usb-modules",
                   "--out", out / "linux"]),
        ("bootshim", [HERE / "bootshim/run.sh", "--source", edk2 / "BootShim/BootShim.S",
                      "--output-dir", out / "bootshim"]
                     + [value for image in images for value in ("--image", image.resolve())]),
        ("fastboot-control-flow", [py, HERE / "fastboot/run.py", "--source-root", edk2,
                                   "--out", out / "fastboot"]),
    ]
    for name, command in commands:
        if not run(name, command):
            print(f"FAILED {name}; inspect {report_path}", file=sys.stderr)
            return 1
    linux = json.loads((out / "linux/result.json").read_text())
    shim = json.loads((out / "bootshim/report.json").read_text())
    fastboot = json.loads((out / "fastboot/results.json").read_text())
    report["checks"] = {
        "linux": linux["passed"],
        "bootshim": shim["passed"],
        "fastboot_expected_outcomes": all(case["pass"] for case in fastboot["results"]),
        "kernel_matches_input_build": sha(out / "linux/kernel.efi") == sha(build / "arch/arm64/boot/Image"),
    }
    report["counts"] = {
        "linux_boot_scenarios": len(linux["tests"]),
        "bootshim_handoffs": sum(len(image["emulation_cases"]) for image in shim["images"]),
        "bootshim_preflight_rejections": sum(len(image["negative_checks"]) for image in shim["images"]),
        "fastboot_expected_outcomes": len(fastboot["results"]),
    }
    # Export only the repaired runtime. Simulation scripts and USB dummy modules
    # stay in the test archives and never enter this reusable software bundle.
    bundle = out / "software"
    bundle.mkdir(exist_ok=True)
    for src, name in [(out / "linux/kernel.efi", "kernel.efi"),
                      (out / "linux/initramfs-repaired.cpio", "initramfs.cpio")]:
        shutil.copyfile(src, bundle / name)
    manifest = {"source_boot_image_sha256": sha(boot),
                "scope": "kernel and repaired initramfs tested on QEMU virt; LG hardware and ABL unvalidated",
                "simulation_harness_included": False,
                "files": {name: sha(bundle / name) for name in ("kernel.efi", "initramfs.cpio")},
                "correction": linux["correction"]}
    (bundle / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    report["passed"] = all(report["checks"].values())
    report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    report["software_bundle"] = str(bundle)
    save()
    print(f"\n{'PASS' if report['passed'] else 'FAIL'} local software suite: {report_path}")
    print("LG hardware, ABL and Qualcomm USB PHY are not emulated.")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
