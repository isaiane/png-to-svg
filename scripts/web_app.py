#!/usr/bin/env python3
"""Run the PNG-to-SVG vectorizer as a local web application."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import webbrowser
from email.parser import BytesParser
from email.policy import default
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


SKILL_DIR = Path(__file__).resolve().parent.parent
INDEX_FILE = SKILL_DIR / "assets" / "web" / "index.html"
TRACER = Path(__file__).with_name("png_to_svg.py")
MAX_UPLOAD = 25 * 1024 * 1024


def multipart_fields(content_type: str, body: bytes) -> tuple[dict[str, str], bytes, str]:
    message = BytesParser(policy=default).parsebytes(
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + body
    )
    if not message.is_multipart():
        raise ValueError("expected multipart/form-data")
    fields: dict[str, str] = {}
    image = b""
    filename = "upload.png"
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        payload = part.get_payload(decode=True) or b""
        if name == "image":
            image = payload
            filename = part.get_filename() or filename
        else:
            fields[name] = payload.decode("utf-8", errors="replace")
    if not image:
        raise ValueError("select a PNG image")
    return fields, image, filename


def build_command(fields: dict[str, str], source: Path, output: Path, report: Path) -> list[str]:
    command = [sys.executable, str(TRACER), str(source), str(output), "--report", str(report)]
    allowed = {
        "mode": "--mode",
        "colors": "--colors",
        "threshold": "--threshold",
        "foreground": "--foreground",
        "alpha_threshold": "--alpha-threshold",
        "background": "--background",
        "bg_tolerance": "--bg-tolerance",
        "blur": "--blur",
        "simplify": "--simplify",
        "min_area": "--min-area",
        "max_dimension": "--max-dimension",
        "precision": "--precision",
    }
    for field, option in allowed.items():
        value = fields.get(field, "").strip()
        if value:
            command.extend((option, value))
    if fields.get("invert") == "true":
        command.append("--invert")
    palette = fields.get("palette", "").strip()
    if palette:
        command.extend(("--palette", palette))
    return command


class Handler(BaseHTTPRequestHandler):
    server_version = "PNGtoSVG/1.0"

    def send_bytes(self, status: int, content_type: str, payload: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(payload)

    def send_json(self, status: int, data: object) -> None:
        self.send_bytes(status, "application/json; charset=utf-8", json.dumps(data).encode())

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/":
            self.send_bytes(HTTPStatus.OK, "text/html; charset=utf-8", INDEX_FILE.read_bytes())
        elif path == "/health":
            self.send_json(HTTPStatus.OK, {"status": "ok"})
        else:
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if urlparse(self.path).path != "/api/vectorize":
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_UPLOAD:
                raise ValueError("upload must be between 1 byte and 25 MB")
            fields, image, filename = multipart_fields(
                self.headers.get("Content-Type", ""), self.rfile.read(length)
            )
            suffix = Path(filename).suffix.lower()
            if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
                suffix = ".png"
            with tempfile.TemporaryDirectory(prefix="png-to-svg-") as temp:
                temp_dir = Path(temp)
                source = temp_dir / f"input{suffix}"
                output = temp_dir / "output.svg"
                report = temp_dir / "report.json"
                source.write_bytes(image)
                completed = subprocess.run(
                    build_command(fields, source, output, report),
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
                )
                if completed.returncode != 0:
                    error = (completed.stderr or completed.stdout or "vectorization failed").strip()
                    self.send_json(HTTPStatus.BAD_REQUEST, {"error": error[-1200:]})
                    return
                summary = json.loads(report.read_text(encoding="utf-8"))
                summary["input"] = filename
                summary["output"] = "vectorized.svg"
                self.send_json(
                    HTTPStatus.OK,
                    {"svg": output.read_text(encoding="utf-8"), "summary": summary},
                )
        except subprocess.TimeoutExpired:
            self.send_json(HTTPStatus.REQUEST_TIMEOUT, {"error": "vectorization timed out"})
        except Exception as exc:  # keep the local UI responsive with a useful message
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    def log_message(self, message: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {message % args}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", help="open the default browser")
    args = parser.parse_args()
    if not INDEX_FILE.is_file() or not TRACER.is_file():
        raise SystemExit("skill assets are incomplete")
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}"
    print(f"PNG to SVG is running at {url}")
    print("Press Ctrl+C to stop.")
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
