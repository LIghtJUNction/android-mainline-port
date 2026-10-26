#!/usr/bin/env python3
"""Build an offline shim artifact only, never a phone boot image."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

HERE = Path(__file__).resolve().parent

def sha(data):
    return hashlib.sha256(data).hexdigest()

def build(payload, out, destination=0xaa000000, dtb=0xabc00000, source=None):
    source = source or HERE / 'shim.S'
    data = payload.read_bytes()
    # Keep the audited handoff destination contract: cache maintenance starts
    # at a 2 MiB boundary. Arbitrary word-aligned destinations are not accepted.
    if not data or len(data) % 4 or not 0 <= destination < 2**64 - len(data) or destination % 0x200000:
        raise ValueError('nonempty word-sized payload and nonwrapping 2 MiB aligned destination required')
    if not 0 <= dtb < 2**64 or dtb % 8:
        raise ValueError('8-byte aligned 64-bit DTB address required')
    out.mkdir(parents=True, exist_ok=False)
    # Snapshot both inputs: later input edits cannot change this build.
    (out / 'payload.bin').write_bytes(data)
    (out / 'shim.S').write_bytes(source.read_bytes())
    commands = [
        ['clang', '--target=aarch64-none-elf', '-c', str(out / 'shim.S'),
         f'-DLOAD_ADDRESS={destination}', f'-DDTB_ADDRESS={dtb}', f'-DPAYLOAD_SIZE={len(data)}',
         f'-DPAYLOAD_PATH="{out.resolve() / "payload.bin"}"', '-o', str(out / 'shim.o')],
        ['ld.lld', '--image-base=0', '-Ttext=0', '--entry=_start', str(out / 'shim.o'), '-o', str(out / 'shim.elf')],
        ['llvm-objcopy', '-O', 'binary', '--only-section=.text', str(out / 'shim.elf'), str(out / 'shim.Image')]]
    for command in commands:
        subprocess.run(command, check=True, capture_output=True)
    raw = (out / 'shim.Image').read_bytes()
    if raw[4096:] != data:
        raise ValueError('linked payload differs')
    names = ('shim.S', 'payload.bin', 'shim.o', 'shim.elf', 'shim.Image')
    manifest = dict(destination=destination, dtb=dtb, payload_size=len(data),
                    files={name: sha((out / name).read_bytes()) for name in names},
                    builder_sha256=sha(Path(__file__).read_bytes()),
                    scope='offline copy shim only; hardware watchdog, timing and cache behavior unknown')
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--payload', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--destination', type=lambda v: int(v, 0), default=0xaa000000)
    parser.add_argument('--dtb', type=lambda v: int(v, 0), default=0xabc00000)
    args = parser.parse_args()
    build(args.payload, args.out, args.destination, args.dtb)
