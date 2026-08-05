#!/usr/bin/env bash
# Static house_v1 layout preview only: no robot, ROS bridge, or runtime control stack.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
WORLD_FILE="${PROJECT_ROOT}/worlds/house_v1.sdf"

if ! command -v gz >/dev/null 2>&1; then
  echo "Gazebo GUI 不可用：未找到 gz 命令。" >&2
  exit 2
fi
if [[ ! -f "${WORLD_FILE}" ]]; then
  echo "三维住宅文件不存在：${WORLD_FILE}" >&2
  exit 2
fi

echo "Static 3D residential preview — no robot execution"
echo "仅加载 house_v1 的墙体、房间和家具；关闭窗口后返回调用方。"
exec gz sim "${WORLD_FILE}"
