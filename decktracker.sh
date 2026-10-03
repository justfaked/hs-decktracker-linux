#!/usr/bin/env bash
# Start the deck tracker and open it in the browser. Extra arguments are passed through.
cd "$(dirname "$(readlink -f "$0")")" || exit 1
exec python3 -m decktracker run --open "$@"
