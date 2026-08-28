#!/usr/bin/env bash
# Double-click (or ./run_webapp_mac_linux.sh) launcher for the Monster Archiver
# Suite web app on macOS/Linux.
set -e
cd "$(dirname "$0")/webapp"

if ! command -v node >/dev/null 2>&1; then
    echo "Node.js was not found. Install the LTS version from https://nodejs.org"
    echo "(or via your package manager / nvm) and run this script again."
    read -p "Press Enter to exit..."
    exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 was not found. Install it (e.g. 'brew install python' on macOS,"
    echo "or your distro's package manager on Linux) and run this script again."
    read -p "Press Enter to exit..."
    exit 1
fi

if [ ! -d node_modules ]; then
    echo "Installing dependencies for the first time — this can take a minute..."
    npm install
fi

npm run dev
