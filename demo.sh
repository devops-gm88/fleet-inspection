#!/usr/bin/env bash
# One command from a clean checkout: generate the seeded photos, compare every
# asset in the period, and write one-page PDFs plus the evidence JSON into out/.
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "error: python3 is required (3.10 or newer)" >&2
  exit 1
fi

python3 - <<'PY'
import sys
if sys.version_info < (3, 10):
    sys.exit("error: Python 3.10 or newer is required (found %s)" % sys.version.split()[0])
PY

exec python3 -m fleet demo "$@"
