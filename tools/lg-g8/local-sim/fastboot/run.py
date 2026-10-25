#!/usr/bin/env python3
"""Compile the current Fastboot C bodies against host-only EFI boundary mocks."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
DEFAULT_SOURCE = HERE.parents[4] / "lg-g8-sources-20261025/mu_aloha_platforms"
LIB = Path("Platforms/QcomModulePkg/Library/FastbootLib")


def no_includes(text: str) -> str:
    return re.sub(r"^\s*#include[^\n]*", "", text, flags=re.M)


def function(text: str, name: str) -> tuple[str, int]:
    # Match a definition, never a prototype/call. Then count braces outside
    # comments and string literals so the copied body remains byte-for-byte.
    match = re.search(rf"(?m)^(?:STATIC\s+)?(?:EFI_STATUS|BOOLEAN|VOID|UINTN|ANDROID_FASTBOOT_STATE)\s*\*?\s*{name}\s*\([^;{{}}]*\)\s*{{", text)
    if match is None:
        raise ValueError(f"No unique definition found for {name}")
    start = match.start()
    opening = text.index("{", start)
    token = re.compile(r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[{}]', re.S)
    depth = 0
    for brace in token.finditer(text, opening):
        if brace.group() == "{":
            depth += 1
        elif brace.group() == "}":
            depth -= 1
            if depth == 0:
                return text[start:brace.end()], text.count("\n", 0, start) + 1
    raise ValueError(f"Unterminated definition: {name}")


def generate(source: Path, build: Path) -> dict:
    manifest = {"source_root": str(source), "whole_translation_units": [], "extracted_functions": [], "sources": {}}
    for relative in [LIB / "FastbootMain.c", LIB / "FastbootCmds.c", LIB / "UsbDescriptors.c",
                     LIB / "FastbootCmds.h", LIB / "FastbootMain.h",
                     Path("Platforms/QcomModulePkg/Include/Protocol/EFIUsbDevice.h"),
                     Path("Platforms/QcomModulePkg/Application/LinuxLoader/LinuxLoader.c")]:
        data = (source / relative).read_bytes()
        manifest["sources"][str(relative)] = hashlib.sha256(data).hexdigest()
    for name in ["FastbootMain.c", "UsbDescriptors.c"]:
        data = (source / LIB / name).read_text()
        generated = '#include "host_shim.h"\n' + f'#line 1 "{source / LIB / name}"\n' + no_includes(data)
        (build / name).write_text(generated)
        manifest["whole_translation_units"].append({"file": str(LIB / name), "change": "include directives only"})
    (build / "FastbootCmds.host.h").write_text(no_includes((source / LIB / "FastbootCmds.h").read_text()))
    (build / "FastbootMain.host.h").write_text(no_includes((source / LIB / "FastbootMain.h").read_text()))
    commands = (source / LIB / "FastbootCmds.c").read_text()
    # Storage/board/USB operations remain mocks. Keep all local init, memory-map,
    # command registration, variable publication, and teardown bodies intact.
    names = ["GetMaxAllocatableMemory", "FatalErrorNotify", "FastbootFatal", "FastbootUnInit", "FastbootPublishVar",
             "GetXfrSize", "FastbootRegister", "ReadAllowUnlockValue", "FastbootCommandSetup", "FastbootCmdsInit",
             "FastbootCmdsUnInit", "FastbootDloadBuffer", "FastbootCurrentState"]
    generated = '#include "host_shim.h"\n#include "cmds_globals.h"\n'
    for name in names:
        body, line = function(commands, name)
        generated += f'\n#line {line} "{source / LIB / "FastbootCmds.c"}"\n{body}\n'
        manifest["extracted_functions"].append({"name": name, "line": line, "sha256": hashlib.sha256(body.encode()).hexdigest()})
    (build / "FastbootCmds.init.c").write_text(generated)
    (build / "source-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


CASES = {
    "success": {"status": "success", "contains": ["Usb.AllocateTransferBuffer download", "BuildDefaultDescriptors serial", "Usb.StartEx", "Usb.Send endpoint=1 size=511", "Usb.Stop", "Usb.FreeTransferBuffer after-stop"], "order": ["BS.CreateEventEx", "BS.SignalEvent", "BS.LocateProtocol", "BS.SetWatchdogTimer", "BS.GetMemoryMap query", "Usb.AllocateTransferBuffer download", "BS.SetMem", "PartitionHasMultiSlot", "BuildDefaultDescriptors serial", "RT.GetVariable", "Usb.StartEx", "Usb.AllocateTransferBuffer rx", "Usb.AllocateTransferBuffer tx", "DisplayFastbootMenu", "Usb.HandleEvent", "Usb.Stop"]},
    "create-controller-error": {"status": "device-error", "absent": ["BS.LocateProtocol", "Usb.StartEx"]},
    "signal-error-ignored": {"status": "success", "contains": ["BS.SignalEvent injected-error", "Usb.StartEx"]},
    "locate-error": {"status": "not-found", "absent": ["BS.SetWatchdogTimer", "Usb.StartEx"]},
    "commands-event-error": {"status": "device-error", "absent": ["Usb.AllocateTransferBuffer", "Usb.StartEx"]},
    "memory-map-query-error": {"status": "out-of-resources", "absent": ["Usb.AllocateTransferBuffer", "Usb.StartEx"]},
    "memory-fragmented": {"status": "out-of-resources", "absent": ["Usb.AllocateTransferBuffer", "Usb.StartEx"]},
    "download-exhausted": {"status": "out-of-resources", "contains": ["Usb.AllocateTransferBuffer download"], "absent": ["Usb.StartEx"]},
    "download-retry": {"status": "success", "contains": ["download size=100663296 injected-error", "download size=75497472"]},
    "setup-serial-error-ignored": {"status": "success", "contains": ["BoardSerialNum setup injected-error", "Usb.StartEx"], "absent": ["PartitionGetInfo frp"]},
    "setup-frp-error-ignored": {"status": "success", "contains": ["PartitionGetInfo frp injected-error", "Usb.StartEx"]},
    "usb-superplus": {"status": "success", "contains": ["Usb.StartEx bcdUSB=0310"]},
    "start-error": {"status": "not-started", "contains": ["Usb.StartEx injected-error"], "absent": ["Usb.AllocateTransferBuffer rx"]},
    "rx-error": {"status": "out-of-resources", "contains": ["Usb.AllocateTransferBuffer rx injected-error"], "absent": ["DisplayFastbootMenu", "Usb.Stop"]},
    "tx-error": {"status": "out-of-resources", "contains": ["Usb.AllocateTransferBuffer tx injected-error"], "absent": ["DisplayFastbootMenu", "Usb.Stop"]},
    "event-error-ignored": {"status": "success", "contains": ["Usb.HandleEvent injected-error", "FastbootFatal bounded-exit"]},
    "rx-command": {"status": "success", "contains": ["DataReady bytes=8 buffer=rx"]},
    "rx-data": {"status": "success", "contains": ["DataReady bytes=8 buffer=download"]},
    "tx-complete": {"status": "success", "contains": ["Usb.Send endpoint=1 size=16777216"]},
    "rx-cancel": {"status": "success", "contains": ["Usb.HandleEvent transfer-cancel"]},
    "rx-transfer-error": {"status": "success", "contains": ["Usb.HandleEvent transfer-error", "Usb.Stop"]},
    "strict-stop-lifetime": {"status": "invalid-parameter", "contains": ["Usb.FreeTransferBuffer after-stop rejected"]},
    "hang-download": {"timeout": True, "last": "HANG Usb.AllocateTransferBuffer download", "absent": ["Usb.StartEx"]},
    "hang-setup": {"timeout": True, "last": "HANG BoardSerialNum setup", "absent": ["Usb.StartEx"]},
    "hang-startex": {"timeout": True, "last": "HANG Usb.StartEx"},
    "hang-event": {"timeout": True, "last": "HANG Usb.HandleEvent"},
    "descriptor-serial-error": {"sanitizer": True, "contains": ["BoardSerialNum descriptors injected-error"]},
    "descriptor-serial-error-superplus": {"sanitizer": True, "contains": ["BoardSerialNum descriptors injected-error"], "absent": ["Usb.StartEx"]},
    "descriptor-pool-error": {"sanitizer": True, "contains": ["AllocateZeroPool descriptor injected-error"]},
    "descriptor-retained-by-firmware": {"sanitizer": True, "contains": ["Usb.HandleEvent read-retained-descriptor-pointers"]},
    "protocol-legacy-no-startex": {"sanitizer": True, "contains": ["BS.LocateProtocol legacy-with-null-StartEx"]},
}


def verify(name: str, spec: dict, proc: subprocess.CompletedProcess, timed_out: bool) -> list[str]:
    out = proc.stdout
    errors = []
    if bool(spec.get("timeout")) != timed_out:
        errors.append(f"timeout={timed_out}, expected {spec.get('timeout', False)}")
    if spec.get("sanitizer"):
        if proc.returncode == 0 or not re.search(r"AddressSanitizer|runtime error:", out):
            errors.append("expected nonzero sanitizer diagnosis")
    elif not timed_out:
        if proc.returncode != 0:
            errors.append(f"unexpected exit {proc.returncode}")
        if f"RESULT {spec.get('status')}" not in out:
            errors.append(f"expected RESULT {spec.get('status')}")
        if re.search(r"AddressSanitizer|runtime error:", out):
            errors.append("unexpected sanitizer finding")
    for needle in spec.get("contains", []):
        if needle not in out:
            errors.append(f"missing {needle}")
    for needle in spec.get("absent", []):
        if needle in out:
            errors.append(f"unexpected {needle}")
    pos = -1
    for needle in spec.get("order", []):
        pos = out.find(needle, pos + 1)
        if pos < 0:
            errors.append(f"sequence missing {needle}")
            break
    if spec.get("last") and spec["last"] not in out.splitlines()[-1]:
        errors.append(f"expected final trace {spec['last']}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--case", choices=CASES, action="append")
    parser.add_argument("--timeout", type=float, default=1.5)
    parser.add_argument("--cc", default=os.environ.get("CC", "clang"))
    parser.add_argument("--out", type=Path, default=HERE / "out")
    args = parser.parse_args()
    build = args.out.resolve()
    build.mkdir(parents=True, exist_ok=True)
    (build / "results.json").unlink(missing_ok=True)
    manifest = generate(args.source_root.resolve(), build)
    includes = [HERE, build, args.source_root / "MU_BASECORE/MdePkg/Include",
                args.source_root / "MU_BASECORE/MdePkg/Include/X64", args.source_root / "Platforms/QcomModulePkg/Include"]
    cc = shutil.which(args.cc)
    if not cc:
        raise SystemExit(f"Compiler not found: {args.cc}")
    command = [cc, "-std=gnu11", "-O1", "-g", "-fno-inline", "-fshort-wchar", "-fno-omit-frame-pointer", "-ftrivial-auto-var-init=pattern",
               "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-Wall", "-Wextra", "-Wno-unused-parameter",
               "-Wno-unused-variable", "-Wno-missing-field-initializers", "-Wno-pragma-pack", "-DNO_MSABI_VA_FUNCS"]
    for directory in includes:
        command += ["-I", str(directory)]
    command += [str(build / name) for name in ["FastbootMain.c", "FastbootCmds.init.c", "UsbDescriptors.c"]]
    command += [str(HERE / "harness.c"), "-o", str(build / "fastboot-host")]
    compiled = subprocess.run(command, capture_output=True, text=True)
    (build / "compile.log").write_text(compiled.stdout + compiled.stderr)
    if compiled.returncode:
        print(compiled.stdout + compiled.stderr, file=sys.stderr)
        return compiled.returncode
    selected = args.case or list(CASES)
    results = []
    env = dict(os.environ, ASAN_OPTIONS="detect_leaks=0:detect_stack_use_after_return=1:halt_on_error=1", UBSAN_OPTIONS="halt_on_error=1:print_stacktrace=1")
    for name in selected:
        start = time.monotonic()
        timed_out = False
        try:
            proc = subprocess.run([str(build / "fastboot-host"), name], env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=args.timeout)
        except subprocess.TimeoutExpired as error:
            timed_out = True
            output = error.stdout or b""
            if isinstance(output, bytes):
                output = output.decode(errors="replace")
            proc = subprocess.CompletedProcess([], -9, output)
        (build / f"{name}.log").write_text(proc.stdout)
        errors = verify(name, CASES[name], proc, timed_out)
        result = {"case": name, "pass": not errors, "returncode": proc.returncode, "timeout": timed_out, "seconds": round(time.monotonic() - start, 3), "errors": errors}
        results.append(result)
        print(f"{'PASS' if not errors else 'FAIL'} {name}" + (": " + "; ".join(errors) if errors else ""), flush=True)
    report = {"compiler": cc, "command": command, "manifest": manifest, "results": results,
              "scope": "native x86_64 exact C control flow with synthetic EFI/USB/storage boundaries; no hardware or firmware ABI acceptance"}
    (build / "results.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"{sum(result['pass'] for result in results)}/{len(results)} expected outcomes; logs: {build}")
    return int(any(not result["pass"] for result in results))


if __name__ == "__main__":
    raise SystemExit(main())
