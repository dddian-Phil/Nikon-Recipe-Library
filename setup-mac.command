#!/bin/bash
set -eu
workspace_path="$(cd -- "$(dirname -- "$0")" && pwd)"
cd "$workspace_path"
uv_path="$(command -v uv || true)"
if [ -z "$uv_path" ] && [ -x "$HOME/.local/bin/uv" ]; then
    uv_path="$HOME/.local/bin/uv"
fi
if [ -z "$uv_path" ]; then
    echo '未找到 uv。请先安装 uv，再运行 setup-mac.command。' >&2
    exit 1
fi
export UV_CACHE_DIR="$workspace_path/local/uv-cache"
export UV_PYTHON_INSTALL_DIR="$workspace_path/local/python"
"$uv_path" python install --no-bin 3.12
if [ ! -x "$workspace_path/.venv/bin/python" ]; then
    "$uv_path" venv --python 3.12 "$workspace_path/.venv"
fi
"$uv_path" pip install --python "$workspace_path/.venv/bin/python" -r "$workspace_path/requirements.txt"
"$workspace_path/.venv/bin/python" -B app/launch.py configure
