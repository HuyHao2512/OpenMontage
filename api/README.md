# OpenMontage MCP Server (Demo Bridge)

This is a thin **MCP (Model Context Protocol)** bridge that exposes a subset of OpenMontage to external clients such as Claude Desktop, Cursor, or any other MCP host.

> **Scope:** This bridge is intentionally thin. It handles **discovery, project creation, planning, approval, and checkpoint access**. Full pipeline stage execution still relies on the agent-first orchestration described in `AGENT_GUIDE.md`. The included `run_stage` tool demonstrates the wiring for the `idea` stage; production use should connect it to the real stage director skills or run the agent loop externally.

## Install

From the repo root:

```bash
pip install -r requirements.txt -r api/requirements-mcp.txt
```

## Run (stdio transport)

```bash
python api/mcp_server.py
```

Or with the MCP CLI:

```bash
mcp run api/mcp_server.py
```

> **Note:** The server discovers and probes the OpenMontage tool registry once at startup. The first launch can take 30–60 seconds depending on installed providers (it probes npm, Python packages, and environment variables). Subsequent tool calls are fast.

## Web UI (FastAPI)

A browser dashboard is also available via `api/web_server.py`.

```bash
pip install fastapi uvicorn python-multipart
python -m uvicorn api.web_server:app --host 0.0.0.0 --port 8000
```

Open http://localhost:8000 for the dashboard. See [DEPLOY.md](../DEPLOY.md) for Docker Compose deployment.

## Configure in Claude Desktop

Add to your Claude Desktop config (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "openmontage": {
      "command": "python",
      "args": ["D:/Test/OpenMontage/api/mcp_server.py"]
    }
  }
}
```

## Exposed Tools

| Tool | Purpose |
|------|---------|
| `openmontage_preflight` | Return the capability menu (composition runtimes + provider summary) |
| `openmontage_list_pipelines` | List available pipeline manifests |
| `openmontage_create_project` | Create a project workspace under `projects/` |
| `openmontage_plan` | Create a production plan and write an `awaiting_human` checkpoint |
| `openmontage_approve` | Mark a stage/plan as approved |
| `openmontage_run_stage` | Demo execution: runs the `idea` stage and produces a `brief` artifact |
| `openmontage_get_status` | Read the latest checkpoint + project metadata |

## Test

```bash
python api/test_mcp_client.py
```

## Notes

- The bridge reuses the existing `ToolRegistry`, `pipeline_loader`, and `checkpoint` modules. It does **not** add a Python orchestrator or bypass the pipeline/skill system.
- For production, replace the demo `run_stage` implementation with an agent loop that reads the stage director skill from `skills/pipelines/<pipeline>/` before calling tools.
- Never expose raw `.env` values or provider API keys through these tools.
