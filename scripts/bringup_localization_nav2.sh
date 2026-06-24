#!/usr/bin/env bash
set -Eeo pipefail

# House Sitter v2 TurtleBot 4 bringup helper
# Starts Gazebo, localization, and Nav2.
# Does not publish /cmd_vel, does not send /undock, does not send /navigate_to_pose.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

MAP_PATH="${MAP_PATH:-/opt/ros/jazzy/share/turtlebot4_navigation/maps/warehouse.yaml}"
START_GAZEBO="${START_GAZEBO:-1}"
SET_INITIAL_POSE="${SET_INITIAL_POSE:-1}"

INITIAL_X="${INITIAL_X:-0.0}"
INITIAL_Y="${INITIAL_Y:-0.0}"
INITIAL_QZ="${INITIAL_QZ:-0.0}"
INITIAL_QW="${INITIAL_QW:-1.0}"

STAMP="$(date +%Y%m%d_%H%M%S)"
LOG_DIR="${PROJECT_ROOT}/logs/bringup_${STAMP}"
mkdir -p "${LOG_DIR}"

PIDS=()
NAMES=()

source_ros() {
  source /opt/ros/jazzy/setup.bash

  if [[ -f "${PROJECT_ROOT}/../install/setup.bash" ]]; then
    source "${PROJECT_ROOT}/../install/setup.bash"
  elif [[ -f "${PROJECT_ROOT}/install/setup.bash" ]]; then
    source "${PROJECT_ROOT}/install/setup.bash"
  fi
}

start_bg() {
  local name="$1"
  shift

  local log_file="${LOG_DIR}/${name}.log"

  echo "[START] ${name}"
  echo "        log: ${log_file}"

  stdbuf -oL -eL "$@" >"${log_file}" 2>&1 &

  local pid=$!
  PIDS+=("${pid}")
  NAMES+=("${name}")

  sleep 2

  if ! kill -0 "${pid}" 2>/dev/null; then
    echo "[ERROR] ${name} exited immediately."
    echo "        Check log: ${log_file}"
    tail -n 80 "${log_file}" || true
    exit 1
  fi
}

cleanup() {
  trap - EXIT INT TERM

  echo
  echo "[CLEANUP] Stopping launched processes..."

  for i in "${!PIDS[@]}"; do
    local pid="${PIDS[$i]}"
    local name="${NAMES[$i]}"

    if kill -0 "${pid}" 2>/dev/null; then
      echo "  sending SIGINT to ${name}, pid=${pid}"
      kill -INT "${pid}" 2>/dev/null || true
    fi
  done

  sleep 5

  for i in "${!PIDS[@]}"; do
    local pid="${PIDS[$i]}"
    local name="${NAMES[$i]}"

    if kill -0 "${pid}" 2>/dev/null; then
      echo "  ${name} still running, sending SIGTERM"
      kill -TERM "${pid}" 2>/dev/null || true
    fi
  done

  wait || true

  echo "[CLEANUP] Done."
  echo "[LOGS] ${LOG_DIR}"
}

trap cleanup EXIT INT TERM

wait_topic_once() {
  local topic="$1"
  local timeout_s="${2:-60}"
  local end=$((SECONDS + timeout_s))

  echo "[WAIT] ${topic}"

  while (( SECONDS < end )); do
    if timeout 5s ros2 topic echo "${topic}" --once >/dev/null 2>&1; then
      echo "[OK] ${topic} has data"
      return 0
    fi
    sleep 2
  done

  echo "[ERROR] Timed out waiting for ${topic}"
  return 1
}

wait_node() {
  local node="$1"
  local timeout_s="${2:-60}"
  local end=$((SECONDS + timeout_s))

  echo "[WAIT] node ${node}"

  while (( SECONDS < end )); do
    if ros2 node list 2>/dev/null | grep -qx "${node}"; then
      echo "[OK] node exists: ${node}"
      return 0
    fi
    sleep 2
  done

  echo "[WARN] Timed out waiting for node ${node}"
  return 1
}

wait_action() {
  local action="$1"
  local timeout_s="${2:-90}"
  local end=$((SECONDS + timeout_s))

  echo "[WAIT] action ${action}"

  while (( SECONDS < end )); do
    if ros2 action list 2>/dev/null | grep -qx "${action}"; then
      echo "[OK] action exists: ${action}"
      return 0
    fi
    sleep 2
  done

  echo "[ERROR] Timed out waiting for action ${action}"
  return 1
}

wait_tf() {
  local from_frame="$1"
  local to_frame="$2"
  local timeout_s="${3:-60}"
  local end=$((SECONDS + timeout_s))
  local tmp_file="${LOG_DIR}/tf_${from_frame}_to_${to_frame}.log"

  echo "[WAIT] TF ${from_frame} -> ${to_frame}"

  while (( SECONDS < end )); do
    timeout 5s ros2 run tf2_ros tf2_echo "${from_frame}" "${to_frame}" >"${tmp_file}" 2>&1 || true

    if grep -q "Translation:" "${tmp_file}"; then
      echo "[OK] TF exists: ${from_frame} -> ${to_frame}"
      return 0
    fi

    sleep 2
  done

  echo "[WARN] Timed out waiting for TF ${from_frame} -> ${to_frame}"
  return 1
}

publish_initial_pose() {
  echo "[ACTION] Publishing one /initialpose message for AMCL"

  ros2 topic pub --once /initialpose geometry_msgs/msg/PoseWithCovarianceStamped "
header:
  frame_id: map
pose:
  pose:
    position:
      x: ${INITIAL_X}
      y: ${INITIAL_Y}
      z: 0.0
    orientation:
      x: 0.0
      y: 0.0
      z: ${INITIAL_QZ}
      w: ${INITIAL_QW}
  covariance:
  - 0.25
  - 0.0
  - 0.0
  - 0.0
  - 0.0
  - 0.0
  - 0.0
  - 0.25
  - 0.0
  - 0.0
  - 0.0
  - 0.0
  - 0.0
  - 0.0
  - 0.0
  - 0.0
  - 0.0
  - 0.0
  - 0.0
  - 0.0
  - 0.0
  - 0.0
  - 0.0
  - 0.0
  - 0.0
  - 0.0
  - 0.0
  - 0.0
  - 0.0
  - 0.0
  - 0.0
  - 0.0
  - 0.0
  - 0.0
  - 0.0
  - 0.0685
" >"${LOG_DIR}/initialpose.log" 2>&1 || {
    echo "[WARN] Failed to publish /initialpose."
    echo "       Check ${LOG_DIR}/initialpose.log"
    return 1
  }

  echo "[OK] /initialpose published once"
}

main() {
  source_ros

  echo "============================================================"
  echo "House Sitter v2 TurtleBot 4 bringup"
  echo "============================================================"
  echo "Project root: ${PROJECT_ROOT}"
  echo "Map path:     ${MAP_PATH}"
  echo "Log dir:      ${LOG_DIR}"
  echo "Start Gazebo: ${START_GAZEBO}"
  echo "Initial pose: ${SET_INITIAL_POSE}"
  echo "============================================================"

  if [[ "${START_GAZEBO}" == "1" ]]; then
    start_bg gazebo ros2 launch turtlebot4_gz_bringup turtlebot4_gz.launch.py rviz:=false

    wait_topic_once /clock 90
    wait_topic_once /scan 90
    wait_topic_once /odom 90
  else
    echo "[SKIP] START_GAZEBO=0, assuming Gazebo is already running."

    wait_topic_once /clock 30
    wait_topic_once /scan 30
    wait_topic_once /odom 30
  fi

  start_bg localization ros2 launch turtlebot4_navigation localization.launch.py use_sim_time:=true map:="${MAP_PATH}"

  wait_node /amcl 60 || true

  if [[ "${SET_INITIAL_POSE}" == "1" ]]; then
    sleep 5
    publish_initial_pose || true
  else
    echo "[SKIP] SET_INITIAL_POSE=0"
  fi

  wait_topic_once /map 60 || true
  wait_topic_once /amcl_pose 60 || true
  wait_tf map odom 60 || true

  start_bg nav2 ros2 launch turtlebot4_navigation nav2.launch.py use_sim_time:=true

  wait_action /navigate_to_pose 90

  echo
  echo "============================================================"
  echo "[READY]"
  echo "Gazebo + localization + Nav2 have been started."
  echo "Keep this terminal open."
  echo
  echo "Run this in another terminal:"
  echo "  cd ~/create3_ws"
  echo "  ./house_sitter_v2/scripts/check_nav2_ready.sh"
  echo
  echo "This script does not send /cmd_vel, /undock, or navigation goals."
  echo "Press Ctrl+C here to stop everything cleanly."
  echo "Logs are in: ${LOG_DIR}"
  echo "============================================================"

  while true; do
    sleep 5

    for i in "${!PIDS[@]}"; do
      local pid="${PIDS[$i]}"
      local name="${NAMES[$i]}"

      if ! kill -0 "${pid}" 2>/dev/null; then
        echo "[ERROR] ${name} exited unexpectedly."
        echo "        Check log: ${LOG_DIR}/${name}.log"
        tail -n 80 "${LOG_DIR}/${name}.log" || true
        exit 1
      fi
    done
  done
}

main "$@"
