"""LucyLab text-to-speech provider tool."""

from __future__ import annotations

import os
import time
import requests
from pathlib import Path
from typing import Any

from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    RetryPolicy,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolStatus,
    ToolTier,
)


class LucyLabTTS(BaseTool):
    name = "lucylab_tts"
    version = "0.1.0"
    tier = ToolTier.VOICE
    capability = "tts"
    provider = "lucylab"
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.ASYNC
    determinism = Determinism.DETERMINISTIC
    runtime = ToolRuntime.API

    dependencies = ["requests"]
    install_instructions = (
        "Set the LUCYLAB_API_KEY environment variable:\n"
        "  export LUCYLAB_API_KEY=your_key_here\n"
        "Get a key at https://lucylab.io"
    )
    fallback = "piper_tts"
    fallback_tools = ["piper_tts"]
    agent_skills = ["lucylab-docs"]

    capabilities = [
        "text_to_speech",
        "voice_selection",
    ]
    supports = {
        "voice_cloning": True,
        "multilingual": True,
        "offline": False,
        "native_audio": True,
    }
    best_for = [
        "high quality vietnamese voices",
    ]
    not_good_for = [
        "fully offline production",
    ]

    input_schema = {
        "type": "object",
        "required": ["text", "voice"],
        "properties": {
            "text": {"type": "string"},
            "voice": {
                "type": "string",
                "description": "LucyLab voice ID (e.g. YOUR_VOICE_ID)",
            },
            "speed": {
                "type": "number",
                "default": 1.0,
                "description": "Voice speed multiplier",
            },
            "output_path": {"type": "string"},
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=256, vram_mb=0, disk_mb=50, network_required=True
    )
    retry_policy = RetryPolicy(max_retries=2, retryable_errors=["rate_limit", "timeout"])
    idempotency_key_fields = ["text", "voice", "speed"]
    side_effects = ["writes audio file to output_path", "calls LucyLab API"]
    user_visible_verification = ["Listen to generated audio for intelligibility and tone"]

    def get_status(self) -> ToolStatus:
        if os.environ.get("LUCYLAB_API_KEY"):
            return ToolStatus.AVAILABLE
        return ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        # Giả định chi phí, tuỳ API của Lucylab
        return round(len(inputs.get("text", "")) * 0.00002, 4)

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        api_key = os.environ.get("LUCYLAB_API_KEY")
        if not api_key:
            return ToolResult(success=False, error="No LucyLab API key. " + self.install_instructions)

        start = time.time()
        try:
            result = self._generate(inputs, api_key)
        except Exception as exc:
            return ToolResult(success=False, error=f"LucyLab TTS failed: {exc}")

        result.duration_seconds = round(time.time() - start, 2)
        result.cost_usd = self.estimate_cost(inputs)
        return result

    def _generate(self, inputs: dict[str, Any], api_key: str) -> ToolResult:
        try:
            from tools.analysis.audio_probe import probe_duration
        except ImportError:
            probe_duration = lambda path: None
            
        text = inputs["text"]
        voice = inputs["voice"]
        speed = inputs.get("speed", 1.0)
        output_path = Path(inputs.get("output_path", "lucylab_tts.wav"))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        # Bước 1: Tạo TTS job
        payload_create = {
            "method": "ttsLongText",
            "input": {
                "text": text,
                "userVoiceId": voice,
                "speed": speed
            }
        }
        
        resp = requests.post("https://api.lucylab.io/json-rpc", headers=headers, json=payload_create, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        
        if "error" in data:
            raise Exception(f"API Error: {data['error']}")
            
        export_id = data.get("result", {}).get("projectExportId")
        if not export_id:
            raise Exception("Failed to get projectExportId from LucyLab API")
            
        # Bước 2: Polling
        audio_url = None
        max_attempts = 60
        for _ in range(max_attempts):
            payload_status = {
                "method": "getExportStatus",
                "input": {"projectExportId": export_id}
            }
            status_resp = requests.post("https://api.lucylab.io/json-rpc", headers=headers, json=payload_status, timeout=10)
            status_resp.raise_for_status()
            status_data = status_resp.json()
            
            if "error" in status_data:
                raise Exception(f"Status API Error: {status_data['error']}")
                
            state = status_data.get("result", {}).get("state")
            if state == "completed":
                audio_url = status_data.get("result", {}).get("url")
                break
            elif state == "failed":
                raise Exception("LucyLab TTS job failed on the server side.")
                
            time.sleep(2.0)
            
        if not audio_url:
            raise Exception(f"Polling timed out after {max_attempts * 2} seconds")
            
        # Bước 3: Download file
        audio_resp = requests.get(audio_url, timeout=30)
        audio_resp.raise_for_status()
        
        with open(output_path, "wb") as f:
            f.write(audio_resp.content)
            
        audio_duration = probe_duration(output_path)
        
        return ToolResult(
            success=True,
            data={
                "provider": self.provider,
                "model": "lucylab",
                "voice": voice,
                "text_length": len(text),
                "audio_duration_seconds": round(audio_duration, 2) if audio_duration else None,
                "output": str(output_path),
            },
            artifacts=[str(output_path)],
            model="lucylab",
        )
