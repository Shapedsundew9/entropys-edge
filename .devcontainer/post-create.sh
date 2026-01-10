#!/bin/bash

# This script runs after the container is created.
# The 'set -e' command ensures that the script will exit immediately if a command fails.
set -e

echo "--- Running post-create script ---"

# Activating the virtual environment
echo "Creating virtual environment..."
python3 -m venv ../.venv
../.venv/bin/pip install --upgrade pip
../.venv/bin/pip install -r requirements.txt

