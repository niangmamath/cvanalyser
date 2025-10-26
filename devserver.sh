#!/bin/sh
# Wrap the execution in a nix-shell to ensure all dependencies are available
nix-shell -p python3 tesseract poppler_utils --run "source .venv/bin/activate && python -u -m flask --app main run --debug -p \${PORT:-8080}"