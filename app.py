from __future__ import annotations

import logging
import os

from dotenv import load_dotenv
from flask import Flask, render_template, request

from figma_ingester import FigmaIngestionError, ingest_figma


load_dotenv()

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

app = Flask(__name__)


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


if __name__ == "__main__":
    app.run(debug=True)
