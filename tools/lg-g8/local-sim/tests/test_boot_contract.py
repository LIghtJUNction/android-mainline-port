#!/usr/bin/env python3
"""Synthetic offline boot-contract tests; requires system dtc and libfdt."""
import gzip
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

TOOL = Path(__file__).resolve().parents[2] / "audit-boot-contract.py"
spec = importlib.util.spec_from_file_location("audit_boot_contract", TOOL)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def make_fdt(reserved="", memreserve="", extra="", cells=2,
             parent_properties=None, memory_properties="", force=False):
    def reg(base, size):
        return f"<0 {base:#x} 0 {size:#x}>" if cells == 2 else f"<{base:#x} {size:#x}>"
    if parent_properties is None:
        parent_properties = f"#address-cells = <{cells}>; #size-cells = <{cells}>; ranges;"
    source = f"""/dts-v1/;
{memreserve}
/ {{ #address-cells = <{cells}>; #size-cells = <{cells}>;
 memory@80000000 {{ device_type = "memory"; reg = {reg(0x80000000, 0x10000000)}; {memory_properties} }};
 reserved-memory {{ {parent_properties}
 {reserved}
 }};
 {extra}
}};
"""
    return subprocess.run(["dtc"] + (["-f"] if force else []) + ["-I", "dts", "-O", "dtb", "-"],
                          input=source.encode(), capture_output=True, check=True).stdout


def reservation(name, base, size, properties=""):
    return f"{name}@{base:x} {{ reg = <0 {base:#x} 0 {size:#x}>; {properties} }};"


def raw_image(size=0x30000, file_size=0x10000, text=0, flags=8):
    blob = bytearray(file_size)
    struct.pack_into("<QQQ", blob, 8, text, size, flags)
    blob[56:60] = b"ARM\x64"
    return bytes(blob)


def boot(dtb, kernel=None, ramdisk=b"test initramfs", addresses=None):
    addresses = addresses or (0x80200000, 0x81000000, 0x82000000)
    kernel = raw_image() if kernel is None else kernel
    header = bytearray(4096)
    header[:8] = b"ANDROID!"
    struct.pack_into("<10I", header, 8, len(kernel), addresses[0], len(ramdisk), addresses[1],
                     0, 0, 0, 4096, 2, 0)
    struct.pack_into("<IIQ", header, 1644, 1660, len(dtb), addresses[2])
    return bytes(header) + b"".join(part + b"\0" * (audit.align(len(part), 4096) - len(part))
                                  for part in (kernel, ramdisk, dtb))


class ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.simple = make_fdt()

    def checks(self, result):
        return [issue["check"] for issue in result["issues"]]

    def test_known_ranges_pass_is_still_incomplete(self):
        result = audit.audit(boot(self.simple), self.simple, [self.simple])
        self.assertTrue(result["known_range_checks_passed"])
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["bootability"], "not established")
        self.assertIn("hypothesis", result["address_provenance"]["kernel"]["source"])
        self.assertEqual(result["candidate_matches_embedded_indices"], [0])

    def test_runtime_bss_overlap_not_just_file_length(self):
        image = boot(self.simple, kernel=raw_image(size=0x40000),
                     addresses=(0x80200000, 0x80220000, 0x82000000))
        result = audit.audit(image, self.simple, [self.simple])
        self.assertIn("pairwise load overlap", self.checks(result))
        self.assertEqual(result["image_runtime_loads"][0]["size"], 0x40000)

    def test_adjacent_loads_do_not_overlap(self):
        image = boot(self.simple, kernel=raw_image(size=0x20000),
                     addresses=(0x80200000, 0x80220000, 0x82000000))
        self.assertEqual(audit.audit(image, self.simple, [self.simple])["pairwise_load_overlaps"], [])

    def test_split_coverage_is_union(self):
        reference = make_fdt(reservation("firmware", 0x85000000, 0x40000))
        candidate = make_fdt(reservation("a", 0x85000000, 0x20000) +
                             reservation("b", 0x85020000, 0x20000))
        result = audit.audit(boot(candidate), candidate, [reference])
        self.assertTrue(result["known_range_checks_passed"])
        self.assertEqual(result["contracts"][1]["reservations_missing_from_candidate"], [])

    def test_reusable_cannot_replace_permanent_firmware_reservation(self):
        reference = make_fdt(reservation("firmware", 0x85000000, 0x40000))
        candidate = make_fdt(reservation("cma", 0x85000000, 0x40000, "reusable;"))
        result = audit.audit(boot(candidate), candidate, [reference])
        self.assertEqual(result["contracts"][1]["reservations_missing_from_candidate"], [])
        self.assertTrue(result["contracts"][1]["permanent_reservations_missing_from_candidate"])
        self.assertFalse(result["known_range_checks_passed"])

    def test_single_byte_union_hole_detected(self):
        reference = make_fdt(reservation("firmware", 0x85000000, 0x40000))
        candidate = make_fdt(reservation("a", 0x85000000, 0x20000) +
                             reservation("b", 0x85020001, 0x1ffff))
        result = audit.audit(boot(candidate), candidate, [reference])
        hole = result["contracts"][1]["reservations_missing_from_candidate"]
        self.assertEqual([(h["start"], h["size"]) for h in hole], [("0x85020000", 1)])

    def test_memreserve_collision_and_union_coverage(self):
        reference = make_fdt(memreserve="/memreserve/ 0x80210000 0x1000;")
        result = audit.audit(boot(self.simple), self.simple, [reference])
        self.assertIn("load intersects reference[0] reservation", self.checks(result))
        self.assertIn("reference[0] reservation union not covered", self.checks(result))

    def test_reusable_load_collision(self):
        candidate = make_fdt(reservation("cma", 0x81000000, 0x10000, "reusable;"))
        result = audit.audit(boot(candidate), candidate, [candidate])
        self.assertIn("load intersects candidate reservation", self.checks(result))

    def test_all_references_checked(self):
        other = make_fdt(reservation("firmware", 0x82000000, 0x10000))
        result = audit.audit(boot(self.simple), self.simple, [self.simple, other])
        self.assertIn("load intersects reference[1] reservation", self.checks(result))

    def test_load_outside_ram_and_override_provenance(self):
        result = audit.audit(boot(self.simple), self.simple, [self.simple], {"ramdisk": 0x90000000})
        self.assertIn("load outside candidate RAM", self.checks(result))
        self.assertEqual(result["address_provenance"]["ramdisk"]["source"], "explicit scenario override")

    def test_candidate_exposes_unreferenced_ram(self):
        candidate = make_fdt(extra='memory@a0000000 { device_type="memory"; reg=<0 0xa0000000 0 0x10000>; };')
        result = audit.audit(boot(candidate), candidate, [self.simple])
        self.assertIn("candidate exposes RAM outside reference[0]", self.checks(result))

    def test_dynamic_and_disabled_reservations(self):
        candidate = make_fdt('dynamic { size=<0 0x2000>; };' +
                             reservation("disabled", 0x81000000, 0x1000, 'status="disabled";'))
        result = audit.audit(boot(candidate), candidate, [candidate])
        self.assertTrue(result["known_range_checks_passed"])
        self.assertTrue(any("allocation unknown" in msg for msg in result["unknowns"]))
        self.assertEqual(result["contracts"][0]["reserved"], [])

    def test_stock_ram_placeholder_is_unknown_not_empty_coverage(self):
        needle = struct.pack(">QQ", 0x80000000, 0x10000000)
        self.assertEqual(self.simple.count(needle), 1)
        placeholder = self.simple.replace(needle, b"\0" * 16)
        result = audit.audit(boot(placeholder), placeholder, [self.simple])
        self.assertIsNone(result["contracts"][0]["loads_outside_ram"])
        self.assertFalse(result["contracts"][0]["ram_coverage_known"])
        self.assertTrue(any("placeholder" in msg for msg in result["unknowns"]))
        reverse = audit.audit(boot(self.simple), self.simple, [placeholder])
        self.assertIsNone(reverse["contracts"][1]["candidate_ram_outside_reference"])
        self.assertTrue(reverse["known_range_checks_passed"])

    def test_one_cell_ram_and_reservations(self):
        blob = make_fdt('firmware@85000000 {reg=<0x85000000 0x1000>;};', cells=1)
        self.assertEqual(audit.fdt_contract(blob)["reserved"][0][1:], (0x85000000, 0x85001000))

    def test_compressed_unknown_and_legacy_runtime_unknown(self):
        for payload, kind in ((gzip.compress(raw_image()), "gzip"), (b"opaque", "unknown"),
                              (raw_image(size=0), "arm64-Image")):
            with self.subTest(kind=kind):
                result = audit.audit(boot(self.simple, kernel=payload), self.simple, [self.simple],
                                     {"kernel": 0x80280000})
                self.assertEqual(result["arm64_image"]["format"], kind)
                self.assertIsNone(result["arm64_image"]["runtime_size"])
                self.assertEqual(result["image_runtime_loads"], [])
                self.assertEqual(result["status"], "incomplete")
                self.assertTrue(result["arm64_image"]["unknowns"])

    def test_external_dtb_extent_is_not_truncated_to_original_table(self):
        candidate = make_fdt(extra='test-node {large-property = "' + 'x' * 2000 + '";};')
        result = audit.audit(boot(self.simple), candidate, [self.simple])
        extent = next(item for item in result["payload_loads"] if item["name"] == "dtb payload")
        self.assertEqual(extent["size"], len(candidate))
        self.assertEqual(result["candidate_matches_embedded_indices"], [])

    def test_arm64_text_offset_placement(self):
        image = boot(self.simple, kernel=raw_image(text=0x80000))
        self.assertIn("arm64 Image placement", self.checks(audit.audit(image, self.simple, [self.simple])))
        aligned = audit.audit(image, self.simple, [self.simple], {"kernel": 0x80280000})
        self.assertNotIn("arm64 Image placement", self.checks(aligned))

    def test_v2_sections_and_header_fail_closed(self):
        valid = boot(self.simple)
        mutations = []
        for offset, value, fmt in ((40, 0, "<I"), (1644, 1661, "<I"), (36, 0, "<I"),
                                   (24, 1, "<I"), (28, 1, "<I"), (1632, 1, "<I"),
                                   (1636, 1, "<Q"), (1648, 0, "<I"), (8, 0xffffffff, "<I")):
            changed = bytearray(valid)
            struct.pack_into(fmt, changed, offset, value)
            mutations.append(bytes(changed))
        mutations += [valid[:100], valid[:-1], valid[:4096 + 10]]
        for image in mutations:
            with self.subTest(length=len(image)):
                with self.assertRaises(ValueError):
                    audit.parse_boot(image)

    def test_invalid_fdt_lengths_grammar_and_overflow(self):
        broken = bytearray(self.simple)
        struct.pack_into(">I", broken, struct.unpack_from(">I", broken, 8)[0], 0xdeadbeef)
        overflowing = make_fdt('bad {reg=<0xffffffff 0xfffffff0 0 0x100>;};')
        badreg = make_fdt('bad {reg=<0 1 0>;};')
        for blob in (self.simple[:-1], self.simple + b"\0", bytes(broken), overflowing, badreg, b"bad"):
            with self.subTest(length=len(blob)):
                with self.assertRaises(ValueError):
                    audit.fdt_contract(blob)

    def test_concatenated_fdt_validation(self):
        self.assertEqual(len(audit.embedded_fdts(self.simple * 2)), 2)
        for blob in (self.simple + b"\0", self.simple[:-1], b"\0" * 40):
            with self.assertRaises(ValueError):
                audit.embedded_fdts(blob)

    def test_ambiguous_translation_rejected(self):
        blob = make_fdt().replace(b"ranges\0", b"rangex\0")
        with self.assertRaisesRegex(ValueError, "explicit empty ranges"):
            audit.fdt_contract(blob)

    def test_reserved_memory_requires_explicit_cells(self):
        for properties in ("ranges;", "#address-cells=<2>; ranges;", "#size-cells=<2>; ranges;"):
            with self.subTest(properties=properties):
                blob = make_fdt(reservation("firmware", 0x85000000, 0x10000),
                                parent_properties=properties)
                with self.assertRaisesRegex(ValueError, "requires explicit address and size cells"):
                    audit.audit(boot(blob), blob, [self.simple])

    def test_multiple_reserved_reg_tuples_not_credited(self):
        blob = make_fdt('firmware@84000000 {reg=<0 0x84000000 0 0x10000 '
                        '0 0x85000000 0 0x10000>;};')
        reference = make_fdt(reservation("firmware", 0x85000000, 0x10000))
        with self.assertRaisesRegex(ValueError, "multiple reserved-memory reg tuples"):
            audit.audit(boot(blob), blob, [reference])

    def test_disabled_reserved_parent_is_not_silently_skipped(self):
        blob = make_fdt(reservation("firmware", 0x81000000, 0x10000),
                        parent_properties='#address-cells=<2>; #size-cells=<2>; ranges; status="disabled";')
        with self.assertRaisesRegex(ValueError, "disabled reserved-memory parent"):
            audit.fdt_contract(blob)

    def test_reserved_boolean_grammar(self):
        for properties in ('no-map; reusable;', 'no-map=<1>;', 'reusable=<0>;'):
            with self.subTest(properties=properties):
                blob = make_fdt(reservation("firmware", 0x85000000, 0x10000, properties))
                with self.assertRaises(ValueError):
                    audit.fdt_contract(blob)

    def test_linux_usable_memory_cannot_certify_broader_reg(self):
        restricted = make_fdt(memory_properties='linux,usable-memory=<0 0x80000000 0 0x100000>;')
        chosen = make_fdt(extra='chosen {linux,usable-memory-range=<0 0x80000000 0 0x100000>;};')
        for blob in (restricted, chosen):
            with self.subTest(size=len(blob)):
                with self.assertRaisesRegex(ValueError, "usable-memory restrictions"):
                    audit.audit(boot(blob), blob, [self.simple])

    def test_libfdt_valid_duplicates_cannot_hide_reservation(self):
        duplicate_node = make_fdt(reservation("firmware", 0x85000000, 0x10000) +
                                  'firmware@85000000 {};', force=True)
        duplicate_property = make_fdt('firmware@85000000 {reg=<0 0x85000000 0 0x10000>; '
                                      'reg=<0 0x86000000 0 0x10000>;};', force=True)
        for blob in (duplicate_node, duplicate_property):
            with self.subTest(size=len(blob)):
                # The dependency accepts these, so this tests the added guard.
                audit.dtb.tree(audit.FdtBytes(blob))
                with self.assertRaisesRegex(ValueError, "duplicate FDT"):
                    audit.audit(boot(blob), blob, [self.simple])
                # Unselected embedded trees must receive the same validation.
                with self.assertRaisesRegex(ValueError, "duplicate FDT"):
                    audit.audit(boot(self.simple + blob), self.simple, [self.simple])

    def test_legacy_image_assumes_protocol_text_offset(self):
        image = bytearray(raw_image(size=0))
        struct.pack_into(">Q", image, 8, 0x80000)
        result = audit.audit(boot(self.simple, kernel=bytes(image)), self.simple, [self.simple],
                             {"kernel": 0x80280000})
        self.assertEqual(result["arm64_image"]["text_offset"], 0x80000)
        self.assertNotIn("arm64 Image placement", self.checks(result))
        self.assertIsNone(result["arm64_image"]["runtime_size"])

    def test_arm64_dtb_alignment_and_size(self):
        result = audit.audit(boot(self.simple), self.simple, [self.simple], {"dtb": 0x82000001})
        self.assertIn("arm64 DTB alignment", self.checks(result))
        # Extend FDT padding only; libfdt still validates it as one exact tree.
        large = bytearray(self.simple + b"\0" * (0x200001 - len(self.simple)))
        struct.pack_into(">I", large, 4, len(large))
        result = audit.audit(boot(bytes(large)), bytes(large), [self.simple])
        self.assertIn("arm64 DTB size", self.checks(result))

    def test_arm64_initrd_common_window(self):
        for ramdisk, expected in ((0x87ffff000, False), (0x880000000, True)):
            with self.subTest(ramdisk=ramdisk):
                result = audit.audit(boot(self.simple), self.simple, [self.simple],
                                     {"kernel": 0x80200000, "ramdisk": ramdisk})
                self.assertEqual("arm64 initrd window" in self.checks(result), expected)

    def test_cli_exclusive_report_and_reference_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            boot_path, fdt_path, report = root / "boot.img", root / "tree.dtb", root / "report.json"
            boot_path.write_bytes(boot(self.simple))
            fdt_path.write_bytes(self.simple)
            cmd = [sys.executable, "-I", str(TOOL), "--boot", str(boot_path),
                   "--candidate-dtb", str(fdt_path), "--reference-fdt", str(fdt_path),
                   "--expect-reference-sha256", audit.sha(self.simple), "--report", str(report)]
            first = subprocess.run(cmd, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            evidence = report.read_bytes()
            self.assertEqual(json.loads(evidence)["sources"]["boot"]["sha256"], audit.sha(boot_path.read_bytes()))
            second = subprocess.run(cmd, capture_output=True, text=True)
            self.assertEqual(second.returncode, 2)
            self.assertEqual(report.read_bytes(), evidence)
            bad_cmd = cmd.copy()
            bad_cmd[bad_cmd.index(audit.sha(self.simple))] = "0" * 64
            bad_cmd[-1] = str(root / "bad-report.json")
            bad = subprocess.run(bad_cmd, capture_output=True, text=True)
            self.assertEqual(bad.returncode, 2)
            self.assertEqual(json.loads((root / "bad-report.json").read_bytes())["status"], "invalid-input")
            self.assertEqual(boot_path.read_bytes(), boot(self.simple))


if __name__ == "__main__":
    unittest.main()
