"""OpenMontage web server.

Serves the static UI and exposes a REST API for multi-user project management
on top of the existing OpenMontage registry, pipeline loader, checkpoint, and
MCP bridge handlers.

Run locally:
    python -m uvicorn api.web_server:app --reload --port 8000

Run in Docker:
    docker-compose up --build
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from mimetypes import guess_type
from pydantic import BaseModel, Field

log = logging.getLogger("web_server")

# Import MCP bridge handlers (they live in the same package)
from api.mcp_server import (
    HANDLERS,
    PROJECTS_DIR,
    PIPELINE_DIR,
    _is_safe_project_id,
    _load_json,
    _project_path,
)
from lib.checkpoint import get_latest_checkpoint, get_next_stage
from lib.pipeline_loader import list_pipelines, load_pipeline
from tools.tool_registry import registry

app = FastAPI(
    title="OpenMontage Web",
    version="0.1.0",
    description="Web UI and REST API for OpenMontage video production pipelines.")

# Serve the bundled dashboard from api/static/
static_dir = Path(__file__).resolve().parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class CreateProjectRequest(BaseModel):
    title: str = Field(..., min_length=1)
    pipeline: str = "animated-explainer"
    brief: str = ""
    duration_seconds: int = 60
    language: str = "vi"
    style: str = "clean-professional"
    target_platform: str = "youtube"


class ApproveRequest(BaseModel):
    stage: str
    approved: bool = True
    notes: str = ""


class ProjectSummary(BaseModel):
    project_id: str
    title: str
    pipeline: str
    created_at: str
    status: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _project_status(project_id: str) -> str | None:
    latest = get_latest_checkpoint(PIPELINE_DIR, project_id)
    if latest:
        return latest.get("status")
    return None


def _list_project_summaries() -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    if not PROJECTS_DIR.exists():
        return summaries
    for path in sorted(PROJECTS_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not path.is_dir():
            continue
        meta = _load_json(path / "project.json") or {}
        pid = meta.get("project_id") or path.name
        summaries.append({
            "project_id": pid,
            "title": meta.get("title", pid),
            "pipeline": meta.get("pipeline", "unknown"),
            "created_at": meta.get("created_at", ""),
            "status": _project_status(pid),
        })
    return summaries


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/")
async def root():
    index_path = static_dir / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return {"message": "OpenMontage Web API is running"}


@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}


@app.get("/preflight")
async def preflight():
    try:
        data = HANDLERS["openmontage_preflight"]({})
        return json.loads(data)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/pipelines")
async def pipelines():
    try:
        data = HANDLERS["openmontage_list_pipelines"]({})
        return json.loads(data)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/projects")
async def create_project(payload: CreateProjectRequest):
    try:
        slug = HANDLERS["openmontage_create_project"]({
            "title": payload.title,
            "pipeline": payload.pipeline,
            "brief": payload.brief,
        })
        project = json.loads(slug)
        project_id = project["project_id"]

        # Immediately create a production plan if brief was supplied
        if payload.brief:
            HANDLERS["openmontage_plan"]({
                "project_id": project_id,
                "brief": payload.brief,
                "pipeline": payload.pipeline,
                "duration_seconds": payload.duration_seconds,
                "language": payload.language,
                "style": payload.style,
                "target_platform": payload.target_platform,
            })
        return project
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/projects")
async def list_projects():
    return {"projects": _list_project_summaries()}


@app.get("/projects/{project_id}")
async def get_project(project_id: str):
    if not _is_safe_project_id(project_id):
        raise HTTPException(status_code=400, detail="Invalid project_id")
    project_path = _project_path(project_id)
    if not project_path.exists():
        raise HTTPException(status_code=404, detail="Project not found")
    metadata = _load_json(project_path / "project.json")
    latest = get_latest_checkpoint(PIPELINE_DIR, project_id)
    next_stage = None
    if metadata and metadata.get("pipeline"):
        try:
            next_stage = get_next_stage(PIPELINE_DIR, project_id, metadata["pipeline"])
        except Exception:
            next_stage = None
    return {
        "project_id": project_id,
        "metadata": metadata,
        "latest_checkpoint": latest,
        "next_stage": next_stage,
    }


@app.post("/projects/{project_id}/plan")
async def plan_project(project_id: str, payload: CreateProjectRequest):
    if not _is_safe_project_id(project_id):
        raise HTTPException(status_code=400, detail="Invalid project_id")
    try:
        result = HANDLERS["openmontage_plan"]({
            "project_id": project_id,
            "brief": payload.brief,
            "pipeline": payload.pipeline,
            "duration_seconds": payload.duration_seconds,
            "language": payload.language,
            "style": payload.style,
            "target_platform": payload.target_platform,
        })
        return json.loads(result)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/projects/{project_id}/approve")
async def approve_project(project_id: str, payload: ApproveRequest):
    if not _is_safe_project_id(project_id):
        raise HTTPException(status_code=400, detail="Invalid project_id")
    try:
        result = HANDLERS["openmontage_approve"]({
            "project_id": project_id,
            "stage": payload.stage,
            "approved": payload.approved,
            "notes": payload.notes,
        })
        return json.loads(result)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/projects/{project_id}/run/idea")
async def run_idea(project_id: str):
    if not _is_safe_project_id(project_id):
        raise HTTPException(status_code=400, detail="Invalid project_id")
    try:
        result = HANDLERS["openmontage_run_stage"]({
            "project_id": project_id,
            "stage": "idea",
        })
        return json.loads(result)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/projects/{project_id}/chat")
async def chat_project(project_id: str, request: Request):
    if not _is_safe_project_id(project_id):
        raise HTTPException(status_code=400, detail="Invalid project_id")
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc
    try:
        from api.pipeline_runner import chat_message
        result = chat_message(
            project_id=project_id,
            message=body.get("message", ""),
            history=body.get("history", []),
        )
        return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/projects/{project_id}/generate")
async def generate_project(project_id: str, request: Request):
    if not _is_safe_project_id(project_id):
        raise HTTPException(status_code=400, detail="Invalid project_id")
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc

    project_path = _project_path(project_id)
    if not project_path.exists():
        raise HTTPException(status_code=404, detail="Project not found")

    metadata = _load_json(project_path / "project.json") or {}
    brief = body.get("brief") or metadata.get("brief", "")
    if not brief:
        brief = metadata.get("title", "Video production")

    # Tự động nhận diện (Agentic Classification) Pipeline từ lịch sử chat
    pipeline_val = metadata.get("pipeline", "cinematic")
    lower_brief = brief.lower()
    if any(k in lower_brief for k in ["hyperframes", "remotion", "đồ họa", "đồ hoạ", "motion graphics", "infographic", "hoạt hình"]):
        pipeline_val = "animated-explainer"
    elif any(k in lower_brief for k in ["stock", "pexel", "tài liệu", "thực tế", "phong cảnh", "cảnh quay"]):
        pipeline_val = "cinematic"
    else:
        # Nếu chưa rõ, gọi LLM siêu tốc để phân loại
        try:
            from api.pipeline_runner import _call_llm
            prompt = f"Phân loại yêu cầu sau thành 1 trong 2 loại: 'animated-explainer' (video đồ hoạ, hyperframes, remotion) hoặc 'cinematic' (video dùng cảnh thật, pexels, stock). Yêu cầu: {brief}. CHỈ TRẢ VỀ ĐÚNG 1 TỪ (animated-explainer hoặc cinematic)."
            res = _call_llm([{"role":"user", "content": prompt}], temperature=0.1)
            if "animated" in res.lower() or "explainer" in res.lower():
                pipeline_val = "animated-explainer"
            elif "cinematic" in res.lower():
                pipeline_val = "cinematic"
        except Exception:
            pass
            
    # Cập nhật metadata để lưu lại pipeline đã chọn
    metadata["pipeline"] = pipeline_val
    import json
    with open(project_path / "project.json", "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)

    try:
        from api.pipeline_runner import run_pipeline
        from lib.checkpoint import write_checkpoint

        # Reset checkpoint immediately so the frontend poller does not read stale "completed" status
        write_checkpoint(PIPELINE_DIR, project_id, "proposal", "in_progress", {})

        # Return immediately with a 202 Accepted; run the pipeline in background.
        async def _run():
            try:
                run_pipeline(
                    project_id=project_id,
                    brief=brief,
                    pipeline=body.get("pipeline") or metadata.get("pipeline", "animated-explainer"),
                    duration_seconds=body.get("duration_seconds", 60),
                    language=body.get("language", "vi"),
                    render_runtime=body.get("render_runtime"),
                    auto_approve=True,
                    progress_callback=lambda stage, status, data=None: write_checkpoint(
                        PIPELINE_DIR,
                        project_id,
                        stage,
                        "in_progress" if status == "running" else status,
                        data or {},
                    ),
                )
            except Exception as exc:
                log.exception("Background generation failed for %s", project_id)
                write_checkpoint(PIPELINE_DIR, project_id, "compose", "failed", {"error": str(exc)})

        asyncio.create_task(_run())
        return {"project_id": project_id, "status": "started", "brief": brief}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.api_route("/projects/{project_id}/assets/{asset_path:path}", methods=["GET", "HEAD"])
async def serve_project_asset(project_id: str, asset_path: str, request: Request):
    """Serve a generated asset (video, image, audio) for preview/download."""
    if not _is_safe_project_id(project_id):
        raise HTTPException(status_code=400, detail="Invalid project_id")
    base = _project_path(project_id)
    target = (base / asset_path).resolve()
    # Security: refuse paths outside the project directory
    if not str(target).startswith(str(base)):
        raise HTTPException(status_code=403, detail="Access denied")
    if not target.exists() or target.is_dir():
        raise HTTPException(status_code=404, detail="Asset not found")

    content_type, _ = guess_type(str(target))
    if content_type is None:
        content_type = "application/octet-stream"

    if request.method == "HEAD":
        return Response(
            content="",
            media_type=content_type,
            headers={
                "Content-Length": str(target.stat().st_size),
                "Accept-Ranges": "bytes",
            },
        )

    file_size = target.stat().st_size
    range_header = request.headers.get("Range")

    if range_header:
        import re
        match = re.match(r"bytes=(\d+)-(\d*)", range_header)
        if match:
            start = int(match.group(1))
            end = match.group(2)
            end = int(end) if end else file_size - 1
            if start >= file_size or start > end:
                return Response(status_code=416, headers={"Content-Range": f"bytes */{file_size}"})
            end = min(end, file_size - 1)
            length = end - start + 1
            
            def file_iterator(path, offset, bytes_to_read):
                with open(path, "rb") as f:
                    f.seek(offset)
                    chunk_size = 65536
                    while bytes_to_read > 0:
                        chunk = f.read(min(chunk_size, bytes_to_read))
                        if not chunk:
                            break
                        yield chunk
                        bytes_to_read -= len(chunk)

            from fastapi.responses import StreamingResponse
            headers = {
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(length),
            }
            return StreamingResponse(
                file_iterator(target, start, length),
                status_code=206,
                headers=headers,
                media_type=content_type
            )

    headers = {"Accept-Ranges": "bytes", "Content-Length": str(file_size)}
    return FileResponse(str(target), headers=headers, media_type=content_type)


@app.exception_handler(Exception)
async def generic_exception_handler(_request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"error": type(exc).__name__, "detail": str(exc)},
    )
