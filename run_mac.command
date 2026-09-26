#!/bin/bash
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  echo "처음 실행: 필요한 프로그램을 설치합니다..."
  python3 -m venv .venv
fi
source .venv/bin/activate
python -m pip install -q --upgrade pip
python -m pip install -q -r requirements.txt
python -m pip install -q --upgrade yt-dlp
python app.py
