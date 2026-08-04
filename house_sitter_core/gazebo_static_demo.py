"""Generate a deterministic, static Gazebo Sim world from demo artifacts."""

from __future__ import annotations

import copy
import json
import math
import os
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from .simulation_sequence import SimulationSequenceError, build_simulation_sequence


class GazeboStaticDemoError(ValueError):
    """Raised when a static demo world cannot be built safely."""


LABELS = ("living_room", "kitchen", "bedroom", "charging_area")
COLORS = {
    "living_room": (0.86, 0.20, 0.18, 0.82),
    "kitchen": (0.12, 0.54, 0.86, 0.82),
    "bedroom": (0.58, 0.68, 0.08, 0.82),
    "charging_area": (0.42, 0.34, 0.78, 0.82),
}
MODEL_XACRO = Path("/opt/ros/jazzy/share/turtlebot4_description/urdf/standard/turtlebot4.urdf.xacro")
VISUALIZATION_SIZE_M = 12.0
VISUALIZATION_MARGIN_M = 1.0


def _load(path: Path, name: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GazeboStaticDemoError(f"Cannot load {name}: {exc}") from exc
    if not isinstance(value, dict):
        raise GazeboStaticDemoError(f"{name} must be a JSON object.")
    return value


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise GazeboStaticDemoError(f"{name} must be a finite number.")
    return float(value)


def _polygon(region: dict[str, Any]) -> list[tuple[float, float]]:
    polygon = region.get("polygon")
    vertices = polygon.get("vertices") if isinstance(polygon, dict) else None
    if not isinstance(vertices, list) or len(vertices) < 3:
        raise GazeboStaticDemoError("Each region polygon must contain at least three vertices.")
    points: list[tuple[float, float]] = []
    for index, point in enumerate(vertices):
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise GazeboStaticDemoError(f"polygon vertex {index} must contain two coordinates.")
        xy = (_finite(point[0], "polygon x"), _finite(point[1], "polygon y"))
        if points and xy == points[-1]:
            raise GazeboStaticDemoError("polygon contains consecutive duplicate vertices.")
        points.append(xy)
    if points[0] == points[-1]:
        points.pop()
    if len(points) < 3:
        raise GazeboStaticDemoError("polygon must have three distinct vertices.")
    return points


def _validate_inputs(regions: dict[str, Any], goals: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        build_simulation_sequence(regions, goals, LABELS)
    except SimulationSequenceError as exc:
        raise GazeboStaticDemoError(str(exc)) from exc
    region_records = regions.get("regions")
    goal_records = goals.get("goals")
    if not isinstance(region_records, list) or not isinstance(goal_records, list):
        raise GazeboStaticDemoError("Artifacts must contain regions and goals lists.")
    by_label: dict[str, dict[str, Any]] = {}
    for region in region_records:
        label = region.get("canonical_label")
        if label in by_label:
            raise GazeboStaticDemoError(f"duplicate region label: {label}")
        if label not in LABELS:
            raise GazeboStaticDemoError(f"unexpected region label: {label}")
        _polygon(region)
        by_label[label] = region
    goals_by_label: dict[str, dict[str, Any]] = {}
    for goal in goal_records:
        label = goal.get("canonical_label")
        if label in goals_by_label:
            raise GazeboStaticDemoError(f"multiple accepted goals for label: {label}")
        goals_by_label[label] = goal
    if set(by_label) != set(LABELS) or set(goals_by_label) != set(LABELS):
        raise GazeboStaticDemoError("Exactly the four default demo labels are required.")
    for label in LABELS:
        region, goal = by_label[label], goals_by_label[label]
        if (region["proposal_id"], region["partition_id"]) != (goal["proposal_id"], goal["candidate_partition_id"]):
            raise GazeboStaticDemoError(f"region and goal source mismatch for {label}")
    return by_label, goals_by_label


def _robot_model() -> ET.Element:
    if not MODEL_XACRO.is_file():
        raise GazeboStaticDemoError(f"TurtleBot4 standard model source not found: {MODEL_XACRO}")
    if shutil.which("xacro") is None or shutil.which("gz") is None:
        raise GazeboStaticDemoError("xacro and gz are required to materialize the installed TurtleBot4 model.")
    with tempfile.TemporaryDirectory(prefix="gazebo-static-model-") as directory:
        urdf = Path(directory) / "turtlebot4.urdf"
        try:
            generated = subprocess.run(
                ["xacro", str(MODEL_XACRO), "gazebo:=ignition", "namespace:=turtlebot4"],
                check=True, capture_output=True, text=True,
            ).stdout
            urdf.write_text(generated, encoding="utf-8")
            converted = subprocess.run(["gz", "sdf", "-p", str(urdf)], check=True, capture_output=True, text=True).stdout
            root = ET.fromstring(converted)
        except (OSError, subprocess.CalledProcessError, ET.ParseError) as exc:
            raise GazeboStaticDemoError(f"Could not materialize installed TurtleBot4 model: {exc}") from exc
    model = root.find("model")
    if model is None or model.get("name") != "turtlebot4":
        raise GazeboStaticDemoError("Materialized TurtleBot4 model has an unexpected SDF shape.")
    model = copy.deepcopy(model)
    static = model.find("static")
    if static is None:
        static = ET.Element("static")
        model.insert(0, static)
    static.text = "true"
    pose = model.find("pose")
    if pose is None:
        pose = ET.Element("pose")
        model.insert(1, pose)
    pose.text = "0 0 0.02 0 0 0"
    for parent in model.iter():
        for child in list(parent):
            if child.tag in {"plugin", "sensor"}:
                parent.remove(child)
    return model


def _visual(name: str, material: tuple[float, float, float, float], geometry: ET.Element, pose: str) -> ET.Element:
    model = ET.Element("model", {"name": name})
    ET.SubElement(model, "static").text = "true"
    ET.SubElement(model, "pose").text = pose
    link = ET.SubElement(model, "link", {"name": "visual_link"})
    visual = ET.SubElement(link, "visual", {"name": "visual"})
    ET.SubElement(visual, "geometry").append(geometry)
    material_node = ET.SubElement(visual, "material")
    ET.SubElement(material_node, "ambient").text = "%.6f %.6f %.6f %.6f" % material
    ET.SubElement(material_node, "diffuse").text = "%.6f %.6f %.6f %.6f" % material
    return model


def _region_edge(label: str, index: int, a: tuple[float, float], b: tuple[float, float]) -> ET.Element:
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    if length <= 1e-9:
        raise GazeboStaticDemoError("polygon contains a zero-length edge.")
    geometry = ET.Element("box")
    ET.SubElement(geometry, "size").text = f"{length:.9f} 0.09 0.10"
    pose = f"{(a[0]+b[0])/2:.9f} {(a[1]+b[1])/2:.9f} 0.05 0 0 {math.atan2(dy, dx):.9f}"
    return _visual(f"synthetic_region_{label}_edge_{index:03d}", COLORS[label], geometry, pose)


def _goal_marker(label: str, goal: dict[str, Any], position: tuple[float, float]) -> ET.Element:
    x, y = position
    geometry = ET.Element("cylinder")
    ET.SubElement(geometry, "radius").text = "0.22"
    ET.SubElement(geometry, "length").text = "0.55"
    return _visual(f"synthetic_goal_{label}_{goal['goal_order']:02d}", COLORS[label], geometry, f"{x:.9f} {y:.9f} 0.275 0 0 0")


def _region_index_marker(label: str, order: int, vertices: list[tuple[float, float]]) -> ET.Element:
    x = sum(point[0] for point in vertices) / len(vertices)
    y = sum(point[1] for point in vertices) / len(vertices)
    geometry = ET.Element("cylinder")
    ET.SubElement(geometry, "radius").text = "0.30"
    ET.SubElement(geometry, "length").text = f"{0.18 + order * 0.08:.3f}"
    return _visual(f"synthetic_region_index_{order}_{label}", COLORS[label], geometry, f"{x:.9f} {y:.9f} {0.09 + order * 0.04:.3f} 0 0 0")


def _visualization_transform(
    regions: dict[str, dict[str, Any]], goals: dict[str, dict[str, Any]],
) -> tuple[float, float, float, dict[str, list[tuple[float, float]]], dict[str, tuple[float, float]]]:
    original_regions = {label: _polygon(regions[label]) for label in LABELS}
    original_goals = {
        label: (_finite(goals[label]["goal"]["map_x"], "goal map_x"), _finite(goals[label]["goal"]["map_y"], "goal map_y"))
        for label in LABELS
    }
    all_points = [point for label in LABELS for point in original_regions[label]] + list(original_goals.values())
    min_x, max_x = min(point[0] for point in all_points), max(point[0] for point in all_points)
    min_y, max_y = min(point[1] for point in all_points), max(point[1] for point in all_points)
    span = max(max_x - min_x, max_y - min_y)
    if span <= 0:
        raise GazeboStaticDemoError("Visualization source geometry has no finite extent.")
    scale = (VISUALIZATION_SIZE_M - 2 * VISUALIZATION_MARGIN_M) / span
    offset_x = -(min_x + max_x) * 0.5 * scale
    offset_y = -(min_y + max_y) * 0.5 * scale
    transform = lambda point: (point[0] * scale + offset_x, point[1] * scale + offset_y)
    visual_regions = {label: [transform(point) for point in original_regions[label]] for label in LABELS}
    visual_goals = {label: transform(original_goals[label]) for label in LABELS}
    return scale, offset_x, offset_y, visual_regions, visual_goals


def _world_xml(
    regions: dict[str, dict[str, Any]], goals: dict[str, dict[str, Any]],
    visual_regions: dict[str, list[tuple[float, float]]], visual_goals: dict[str, tuple[float, float]],
) -> str:
    root = ET.Element("sdf", {"version": "1.9"})
    world = ET.SubElement(root, "world", {"name": "synthetic_demo"})
    ET.SubElement(world, "gravity").text = "0 0 -9.8"
    ET.SubElement(world, "plugin", {"name": "gz::sim::systems::SceneBroadcaster", "filename": "gz-sim-scene-broadcaster-system"})
    gui = ET.SubElement(world, "gui", {"fullscreen": "false"})
    view = ET.SubElement(gui, "plugin", {"name": "3D View", "filename": "GzScene3D"})
    gz_gui = ET.SubElement(view, "gz-gui"); ET.SubElement(gz_gui, "title").text = "Synthetic Demo 3D View"
    ET.SubElement(view, "engine").text = "ogre2"; ET.SubElement(view, "scene").text = "scene"
    ET.SubElement(view, "ambient_light").text = "0.75 0.75 0.75"
    ET.SubElement(view, "background_color").text = "0.12 0.14 0.18"
    ET.SubElement(view, "camera_pose").text = "-10 -10 11 0 0.62 0.785398"
    light = ET.SubElement(world, "light", {"name": "sun", "type": "directional"})
    ET.SubElement(light, "cast_shadows").text = "true"; ET.SubElement(light, "direction").text = "-0.4 0.2 -1"
    ground = _visual("synthetic_ground", (0.35, 0.37, 0.40, 1.0), ET.Element("plane"), "0 0 0 0 0 0")
    ET.SubElement(ground.find("link/visual/geometry/plane"), "normal").text = "0 0 1"  # type: ignore[union-attr]
    ET.SubElement(ground.find("link/visual/geometry/plane"), "size").text = f"{VISUALIZATION_SIZE_M} {VISUALIZATION_SIZE_M}"  # type: ignore[union-attr]
    world.append(ground)
    wall_color = (0.65, 0.67, 0.70, 0.50)
    for name, pose, size in (
        ("north", "0 6 0.30 0 0 0", "12 0.10 0.60"), ("south", "0 -6 0.30 0 0 0", "12 0.10 0.60"),
        ("east", "6 0 0.30 0 0 0", "0.10 12 0.60"), ("west", "-6 0 0.30 0 0 0", "0.10 12 0.60"),
    ):
        box = ET.Element("box"); ET.SubElement(box, "size").text = size
        world.append(_visual(f"visual_boundary_{name}", wall_color, box, pose))
    world.append(_robot_model())
    for order, label in enumerate(LABELS, 1):
        vertices = visual_regions[label]
        for index, (a, b) in enumerate(zip(vertices, vertices[1:] + vertices[:1]), 1):
            world.append(_region_edge(label, index, a, b))
        world.append(_region_index_marker(label, order, vertices))
        world.append(_goal_marker(label, goals[label], visual_goals[label]))
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="unicode", xml_declaration=True) + "\n"


def generate_world(regions_path: Path, goals_path: Path) -> tuple[str, dict[str, Any]]:
    regions_document, goals_document = _load(regions_path, "semantic regions"), _load(goals_path, "safe goals")
    regions, goals = _validate_inputs(regions_document, goals_document)
    scale, offset_x, offset_y, visual_regions, visual_goals = _visualization_transform(regions, goals)
    world = _world_xml(regions, goals, visual_regions, visual_goals)
    identity = regions_document["map_identity"]
    manifest = {
        "schema_version": "1.0", "demo_only": True, "synthetic_semantics": True,
        "ground_truth": False, "review_only": True, "simulation_only": True, "executable": False,
        "map_identity": identity,
        "source_artifacts": {"semantic_regions": regions_path.name, "safe_goals": goals_path.name},
        "turtlebot4_model_source": "turtlebot4_description/urdf/standard/turtlebot4.urdf.xacro (system ROS Jazzy)",
        "world_coordinate_policy": "Visualization-only uniform scale and translation; original artifact coordinates remain unchanged.",
        "visualization_only": True, "visualization_scale": scale,
        "visualization_offset_x": offset_x, "visualization_offset_y": offset_y,
        "regions": [{"label": label, "display_index": index, "proposal_id": regions[label]["proposal_id"], "partition_id": regions[label]["partition_id"], "original_map_coordinates": _polygon(regions[label]), "visual_gazebo_coordinates": visual_regions[label]} for index, label in enumerate(LABELS, 1)],
        "goals": [{"label": label, "proposal_id": goals[label]["proposal_id"], "partition_id": goals[label]["candidate_partition_id"], "goal_order": goals[label]["goal_order"], "original_map_coordinates": {"x": goals[label]["goal"]["map_x"], "y": goals[label]["goal"]["map_y"]}, "visual_gazebo_coordinates": {"x": visual_goals[label][0], "y": visual_goals[label][1]}} for label in LABELS],
        "colors": {label: list(COLORS[label]) for label in LABELS},
        "display_index_legend": {str(index): label for index, label in enumerate(LABELS, 1)},
        "generation_warnings": ["Static visualization only; robot motion, ROS, Nav2, and navigation commands are disabled."],
    }
    return world, manifest


def write_demo(output_dir: Path, world: str, manifest: dict[str, Any]) -> None:
    output = Path(output_dir)
    if output.exists():
        raise GazeboStaticDemoError(f"output directory already exists: {output}")
    temporary: Path | None = None; published = False
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=output.parent))
        (temporary / "synthetic_demo.sdf").write_text(world, encoding="utf-8")
        (temporary / "gazebo_demo_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, output); published = True
    finally:
        if not published and temporary is not None and temporary.exists():
            shutil.rmtree(temporary)
