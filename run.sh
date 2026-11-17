#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Development launcher: no installation required.
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m ghelper "$@"
