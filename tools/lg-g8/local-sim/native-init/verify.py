#!/usr/bin/env python3
"""Build a useful diagnostic initramfs and exercise its shell through virtual USB."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import re
import stat
import subprocess
import sys

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("linux_local_sim", HERE.parent / "run_linux.py")
sim = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sim)
REPO = HERE.parents[3]

SIM_INIT = b'''#!/bin/sh
/bin/busybox mount -t devtmpfs devtmpfs /dev
/bin/busybox mount -t proc proc /proc
/bin/busybox mount -t sysfs sysfs /sys
/bin/busybox --list | /bin/busybox grep -x getty || exit 1
echo LOCAL-SIM NATIVE_GETTY_APPLET_OK
/bin/busybox insmod /sim/dummy_hcd.ko || exit 1
/bin/busybox insmod /sim/cdc-acm.ko || exit 1
(/bin/busybox sleep 2; /bin/busybox sh /sim-native-assert) &
exec /init
'''

SIM_ASSERT = b'''#!/bin/sh
exec >/dev/console 2>&1
BB=/bin/busybox
fail() {
    echo "LOCAL-SIM FAIL: $*"
    $BB cat /run/usb-native-shell.log 2>/dev/null
    $BB dmesg | $BB tail -n 50
    $BB poweroff -f
    exit 1
}
wait_for() {
    pattern=$1
    i=0
    while [ "$i" -lt 150 ]; do
        $BB grep -Eq "$pattern" /run/usb-native-shell.log 2>/dev/null && return 0
        $BB sleep 0.1
        i=$((i+1))
    done
    return 1
}
i=0
while [ ! -c /dev/ttyACM0 ] && [ "$i" -lt 30 ]; do
    $BB sleep 1
    i=$((i+1))
done
[ -c /dev/ttyACM0 ] || fail 'USB host ACM did not enumerate'
: >/run/usb-native-shell.log
start_client() {
    exec 3<>/dev/ttyACM0
    $BB stty raw -echo -hupcl clocal <&3 || fail 'host termios'
    $BB cat <&3 >>/run/usb-native-shell.log &
    reader=$!
    query=$(printf '\\033[6n')
    seen=$($BB grep -F -o "$query" /run/usb-native-shell.log | $BB wc -l)
    (
        responded=$seen
        while :; do
            queries=$($BB grep -F -o "$query" /run/usb-native-shell.log | $BB wc -l)
            while [ "$responded" -lt "$queries" ]; do
                printf '\\033[1;1R' >&3
                responded=$((responded+1))
            done
            $BB sleep 0.1
        done
    ) &
    terminal=$!
}
stop_client() {
    kill "$reader" "$terminal" 2>/dev/null || true
    wait "$reader" 2>/dev/null || true
    wait "$terminal" 2>/dev/null || true
    exec 3>&-
}
start_client
# USB getty deliberately waits for Enter until a real client has set raw mode.
printf '\\r' >&3
wait_for LGG8-DIAGNOSTIC-SESSION || fail 'first getty session absent'
$BB sleep 0.2
# Expected expanded results are absent from both transmitted command strings.
printf '%s\r' 'v=$((317*29)); printf "%s" "$v" >/run/usb-native-result; printf "\\nNATIVE_RESULT:%s:%s:%s:%s\\n" "$(/bin/busybox id -u)" "$(/bin/busybox uname -m)" "$(/bin/cat /run/usb-native-result)" "$$"' >&3
wait_for 'NATIVE_RESULT:0:aarch64:9193:[0-9]+' || fail 'first shell command result'
first=$($BB grep -o 'NATIVE_RESULT:0:aarch64:9193:[0-9]*' /run/usb-native-shell.log | $BB tail -n 1)
first_pid=${first##*:}
echo "LOCAL-SIM USB_SHELL_RESPONSE=$first"
printf 'exit\r' >&3
stop_client
$BB sleep 1.5
start_client
printf '\\r' >&3
echo 'LOCAL-SIM USB_CLIENT_REOPENED'
i=0
new_session_pid=$first_pid
while [ "$i" -lt 150 ]; do
    banner=$($BB grep -o 'LGG8-DIAGNOSTIC-SESSION pid=[0-9]*' /run/usb-native-shell.log | $BB tail -n 1)
    new_session_pid=${banner##*=}
    [ -n "$new_session_pid" ] && [ "$new_session_pid" != "$first_pid" ] && break
    $BB sleep 0.1
    i=$((i+1))
done
[ -n "$new_session_pid" ] && [ "$new_session_pid" != "$first_pid" ] || fail 'getty did not restart after exit'
$BB sleep 0.2
printf '%s\r' 'printf "\\nNATIVE_RECONNECT:%s:%s:%s\\n" "$((457*13))" "$(/bin/cat /run/usb-native-result)" "$$"' >&3
wait_for 'NATIVE_RECONNECT:5941:9193:[0-9]+' || fail 'reconnected shell command result'
second=$($BB grep -o 'NATIVE_RECONNECT:5941:9193:[0-9]*' /run/usb-native-shell.log | $BB tail -n 1)
second_pid=${second##*:}
[ "$first_pid" != "$second_pid" ] || fail 'session process did not change'
[ "$first_pid" -gt 1 ] && [ "$second_pid" -gt 1 ] || fail 'invalid shell process IDs'
echo "LOCAL-SIM USB_RECONNECT_RESPONSE=$second"
kill -0 1 || fail 'PID1 died'
pid1=$($BB tr '\\000' ' ' </proc/1/cmdline)
[ "$pid1" = '/bin/sh /init ' ] || fail "unexpected PID1 $pid1"
echo "LOCAL-SIM NATIVE_PID1_ALIVE=$pid1"
if [ -d /sys/firmware/efi ]; then
    echo 'LOCAL-SIM NATIVE_UEFI_PRESENT'
fi
$BB dmesg | $BB grep -q 'LGG8-NATIVE: INIT pid=1' || fail 'native init marker absent'
echo 'LOCAL-SIM USB_TRANSCRIPT_BEGIN'
$BB cat /run/usb-native-shell.log
echo 'LOCAL-SIM USB_TRANSCRIPT_END'
stop_client
echo 'LOCAL-SIM NATIVE_SHELL_PASS'
$BB poweroff -f
'''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--initramfs", type=Path, default=REPO / "out/local-sim/software/initramfs.cpio")
    parser.add_argument("--kernel", type=Path, default=REPO / "out/local-sim/software/kernel.efi")
    parser.add_argument("--usb-module-dir", type=Path, default=REPO / "out/local-sim/usb-modules")
    parser.add_argument("--out", type=Path, default=HERE / "out")
    parser.add_argument("--timeout", type=float, default=80)
    parser.add_argument("--shell", type=Path, help="Use this standard ARM64 Shell.efi through UEFI instead of direct boot")
    parser.add_argument("--firmware", type=Path, default=Path("/usr/share/edk2/aarch64/QEMU_EFI.fd"))
    parser.add_argument("--qemu-dtb", type=Path, help="Matching generated QEMU virt DTB; never provide the LG DTB here")
    args = parser.parse_args()
    if args.qemu_dtb and not args.shell:
        parser.error("--qemu-dtb requires --shell")
    if args.qemu_dtb:
        model = subprocess.run(["fdtget", "-t", "s", str(args.qemu_dtb), "/", "model"],
                               capture_output=True, text=True, check=True).stdout.strip()
        if model != "LGG8-QEMU-EXTERNAL-DTB-PROBE":
            parser.error("only the matching QEMU external-DTB test input is accepted")
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    report_path = out / "report.json"
    report_path.unlink(missing_ok=True)
    original = args.initramfs.read_bytes()
    entries = sim.read_cpio(original)
    if any(name.startswith("sim") or name.endswith(".ko") for name in entries):
        raise ValueError("input must be a clean runtime archive without a simulation harness")
    original_init = entries.get("init")
    original_init_sha = sim.digest(original_init[1]) if original_init else None
    original_busybox = entries["bin/busybox"]
    interpreter = sim.interpreter(original_busybox[1])
    if interpreter and interpreter.lstrip("/") not in entries:
        raise ValueError(f"BusyBox interpreter missing: {interpreter}")
    entries["init"] = stat.S_IFREG | 0o755, (HERE / "init").read_bytes(), 0, 0
    entries["bin/diag-login"] = stat.S_IFREG | 0o755, (HERE / "diag-login").read_bytes(), 0, 0
    entries["root"] = stat.S_IFDIR | 0o700, b"", 0, 0
    clean = sim.write_cpio(entries)
    clean_path = out / "initramfs-native.cpio"
    clean_path.write_bytes(clean)
    checked = sim.read_cpio(clean)
    assert checked["bin/busybox"] == original_busybox
    assert not any(name.startswith("sim") or name.endswith(".ko") for name in checked)
    tests = dict(checked)
    tests["sim-init"] = stat.S_IFREG | 0o755, SIM_INIT, 0, 0
    tests["sim-native-assert"] = stat.S_IFREG | 0o755, SIM_ASSERT, 0, 0
    tests["sim"] = stat.S_IFDIR | 0o755, b"", 0, 0
    for name in ("dummy_hcd.ko", "cdc-acm.ko"):
        tests[f"sim/{name}"] = stat.S_IFREG | 0o644, (args.usb_module_dir / name).read_bytes(), 0, 0
    test_path = out / "initramfs-test.cpio"
    test_path.write_bytes(sim.write_cpio(tests))
    esp_manifest = None
    if args.shell:
        esp = out / "esp"
        esp_manifest = sim.build_esp(args.shell, args.kernel, test_path, esp,
            "console=ttyAMA0,115200 earlycon=pl011,0x09000000 acpi=off rdinit=/sim-init panic=0 loglevel=7", args.qemu_dtb)
        boot = ["-bios", str(args.firmware.resolve()),
                "-drive", f"if=none,id=esp,format=raw,readonly=on,file=fat:ro:{esp}",
                "-device", "virtio-blk-device,drive=esp,bootindex=1"]
    else:
        boot = ["-kernel", str(args.kernel.resolve()), "-initrd", str(test_path),
                "-append", "console=ttyAMA0,115200 rdinit=/sim-init panic=0"]
    result = sim.run_qemu(boot, out / "qemu.log", args.timeout, "LOCAL-SIM NATIVE_SHELL_PASS")
    text = (out / "qemu.log").read_text(errors="replace")
    sent = [value.decode() for value in re.findall(rb"printf '%s\r' '([^']*)' >&3", SIM_ASSERT)]
    first = re.search(r"LOCAL-SIM USB_SHELL_RESPONSE=NATIVE_RESULT:0:aarch64:9193:(\d+)", text)
    second = re.search(r"LOCAL-SIM USB_RECONNECT_RESPONSE=NATIVE_RECONNECT:5941:9193:(\d+)", text)
    markers = ["LOCAL-SIM NATIVE_GETTY_APPLET_OK", "LOCAL-SIM USB_SHELL_RESPONSE=NATIVE_RESULT:0:aarch64:9193:",
               "LOCAL-SIM USB_RECONNECT_RESPONSE=NATIVE_RECONNECT:5941:9193:", "LOCAL-SIM NATIVE_PID1_ALIVE=",
               "LOCAL-SIM USB_CLIENT_REOPENED"]
    checks = {"real_usb_shell_execution_and_reconnect": result["matched"] and all(marker in text for marker in markers),
              "session_processes_differ": bool(first and second and first[1] != second[1]),
              "expanded_values_absent_from_transmitted_commands": len(sent) == 2 and "9193" not in sent[0] and "5941" not in sent[1] and "9193" not in sent[1],
              "busybox_unchanged": checked["bin/busybox"] == original_busybox,
              "clean_archive_has_no_simulation_payload": not any(name.startswith("sim") or name.endswith(".ko") for name in checked)}
    if args.shell:
        checks["standard_shell_and_uefi"] = "LGG8 STANDARD_EFI_SHELL" in text and "LOCAL-SIM NATIVE_UEFI_PRESENT" in text
    if args.qemu_dtb:
        checks["external_qemu_dtb_selected"] = "Machine model: LGG8-QEMU-EXTERNAL-DTB-PROBE" in text
    report = {"scope": "actual ARM64 Linux, getty and shell over dummy_hcd/cdc-acm virtual USB; not LG hardware",
              "network_services": False, "authentication": "none; local diagnostic root shell",
              "source_initramfs_sha256": sim.digest(original), "source_init_sha256": original_init_sha,
              "kernel_sha256": sim.digest(args.kernel.read_bytes()), "native_initramfs_sha256": sim.digest(clean),
              "native_init_sha256": sim.digest(checked["init"][1]), "busybox_sha256": sim.digest(original_busybox[1]),
              "transmitted_commands": sent,
              "usb_shell_processes": [first[1] if first else None, second[1] if second else None],
              "clean_archive_entries": sorted(checked), "checks": checks, "qemu": result,
              "launch": "standard UEFI Shell" if args.shell else "QEMU direct boot", "esp": esp_manifest,
              "passed": all(checks.values())}
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    print("PASS" if report["passed"] else "FAIL", "native USB diagnostic shell")
    for marker in result["markers"]:
        print(marker)
    print("Clean initramfs:", clean_path)
    print("Report:", report_path)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
