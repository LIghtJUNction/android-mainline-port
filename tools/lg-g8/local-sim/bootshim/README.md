# Local BootShim CPU handoff check

Run from the repository root:

```sh
tools/lg-g8/local-sim/bootshim/run.sh
```

Requirements: `uv`, `clang`, `llvm-objcopy`, and `llvm-objdump`. The script pins
Unicorn 2.1.4 in an isolated uv script environment under
`${UV_CACHE_DIR:-/tmp/lg-g8-bootshim-uv-cache}`. It does not change the system
Python environment. Generated files go into this directory's ignored `out/`.

The default inputs are the local `lg-alpha-usbconfig-stock-v7.img` and older
`lg-alpha-bootcmd.img` under
`~/Documents/Codex/2026-10-25-lg-g8-omarchy/audit/`. Override them as needed:

```sh
./run.sh --image /absolute/path/to/boot.img \
  --source /absolute/path/to/BootShim.S \
  --output-dir /tmp/lg-g8-bootshim-result
```

The check parses Android v0 packaging and the gzip stream, then assembles the
local `BootShim.S` with `UEFI_BASE=0x9fc00000` and `UEFI_SIZE=0x300000`. All 112
embedded shim bytes must equal the assembled reference. The bytes executed by
Unicorn are extracted from each image, rather than taken from the build.

For each appended FDT (device tree), two paths run: a relocated source that
copies the 3 MiB FD to `0x9fc00000`, and an already placed payload that skips
copying. Execution stops at the branch to the FD, before any UEFI instruction.
The checks compare every destination byte, the complete source and FDT, guard
pages, and the expected values of `x0` through `x30`, `SP`, `PC`, and `NZCV`.
In particular `x0` still points to the unchanged FDT. The skip path must make
zero writes to the destination FD.

Six malformed-input cases per image cover a one-byte truncated FD, wrong ARM64
magic, wrong copy size, changed shim instruction, truncated gzip trailer, and
truncated final FDT. These are **host preflight rejections**. BootShim itself
has no runtime payload-length check; the tests do not claim otherwise.

`out/report.json` records input/source/shim/FD hashes and every result.
`out/reference/BootShim.disassembly.txt` shows the assembled instructions.
The simulated source/FDT addresses are controlled test inputs, not observations
of the LG bootloader's memory placement or FDT selection. FDT validation here
checks header bounds; it does not establish which device tree the phone uses.

This emulates the CPU copy-and-branch contract only. It does not emulate LG
hardware, Qualcomm peripherals, ABL, gzip loading, or UEFI, and cannot establish
that Linux boots on the LG G8. It never accesses a phone or invokes device tools.
