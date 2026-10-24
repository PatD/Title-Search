#!/bin/bash
set -u

cd "$(dirname "$0")" || exit 1
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

VENV_PY="$(pwd)/.venv/bin/python"

if [ ! -x "$VENV_PY" ]; then
    if ! command -v python3 >/dev/null 2>&1; then
        echo "Python 3.10 or newer was not found. Install Python, then open this file again."
        echo "Download Python from https://www.python.org/downloads/macos/"
        read -r -p "Press Return to close..."
        exit 1
    fi
    python3 -m venv .venv || {
        echo "Could not create the Python environment. Check that Python 3.10 or newer is installed."
        read -r -p "Press Return to close..."
        exit 1
    }
fi

if ! "$VENV_PY" -c "import rich, textual" >/dev/null 2>&1; then
    echo "Installing Title Search dependencies. This happens only when needed..."
    "$VENV_PY" -m pip install --require-hashes -r requirements.txt || {
        echo "Dependency installation failed. Check your internet connection and try again."
        read -r -p "Press Return to close..."
        exit 1
    }
fi

echo "Starting Title Search..."
"$VENV_PY" title_search_tui.py
APP_EXIT=$?
if [ "$APP_EXIT" -ne 0 ]; then
    echo "Title Search exited with code $APP_EXIT."
fi
echo
read -r -p "Press Return to close..."
exit "$APP_EXIT"
