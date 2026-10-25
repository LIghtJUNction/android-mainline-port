#!/usr/bin/env bash
set -euo pipefail

task_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
export UV_CACHE_DIR="${UV_CACHE_DIR:-${TMPDIR:-/tmp}/lg-g8-bootshim-uv-cache}"
exec uv run --no-project --script "$task_dir/verify.py" "$@"
