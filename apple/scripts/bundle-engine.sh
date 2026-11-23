#!/bin/sh
# Assembles the engine the Mac app carries: a Python runtime, everything hearth depends on at
# the versions uv.lock names, and hearth itself.
#
#   scripts/bundle-engine.sh <destination> [signing identity]
#
# The runtime and the dependencies are built once and kept in a cache, keyed by what they are
# made from, so a build that changes only hearth's own source copies a few files. With a
# signing identity, every binary is signed in the cache, once, since the app's own signature
# does not cover code it finds in its resources. "-" signs for this Mac only.
set -eu

DEST=${1:?usage: bundle-engine.sh <destination> [signing identity]}
IDENTITY=${2:--}
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
CACHE=${HEARTH_ENGINE_CACHE:-$HOME/Library/Caches/dev.hearth.engine}
PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

# A relocatable CPython from python-build-standalone, pinned to one build and its checksum.
PY_VERSION=3.14.10
PY_BUILD=20261110
PY_SHA256=f15ec3d15460dd66c2eb88639b152cd9040e7d1ece11e38bf89e8f53f0171e02
PY_NAME="cpython-$PY_VERSION+$PY_BUILD-aarch64-apple-darwin-install_only_stripped.tar.gz"
PY_URL="https://github.com/astral-sh/python-build-standalone/releases/download/$PY_BUILD/$(printf %s "$PY_NAME" | sed 's/+/%2B/')"

command -v uv >/dev/null || { echo "bundle-engine: uv is not installed (https://docs.astral.sh/uv/)" >&2; exit 1; }
mkdir -p "$CACHE"

TARBALL="$CACHE/$PY_NAME"
if [ ! -f "$TARBALL" ]; then
  curl --fail --location --silent --show-error --output "$TARBALL.part" "$PY_URL"
  mv "$TARBALL.part" "$TARBALL"
fi
echo "$PY_SHA256  $TARBALL" | shasum -a 256 --check --status || {
  echo "bundle-engine: $TARBALL is not the Python this script names; delete it and run again" >&2
  exit 1
}

# What the staged engine is made from: the runtime, the lock file, this script, the identity.
KEY=$( { echo "$PY_SHA256 $IDENTITY"; cat "$REPO/uv.lock" "$0"; } | shasum -a 256 | cut -c1-16)
STAGE="$CACHE/stage-$KEY"

if [ ! -f "$STAGE/.complete" ]; then
  rm -rf "$STAGE"
  mkdir -p "$STAGE"
  tar -xzf "$TARBALL" -C "$STAGE"          # unpacks to $STAGE/python
  PYTHON="$STAGE/python/bin/python3.14"

  ( cd "$REPO" && uv export --frozen --no-dev --no-emit-project --format requirements-txt ) > "$STAGE/requirements.txt"
  uv pip install --quiet --python "$PYTHON" --target "$STAGE/site-packages" \
    --require-hashes --no-deps --requirement "$STAGE/requirements.txt"
  rm "$STAGE/requirements.txt"

  # Nothing the engine never runs: tests, GUIs, headers, and the installer that got it here.
  LIB="$STAGE/python/lib/python3.14"
  rm -rf "$LIB/test" "$LIB/idlelib" "$LIB/tkinter" "$LIB/turtledemo" "$LIB/ensurepip" \
    "$LIB/site-packages" "$LIB"/config-3.14-* "$STAGE/python/include" "$STAGE/python/share" \
    "$STAGE/python/lib/pkgconfig" "$STAGE/python"/lib/tcl* "$STAGE/python"/lib/tk* \
    "$STAGE/python"/lib/itcl* "$STAGE/python"/lib/thread* "$STAGE/site-packages/bin"
  find "$STAGE/python/bin" -mindepth 1 ! -name python3.14 -delete
  find "$STAGE" -name __pycache__ -type d -prune -exec rm -rf {} +

  # Compiled ahead and never checked against its source again: the app's bundle is sealed by
  # its signature, so Python must find its bytecode there and write none (it runs with -B).
  "$PYTHON" -I -m compileall -q --invalidation-mode unchecked-hash "$STAGE/python/lib" "$STAGE/site-packages" >/dev/null

  # The hardened runtime only lets a process load code signed by its own team, and a
  # signature made for this Mac only ("-") names no team, so it goes without.
  if [ "$IDENTITY" = "-" ]; then HARDENED=""; else HARDENED="--options runtime"; fi
  find "$STAGE" -type f \( -name '*.so' -o -name '*.dylib' -o -path '*/bin/python3.14' \) -print0 |
    xargs -0 codesign --force $HARDENED --timestamp=none --sign "$IDENTITY" 2>&1 |
    { grep -v 'replacing existing signature' || true; }
  touch "$STAGE/.complete"
fi

mkdir -p "$DEST"
rsync -a --delete --exclude .complete --exclude /app --exclude /main.py "$STAGE/" "$DEST/"

# Hearth itself, from this checkout, compiled the same way.
mkdir -p "$DEST/app"
rsync -a --delete --exclude __pycache__ "$REPO/src/hearth" "$DEST/app/"
cp "$HERE/../engine/main.py" "$DEST/main.py"
"$DEST/python/bin/python3.14" -I -m compileall -q --invalidation-mode unchecked-hash "$DEST/app" >/dev/null
