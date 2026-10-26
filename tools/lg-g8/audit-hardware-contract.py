#!/usr/bin/env python3
"""Read-only LG G8 DTB/config audit; declarations are not hardware acceptance."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import struct
import sys

HERE = Path(__file__).resolve().parent
RUNTIME_SHA256 = "75af820a2032ff3b8043e2507ebab2d0dc38169feb56539997c00292f36f25bf"
CONFIG_KEYS = {
    "CONFIG_IOMMU_SUPPORT", "CONFIG_IOMMU_API", "CONFIG_IOMMU_DMA",
    "CONFIG_IOMMU_DEFAULT_DMA_STRICT", "CONFIG_IOMMU_DEFAULT_DMA_LAZY",
    "CONFIG_IOMMU_DEFAULT_PASSTHROUGH", "CONFIG_ARM_SMMU", "CONFIG_ARM_SMMU_QCOM",
    "CONFIG_ARM_SMMU_DISABLE_BYPASS_BY_DEFAULT", "CONFIG_USB_DWC3",
    "CONFIG_USB_DWC3_QCOM", "CONFIG_USB_DWC3_GADGET", "CONFIG_USB_DWC3_HOST",
    "CONFIG_USB_DWC3_DUAL_ROLE", "CONFIG_PHY_QCOM_QMP", "CONFIG_PHY_QCOM_QMP_COMBO",
    "CONFIG_PHY_QCOM_QMP_USB", "CONFIG_PHY_QCOM_USB_HS",
    "CONFIG_PHY_QCOM_USB_SNPS_FEMTO_V2", "CONFIG_INTERCONNECT_QCOM_SM8150",
    "CONFIG_DRM", "CONFIG_DRM_MSM", "CONFIG_DRM_SIMPLEDRM", "CONFIG_FB",
    "CONFIG_FB_SIMPLE", "CONFIG_SYSFB", "CONFIG_SYSFB_SIMPLEFB",
    "CONFIG_CMDLINE_FORCE", "CONFIG_CMDLINE_EXTEND",
}
BOOLS = {"1": True, "y": True, "Y": True, "on": True,
         "0": False, "n": False, "N": False, "off": False}


def verifier():
    spec = importlib.util.spec_from_file_location("hardware_fdt_reader", HERE / "local-sim/dtb/verify.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def strings(value):
    return value.rstrip(b"\0").decode("utf-8", errors="replace").split("\0") if value else []


def cells(value):
    if len(value) % 4:
        raise ValueError("property length is not a whole number of cells")
    return list(struct.unpack(f">{len(value) // 4}I", value))


def single(value):
    values = cells(value)
    if len(values) != 1:
        raise ValueError("expected one cell")
    return values[0]


def parent(path):
    return path.rsplit("/", 1)[0] or "/"


class Tree:
    def __init__(self, nodes):
        self.nodes = nodes
        self.handles = {}
        self.errors = []
        for path, props in nodes.items():
            values = []
            for key in ("phandle", "linux,phandle"):
                if key not in props:
                    continue
                try:
                    handle = single(props[key])
                    if handle in (0, 0xffffffff):
                        raise ValueError("reserved phandle")
                    values.append(handle)
                    if path not in self.handles.setdefault(handle, []):
                        self.handles[handle].append(path)
                except ValueError as error:
                    self.errors.append({"node": path, "property": key, "error": str(error)})
            if len(set(values)) > 1:
                self.errors.append({"node": path, "error": "phandle aliases disagree"})
        for handle, paths in self.handles.items():
            if len(paths) > 1:
                self.errors.append({"phandle": hex(handle), "nodes": paths, "error": "duplicate phandle"})

    def availability(self, path):
        blocked = []
        cursor = path
        while True:
            props = self.nodes.get(cursor)
            if props is None:
                blocked.append({"node": cursor, "status": "missing"})
            elif "status" in props and props["status"] not in (b"okay\0", b"ok\0"):
                # Do not echo arbitrary untrusted strings.
                status = "disabled" if props["status"] == b"disabled\0" else "unavailable-or-malformed"
                blocked.append({"node": cursor, "status": status})
            if cursor == "/":
                break
            cursor = parent(cursor)
        return {"available": not blocked, "blocked_by": blocked}

    def references(self, path, prop, count_key=None):
        props = self.nodes[path]
        result = {"present": prop in props, "specifiers": [], "errors": []}
        if prop not in props:
            return result
        try:
            data = cells(props[prop])
            if not data:
                raise ValueError("empty reference list")
            cursor = 0
            while cursor < len(data):
                handle = data[cursor]
                cursor += 1
                targets = self.handles.get(handle, [])
                if len(targets) != 1:
                    raise ValueError("dangling or ambiguous phandle " + hex(handle))
                target = targets[0]
                count = single(self.nodes[target].get(count_key, b"")) if count_key else 0
                if count > len(data) - cursor:
                    raise ValueError("truncated provider specifier")
                args = data[cursor:cursor + count]
                cursor += count
                entry = {"provider": target, "arguments": [hex(x) for x in args],
                         "provider_availability": self.availability(target),
                         "provider_compatible": strings(self.nodes[target].get("compatible", b""))}
                arm_compatible = any(c in ("arm,mmu-500", "arm,smmu-v1", "arm,smmu-v2", "qcom,qsmmu-v500")
                                     for c in entry["provider_compatible"])
                if prop == "iommus" and count == 2 and arm_compatible:
                    entry["arm_smmu_interpretation"] = {"sid": hex(args[0]), "mask": hex(args[1]),
                        "scope": "two-cell ARM SMMU convention; not an observed domain"}
                result["specifiers"].append(entry)
        except ValueError as error:
            result["errors"].append(str(error))
        return result

    def reg(self, path):
        props = self.nodes[path]
        if "reg" not in props:
            return []
        owner = self.nodes.get(parent(path), {})
        ac = single(owner.get("#address-cells", struct.pack(">I", 2)))
        sc = single(owner.get("#size-cells", struct.pack(">I", 1)))
        data = cells(props["reg"])
        if ac not in (1, 2) or sc not in (0, 1, 2) or not data or len(data) % (ac + sc):
            raise ValueError("unsupported cell widths or malformed reg")
        result = []
        for offset in range(0, len(data), ac + sc):
            address = size = 0
            for cell in data[offset:offset + ac]:
                address = (address << 32) | cell
            for cell in data[offset + ac:offset + ac + sc]:
                size = (size << 32) | cell
            result.append({"base": hex(address), "size": hex(size)})
        return result

    def identity_address(self, path):
        cursor = parent(path)
        while cursor != "/":
            if self.nodes.get(cursor, {}).get("ranges") != b"":
                return False
            cursor = parent(cursor)
        return True

    def describe(self, path):
        props = self.nodes[path]
        result = {"node": path, "compatible": strings(props.get("compatible", b"")),
                  "availability": self.availability(path), "references": {}}
        try:
            result["reg_bus_addresses"] = self.reg(path)
        except ValueError as error:
            result["reg_error"] = str(error)
        for prop, count in (("iommus", "#iommu-cells"), ("phys", "#phy-cells"),
                            ("clocks", "#clock-cells"), ("interconnects", "#interconnect-cells"),
                            ("power-domains", "#power-domain-cells"), ("resets", "#reset-cells")):
            result["references"][prop] = self.references(path, prop, count)
        result["references"]["usb-phy"] = self.references(path, "usb-phy")
        for prop in props:
            if prop.endswith("-supply"):
                result["references"][prop] = self.references(path, prop)
        result["linux_sysdev_is_parent_declared"] = "linux,sysdev_is_parent" in props
        if "maximum-speed" in props:
            speed = props["maximum-speed"]
            allowed = ("low-speed", "full-speed", "high-speed", "super-speed", "super-speed-plus")
            result["maximum-speed"] = next((x for x in allowed if speed == x.encode() + b"\0"), "invalid")
        for prop in ("clock-names", "interconnect-names", "phy-names"):
            if prop in props:
                result[prop] = strings(props[prop])
        if "dr_mode" in props:
            mode = props["dr_mode"]
            result["dr_mode"] = strings(mode)[0] if mode in (b"host\0", b"peripheral\0", b"otg\0") else "invalid"
        result["dma_coherent_declared"] = "dma-coherent" in props
        result["dma_ranges_present"] = "dma-ranges" in props
        # Downstream bus tables describe possible cases, never a current vote.
        for key in ("qcom,msm-bus,num-cases", "qcom,msm-bus,num-paths", "qcom,msm-bus,vectors-KBps"):
            if key in props:
                try:
                    result[key] = cells(props[key])
                except ValueError as error:
                    result[key] = {"error": str(error)}
        return result


def cmdline_arguments(text):
    """Recognize Linux next_arg quoting and parse_args' -- terminator.

    Backslashes are ordinary bytes here, unlike a shell lexer. Malformed quoting
    or NUL is left unknown instead of interpreting fragments as kernel options.
    """
    if "\0" in text:
        raise ValueError("embedded NUL in command line")
    cursor = 0
    whitespace = " \t\n\r\v\f"
    while cursor < len(text):
        while cursor < len(text) and text[cursor] in whitespace:
            cursor += 1
        if cursor == len(text):
            return
        quoted = text[cursor] == '"'
        if quoted:
            cursor += 1
        start = cursor
        in_quote = quoted
        equals = None
        while cursor < len(text):
            char = text[cursor]
            if char in whitespace and not in_quote:
                break
            if char == "=" and equals is None and cursor > start:
                equals = cursor - start
            if char == '"':
                in_quote = not in_quote
            cursor += 1
        if in_quote:
            raise ValueError("unbalanced command line quotes")
        token = text[start:cursor]
        if equals is None:
            key, sep, value = token, "", ""
        else:
            key, sep, value = token[:equals], "=", token[equals + 1:]
            if value.startswith('"'):
                value = value[1:]
                if value.endswith('"'):
                    value = value[:-1]
        if quoted:
            if sep and value.endswith('"'):
                value = value[:-1]
            elif not sep and key.endswith('"'):
                key = key[:-1]
        if key == "--" and not sep:
            return
        yield key, sep, value


def policy_tokens(text):
    """Whitelist keys and accepted values; unrelated or malformed values never escape."""
    result = []
    try:
        arguments = list(cmdline_arguments(text))
    except ValueError:
        return [{"key": "malformed-cmdline", "valid": False}]
    for key, sep, value in arguments:
        if key in ("iommu.passthrough", "iommu.strict"):
            result.append({"key": key, "value": BOOLS.get(value) if sep else None,
                           "valid": bool(sep) and value in BOOLS})
        elif key in ("clk_ignore_unused", "pd_ignore_unused"):
            result.append({"key": key, "valid": not sep})
        elif key == "swiotlb":
            result.append({"key": key, "valid": bool(re.fullmatch(r"[0-9]+(?:,(?:force|noforce))?", value))})
        elif key.startswith(("iommu.", "arm-smmu.")):
            result.append({"key": "other-iommu-option", "valid": False})
    return result


def parse_config(text):
    config, cmdline, duplicates = {}, [], []
    for line in text.splitlines():
        match = re.fullmatch(r"# (CONFIG_[A-Z0-9_]+) is not set", line)
        if match:
            key, value = match[1], "n"
        elif line.startswith("CONFIG_") and "=" in line:
            key, value = line.split("=", 1)
        else:
            continue
        if key == "CONFIG_CMDLINE":
            try:
                cmdline.append(json.loads(value))
            except (ValueError, TypeError):
                cmdline.append(None)
        if key in CONFIG_KEYS:
            if key in config:
                duplicates.append(key)
            config[key] = value if value in ("y", "m", "n") else "invalid"
    return {"selected": config, "duplicate_keys": duplicates,
            "compiled_cmdline_present": bool(cmdline),
            "compiled_cmdline_nonempty": any(bool(x) for x in cmdline),
            "compiled_cmdline_valid": all(isinstance(x, str) for x in cmdline) and len(cmdline) <= 1,
            "compiled_cmdline_tokens": [t for x in cmdline if isinstance(x, str) for t in policy_tokens(x)]}


def policy(config, dt_tokens, extra_tokens=None):
    config = config or {"selected": {}, "duplicate_keys": [], "compiled_cmdline_tokens": [],
                        "compiled_cmdline_valid": True, "compiled_cmdline_nonempty": False}
    selected = config["selected"]
    sources = {"dt_chosen": dt_tokens, "compiled": config["compiled_cmdline_tokens"],
               "supplied_extra": extra_tokens or []}
    defaults = [key for key in ("CONFIG_IOMMU_DEFAULT_DMA_STRICT", "CONFIG_IOMMU_DEFAULT_DMA_LAZY",
                               "CONFIG_IOMMU_DEFAULT_PASSTHROUGH") if selected.get(key) == "y"]
    problems = []
    if len(defaults) != 1:
        problems.append("missing or conflicting configured default domain")
    if config["duplicate_keys"] or not config["compiled_cmdline_valid"]:
        problems.append("duplicate or malformed config")
    if (config["compiled_cmdline_nonempty"] or
            selected.get("CONFIG_CMDLINE_FORCE") == "y" or
            selected.get("CONFIG_CMDLINE_EXTEND") == "y"):
        problems.append("compiled cmdline or merge flags require architecture merge verification")
    if any(value == "invalid" for value in selected.values()):
        problems.append("malformed selected config value")
    tokens = [token for group in sources.values() for token in group]
    if any(not t["valid"] for t in tokens):
        problems.append("unrecognized or malformed policy option")
    values = {}
    for key in ("iommu.passthrough", "iommu.strict"):
        found = {t["value"] for t in tokens if t["key"] == key and t["valid"]}
        if len(found) > 1:
            problems.append("conflicting " + key + " declarations")
        values[key] = next(iter(found)) if len(found) == 1 else None
    mode = "unknown"
    strict = "unknown"
    if not problems:
        passthrough = defaults[0] == "CONFIG_IOMMU_DEFAULT_PASSTHROUGH"
        if values["iommu.passthrough"] is not None:
            passthrough = values["iommu.passthrough"]
        mode = "identity" if passthrough else "translated"
        configured_strict = selected.get("CONFIG_IOMMU_DEFAULT_DMA_STRICT") == "y"
        strict = values["iommu.strict"] if values["iommu.strict"] is not None else configured_strict
    return {"sources": sources, "configured_defaults": defaults, "problems": problems,
            "conditional_default_domain": mode, "conditional_dma_strict": strict,
            "condition": "assuming this config and supplied cmdline inputs describe the boot; no unseen overrides or per-device fallback",
            "runtime_device_domain": "unknown; requires runtime attachment/domain and DMA evidence",
            "disable_bypass_by_default": selected.get("CONFIG_ARM_SMMU_DISABLE_BYPASS_BY_DEFAULT", "unknown"),
            "bypass_note": "unmatched-stream bypass policy is separate from the attached default domain"}


def inspect(nodes, reader, memreserve=()):
    tree = Tree(nodes)
    selected = []
    for path, props in nodes.items():
        compat = strings(props.get("compatible", b""))
        if any(any(word in item for word in ("dwc3", "dwc-usb3", "usb-hsphy", "usb-ssphy", "usb-hs-phy",
                                             "usb-ss-dp-phy", "usb3-phy", "usb-phy", "smmu", "mdss", "sde-kms"))
               for item in compat):
            selected.append(path)
    # Include referenced PHYs and display bus-vote children without dumping panel data.
    for path in list(selected):
        for prop, count in (("phys", "#phy-cells"), ("usb-phy", None)):
            for spec in tree.references(path, prop, count)["specifiers"]:
                selected.append(spec["provider"])
        if any(x in path for x in ("mdss", "display-subsystem")):
            selected.extend(p for p in nodes if parent(p) == path and
                            ("qcom,msm-bus,vectors-KBps" in nodes[p] or
                             any("dsi" in c or "dpu" in c for c in strings(nodes[p].get("compatible", b"")))))
    reservations = []
    reservation_errors = []
    for name, low, high in memreserve:
        if not 0 <= low < high <= 1 << 64:
            reservation_errors.append({"node": name, "error": "invalid or overflowing memreserve interval"})
        else:
            reservations.append((name, low, high))
    reserved_root_valid = True
    if "/reserved-memory" in nodes:
        try:
            root, reserved_root = nodes.get("/", {}), nodes["/reserved-memory"]
            for key, default in (("#address-cells", 2), ("#size-cells", 1)):
                if single(reserved_root.get(key, b"")) != single(root.get(key, struct.pack(">I", default))):
                    raise ValueError("reserved-memory cell widths do not match root")
            if reserved_root.get("ranges") != b"":
                raise ValueError("nonidentity or absent reserved-memory ranges")
        except ValueError as error:
            reserved_root_valid = False
            reservation_errors.append({"node": "/reserved-memory", "error": str(error)})
    for path, props in nodes.items():
        if (parent(path) == "/reserved-memory" and "reg" in props and "reusable" not in props
                and reserved_root_valid and tree.availability(path)["available"]):
            try:
                ranges = tree.reg(path)
                # This local kernel consumes only the first fixed reg tuple.
                # Multiple tuples remain unknown instead of implying protection.
                if len(ranges) != 1:
                    raise ValueError("multiple fixed reservation tuples require kernel semantics verification")
                base, size = int(ranges[0]["base"], 16), int(ranges[0]["size"], 16)
                if not size or base + size > 1 << 64:
                    raise ValueError("zero-sized or overflowing fixed reservation")
                reservations.append((path, base, base + size))
            except ValueError as error:
                reservation_errors.append({"node": path, "error": str(error)})
    framebuffers = []
    formats = {"r5g6b5": 2, "r5g5b5a1": 2, "x1r5g5b5": 2, "r8g8b8": 3,
               "a8r8g8b8": 4, "x8r8g8b8": 4, "a8b8g8r8": 4, "x8b8g8r8": 4,
               "x2r10g10b10": 4, "a2r10g10b10": 4}
    for path, props in nodes.items():
        if "simple-framebuffer" not in strings(props.get("compatible", b"")):
            continue
        entry = {"node": path, "availability": tree.availability(path), "errors": []}
        try:
            entry["reg_bus_addresses"] = tree.reg(path)
            entry.update({key: single(props.get(key, b"")) for key in ("width", "height", "stride")})
            fmt = props.get("format", b"")
            format_name = next((key for key in formats if fmt == key.encode() + b"\0"), None)
            entry["format"] = format_name or "unknown-or-malformed"
            if not format_name:
                raise ValueError("unknown framebuffer format")
            if not tree.identity_address(path):
                raise ValueError("framebuffer bus translation not established")
            if not entry["width"] or not entry["height"] or entry["stride"] < entry["width"] * formats[format_name]:
                raise ValueError("invalid geometry or short stride")
            if len(entry["reg_bus_addresses"]) != 1:
                raise ValueError("expected one framebuffer allocation")
            allocation = entry["reg_bus_addresses"][0]
            base, size = int(allocation["base"], 16), int(allocation["size"], 16)
            if not size or base + size > 1 << 64:
                raise ValueError("zero-sized or overflowing framebuffer allocation")
            entry["required_bytes"] = entry["stride"] * entry["height"]
            entry["fits_allocation"] = entry["required_bytes"] <= size
            entry["reservation_coverage_known"] = not reservation_errors
            entry["reservation_gaps"] = [[hex(a), hex(b)] for a, b in reader.gaps(base, base + size, reservations)]
        except ValueError as error:
            entry["errors"].append(str(error))
        entry["dependencies"] = tree.describe(path)
        framebuffers.append(entry)
    chosen = nodes.get("/chosen", {}).get("bootargs")
    chosen_valid = chosen is None or (chosen.endswith(b"\0") and b"\0" not in chosen[:-1])
    dt_tokens = policy_tokens(chosen[:-1].decode("utf-8", errors="replace")) if chosen_valid and chosen is not None else []
    if not chosen_valid:
        dt_tokens.append({"key": "malformed-dt-bootargs", "valid": False})
    return {"phandle_errors": tree.errors,
            "nodes": [tree.describe(path) for path in sorted(set(selected))],
            "fixed_reservations": [{"node": n, "base": hex(a), "size": hex(b - a)} for n, a, b in reservations],
            "reservation_errors": reservation_errors,
            "simple_framebuffers": framebuffers,
            "display_limit": "carveouts and bus tables do not establish active scanout, panel selection, clocks or bandwidth votes",
            "dt_policy_tokens": dt_tokens}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", required=True, type=Path)
    parser.add_argument("--runtime-sha256", default=RUNTIME_SHA256)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--candidate-sha256")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--extra-cmdline-file", type=Path)
    parser.add_argument("--source", action="append", default=[], metavar="LABEL=PATH", help="hash source evidence without executing it")
    parser.add_argument("--out", required=True, type=Path, help="new JSON file; refuses replacement")
    args = parser.parse_args()
    reader = verifier()
    inputs, trees = {}, {}
    all_paths = {"runtime": args.runtime, "candidate": args.candidate, "config": args.config}
    if args.extra_cmdline_file:
        all_paths["extra_cmdline"] = args.extra_cmdline_file
    for item in args.source:
        label, sep, path = item.partition("=")
        if not sep or not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]*", label) or label in all_paths:
            raise ValueError("source must have a unique LABEL=PATH")
        all_paths[label] = Path(path)
    for label, path in all_paths.items():
        inputs[label] = {"path": str(path.resolve()), "sha256": digest(path)}
    for label, expected in (("runtime", args.runtime_sha256), ("candidate", args.candidate_sha256)):
        if expected and inputs[label]["sha256"] != expected.lower():
            raise ValueError(label + " SHA256 mismatch")
        trees[label] = inspect(reader.tree(all_paths[label]), reader, reader.memreserve(all_paths[label]))
        inputs[label]["libfdt_check_full"] = 0
    config = parse_config(args.config.read_text())
    extra = policy_tokens(args.extra_cmdline_file.read_text()) if args.extra_cmdline_file else []
    trees["runtime"]["iommu_policy"] = policy(None, trees["runtime"]["dt_policy_tokens"])
    trees["candidate"]["iommu_policy"] = policy(config, trees["candidate"]["dt_policy_tokens"], extra)
    for label, path in all_paths.items():
        if digest(path) != inputs[label]["sha256"]:
            raise ValueError(label + " changed while auditing")
    report = {"schema_version": 1,
              "scope": "offline static declarations only; no image provenance, device access, boot, domain, USB data-flow or display acceptance",
              "inputs": inputs, "candidate_config": config, "trees": trees,
              "source_provenance": "source arguments are hash-labelled local evidence; upstream identity and correspondence to a built image are not verified"}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(report, stream, indent=2, ensure_ascii=True)
        stream.write("\n")
    print(json.dumps({"report": str(args.out.resolve()), "runtime_sha256": inputs["runtime"]["sha256"],
                      "candidate_sha256": inputs["candidate"]["sha256"], "scope": report["scope"]}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, RuntimeError, struct.error) as error:
        print(f"audit-hardware-contract: {error}", file=sys.stderr)
        raise SystemExit(2)
