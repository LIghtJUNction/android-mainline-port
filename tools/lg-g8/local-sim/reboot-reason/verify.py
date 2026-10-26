#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["unicorn==2.1.4"]
# ///
"""Execute the reboot selector through the IMEM store with a mocked PMIC API."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import struct

from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
from unicorn import arm64_const as regs
from build import KERNEL_SHA256, SELECTOR, PON_SETTER, patched


def reason(kernel: bytes, command: str | None) -> tuple[int, int]:
    cpu = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    cpu.mem_map(0, 64 * 1024 * 1024)
    cpu.mem_write(0, kernel)
    cpu.mem_map(0x5000000, 8192)
    cpu.mem_map(0x6000000, 65536)
    if command is not None:
        cpu.mem_write(0x5000000, command.encode() + b"\0")
    cpu.reg_write(regs.UC_ARM64_REG_X19, 0 if command is None else 0x5000000)
    cpu.reg_write(regs.UC_ARM64_REG_X22, 0x32F1000)
    cpu.mem_write(0x32F1E58, struct.pack("<Q", 0x5001000))
    cpu.reg_write(regs.UC_ARM64_REG_SP, 0x6010000)
    cpu.reg_write(regs.UC_ARM64_REG_PSTATE, 0x3C5)
    values = []

    def stop(machine, address, size, context):
        if address == PON_SETTER:
            values.append(machine.reg_read(regs.UC_ARM64_REG_W0))
        if address == 0xC1E318:
            machine.emu_stop()
        else:
            machine.reg_write(regs.UC_ARM64_REG_W0, 0)
            machine.reg_write(regs.UC_ARM64_REG_PC, machine.reg_read(regs.UC_ARM64_REG_X30))

    cpu.hook_add(UC_HOOK_CODE, stop, begin=PON_SETTER, end=PON_SETTER)
    cpu.hook_add(UC_HOOK_CODE, stop, begin=0x145998, end=0x145998)
    cpu.hook_add(UC_HOOK_CODE, stop, begin=0xC1E318, end=0xC1E318)
    cpu.emu_start(SELECTOR, 0, count=100000)
    if len(values) != 1:
        raise ValueError("selector did not reach the PMIC boundary")
    return values[0], struct.unpack("<I", bytes(cpu.mem_read(0x5001000, 4)))[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    original = args.image.read_bytes()
    fixed = patched(original)
    cases = [("bootloader", (2, 0x77665500), (0x27, 0x77665568)),
             ("bootloader,test", (2, 0x77665500), (0x27, 0x77665568)),
             ("recovery", (1, 0x77665502), (1, 0x77665502)),
             ("rtc", (3, 0x77665503), (3, 0x77665503)),
             ("", (0x20, 0x77665501), (0x20, 0x77665501)),
             (None, (0x20, 0x77665501), (0x20, 0x77665501)),
             ("FOTA LCD off", (0x23, 0x77665560), (0x23, 0x77665560)),
             ("unknown-target", (0x20, 0x77665501), (0x20, 0x77665501))]
    results = []
    for command, before, after in cases:
        actual_before, actual_after = reason(original, command), reason(fixed, command)
        if (actual_before, actual_after) != (before, after):
            raise ValueError(f"{command!r}: got {actual_before}/{actual_after}")
        results.append({"command": command, "stock_reason": actual_before,
                        "patched_reason": actual_after, "passed": True})
    result = {"scope": "actual kernel comparisons and marker stores; PMIC API and IO instrumentation mocked; IMEM store targets emulator RAM",
              "original_kernel_sha256": KERNEL_SHA256, "patched_kernel_sha256": hashlib.sha256(fixed).hexdigest(),
              "patches": [{"offset": 0xC1DE94, "old": "mov w0, #2", "new": "mov w0, #0x27"},
                          {"offset": 0xC1DEB8, "old": "mov w8, #0x5500", "new": "mov w8, #0x5568"}],
              "cases": results, "passed": True}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
