#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
if ! command -v cargo >/dev/null 2>&1; then
    echo 'Install Rust stable first. See README.md.' >&2
    exit 1
fi
if [ ! -x .venv/bin/python ]; then
    python3 -m venv .venv
fi
if ! .venv/bin/python -c 'import quill_native._engine; import quill_native; assert quill_native.__version__ == "0.3.0"' >/dev/null 2>&1; then
    .venv/bin/python -m pip install .
fi
exec .venv/bin/python -m quill_native "$@"
