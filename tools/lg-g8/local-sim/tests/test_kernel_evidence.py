"""Check evidence boundaries without compiling a kernel or accessing a device."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

TOOL = Path(__file__).resolve().parents[2] / "snapshot-kernel-evidence.py"


class KernelEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="lg-kernel-evidence-")
        self.root = Path(self.temporary.name)
        self.inputs = {"kernel": b"test image", "config": b"CONFIG_QCOM_WDT=m\n",
                       "dtb": b"test tree"}
        for name, data in self.inputs.items():
            (self.root / name).write_bytes(data)
        self.manifest = {"kernel_sha256": self.sha(self.inputs["kernel"]),
                         "files": {"linux.dtb": self.sha(self.inputs["dtb"])}}
        self.manifest_path = self.root / "manifest.json"
        self.save_manifest()
        self.out = self.root / "snapshot.json"

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def sha(data):
        return hashlib.sha256(data).hexdigest()

    def save_manifest(self):
        self.manifest_path.write_text(json.dumps(self.manifest))

    def run_tool(self, *extra):
        command = [sys.executable, "-I", str(TOOL)]
        for name in self.inputs:
            command += ["--" + name, str(self.root / name)]
        return subprocess.run(command + ["--candidate-manifest", str(self.manifest_path),
                                         "--out", str(self.out), *extra],
                              cwd=self.root, capture_output=True, text=True, timeout=10)

    def test_records_identity_without_claiming_config_provenance(self):
        # A planted module in the input directory must never be imported.
        (self.root / "json.py").write_text("raise RuntimeError('input executed')")
        original = self.manifest_path.read_bytes()
        result = self.run_tool("--file", f"selected-source={self.root / 'config'}")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(self.out.read_text())
        self.assertEqual(report["candidate_comparison"]["matches"],
                         {"kernel": True, "dtb": True})
        self.assertNotIn("config", report["candidate_comparison"]["matches"])
        self.assertIn("not historical build provenance", report["scope"])
        self.assertEqual(report["files"]["config"]["sha256"], self.sha(self.inputs["config"]))
        self.assertEqual(self.manifest_path.read_bytes(), original)
        self.assertEqual((self.root / "kernel").read_bytes(), self.inputs["kernel"])
        self.assertEqual(self.out.stat().st_mode & 0o777, 0o600)

    def test_mismatch_is_reported_without_rewriting_claim(self):
        self.manifest["kernel_sha256"] = "0" * 64
        self.save_manifest()
        result = self.run_tool()
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(self.out.read_text())
        self.assertFalse(report["candidate_comparison"]["matches"]["kernel"])
        self.assertEqual(report["candidate_comparison"]["claimed_sha256"]["kernel"], "0" * 64)

    def test_refuses_existing_evidence_and_duplicate_labels(self):
        self.out.write_bytes(b"old evidence")
        self.assertNotEqual(self.run_tool().returncode, 0)
        self.assertEqual(self.out.read_bytes(), b"old evidence")
        self.out.unlink()
        self.assertNotEqual(self.run_tool("--file", f"kernel={self.root / 'config'}").returncode, 0)
        self.assertFalse(self.out.exists())

    def test_missing_input_and_malformed_claim_leave_no_report(self):
        (self.root / "kernel").unlink()
        self.assertNotEqual(self.run_tool().returncode, 0)
        self.assertFalse(self.out.exists())
        (self.root / "kernel").write_bytes(self.inputs["kernel"])
        self.manifest["kernel_sha256"] = "not a digest"
        self.save_manifest()
        self.assertNotEqual(self.run_tool().returncode, 0)
        self.assertFalse(self.out.exists())

    def test_rejects_fifo_without_blocking(self):
        (self.root / "kernel").unlink()
        os.mkfifo(self.root / "kernel")
        result = self.run_tool()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not a regular file", result.stderr)
        self.assertFalse(self.out.exists())

    def test_rejects_output_symlink_without_touching_target(self):
        target = self.root / "old-evidence"
        target.write_bytes(b"keep")
        self.out.symlink_to(target)
        self.assertNotEqual(self.run_tool().returncode, 0)
        self.assertEqual(target.read_bytes(), b"keep")
        target.unlink()
        self.assertNotEqual(self.run_tool().returncode, 0)
        self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
