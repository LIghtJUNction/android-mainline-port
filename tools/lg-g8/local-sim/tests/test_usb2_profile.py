#!/usr/bin/env python3
"""Compile and verify the optional USB2 profile; no image or device execution."""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
from pathlib import Path
import shlex
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[4]
BASELINE = ROOT / "devices/lg-alphalm/sm8150-lg-alphalm-board.dts"
PROFILE = BASELINE.with_name("sm8150-lg-alphalm-usb2.dts")
AUDITED_SHA = "eb6f12cfedf7cdd9591b27cc138e79ee08efa24b36053ff5bac80fc21166d5d9"
USB = "/soc@0/usb@a6f8800"
CORE = USB + "/usb@a600000"
HS = "/soc@0/phy@88e2000"
QMP = "/soc@0/phy@88e8000"
USB_SECONDARY = "/soc@0/usb@a8f8800"
ALLOWED = {(USB, "qcom,select-utmi-as-pipe-clk"),
           (CORE, "maximum-speed"), (CORE, "phys"), (CORE, "phy-names")}
REFERENCE_CELLS = {
    "phys": "#phy-cells", "clocks": "#clock-cells", "resets": "#reset-cells",
    "iommus": "#iommu-cells", "interconnects": "#interconnect-cells",
    "power-domains": "#power-domain-cells", "dmas": "#dma-cells",
    "pwms": "#pwm-cells", "io-channels": "#io-channel-cells",
    "interrupts-extended": "#interrupt-cells", "sound-dai": "#sound-dai-cells",
    "assigned-clocks": "#clock-cells", "assigned-clock-parents": "#clock-cells",
}


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


reader = load("usb2_fdt_reader", ROOT / "tools/lg-g8/local-sim/dtb/verify.py")
audit = load("usb2_hardware_reader", ROOT / "tools/lg-g8/audit-hardware-contract.py")


def semantic(nodes):
    """Compare references by provider path, ignoring generated phandle numbers."""
    tree = audit.Tree(nodes)
    if tree.errors:
        raise ValueError("invalid or ambiguous phandles")
    result = {}
    for path, props in nodes.items():
        result[path] = {}
        for key, value in props.items():
            if key in ("phandle", "linux,phandle"):
                continue
            count = REFERENCE_CELLS.get(key)
            is_ref = (key in REFERENCE_CELLS or key.endswith("-supply")
                      or key in ("interrupt-parent", "memory-region", "nvmem-cells", "usb-phy",
                                 "operating-points-v2", "required-opps", "remote-endpoint")
                      or key.startswith("pinctrl-") and key[8:].isdigit()
                      or key == "gpios" or key.endswith("-gpios"))
            if key == "gpios" or key.endswith("-gpios"):
                count = "#gpio-cells"
            if is_ref:
                refs = tree.references(path, key, count)
                if refs["errors"]:
                    raise ValueError(f"invalid reference at {path} property {key}")
                result[path][key] = [(r["provider"], r["arguments"]) for r in refs["specifiers"]]
            else:
                result[path][key] = value
    return result


def compare(before, after, baseline_dtb, profile_dtb):
    left, right = semantic(before), semantic(after)
    diffs = sorted((path, key) for path in left.keys() | right.keys()
                   for key in left.get(path, {}).keys() | right.get(path, {}).keys()
                   if left.get(path, {}).get(key) != right.get(path, {}).get(key))
    tree = audit.Tree(after)
    refs = tree.references(CORE, "phys", "#phy-cells")
    hs_refs = refs["specifiers"]
    supply_keys = {key for key in after[HS] if key.endswith("-supply")}
    supply_refs = [tree.references(HS, key) for key in supply_keys]
    # Whole-tree comparison above also protects every supply and SID property.
    checks = {
        "libfdt_check_full_both": True,  # reader.tree() validated both blobs
        "same_node_set": left.keys() == right.keys(),
        "exact_four_property_semantic_diff": set(diffs) == ALLOWED,
        "utmi_pipe_clock_selected": after[USB].get("qcom,select-utmi-as-pipe-clk") == b"",
        "high_speed_limit": after[CORE].get("maximum-speed") == b"high-speed\0",
        "sole_usb2_phy_name": after[CORE].get("phy-names") == b"usb2-phy\0",
        "sole_hs_phy_resolves_and_available": (
            not refs["errors"] and len(hs_refs) == 1
            and hs_refs[0]["provider"] == HS and hs_refs[0]["arguments"] == []
            and hs_refs[0]["provider_availability"]["available"]),
        "usb1_available_and_peripheral": (
            tree.availability(CORE)["available"]
            and after[CORE].get("dr_mode") == b"peripheral\0"),
        "qmp_still_disabled": before[QMP].get("status") == after[QMP].get("status") == b"disabled\0",
        "secondary_usb_still_disabled": (before[USB_SECONDARY].get("status")
                                           == after[USB_SECONDARY].get("status") == b"disabled\0"),
        "same_ram": reader.memory(before) == reader.memory(after),
        "same_reservations": (reader.reserved(before) == reader.reserved(after)
                              and reader.memreserve(baseline_dtb) == reader.memreserve(profile_dtb)),
        "same_sid_declarations": all(props.get("iommus") == right[path].get("iommus")
                                      for path, props in left.items()),
        "same_supplies": all(value == right[path].get(key) for path, props in left.items()
                              for key, value in props.items() if key.endswith("-supply")),
        "hs_supplies_resolve_and_available": (
            supply_keys == {"vdda-pll-supply", "vdda18-supply", "vdda33-supply"}
            and all(not r["errors"] and len(r["specifiers"]) == 1
                    and r["specifiers"][0]["provider_availability"]["available"] for r in supply_refs)),
    }
    return checks, [{"node": path, "property": key} for path, key in diffs], refs


def negative_checks(before, after, baseline_dtb, profile_dtb):
    """Ensure the verifier rejects plausible profile regressions without images."""
    checks = {}
    reservation = next(path for path, props in after.items()
                       if path.startswith("/reserved-memory/") and "reg" in props)
    changed = copy.deepcopy(after)
    data = bytearray(changed[reservation]["reg"])
    data[-1] ^= 1
    changed[reservation]["reg"] = bytes(data)
    result, _, _ = compare(before, changed, baseline_dtb, profile_dtb)
    checks["reject_unapproved_reservation_change"] = (
        not result["exact_four_property_semantic_diff"] and not result["same_reservations"])
    changed = copy.deepcopy(after)
    # Retain the baseline's two providers, resolving its handles into this tree.
    handles = audit.Tree(changed)
    provider_handles = {paths[0]: handle for handle, paths in handles.handles.items()}
    original_refs = audit.Tree(before).references(CORE, "phys", "#phy-cells")
    values = []
    for ref in original_refs["specifiers"]:
        provider = ref["provider"]
        if provider not in provider_handles:
            handle = max(provider_handles.values(), default=0) + 1
            changed[provider]["phandle"] = handle.to_bytes(4, "big")
            provider_handles[provider] = handle
        values += [provider_handles[provider]] + [int(v, 16) for v in ref["arguments"]]
    changed[CORE]["phys"] = b"".join(value.to_bytes(4, "big") for value in values)
    changed[CORE]["phy-names"] = before[CORE]["phy-names"]
    result, _, _ = compare(before, changed, baseline_dtb, profile_dtb)
    checks["reject_retained_two_phy_baseline"] = (
        not result["exact_four_property_semantic_diff"]
        and not result["sole_hs_phy_resolves_and_available"] and not result["sole_usb2_phy_name"])
    changed = copy.deepcopy(after)
    del changed[USB]["qcom,select-utmi-as-pipe-clk"]
    result, _, _ = compare(before, changed, baseline_dtb, profile_dtb)
    checks["reject_missing_utmi_clock_selection"] = (
        not result["exact_four_property_semantic_diff"] and not result["utmi_pipe_clock_selected"])
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel-source", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path, help="new directory; never overwrite")
    args = parser.parse_args()
    source, out = args.kernel_source.resolve(), args.out.resolve()
    if not source.is_dir():
        parser.error("kernel source must be an existing directory")
    if out == source or source in out.parents:
        parser.error("output must be outside the external kernel source")
    out.mkdir(parents=True, exist_ok=False)
    cpp = ["clang", "-E", "-P", "-x", "assembler-with-cpp", "-nostdinc", "-undef", "-D__DTS__"]
    for directory in (source / "arch/arm64/boot/dts/qcom", source / "include",
                      source / "scripts/dtc/include-prefixes"):
        cpp += ["-I", str(directory)]
    commands = []
    report = {"scope": "static USB2 profile structure only; no image/device/runtime acceptance",
              "kernel_source": str(source), "commands": commands, "checks": {}, "passed": False}

    def run(command, stem, data=None):
        try:
            result = subprocess.run(command, input=data, capture_output=True, timeout=60)
        except subprocess.TimeoutExpired as error:
            commands.append({"argv": command, "timeout_seconds": 60})
            (out / (stem + ".stderr")).write_bytes(error.stderr or b"")
            (out / (stem + ".stdout")).write_bytes(error.stdout or b"")
            raise
        commands.append({"argv": command, "exit": result.returncode})
        (out / (stem + ".stderr")).write_bytes(result.stderr)
        (out / (stem + ".stdout")).write_bytes(result.stdout)
        result.check_returncode()
        return result.stdout

    def dependencies(phase):
        paths = {BASELINE, PROFILE, Path(__file__).resolve(),
                 ROOT / "tools/lg-g8/local-sim/dtb/verify.py",
                 ROOT / "tools/lg-g8/audit-hardware-contract.py"}
        for stem, path in (("baseline", BASELINE), ("usb2", PROFILE)):
            raw = run(cpp + ["-M", "-MT", "inputs", str(path)], stem + ".dependencies-" + phase)
            paths.update(Path(p).resolve() for p in shlex.split(raw.decode().split(":", 1)[1].replace("\\\n", "")))
        for tool in ("clang", "dtc"):
            paths.add(Path(shutil.which(tool)).resolve())
        return {str(p): reader.sha(p) for p in sorted(paths)}

    before_hashes = None
    try:
        before_hashes = dependencies("before")
        (out / "source-hashes-before.json").write_text(json.dumps(before_hashes, indent=2) + "\n")
        blobs = []
        for stem, path in (("baseline", BASELINE), ("usb2", PROFILE)):
            preprocessed = run(cpp + [str(path)], stem + ".cpp")
            dtb = out / (stem + ".dtb")
            run(["dtc", "-I", "dts", "-O", "dtb", "-o", str(dtb), "-"], stem + ".dtc", preprocessed)
            blobs.append(dtb)
        baseline_dtb, profile_dtb = blobs
        before, after = (reader.tree(path) for path in blobs)
        checks, differences, refs = compare(before, after, baseline_dtb, profile_dtb)
        checks.update(negative_checks(before, after, baseline_dtb, profile_dtb))
        checks["audited_baseline_reproduced"] = reader.sha(baseline_dtb) == AUDITED_SHA
        report.update(checks=checks, semantic_differences=differences, profile_phys=refs,
                      baseline_sha256=reader.sha(baseline_dtb), profile_sha256=reader.sha(profile_dtb))
    except Exception as error:
        # Never echo property values, preprocessed contents, or raw bootargs.
        report["error"] = type(error).__name__
    finally:
        if before_hashes is not None:
            try:
                after_hashes = dependencies("after")
                (out / "source-hashes-after.json").write_text(json.dumps(after_hashes, indent=2) + "\n")
                report["checks"]["all_source_dependencies_and_tools_unchanged"] = before_hashes == after_hashes
                report["dependency_count"] = len(before_hashes)
            except Exception as error:
                report["error"] = type(error).__name__
        report["passed"] = bool(report["checks"]) and all(report["checks"].values()) and "error" not in report
        (out / "verification.json").write_text(json.dumps(report, indent=2) + "\n")
    for name, passed in report["checks"].items():
        print("PASS" if passed else "FAIL", name)
    for name in ("baseline_sha256", "profile_sha256", "error"):
        if name in report:
            print(name, report[name])
    print("PASS" if report["passed"] else "FAIL", "static USB2 diagnostic profile", out / "verification.json")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
