#!/usr/bin/env bash
set -o pipefail

# House Sitter v2 compact Nav2 readiness checker

#

# This script is read-only. It does NOT:

# - publish /cmd_vel

# - send /undock

# - send /navigate_to_pose

# - modify files

#

# Usage:

# ./house_sitter_v2/scripts/check_nav2_ready.sh

PASS=0
WARN=0
FAIL=0
DOCK_STATUS_TIMEOUT="${DOCK_STATUS_TIMEOUT:-20}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

source_ros() {
if [[ -f /opt/ros/jazzy/setup.bash ]]; then
source /opt/ros/jazzy/setup.bash
else
echo "[FAIL] /opt/ros/jazzy/setup.bash not found."
exit 1
fi

if [[ -f "${PROJECT_ROOT}/../install/setup.bash" ]]; then
source "${PROJECT_ROOT}/../install/setup.bash"
elif [[ -f "${PROJECT_ROOT}/install/setup.bash" ]]; then
source "${PROJECT_ROOT}/install/setup.bash"
fi
}

ok() {
echo "[OK]   $1"
PASS=$((PASS + 1))
}

warn() {
echo "[WARN] $1"
WARN=$((WARN + 1))
}

fail() {
echo "[FAIL] $1"
FAIL=$((FAIL + 1))
}

check_topic_data() {
local topic="$1"
local field="${2:-}"
local timeout_s="${3:-5}"
local tmp_file
tmp_file="$(mktemp)"

local args=("${topic}" "--once")
if [[ -n "${field}" ]]; then
args+=("--field" "${field}")
fi

timeout "${timeout_s}s" ros2 topic echo "${args[@]}" >"${tmp_file}" 2>&1
local code=$?

if [[ ${code} -eq 0 ]] && [[ -s "${tmp_file}" ]]; then
ok "${topic} has data"
else
fail "${topic} has no data within ${timeout_s}s"
sed -n '1,8p' "${tmp_file}" | sed 's/^/       /'
fi

rm -f "${tmp_file}"
}

check_topic_exists() {
local topic="$1"

if timeout 5s ros2 topic list 2>/dev/null | grep -qx "${topic}"; then
ok "topic exists: ${topic}"
else
fail "topic missing: ${topic}"
fi
}

check_action_exists() {
local action="$1"

if timeout 15s ros2 action list 2>/dev/null | grep -qx "${action}"; then
ok "action exists: ${action}"
else
fail "action missing: ${action}"
fi
}

check_tf() {
local from_frame="$1"
local to_frame="$2"
local tmp_file
tmp_file="$(mktemp)"

timeout 5s ros2 run tf2_ros tf2_echo "${from_frame}" "${to_frame}" >"${tmp_file}" 2>&1 || true

if grep -q "Translation:" "${tmp_file}"; then
ok "TF exists: ${from_frame} -> ${to_frame}"
else
fail "TF missing: ${from_frame} -> ${to_frame}"
sed -n '1,8p' "${tmp_file}" | sed 's/^/       /'
fi

rm -f "${tmp_file}"
}

check_lifecycle_active() {
local node="$1"
local output

output="$(timeout 8s ros2 lifecycle get "${node}" 2>/dev/null || true)"

if echo "${output}" | grep -qi "active"; then
ok "lifecycle active: ${node}"
elif [[ -n "${output}" ]]; then
warn "lifecycle not active: ${node} -> ${output}"
else
fail "lifecycle node unavailable: ${node}"
fi
}

print_topic_type() {
local topic="$1"
local output

output="$(timeout 5s ros2 topic info "${topic}" 2>/dev/null || true)"

if [[ -n "${output}" ]]; then
ok "topic info available: ${topic}"
echo "${output}" | sed -n '1,5p' | sed 's/^/       /'
else
warn "topic info unavailable: ${topic}"
fi
}

main() {
source_ros

echo "============================================================"
echo "House Sitter v2 Nav2 readiness check"
echo "============================================================"

echo
echo "[1] Core simulation data"
check_topic_data /clock "" 5
check_topic_data /scan "" 5
check_topic_data /odom "" 5
check_topic_exists /tf
check_topic_exists /tf_static

echo
echo "[2] Localization"
check_topic_data /map info 6
check_topic_data /amcl_pose pose.pose 6
check_tf map odom
check_tf odom base_link

echo
echo "[3] Nav2 lifecycle"
check_lifecycle_active /controller_server
check_lifecycle_active /planner_server
check_lifecycle_active /bt_navigator
check_lifecycle_active /behavior_server
check_lifecycle_active /velocity_smoother
check_lifecycle_active /collision_monitor

echo
echo "[4] Actions"
check_action_exists /navigate_to_pose
check_action_exists /undock

echo
echo "[5] Dock and wheel status"
check_topic_data /dock_status "" "${DOCK_STATUS_TIMEOUT}"
check_topic_data /wheel_status "" 5

echo
echo "[6] Velocity chain topic info"
print_topic_type /cmd_vel_nav
print_topic_type /cmd_vel_smoothed
print_topic_type /cmd_vel
print_topic_type /diffdrive_controller/cmd_vel

echo
echo "============================================================"
echo "Summary: PASS=${PASS}, WARN=${WARN}, FAIL=${FAIL}"
echo "============================================================"

if [[ ${FAIL} -eq 0 ]]; then
echo "[RESULT] Nav2 appears ready for the next confirmed step."
echo "         Do NOT send /navigate_to_pose or /cmd_vel without explicit confirmation."
exit 0
else
echo "[RESULT] Not ready yet. Fix failed checks before sending navigation goals."
exit 1
fi
}

main "$@"
