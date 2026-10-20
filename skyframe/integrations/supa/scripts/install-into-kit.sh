#!/bin/sh
# Install the SkyFrame drop-in into a COPY of supa-fem-kit.
# Usage: scripts/install-into-kit.sh /path/to/supa-fem-kit-copy
set -e
kit="$1"
here="$(cd "$(dirname "$0")/.." && pwd)"
[ -n "$kit" ] || { echo "usage: $0 /path/to/supa-fem-kit-copy"; exit 2; }
[ -f "$kit/src/types.ts" ] || { echo "$kit does not look like supa-fem-kit (no src/types.ts)"; exit 2; }
[ -f "$kit/src/builtin.ts" ] && { echo "already installed ($kit/src/builtin.ts exists)"; exit 2; }
mv "$kit/src/solve.ts" "$kit/src/builtin.ts"
cp "$here/src/solve.ts" "$kit/src/solve.ts"
cp "$here/test/xhr-sync.mjs" "$kit/xhr-sync.mjs"
echo "Installed: src/solve.ts -> src/builtin.ts (unchanged), new src/solve.ts, xhr-sync.mjs"
echo "Next, in $kit: npm install && npm run typecheck"
