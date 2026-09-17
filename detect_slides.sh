#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
export PYTHONPATH="${script_dir}${PYTHONPATH:+:${PYTHONPATH}}"
exec "${PYTHON:-python3}" -c \
    'from slides_docx.cli import legacy_detect_main; raise SystemExit(legacy_detect_main())' \
    "$@"
