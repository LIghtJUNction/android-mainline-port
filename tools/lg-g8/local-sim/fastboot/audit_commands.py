#!/usr/bin/env python3
"""Offline audit of one preprocessed FastbootCmdDesc cmd_list initializer.

Input is the isolated initializer declaration, not a whole translation unit.
This checks the command surface only; it cannot establish handler safety.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path


# Exact pairs: a familiar name wired to an unexpected handler must fail too.
ALLOWED = {
    "boot": "CmdBoot",
    "getvar:": "CmdGetVar",
    "download:": "CmdDownload",
}


def audit(text):
    # Do not silently interpret conditional source as an effective build config.
    if re.search(r"^\s*#", text, re.M):
        raise ValueError("preprocess input first; directives are not accepted")
    text = re.sub(r"/\*.*?\*/|//[^\n]*", "", text, flags=re.S)
    table = re.fullmatch(
        r"\s*struct\s+FastbootCmdDesc\s+cmd_list\s*\[\s*\]\s*=\s*\{(.*)\}\s*;\s*",
        text, re.S,
    )
    if not table:
        raise ValueError("expected exactly one isolated cmd_list declaration")
    body = table[1]
    entry = re.compile(r'\s*\{\s*"([^"\\]*)"\s*,\s*([A-Za-z_][A-Za-z_0-9]*)\s*\}\s*,?')
    pairs = []
    while body.strip():
        match = entry.match(body)
        if not match:
            raise ValueError("unsupported command initializer syntax")
        pairs.append((match[1], match[2]))
        body = body[match.end():]
        if body.strip() and not match[0].rstrip().endswith(","):
            raise ValueError("missing entry separator")
    if not pairs or pairs[0] != ("", "NULL"):
        raise ValueError("missing index-zero sentinel used by registration loop")
    commands = pairs[1:]
    if not commands:
        raise ValueError("empty command surface")
    if len({name for name, _ in commands}) != len(commands):
        raise ValueError("duplicate command prefix")
    rejected = [name for name, handler in commands if ALLOWED.get(name) != handler]
    if rejected:
        raise ValueError("commands outside RAM diagnostic allowlist: " + repr(rejected))
    return [dict(name=name, handler=handler) for name, handler in commands]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("table", type=Path)
    args = parser.parse_args()
    raw = args.table.read_bytes()
    result = {"sha256": hashlib.sha256(raw).hexdigest(), "scope": "command-table-only"}
    try:
        result.update(status="pass", commands=audit(raw.decode("utf-8")))
    except (ValueError, UnicodeError) as error:
        result.update(status="reject", reason=str(error))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
