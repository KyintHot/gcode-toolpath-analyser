"""HTTP API and static file host for the G-code analyser web app."""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .analyzer import analyze
from .models import Config, ConfigError

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
SAMPLES = ROOT / "samples"
MAX_UPLOAD = 256 * 1024 * 1024  # bytes
CACHE_SIZE = 6

app = FastAPI(title="G-code Toolpath Analyser", version="1.0.0")

_cache: "OrderedDict[str, tuple[str, str]]" = OrderedDict()
_cache_lock = threading.Lock()


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1", errors="replace")


def _store(text: str, filename: str) -> str:
    token = uuid.uuid4().hex[:16]
    with _cache_lock:
        _cache[token] = (text, filename)
        while len(_cache) > CACHE_SIZE:
            _cache.popitem(last=False)
    return token


def _load(token: str) -> tuple[str, str]:
    with _cache_lock:
        item = _cache.get(token)
    if item is None:
        raise ConfigError(
            "That analysis is no longer in memory. Please upload the file again."
        )
    return item


def _config_from_query(request: Request) -> Config:
    return Config.from_mapping(dict(request.query_params))


def _payload(cfg: Config, text: str, filename: str, token: str, started: float) -> Response:
    result = analyze(text, cfg, filename)
    body = {
        "id": token,
        "file": filename,
        "metrics": result.metrics,
        "path": result.path,
        "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 1),
    }
    return Response(
        content=json.dumps(body, separators=(",", ":")),
        media_type="application/json",
    )


def _error(message: str, status: int) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "version": app.version, "web": WEB.exists()}


@app.post("/api/analyze")
async def analyze_upload(request: Request) -> Response:
    started = time.perf_counter()
    try:
        cfg = _config_from_query(request)
    except ConfigError as exc:
        return _error(str(exc), 400)

    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_UPLOAD:
        return _error("File is larger than the 256 MB limit", 413)

    data = await request.body()
    if not data:
        return _error("No file content received", 400)

    filename = unquote(request.headers.get("x-filename", "")).strip()[:160]
    if not filename:
        filename = "uploaded.gcode"

    text = _decode(data)
    token = _store(text, filename)
    try:
        return await run_in_threadpool(_payload, cfg, text, filename, token, started)
    except ConfigError as exc:
        return _error(str(exc), 400)
    except Exception as exc:  # defensive: never leak a bare 500
        return _error(f"Analysis failed: {type(exc).__name__}: {exc}", 500)


@app.post("/api/replan/{token}")
async def replan(token: str, request: Request) -> Response:
    started = time.perf_counter()
    raw = await request.body()
    try:
        body = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return _error("Body must be JSON", 400)
    try:
        text, filename = _load(token)
        cfg = Config.from_mapping((body or {}).get("config") or {})
    except ConfigError as exc:
        return _error(str(exc), 400)
    try:
        return await run_in_threadpool(_payload, cfg, text, filename, token, started)
    except Exception as exc:
        return _error(f"Analysis failed: {type(exc).__name__}: {exc}", 500)


@app.get("/api/samples")
def list_samples() -> dict[str, Any]:
    if not SAMPLES.is_dir():
        return {"samples": []}
    items = []
    for path in sorted(SAMPLES.glob("*.gcode")):
        items.append(
            {
                "name": path.name,
                "bytes": path.stat().st_size,
                "title": path.stem.replace("_", " ").title(),
            }
        )
    return {"samples": items}


@app.post("/api/samples/{name}")
async def load_sample(name: str, request: Request) -> Response:
    started = time.perf_counter()
    try:
        cfg = _config_from_query(request)
    except ConfigError as exc:
        return _error(str(exc), 400)
    if "/" in name or "\\" in name or not name.endswith(".gcode"):
        return _error("Unknown sample", 404)
    path = SAMPLES / name
    if not path.is_file():
        return _error("Unknown sample", 404)
    text = _decode(path.read_bytes())
    token = _store(text, name)
    try:
        return await run_in_threadpool(_payload, cfg, text, name, token, started)
    except Exception as exc:
        return _error(f"Analysis failed: {type(exc).__name__}: {exc}", 500)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB / "index.html")


if WEB.is_dir():
    app.mount("/static", StaticFiles(directory=str(WEB)), name="static")
