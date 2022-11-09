#!/bin/bash
git ls-files \*.py | sort -u | xargs mypy --pretty --show-error-codes --check-untyped-defs --install-types --ignore-missing-imports "$@"
