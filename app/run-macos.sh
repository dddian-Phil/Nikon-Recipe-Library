#!/bin/bash
set -eu
workspace_path="$(cd -- "$(dirname -- "$0")/.." && pwd)"
python_path="$workspace_path/.venv/bin/python"
if [ ! -x "$python_path" ]; then
    echo '请先双击 setup-mac.command 安装本项目的 Python 环境。' >&2
    exit 1
fi
exec "$python_path" -B "$workspace_path/app/launch.py" "$@"
