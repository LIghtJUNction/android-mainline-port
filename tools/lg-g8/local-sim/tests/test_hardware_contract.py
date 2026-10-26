"""Exercise failure boundaries with synthetic trees and real libfdt-validated blobs."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

TOOL = Path(__file__).resolve().parents[2] / "audit-hardware-contract.py"
spec = importlib.util.spec_from_file_location("hardware_contract", TOOL)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def pack(*values):
    return struct.pack(f">{len(values)}I", *values)


def dtb(nodes):
    """Small valid FDT encoder; does not depend on dtc or a kernel checkout."""
    names = sorted({key for props in nodes.values() for key in props})
    strings = b""
    offsets = {}
    for name in names:
        offsets[name] = len(strings)
        strings += name.encode() + b"\0"
    structure = bytearray()

    def padded(value):
        return value + b"\0" * (-len(value) % 4)

    def visit(path):
        name = "" if path == "/" else path.rsplit("/", 1)[1]
        structure.extend(pack(1) + padded(name.encode() + b"\0"))
        for key, value in nodes[path].items():
            structure.extend(pack(3, len(value), offsets[key]) + padded(value))
        for child in nodes:
            if child != "/" and audit.parent(child) == path:
                visit(child)
        structure.extend(pack(2))

    visit("/")
    structure.extend(pack(9))
    structure_offset = 56
    strings_offset = structure_offset + len(structure)
    return pack(0xd00dfeed, strings_offset + len(strings), structure_offset, strings_offset,
                40, 17, 16, 0, len(strings), len(structure)) + bytes(16) + structure + strings


class HardwareContractTests(unittest.TestCase):
    def setUp(self):
        self.nodes = {
            "/": {"#address-cells": pack(2), "#size-cells": pack(2)},
            "/soc": {"#address-cells": pack(1), "#size-cells": pack(1), "ranges": b""},
            "/soc/smmu": {"phandle": pack(1), "#iommu-cells": pack(2), "compatible": b"arm,mmu-500\0"},
            "/soc/usb": {"compatible": b"qcom,dwc3\0"},
            "/soc/usb/core": {"compatible": b"snps,dwc3\0", "iommus": pack(1, 0x140, 0)},
        }
        self.config = audit.parse_config("CONFIG_IOMMU_DEFAULT_DMA_STRICT=y\nCONFIG_CMDLINE=\"\"\n")

    def refs(self):
        return audit.Tree(self.nodes).references("/soc/usb/core", "iommus", "#iommu-cells")

    def test_absent_and_valid_iommu_are_distinct(self):
        self.assertEqual(self.refs()["specifiers"][0]["arm_smmu_interpretation"]["sid"], "0x140")
        del self.nodes["/soc/usb/core"]["iommus"]
        self.assertEqual(self.refs(), {"present": False, "specifiers": [], "errors": []})

    def test_dangling_duplicate_and_malformed_iommus(self):
        for value in (pack(99, 0x140, 0), pack(1, 0x140), b"\0", b""):
            with self.subTest(value=value):
                self.nodes["/soc/usb/core"]["iommus"] = value
                self.assertTrue(self.refs()["errors"])
        self.nodes["/soc/usb/core"]["iommus"] = pack(1, 0x140, 0)
        self.nodes["/other"] = {"phandle": pack(1)}
        tree = audit.Tree(self.nodes)
        self.assertTrue(tree.errors)
        self.assertIn("ambiguous", self.refs()["errors"][0])

    def test_disabled_ancestor_and_provider_are_reported(self):
        self.nodes["/soc"]["status"] = b"disabled\0"
        tree = audit.Tree(self.nodes)
        self.assertFalse(tree.availability("/soc/usb/core")["available"])
        self.assertEqual(tree.availability("/soc/usb/core")["blocked_by"][0]["node"], "/soc")
        self.assertFalse(self.refs()["specifiers"][0]["provider_availability"]["available"])

    def test_variable_phy_provider_widths_disabled_and_missing(self):
        self.nodes["/hsphy"] = {"phandle": pack(2), "#phy-cells": pack(0)}
        self.nodes["/qmp"] = {"phandle": pack(3), "#phy-cells": pack(1), "status": b"disabled\0"}
        self.nodes["/soc/usb/core"]["phys"] = pack(2, 3, 0)
        tree = audit.Tree(self.nodes)
        refs = tree.references("/soc/usb/core", "phys", "#phy-cells")
        self.assertFalse(refs["errors"])
        self.assertEqual([x["arguments"] for x in refs["specifiers"]], [[], ["0x0"]])
        self.assertFalse(refs["specifiers"][1]["provider_availability"]["available"])
        del self.nodes["/qmp"]["#phy-cells"]
        self.assertTrue(audit.Tree(self.nodes).references("/soc/usb/core", "phys", "#phy-cells")["errors"])
        del self.nodes["/qmp"]
        self.assertTrue(audit.Tree(self.nodes).references("/soc/usb/core", "phys", "#phy-cells")["errors"])

    def test_legacy_phy_and_dma_device_declarations(self):
        self.nodes["/hsphy"] = {"phandle": pack(2)}
        self.nodes["/qmp"] = {"phandle": pack(3), "status": b"disabled\0"}
        props = self.nodes["/soc/usb/core"]
        props.update({"usb-phy": pack(2, 3), "maximum-speed": b"super-speed\0"})
        result = audit.inspect(self.nodes, audit.verifier())
        core = next(n for n in result["nodes"] if n["node"] == "/soc/usb/core")
        refs = core["references"]["usb-phy"]
        self.assertFalse(refs["errors"])
        self.assertEqual([x["provider"] for x in refs["specifiers"]], ["/hsphy", "/qmp"])
        self.assertFalse(refs["specifiers"][1]["provider_availability"]["available"])
        self.assertIn("/qmp", [n["node"] for n in result["nodes"]])
        self.assertEqual(core["maximum-speed"], "super-speed")
        self.assertFalse(core["linux_sysdev_is_parent_declared"])
        props["linux,sysdev_is_parent"] = b""
        self.assertTrue(audit.Tree(self.nodes).describe("/soc/usb/core")["linux_sysdev_is_parent_declared"])
        props["usb-phy"] = pack(99)
        self.assertTrue(audit.Tree(self.nodes).describe("/soc/usb/core")["references"]["usb-phy"]["errors"])

    def test_iommu_interpretation_requires_arm_provider(self):
        self.nodes["/soc/smmu"]["compatible"] = b"example,other-iommu\0"
        self.assertNotIn("arm_smmu_interpretation", self.refs()["specifiers"][0])

    def test_dt_bootargs_termination_and_last_policy_token(self):
        self.nodes["/chosen"] = {"bootargs": b"iommu.passthrough=1\0"}
        reader = audit.verifier()
        tokens = audit.inspect(self.nodes, reader)["dt_policy_tokens"]
        self.assertEqual(audit.policy(self.config, tokens)["conditional_default_domain"], "identity")
        for value in (b"iommu.passthrough=1", b"iommu.passthrough=1\0hidden\0"):
            self.nodes["/chosen"]["bootargs"] = value
            tokens = audit.inspect(self.nodes, reader)["dt_policy_tokens"]
            self.assertEqual(audit.policy(self.config, tokens)["conditional_default_domain"], "unknown")

    def test_policy_strict_default_and_override_do_not_claim_domain(self):
        result = audit.policy(self.config, [])
        self.assertEqual(result["conditional_default_domain"], "translated")
        self.assertIs(result["conditional_dma_strict"], True)
        self.assertTrue(result["runtime_device_domain"].startswith("unknown"))
        result = audit.policy(self.config, audit.policy_tokens("iommu.passthrough=1"))
        self.assertEqual(result["conditional_default_domain"], "identity")

    def test_policy_conflicts_unknowns_and_config_errors(self):
        for text in ("iommu.passthrough=0 iommu.passthrough=1", "iommu.strict=wat",
                     "iommu.strict=0 iommu.strict=1", "iommu.passthrough", "arm-smmu.unknown=1"):
            with self.subTest(text=text):
                result = audit.policy(self.config, audit.policy_tokens(text))
                self.assertEqual(result["conditional_default_domain"], "unknown")
                self.assertTrue(result["problems"])
        self.assertEqual(audit.policy(None, [])["conditional_default_domain"], "unknown")
        for text in ('CONFIG_IOMMU_DEFAULT_DMA_STRICT=y\nCONFIG_IOMMU_DEFAULT_PASSTHROUGH=y',
                     'CONFIG_IOMMU_DEFAULT_DMA_STRICT=y\nCONFIG_IOMMU_DEFAULT_DMA_STRICT=y',
                     'CONFIG_IOMMU_DEFAULT_DMA_STRICT=y\nCONFIG_CMDLINE="iommu.passthrough=1"'):
            self.assertEqual(audit.policy(audit.parse_config(text), [])["conditional_default_domain"], "unknown")

    def test_policy_respects_linux_quoting_and_option_terminator(self):
        for text in ('note="ignored iommu.passthrough=1 "', '-- iommu.passthrough=1',
                     '"--" iommu.passthrough=1'):
            with self.subTest(text=text):
                result = audit.policy(self.config, audit.policy_tokens(text))
                self.assertEqual(result["conditional_default_domain"], "translated")
        for text in ('iommu.passthrough="1"', '"iommu.passthrough=1"'):
            with self.subTest(text=text):
                result = audit.policy(self.config, audit.policy_tokens(text))
                self.assertEqual(result["conditional_default_domain"], "identity")
        for text in ('note="unfinished iommu.passthrough=1', 'iommu.strict=1\0hidden'):
            self.assertEqual(audit.policy(self.config, audit.policy_tokens(text))
                             ["conditional_default_domain"], "unknown")

    def test_policy_merge_flags_and_invalid_config_remain_unknown(self):
        for line in ('CONFIG_CMDLINE_FORCE=y', 'CONFIG_CMDLINE_EXTEND=y',
                     'CONFIG_CMDLINE_FORCE=invalid', 'CONFIG_IOMMU_DEFAULT_DMA_LAZY=invalid'):
            with self.subTest(line=line):
                config = audit.parse_config('CONFIG_IOMMU_DEFAULT_DMA_STRICT=y\nCONFIG_CMDLINE=""\n' + line)
                self.assertEqual(audit.policy(config, audit.policy_tokens('iommu.passthrough=1'))
                                 ["conditional_default_domain"], "unknown")

    def test_privacy_filter_never_echoes_unrecognized_values(self):
        secret = "sensitive-serial-calibration-mac"
        tokens = audit.policy_tokens(f"androidboot.serialno={secret} iommu.strict={secret} arm-smmu.{secret}=1")
        config = audit.parse_config(f'CONFIG_LOCALVERSION="{secret}"\nCONFIG_CMDLINE="androidboot.serialno={secret}"')
        self.assertNotIn(secret, json.dumps({"tokens": tokens, "config": config}))
        self.nodes["/chosen"] = {"bootargs": f"androidboot.serialno={secret}".encode() + b"\0"}
        self.nodes["/soc/usb/core"]["local-mac-address"] = secret.encode()
        self.assertNotIn(secret, json.dumps(audit.inspect(self.nodes, audit.verifier())))

    def test_32bit_reg_and_framebuffer_coverage_stride(self):
        self.nodes["/soc/fb"] = {"compatible": b"simple-framebuffer\0", "reg": pack(0x1000, 0x1000),
                                 "width": pack(16), "height": pack(16), "stride": pack(64),
                                 "format": b"a8r8g8b8\0"}
        reader = audit.verifier()
        result = audit.inspect(self.nodes, reader, [("test", 0x1000, 0x2000)])["simple_framebuffers"][0]
        self.assertEqual(result["errors"], [])
        self.assertTrue(result["fits_allocation"])
        self.assertFalse(result["reservation_gaps"])
        result = audit.inspect(self.nodes, reader)["simple_framebuffers"][0]
        self.assertEqual(result["reservation_gaps"], [["0x1000", "0x2000"]])
        self.nodes["/soc/fb"]["stride"] = pack(4)
        self.assertIn("short stride", audit.inspect(self.nodes, reader)["simple_framebuffers"][0]["errors"][0])
        self.nodes["/soc/fb"]["stride"] = pack(64)
        self.nodes["/soc"]["ranges"] = pack(0, 0, 0, 0x10000)
        self.assertIn("translation", audit.inspect(self.nodes, reader)["simple_framebuffers"][0]["errors"][0])

    def test_disabled_or_malformed_reservations_do_not_protect_framebuffer(self):
        self.nodes["/soc/fb"] = {"compatible": b"simple-framebuffer\0", "reg": pack(0x1000, 0x1000),
                                 "width": pack(16), "height": pack(16), "stride": pack(64),
                                 "format": b"a8r8g8b8\0"}
        root = {"#address-cells": pack(2), "#size-cells": pack(2), "ranges": b""}
        self.nodes["/reserved-memory"] = root
        reservation = {"reg": pack(0, 0x1000, 0, 0x1000)}
        self.nodes["/reserved-memory/fb"] = reservation
        reader = audit.verifier()
        valid = audit.inspect(self.nodes, reader)["simple_framebuffers"][0]
        self.assertFalse(valid["reservation_gaps"])
        self.assertTrue(valid["reservation_coverage_known"])
        reservation["status"] = b"disabled\0"
        disabled = audit.inspect(self.nodes, reader)["simple_framebuffers"][0]
        self.assertEqual(disabled["reservation_gaps"], [["0x1000", "0x2000"]])
        del reservation["status"]
        for malformed in (pack(0, 0x1000, 0, 0x1000, 0, 0x2000, 0, 0x1000), b"",
                          pack(0xffffffff, 0xfffff000, 0, 0x2000)):
            reservation["reg"] = malformed
            result = audit.inspect(self.nodes, reader)
            self.assertTrue(result["reservation_errors"])
            self.assertFalse(result["simple_framebuffers"][0]["reservation_coverage_known"])
            self.assertEqual(result["simple_framebuffers"][0]["reservation_gaps"], [["0x1000", "0x2000"]])
        reservation["reg"] = pack(0, 0x1000, 0, 0x1000)
        root["#address-cells"] = pack(1)
        result = audit.inspect(self.nodes, reader)
        self.assertTrue(result["reservation_errors"])
        self.assertFalse(result["simple_framebuffers"][0]["reservation_coverage_known"])

    def test_framebuffer_and_memreserve_overflow_is_not_valid_coverage(self):
        self.nodes["/fb"] = {"compatible": b"simple-framebuffer\0",
                             "reg": pack(0xffffffff, 0xfffff000, 0, 0x2000),
                             "width": pack(16), "height": pack(16), "stride": pack(64),
                             "format": b"a8r8g8b8\0"}
        result = audit.inspect(self.nodes, audit.verifier(), [("overflow", (1 << 64) - 4096, (1 << 64) + 4096)])
        self.assertTrue(result["reservation_errors"])
        self.assertIn("overflowing", result["simple_framebuffers"][0]["errors"][0])
        self.assertNotIn("fits_allocation", result["simple_framebuffers"][0])

    def test_cli_validates_hashes_libfdt_exclusive_output_and_permissions(self):
        with tempfile.TemporaryDirectory(prefix="hardware-test-") as directory:
            root = Path(directory)
            runtime = root / "runtime.dtb"
            runtime.write_bytes(dtb(self.nodes))
            config = root / "config"
            config.write_text('CONFIG_IOMMU_DEFAULT_DMA_STRICT=y\nCONFIG_CMDLINE=""\n')
            output = root / "report.json"
            sha = hashlib.sha256(runtime.read_bytes()).hexdigest()
            command = [sys.executable, "-I", str(TOOL), "--runtime", str(runtime), "--runtime-sha256", sha,
                       "--candidate", str(runtime), "--candidate-sha256", sha,
                       "--config", str(config), "--out", str(output)]
            # Poisoned input-directory module must not execute.
            (root / "json.py").write_text("raise RuntimeError('planted module executed')")
            first = subprocess.run(command, cwd=root, text=True, capture_output=True, timeout=15)
            self.assertEqual(first.returncode, 0, first.stderr)
            report = json.loads(output.read_text())
            self.assertEqual(report["inputs"]["runtime"]["libfdt_check_full"], 0)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            original = output.read_bytes()
            again = subprocess.run(command, cwd=root, text=True, capture_output=True, timeout=15)
            self.assertEqual(again.returncode, 2)
            self.assertEqual(output.read_bytes(), original)
            output.unlink()
            runtime.write_bytes(b"invalid FDT")
            mismatch = subprocess.run(command, cwd=root, text=True, capture_output=True, timeout=15)
            self.assertEqual(mismatch.returncode, 2)
            self.assertIn("SHA256 mismatch", mismatch.stderr)
            command[command.index("--runtime-sha256") + 1] = hashlib.sha256(runtime.read_bytes()).hexdigest()
            invalid = subprocess.run(command, cwd=root, text=True, capture_output=True, timeout=15)
            self.assertEqual(invalid.returncode, 2)
            self.assertIn("fdt_check_full", invalid.stderr)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
