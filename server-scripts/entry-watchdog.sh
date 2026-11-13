#!/bin/bash
# Template. Live copies:
#   server-scripts/entry-watchdog-new.sh  -> RuVDS 195.133.146.140:/root/entry-watchdog.sh
#   server-scripts/entry-watchdog-old.sh  -> RuVDS-old 194.226.54.124:/root/entry-watchdog.sh
# See docs/TWO_HOP.md «Надёжный dual-entry».
set -uo pipefail
echo "Use host-specific /root/entry-watchdog.sh installed on each RU entry."
