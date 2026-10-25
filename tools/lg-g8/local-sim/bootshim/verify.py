#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["unicorn==2.1.4"]
# ///
"""Execute the actual image's AArch64 BootShim, stopping before its FD entry.

The Android/gzip/FDT checks are host-side preflight checks, not emulated ABL
behavior. Only BootShim's CPU instructions and mapped RAM are emulated.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import zlib

from unicorn import Uc, UcError, UC_ARCH_ARM64, UC_HOOK_CODE, UC_HOOK_MEM_WRITE, UC_MODE_ARM
from unicorn import arm64_const as regs


BASE = 0x9FC00000
SIZE = 0x300000
SHIM_SIZE = 112
PAGE = 4096
FDT_BASE = 0xA4000000
RELOCATED_LOAD = 0x88000000
FDT_MAGIC = b"\xd0\x0d\xfe\xed"
ARM64_MAGIC = b"ARM\x64"
SCRIPT_DIR = Path(__file__).resolve().parent
AUDIT_DIR = Path.home() / "Documents/Codex/2026-10-25-lg-g8-omarchy/audit"
DEFAULT_IMAGES = (
    AUDIT_DIR / "usbconfig-stock-v7-20261025/lg-alpha-usbconfig-stock-v7.img",
    AUDIT_DIR / "uefi-bootcmd-20261025-v1/lg-alpha-bootcmd.img",
)
DEFAULT_SOURCE = (
    SCRIPT_DIR.parents[4]
    / "lg-g8-sources-20261025/mu_aloha_platforms/BootShim/BootShim.S"
)


class VerificationError(Exception):
    """An input or handoff failed a required check."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def rounded(size: int) -> int:
    return (size + PAGE - 1) // PAGE * PAGE


@dataclass(frozen=True)
class DecodedKernel:
    raw: bytes
    compressed_size: int
    fdts: tuple[bytes, ...]


@dataclass(frozen=True)
class BootImage:
    path: Path
    image: bytes
    kernel: bytes
    decoded: DecodedKernel
    kernel_address_field: int


def validate_raw(raw: bytes, reference_shim: bytes) -> None:
    require(len(raw) >= 64, "truncated ARM64 header")
    require(raw[56:60] == ARM64_MAGIC, "wrong ARM64 header magic")
    base, size = struct.unpack_from("<QQ", raw, 8)
    require(base == BASE, f"wrong BootShim base: {base:#x}")
    require(size == SIZE, f"wrong BootShim copy size: {size:#x}")
    require(size % 16 == 0, "BootShim copy size must be a multiple of 16")
    require(len(raw) == SHIM_SIZE + size, "truncated or oversized BootShim/FD payload")
    require(raw[:SHIM_SIZE] == reference_shim, "embedded BootShim differs from compiled source")
    fd = raw[SHIM_SIZE:]
    require(fd[40:44] == b"_FVH", "FD does not start with a firmware-volume header")
    require(struct.unpack_from("<Q", fd, 32)[0] == size, "FD firmware-volume size differs from copy size")
    header_size = struct.unpack_from("<H", fd, 48)[0]
    require(56 <= header_size <= size and header_size % 2 == 0, "invalid FD header length")
    require(
        sum(struct.unpack_from(f"<{header_size // 2}H", fd)) & 0xFFFF == 0,
        "FD firmware-volume header checksum mismatch",
    )


def split_fdts(tail: bytes) -> tuple[bytes, ...]:
    fdts = []
    offset = 0
    require(bool(tail), "missing appended FDT")
    while offset < len(tail):
        require(len(tail) - offset >= 40, "truncated appended FDT header")
        require(tail[offset : offset + 4] == FDT_MAGIC, "wrong appended FDT magic")
        header = struct.unpack_from(">10I", tail, offset)
        _, size, struct_offset, strings_offset, reserve_offset, version, last_version, _, strings_size, struct_size = header
        require(40 <= size <= len(tail) - offset, "truncated or invalid appended FDT payload")
        require(version >= 17 and last_version <= 17, "unsupported appended FDT version")
        require(40 <= reserve_offset < size, "invalid FDT reserve-map offset")
        require(40 <= struct_offset <= size and struct_size <= size - struct_offset, "invalid FDT structure range")
        require(40 <= strings_offset <= size and strings_size <= size - strings_offset, "invalid FDT strings range")
        fdts.append(tail[offset : offset + size])
        offset += size
    return tuple(fdts)


def decode_kernel(kernel: bytes, reference_shim: bytes) -> DecodedKernel:
    require(kernel.startswith(b"\x1f\x8b\x08"), "wrong kernel gzip header")
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        # Bound allocation even for corrupt or unexpectedly large inputs.
        raw = decoder.decompress(kernel, SHIM_SIZE + SIZE + 1)
    except zlib.error as exc:
        raise VerificationError(f"invalid gzip stream: {exc}") from exc
    require(decoder.eof, "truncated or oversized gzip stream")
    validate_raw(raw, reference_shim)
    fdts = split_fdts(decoder.unused_data)
    return DecodedKernel(raw, len(kernel) - len(decoder.unused_data), fdts)


def read_image(path: Path, reference_shim: bytes) -> BootImage:
    image = path.read_bytes()
    require(len(image) >= 1632, "truncated Android v0 boot header")
    require(image[:8] == b"ANDROID!", "wrong Android boot header magic")
    kernel_size, kernel_addr, ramdisk_size, _, second_size, _, _, page_size, version, _ = struct.unpack_from(
        "<10I", image, 8
    )
    require(version == 0, f"unsupported Android boot header version {version}")
    require(page_size >= 1632 and page_size & (page_size - 1) == 0, "invalid Android boot page size")
    require(kernel_size > 0 and page_size + kernel_size <= len(image), "truncated Android kernel segment")
    ramdisk_offset = page_size + (kernel_size + page_size - 1) // page_size * page_size
    second_offset = ramdisk_offset + (ramdisk_size + page_size - 1) // page_size * page_size
    require(ramdisk_offset + ramdisk_size <= len(image), "truncated Android ramdisk segment")
    require(second_offset + second_size <= len(image), "truncated Android second segment")
    kernel = image[page_size : page_size + kernel_size]
    return BootImage(path, image, kernel, decode_kernel(kernel, reference_shim), kernel_addr)


def compile_reference(source: Path, output: Path) -> tuple[bytes, dict]:
    require(source.is_file(), f"BootShim source not found: {source}")
    executables = {}
    for name in ("clang", "llvm-objcopy", "llvm-objdump"):
        executables[name] = shutil.which(name)
        require(executables[name] is not None, f"required executable not found: {name}")
    output.mkdir(parents=True, exist_ok=True)
    obj = output / "BootShim.o"
    binary = output / "BootShim.bin"
    command = [
        executables["clang"], "--target=aarch64-none-elf", f"-DUEFI_BASE={BASE:#x}",
        f"-DUEFI_SIZE={SIZE:#x}", "-c", str(source), "-o", str(obj),
    ]
    subprocess.run(command, check=True, capture_output=True, text=True)
    subprocess.run(
        [executables["llvm-objcopy"], "-O", "binary", "--only-section=.text", str(obj), str(binary)],
        check=True, capture_output=True, text=True,
    )
    shim = binary.read_bytes()
    require(len(shim) == SHIM_SIZE, f"compiled BootShim is {len(shim)} bytes, expected {SHIM_SIZE}")
    disassembly = subprocess.run(
        [executables["llvm-objdump"], "-d", str(obj)], check=True, capture_output=True, text=True
    ).stdout
    (output / "BootShim.disassembly.txt").write_text(disassembly)
    compiler_version = subprocess.run(
        [executables["clang"], "--version"], check=True, capture_output=True, text=True
    ).stdout.splitlines()[0]
    return shim, {
        "source": str(source), "source_sha256": sha256(source.read_bytes()),
        "shim_sha256": sha256(shim), "compiler": compiler_version, "command": command,
    }


def emulate(decoded: DecodedKernel, fdt: bytes, in_place: bool) -> dict:
    """Run bytes extracted from the input image; never run the compiled copy."""
    raw = decoded.raw
    fd = raw[SHIM_SIZE:]
    load = BASE - SHIM_SIZE if in_place else RELOCATED_LOAD
    payload_address = load + SHIM_SIZE
    cpu = Uc(UC_ARCH_ARM64, UC_MODE_ARM)

    # Guard pages are mapped and poisoned so an overrun is observable by comparison.
    destination_start = BASE - PAGE
    destination_length = SIZE + 2 * PAGE
    cpu.mem_map(destination_start, destination_length)
    cpu.mem_write(destination_start, b"\xa5" * destination_length)
    if not in_place:
        cpu.mem_map(load, rounded(len(raw)))
    cpu.mem_write(load, raw)
    cpu.mem_map(FDT_BASE, rounded(len(fdt)))
    cpu.mem_write(FDT_BASE, fdt)
    before_guard = bytes(cpu.mem_read(BASE - PAGE, PAGE))
    after_guard = bytes(cpu.mem_read(BASE + SIZE, PAGE))

    xregs = [getattr(regs, f"UC_ARM64_REG_X{index}") for index in range(31)]
    initial = [(0xA500000000000000 + index * 0x01010101010101) & ((1 << 64) - 1) for index in range(31)]
    initial[0] = FDT_BASE
    for register, value in zip(xregs, initial):
        cpu.reg_write(register, value)
    stack_pointer = 0xA6001000
    cpu.reg_write(regs.UC_ARM64_REG_SP, stack_pointer)
    cpu.reg_write(regs.UC_ARM64_REG_NZCV, 0xA0000000)
    reached_entry = False
    in_place_writes = 0

    def entry_hook(emulator: Uc, address: int, instruction_size: int, user_data: object) -> None:
        nonlocal reached_entry
        reached_entry = True
        emulator.emu_stop()

    def write_hook(emulator: Uc, access: int, address: int, size: int, value: int, user_data: object) -> None:
        nonlocal in_place_writes
        in_place_writes += 1

    cpu.hook_add(UC_HOOK_CODE, entry_hook, begin=BASE, end=BASE)
    if in_place:
        cpu.hook_add(UC_HOOK_MEM_WRITE, write_hook, begin=BASE, end=BASE + SIZE - 1)
    # Copy loop is four instructions per 16 bytes plus a small fixed prologue.
    # A wrong loop/branch therefore fails boundedly instead of hanging.
    instruction_limit = 4 * (SIZE // 16) + 32
    try:
        cpu.emu_start(load, 0, timeout=10_000_000, count=instruction_limit)
    except UcError as exc:
        raise VerificationError(f"AArch64 BootShim emulation failed: {exc}") from exc
    require(reached_entry, "BootShim did not reach the FD entry within the instruction/time budget")
    require(cpu.reg_read(regs.UC_ARM64_REG_PC) == BASE, "BootShim branched to the wrong address")
    copied = bytes(cpu.mem_read(BASE, SIZE))
    require(copied == fd, "destination FD is not byte-exact")
    require(bytes(cpu.mem_read(load, len(raw))) == raw, "BootShim modified its source bytes")
    require(bytes(cpu.mem_read(FDT_BASE, len(fdt))) == fdt, "BootShim modified the pointed-to FDT")
    require(bytes(cpu.mem_read(BASE - PAGE, PAGE)) == before_guard, "BootShim overwrote the preceding guard")
    require(bytes(cpu.mem_read(BASE + SIZE, PAGE)) == after_guard, "BootShim overwrote the following guard")

    expected = initial.copy()
    expected[1] = payload_address
    expected[4] = payload_address if in_place else payload_address + SIZE
    expected[5] = BASE
    if in_place:
        require(in_place_writes == 0, "in-place BootShim path unexpectedly wrote to the FD")
    else:
        expected[2], expected[3] = struct.unpack_from("<QQ", fd, SIZE - 16)
        expected[6] = 0
    actual = [cpu.reg_read(register) for register in xregs]
    for index, (wanted, got) in enumerate(zip(expected, actual)):
        require(got == wanted, f"x{index} handoff mismatch: expected {wanted:#x}, got {got:#x}")
    require(cpu.reg_read(regs.UC_ARM64_REG_SP) == stack_pointer, "BootShim changed SP")
    require(cpu.reg_read(regs.UC_ARM64_REG_NZCV) == 0x60000000, "unexpected final NZCV flags")
    return {
        "path": "already-at-destination" if in_place else "relocated-copy",
        "shim_load_address": hex(load), "source_payload_address": hex(payload_address),
        "branch_target": hex(BASE), "fd_bytes_verified": SIZE, "destination_sha256": sha256(copied),
        "fdt_sha256": sha256(fdt), "x0": hex(actual[0]),
        "registers_checked": "x0-x30, SP, PC, NZCV",
        "preserved_registers": "x0, x7-x30, SP" if not in_place else "x0, x2, x3, x6-x30, SP",
        "guards_unchanged": True, "source_unchanged": True, "fdt_unchanged": True,
        "in_place_destination_writes": in_place_writes if in_place else None,
        "instruction_limit": instruction_limit,
    }


def negative_checks(image: BootImage, reference: bytes) -> list[dict]:
    """Malformed inputs must be rejected by host preflight, before emulation."""
    decoded = image.decoded
    wrong_magic = bytearray(decoded.raw)
    wrong_magic[56] ^= 1
    wrong_size = bytearray(decoded.raw)
    struct.pack_into("<Q", wrong_size, 16, SIZE - 16)
    wrong_code = bytearray(decoded.raw)
    wrong_code[0] ^= 1
    cases = [
        ("truncated-fd-one-byte", lambda: validate_raw(decoded.raw[:-1], reference)),
        ("wrong-arm64-header-magic", lambda: validate_raw(bytes(wrong_magic), reference)),
        ("wrong-copy-size-header", lambda: validate_raw(bytes(wrong_size), reference)),
        ("modified-bootshim-instruction", lambda: validate_raw(bytes(wrong_code), reference)),
        ("truncated-gzip-trailer", lambda: decode_kernel(image.kernel[: decoded.compressed_size - 1], reference)),
        ("truncated-final-appended-fdt", lambda: decode_kernel(image.kernel[:-1], reference)),
    ]
    results = []
    for name, run in cases:
        try:
            run()
        except VerificationError as exc:
            results.append({"case": name, "rejected": True, "reason": str(exc), "layer": "host-preflight"})
        else:
            raise VerificationError(f"negative check unexpectedly accepted {name}")
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", action="append", type=Path, help="Android v0 image; repeat to compare images")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="BootShim.S reference to assemble locally")
    parser.add_argument("--output-dir", type=Path, default=SCRIPT_DIR / "out")
    args = parser.parse_args()
    images = args.image if args.image else DEFAULT_IMAGES
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_path = args.output_dir / "report.json"
    # A failed rerun must not leave a previous success presented as current output.
    report_path.unlink(missing_ok=True)
    reference, build = compile_reference(args.source.resolve(), args.output_dir / "reference")
    report = {
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "local AArch64 CPU/RAM BootShim handoff only; no LG hardware, ABL, gzip loader, or UEFI execution",
        "engine": "unicorn==2.1.4", "base": hex(BASE), "copy_size": SIZE,
        "reference": build, "images": [],
    }
    for path in images:
        image = read_image(path.resolve(), reference)
        decoded = image.decoded
        runs = []
        for index, fdt in enumerate(decoded.fdts):
            for in_place in (False, True):
                result = emulate(decoded, fdt, in_place)
                result["appended_fdt_index"] = index
                runs.append(result)
        negatives = negative_checks(image, reference)
        record = {
            "path": str(image.path), "image_sha256": sha256(image.image),
            "image_size": len(image.image), "kernel_size": len(image.kernel),
            "kernel_address_header_field": hex(image.kernel_address_field),
            "gzip_stream_size": decoded.compressed_size, "uncompressed_size": len(decoded.raw),
            "shim_size": SHIM_SIZE, "shim_sha256": sha256(decoded.raw[:SHIM_SIZE]),
            "fd_sha256": sha256(decoded.raw[SHIM_SIZE:]),
            "appended_fdt_sizes": [len(fdt) for fdt in decoded.fdts],
            "emulation_cases": runs, "negative_checks": negatives,
        }
        report["images"].append(record)
        print(
            f"PASS {image.path.name}: {len(runs)} CPU handoffs, {len(negatives)} malformed inputs rejected; "
            f"FD {SIZE:#x} bytes, x0/FDT and register contract verified"
        )
    report["passed"] = True
    report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Report: {report_path}")
    print("Boundary: CPU + RAM handoff only; execution stops before the first UEFI FD instruction.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (VerificationError, OSError, subprocess.CalledProcessError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
            print(exc.stderr, file=sys.stderr)
        raise SystemExit(1)
