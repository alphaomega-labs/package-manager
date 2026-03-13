#!/usr/bin/env bash
set -euo pipefail

repo="${1:-pypi}"

python -m pip install --upgrade build twine
rm -rf build dist src/*.egg-info
python -m build
python -m twine check dist/*
python -m twine upload --repository "$repo" dist/*
