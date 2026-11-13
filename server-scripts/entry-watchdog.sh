#!/bin/bash
# Template: deploy per-entry. NEW uses local SSH forward; OLD uses WG+SSH-R fallback.
# See docs/TWO_HOP.md «Надёжный dual-entry».
set -uo pipefail
echo "Use host-specific /root/entry-watchdog.sh installed on each RU entry."
