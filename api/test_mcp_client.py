"""Simple MCP client test for the OpenMontage bridge.

This test starts the MCP server in a subprocess and calls each exposed tool via
stdio. It is useful for verifying the bridge works without installing Claude
Desktop.

Usage:
    python api/test_mcp_client.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT_DIR = Path(__file__).resolve().parent.parent
SERVER_PATH = ROOT_DIR / "api" / "mcp_server.py"


def _assert_ok(result_text: str, label: str) -> dict:
    data = json.loads(result_text)
    if "error" in data:
        raise AssertionError(f"{label} failed: {data['error']}")
    print(f"\n=== {label} ===")
    print(json.dumps(data, indent=2, ensure_ascii=False))
    return data


async def run_tests() -> int:
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(SERVER_PATH)],
        cwd=str(ROOT_DIR),
    )

    # Unique project id for this test run
    test_id = f"mcp-demo-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            # 1. Preflight
            result = await session.call_tool("openmontage_preflight", {})
            _assert_ok(result.content[0].text, "Preflight")

            # 2. List pipelines
            result = await session.call_tool("openmontage_list_pipelines", {})
            _assert_ok(result.content[0].text, "Pipelines")

            # 3. Create project
            result = await session.call_tool(
                "openmontage_create_project",
                {"title": "MCP Demo Video", "slug": test_id, "pipeline": "animated-explainer"},
            )
            project = _assert_ok(result.content[0].text, "Create project")
            project_id = project["project_id"]

            # 4. Plan
            result = await session.call_tool(
                "openmontage_plan",
                {
                    "project_id": project_id,
                    "brief": "Make a 45-second animated explainer about why the sky is blue.",
                    "pipeline": "animated-explainer",
                    "duration_seconds": 45,
                    "language": "en",
                },
            )
            _assert_ok(result.content[0].text, "Plan")

            # 5. Approve
            result = await session.call_tool(
                "openmontage_approve",
                {"project_id": project_id, "stage": "proposal", "approved": True},
            )
            _assert_ok(result.content[0].text, "Approve")

            # 6. Run idea stage
            result = await session.call_tool(
                "openmontage_run_stage",
                {"project_id": project_id, "stage": "idea"},
            )
            _assert_ok(result.content[0].text, "Run idea stage")

            # 7. Status
            result = await session.call_tool(
                "openmontage_get_status",
                {"project_id": project_id},
            )
            _assert_ok(result.content[0].text, "Status")

    print("\nAll MCP bridge tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run_tests()))
