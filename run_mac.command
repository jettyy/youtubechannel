#!/bin/bash
cd "$(dirname "$0")"

# 파이썬 3.10 이상 찾기 (맥 기본 python3 는 버전이 낮을 수 있음)
PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  echo "[오류] 파이썬 3.10 이상이 필요합니다."
  echo "       https://www.python.org/downloads/ 에서 macOS 용 파이썬을 설치한 뒤 다시 실행하세요."
  read -r -p "엔터를 누르면 창을 닫습니다..."
  exit 1
fi

if [ ! -d .venv ]; then
  echo "처음 실행: 필요한 프로그램을 설치합니다... (몇 분 걸릴 수 있음)"
  "$PY" -m venv .venv || { read -r -p "설치 실패. 엔터를 누르면 닫습니다..."; exit 1; }
fi
source .venv/bin/activate
python -m pip install -q --upgrade pip
python -m pip install -q -r requirements.txt
python -m pip install -q --upgrade yt-dlp
python app.py
