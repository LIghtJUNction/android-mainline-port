#!/usr/bin/env python3
"""Build a virtual USB host/controller pair against an existing Linux build."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel-source", required=True, type=Path)
    parser.add_argument("--kernel-build", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    source, build, out = (p.resolve() for p in (args.kernel_source, args.kernel_build, args.out))
    out.mkdir(parents=True, exist_ok=True)
    protected = [build / ".config", build / "arch/arm64/boot/Image"]
    before = {str(p): sha(p) for p in protected}
    sources = ["drivers/usb/gadget/udc/dummy_hcd.c", "drivers/usb/class/cdc-acm.c", "drivers/usb/class/cdc-acm.h"]
    for name in sources:
        shutil.copyfile(source / name, out / Path(name).name)
    (out / "Makefile").write_text("obj-m := dummy_hcd.o cdc-acm.o\n")
    common = ["make", "-C", str(source), f"O={build}", "ARCH=arm64", "LLVM=1", "-j2"]
    with (out / "build.log").open("wb") as log:
        subprocess.run(common + ["modules_prepare"], stdout=log, stderr=subprocess.STDOUT, check=True)
        subprocess.run(common + [f"M={out}", f"KBUILD_EXTRA_SYMBOLS={build / 'vmlinux.symvers'}", "modules"],
                       stdout=log, stderr=subprocess.STDOUT, check=True)
    after = {str(p): sha(p) for p in protected}
    if before != after:
        raise RuntimeError("existing kernel/config changed")
    manifest = {"kernel_and_config_unchanged": True, "kernel": before,
                "source": {name: sha(source / name) for name in sources},
                "modules": {name: sha(out / name) for name in ("dummy_hcd.ko", "cdc-acm.ko")}}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print("Virtual USB modules ready; kernel and .config unchanged:", out)


if __name__ == "__main__":
    main()
