#!/usr/bin/env bash
set -Eeuo pipefail

# Static visualization only: no ROS, Nav2, RViz, or robot commands.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
REGIONS="${1:-${PROJECT_ROOT}/local_annotations/demo_semantic_run_001/demo_semantic_regions.json}"
GOALS="${2:-${PROJECT_ROOT}/local_annotations/demo_semantic_run_001/safe_goal_candidates.json}"
OUTPUT="${3:-${PROJECT_ROOT}/local_annotations/gazebo_static_demo_run_001}"

if ! command -v gz >/dev/null 2>&1; then
  echo "ERROR: gz (Gazebo Sim) is not available." >&2
  exit 2
fi
if ! command -v xacro >/dev/null 2>&1 || [[ ! -f /opt/ros/jazzy/share/turtlebot4_description/urdf/standard/turtlebot4.urdf.xacro ]]; then
  echo "ERROR: installed TurtleBot4 standard Xacro is not available." >&2
  exit 2
fi
echo "Gazebo: $(gz sim --version | sed -n '1p')"
echo "TurtleBot4 model: /opt/ros/jazzy/share/turtlebot4_description/urdf/standard/turtlebot4.urdf.xacro"

echo "SYNTHETIC DEMO LABELS"
echo "NOT GROUND TRUTH"
echo "SIMULATION / REVIEW ONLY"
echo "ROBOT MOTION DISABLED"
python3 "${SCRIPT_DIR}/create_gazebo_static_demo.py" \
  --semantic-regions "${REGIONS}" --safe-goals "${GOALS}" --output-dir "${OUTPUT}"

# The installed Xacro conversion retains model://turtlebot4_description/... mesh URIs.
export GZ_SIM_RESOURCE_PATH="/opt/ros/jazzy/share${GZ_SIM_RESOURCE_PATH:+:${GZ_SIM_RESOURCE_PATH}}"
# The generated world contains only static models. No ROS nodes or navigation clients are started.
exec gz sim "${OUTPUT}/synthetic_demo.sdf"
