from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from utils.url_parser import extract_figma_file_key


logger = logging.getLogger(__name__)

FIGMA_API_BASE = "https://api.figma.com/v1"
OUTPUT_DIR = Path("output")
REQUEST_TIMEOUT = (10, 60)


class FigmaIngestionError(Exception):
    pass


class FigmaConfigError(FigmaIngestionError):
    pass


class FigmaURLParseError(FigmaIngestionError):
    pass


class FigmaAPIError(FigmaIngestionError):
    pass


def ingest_figma(figma_url: str) -> dict[str, Any]:
    try:
        file_key = extract_figma_file_key(figma_url)
    except ValueError as exc:
        raise FigmaURLParseError(str(exc)) from exc

    api_key = get_figma_api_key()
    staging_dir: Path | None = None

    try:
        output_dir = OUTPUT_DIR / file_key
        output_dir.mkdir(parents=True, exist_ok=True)
        staging_dir = Path(tempfile.mkdtemp(prefix="ingest_", dir=str(output_dir)))

        raw_data = fetch_figma_file(file_key, api_key)
        normalized_data = normalize_figma_data(raw_data, figma_url=figma_url, file_key=file_key)

        raw_path = staging_dir / "figma_raw.json"
        normalized_path = staging_dir / "figma_normalized.json"
        manifest_path = staging_dir / "manifest.json"

        write_json(raw_path, raw_data)
        write_json(normalized_path, normalized_data)

        render_target = select_render_target(raw_data)
        render_path = staging_dir / "figma_render.png"
        rendered_node = render_figma_image(file_key, api_key, render_target, render_path)

        manifest = {
            "file_key": file_key,
            "file_name": raw_data.get("name"),
            "source_url": figma_url,
            "status": "success",
            "rendered_node_id": rendered_node.get("id"),
            "rendered_node_name": rendered_node.get("name"),
            "rendered_node_type": rendered_node.get("type"),
            "files": {
                "raw_json": "figma_raw.json",
                "normalized_json": "figma_normalized.json",
                "render": "figma_render.png",
            },
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        write_json(manifest_path, manifest)

        promote_staging_files(staging_dir, output_dir)

        return {
            "status": "success",
            "message": "Figma design imported successfully.",
            "file_key": file_key,
            "file_name": raw_data.get("name"),
            "output_dir": str(output_dir),
            "files": manifest["files"],
        }
    except Exception as exc:
        if staging_dir is not None:
            cleanup_staging(staging_dir)
        if isinstance(exc, FigmaIngestionError):
            raise
        logger.exception("Unexpected ingestion failure for file key %s", file_key)
        raise FigmaIngestionError("Import failed: Unexpected error while processing the Figma file.") from exc


def get_figma_api_key() -> str:
    api_key = os.getenv("FIGMA_API_KEY") or os.getenv("FIGMA_TOKEN")
    if not api_key:
        raise FigmaConfigError("Import failed: Missing FIGMA_API_KEY environment variable.")
    return api_key


def fetch_figma_file(file_key: str, api_key: str) -> dict[str, Any]:
    url = f"{FIGMA_API_BASE}/files/{file_key}"
    try:
        response = requests.get(url, headers={"X-Figma-Token": api_key}, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        raise FigmaAPIError("Import failed: Network error while contacting the Figma API.") from exc

    if response.status_code == 403:
        raise FigmaAPIError("Import failed: Figma file could not be accessed. Check the URL and API permissions.")
    if response.status_code == 404:
        raise FigmaAPIError("Import failed: Figma file was not found. Check the URL.")
    if response.status_code != 200:
        raise FigmaAPIError(f"Import failed: Figma API returned HTTP {response.status_code}.")

    try:
        data = response.json()
    except ValueError as exc:
        raise FigmaAPIError("Import failed: Figma API returned malformed JSON.") from exc

    if not isinstance(data, dict):
        raise FigmaAPIError("Import failed: Unexpected Figma API response structure.")

    return data


def normalize_figma_data(raw_data: dict[str, Any], *, figma_url: str, file_key: str) -> dict[str, Any]:
    root = raw_data.get("document")
    if not isinstance(root, dict):
        raise FigmaAPIError("Import failed: Figma document tree is missing from the API response.")

    nodes: dict[str, dict[str, Any]] = {}
    pages: list[dict[str, Any]] = []
    tree = normalize_node(root, parent_id=None, nodes=nodes, pages=pages)

    return {
        "file_key": file_key,
        "file_name": raw_data.get("name"),
        "source_url": figma_url,
        "last_modified": raw_data.get("lastModified"),
        "thumbnail_url": raw_data.get("thumbnailUrl"),
        "version": raw_data.get("version"),
        "role": raw_data.get("role"),
        "schema_version": raw_data.get("schemaVersion"),
        "pages": pages,
        "tree": tree,
        "nodes": nodes,
    }


def normalize_node(
    node: dict[str, Any],
    *,
    parent_id: str | None,
    nodes: dict[str, dict[str, Any]],
    pages: list[dict[str, Any]],
) -> dict[str, Any]:
    node_id = node.get("id")
    child_nodes = node.get("children") if isinstance(node.get("children"), list) else []
    children_ids: list[str] = []
    children: list[dict[str, Any]] = []

    for child in child_nodes:
        if isinstance(child, dict):
            child_summary = normalize_node(child, parent_id=node_id, nodes=nodes, pages=pages)
            children.append(child_summary)
            child_id = child_summary.get("id")
            if child_id is not None:
                children_ids.append(child_id)

    summary = {
        "id": node_id,
        "name": node.get("name"),
        "type": node.get("type"),
        "parent_id": parent_id,
        "child_ids": children_ids,
        "visible": node.get("visible"),
        "locked": node.get("locked"),
        "opacity": node.get("opacity"),
        "blendMode": node.get("blendMode"),
        "absoluteBoundingBox": node.get("absoluteBoundingBox"),
        "absoluteRenderBounds": node.get("absoluteRenderBounds"),
        "relativeTransform": node.get("relativeTransform"),
        "constraints": node.get("constraints"),
        "layoutMode": node.get("layoutMode"),
        "primaryAxisSizingMode": node.get("primaryAxisSizingMode"),
        "counterAxisSizingMode": node.get("counterAxisSizingMode"),
        "paddingLeft": node.get("paddingLeft"),
        "paddingRight": node.get("paddingRight"),
        "paddingTop": node.get("paddingTop"),
        "paddingBottom": node.get("paddingBottom"),
        "itemSpacing": node.get("itemSpacing"),
        "fills": node.get("fills"),
        "strokes": node.get("strokes"),
        "strokeWeight": node.get("strokeWeight"),
        "effects": node.get("effects"),
        "cornerRadius": node.get("cornerRadius"),
        "characters": node.get("characters"),
        "style": node.get("style"),
        "styleOverrideTable": node.get("styleOverrideTable"),
        "characterStyleOverrides": node.get("characterStyleOverrides"),
        "componentId": node.get("componentId"),
        "componentPropertyReferences": node.get("componentPropertyReferences"),
        "sharedPluginData": node.get("sharedPluginData"),
        "pluginData": node.get("pluginData"),
        "boundVariables": node.get("boundVariables"),
        "exportSettings": node.get("exportSettings"),
        "children": children,
    }

    if node.get("type") == "PAGE":
        pages.append(
            {
                "id": summary["id"],
                "name": summary["name"],
                "child_ids": children_ids,
            }
        )

    if node_id is not None:
        nodes[str(node_id)] = {
            k: v for k, v in summary.items() if k != "children"
        }

    return summary


def select_render_target(raw_data: dict[str, Any]) -> dict[str, Any]:
    document = raw_data.get("document")
    if not isinstance(document, dict):
        raise FigmaAPIError("Import failed: Cannot determine a render target from the Figma response.")

    # Prefer the first renderable frame/component. If none exists, fall back to the first page.
    fallback_page = None

    def walk(node: dict[str, Any]) -> dict[str, Any] | None:
        nonlocal fallback_page
        if node.get("type") == "PAGE" and fallback_page is None:
            fallback_page = node
        if node.get("type") in {"FRAME", "COMPONENT", "INSTANCE", "SECTION"}:
            return node
        children = node.get("children") if isinstance(node.get("children"), list) else []
        for child in children:
            if isinstance(child, dict):
                found = walk(child)
                if found is not None:
                    return found
        return None

    target = walk(document)
    if target is None:
        target = fallback_page or document

    if not isinstance(target, dict) or not target.get("id"):
        raise FigmaAPIError("Import failed: No renderable node was found in the Figma file.")

    return target


def render_figma_image(file_key: str, api_key: str, node: dict[str, Any], output_path: Path) -> dict[str, Any]:
    node_id = node.get("id")
    if not node_id:
        raise FigmaAPIError("Import failed: Render target node is missing an ID.")

    try:
        response = requests.get(
            f"{FIGMA_API_BASE}/images/{file_key}",
            headers={"X-Figma-Token": api_key},
            params={"ids": node_id, "format": "png", "scale": 2},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise FigmaAPIError("Import failed: Network error while requesting a render from Figma.") from exc

    if response.status_code == 403:
        raise FigmaAPIError("Import failed: Figma image rendering was denied by the API.")
    if response.status_code == 404:
        raise FigmaAPIError("Import failed: Figma image rendering target was not found.")
    if response.status_code != 200:
        raise FigmaAPIError(f"Import failed: Figma image API returned HTTP {response.status_code}.")

    try:
        payload = response.json()
    except ValueError as exc:
        raise FigmaAPIError("Import failed: Figma image API returned malformed JSON.") from exc

    image_url = None
    images = payload.get("images") if isinstance(payload, dict) else None
    if isinstance(images, dict):
        image_url = images.get(node_id)

    if not image_url:
        raise FigmaAPIError("Import failed: Figma did not return a render URL for the selected node.")

    try:
        image_response = requests.get(image_url, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        raise FigmaAPIError("Import failed: Network error while downloading the rendered image.") from exc
    if image_response.status_code != 200:
        raise FigmaAPIError("Import failed: Could not download the rendered image from Figma.")

    output_path.write_bytes(image_response.content)
    return node


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def promote_staging_files(staging_dir: Path, output_dir: Path) -> None:
    for item in staging_dir.iterdir():
        target = output_dir / item.name
        if target.exists():
            if target.is_file() or target.is_symlink():
                target.unlink()
            else:
                shutil.rmtree(target)
        shutil.move(str(item), str(target))
    cleanup_staging(staging_dir)


def cleanup_staging(staging_dir: Path) -> None:
    if staging_dir.exists():
        shutil.rmtree(staging_dir, ignore_errors=True)
