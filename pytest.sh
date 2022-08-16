#!/bin/bash

sudo -v
python -m pytest -v ./*/test_*.py
