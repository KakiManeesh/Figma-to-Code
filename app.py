from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, render_template, request

from figma_ingester import FigmaIngestionError, ingest_figma
from design_preprocessor import DesignPreprocessingError, preprocess_design


load_dotenv()

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

app = Flask(__name__)


def load_file_context(file_key: str) -> dict[str, object]:
    output_dir = Path("output") / file_key
    manifest_path = output_dir / "manifest.json"
    if not manifest_path.exists():
        return {"file_key": file_key, "output_dir": str(output_dir), "files": {}}

    try:
        import json

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return {"file_key": file_key, "output_dir": str(output_dir), "files": {}}

    return {
        "file_key": manifest.get("file_key", file_key),
        "file_name": manifest.get("file_name"),
        "output_dir": str(output_dir),
        "files": manifest.get("files", {}),
        "source_url": manifest.get("source_url"),
    }


@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        figma_url = request.form.get("figma_url", "").strip()
        if not figma_url:
            return render_template("index.html", error="Please enter a Figma design URL.")

        try:
            result = ingest_figma(figma_url)
            return render_template("index.html", result=result)
        except FigmaIngestionError as exc:
            app.logger.exception("Figma import failed")
            return render_template("index.html", error=str(exc), figma_url=figma_url)

    return render_template("index.html")


@app.route("/preprocess/<file_key>", methods=["POST"])
def preprocess(file_key: str):
    try:
        result = load_file_context(file_key)
        preprocess_result = preprocess_design(file_key)
        result["files"] = {
            **result.get("files", {}),
            **preprocess_result.get("files", {}),
        }
        return render_template(
            "index.html",
            result=result,
            preprocess_result=preprocess_result,
        )
    except DesignPreprocessingError as exc:
        app.logger.exception("Design preprocessing failed")
        return render_template("index.html", error=str(exc), result=load_file_context(file_key))


if __name__ == "__main__":
    app.run(debug=True)
