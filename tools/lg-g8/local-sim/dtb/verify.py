#!/usr/bin/env python3
"""Compile the Alpha board patch and compare its RAM contract with clean runtime FDT."""
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

HERE = Path(__file__).resolve().parent
BOARD = Path("arch/arm64/boot/dts/qcom/sm8150-lg-alphalm-board.dts")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree(path: Path) -> dict[str, dict[str, bytes]]:
    blob = path.read_bytes()
    library = ctypes.util.find_library("fdt")
    if not library:
        raise RuntimeError("libfdt is required for complete FDT validation")
    lib = ctypes.CDLL(library)
    lib.fdt_check_full.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    lib.fdt_check_full.restype = ctypes.c_int
    result = lib.fdt_check_full(ctypes.create_string_buffer(blob), len(blob))
    if result:
        raise ValueError(f"{path}: libfdt fdt_check_full={result}; provide a validated, clean FDT")
    header = struct.unpack_from(">10I", blob)
    strings = blob[header[3]:header[3] + header[8]]
    cursor, stack, nodes = header[2], [], {}
    while cursor < header[2] + header[9]:
        token = struct.unpack_from(">I", blob, cursor)[0]
        cursor += 4
        if token == 1:
            end = blob.index(b"\0", cursor)
            stack.append(blob[cursor:end].decode())
            cursor = (end + 4) & ~3
            nodes["/" + "/".join(stack[1:])] = {}
        elif token == 2:
            stack.pop()
        elif token == 3:
            size, offset = struct.unpack_from(">II", blob, cursor)
            cursor += 8
            name = strings[offset:strings.index(b"\0", offset)].decode()
            nodes["/" + "/".join(stack[1:])][name] = blob[cursor:cursor + size]
            cursor = (cursor + size + 3) & ~3
        elif token == 4:
            pass
        elif token == 9:
            return nodes
        else:
            raise ValueError(f"unexpected token {token}")
    raise ValueError("FDT_END missing")


def ranges(value: bytes) -> list[tuple[int, int]]:
    if len(value) % 16:
        raise ValueError("expected 64-bit address and size cells")
    return [(address, address + size) for address, size in struct.iter_unpack(">QQ", value) if size]


def reserved(nodes: dict) -> list[tuple[str, int, int]]:
    return [(name, start, end) for name, props in nodes.items()
            if name.startswith("/reserved-memory/") and name.count("/") == 2
            and "reg" in props and "reusable" not in props
            for start, end in ranges(props["reg"])]


def memreserve(path: Path) -> list[tuple[str, int, int]]:
    blob = path.read_bytes()
    cursor = struct.unpack_from(">I", blob, 16)[0]
    result = []
    while True:
        address, size = struct.unpack_from(">QQ", blob, cursor)
        cursor += 16
        if not address and not size:
            return result
        if size:
            result.append((f"FDT memreserve[{len(result)}]", address, address + size))


def gaps(start: int, end: int, coverage: list[tuple[str, int, int]]) -> list[tuple[int, int]]:
    cursor, missing = start, []
    for _, low, high in sorted(coverage, key=lambda entry: entry[1]):
        if high <= cursor or low >= end:
            continue
        if low > cursor:
            missing.append((cursor, min(low, end)))
        cursor = max(cursor, high)
        if cursor >= end:
            break
    if cursor < end:
        missing.append((cursor, end))
    return missing


def overlaps(intervals: list[tuple[str, int, int]]) -> list[dict]:
    result = []
    for index, (name, start, end) in enumerate(intervals):
        for other, low, high in intervals[index + 1:]:
            if max(start, low) < min(end, high):
                result.append({"nodes": [name, other], "start": hex(max(start, low)),
                               "end": hex(min(end, high))})
    return result


def missing(runtime: list, candidate: list) -> list[dict]:
    return [{"runtime_node": name, "start": hex(low), "end": hex(high), "size": hex(high - low)}
            for name, start, end in runtime for low, high in gaps(start, end, candidate)]


def memory(nodes: dict) -> list[tuple[int, int]]:
    memories = [props for name, props in nodes.items()
                if name.count("/") == 1 and props.get("device_type") == b"memory\0"]
    if len(memories) != 1:
        raise ValueError(f"expected one memory node, found {len(memories)}")
    return sorted(ranges(memories[0]["reg"]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel-source", required=True, type=Path)
    parser.add_argument("--runtime", required=True, type=Path)
    parser.add_argument("--baseline-dtb", type=Path)
    parser.add_argument("--out", type=Path, default=HERE / "out")
    args = parser.parse_args()
    source, out = args.kernel_source.resolve(), args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    report_path = out / "report.json"
    report_path.unlink(missing_ok=True)
    before_sha = sha(source / BOARD)
    work = out / "work"
    (work / BOARD.parent).mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source / BOARD, work / BOARD)
    patch = subprocess.run(["patch", "--batch", "-p1", "-d", str(work), "-i", str(HERE / "align-alphalm-runtime.patch")],
                           capture_output=True, text=True)
    (out / "patch.log").write_text(patch.stdout + patch.stderr)
    patch.check_returncode()

    def compile_dtb(input_path: Path, stem: str) -> Path:
        preprocessor = ["clang", "-E", "-P", "-x", "assembler-with-cpp", "-nostdinc", "-undef", "-D__DTS__"]
        for directory in [source / BOARD.parent, source / "include", source / "scripts/dtc/include-prefixes"]:
            preprocessor += ["-I", str(directory)]
        cpp = subprocess.run(preprocessor + [str(input_path)], capture_output=True, check=True)
        (out / (stem + ".preprocessed.dts")).write_bytes(cpp.stdout)
        dtb = out / (stem + ".dtb")
        result = subprocess.run(["dtc", "-I", "dts", "-O", "dtb", "-o", str(dtb), "-"],
                                input=cpp.stdout, capture_output=True)
        (out / (stem + ".dtc.stderr")).write_bytes(result.stderr)
        result.check_returncode()
        return dtb

    baseline = compile_dtb(source / BOARD, "before")
    patched = compile_dtb(work / BOARD, "patched")
    runtime, before, after = (tree(path) for path in [args.runtime, baseline, patched])
    runtime_ranges, before_ranges, after_ranges = (reserved(nodes) for nodes in [runtime, before, after])
    runtime_ranges += memreserve(args.runtime)
    before_ranges += memreserve(baseline)
    after_ranges += memreserve(patched)
    usb1 = "/soc@0/usb@a6f8800"
    usb2 = "/soc@0/usb@a8f8800"
    checks = {
        "external_source_unchanged": before_sha == sha(source / BOARD),
        "board_id_matches_runtime": after["/"]["qcom,board-id"] == runtime["/"]["qcom,board-id"],
        "memory_ranges_match_runtime": memory(after) == memory(runtime),
        "all_runtime_fixed_reservations_covered": not missing(runtime_ranges, after_ranges),
        "candidate_has_no_reserved_overlaps": not overlaps(after_ranges),
        "usb1_enabled": after[usb1]["status"] == b"okay\0",
        "usb1_peripheral": after[usb1 + "/usb@a600000"]["dr_mode"] == b"peripheral\0",
        "usb2_disabled": after[usb2]["status"] == b"disabled\0",
        "psci_smc": after["/psci"]["method"] == runtime["/psci"]["method"] == b"smc\0",
        "cmd_db_preserved": after["/reserved-memory/memory@85f20000"] == before["/reserved-memory/memory@85f20000"],
    }
    if args.baseline_dtb:
        checks["compiled_baseline_matches_existing_dtb"] = baseline.read_bytes() == args.baseline_dtb.read_bytes()
    report = {
        "scope": "static DTB grammar and runtime RAM/firmware reservation contract; no device or hardware execution",
        "inputs": {"source": str(source / BOARD), "source_sha256": before_sha,
                   "runtime": str(args.runtime.resolve()), "runtime_sha256": sha(args.runtime),
                   "patch_sha256": sha(HERE / "align-alphalm-runtime.patch")},
        "before_sha256": sha(baseline), "patched_sha256": sha(patched),
        "libfdt_check_full": {"runtime": 0, "before": 0, "patched": 0},
        "checks": checks, "passed": all(checks.values()),
        "runtime_fixed_nodes": [{"node": n, "base": hex(s), "size": hex(e - s)} for n, s, e in runtime_ranges],
        "patched_fixed_nodes": [{"node": n, "base": hex(s), "size": hex(e - s)} for n, s, e in after_ranges],
        "missing_before": missing(runtime_ranges, before_ranges),
        "missing_after": missing(runtime_ranges, after_ranges),
        "runtime_overlaps": overlaps(runtime_ranges),
        "candidate_overlaps_before": overlaps(before_ranges),
        "candidate_overlaps_after": overlaps(after_ranges),
        "dynamic_or_reusable_runtime_nodes": [name for name, props in runtime.items()
            if name.startswith("/reserved-memory/") and name.count("/") == 2
            and ("size" in props and "reg" not in props or "reusable" in props)],
        "memory_ranges": [{"base": hex(s), "size": hex(e - s)} for s, e in memory(after)],
    }
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    print("PASS" if report["passed"] else "FAIL", "static Alpha DTB contract", patched)
    print("SHA256", report["patched_sha256"])
    print("missing fixed ranges before/after", len(report["missing_before"]), len(report["missing_after"]))
    for name, ok in checks.items():
        print("PASS" if ok else "FAIL", name)
    print("Report:", report_path)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
