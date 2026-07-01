#!/usr/bin/env bash
# Обёртка: сначала lite-установка (безопасно рядом с Xray).
# Полный install_amneziawg.sh — опционально, если lite не подошёл.
set -euo pipefail
exec bash /root/setup-amnesiawg-lite.sh "$@"
