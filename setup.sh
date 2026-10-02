#!/usr/bin/env bash
# Set up the Match Look engine on macOS: a local virtualenv with its dependencies.
set -euo pipefail
cd "$(dirname "$0")"

python3 -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt
mkdir -p "$HOME/.matchlook/bridge"

cat <<MSG

Engine installed in .venv

Next, in Lightroom Classic:
  1. File > Plug-in Manager > Add > choose: $(pwd)/MatchLook.lrplugin
  2. Library > Plug-in Extras > Match Look Bridge Status  (should say "running")

Then check the connection from here:
  .venv/bin/python -m engine.bridge ping

And in Claude Code (started in this folder): select your photos, click the graded one, run /match-look

First time? docs/MAC_TESTING.md walks through a full test, including calibration.
MSG
