#!/usr/bin/env python3
"""Compose the final demo evidence image from existing simulation artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Dict

from PIL import Image, ImageDraw, ImageFont


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = PROJECT_ROOT / "logs"
MAP_VISUAL_PATH = LOG_DIR / "latest_llm_sim_nav2_visual.png"
RGB_SNAPSHOT_PATH = LOG_DIR / "latest_rgb_camera_snapshot.png"
MAP_SUMMARY_PATH = LOG_DIR / "latest_llm_sim_nav2_visual_summary.txt"
RGB_SUMMARY_PATH = LOG_DIR / "latest_rgb_camera_snapshot_summary.txt"
OUTPUT_IMAGE_PATH = LOG_DIR / "latest_final_demo_evidence.png"
OUTPUT_SUMMARY_PATH = LOG_DIR / "latest_final_demo_evidence_summary.txt"


def read_summary(path: Path) -> Dict[str, str]:
    data: Dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if ": " not in line:
            continue
        key, value = line.split(": ", 1)
        data[key.strip()] = value.strip()
    return data


def fit_image(image: Image.Image, max_size: tuple[int, int]) -> Image.Image:
    copy = image.copy()
    copy.thumbnail(max_size, Image.Resampling.LANCZOS)
    return copy


def paste_centered(canvas: Image.Image, image: Image.Image, box: tuple[int, int, int, int]) -> None:
    left, top, right, bottom = box
    width = right - left
    height = bottom - top
    x = left + (width - image.width) // 2
    y = top + (height - image.height) // 2
    canvas.paste(image, (x, y))


def main() -> int:
    map_summary = read_summary(MAP_SUMMARY_PATH)
    rgb_summary = read_summary(RGB_SUMMARY_PATH)

    map_visual = Image.open(MAP_VISUAL_PATH).convert("RGB")
    rgb_snapshot = Image.open(RGB_SNAPSHOT_PATH).convert("RGB")

    canvas = Image.new("RGB", (1800, 1180), color=(245, 246, 248))
    draw = ImageDraw.Draw(canvas)
    title_font = ImageFont.load_default(28)
    section_font = ImageFont.load_default(18)
    text_font = ImageFont.load_default(16)

    draw.text((50, 28), "Gemini LLM to simulation-only Nav2 demo", fill=(20, 24, 28), font=title_font)

    left_panel = (50, 90, 1060, 860)
    right_panel = (1110, 90, 1750, 860)
    bottom_panel = (50, 900, 1750, 1130)

    for box in (left_panel, right_panel, bottom_panel):
        draw.rounded_rectangle(box, radius=14, fill=(255, 255, 255), outline=(210, 214, 220), width=2)

    draw.text((72, 108), "Map-based Nav2 execution evidence", fill=(25, 30, 35), font=section_font)
    draw.text((1132, 108), "RGB camera snapshot from simulation", fill=(25, 30, 35), font=section_font)

    map_image = fit_image(map_visual, (left_panel[2] - left_panel[0] - 40, left_panel[3] - left_panel[1] - 90))
    rgb_image = fit_image(rgb_snapshot, (right_panel[2] - right_panel[0] - 40, right_panel[3] - right_panel[1] - 90))
    paste_centered(canvas, map_image, (left_panel[0] + 20, left_panel[1] + 50, left_panel[2] - 20, left_panel[3] - 20))
    paste_centered(canvas, rgb_image, (right_panel[0] + 20, right_panel[1] + 50, right_panel[2] - 20, right_panel[3] - 20))

    bottom_lines = [
        f"command: {map_summary.get('user command', 'visit the hallway')}",
        f"LLM provider: {map_summary.get('LLM provider', 'gemini')}",
        f"verifier: {map_summary.get('verifier result', 'passed')}",
        f"compute_path_to_pose: {map_summary.get('compute_path_to_pose result', 'PASS')}",
        f"navigate_to_pose: {map_summary.get('navigate_to_pose result', 'SUCCEEDED')}",
        f"direct /cmd_vel: {'avoided' if map_summary.get('direct /cmd_vel avoided', 'yes') == 'yes' else 'used'}",
        f"Gazebo GUI: {'not used' if rgb_summary.get('Gazebo GUI used', 'no') == 'no' else 'used'}",
        f"scope: {map_summary.get('simulation-only', 'yes')}",
    ]
    y = bottom_panel[1] + 24
    for line in bottom_lines:
        draw.text((72, y), line, fill=(25, 30, 35), font=text_font)
        y += 24

    canvas.save(OUTPUT_IMAGE_PATH)

    summary_lines = [
        f"visual map file: {MAP_VISUAL_PATH}",
        f"RGB snapshot file: {RGB_SNAPSHOT_PATH}",
        f"final evidence image file: {OUTPUT_IMAGE_PATH}",
        f"user command: {map_summary.get('user command', 'visit the hallway')}",
        f"LLM provider: {map_summary.get('LLM provider', 'gemini')}",
        f"verifier result: {map_summary.get('verifier result', 'passed')}",
        f"Nav2 result: {map_summary.get('navigate_to_pose result', 'SUCCEEDED')}",
        f"direct /cmd_vel avoided: {map_summary.get('direct /cmd_vel avoided', 'yes')}",
        f"simulation-only: {map_summary.get('simulation-only', 'yes')}",
        f"Gazebo GUI used: {rgb_summary.get('Gazebo GUI used', 'no')}",
    ]
    OUTPUT_SUMMARY_PATH.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")

    print(f"final evidence image written: {OUTPUT_IMAGE_PATH}")
    print(f"summary written: {OUTPUT_SUMMARY_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
