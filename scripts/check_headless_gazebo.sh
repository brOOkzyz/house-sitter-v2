#!/usr/bin/env bash
set -o pipefail

# Read-only readiness check for the headless TurtleBot 4 simulation route.
# This script does not publish topics, send actions, or start ROS processes.

PASS=0
FAIL=0

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

ok() {
    echo "[OK]   $1"
    PASS=$((PASS + 1))
}

fail() {
    echo "[FAIL] $1"
    FAIL=$((FAIL + 1))
}

source_setup_safely() {
    local setup_file="$1"

    if [[ ! -f "${setup_file}" ]]; then
        echo "ERROR: setup file not found: ${setup_file}" >&2
        return 1
    fi

    set +u
    # shellcheck disable=SC1090
    source "${setup_file}"
    set -u
}

source_ros() {
    if [[ ! -f /opt/ros/jazzy/setup.bash ]]; then
        fail "/opt/ros/jazzy/setup.bash not found"
        return 1
    fi
    source_setup_safely /opt/ros/jazzy/setup.bash

    if [[ -f "${PROJECT_ROOT}/install/setup.bash" ]]; then
        source_setup_safely "${PROJECT_ROOT}/install/setup.bash"
    fi
}

check_topic_data() {
    local topic="$1"
    local type="$2"
    local timeout_s="${3:-8}"
    local output
    local status

    output="$(timeout "${timeout_s}s" ros2 topic echo "${topic}" "${type}" --once 2>&1)"
    status=$?
    if [[ ${status} -eq 0 ]] && [[ -n "${output}" ]]; then
        ok "${topic} has data"
    else
        fail "${topic} has no data within ${timeout_s}s"
        printf '%s\n' "${output}" | sed -n '1,5p' | sed 's/^/       /'
    fi
}

check_server_only() {
    local pids
    local pid
    local command
    local found=0
    local unsafe=0

    pids="$(pgrep -f '[g]z sim' 2>/dev/null || true)"
    if [[ -z "${pids}" ]]; then
        fail "Gazebo server process is not running"
        return
    fi

    while read -r pid; do
        [[ -n "${pid}" ]] || continue
        [[ -r "/proc/${pid}/cmdline" ]] || continue
        command="$(tr '\0' ' ' <"/proc/${pid}/cmdline")"
        [[ "${command}" == *"gz sim"* ]] || continue
        found=1

        if [[ " ${command} " != *" -s "* ]] && \
            [[ " ${command} " != *" --server "* ]]; then
            fail "Gazebo process is not server-only: ${command}"
            unsafe=1
        fi

        if [[ -r "/proc/${pid}/maps" ]] && \
            grep -aq 'libMinimalScene\.so' "/proc/${pid}/maps"; then
            fail "libMinimalScene.so is loaded by PID ${pid}"
            unsafe=1
        fi
    done <<<"${pids}"

    if [[ ${found} -eq 0 ]]; then
        fail "Gazebo server process is not running"
        return
    fi

    if pgrep -af '[g]zclient|[g]z gui|[g]z-gui' >/dev/null 2>&1; then
        fail "Gazebo GUI/client process detected"
        pgrep -af '[g]zclient|[g]z gui|[g]z-gui' | sed 's/^/       /'
        unsafe=1
    fi

    if [[ ${unsafe} -eq 0 ]]; then
        ok "Gazebo is server-only; no GUI process or MinimalScene library detected"
    fi
}

main() {
    source_ros || exit 1

    echo "============================================================"
    echo "House Sitter v2 headless Gazebo readiness check"
    echo "============================================================"

    check_server_only
    check_topic_data /clock rosgraph_msgs/msg/Clock 15
    check_topic_data /scan sensor_msgs/msg/LaserScan 8
    check_topic_data /odom nav_msgs/msg/Odometry 8
    check_topic_data /dock_status irobot_create_msgs/msg/DockStatus 8

    echo "============================================================"
    echo "Summary: PASS=${PASS}, FAIL=${FAIL}"
    echo "============================================================"

    if [[ ${FAIL} -eq 0 ]]; then
        echo "[RESULT] Headless TurtleBot 4 simulation is ready."
        exit 0
    fi

    echo "[RESULT] Headless TurtleBot 4 simulation is not ready."
    exit 1
}

main "$@"
