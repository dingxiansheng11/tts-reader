#!/usr/bin/env bash
# 长文朗读器 · macOS / Linux 启动脚本
set -e
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "未找到 python3。请先安装 Python 3.10 或更高版本：https://www.python.org/downloads/"
  exit 1
fi

if ! python3 -c "import edge_tts" >/dev/null 2>&1; then
  echo "依赖未安装，正在安装 edge-tts …"
  python3 -m pip install -q -r requirements.txt || \
    python3 -m pip install -q -i https://pypi.tuna.tsinghua.edu.cn/simple edge-tts
fi

exec python3 app.py
