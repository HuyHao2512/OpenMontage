"""OpenMontage MCP server bridge.

A thin MCP wrapper around OpenMontage discovery, project management, planning,
and checkpoint access. Full stage execution remains agent-driven; this server
only wires the existing Python infrastructure to an MCP transport.

Run:
    python api/mcp_server.py
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Make repo root importable from api/
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from lib.checkpoint import (
    get_latest_checkpoint,
    get_next_stage,
    read_checkpoint,
    write_checkpoint,
)
from lib.pipeline_loader import list_pipelines, load_pipeline
from tools.tool_registry import registry

# ---------------------------------------------------------------------------
# Paths and helpers
# ---------------------------------------------------------------------------

PROJECTS_DIR = ROOT_DIR / "projects"
PIPELINE_DIR = ROOT_DIR / "pipelines"


def _slugify(text: str) -> str:
    """Simple ASCII slugifier."""
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9\s-]+", "", text)
    text = re.sub(r"[\s-]+", "-", text)
    return text.strip("-") or "project"


def _ensure_registry() -> None:
    """Discover tools once."""
    registry.ensure_discovered("tools")


def _project_path(project_id: str) -> Path:
    return PROJECTS_DIR / project_id


def _is_safe_project_id(project_id: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9_-]+", project_id))


def _load_json(path: Path) -> Any:
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

TOOLS: list[Tool] = [
    Tool(
        name="openmontage_preflight",
        description="Return OpenMontage capability summary: composition runtimes and provider menu.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="openmontage_list_pipelines",
        description="List available OpenMontage pipeline manifests with stability info.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="openmontage_create_project",
        description="Create a project workspace under projects/<slug>.",
        inputSchema={
            "type": "object",
            "required": ["title"],
            "properties": {
                "title": {"type": "string", "description": "Human-readable project title"},
                "slug": {"type": "string", "description": "Optional project id; derived from title if omitted"},
                "pipeline": {"type": "string", "description": "Pipeline name to use (e.g. animated-explainer)"},
            },
        },
    ),
    Tool(
        name="openmontage_plan",
        description="Create a production plan for a project and write an awaiting_human checkpoint.",
        inputSchema={
            "type": "object",
            "required": ["project_id", "brief", "pipeline"],
            "properties": {
                "project_id": {"type": "string"},
                "brief": {"type": "string", "description": "Production brief / user request"},
                "pipeline": {"type": "string"},
                "duration_seconds": {"type": "integer"},
                "language": {"type": "string", "default": "en"},
                "style": {"type": "string", "default": "clean-professional"},
                "target_platform": {"type": "string", "default": "youtube"},
            },
        },
    ),
    Tool(
        name="openmontage_approve",
        description="Approve a stage or plan so execution can proceed.",
        inputSchema={
            "type": "object",
            "required": ["project_id", "stage", "approved"],
            "properties": {
                "project_id": {"type": "string"},
                "stage": {"type": "string"},
                "approved": {"type": "boolean"},
                "notes": {"type": "string"},
            },
        },
    ),
    Tool(
        name="openmontage_run_stage",
        description="Demo stage runner. Currently supports the 'idea' stage; produces a brief artifact and advances the checkpoint. Full pipeline execution requires agent orchestration.",
        inputSchema={
            "type": "object",
            "required": ["project_id", "stage"],
            "properties": {
                "project_id": {"type": "string"},
                "stage": {"type": "string", "description": "Stage to run (demo: only 'idea' implemented)"},
            },
        },
    ),
    Tool(
        name="openmontage_get_status",
        description="Return the latest checkpoint and project metadata for a project.",
        inputSchema={
            "type": "object",
            "required": ["project_id"],
            "properties": {"project_id": {"type": "string"}},
        },
    ),
]


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

def _handle_preflight(_arguments: dict[str, Any]) -> str:
    _ensure_registry()
    summary = registry.provider_menu_summary()
    pipelines = list_pipelines()
    return json.dumps(
        {
            "capabilities": summary,
            "pipelines": pipelines,
            "projects_dir": str(PROJECTS_DIR),
        },
        indent=2,
        ensure_ascii=False,
    )


def _handle_list_pipelines(_arguments: dict[str, Any]) -> str:
    pipelines = []
    for name in sorted(list_pipelines()):
        try:
            manifest = load_pipeline(name)
            pipelines.append(
                {
                    "id": name,
                    "name": manifest.get("name", name),
                    "stability": manifest.get("stability", "unknown"),
                    "description": manifest.get("description", ""),
                }
            )
        except Exception as exc:
            pipelines.append({"id": name, "error": str(exc)})
    return json.dumps({"pipelines": pipelines}, indent=2, ensure_ascii=False)


def _handle_create_project(args: dict[str, Any]) -> str:
    title = args.get("title", "").strip()
    if not title:
        raise ValueError("title is required")
    slug = args.get("slug", "").strip() or _slugify(title)
    pipeline = args.get("pipeline", "animated-explainer")

    if not _is_safe_project_id(slug):
        raise ValueError(f"Invalid project_id: {slug!r}")

    project_path = _project_path(slug)
    if project_path.exists():
        raise FileExistsError(f"Project {slug!r} already exists at {project_path}")

    for subdir in ("artifacts", "assets", "renders"):
        (project_path / subdir).mkdir(parents=True, exist_ok=True)

    metadata = {
        "project_id": slug,
        "title": title,
        "pipeline": pipeline,
        "brief": args.get("brief", ""),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(project_path / "project.json", "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)

    return json.dumps(
        {"project_id": slug, "path": str(project_path), "metadata": metadata},
        indent=2,
        ensure_ascii=False,
    )


def _handle_plan(args: dict[str, Any]) -> str:
    project_id = args["project_id"]
    if not _is_safe_project_id(project_id):
        raise ValueError(f"Invalid project_id: {project_id!r}")

    project_path = _project_path(project_id)
    if not project_path.exists():
        raise FileNotFoundError(f"Project {project_id!r} not found")

    pipeline = args["pipeline"]
    manifest = load_pipeline(pipeline)
    stages = manifest.get("stages", [])
    duration = args.get("duration_seconds", 60)
    brief = args["brief"]

    concept_options = [
        {
            "id": "c1",
            "title": f"{brief[:40]} — direct explainer",
            "hook": f"Ever wondered about {brief[:30]}?",
            "narrative_structure": "problem_solution",
            "visual_approach": "clean motion graphics with data callouts",
            "target_duration_seconds": duration,
            "why_this_works": "Directly answers the user's brief with a clear problem-solution arc.",
            "target_platform": args.get("target_platform", "youtube"),
            "key_points": ["Hook", "Core explanation", "Supporting evidence", "Call to action"],
            "core_message": brief,
            "cta": "Learn more",
            "tone": args.get("style", "clean-professional"),
        },
        {
            "id": "c2",
            "title": f"{brief[:40]} — story-driven explainer",
            "hook": f"Let me tell you a quick story about {brief[:30]}.",
            "narrative_structure": "story",
            "visual_approach": "character-led scenes with kinetic typography",
            "target_duration_seconds": duration,
            "why_this_works": "Emotional narrative improves retention for general audiences.",
            "target_platform": args.get("target_platform", "youtube"),
            "key_points": ["Character hook", "Rising action", "Climax", "Resolution"],
            "core_message": brief,
            "cta": "Subscribe for more stories",
            "tone": args.get("style", "clean-professional"),
        },
        {
            "id": "c3",
            "title": f"{brief[:40]} — comparison explainer",
            "hook": f"What's the real difference behind {brief[:30]}?",
            "narrative_structure": "comparison",
            "visual_approach": "split-screen comparison with animated diagrams",
            "target_duration_seconds": duration,
            "why_this_works": "Comparison structures make abstract concepts concrete.",
            "target_platform": args.get("target_platform", "youtube"),
            "key_points": ["Side A", "Side B", "Key contrast", "Takeaway"],
            "core_message": brief,
            "cta": "Watch the next video",
            "tone": args.get("style", "clean-professional"),
        },
    ]

    production_plan = {
        "pipeline": pipeline,
        "render_runtime": "ffmpeg",
        "stages": [
            {
                "stage": stage["name"],
                "tools": [
                    {
                        "tool_name": "video_compose",
                        "role": "assemble and render the final video",
                        "available": True,
                    }
                ],
                "approach": f"Execute {stage['name']} stage according to the pipeline manifest.",
            }
            for stage in stages
        ],
        "delivery_promise": {
            "promise_type": "motion_led",
            "motion_required": True,
            "source_required": False,
            "tone_mode": "educational",
            "quality_floor": "presentable",
            "approved_fallback": None,
        },
        "renderer_family": "explainer-teacher",
    }

    proposal_packet = {
        "version": "1.0",
        "concept_options": concept_options,
        "selected_concept": {
            "concept_id": "c1",
            "rationale": "Best alignment with the user's brief and target duration.",
        },
        "production_plan": production_plan,
        "cost_estimate": {
            "total_estimated_usd": 0.0,
            "line_items": [
                {
                    "tool": "video_compose",
                    "operation": "local composition and render",
                    "quantity": 1,
                    "estimated_usd": 0.0,
                    "notes": "Demo plan; real costs depend on provider usage.",
                }
            ],
            "budget_verdict": "no_budget_set",
        },
        "approval": {
            "status": "pending",
            "user_notes": "Awaiting human approval via MCP client.",
        },
        "metadata": {
            "brief": brief,
            "language": args.get("language", "en"),
            "style": args.get("style", "clean-professional"),
            "target_platform": args.get("target_platform", "youtube"),
            "duration_seconds": duration,
        },
    }

    # Write proposal_packet artifact and awaiting_human checkpoint
    artifacts = {"proposal_packet": proposal_packet}
    checkpoint_path = write_checkpoint(
        pipeline_dir=PIPELINE_DIR,
        project_id=project_id,
        stage="proposal",
        status="awaiting_human",
        artifacts=artifacts,
        pipeline_type=pipeline,
        human_approval_required=True,
    )

    return json.dumps(
        {
            "project_id": project_id,
            "status": "awaiting_human",
            "checkpoint": str(checkpoint_path),
            "artifact": proposal_packet,
        },
        indent=2,
        ensure_ascii=False,
    )


def _handle_approve(args: dict[str, Any]) -> str:
    project_id = args["project_id"]
    stage = args["stage"]
    approved = bool(args.get("approved"))
    notes = args.get("notes", "")

    if not _is_safe_project_id(project_id):
        raise ValueError(f"Invalid project_id: {project_id!r}")

    cp = read_checkpoint(PIPELINE_DIR, project_id, stage)
    if cp is None:
        raise FileNotFoundError(f"No checkpoint found for {project_id}/{stage}")

    cp["human_approved"] = approved
    cp["human_approval_required"] = False
    cp["approval_notes"] = notes
    cp["approval_timestamp"] = datetime.now(timezone.utc).isoformat()
    if approved:
        cp["status"] = "completed"

    checkpoint_path = write_checkpoint(
        pipeline_dir=PIPELINE_DIR,
        project_id=project_id,
        stage=stage,
        status=cp["status"],
        artifacts=cp["artifacts"],
        pipeline_type=cp.get("pipeline_type"),
        human_approval_required=False,
        human_approved=approved,
        metadata=cp.get("metadata"),
    )

    return json.dumps(
        {
            "project_id": project_id,
            "stage": stage,
            "approved": approved,
            "checkpoint": str(checkpoint_path),
        },
        indent=2,
        ensure_ascii=False,
    )


def _handle_run_stage(args: dict[str, Any]) -> str:
    """Demo runner for the 'idea' stage.

    Production implementation should invoke the agent loop that reads the stage
    director skill and calls the appropriate tools.
    """
    project_id = args["project_id"]
    stage = args["stage"]

    if not _is_safe_project_id(project_id):
        raise ValueError(f"Invalid project_id: {project_id!r}")

    project_path = _project_path(project_id)
    metadata = _load_json(project_path / "project.json") or {}
    pipeline = metadata.get("pipeline", "animated-explainer")

    if stage != "idea":
        return json.dumps(
            {
                "project_id": project_id,
                "stage": stage,
                "status": "not_implemented",
                "message": (
                    "This demo MCP bridge only runs the 'idea' stage. "
                    "Full execution requires an agent loop over the pipeline stages."
                ),
            },
            indent=2,
            ensure_ascii=False,
        )

    # Produce a simple brief artifact for the demo idea stage.
    # We write it directly to the project's artifacts folder instead of a
    # checkpoint because "idea" is not a valid stage in pipeline manifests.
    brief = {
        "title": metadata.get("title", project_id),
        "hook": f"An engaging introduction to {metadata.get('title', project_id)}.",
        "target_audience": "general",
        "tone": "clean and informative",
        "duration_seconds": 60,
        "language": metadata.get("language", "en"),
        "key_points": ["Hook", "Context", "Core message", "Call to action"],
    }

    idea_path = project_path / "artifacts" / "idea_brief.json"
    idea_path.parent.mkdir(parents=True, exist_ok=True)
    with open(idea_path, "w", encoding="utf-8") as f:
        json.dump(brief, f, indent=2, ensure_ascii=False)

    return json.dumps(
        {
            "project_id": project_id,
            "stage": "idea",
            "status": "completed",
            "artifact_path": str(idea_path),
            "brief": brief,
        },
        indent=2,
        ensure_ascii=False,
    )


def _handle_get_status(args: dict[str, Any]) -> str:
    project_id = args["project_id"]
    if not _is_safe_project_id(project_id):
        raise ValueError(f"Invalid project_id: {project_id!r}")

    project_path = _project_path(project_id)
    metadata = _load_json(project_path / "project.json")
    latest = get_latest_checkpoint(PIPELINE_DIR, project_id)
    next_stage = None
    if metadata and metadata.get("pipeline"):
        try:
            next_stage = get_next_stage(PIPELINE_DIR, project_id, metadata["pipeline"])
        except Exception:
            next_stage = None

    return json.dumps(
        {
            "project_id": project_id,
            "metadata": metadata,
            "latest_checkpoint": latest,
            "next_stage": next_stage,
        },
        indent=2,
        ensure_ascii=False,
    )


HANDLERS = {
    "openmontage_preflight": _handle_preflight,
    "openmontage_list_pipelines": _handle_list_pipelines,
    "openmontage_create_project": _handle_create_project,
    "openmontage_plan": _handle_plan,
    "openmontage_approve": _handle_approve,
    "openmontage_run_stage": _handle_run_stage,
    "openmontage_get_status": _handle_get_status,
}


# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------

app = Server("openmontage-mcp-bridge")


@app.list_tools()
async def list_tools() -> list[Tool]:
    return TOOLS


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any] | None) -> list[TextContent]:
    handler = HANDLERS.get(name)
    if handler is None:
        raise ValueError(f"Unknown tool: {name}")

    try:
        result = handler(arguments or {})
    except Exception as exc:
        # Surface structured errors so the MCP client can act on them
        error_payload = {
            "error": {
                "code": type(exc).__name__,
                "message": str(exc),
            }
        }
        return [TextContent(type="text", text=json.dumps(error_payload, indent=2, ensure_ascii=False))]

    return [TextContent(type="text", text=result)]


async def main() -> None:
    # Discover tools once at startup so the first tool call (usually
    # preflight) returns promptly instead of blocking on imports/network probes.
    _ensure_registry()
    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
