#!/usr/bin/env bash
set -Eeuo pipefail

# Simulation-only full-stack demo for the current headless TurtleBot 4 route.
# Safety boundaries:
# - simulation-only; do not use this against a real robot
# - no Gazebo GUI
# - no direct /cmd_vel or /cmd_vel_unstamped publication
# - movement is only through Nav2 action goals
# - stop on failed readiness
# - stop if docked state cannot be confirmed
# - stop if compute_path_to_pose fails inside the micro smoke helper

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
LOG_DIR="${PROJECT_ROOT}/logs"
SUMMARY_FILE="${LOG_DIR}/latest_sim_full_stack_demo_summary.txt"
RUN_ID="$(date '+%Y%m%d_%H%M%S')"

mkdir -p "${LOG_DIR}"

source_setup_safely() {
    local setup_file="$1"

    if [[ ! -f "${setup_file}" ]]; then
        echo "[FAIL] setup file not found: ${setup_file}" >&2
        return 1
    fi

    set +u
    # shellcheck disable=SC1090
    source "${setup_file}"
    set -u
}

write_summary() {
    local result="$1"
    local reason="${2:-}"
    local micro_summary="${LOG_DIR}/latest_sim_nav2_micro_smoke_summary.txt"

    {
        echo "start time: ${RUN_ID}"
        echo "result: ${result}"
        echo "reason: ${reason}"
        echo "simulation-only: true"
        echo "gazebo gui: forbidden"
        echo "direct cmd_vel: forbidden"
        echo "movement interface: Nav2 actions only"
        echo "micro smoke summary: ${micro_summary}"
        if [[ -f "${micro_summary}" ]]; then
            echo
            echo "micro smoke details:"
            cat "${micro_summary}"
        fi
    } >"${SUMMARY_FILE}"
}

mark_micro_nav2_ready_pass() {
    local micro_summary="${LOG_DIR}/latest_sim_nav2_micro_smoke_summary.txt"

    if [[ -f "${micro_summary}" ]]; then
        sed -i 's/final Nav2 readiness: pending external check/final Nav2 readiness: PASS/' "${micro_summary}"
    fi
}

fail_demo() {
    local reason="$1"
    echo "[FAIL] ${reason}"
    write_summary "FAIL" "${reason}"
    echo "[SUMMARY] ${SUMMARY_FILE}"
    exit 1
}

pass_step() {
    echo "[PASS] $1"
}

run_checked() {
    local label="$1"
    shift

    echo "[CHECK] ${label}"
    if "$@"; then
        pass_step "${label}"
    else
        fail_demo "${label}"
    fi
}

dock_status_text() {
    timeout 20 ros2 topic echo --once /dock_status 2>&1
}

dock_is_docked() {
    local output="$1"

    if grep -q 'is_docked: true' <<<"${output}"; then
        echo "true"
    elif grep -q 'is_docked: false' <<<"${output}"; then
        echo "false"
    else
        echo "unknown"
    fi
}

ensure_undocked() {
    local output
    local state
    local action_type

    output="$(dock_status_text)" || fail_demo "/dock_status unavailable"
    printf '%s\n' "${output}" | sed -n '1,12p'
    state="$(dock_is_docked "${output}")"

    if [[ "${state}" == "unknown" ]]; then
        fail_demo "dock state cannot be confirmed"
    fi

    if [[ "${state}" == "false" ]]; then
        pass_step "dock status is_docked=false"
        return 0
    fi

    echo "[INFO] is_docked=true; sending one simulation-only /undock goal"
    action_type="$(timeout 5 ros2 action type /undock 2>/dev/null || true)"
    if [[ "${action_type}" != "irobot_create_msgs/action/Undock" ]]; then
        fail_demo "/undock action type is not irobot_create_msgs/action/Undock: ${action_type}"
    fi

    timeout 60 ros2 action send_goal /undock irobot_create_msgs/action/Undock "{}" \
        || fail_demo "simulation-only /undock failed"

    output="$(dock_status_text)" || fail_demo "/dock_status unavailable after undock"
    printf '%s\n' "${output}" | sed -n '1,12p'
    state="$(dock_is_docked "${output}")"
    if [[ "${state}" != "false" ]]; then
        fail_demo "robot is still docked after undock: ${state}"
    fi
    pass_step "undock complete; is_docked=false"
}

main() {
    echo "============================================================"
    echo "House Sitter v2 simulation-only full-stack demo"
    echo "============================================================"
    echo "Safety: no Gazebo GUI, no real robot, no direct /cmd_vel."
    echo "Movement is limited to Nav2 action goals after readiness checks."
    echo "============================================================"

    source_setup_safely /opt/ros/jazzy/setup.bash
    if [[ -f "${PROJECT_ROOT}/install/setup.bash" ]]; then
        source_setup_safely "${PROJECT_ROOT}/install/setup.bash"
    elif [[ -f "${PROJECT_ROOT}/../install/setup.bash" ]]; then
        source_setup_safely "${PROJECT_ROOT}/../install/setup.bash"
    else
        fail_demo "workspace install/setup.bash not found"
    fi

    cd "${PROJECT_ROOT}"

    run_checked "headless Gazebo readiness" timeout 60 ./scripts/check_headless_gazebo.sh
    run_checked "Nav2 readiness before micro smoke" timeout 90 ./scripts/check_nav2_ready.sh
    run_checked "required Nav2 actions exist" bash -c \
        "timeout 10 ros2 action list | grep -E '^/(navigate_to_pose|compute_path_to_pose)$' >/dev/null"

    ensure_undocked

    run_checked "simulation-only Nav2 micro smoke" timeout 140 python3 ./scripts/run_sim_nav2_micro_smoke.py
    run_checked "Nav2 readiness after micro smoke" timeout 90 ./scripts/check_nav2_ready.sh
    mark_micro_nav2_ready_pass

    write_summary "PASS" "simulation-only full-stack demo completed"
    echo "[PASS] simulation-only full-stack demo completed"
    echo "[SUMMARY] ${SUMMARY_FILE}"
}

main "$@"
