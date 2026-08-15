#!/bin/bash

source .venv/bin/activate

while getopts "w" opt; do
  case $opt in
    w) momo refresh --news ;;
  esac
done

uvicorn momo.api.main:app --reload --host 127.0.0.1 --port 8000
