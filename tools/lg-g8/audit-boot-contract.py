#!/usr/bin/env python3
"""Audit explicit Android v2 boot/FDT inputs offline; never establishes bootability."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import struct

HERE = Path(__file__).resolve().parent
HELPER = HERE / "local-sim/dtb/verify.py"
spec = importlib.util.spec_from_file_location("boot_contract_dtb_helpers", HELPER)
dtb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dtb)
U64_LIMIT = 1 << 64


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def span(start: int, size: int) -> tuple[int, int]:
    require(0 <= start < U64_LIMIT and 0 < size < U64_LIMIT
            and start + size <= U64_LIMIT, "empty or overflowing physical interval")
    return start, start + size


def align(value: int, page: int) -> int:
    return (value + page - 1) // page * page


def parse_boot(blob: bytes) -> dict:
    require(len(blob) >= 1660, "truncated Android v2 header")
    require(blob[:8] == b"ANDROID!", "wrong Android boot magic")
    fields = struct.unpack_from("<9I", blob, 8)
    ks, ka, rs, ra, ss, sa, tags, page, version = fields
    require(version == 2, "only Android boot header v2 is supported")
    require(struct.unpack_from("<I", blob, 1644)[0] == 1660, "wrong v2 header_size")
    require(page in (2048, 4096, 8192, 16384), "unsupported boot page size")
    recovery_size, recovery_offset = struct.unpack_from("<IQ", blob, 1632)
    require(not ss and not sa, "second-stage payload/address is unsupported")
    require(not recovery_size and not recovery_offset, "recovery_dtbo is unsupported")
    ds, da = struct.unpack_from("<IQ", blob, 1648)
    require(ks > 0 and ds > 0, "kernel and DTB sections must be nonempty")
    offset, sections = page, {}
    for name, size, address in (("kernel", ks, ka), ("ramdisk", rs, ra), ("dtb", ds, da)):
        require(offset + size <= len(blob), f"truncated {name} section")
        require(offset + align(size, page) <= len(blob), f"truncated {name} page padding")
        sections[name] = {"offset": offset, "size": size, "header_address": address}
        offset += align(size, page)
    # Partition padding and AVB metadata are retained as opaque bytes, never verified here.
    return {"version": version, "page_size": page, "sections": sections,
            "payload_end": offset, "uninterpreted_trailing_bytes": len(blob) - offset,
            "tags_address": tags}


class FdtBytes:
    """Give the existing fully validated parser an immutable input snapshot."""
    def __init__(self, blob: bytes):
        self.blob = blob

    def read_bytes(self) -> bytes:
        return self.blob

    def __str__(self) -> str:
        return "explicit FDT input"


def validated_tree(blob: bytes) -> dict:
    require(len(blob) >= 40, "truncated FDT header")
    require(blob[:4] == b"\xd0\x0d\xfe\xed", "wrong FDT magic")
    require(struct.unpack_from(">I", blob, 4)[0] == len(blob),
            "FDT totalsize must equal input length; select one exact DTB")
    require(struct.unpack_from(">I", blob, 20)[0] == 17,
            "only project FDT v17 inputs are supported")
    nodes = dtb.tree(FdtBytes(blob))  # Includes mandatory libfdt fdt_check_full.
    # libfdt validates structure, but accepts duplicate node/property names.
    # The reused dict parser would silently discard those earlier definitions.
    header = struct.unpack_from(">10I", blob)
    strings = blob[header[3]:header[3] + header[8]]
    cursor, stack, seen, properties = header[2], [], set(), {}
    while True:
        token = struct.unpack_from(">I", blob, cursor)[0]
        cursor += 4
        if token == 1:
            end = blob.index(b"\0", cursor)
            stack.append(blob[cursor:end])
            path = tuple(stack)
            require(path not in seen, "duplicate FDT node path")
            seen.add(path)
            properties[path] = set()
            cursor = align(end + 1, 4)
        elif token == 2:
            stack.pop()
        elif token == 3:
            size, offset = struct.unpack_from(">II", blob, cursor)
            name = strings[offset:strings.index(b"\0", offset)]
            names = properties[tuple(stack)]
            require(name not in names, "duplicate FDT property name")
            names.add(name)
            cursor = align(cursor + 8 + size, 4)
        elif token == 9:
            break
        else:
            require(token == 4, "unexpected validated FDT token")
    return nodes


def embedded_fdts(blob: bytes) -> list[bytes]:
    cursor, result = 0, []
    while cursor < len(blob):
        require(cursor + 40 <= len(blob), "truncated concatenated DTB header")
        size = struct.unpack_from(">I", blob, cursor + 4)[0]
        require(40 <= size <= len(blob) - cursor, "invalid concatenated DTB length")
        item = blob[cursor:cursor + size]
        validated_tree(item)
        result.append(item)
        cursor += size
    return result


def cell_count(props: dict, key: str, default: int) -> int:
    value = props.get(key, struct.pack(">I", default))
    require(len(value) == 4, f"invalid {key}")
    count = int.from_bytes(value, "big")
    require(count in (1, 2), f"unsupported {key}; expected 1 or 2")
    return count


def reg_ranges(value: bytes, address_cells: int, size_cells: int,
               allow_empty: bool = False) -> list[tuple[int, int]]:
    width = 4 * (address_cells + size_cells)
    require(bool(value) and len(value) % width == 0, "invalid reg tuple length")
    result = []
    for offset in range(0, len(value), width):
        address = int.from_bytes(value[offset:offset + 4 * address_cells], "big")
        size = int.from_bytes(value[offset + 4 * address_cells:offset + width], "big")
        if size == 0 and allow_empty:
            continue
        result.append(span(address, size))
    return result


def active(nodes: dict, path: str) -> bool:
    while True:
        status = nodes[path].get("status", b"okay\0")
        require(status in (b"okay\0", b"ok\0", b"disabled\0", b"reserved\0", b"fail\0")
                or status.startswith(b"fail-"), "unsupported FDT status")
        if status not in (b"okay\0", b"ok\0"):
            return False
        if path == "/":
            return True
        path = path.rsplit("/", 1)[0] or "/"


def fdt_contract(blob: bytes) -> dict:
    nodes = validated_tree(blob)
    ac = cell_count(nodes["/"], "#address-cells", 2)
    sc = cell_count(nodes["/"], "#size-cells", 1)
    ram, reserved, permanent, uncertain = [], [], [], []
    ram_complete = True
    # These Linux properties change the effective RAM map. Do not certify the
    # broader reg map until the applicable kernel/config policy is modeled.
    for props in nodes.values():
        require(not ({"linux,usable-memory", "linux,usable-memory-range"} & props.keys()),
                "Linux usable-memory restrictions are unsupported")
    for path, props in nodes.items():
        if props.get("device_type") == b"memory\0" and active(nodes, path):
            require(path.count("/") == 1, "nested/translated memory nodes unsupported")
            require("reg" in props, "memory node lacks reg")
            entries = reg_ranges(props["reg"], ac, sc, allow_empty=True)
            ram += [(path, low, high) for low, high in entries]
            if len(entries) * 4 * (ac + sc) != len(props["reg"]):
                ram_complete = False
                uncertain.append(f"{path}: zero-size RAM placeholder requires bootloader fixup")
    if not ram:
        ram_complete = False
        uncertain.append("no concrete enabled RAM ranges; RAM coverage cannot be established")
    parent = nodes.get("/reserved-memory")
    if parent is not None:
        require(active(nodes, "/reserved-memory"),
                "disabled reserved-memory parent has kernel-dependent semantics; unsupported")
        require("ranges" in parent and parent["ranges"] == b"",
                "reserved-memory requires explicit empty ranges (no translation)")
        require("#address-cells" in parent and "#size-cells" in parent,
                "reserved-memory requires explicit address and size cells")
        rac = cell_count(parent, "#address-cells", ac)
        rsc = cell_count(parent, "#size-cells", sc)
        require((rac, rsc) == (ac, sc), "reserved-memory cells must match root")
        for path, props in nodes.items():
            if not path.startswith("/reserved-memory/") or not active(nodes, path):
                continue
            require(path.count("/") == 2, "nested reserved-memory nodes unsupported")
            for flag in ("no-map", "reusable"):
                require(flag not in props or props[flag] == b"", f"{flag} must be an empty boolean")
            require(not ("no-map" in props and "reusable" in props),
                    "reserved-memory no-map and reusable are mutually exclusive")
            if "reg" in props:
                # Linux versions differ on support for multiple static tuples;
                # never credit later tuples as guaranteed firmware protection.
                ranges = reg_ranges(props["reg"], rac, rsc)
                require(len(ranges) == 1, "multiple reserved-memory reg tuples are unsupported")
                # Include reusable/CMA too: it is still reserved during early boot.
                kind = "reusable" if "reusable" in props else "fixed"
                entries = [(f"{path} [{kind}]", low, high) for low, high in ranges]
                reserved += entries
                if kind == "fixed":
                    permanent += entries
            else:
                uncertain.append(f"{path}: dynamic or unspecified reservation; allocation unknown")
    # The tree parser has already validated the full reserve map including its terminator.
    offset = struct.unpack_from(">I", blob, 16)[0]
    index = 0
    while True:
        address, size = struct.unpack_from(">QQ", blob, offset)
        offset += 16
        if not address and not size:
            break
        if size:
            low, high = span(address, size)
            entry = (f"FDT memreserve[{index}]", low, high)
            reserved.append(entry)
            permanent.append(entry)
        index += 1
    return {"ram": ram, "ram_complete": ram_complete, "reserved": reserved,
            "permanent": permanent, "unknowns": uncertain}


def image_header(payload: bytes) -> dict:
    compressed = ((b"\x1f\x8b", "gzip"), (b"\xfd7zXZ\0", "xz"),
                  (b"\x04\x22\x4d\x18", "lz4"), (b"\x02\x21\x4c\x18", "lz4-legacy"),
                  (b"\x28\xb5\x2f\xfd", "zstd"))
    for magic, name in compressed:
        if payload.startswith(magic):
            return {"format": name, "runtime_size": None,
                    "unknowns": ["compressed kernel: decoded Image, decompression destination, "
                                 "runtime size and scratch/staging ranges unknown; not decompressed"]}
    if len(payload) < 64 or payload[56:60] != b"ARM\x64":
        return {"format": "unknown", "runtime_size": None,
                "unknowns": ["kernel is not a recognized raw arm64 Image; execution span unknown"]}
    text_offset, size, flags = struct.unpack_from("<QQQ", payload, 8)
    require(size == 0 or size >= 64, "arm64 Image image_size is shorter than its header")
    unknowns = []
    if size == 0:
        # Pre-v3.17 text_offset has unspecified endianness; the boot protocol
        # specifies the effective legacy offset instead of decoding it as LE.
        text_offset = 0x80000
        unknowns.append("legacy arm64 Image image_size=0: runtime size unknown; text_offset assumed 0x80000")
    if flags & ~0xF:
        unknowns.append("arm64 Image flags contain unhandled bits")
    if flags & 1:
        unknowns.append("big-endian Image: platform/bootloader support unverified")
    if not flags & 8:
        unknowns.append("Image requests placement near the start of RAM; bootloader compliance unverified")
    return {"format": "arm64-Image", "text_offset": text_offset, "runtime_size": size or None,
            "flags": flags, "unknowns": unknowns}


def intervals_json(intervals: list) -> list[dict]:
    return [{"name": name, "start": hex(low), "end": hex(high), "size": high - low}
            for name, low, high in intervals]


def uncovered(intervals: list, coverage: list) -> list[dict]:
    return intervals_json([(name, low, high) for name, start, end in intervals
                           for low, high in dtb.gaps(start, end, coverage)])


def audit(boot_blob: bytes, candidate_blob: bytes, references: list[bytes],
          addresses: dict | None = None) -> dict:
    require(bool(references), "at least one reference FDT is required")
    header = parse_boot(boot_blob)
    sections = header["sections"]
    embedded = embedded_fdts(boot_blob[sections["dtb"]["offset"]:
                                        sections["dtb"]["offset"] + sections["dtb"]["size"]])
    candidate = fdt_contract(candidate_blob)
    refs = [fdt_contract(blob) for blob in references]
    kernel = sections["kernel"]
    image = image_header(boot_blob[kernel["offset"]:kernel["offset"] + kernel["size"]])
    unknowns = [
        "Addresses are a declared load scenario, not observed ABL placement; relocation and actual x0 FDT unknown",
        "Bootloader selection, overlays, FDT fixups/growth and decompression workspace are not modeled",
        "RAM/reservations describe supplied trees, not independent hardware validation",
        "Kernel build configuration and effective cmdline (including mem=/memmap= limits) are not audited",
        "Framebuffer and other live firmware buffers outside explicit reservation entries are not modeled",
        "Reservation union checks address coverage; no-map, access permissions and firmware ownership are not certified",
        "Initramfs unpacking allocations, firmware DMA lifetimes and dynamic reservations remain unproven",
        "AVB/authentication, instruction execution, drivers, recovery and bootability are not tested",
    ] + image["unknowns"]
    matches = [i for i, blob in enumerate(embedded) if blob == candidate_blob]
    unknowns.append("An exact embedded DTB match does not establish bootloader selection" if matches else
                    "Candidate DTB is external to boot DTB section; this is a prospective layout scenario")
    issues, loads, runtime_loads, provenance = [], [], [], {}
    for name, section in sections.items():
        if not section["size"]:
            continue
        address = (addresses or {}).get(name, section["header_address"])
        provenance[name] = {"address": hex(address), "source": "explicit scenario override" if
                            name in (addresses or {}) else "Android header field (hypothesis only)"}
        # An external scenario may supply a DTB larger than the original table.
        # Retain the whole original table footprint as a conservative lower bound.
        size = max(section["size"], len(candidate_blob)) if name == "dtb" else section["size"]
        low, high = span(address, size)
        loads.append((name + " payload", low, high))
        if name == "kernel" and image["runtime_size"] is not None:
            low, high = span(address, image["runtime_size"])
            runtime_loads.append(("kernel Image runtime", low, high))
        if name == "kernel" and image["format"] == "arm64-Image":
            text = image["text_offset"]
            if address < text or (address - text) % 0x200000:
                issues.append({"check": "arm64 Image placement", "detail":
                               "kernel address minus text_offset is not a nonnegative 2 MiB aligned base"})
    # Compare the maximum file/runtime footprint for each component, without treating
    # its own payload and runtime as an inter-component collision.
    extents = list(loads)
    if runtime_loads:
        name, low, high = runtime_loads[0]
        extents = [(n, s, max(e, high)) if n == "kernel payload" else (n, s, e)
                   for n, s, e in extents]
        extents[0] = ("kernel file/runtime union", extents[0][1], extents[0][2])
    if int(provenance["dtb"]["address"], 16) % 8:
        issues.append({"check": "arm64 DTB alignment", "detail": "DTB address must be 8-byte aligned"})
    if len(candidate_blob) > 0x200000:
        issues.append({"check": "arm64 DTB size", "detail": "selected DTB exceeds 2 MiB"})
    if runtime_loads and sections["ramdisk"]["size"]:
        # Both Image and initrd must fit a common 1 GiB aligned 32 GiB window.
        window_loads = [item for item in extents if item[0] != "dtb payload"]
        window_base = min(start for _, start, _ in window_loads) // 0x40000000 * 0x40000000
        if max(end for _, _, end in window_loads) > window_base + 0x800000000:
            issues.append({"check": "arm64 initrd window", "detail":
                           "kernel and initrd do not fit a 1 GiB aligned window of up to 32 GiB"})
    collisions = dtb.overlaps(extents)
    if collisions:
        issues.append({"check": "pairwise load overlap", "ranges": collisions})
    contracts = [("candidate", candidate)] + [(f"reference[{i}]", ref) for i, ref in enumerate(refs)]
    reports = []
    for label, contract in contracts:
        unknowns += [f"{label}: {item}" for item in contract["unknowns"]]
        outside = uncovered(extents, contract["ram"]) if contract["ram_complete"] else None
        reserved_hits = []
        for load, start, end in extents:
            for reservation, low, high in contract["reserved"]:
                if max(start, low) < min(end, high):
                    reserved_hits.append({"load": load, "reservation": reservation,
                                          "start": hex(max(start, low)), "end": hex(min(end, high))})
        if outside:
            issues.append({"check": f"load outside {label} RAM", "ranges": outside})
        if reserved_hits:
            issues.append({"check": f"load intersects {label} reservation", "ranges": reserved_hits})
        reports.append({"label": label, "ram": intervals_json(contract["ram"]),
                        "ram_coverage_known": contract["ram_complete"],
                        "reserved": intervals_json(contract["reserved"]),
                        "reserved_overlaps": dtb.overlaps(contract["reserved"]),
                        "loads_outside_ram": outside, "load_reserved_intersections": reserved_hits})
    for i, reference in enumerate(refs):
        missing = uncovered(reference["reserved"], candidate["reserved"])
        permanent_missing = uncovered(reference["permanent"], candidate["permanent"])
        ram_added = uncovered(candidate["ram"], reference["ram"]) if reference["ram_complete"] else None
        ram_missing = uncovered(reference["ram"], candidate["ram"]) if candidate["ram_complete"] else None
        reports[i + 1].update({"reservations_missing_from_candidate": missing,
                              "permanent_reservations_missing_from_candidate": permanent_missing,
                              "candidate_ram_outside_reference": ram_added,
                              "reference_ram_missing_from_candidate": ram_missing})
        if missing:
            issues.append({"check": f"reference[{i}] reservation union not covered", "ranges": missing})
        if permanent_missing:
            issues.append({"check": f"reference[{i}] permanent reservation union not covered by permanent candidate regions",
                           "ranges": permanent_missing})
        if ram_added:
            issues.append({"check": f"candidate exposes RAM outside reference[{i}]", "ranges": ram_added})
        if ram_missing:
            unknowns.append(f"candidate omits some reference[{i}] RAM; intentional restriction unverified")
    return {"schema_version": 1, "scope": "offline Android v2 declared memory layout; no device access",
            "status": "violations" if issues else "incomplete", "known_range_checks_passed": not issues,
            "bootability": "not established", "boot_header": header, "arm64_image": image,
            "address_provenance": provenance, "payload_loads": intervals_json(loads),
            "image_runtime_loads": intervals_json(runtime_loads), "checked_load_unions": intervals_json(extents),
            "pairwise_load_overlaps": collisions, "embedded_dtb_sha256": [sha(b) for b in embedded],
            "candidate_matches_embedded_indices": matches,
            "fdt_validation": "libfdt fdt_check_full=0 for every supplied and embedded DTB",
            "contracts": reports, "issues": issues, "unknowns": unknowns}


def number(value: str) -> int:
    result = int(value, 0)
    require(0 <= result < U64_LIMIT, "address must fit uint64")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--boot", required=True, type=Path)
    parser.add_argument("--candidate-dtb", required=True, type=Path,
                        help="one exact DTB; external selection is reported as a hypothetical scenario")
    parser.add_argument("--reference-fdt", required=True, action="append", type=Path,
                        help="validated stock/runtime FDT; repeat to check every reference")
    parser.add_argument("--expect-reference-sha256", action="append", default=[],
                        help="optional hashes in reference order; if supplied, one per reference")
    for section in ("kernel", "ramdisk", "dtb"):
        parser.add_argument(f"--{section}-load", type=number, help="explicit hypothetical physical address")
    parser.add_argument("--report", required=True, type=Path, help="new JSON file; existing evidence is never replaced")
    args = parser.parse_args()
    sources, inputs = {}, {}
    try:
        paths = [("boot", args.boot), ("candidate_dtb", args.candidate_dtb)]
        paths += [(f"reference[{i}]", p) for i, p in enumerate(args.reference_fdt)]
        for label, path in paths:
            blob = path.read_bytes()
            inputs[label] = blob
            sources[label] = {"path": str(path.resolve()), "sha256": sha(blob), "size": len(blob)}
        expected = args.expect_reference_sha256
        require(not expected or len(expected) == len(args.reference_fdt),
                "supply one expected SHA-256 per reference, in reference order")
        for i, digest in enumerate(expected):
            require(sha(inputs[f"reference[{i}]"]) == digest.lower(), f"reference[{i}] SHA-256 mismatch")
        addresses = {name: getattr(args, name + "_load") for name in ("kernel", "ramdisk", "dtb")
                     if getattr(args, name + "_load") is not None}
        report = audit(inputs["boot"], inputs["candidate_dtb"],
                       [inputs[f"reference[{i}]"] for i in range(len(args.reference_fdt))], addresses)
        code = 0 if report["known_range_checks_passed"] else 1
    except (ValueError, OSError, RuntimeError, struct.error) as exc:
        report = {"status": "invalid-input", "bootability": "not established", "error": str(exc)}
        code = 2
    report["sources"] = sources
    report["auditor_sha256"] = sha(Path(__file__).read_bytes())
    report["dtb_helper_sha256"] = sha(HELPER.read_bytes())
    try:
        # No mkdir/unlink/replace: caller chooses a new evidence directory explicitly.
        with args.report.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    except OSError as exc:
        parser.exit(2, f"report creation failed: {exc}\n")
    print(f"{report['status']}: {len(report.get('issues', []))} known range violations; bootability not established")
    print(f"report: {args.report.resolve()}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
