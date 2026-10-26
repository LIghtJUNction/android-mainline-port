#!/usr/bin/env python3
"""Fingerprint explicit local files without building, executing inputs or contacting a device."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat


SCOPE = "Present-file snapshot only; not historical build provenance or device acceptance"


def fingerprint(path: Path) -> dict:
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"not a regular file: {path}")
        digest = hashlib.sha256()
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
        after = os.fstat(stream.fileno())
    identity = lambda info: (info.st_dev, info.st_ino, info.st_size,
                             info.st_mtime_ns, info.st_ctime_ns)
    if identity(before) != identity(after) or identity(after) != identity(path.stat()):
        raise ValueError(f"file changed during collection: {path}")
    return {"path": str(path.resolve()), "size": after.st_size,
            "sha256": digest.hexdigest()}


def collect(files: dict[str, Path], manifest_path: Path | None) -> dict:
    started = datetime.now(timezone.utc).isoformat()
    records = {label: fingerprint(path) for label, path in files.items()}
    comparison = None
    if manifest_path is not None:
        manifest_record = fingerprint(manifest_path)
        manifest = json.loads(manifest_path.read_bytes())
        if fingerprint(manifest_path) != manifest_record:
            raise ValueError("candidate manifest changed during collection")
        claims = {"kernel": manifest["kernel_sha256"],
                  "dtb": manifest["files"]["linux.dtb"]}
        for digest in claims.values():
            if (not isinstance(digest, str) or len(digest) != 64
                    or any(char not in "0123456789abcdef" for char in digest)):
                raise ValueError("invalid candidate SHA-256")
        comparison = {
            "manifest": manifest_record,
            "scope": "Byte identity against manifest claims only; claims are not authenticated",
            "matches": {label: records[label]["sha256"] == digest
                        for label, digest in claims.items()},
            "claimed_sha256": claims,
        }
    # Detect ordinary input changes across the collection interval as well.
    for label, path in files.items():
        if fingerprint(path) != records[label]:
            raise ValueError(f"file changed during collection: {path}")
    return {
        "schema_version": 1,
        "scope": SCOPE,
        "collection_started_utc": started,
        "collection_finished_utc": datetime.now(timezone.utc).isoformat(),
        "collector": fingerprint(Path(__file__)),
        "files": records,
        "candidate_comparison": comparison,
        "limitations": [
            "Selected files only; no complete source tree or include-dependency identity",
            "Co-location, timestamps and generated labels do not prove build inputs",
            "No upstream commit, reproducibility, format validity or boot success inferred",
            "Change checks do not provide an atomic filesystem snapshot",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--dtb", type=Path, required=True)
    parser.add_argument("--file", action="append", default=[], metavar="LABEL=PATH",
                        help="additional source or generated metadata file; never executed")
    parser.add_argument("--candidate-manifest", type=Path,
                        help="compare kernel and DTB to existing handoff manifest claims")
    parser.add_argument("--out", type=Path, required=True,
                        help="new private JSON report; refuses to overwrite")
    args = parser.parse_args()
    try:
        files = {"kernel": args.kernel, "config": args.config, "dtb": args.dtb}
        for item in args.file:
            label, separator, path = item.partition("=")
            if not separator or not label or not path or label in files:
                raise ValueError("--file requires a unique nonempty LABEL=PATH")
            files[label] = Path(path)
        if args.out.exists() or args.out.is_symlink():
            raise ValueError("output already exists")
        report = collect(files, args.candidate_manifest)
        # Exclusive creation protects old evidence; no source or manifest writes.
        descriptor = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2)
            stream.write("\n")
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f"error: {error}\n")
    comparison = report["candidate_comparison"]
    print(json.dumps({"scope": SCOPE, "file_count": len(files),
                      "candidate_matches": comparison["matches"] if comparison else None}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
