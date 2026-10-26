"""Exercise the native collector on host fixtures; no Android or pstorefs access."""
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import time
import unittest

SOURCE = Path(__file__).resolve().parents[2] / "pstore-snapshot.c"
MIB = 1024 * 1024


class PstoreSnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(prefix="pstore-build-")
        cls.binary = Path(cls.build.name) / "snapshot-test"
        cls.production = Path(cls.build.name) / "snapshot"
        for binary, flags in ((cls.binary, ["-DSNAPSHOT_TESTING"]),
                              (cls.production, [])):
            subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-O2",
                            *flags, str(SOURCE), "-o", str(binary)], check=True,
                           capture_output=True, timeout=30)
        # Inject failures below the collection logic, without production test switches.
        shim = Path(cls.build.name) / "fault.c"
        shim.write_text(r'''
#define _GNU_SOURCE
#include <dirent.h>
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
static int matches(int fd) {
    struct stat st;
    const char *ino = getenv("SNAPSHOT_FAULT_INO");
    return ino && !fstat(fd, &st) && st.st_ino == strtoull(ino, NULL, 10);
}
ssize_t read(int fd, void *buf, size_t n) {
    ssize_t (*real)(int,void *,size_t) = dlsym(RTLD_NEXT,"read");
    const char *fault = getenv("SNAPSHOT_FAULT");
    if (matches(fd) && fault && !strcmp(fault,"read")) { errno=EIO; return -1; }
    return real(fd,buf,n);
}
struct dirent *readdir(DIR *dir) {
    struct dirent *(*real)(DIR *) = dlsym(RTLD_NEXT,"readdir");
    const char *fault = getenv("SNAPSHOT_FAULT");
    if (matches(dirfd(dir)) && fault && !strcmp(fault,"enumerate")) {
        errno=EIO; return NULL;
    }
    return real(dir);
}
int fstatat(int fd, const char *name, struct stat *st, int flags) {
    int (*real)(int,const char *,struct stat *,int) = dlsym(RTLD_NEXT,"fstatat");
    int result=real(fd,name,st,flags);
    const char *fault=getenv("SNAPSHOT_FAULT");
    if (!result && matches(fd) && fault && !strcmp(fault,"changed")) st->st_size++;
    return result;
}
int openat(int fd, const char *path, int flags, ...) {
    int (*real)(int,const char *,int,...) = dlsym(RTLD_NEXT,"openat");
    mode_t mode=0;
    if (flags & O_CREAT) {
        va_list args; va_start(args,flags); mode=(mode_t)va_arg(args,int); va_end(args);
    }
    const char *fault=getenv("SNAPSHOT_FAULT");
    if (fault && !strcmp(fault,"output") && !strncmp(path,"record-",7) && (flags & O_CREAT)) {
        errno=ENOSPC; return -1;
    }
    return real(fd,path,flags,mode);
}
static int record(int fd) {
    char link[64], path[4096];
    snprintf(link,sizeof(link),"/proc/self/fd/%d",fd);
    ssize_t n=readlink(link,path,sizeof(path)-1);
    if (n<0) return 0;
    path[n]=0;
    return strstr(path,"/record-") != NULL;
}
ssize_t write(int fd, const void *buf, size_t n) {
    ssize_t (*real)(int,const void *,size_t) = dlsym(RTLD_NEXT,"write");
    const char *fault=getenv("SNAPSHOT_FAULT");
    if (fault && record(fd) && !strcmp(fault,"write")) {
        off_t pos=lseek(fd,0,SEEK_CUR);
        if (pos>0) { errno=ENOSPC; return -1; }
        if (n>7) n=7;
    }
    return real(fd,buf,n);
}
int close(int fd) {
    int (*real)(int) = dlsym(RTLD_NEXT,"close");
    const char *fault=getenv("SNAPSHOT_FAULT");
    int fail=fault && !strcmp(fault,"close") && record(fd);
    int result=real(fd);
    if (fail) { errno=EIO; return -1; }
    return result;
}
''')
        cls.shim = Path(cls.build.name) / "fault.so"
        subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-shared",
                        "-fPIC", str(shim), "-ldl", "-o", str(cls.shim)], check=True,
                       capture_output=True, timeout=30)

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pstore-case-")
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.out = self.root / "snapshot"

    def tearDown(self):
        self.temporary.cleanup()

    def run_tool(self, *extra, source=None, out=None, fault=None, inode=None):
        env = os.environ.copy()
        if fault:
            env.update(LD_PRELOAD=str(self.shim), SNAPSHOT_FAULT=fault,
                       SNAPSHOT_FAULT_INO=str(inode or self.source.stat().st_ino))
        return subprocess.run([str(self.binary), "--test-source", str(source or self.source),
                               "--out", str(out or self.out), *extra], env=env,
                              capture_output=True, text=True, timeout=10)

    def reports(self):
        status = json.loads((self.out / "status.json").read_text())
        manifest = [json.loads(x) for x in (self.out / "manifest.jsonl").read_text().splitlines()]
        return status, manifest

    def test_empty_directory_is_complete_but_not_a_zero_byte_file(self):
        self.assertEqual(self.run_tool().returncode, 0)
        status, events = self.reports()
        self.assertTrue(status["complete"])
        self.assertEqual(events[-1]["entries"], 0)
        self.assertTrue(events[-1]["complete"])
        self.assertTrue((self.out / "mountinfo").read_bytes())
        self.assertTrue((self.out / "boot_id").read_bytes())

    def test_hashes_binary_names_permissions_and_source_unchanged(self):
        inputs = {}
        for size in (0, 1, 3, 55, 56, 63, 64, 65, 127, 65537):
            name = f"record-{size}\n\"\\"
            data = (bytes(range(256)) * ((size + 255) // 256))[:size]
            (self.source / name).write_bytes(data)
            inputs[os.fsencode(name)] = data
        unusual = os.fsencode(self.source) + b"/nonutf8-\xff"
        with open(unusual, "wb") as stream:
            stream.write(b"abc")
        inputs[b"nonutf8-\xff"] = b"abc"
        self.assertEqual(self.run_tool().returncode, 0)
        status, events = self.reports()
        self.assertTrue(status["complete"])
        files = [x for x in events if x["event"] == "file"]
        self.assertEqual(len(files), len(inputs))
        for item in files:
            name = bytes.fromhex(item["name_hex"])
            data = inputs[name]
            self.assertEqual(item["status"], "ok")
            self.assertEqual(item["bytes"], len(data))
            self.assertEqual(item["sha256"], hashlib.sha256(data).hexdigest())
            self.assertEqual((self.out / item["stored"]).read_bytes(), data)
            with open(os.fsencode(self.source) + b"/" + name, "rb") as stream:
                self.assertEqual(stream.read(), data)
        self.assertEqual(stat.S_IMODE(self.out.stat().st_mode), 0o700)
        for path in self.out.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_existing_directory_and_output_symlink_are_never_overwritten(self):
        self.out.mkdir()
        evidence = self.out / "status.json"
        evidence.write_bytes(b"keep")
        self.assertNotEqual(self.run_tool().returncode, 0)
        self.assertEqual(evidence.read_bytes(), b"keep")
        alias = self.root / "alias"
        alias.symlink_to(self.out, target_is_directory=True)
        self.assertNotEqual(self.run_tool(out=alias).returncode, 0)
        self.assertEqual(evidence.read_bytes(), b"keep")

    def test_ancestor_symlinks_and_traversal_are_rejected(self):
        alias = self.root / "alias"
        alias.symlink_to(self.source, target_is_directory=True)
        self.assertNotEqual(self.run_tool(source=alias).returncode, 0)
        self.assertFalse(self.reports()[0]["complete"])
        self.assertNotEqual(self.run_tool(out=alias / "new").returncode, 0)
        self.assertFalse((self.source / "new").exists())
        self.assertNotEqual(self.run_tool(out=self.source / ".." / "new").returncode, 0)
        self.assertFalse((self.root / "new").exists())

    def test_missing_source_is_incomplete_not_empty(self):
        self.assertNotEqual(self.run_tool(source=self.source / "missing").returncode, 0)
        status, events = self.reports()
        self.assertFalse(status["complete"])
        self.assertIn("source_open_error", [x["event"] for x in events])

    def test_symlink_fifo_and_directory_do_not_block_or_follow(self):
        target = self.root / "target"
        target.write_bytes(b"keep")
        (self.source / "link").symlink_to(target)
        os.mkfifo(self.source / "pipe")
        (self.source / "directory").mkdir()
        self.assertNotEqual(self.run_tool().returncode, 0)
        status, events = self.reports()
        self.assertFalse(status["complete"])
        files = {bytes.fromhex(x["name_hex"]): x for x in events if x["event"] == "file"}
        self.assertEqual(files[b"link"]["status"], "open_error")
        self.assertEqual(files[b"pipe"]["status"], "nonregular")
        self.assertEqual(files[b"directory"]["status"], "nonregular")
        self.assertTrue(all(x["stored"] is None for x in files.values()))
        self.assertEqual(target.read_bytes(), b"keep")

    def test_file_count_boundary(self):
        for i in range(32):
            (self.source / str(i)).touch()
        self.assertEqual(self.run_tool().returncode, 0)
        (self.source / "extra").touch()
        self.assertNotEqual(self.run_tool(out=self.root / "too-many").returncode, 0)
        events = [json.loads(x) for x in (self.root / "too-many" / "manifest.jsonl").read_text().splitlines()]
        self.assertIn("file_count_limit", [x["event"] for x in events])
        self.assertEqual(events[-1]["entries"], 32)
        self.assertFalse(events[-1]["complete"])

    def test_size_boundaries_and_total_limit(self):
        for name in ("a", "b"):
            with (self.source / name).open("wb") as stream:
                stream.truncate(4 * MIB)
        self.assertEqual(self.run_tool().returncode, 0)
        self.assertEqual(self.reports()[1][-1]["bytes"], 8 * MIB)
        (self.source / "c").write_bytes(b"x")
        other = self.root / "over-total"
        self.assertNotEqual(self.run_tool(out=other).returncode, 0)
        events = [json.loads(x) for x in (other / "manifest.jsonl").read_text().splitlines()]
        self.assertIn("total_size_limit", [x.get("status") for x in events])
        self.assertLessEqual(events[-1]["bytes"], 8 * MIB)
        for path in self.source.iterdir():
            path.unlink()
        with (self.source / "oversize").open("wb") as stream:
            stream.truncate(4 * MIB + 1)
        self.assertNotEqual(self.run_tool(out=self.root / "over-file").returncode, 0)
        events = [json.loads(x) for x in (self.root / "over-file" / "manifest.jsonl").read_text().splitlines()]
        self.assertIn("file_size_limit", [x.get("status") for x in events])

    def test_read_enumeration_and_source_change_errors_are_incomplete(self):
        record = self.source / "data"
        record.write_bytes(b"source data")
        for fault, expected, inode in (("read", "read_error", record.stat().st_ino),
                                       ("enumerate", "enumeration_error", self.source.stat().st_ino),
                                       ("changed", "source_changed", self.source.stat().st_ino)):
            with self.subTest(fault=fault):
                self.out = self.root / fault
                self.assertNotEqual(self.run_tool(fault=fault, inode=inode).returncode, 0)
                status, events = self.reports()
                self.assertFalse(status["complete"])
                self.assertIn(expected, [x.get("status", x["event"]) for x in events])
                self.assertFalse(events[-1]["complete"])
        self.assertEqual(record.read_bytes(), b"source data")

    def test_output_creation_failure_is_incomplete_without_saved_record(self):
        (self.source / "data").write_bytes(b"source data")
        self.assertNotEqual(self.run_tool(fault="output").returncode, 0)
        status, events = self.reports()
        self.assertFalse(status["complete"])
        item = next(x for x in events if x["event"] == "file")
        self.assertEqual(item["status"], "output_error")
        self.assertIsNone(item["stored"])
        self.assertFalse(events[-1]["complete"])

    def test_partial_write_and_close_errors_record_actual_saved_bytes(self):
        (self.source / "data").write_bytes(b"source data" * 100)
        for fault in ("write", "close"):
            with self.subTest(fault=fault):
                self.out = self.root / fault
                self.assertNotEqual(self.run_tool(fault=fault).returncode, 0)
                status, events = self.reports()
                self.assertFalse(status["complete"])
                item = next(x for x in events if x["event"] == "file")
                self.assertEqual(item["status"], fault + "_error")
                data = (self.out / item["stored"]).read_bytes()
                self.assertEqual(item["bytes"], len(data))
                self.assertEqual(item["sha256"], hashlib.sha256(data).hexdigest())

    def test_timeout_is_not_a_complete_empty_capture(self):
        start = time.monotonic()
        result = self.run_tool("--test-delay-ms", "2000", "--test-timeout-ms", "50")
        self.assertNotEqual(result.returncode, 0)
        self.assertLess(time.monotonic() - start, 1.5)
        status = json.loads((self.out / "status.json").read_text())
        self.assertEqual(status["state"], "timeout")
        self.assertFalse(status["complete"])
        self.assertFalse((self.out / "manifest.jsonl").exists())

    def test_production_rejects_test_options_and_output_outside_dev(self):
        for args in (("--out", str(self.out)),
                     ("--out", "/dev/never-created", "--test-source", str(self.source)),
                     ("--out", "/dev/never-created", "--test-delay-ms", "1"),
                     ("--out", "/dev/never-created", "--test-timeout-ms", "1")):
            result = subprocess.run([str(self.production), *args], capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 2)
        self.assertFalse(self.out.exists())


if __name__ == "__main__":
    unittest.main()
