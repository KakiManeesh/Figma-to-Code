# Figma to Code

## Project Overview

This project converts a Figma dashboard design into structured, machine-readable artifacts that can later be understood by AI and eventually rendered as a dashboard.

Current high-level pipeline:

```text
Figma URL
  ↓
Phase 1: Figma Data Ingestion
  ↓
figma_raw.json
figma_normalized.json
figma_render.png
  ↓
Phase 2A: Design Preprocessing
  ↓
component_candidates.json
```

The long-term goal is to continue from these extracted design artifacts into AI-driven design understanding, dashboard configuration, validation, and final rendering.

## Project Structure

```text
project/
├── app.py
├── design_preprocessor.py
├── figma_ingester.py
├── requirements.txt
├── .env.example
├── .gitignore
├── README.md
├── templates/
│   └── index.html
├── utils/
│   ├── __init__.py
│   └── url_parser.py
└── output/
    └── <file_key>/
        ├── figma_raw.json
        ├── figma_normalized.json
        ├── figma_render.png
        ├── manifest.json
        └── component_candidates.json
```

## Files and Their Responsibilities

### `app.py`
Flask entry point. It keeps the web layer thin and only handles routes, form input, and calls into the ingestion and preprocessing modules.

### `figma_ingester.py`
Implements Phase 1. It:

- extracts the Figma file key from the submitted URL
- reads the Figma API key from environment variables
- calls the Figma REST API
- saves the raw response as `figma_raw.json`
- creates a normalized design representation as `figma_normalized.json`
- retrieves a rendered image and saves it as `figma_render.png`
- writes `manifest.json`

### `design_preprocessor.py`
Implements Phase 2A. It reads `figma_normalized.json`, traverses the design tree deterministically, filters meaningful structural nodes, and writes `component_candidates.json`.

### `utils/url_parser.py`
Contains Figma URL parsing logic. It extracts the unique file key from supported Figma URL formats.

### `templates/index.html`
Simple Flask template for the browser UI. It provides:

- Figma URL input
- import success/error display
- a button to run preprocessing after import

### `requirements.txt`
Lists the Python dependencies required to run the app:

- Flask
- python-dotenv
- requests

### `.env.example`
Example environment file showing the expected Figma API key variable.

### `.gitignore`
Prevents secrets and generated runtime artifacts from being committed. The `output/` contents stay local.

### `output/`
Runtime output directory. Each imported Figma file gets its own folder named by file key. Generated ingestion and preprocessing artifacts are stored there.

## Implementation Phases

### Phase 1: Figma Data Ingestion - Completed

Purpose:

Capture the original Figma file data and a rendered visual reference from a user-provided URL.

What is implemented:

- Flask receives a Figma URL from the browser
- the file key is extracted from the URL
- the Figma REST API is called using the configured API key
- the raw API response is preserved
- the response is normalized into a cleaner tree structure
- a rendered image is retrieved for a practical top-level design node
- all files are written under `output/<file_key>/`

Input:

- Figma design URL

Output:

- `figma_raw.json`
- `figma_normalized.json`
- `figma_render.png`
- `manifest.json`

Files involved:

- `app.py`
- `figma_ingester.py`
- `utils/url_parser.py`
- `templates/index.html`

### Phase 2A: Figma Design Preprocessing - Completed

Purpose:

Transform `figma_normalized.json` into a cleaner structural summary that keeps useful hierarchy, text, geometry, and style hints while removing low-value detail.

What is implemented:

- deterministic Python traversal of the normalized Figma tree
- extraction of meaningful structural nodes such as `FRAME`, `GROUP`, `COMPONENT`, `COMPONENT_SET`, `INSTANCE`, and `SECTION`
- descendant text collection for each candidate node
- parent/child relationship preservation
- bounding box extraction
- compact style hints such as layout mode, fill presence, stroke presence, corner radius, and opacity
- child type counts for structural analysis later
- filtering of small or noisy nodes to reduce clutter
- output written to `component_candidates.json`
- `manifest.json` updated to include the new file

Input:

- `figma_normalized.json`

Output:

- `component_candidates.json`

Files involved:

- `design_preprocessor.py`
- `app.py`
- `templates/index.html`

## Current Pipeline

```text
Figma URL
  ↓
Phase 1: Figma Data Ingestion
  ↓
figma_raw.json
figma_normalized.json
figma_render.png
  ↓
Phase 2A: Design Preprocessing
  ↓
component_candidates.json
```

## Current Implementation Status

| Phase | Description | Status |
|---|---|---|
| Phase 1 | Figma Data Ingestion | Completed |
| Phase 2A | Design Preprocessing | Completed |
| Phase 2B | AI/VLM Design Understanding | Not Started |
| Phase 3 | Structured Dashboard Configuration | Not Started |
| Phase 4 | Dashboard Rendering | Not Started |

## Next Phase

### Phase 2B: AI/VLM Design Understanding

The next planned phase will use the outputs from preprocessing, along with the rendered Figma design where appropriate, to semantically understand the design components.

This phase is not implemented yet. The current project stops at deterministic preprocessing and structured candidate extraction.

## Running the Project

```bash
pip install -r requirements.txt
python app.py
```

Create a `.env` file from `.env.example` and provide your Figma API key:

```env
FIGMA_API_KEY=your_key_here
```
