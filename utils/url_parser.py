from __future__ import annotations

from urllib.parse import urlparse


class InvalidFigmaURLError(ValueError):
    pass


def extract_figma_file_key(figma_url: str) -> str:
    value = (figma_url or "").strip()
    if not value:
        raise InvalidFigmaURLError("Import failed: Please provide a Figma design URL.")

    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or "figma.com" not in parsed.netloc.lower():
        raise InvalidFigmaURLError("Import failed: Invalid Figma URL.")

    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        raise InvalidFigmaURLError("Import failed: Could not extract a Figma file key from the URL.")

    supported_routes = {"design", "file", "proto", "board"}
    if parts[0] not in supported_routes:
        raise InvalidFigmaURLError("Import failed: Unsupported Figma URL format.")

    file_key = parts[1].strip()
    if not file_key:
        raise InvalidFigmaURLError("Import failed: Could not extract a Figma file key from the URL.")

    return file_key
