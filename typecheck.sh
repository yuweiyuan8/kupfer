#!/bin/bash
git ls-files \*.py | sort -u | xargs mypy --pretty --show-error-codes --install-types --ignore-missing-imports "$@"
