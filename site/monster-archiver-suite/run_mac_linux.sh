#!/usr/bin/env bash
# Double-click (or ./run_mac_linux.sh) launcher for Monster Archiver on macOS/Linux.
set -e
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 was not found. Install it (e.g. 'brew install python' on macOS,"
    echo "or your distro's package manager on Linux) and run this script again."
    read -p "Press Enter to exit..."
    exit 1
fi

python3 rezakir.py
read -p "Press Enter to exit..."
