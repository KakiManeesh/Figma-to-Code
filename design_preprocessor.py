from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)

OUTPUT_DIR = Path("output")
NORMALIZED_FILENAME = "figma_normalized.json"
OUTPUT_FILENAME = "component_candidates.json"
MANIFEST_FILENAME = "manifest.json"

STRUCTURAL_NODE_TYPES = {"FRAME", "GROUP", "COMPONENT", "COMPONENT_SET", "INSTANCE", "SECTION"}
TEXTUAL_NODE_TYPES = {"TEXT"}
NOISE_CHILD_TYPES = {"RECTANGLE", "LINE", "VECTOR", "ELLIPSE"}

MIN_WIDTH = 20.0
MIN_HEIGHT = 20.0
MIN_AREA = 400.0
MAX_WRAPPER_CHILDREN = 1
TEXT_OVERLAP_SKIP_RATIO = 0.9
AREA_SIMILARITY_SKIP_RATIO = 0.85


class DesignPreprocessingError(Exception):
    pass


class DesignFileNotFoundError(DesignPreprocessingError):
    pass


class DesignDataError(DesignPreprocessingError):
    pass


def preprocess_design(file_key: str) -> dict[str, Any]:
    file_key = (file_key or "").strip()
    if not file_key:
        raise DesignFileNotFoundError("Preprocess failed: missing Figma file key.")

    output_dir = OUTPUT_DIR / file_key
    if not output_dir.exists():
        raise DesignFileNotFoundError("Preprocess failed: output folder does not exist for this file key.")

    normalized_path = output_dir / NORMALIZED_FILENAME
    if not normalized_path.exists():
        raise DesignFileNotFoundError("Preprocess failed: figma_normalized.json was not found.")

    raw_data = load_json(normalized_path)
    if not isinstance(raw_data, dict):
        raise DesignDataError("Preprocess failed: normalized design data is malformed.")

    tree = raw_data.get("tree")
    if not isinstance(tree, dict):
        raise DesignDataError("Preprocess failed: normalized design tree is missing or malformed.")

    indexed_nodes: dict[str, dict[str, Any]] = {}
    total_nodes = 0
    analyze_tree(tree, indexed_nodes=indexed_nodes, total_nodes_ref=[0], depth=0)
    total_nodes = len(indexed_nodes)

    candidates = []
    ordered_nodes = sorted(
        indexed_nodes.values(),
        key=lambda node: (node.get("depth", 0), node.get("bbox", {}).get("y", 0), node.get("bbox", {}).get("x", 0)),
    )

    candidate_lookup: dict[str, dict[str, Any]] = {}
    for node in ordered_nodes:
        if is_candidate_node(node):
            candidate_lookup[node["node_id"]] = build_candidate(node)

    for node in ordered_nodes:
        node_id = node["node_id"]
        candidate = candidate_lookup.get(node_id)
        if candidate and not is_redundant_candidate(candidate, candidate_lookup, indexed_nodes):
            candidates.append(candidate)

    result = {
        "file_key": file_key,
        "summary": {
            "total_nodes": total_nodes,
            "candidate_components": len(candidates),
        },
        "candidates": candidates,
    }

    output_path = output_dir / OUTPUT_FILENAME
    write_json_atomic(output_path, result)
    update_manifest(output_dir, file_key, result)

    return {
        "status": "success",
        "message": "Design preprocessing completed successfully.",
        "file_key": file_key,
        "file_name": raw_data.get("file_name"),
        "output_dir": str(output_dir),
        "files": {
            "component_candidates": OUTPUT_FILENAME,
        },
        "summary": result["summary"],
    }


def analyze_tree(
    node: dict[str, Any],
    *,
    indexed_nodes: dict[str, dict[str, Any]],
    total_nodes_ref: list[int],
    depth: int,
    parent: dict[str, Any] | None = None,
) -> dict[str, Any]:
    total_nodes_ref[0] += 1

    node_id = node.get("id")
    if node_id is None:
        raise DesignDataError("Preprocess failed: a node is missing its id.")

    node_type = node.get("type")
    node_name = node.get("name")
    children = node.get("children") if isinstance(node.get("children"), list) else []
    direct_children: list[dict[str, Any]] = [child for child in children if isinstance(child, dict)]

    descendant_texts: list[str] = []
    descendant_text_set: set[str] = set()
    child_type_counts: Counter[str] = Counter()
    child_ids: list[str] = []
    direct_texts: list[str] = []

    for child in direct_children:
        child_meta = analyze_tree(
            child,
            indexed_nodes=indexed_nodes,
            total_nodes_ref=total_nodes_ref,
            depth=depth + 1,
            parent=node,
        )
        child_id = child_meta.get("node_id")
        if child_id is not None:
            child_ids.append(child_id)
        child_type = child_meta.get("node_type")
        if child_type:
            child_type_counts[str(child_type)] += 1
        for text in child_meta.get("descendant_texts", []):
            if text not in descendant_text_set:
                descendant_text_set.add(text)
                descendant_texts.append(text)

    if node_type == "TEXT":
        text_value = normalize_text(node.get("characters"))
        if text_value:
            direct_texts = [text_value]
            if text_value not in descendant_text_set:
                descendant_text_set.add(text_value)
                descendant_texts.append(text_value)

    meta = {
        "node_id": node_id,
        "name": node_name,
        "node_type": node_type,
        "parent_id": parent.get("id") if isinstance(parent, dict) else None,
        "parent_name": parent.get("name") if isinstance(parent, dict) else None,
        "children_ids": child_ids,
        "child_count": len(child_ids),
        "bbox": normalize_bbox(node.get("absoluteBoundingBox")),
        "visible": node.get("visible"),
        "layout_mode": node.get("layoutMode"),
        "corner_radius": node.get("cornerRadius"),
        "has_fill": bool(node.get("fills")),
        "has_stroke": bool(node.get("strokes")),
        "opacity": node.get("opacity"),
        "child_type_counts": dict(child_type_counts),
        "direct_texts": direct_texts,
        "descendant_texts": descendant_texts,
        "depth": depth,
        "own_text_count": len(direct_texts),
        "descendant_text_count": len(descendant_texts),
        "area": compute_area(normalize_bbox(node.get("absoluteBoundingBox"))),
        "is_text_leaf": node_type in TEXTUAL_NODE_TYPES,
    }

    indexed_nodes[str(node_id)] = meta
    return meta


def is_candidate_node(node: dict[str, Any]) -> bool:
    node_type = node.get("node_type")
    if node_type not in STRUCTURAL_NODE_TYPES:
        return False

    bbox = node.get("bbox") or {}
    width = float(bbox.get("width") or 0)
    height = float(bbox.get("height") or 0)
    area = float(node.get("area") or 0)

    if width and width < MIN_WIDTH:
        return False
    if height and height < MIN_HEIGHT:
        return False
    if area and area < MIN_AREA and node.get("own_text_count", 0) == 0 and node.get("child_count", 0) == 0:
        return False

    child_count = int(node.get("child_count", 0) or 0)
    own_text_count = int(node.get("own_text_count", 0) or 0)
    descendant_text_count = int(node.get("descendant_text_count", 0) or 0)
    layout_mode = node.get("layout_mode")
    has_visuals = bool(node.get("has_fill")) or bool(node.get("has_stroke")) or node.get("corner_radius") is not None

    if child_count == 0 and own_text_count == 0 and descendant_text_count == 0:
        return False

    if own_text_count == 0 and descendant_text_count == 0:
        if child_count <= 1:
            return False
        if not any(child_type not in NOISE_CHILD_TYPES for child_type in node.get("child_type_counts", {})):
            return False

    if node_type in {"FRAME", "SECTION", "COMPONENT", "COMPONENT_SET"}:
        return descendant_text_count > 0 or child_count >= 1 or has_visuals or layout_mode is not None

    if node_type in {"GROUP", "INSTANCE"}:
        if descendant_text_count > 0 or own_text_count > 0:
            return True
        if child_count >= 2:
            return True
        if layout_mode is not None and has_visuals:
            return True
        return False

    return False


def build_candidate(node: dict[str, Any]) -> dict[str, Any]:
    bbox = node.get("bbox")
    if bbox is None:
        bbox = {"x": None, "y": None, "width": None, "height": None}

    return {
        "node_id": node.get("node_id"),
        "name": node.get("name"),
        "node_type": node.get("node_type"),
        "bbox": bbox,
        "parent_id": node.get("parent_id"),
        "parent_name": node.get("parent_name"),
        "children_ids": node.get("children_ids", []),
        "child_count": node.get("child_count", 0),
        "visible": node.get("visible"),
        "texts": node.get("descendant_texts", []),
        "child_type_counts": node.get("child_type_counts", {}),
        "style_hints": {
            "layout_mode": node.get("layout_mode"),
            "corner_radius": node.get("corner_radius"),
            "has_fill": node.get("has_fill"),
            "has_stroke": node.get("has_stroke"),
            "opacity": node.get("opacity"),
        },
    }


def is_redundant_candidate(
    candidate: dict[str, Any],
    candidate_lookup: dict[str, dict[str, Any]],
    indexed_nodes: dict[str, dict[str, Any]],
) -> bool:
    node_id = candidate.get("node_id")
    current = indexed_nodes.get(str(node_id))
    if not current:
        return False

    parent_id = current.get("parent_id")
    while parent_id:
        parent_candidate = candidate_lookup.get(str(parent_id))
        parent_node = indexed_nodes.get(str(parent_id))
        if parent_candidate and parent_node:
            if redundant_against_parent(candidate, parent_candidate, current, parent_node):
                return True
        parent_id = parent_node.get("parent_id") if parent_node else None
    return False


def redundant_against_parent(
    candidate: dict[str, Any],
    parent_candidate: dict[str, Any],
    current_node: dict[str, Any],
    parent_node: dict[str, Any],
) -> bool:
    candidate_texts = set(normalize_text_list(candidate.get("texts", [])))
    parent_texts = set(normalize_text_list(parent_candidate.get("texts", [])))
    candidate_direct_texts = set(normalize_text_list(current_node.get("direct_texts", [])))

    bbox = candidate.get("bbox") or {}
    parent_bbox = parent_candidate.get("bbox") or {}
    area = compute_area(bbox)
    parent_area = compute_area(parent_bbox)
    area_ratio = (area / parent_area) if parent_area else 0.0

    if not candidate_texts:
        return int(candidate.get("child_count", 0) or 0) <= MAX_WRAPPER_CHILDREN and area_ratio >= AREA_SIMILARITY_SKIP_RATIO

    new_texts = candidate_texts - parent_texts
    if not new_texts and not candidate_direct_texts and int(candidate.get("child_count", 0) or 0) <= MAX_WRAPPER_CHILDREN:
        return True

    overlap_ratio = len(candidate_texts & parent_texts) / len(candidate_texts) if candidate_texts else 0.0
    if overlap_ratio >= TEXT_OVERLAP_SKIP_RATIO and not candidate_direct_texts and int(candidate.get("child_count", 0) or 0) <= MAX_WRAPPER_CHILDREN and area_ratio >= AREA_SIMILARITY_SKIP_RATIO:
        return True

    if candidate_texts == parent_texts and area_ratio >= AREA_SIMILARITY_SKIP_RATIO:
        return True

    if not candidate_direct_texts and int(candidate.get("child_count", 0) or 0) <= MAX_WRAPPER_CHILDREN and area_ratio >= AREA_SIMILARITY_SKIP_RATIO:
        return True

    return False


def update_manifest(output_dir: Path, file_key: str, result: dict[str, Any]) -> None:
    manifest_path = output_dir / MANIFEST_FILENAME
    manifest = load_json(manifest_path)
    if not isinstance(manifest, dict):
        raise DesignDataError("Preprocess failed: manifest.json is missing or malformed.")

    files = manifest.get("files")
    if not isinstance(files, dict):
        files = {}
        manifest["files"] = files

    files["component_candidates"] = OUTPUT_FILENAME
    manifest["preprocessing"] = {
        "status": "success",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": NORMALIZED_FILENAME,
        "file_key": file_key,
        "summary": result.get("summary", {}),
    }

    write_json_atomic(manifest_path, manifest)


def normalize_bbox(value: Any) -> dict[str, float | None]:
    if not isinstance(value, dict):
        return {"x": None, "y": None, "width": None, "height": None}

    return {
        "x": value.get("x"),
        "y": value.get("y"),
        "width": value.get("width"),
        "height": value.get("height"),
    }


def compute_area(bbox: dict[str, Any] | None) -> float:
    if not isinstance(bbox, dict):
        return 0.0
    width = bbox.get("width")
    height = bbox.get("height")
    if width is None or height is None:
        return 0.0
    try:
        return float(width) * float(height)
    except (TypeError, ValueError):
        return 0.0


def normalize_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def normalize_text_list(values: list[Any]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = normalize_text(value)
        if text and text not in seen:
            seen.add(text)
            normalized.append(text)
    return normalized


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DesignFileNotFoundError(f"Preprocess failed: {path.name} could not be found.") from exc
    except json.JSONDecodeError as exc:
        raise DesignDataError(f"Preprocess failed: {path.name} is not valid JSON.") from exc


def write_json_atomic(path: Path, data: Any) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp_path.replace(path)
    except OSError as exc:
        raise DesignPreprocessingError(f"Preprocess failed: could not write {path.name}.") from exc
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
