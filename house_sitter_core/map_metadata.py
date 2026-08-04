"""Read-only ROS occupancy-map metadata and PGM pixels for local annotation."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - environment-dependent fallback
    yaml = None


class MapMetadataError(ValueError):
    """Raised when a ROS map YAML or PGM image is malformed."""


@dataclass(frozen=True)
class PgmImage:
    """Normalized 8-bit grayscale PGM image data, stored row-major from top left."""

    width: int
    height: int
    pixels: bytes
    format: str
    max_value: int


@dataclass(frozen=True)
class RosMapMetadata:
    """Read-only metadata needed to turn image pixels into map-frame points."""

    yaml_path: Path
    image_path: Path
    image: PgmImage
    resolution: float
    origin: tuple[float, float, float]
    negate: int
    occupied_thresh: float
    free_thresh: float

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        """Axis-aligned bounds for zero-yaw maps, used by the current project map."""
        origin_x, origin_y, _ = self.origin
        return (
            origin_x,
            origin_y,
            origin_x + self.image.width * self.resolution,
            origin_y + self.image.height * self.resolution,
        )


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MapMetadataError(f"{field} must be a finite number.")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise MapMetadataError(f"{field} must be a finite number.")
    return numeric


def _read_yaml(path: Path) -> dict[str, Any]:
    if yaml is None:
        raise MapMetadataError("PyYAML is required to read ROS map YAML files.")
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = yaml.safe_load(handle)
    except (OSError, yaml.YAMLError) as exc:
        raise MapMetadataError(f"Cannot load map YAML {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise MapMetadataError("ROS map YAML must contain an object.")
    return value


def _pgm_token(data: bytes, position: int) -> tuple[bytes, int]:
    """Read one ASCII PGM header/data token, skipping whitespace and comments."""
    size = len(data)
    while position < size:
        if data[position] in b" \t\r\n":
            position += 1
        elif data[position] == ord("#"):
            while position < size and data[position] not in b"\r\n":
                position += 1
        else:
            break
    start = position
    while position < size and data[position] not in b" \t\r\n#":
        position += 1
    if start == position:
        raise MapMetadataError("PGM file is truncated or missing a header value.")
    return data[start:position], position


def _pgm_positive_integer(token: bytes, field: str) -> int:
    try:
        value = int(token.decode("ascii"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise MapMetadataError(f"PGM {field} must be an integer.") from exc
    if value <= 0:
        raise MapMetadataError(f"PGM {field} must be positive.")
    return value


def load_pgm(path: Path) -> PgmImage:
    """Load P2 or 8-bit P5 PGM without changing the original map file."""
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise MapMetadataError(f"Cannot load map image {path}: {exc}") from exc

    magic, position = _pgm_token(data, 0)
    if magic not in {b"P2", b"P5"}:
        raise MapMetadataError("PGM format must be P2 or P5.")
    width_token, position = _pgm_token(data, position)
    height_token, position = _pgm_token(data, position)
    max_token, position = _pgm_token(data, position)
    width = _pgm_positive_integer(width_token, "width")
    height = _pgm_positive_integer(height_token, "height")
    max_value = _pgm_positive_integer(max_token, "max value")
    if max_value > 255:
        raise MapMetadataError("Only 8-bit PGM images with max value up to 255 are supported.")
    expected_pixels = width * height

    if magic == b"P2":
        values: list[int] = []
        while True:
            try:
                token, position = _pgm_token(data, position)
            except MapMetadataError:
                break
            value = _pgm_positive_integer(token, "pixel value") if token != b"0" else 0
            if value > max_value:
                raise MapMetadataError("PGM pixel value exceeds the declared maximum.")
            values.append(value)
        if len(values) != expected_pixels:
            raise MapMetadataError("PGM pixel data is truncated or has an unexpected length.")
        raw = bytes(round(value * 255 / max_value) for value in values)
        return PgmImage(width, height, raw, "P2", max_value)

    if position >= len(data) or data[position] not in b" \t\r\n":
        raise MapMetadataError("P5 PGM header must end with whitespace before pixel data.")
    if data[position] == ord("\r") and position + 1 < len(data) and data[position + 1] == ord("\n"):
        position += 1
    position += 1
    raw = data[position:]
    if len(raw) != expected_pixels:
        raise MapMetadataError("P5 PGM pixel data is truncated or has an unexpected length.")
    if max_value != 255:
        raw = bytes(round(value * 255 / max_value) for value in raw)
    return PgmImage(width, height, raw, "P5", max_value)


def load_ros_map(path: Path) -> RosMapMetadata:
    """Load a ROS map YAML and its image relative to the YAML file location."""
    yaml_path = Path(path).resolve()
    config = _read_yaml(yaml_path)
    image_value = config.get("image")
    if not isinstance(image_value, str) or not image_value.strip():
        raise MapMetadataError("ROS map YAML image must be a non-empty string.")
    image_path = (yaml_path.parent / image_value).resolve()
    if not image_path.is_file():
        raise MapMetadataError(f"ROS map image does not exist: {image_path}")

    resolution = _finite_number(config.get("resolution"), "resolution")
    if resolution <= 0:
        raise MapMetadataError("resolution must be greater than zero.")
    origin = config.get("origin")
    if not isinstance(origin, (list, tuple)) or len(origin) < 3:
        raise MapMetadataError("origin must contain x, y, and yaw.")
    parsed_origin = tuple(_finite_number(origin[index], f"origin[{index}]") for index in range(3))
    negate = config.get("negate")
    if isinstance(negate, bool) or not isinstance(negate, int) or negate not in {0, 1}:
        raise MapMetadataError("negate must be 0 or 1.")
    occupied_thresh = _finite_number(config.get("occupied_thresh"), "occupied_thresh")
    free_thresh = _finite_number(config.get("free_thresh"), "free_thresh")
    if not 0 <= free_thresh <= 1 or not 0 <= occupied_thresh <= 1:
        raise MapMetadataError("occupancy thresholds must be between 0 and 1.")

    return RosMapMetadata(
        yaml_path=yaml_path,
        image_path=image_path,
        image=load_pgm(image_path),
        resolution=resolution,
        origin=parsed_origin,  # type: ignore[arg-type]
        negate=negate,
        occupied_thresh=occupied_thresh,
        free_thresh=free_thresh,
    )
