#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["unicorn==2.1.4"]
# ///
"""Exercise actual probe instructions; cache coherency and reset firmware are not modeled."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import zlib

from unicorn import (Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE,
                     UC_HOOK_MEM_WRITE, UC_HOOK_MEM_READ, UC_MEM_WRITE)
from unicorn import arm64_const as regs

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("probe_record", HERE / "record.py")
record = importlib.util.module_from_spec(spec)
spec.loader.exec_module(record)


def codec_cases() -> list[str]:
    if record.decode(record.encode(b"abc", 32)) != b"abc":
        raise ValueError("linear record mismatch")
    if record.decode(record.encode(b"", 12)) != b"":
        raise ValueError("empty record mismatch")
    if record.decode(struct.pack("<III", record.SIGNATURE, 2, 4) + b"abcd") != b"cdab":
        raise ValueError("wrapped record mismatch")
    if record.decode(record.encode(b"abcd", 16)) != b"abcd":
        raise ValueError("full record mismatch")
    invalid = [b"", struct.pack("<III", 0, 0, 0),
               struct.pack("<III", record.SIGNATURE, 2, 1) + b"a",
               struct.pack("<III", record.SIGNATURE, 0, 2) + b"a"]
    for zone in invalid:
        try:
            record.decode(zone)
        except ValueError:
            continue
        raise ValueError("invalid header accepted")
    for nonce in ["", "A" * 32, "z" * 32, "0" * 31]:
        try:
            record.template(nonce)
        except ValueError:
            continue
        raise ValueError("invalid nonce accepted")
    def seal(data):
        struct.pack_into("<I", data, 128, zlib.crc32(data[:128]))
        return bytes(data)

    def parse(data, nonce="0" * 32):
        return record.records(data, nonce, target=0xAA000000, stage2_offset=0x200)

    data = bytearray(record.template("0" * 32))
    struct.pack_into("<11Q", data, 40, 1, 0x96000000, 0, 0, 0, 4, 0, 0x140,
                     0x90000000, 0x90000200, 0x90000000)
    first = seal(data)
    struct.pack_into("<Q", data, 40, 2)
    struct.pack_into("<Q", data, 120, 0xAA000000)
    second = seal(data)
    parse(first)
    parse(first + second)
    invalid_records = [(first[:-1], "0" * 32), (first, "1" * 32)]
    # Every individual bit, including the checksum and reserved word, must be detected.
    for index in range(record.RECORD.size * 2 * 8):
        damaged = bytearray(first + second)
        damaged[index // 8] ^= 1 << (index % 8)
        invalid_records.append((bytes(damaged), "0" * 32))
    # Recompute CRC to exercise semantic checks independently of integrity checks.
    for offset, value in [(40, 3), (80, 0), (88, 1), (96, 1), (104, 0x90000002),
                          (112, 0x90000300), (120, 0x80000000AA000000), (48, 0x97000000)]:
        damaged = bytearray(second)
        struct.pack_into("<Q", damaged, offset, value)
        invalid_records.append((first + seal(damaged), "0" * 32))
    for bad, nonce in invalid_records:
        try:
            parse(bad, nonce)
        except ValueError:
            continue
        raise ValueError("invalid probe record accepted")
    return ["linear", "empty", "wrapped", "full", "truncated", "bad-signature",
            "start-over-size", "size-over-capacity", "nonce-and-phase-validation",
            "all-single-bit-corruptions", "captured-state-and-address-validation",
            "cross-record-consistency"]


def execute(image: bytes, manifest: dict, name: str, load: int = 0x90000000,
            el: int = 1, dtb: int = 0x96000000, injected_sctlr: int | None = None,
            expect: str = "target", sctlr: int = 0) -> dict:
    cpu = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    cpu.ctl_set_cpu_model(regs.UC_CPU_ARM64_MAX)
    symbols = manifest["symbols"]
    target, console = manifest["target"], manifest["console"]
    pages = set()
    for low, length in [(load, len(image)), (target, 4096), (console, record.ZONE_SIZE)]:
        for page in range(low & ~4095, (low + length + 4095) & ~4095, 4096):
            if page not in pages:
                cpu.mem_map(page, 4096)
                pages.add(page)
    cpu.mem_write(target, b"\xa5" * 4096)
    cpu.mem_write(console, b"\xcc" * record.ZONE_SIZE)
    cpu.mem_write(load, image)
    original_target = bytes(cpu.mem_read(target, 4096))
    original_console = bytes(cpu.mem_read(console, record.ZONE_SIZE))
    original_code = bytes(cpu.mem_read(load, len(image)))
    inputs = [dtb, 0x123456789ABCDEF0, 0x23456789ABCDEF01, 0x3456789ABCDEF012]
    for index, value in enumerate(inputs):
        cpu.reg_write(getattr(regs, f"UC_ARM64_REG_X{index}"), value)
    cpu.reg_write(regs.UC_ARM64_REG_SP, 0xDEAD0000)
    pstate = 0x140 | (el << 2) | 1
    cpu.reg_write(regs.UC_ARM64_REG_PSTATE, pstate)
    cpu.cpr_write(3, {1: 0, 2: 4, 3: 6}[el], 1, 0, 0, sctlr)
    cpu.cpr_write(3, 3, 0, 0, 1, 0x8444C004)  # CTR_EL0: 64-byte D-cache line.
    stopped, reset_requests, writes = [], [], []
    relocated_halt = target + symbols["target_halt"] - symbols["stage2"]
    relocated_reset = target + symbols["reset_call"] - symbols["stage2"]

    visited, entered_target = [], []

    def instruction(machine, address, size, context):
        if address == target and visited:
            entered_target.append(True)
        visited.append(address)
        if address == load + symbols["state_ready"] and injected_sctlr is not None:
            # Keep address translation disabled; inject only the captured input for rejection tests.
            machine.reg_write(regs.UC_ARM64_REG_X25, injected_sctlr)
        if entered_target and address == relocated_reset and manifest["reset"] == "psci":
            if el == 3:
                raise ValueError(f"{name}: illegal SMC reached at EL3")
            reset_requests.append(machine.reg_read(regs.UC_ARM64_REG_X0))
            machine.reg_write(regs.UC_ARM64_REG_PC, address + 4)
        if address == load + symbols["blocked"] or entered_target and address == relocated_halt:
            stopped.append("blocked" if address == load + symbols["blocked"] else "target")
            machine.emu_stop()

    def memory_access(machine, access, address, size, value, context):
        # Unicorn reports pair accesses as separate elements; check each element.
        if address % size:
            raise ValueError(f"{name}: unaligned {size}-byte access at {address:#x}")
        if access == UC_MEM_WRITE:
            writes.append((address, size))

    cpu.hook_add(UC_HOOK_CODE, instruction)
    cpu.hook_add(UC_HOOK_MEM_WRITE | UC_HOOK_MEM_READ, memory_access)
    cpu.emu_start(load, 0, count=100000)
    if stopped != [expect]:
        raise ValueError(f"{name}: unexpected stop {stopped}")
    if cpu.reg_read(regs.UC_ARM64_REG_SP) != 0xDEAD0000:
        raise ValueError(f"{name}: stack was changed")
    if bytes(cpu.mem_read(load, len(image))) != original_code:
        raise ValueError(f"{name}: source code changed")
    final_console = bytes(cpu.mem_read(console, record.ZONE_SIZE))
    decoded = []
    if expect == "target" or name.startswith("target-"):
        decoded = record.records(record.decode(final_console), manifest["nonce"],
                                 target=target, stage2_offset=symbols["stage2"])
        phases = [1, 2] if expect == "target" else [1]
        if [item["phase"] for item in decoded] != phases:
            raise ValueError(f"{name}: incorrect phase sequence")
        for item in decoded:
            expected = dict(zip(record.FIELDS, [item["phase"], *inputs, el * 4, sctlr,
                                               pstate & 0x3C0, load, load + symbols["stage2"],
                                               target if item["phase"] == 2 else load]))
            if item != expected:
                raise ValueError(f"{name}: incorrect captured state: {item}, expected {expected}")
        extent = record.HEADER.size + record.RECORD.size * len(decoded)
        if final_console[extent:] != original_console[extent:]:
            raise ValueError(f"{name}: console tail modified")
    elif final_console != original_console:
        raise ValueError(f"{name}: console changed on rejection")
    if expect == "target":
        payload = image[symbols["stage2"]:symbols["stage2_end"]]
        if bytes(cpu.mem_read(target, len(payload))) != payload:
            raise ValueError(f"{name}: relocated bytes mismatch")
        if bytes(cpu.mem_read(target + len(payload), 4096 - len(payload))) != original_target[len(payload):]:
            raise ValueError(f"{name}: target guard changed")
    elif bytes(cpu.mem_read(target, 4096)) != original_target:
        raise ValueError(f"{name}: target changed on rejection")
    console_extent = record.HEADER.size + record.RECORD.size * len(decoded)
    for address, size in writes:
        if not (decoded and console <= address and address + size <= console + console_extent
                or expect == "target" and target <= address and address + size <= target + len(payload)):
            raise ValueError(f"{name}: write outside expected ranges at {address:#x}")
    expected_reset = [0x84000009] if expect == "target" and manifest["reset"] == "psci" else []
    if reset_requests != expected_reset:
        raise ValueError(f"{name}: unexpected reset request {reset_requests}")
    return {"name": name, "el": el, "load": hex(load), "phases": [item["phase"] for item in decoded],
            "stop": stopped[0], "smc_requests": reset_requests,
            "sctlr": sctlr, "sctlr_injected_for_rejection": injected_sctlr,
            "natural_alignment_checked": True, "passed": True}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("build", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads((args.build / "manifest.json").read_text())
    image = (args.build / "probe.Image").read_bytes()
    if hashlib.sha256(image).hexdigest() != manifest["image_sha256"]:
        raise ValueError("image hash differs from manifest")
    cases = [execute(image, manifest, f"el{el}", el=el,
                     expect="blocked" if el == 3 and manifest["reset"] == "psci" else "target")
             for el in (1, 2, 3)]
    cases += [execute(image, manifest, f"alignment-check-el{el}", el=el, sctlr=2,
                      expect="blocked" if el == 3 and manifest["reset"] == "psci" else "target")
              for el in (1, 2, 3)]
    cases += [execute(image, manifest, "word-aligned-source", load=0x90000004, sctlr=2),
              execute(image, manifest, "doubleword-aligned-source", load=0x90000008, sctlr=2),
              execute(image, manifest, "relocated-source", load=0x92001000),
              execute(image, manifest, "mmu-on", injected_sctlr=1, expect="blocked"),
              execute(image, manifest, "big-endian", injected_sctlr=1 << 25, expect="blocked"),
              execute(image, manifest, "console-code-overlap", load=record.CONSOLE, expect="blocked"),
              execute(image, manifest, "console-dtb-overlap", dtb=record.CONSOLE, expect="blocked"),
              execute(image, manifest, "code-dtb-overlap", dtb=0x90000000, expect="blocked"),
              execute(image, manifest, "dtb-range-overflow", dtb=0xFFFFFFFFFFF00000, expect="blocked"),
              execute(image, manifest, "target-code-overlap", load=manifest["target"], expect="blocked"),
              execute(image, manifest, "target-dtb-overlap", dtb=manifest["target"], expect="blocked")]
    result = {"scope": "actual ARM64 instructions and independent ecc=0 codec; cache instructions have no hardware coherency model; SMC intercepted; MMU/EE rejection inputs injected after MRS",
              "image_sha256": manifest["image_sha256"], "codec_cases": codec_cases(),
              "cases": cases, "passed": True}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x") as stream:
        stream.write(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"passed": True, "cpu_cases": len(cases), "codec_groups": len(result["codec_cases"]),
                      "image_sha256": manifest["image_sha256"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
