#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["unicorn==2.1.4"]
# ///
"""Synthetic ARM64 execution; no hardware, watchdog, timing or cache acceptance."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import random
import subprocess
import tempfile
import unicorn
from unicorn import arm64_const as r

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('aligned_build', HERE / 'build.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def verify_artifact(directory):
    manifest = json.loads((directory / 'manifest.json').read_bytes())
    require(set(manifest['files']) == {'shim.S', 'payload.bin', 'shim.o', 'shim.elf', 'shim.Image'}, 'missing build identities')
    for name, digest in manifest['files'].items():
        require(hashlib.sha256((directory / name).read_bytes()).hexdigest() == digest, 'artifact hash mismatch: ' + name)
    require(manifest['builder_sha256'] == builder.sha((HERE / 'build.py').read_bytes()), 'builder mismatch')
    # Rebuild from archived source and payload: hashes alone are not source linkage.
    with tempfile.TemporaryDirectory() as temp:
        out = Path(temp) / 'rebuild'
        builder.build(directory / 'payload.bin', out, manifest['destination'], manifest['dtb'], directory / 'shim.S')
        for name in ('shim.o', 'shim.elf', 'shim.Image'):
            require((out / name).read_bytes() == (directory / name).read_bytes(), 'nonreproducible ' + name)
    require((directory / 'shim.Image').read_bytes()[4096:] == (directory / 'payload.bin').read_bytes(), 'payload mismatch')
    return manifest


def case(directory, load, el, alignment, reject=False, negative=False, initial_daif=0x3c0):
    manifest = json.loads((directory / 'manifest.json').read_bytes())
    dest, dtb = manifest['destination'], manifest['dtb']
    raw = (directory / 'shim.Image').read_bytes()
    payload = raw[4096:]
    n = len(payload)
    symbols = subprocess.check_output(['llvm-nm', '-n', str(directory / 'shim.elf')], text=True)
    blocked = next(int(line.split()[0], 16) for line in symbols.splitlines() if line.endswith(' blocked'))
    clean_line = next(int(line.split()[0], 16) for line in symbols.splitlines() if line.endswith(' clean_line'))
    cpu = unicorn.Uc(unicorn.UC_ARCH_ARM64, unicorn.UC_MODE_ARM)
    cpu.ctl_set_cpu_model(r.UC_CPU_ARM64_MAX)
    pages = set()
    for lo, hi in ((load - 4096, load + len(raw) + 4096), (dest - 4096, dest + n + 4096)):
        for page in range(lo & ~4095, (hi + 4095) & ~4095, 4096):
            if page not in pages:
                cpu.mem_map(page, 4096)
                cpu.mem_write(page, b'\xa5' * 4096)
                pages.add(page)
    cpu.mem_write(load, raw)
    before = {page: bytes(cpu.mem_read(page, 4096)) for page in pages}
    code = bytes(cpu.mem_read(load, 4096))
    guards = [(dest - 4096, bytes(cpu.mem_read(dest - 4096, 4096))), (dest + n, bytes(cpu.mem_read(dest + n, 4096)))]
    cpu.reg_write(r.UC_ARM64_REG_PSTATE, initial_daif | (el << 2) | 1)
    require(cpu.reg_read(r.UC_ARM64_REG_PSTATE) & 0x3c0 == initial_daif, 'initial DAIF mismatch')
    op1 = {1: 0, 2: 4}[el]
    cpu.cpr_write(3, op1, 1, 0, 0, alignment * 2)
    require(cpu.cpr_read(3, op1, 1, 0, 0) & 2 == alignment * 2, 'SCTLR.A initialization')
    cpu.cpr_write(3, 3, 0, 0, 1, 0x8444c004)
    cpu.reg_write(r.UC_ARM64_REG_SP, 0xdead0000)
    for i in range(4):
        cpu.reg_write(getattr(r, f'UC_ARM64_REG_X{i}'), 0xfeed0000 + i)
    hits, bad, writes, cleans = [], [], [], []
    def clean_trace(cpu, address, size, context):
        cleans.append(cpu.reg_read(r.UC_ARM64_REG_X4))
    cpu.hook_add(unicorn.UC_HOOK_CODE, clean_trace, begin=load + clean_line, end=load + clean_line)
    target = load + blocked if reject else dest
    def stop(cpu, address, size, context):
        hits.append(address)
        cpu.emu_stop()
    def access(cpu, kind, address, size, value, context):
        if address % size:
            bad.append((address, size))
            cpu.emu_stop()
        if kind == unicorn.UC_MEM_WRITE:
            require(dest <= address and address + size <= dest + n, 'write outside destination')
            writes.append(address)
    stop_hook = cpu.hook_add(unicorn.UC_HOOK_CODE, stop, begin=target, end=target)
    cpu.hook_add(unicorn.UC_HOOK_MEM_READ | unicorn.UC_HOOK_MEM_WRITE, access)
    cpu.emu_start(load, 0, count=max(10000, n * 3))
    if negative:
        require(bool(bad), 'negative control did not detect unaligned access')
        return dict(negative_detected=True, unaligned=bad[0])
    require(not bad, 'unaligned memory access')
    require(hits == [target], 'did not reach target/blocked')
    if reject:
        # Continue from the first blocked instruction and account for every
        # executed instruction in a bounded window, including stray branches.
        cpu.hook_del(stop_hook)
        blocked_trace = []
        def trace_blocked(cpu, address, size, context):
            blocked_trace.append(address)
        cpu.hook_add(unicorn.UC_HOOK_CODE, trace_blocked)
        cpu.emu_start(target, 0, count=128)
        require(blocked_trace == [target] * 128, 'blocked loop escaped or stopped early')
        require(cpu.reg_read(r.UC_ARM64_REG_PC) == target, 'blocked PC changed')
        require(not writes, 'rejected copy wrote memory')
        require(all(bytes(cpu.mem_read(page, 4096)) == data for page, data in before.items()), 'rejection changed memory')
    else:
        require(cleans == list(range(dest, dest + n, 64)), 'cache clean range misses a destination line')
        require(bytes(cpu.mem_read(dest, n)) == payload, 'copy mismatch')
        require(bytes(cpu.mem_read(load, 4096)) == code, 'shim overwritten')
        require(all(bytes(cpu.mem_read(address, len(data))) == data for address, data in guards), 'sentinel changed')
        require([cpu.reg_read(getattr(r, f'UC_ARM64_REG_X{i}')) for i in range(4)] == [dtb, 0, 0, 0], 'handoff registers')
        require(cpu.reg_read(r.UC_ARM64_REG_SP) == 0xdead0000, 'SP changed')
        require(cpu.reg_read(r.UC_ARM64_REG_PSTATE) & 0x3c0 == 0x3c0, 'DAIF not masked')
        require(cpu.cpr_read(3, op1, 1, 0, 0) & 2 == alignment * 2, 'SCTLR.A changed')
    return dict(load=hex(load), el=el, SCTLR_A=alignment, initial_daif=initial_daif,
                reject=reject, blocked_instructions=128 if reject else 0, stores=len(writes))


def suite(out):
    out.mkdir(parents=True, exist_ok=False)
    payload = out / 'input.bin'
    payload.write_bytes(random.Random(8150).randbytes(0x1000))
    rejected_builds = 0
    for address, data in [(0xaa000004, b'word'), (0xaa000000, b''), (0xaa000000, b'123'),
                          (2**64 - 0x200000, b'x' * 0x200000), (-0x200000, b'word')]:
        test_input = out / 'invalid-input.bin'
        test_input.write_bytes(data)
        try:
            builder.build(test_input, out / 'must-not-exist', address)
        except ValueError:
            rejected_builds += 1
        else:
            raise RuntimeError('unsafe build input accepted')
    for dtb in (0xabc00004, 0xabc00001, -8, 2**64):
        try:
            builder.build(payload, out / 'must-not-exist', dtb=dtb)
        except ValueError:
            require(not (out / 'must-not-exist').exists(), 'rejected DTB created output')
            rejected_builds += 1
        else:
            raise RuntimeError('invalid DTB address accepted')
    fixed = out / 'fixed'
    builder.build(payload, fixed)
    verify_artifact(fixed)
    dest, n = 0xaa000000, 0x1000
    loads = [(0x88000000 + offset, False) for offset in (0, 4, 8, 12)]
    loads += [(dest - 4096, False), (dest - 4096 - 4, False), (dest - 4096 + 4, True),
              (dest - 4096 - 0x800, False), (dest + n, False), (dest + n + 4, False),
              (dest + n - 4, True), (dest + 4, True), (dest, True)]
    results = [case(fixed, load, el, a, reject) for el in (1, 2) for a in (0, 1) for load, reject in loads]
    results += [case(fixed, 0x88000004, el, a, initial_daif=0)
                for el in (1, 2) for a in (0, 1)]
    # Larger payload permits backward overlap without overwriting executable shim.
    payload.write_bytes(random.Random(8151).randbytes(0x4000))
    overlap = out / 'overlap'
    builder.build(payload, overlap)
    results += [case(overlap, dest - 0x3000 + offset, el, a) for offset in (0, 4, 8, 12) for el in (1, 2) for a in (0, 1)]
    high = out / 'high'
    builder.build(payload, high, 0x1aa000000, 0x2abc00000)
    results += [case(high, 0x188000004, el, a) for el in (1, 2) for a in (0, 1)]
    text = (HERE / 'shim.S').read_text()
    constants_only = text.replace('    ldr w2, [x4, #-4]!\n    str w2, [x5, #-4]!\n    subs x6, x6, #4', '    ldp x2, x3, [x4, #-16]!\n    stp x2, x3, [x5, #-16]!\n    subs x6, x6, #16').replace('    ldr w2, [x4], #4\n    str w2, [x5], #4\n    subs x6, x6, #4', '    ldp x2, x3, [x4], #16\n    stp x2, x3, [x5], #16\n    subs x6, x6, #16')
    old = constants_only
    for reg, label in [('x5', 'destination'), ('x6', 'length'), ('x4', 'destination'), ('x0', 'device_tree')]:
        block = f'    adr x11, {label}\n    ldr w{reg[1:]}, [x11]\n    ldr w13, [x11, #4]\n    orr {reg}, {reg}, x13, lsl #32'
        old = old.replace(block, f'    ldr {reg}, {label}')
    negative = []
    for name, source in [('old', old), ('constants-only', constants_only)]:
        path = out / (name + '.S')
        path.write_text(source)
        directory = out / name
        builder.build(payload, directory, source=path)
        negative += [case(directory, 0x88000004, el, 1, negative=True) for el in (1, 2)]
    # Tampering with archived source must fail verification even under python -O.
    (fixed / 'shim.S').write_text(text + '\n/* tampered */\n')
    try:
        verify_artifact(fixed)
    except RuntimeError:
        pass
    else:
        raise RuntimeError('tampering was accepted')
    (fixed / 'shim.S').write_text(text)
    report = dict(cases=results, rejected_builds=rejected_builds, negative_controls=negative, unicorn=unicorn.__version__,
                  verifier_sha256=builder.sha(Path(__file__).read_bytes()),
                  scope=__doc__, full_payload_run=False, hardware_ready=False)
    (out / 'results.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(dict(passed=len(results), negatives=len(negative), hardware_ready=False)))

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    suite(parser.parse_args().out.resolve())
