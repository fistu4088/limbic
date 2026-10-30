#!/usr/bin/env bash
set -euo pipefail

REPO="https://github.com/fistu4088/limbic.git"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

if ! command -v python3 >/dev/null 2>&1; then
  printf 'Python 3.10+ is required. Install Python, then run this command again.\n' >&2
  exit 1
fi
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
  printf 'Python 3.10+ is required. Upgrade Python, then run this command again.\n' >&2
  exit 1
fi
if ! command -v git >/dev/null 2>&1; then
  printf 'Git is required to download limbic. Install Git, then run this command again.\n' >&2
  exit 1
fi

printf 'Downloading limbic…\n'
git clone --depth 1 --quiet "$REPO" "$WORK/limbic"
cd "$WORK/limbic"
python3 -m limbic.installer --source "$WORK/limbic"
