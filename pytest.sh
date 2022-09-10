#!/bin/bash

sudo -v
python -m pytest -v --cov=. --cov-branch --cov-report=term "$@" ./*/test_*.py
