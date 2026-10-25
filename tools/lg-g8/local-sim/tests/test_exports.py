"""Regression checks for binary transport damage and refusal to guess repairs."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

TOOL = Path(__file__).resolve().parents[2] / "repair-tty-export.py"


class ExportRepairTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="lg-export-regression-")
        self.root = Path(self.temporary.name)
        # Include original LF, CRLF and CR-CR-LF bytes: a valid inverse must
        # preserve all of them, rather than deleting every carriage return.
        compiled = subprocess.run(["dtc", "-I", "dts", "-O", "dtb"],
            input=b'/dts-v1/; / { model = "transport-test"; bytes = [0a 0d 0a 0d 0d 0a]; };',
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        self.clean = compiled.stdout
        self.source = self.root / "runtime.dtb"
        self.damaged = self.clean.replace(b"\n", b"\r\n")
        self.source.write_bytes(self.damaged)
        self.out = self.root / "out"

    def tearDown(self):
        self.temporary.cleanup()

    def run_tool(self):
        return subprocess.run([sys.executable, str(TOOL), str(self.source), "--out", str(self.out)],
                              capture_output=True, text=True)

    def test_repairs_full_export_and_preserves_original_cr_bytes(self):
        result = self.run_tool()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.out / self.source.name).read_bytes(), self.clean)
        self.assertEqual(self.source.read_bytes(), self.damaged)
        self.assertEqual(self.run_tool().returncode, 0)

    def test_refuses_mixed_translation(self):
        self.source.write_bytes(self.damaged + b"\n")
        self.assertNotEqual(self.run_tool().returncode, 0)
        self.assertFalse((self.out / self.source.name).exists())

    def test_refuses_grammar_invalid_candidate(self):
        self.source.write_bytes(self.clean[:-8].replace(b"\n", b"\r\n"))
        self.assertNotEqual(self.run_tool().returncode, 0)
        self.assertFalse((self.out / self.source.name).exists())

    def test_does_not_transform_an_already_clean_tree(self):
        self.source.write_bytes(self.clean)
        self.assertNotEqual(self.run_tool().returncode, 0)
        self.assertFalse((self.out / self.source.name).exists())

    def test_does_not_overwrite_different_output(self):
        self.out.mkdir()
        dest = self.out / self.source.name
        dest.write_bytes(b"previous artifact")
        self.assertNotEqual(self.run_tool().returncode, 0)
        self.assertEqual(dest.read_bytes(), b"previous artifact")


if __name__ == "__main__":
    unittest.main()
