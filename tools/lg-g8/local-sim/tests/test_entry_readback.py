"""Offline EP2 readback boundaries; all inputs are synthetic."""
import importlib.util
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib

PATH = Path(__file__).resolve().parents[1] / "entry-probe/record.py"
spec = importlib.util.spec_from_file_location("entry_record", PATH)
record = importlib.util.module_from_spec(spec)
spec.loader.exec_module(record)


class ReadbackTests(unittest.TestCase):
    def setUp(self):
        self.manifest = {"console": record.CONSOLE, "zone_size": record.ZONE_SIZE,
                         "target": 0xAA000000, "nonce": "1" * 32,
                         "symbols": {"stage2": 0x200}}
        self.parts = []
        for phase in (1, 2):
            data = bytearray(record.template(self.manifest["nonce"]))
            struct.pack_into("<11Q", data, 40, phase, 0x96000000, 0, 0, 0,
                             4, 0, 0x140, 0x90000000, 0x90000200,
                             0x90000000 if phase == 1 else 0xAA000000)
            struct.pack_into("<I", data, 128, zlib.crc32(data[:128]))
            self.parts.append(bytes(data))
        self.payload = b"".join(self.parts)

    def test_export_and_raw_agree(self):
        exported = record.readback(self.payload, self.manifest, "pstore-console")
        raw = record.readback(record.encode(self.payload), self.manifest, "raw-zone")
        self.assertEqual(raw, exported)
        self.assertEqual(raw["observation"], "enter-target")

    def test_enter_only(self):
        self.assertEqual(record.readback(self.parts[0], self.manifest, "pstore-console")
                         ["observation"], "enter-only")

    def test_no_auto_detection_or_substring_salvage(self):
        for data, fmt in [(self.payload, "raw-zone"),
                          (record.encode(self.payload), "pstore-console"),
                          (b"prefix" + self.payload, "pstore-console"),
                          (self.payload + b"tail", "pstore-console"),
                          (b"", "pstore-console"),
                          (self.payload[:-1], "pstore-console")]:
            with self.subTest(fmt=fmt, length=len(data)), self.assertRaises(ValueError):
                record.readback(data, self.manifest, fmt)

    def test_stale_corrupt_phase_and_partial_header_rejected(self):
        stale = dict(self.manifest, nonce="2" * 32)
        with self.assertRaises(ValueError):
            record.readback(self.payload, stale, "pstore-console")
        for payload in [self.parts[1], self.parts[0] * 2,
                        self.payload[:60] + bytes([self.payload[60] ^ 1]) + self.payload[61:]]:
            with self.assertRaises(ValueError):
                record.readback(payload, self.manifest, "pstore-console")
        raw = bytearray(record.encode(self.payload))
        struct.pack_into("<III", raw, 0, record.SIGNATURE, 272, 136)
        with self.assertRaises(ValueError):
            record.readback(raw, self.manifest, "raw-zone")

    def test_layout_rejected(self):
        for key in ("console", "zone_size", "target"):
            with self.assertRaises(ValueError):
                record.readback(self.payload, dict(self.manifest, **{key: 0}), "pstore-console")

    def test_cli_input_and_manifest_errors_report_inconclusive(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            source, manifest = root / "input", root / "manifest"
            cases = [
                (None, json.dumps(self.manifest).encode()),
                (self.payload, None),
                (self.payload, b"{invalid"),
                (self.payload, b"\xff"),
                (self.payload, b"null"),
                (self.payload, b"[]"),
                (self.payload, b"{}"),
                (self.payload, json.dumps(dict(self.manifest, nonce=None)).encode()),
                (self.payload, json.dumps(dict(self.manifest, symbols=None)).encode()),
            ]
            for index, (data, manifest_data) in enumerate(cases):
                with self.subTest(index=index):
                    for path, content in ((source, data), (manifest, manifest_data)):
                        if content is None:
                            path.unlink(missing_ok=True)
                        else:
                            path.write_bytes(content)
                    output = root / f"report-{index}"
                    command = [sys.executable, "-I", str(PATH), str(source),
                               "--manifest", str(manifest), "--format", "pstore-console",
                               "--out", str(output)]
                    completed = subprocess.run(command, capture_output=True)
                    self.assertEqual(completed.returncode, 1, completed.stderr)
                    result = json.loads(output.read_bytes())
                    self.assertEqual(json.loads(completed.stdout), result)
                    self.assertFalse(result["valid_record"])
                    self.assertEqual(result["observation"], "inconclusive")
                    self.assertTrue(result["error"])
                    self.assertNotIn(b"Traceback", completed.stderr)
                    self.assertEqual(output.stat().st_mode & 0o777, 0o600)
                    self.assertEqual(result["input_sha256"],
                                     hashlib.sha256(data).hexdigest() if data is not None else None)
                    self.assertEqual(result["manifest_sha256"],
                                     hashlib.sha256(manifest_data).hexdigest()
                                     if data is not None and manifest_data is not None else None)
                    previous = output.read_bytes()
                    self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
                    self.assertEqual(output.read_bytes(), previous)

    def test_cli_reports_inconclusive_and_preserves_output(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            source, manifest, output = [root / p for p in ("input", "manifest", "report")]
            source.write_bytes(b"")
            manifest.write_text(json.dumps(self.manifest))
            command = [sys.executable, "-I", str(PATH), str(source), "--manifest", str(manifest),
                       "--format", "pstore-console", "--out", str(output)]
            first = subprocess.run(command, capture_output=True)
            self.assertEqual(first.returncode, 1)
            result = json.loads(output.read_bytes())
            self.assertFalse(result["valid_record"])
            self.assertEqual(result["observation"], "inconclusive")
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            previous = output.read_bytes()
            source.write_bytes(self.payload)
            self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
            self.assertEqual(output.read_bytes(), previous)
            output.unlink()
            self.assertEqual(subprocess.run(command, capture_output=True).returncode, 0)
            self.assertEqual(json.loads(output.read_bytes())["observation"], "enter-target")


if __name__ == "__main__":
    unittest.main()
