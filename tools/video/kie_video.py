"""KIE.ai video generation provider tool."""

import os
import time
import requests
from pathlib import Path
from typing import Any
from tools.base_tool import BaseTool, ToolResult, ToolTier, ToolStatus, ToolRuntime, ToolStability

class KIEVideo(BaseTool):
    name = "kie_video"
    version = "0.1.0"
    tier = ToolTier.GENERATE
    capability = "video_generation"
    provider = "kie"
    stability = ToolStability.BETA
    runtime = ToolRuntime.API

    dependencies = []
    install_instructions = (
        "Set KIE_API_KEY to your KIE.ai API secret key.\n"
        "Get one at KIE.ai developer console."
    )
    agent_skills = ["ai-video-gen"]

    capabilities = ["text_to_video", "image_to_video"]
    supports = {
        "text_to_video": True,
        "image_to_video": True,
        "native_audio": True,
        "aspect_ratio": True,
    }

    input_schema = {
        "type": "object",
        "required": ["prompt"],
        "properties": {
            "prompt": {"type": "string"},
            "model": {
                "type": "string",
                "default": "grok-imagine/text-to-video",
                "description": "Model name on KIE.ai"
            },
            "duration": {"type": "integer", "default": 6},
            "ratio": {"type": "string", "default": "16:9"},
            "resolution": {"type": "string", "default": "720p"},
            "output_path": {"type": "string"}
        }
    }

    def get_status(self) -> ToolStatus:
        if os.environ.get("KIE_API_KEY"):
            return ToolStatus.AVAILABLE
        return ToolStatus.UNAVAILABLE

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        api_key = os.environ.get("KIE_API_KEY")
        if not api_key:
            return ToolResult(success=False, error="KIE_API_KEY not configured in .env")

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

        # Align aspect ratio format (KIE.ai expects e.g. "16:9" -> "16:9", or "2:3", etc.)
        # Note: If ratio is "16:9", KIE.ai accepts "16:9" or "16:9" equivalent.
        aspect_ratio = inputs.get("ratio", "16:9")

        # 1. Submit task using createTask endpoint
        submit_url = "https://api.kie.ai/api/v1/jobs/createTask"
        
        # Prepare the KIE.ai payload structure
        payload = {
            "model": inputs.get("model", "grok-imagine/text-to-video"),
            "input": {
                "prompt": inputs["prompt"],
                "aspect_ratio": aspect_ratio,
                "mode": "normal",
                "duration": str(inputs.get("duration", 6)),
                "resolution": inputs.get("resolution", "720p")
            }
        }

        try:
            print(f"Submitting task to KIE.ai (Model: {payload['model']})...")
            res = requests.post(submit_url, headers=headers, json=payload, timeout=30)
            res.raise_for_status()
            response_json = res.json()
            
            # Robust extraction of task ID from response
            # KIE.ai structure: {"code": 0, "message": "success", "data": {"taskId": "xxx"}} or direct {"taskId": "xxx"}
            task_id = None
            if "data" in response_json and isinstance(response_json["data"], dict):
                task_id = response_json["data"].get("taskId") or response_json["data"].get("id")
            if not task_id:
                task_id = response_json.get("taskId") or response_json.get("id")
                
            if not task_id:
                return ToolResult(
                    success=False, 
                    error=f"Failed to parse task ID from KIE.ai response: {response_json}"
                )
            
            print(f"Task submitted successfully. Task ID: {task_id}. Polling for results...")

            # 2. Polling KIE.ai recordInfo endpoint
            video_url = None
            max_attempts = 120  # Up to 10 minutes (5s interval)
            import json
            for attempt in range(max_attempts):
                time.sleep(5)
                # GET request to check status
                status_url = f"https://api.kie.ai/api/v1/jobs/recordInfo?taskId={task_id}"
                status_res = requests.get(status_url, headers=headers, timeout=15)
                status_res.raise_for_status()
                status_data = status_res.json()
                
                # Check status inside 'data'
                data_block = status_data.get("data") or {}
                state = str(data_block.get("state", "")).lower()
                
                if state in ("success", "succeeded", "completed", "done"):
                    # Extract resultJson which contains a list of resultUrls
                    result_json_str = data_block.get("resultJson")
                    if result_json_str:
                        try:
                            result_data = json.loads(result_json_str)
                            result_urls = result_data.get("resultUrls")
                            if result_urls and isinstance(result_urls, list) and len(result_urls) > 0:
                                video_url = result_urls[0]
                                break
                        except Exception as parse_err:
                            print(f"Error parsing resultJson: {parse_err}")
                    
                    # Fallback to direct videoUrl / url / result
                    video_url = data_block.get("videoUrl") or data_block.get("url") or data_block.get("result")
                    if video_url:
                        break
                elif state in ("failed", "error", "fail"):
                    error_msg = data_block.get("failMsg") or data_block.get("failCode") or "Unknown error"
                    return ToolResult(success=False, error=f"KIE.ai task failed ({state}): {error_msg}")
                
                # Print progress every 15 seconds
                if attempt % 3 == 0:
                    print(f"Polling task {task_id}... State: {state or 'PENDING'}")

            if not video_url:
                return ToolResult(success=False, error="KIE.ai video generation timed out.")

            print(f"Video ready at: {video_url}. Downloading...")

            # 3. Download output file
            output_path = Path(inputs.get("output_path", "kie_output.mp4"))
            output_path.parent.mkdir(parents=True, exist_ok=True)
            
            video_res = requests.get(video_url, timeout=120)
            video_res.raise_for_status()
            output_path.write_bytes(video_res.content)
            print(f"Downloaded video successfully to: {output_path}")

            return ToolResult(
                success=True,
                data={
                    "provider": "kie",
                    "model": inputs.get("model", "grok-imagine/text-to-video"),
                    "output_path": str(output_path),
                    "video_url": video_url,
                    "task_id": task_id
                },
                artifacts=[str(output_path)]
            )

        except Exception as e:
            return ToolResult(success=False, error=f"KIE.ai execution failed: {e}")
