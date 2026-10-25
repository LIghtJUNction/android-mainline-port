#!/usr/bin/env python3
"""Inspect the actual compressed firmware for BDS/config file presence."""
from __future__ import annotations

import argparse
import hashlib
import json
import lzma
from pathlib import Path
import struct
import uuid

HERE = Path(__file__).resolve().parent
DEFAULT_SOURCE = HERE.parents[4] / "lg-g8-sources-20261025/mu_aloha_platforms"
LZMA_GUID = uuid.UUID("ee4e5898-3914-4259-9d6e-dc7bd79403cf").bytes_le
FILE_GUIDS = {
    "QcomBds": "5a50aa81-c3ae-4608-a0e3-41a2e69baf94",
    "uefiplat.cfg": "dde58710-41cd-4306-dbfb-3fa90bb1d2dd",
    "BdsDxe": "6d33944a-ec75-4855-a54d-809c75241f6c",
    "LinuxLoader": "f536d559-459f-48fa-8bbc-43b554ecae8d",
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def firmware_volumes(fd: bytes):
    cursor = 0
    while (index := fd.find(LZMA_GUID, cursor)) >= 0:
        cursor = index + len(LZMA_GUID)
        start = index - 4
        if start < 0 or index + 20 > len(fd):
            continue
        size = int.from_bytes(fd[start:start + 3], "little")
        data_offset = struct.unpack_from("<H", fd, index + 16)[0]
        if fd[start + 3] != 2 or not 24 <= data_offset < size or start + size > len(fd):
            continue
        decoder = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE)
        try:
            decoded = decoder.decompress(fd[start + data_offset:start + size], max_length=64 * 1024 * 1024)
        except lzma.LZMAError:
            continue
        if not decoder.eof:
            raise ValueError("LZMA section is incomplete or exceeds the local 64 MiB limit")
        offset = 0
        while offset + 4 <= len(decoded):
            section_size = int.from_bytes(decoded[offset:offset + 3], "little")
            header = 4
            if section_size == 0xffffff:
                if offset + 8 > len(decoded):
                    raise ValueError("truncated extended section")
                section_size = struct.unpack_from("<I", decoded, offset + 4)[0]
                header = 8
            if section_size < header or offset + section_size > len(decoded):
                raise ValueError("invalid decompressed section bounds")
            if decoded[offset + 3] == 0x17:
                fv = decoded[offset + header:offset + section_size]
                if fv[40:44] != b"_FVH":
                    raise ValueError("firmware-volume section has no FV signature")
                yield fv, start
            offset = (offset + section_size + 3) & ~3


def ffs_files(fv: bytes) -> list[dict]:
    if len(fv) < 56 or struct.unpack_from("<Q", fv, 32)[0] != len(fv):
        raise ValueError("invalid FV length")
    cursor = (struct.unpack_from("<H", fv, 48)[0] + 7) & ~7
    files = []
    while cursor + 24 <= len(fv):
        header = fv[cursor:cursor + 24]
        if header == b"\xff" * 24:
            break
        size = int.from_bytes(header[20:23], "little")
        minimum = 24
        if size == 0xffffff:
            if cursor + 32 > len(fv):
                raise ValueError("truncated extended FFS header")
            size = struct.unpack_from("<Q", fv, cursor + 24)[0]
            minimum = 32
        if size < minimum or cursor + size > len(fv):
            raise ValueError("invalid FFS bounds")
        files.append({"guid": str(uuid.UUID(bytes_le=header[:16])), "offset": cursor,
                      "size": size, "type": header[18], "raw_state": header[23]})
        cursor = (cursor + size + 7) & ~7
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--firmware", type=Path)
    parser.add_argument("--reference-firmware", type=Path)
    parser.add_argument("--out", type=Path, default=HERE / "out/bds-audit.json")
    args = parser.parse_args()
    source = args.source_root.resolve()
    build = source / "Build/SurfaceDuo1Pkg/RELEASE_CLANGPDB/FV"
    firmware = (args.firmware or build / "SM8150_EFI.fd").resolve()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.unlink(missing_ok=True)
    fd = firmware.read_bytes()
    volumes = list(firmware_volumes(fd))
    if len(volumes) != 1:
        raise ValueError(f"expected one compressed firmware volume, got {len(volumes)}")
    fv, section = volumes[0]
    files = ffs_files(fv)
    presence = {name: [item for item in files if item["guid"] == guid] for name, guid in FILE_GUIDS.items()}
    # These checks describe the current v7 image, not a newly built ESP profile.
    assert presence["BdsDxe"] and presence["LinuxLoader"]
    assert not presence["QcomBds"] and not presence["uefiplat.cfg"]
    config = source / "Platforms/SurfaceDuo1Pkg/Device/lg-alpha/Binaries/RawFiles/uefiplat.cfg"
    report = {"scope": "packaged FFS presence in the current compressed FD; no vendor code or hardware execution",
              "firmware": str(firmware), "firmware_sha256": sha(fd), "compressed_section_offset": section,
              "decompressed_fv_sha256": sha(fv), "ffs_count": len(files), "presence": presence,
              "DefaultBDSBootApp_literal_present_in_FV": any(value in fv for value in
                  (b"DefaultBDSBootApp", "DefaultBDSBootApp".encode("utf-16le"))),
              "source_config_sha256": sha(config.read_bytes()),
              "source_config_default_lines": [line for line in config.read_text().splitlines() if "DefaultBDSBootApp" in line],
              "runtime_variables_inspected": False,
              "unresolved": "External XBL/firmware state and vendor config consumers were not executed; LG boot reachability is unproven."}
    if (build / "FVMAIN.Fv").is_file():
        report["decompressed_fv_matches_build_FVMAIN"] = fv == (build / "FVMAIN.Fv").read_bytes()
    if args.reference_firmware:
        report["reference_sha256"] = sha(args.reference_firmware.read_bytes())
        report["reference_exact"] = report["reference_sha256"] == report["firmware_sha256"]
        assert report["reference_exact"]
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print("PASS: decoded actual FD; standard BdsDxe/LinuxLoader present; QcomBds/config FFS absent")
    print("Report:", args.out.resolve())
    print("Boundary: packaged-file inspection only, not LG firmware execution")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
