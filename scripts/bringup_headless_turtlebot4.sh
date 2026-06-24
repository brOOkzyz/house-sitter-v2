#!/usr/bin/env bash
set -Eeuo pipefail

# Stable TurtleBot 4 simulation bringup without Gazebo GUI / MinimalScene.
# Starts only the Gazebo server, /clock bridge, and TurtleBot 4 spawn stack.
# It does not start Nav2, AMCL, SLAM, or send robot commands.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
WORLD_FILE="/opt/ros/jazzy/share/turtlebot4_gz_bringup/worlds/warehouse.sdf"
RUN_ID="$(date '+%Y%m%d_%H%M%S')"
LOG_DIR="${PROJECT_ROOT}/logs/headless_${RUN_ID}"
STATUS_LOG="${LOG_DIR}/bringup.log"

declare -a CHILD_PIDS=()
declare -a CHILD_NAMES=()
CLEANING_UP=0

log() {
    printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "${STATUS_LOG}"
}

process_running() {
    kill -0 "$1" 2>/dev/null
}

stop_process_group() {
    local pid="$1"
    local name="$2"
    local attempt

    if ! process_running "${pid}"; then
        return
    fi

    log "Stopping ${name} with SIGINT (process group ${pid})"
    kill -INT -- "-${pid}" 2>/dev/null || true

    for attempt in {1..40}; do
        if ! process_running "${pid}"; then
            wait "${pid}" 2>/dev/null || true
            return
        fi
        sleep 0.25
    done

    log "${name} did not exit after 10s; sending SIGTERM"
    kill -TERM -- "-${pid}" 2>/dev/null || true
    wait "${pid}" 2>/dev/null || true
}

cleanup() {
    local index

    if [[ ${CLEANING_UP} -eq 1 ]]; then
        return
    fi
    CLEANING_UP=1

    log "Cleaning up processes started by this script"
    for ((index=${#CHILD_PIDS[@]} - 1; index >= 0; index--)); do
        stop_process_group "${CHILD_PIDS[index]}" "${CHILD_NAMES[index]}"
    done
    log "Cleanup complete"
}

start_child() {
    local name="$1"
    local logfile="$2"
    shift 2

    setsid "$@" >"${logfile}" 2>&1 &
    local pid=$!
    CHILD_PIDS+=("${pid}")
    CHILD_NAMES+=("${name}")
    log "Started ${name} (PID/PGID ${pid}); log: ${logfile}"
}

wait_for_gazebo_server() {
    local attempt

    for attempt in {1..60}; do
        if ! process_running "${CHILD_PIDS[0]}"; then
            log "Gazebo server exited before becoming ready"
            return 1
        fi
        if gz service -l 2>/dev/null | grep -qx '/world/warehouse/control'; then
            log "Gazebo server is ready"
            return 0
        fi
        sleep 0.5
    done

    log "Timed out waiting 30s for Gazebo server"
    return 1
}

wait_for_clock() {
    local attempt

    for attempt in {1..10}; do
        if timeout 2s ros2 topic echo /clock rosgraph_msgs/msg/Clock --once \
            >/dev/null 2>&1; then
            log "/clock bridge is ready"
            return 0
        fi
    done

    log "Timed out waiting 20s for /clock"
    return 1
}

main() {
    mkdir -p "${LOG_DIR}"
    touch "${STATUS_LOG}"

    if [[ ! -f /opt/ros/jazzy/setup.bash ]]; then
        log "ERROR: /opt/ros/jazzy/setup.bash not found"
        return 1
    fi
    # shellcheck disable=SC1091
    source /opt/ros/jazzy/setup.bash

    if [[ -f "${PROJECT_ROOT}/../install/setup.bash" ]]; then
        # shellcheck disable=SC1091
        source "${PROJECT_ROOT}/../install/setup.bash"
    fi

    if [[ ! -f "${WORLD_FILE}" ]]; then
        log "ERROR: world file not found: ${WORLD_FILE}"
        return 1
    fi

    log "Headless TurtleBot 4 bringup"
    log "Logs: ${LOG_DIR}"

    start_child \
        "Gazebo server" \
        "${LOG_DIR}/gazebo_server.log" \
        ros2 launch ros_gz_sim gz_sim.launch.py \
        "gz_args:=-r -s ${WORLD_FILE}"
    wait_for_gazebo_server

    start_child \
        "/clock bridge" \
        "${LOG_DIR}/clock_bridge.log" \
        ros2 run ros_gz_bridge parameter_bridge \
        '/clock@rosgraph_msgs/msg/Clock[gz.msgs.Clock'
    wait_for_clock

    start_child \
        "TurtleBot 4 spawn" \
        "${LOG_DIR}/turtlebot4_spawn.log" \
        ros2 launch turtlebot4_gz_bringup turtlebot4_spawn.launch.py \
        rviz:=false

    log "All headless components started. Press Ctrl+C to stop them cleanly."

    set +e
    wait -n "${CHILD_PIDS[@]}"
    local status=$?
    set -e
    log "A managed process exited (status ${status}); shutting down the stack"
    return "${status}"
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

main "$@"
