#!/bin/bash

sudo -v
python -m pytest --junit-xml=pytest-report.xml -v "$@" ./*/test_*.py
