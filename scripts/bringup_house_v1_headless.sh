#!/usr/bin/env bash
set -Eeuo pipefail

# Local house_v1 smoke bringup: Gazebo server, /clock, TurtleBot4 spawn and
# bridges only. It intentionally does not start localization, SLAM, Nav2, RViz,
# publish cmd_vel, or send a navigation goal.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
WORLD_FILE="${PROJECT_ROOT}/worlds/house_v1.sdf"
LOG_DIR="$(mktemp -d /tmp/house_v1_headless.XXXXXX)"
PIDS=()

cleanup() {
  for pid in "${PIDS[@]:-}"; do
    kill -INT -- "-${pid}" 2>/dev/null || true
  done
  wait || true
}
trap cleanup EXIT INT TERM

set +u
source /opt/ros/jazzy/setup.bash
if [[ -f "${PROJECT_ROOT}/install/setup.bash" ]]; then
  source "${PROJECT_ROOT}/install/setup.bash"
elif [[ -f "${PROJECT_ROOT}/../install/setup.bash" ]]; then
  source "${PROJECT_ROOT}/../install/setup.bash"
fi
set -u
[[ -f "${WORLD_FILE}" ]] || { echo "house_v1 world is missing: ${WORLD_FILE}" >&2; exit 2; }

start() {
  local name="$1"; shift
  setsid "$@" >"${LOG_DIR}/${name}.log" 2>&1 &
  PIDS+=("$!")
}

start gazebo ros2 launch ros_gz_sim gz_sim.launch.py "gz_args:=-r -s ${WORLD_FILE}"
for _ in $(seq 1 60); do gz service -l 2>/dev/null | grep -qx '/world/house_v1/control' && break; sleep 0.5; done
gz service -l 2>/dev/null | grep -qx '/world/house_v1/control' || { echo "house_v1 Gazebo did not become ready; logs: ${LOG_DIR}" >&2; exit 2; }
start clock_bridge ros2 run ros_gz_bridge parameter_bridge '/clock@rosgraph_msgs/msg/Clock[gz.msgs.Clock'
for _ in $(seq 1 10); do timeout 2s ros2 topic echo /clock rosgraph_msgs/msg/Clock --once >/dev/null 2>&1 && break; done
timeout 2s ros2 topic echo /clock rosgraph_msgs/msg/Clock --once >/dev/null 2>&1 || { echo "/clock bridge did not become ready; logs: ${LOG_DIR}" >&2; exit 2; }
start turtlebot4 ros2 launch turtlebot4_gz_bringup turtlebot4_spawn.launch.py world:=house_v1 rviz:=false localization:=false slam:=false nav2:=false x:=5.0 y:=5.0 z:=0.05 yaw:=0.0
echo "house_v1 headless running; logs: ${LOG_DIR}"
wait -n "${PIDS[@]}"
