"""OpenMontage pipeline runner — agent loop for chat-first video generation.

This module implements the orchestration that was missing from the web/MCP demo:
it runs the serial pipeline stages (script, scene_plan, assets, edit, compose)
using an LLM to generate artifacts and the existing tool registry to render.

It is intentionally simple and deterministic enough to be driven from the UI,
but all heavy work is delegated to:
  - the configured LLM provider for creative artifacts
  - tools.video.video_compose (and hyperframes_compose) for rendering
  - lib.checkpoint for state persistence

Run via web_server endpoints:
  POST /projects/{project_id}/chat
  POST /projects/{project_id}/generate
"""

from __future__ import annotations

import json
import logging
import os
from lib.env_loader import load_env

load_env()

import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Optional

from lib.checkpoint import get_next_stage, write_checkpoint
from lib.config_model import OpenMontageConfig
from lib.pipeline_loader import get_stage_order, load_pipeline
from tools.base_tool import ToolResult
from tools.tool_registry import registry
from tools.video.video_compose import VideoCompose

log = logging.getLogger("pipeline_runner")

ROOT_DIR = Path(__file__).resolve().parent.parent
PROJECTS_DIR = ROOT_DIR / "projects"
PIPELINE_DIR = ROOT_DIR / "pipelines"

# ---------------------------------------------------------------------------
# LLM client wrapper
# ---------------------------------------------------------------------------

_CONFIG: OpenMontageConfig | None = None


def _config() -> OpenMontageConfig:
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = OpenMontageConfig.load()
    return _CONFIG


def _get_llm_client() -> Any:
    """Return a provider-specific LLM client based on config.yaml / env."""
    cfg = _config().llm
    provider = cfg.provider.lower()
    if provider == "anthropic":
        try:
            import anthropic
        except ImportError as exc:
            raise RuntimeError("anthropic SDK not installed") from exc
        return anthropic.Anthropic()
    if provider in {"openai", "azure"}:
        try:
            import openai
        except ImportError as exc:
            raise RuntimeError("openai SDK not installed") from exc
        return openai.OpenAI()
    if provider in {"google", "gemini"}:
        try:
            import google.generativeai as genai
        except ImportError as exc:
            raise RuntimeError("google-generativeai SDK not installed") from exc
        genai.configure(api_key=os.environ.get("GOOGLE_API_KEY", os.environ.get("GEMINI_API_KEY")))
        return genai
    raise ValueError(f"Unsupported LLM provider: {cfg.provider}")


def _llm_model_name() -> str:
    cfg = _config().llm
    if cfg.model:
        return cfg.model
    provider = cfg.provider.lower()
    defaults = {
        "anthropic": "claude-3-5-sonnet-20240620",
        "openai": "gpt-4o",
        "azure": "gpt-4o",
        "google": "gemini-3.5-flash",
        "gemini": "gemini-3.5-flash",
    }
    return defaults.get(provider, "claude-3-5-sonnet-20240620")


def _probe_duration(path: str) -> Optional[float]:
    """Return the duration of a media file in seconds using ffprobe."""
    try:
        import subprocess
        proc = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode == 0:
            return float(proc.stdout.strip())
    except Exception:
        pass
    return None


def _load_json(path: Path) -> Optional[dict[str, Any]]:
    """Load a JSON file if it exists, otherwise return None."""
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _wrap_text(text: str, width: int, max_lines: int) -> str:
    """Simple word-wrap for FFmpeg drawtext (single-line input only)."""
    if not text:
        return ""
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        if len(current) + len(word) + 1 <= width:
            current = f"{current} {word}".strip()
        else:
            lines.append(current)
            current = word
            if len(lines) >= max_lines - 1:
                break
    if current and len(lines) < max_lines:
        lines.append(current)
    return "\n".join(lines)


def _find_font_file() -> Path:
    """Return the source path to a system font suitable for FFmpeg drawtext."""
    candidates = [
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/segoeui.ttf"),
        Path("C:/Windows/Fonts/calibri.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/System/Library/Fonts/Helvetica.ttc"),
    ]
    for cand in candidates:
        if cand.exists():
            return cand
    # Final fallback: try to use a Windows font even if it does not exist,
    # so the failure message is clear.
    return Path("C:/Windows/Fonts/arial.ttf")


def _prepare_font_file(temp_dir: Path) -> str:
    """Copy a system font into the temporary directory and return a short name.

    FFmpeg drawtext uses ':' as the option separator. Windows absolute font
    paths such as C:/Windows/Fonts/arial.ttf contain a colon which breaks the
    filter string, so we copy the font next to the other temp assets and refer
    to it with a colon-free relative filename.
    """
    src = _find_font_file()
    dst = temp_dir / "font.ttf"
    if not dst.exists():
        shutil.copy2(str(src), str(dst))
    return "font.ttf"


def _escape_text(text: str) -> str:
    """Escape FFmpeg drawtext special characters.

    FFmpeg drawtext uses '%' for formatting expansion and single quotes must
    be escaped. Newlines are rendered as literal line breaks in the filter
    expression, so they are escaped to \\n.
    """
    return (
        text.replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace("%", "%%")
        .replace("\n", "\\n")
    )


def _call_llm(
    messages: list[dict[str, str]],
    response_format: Optional[type] = None,
    temperature: Optional[float] = None,
    max_tokens: int = 4096,
) -> str:
    """Call the configured LLM and return raw text."""
    cfg = _config().llm
    client = _get_llm_client()
    provider = cfg.provider.lower()
    temp = temperature if temperature is not None else cfg.temperature

    if provider == "anthropic":
        system = ""
        user_messages = []
        for m in messages:
            if m["role"] == "system":
                system = m["content"]
            else:
                user_messages.append(m)
        response = client.messages.create(
            model=_llm_model_name(),
            max_tokens=max_tokens,
            temperature=temp,
            system=system,
            messages=user_messages,
        )
        return response.content[0].text

    if provider in {"openai", "azure"}:
        kwargs: dict[str, Any] = {
            "model": _llm_model_name(),
            "messages": messages,
            "temperature": temp,
            "max_tokens": max_tokens,
        }
        if response_format:
            kwargs["response_format"] = {"type": "json_object"}
        response = client.chat.completions.create(**kwargs)
        return response.choices[0].message.content or ""

    if provider in {"google", "gemini"}:
        system = ""
        history = []
        for m in messages:
            if m["role"] == "system":
                system = m["content"]
            else:
                history.append({"role": m["role"], "parts": [m["content"]]})
        model = client.GenerativeModel(
            model_name=_llm_model_name(),
            system_instruction=system,
        )
        chat = model.start_chat(history=history[:-1] if history else [])
        response = chat.send_message(history[-1]["parts"][0] if history else "")
        return response.text

    raise ValueError(f"Unsupported LLM provider: {cfg.provider}")


def _call_llm_json(
    messages: list[dict[str, str]],
    temperature: Optional[float] = None,
    max_tokens: int = 4096,
) -> dict[str, Any]:
    """Call LLM and parse JSON. Uses response_format when available."""
    cfg = _config().llm
    provider = cfg.provider.lower()
    supports_json_mode = provider in {"openai", "azure"}
    text = _call_llm(
        messages,
        response_format=dict if supports_json_mode else None,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM did not return valid JSON:\n{text[:500]}") from exc


# ---------------------------------------------------------------------------
# Project helpers
# ---------------------------------------------------------------------------

def _project_path(project_id: str) -> Path:
    return PROJECTS_DIR / project_id


def _is_safe_project_id(project_id: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9_-]+", project_id))


def _load_json(path: Path) -> Any:
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _artifact_path(project_id: str, name: str) -> Path:
    return _project_path(project_id) / "artifacts" / f"{name}.json"


def _load_artifact(project_id: str, name: str) -> Optional[dict[str, Any]]:
    return _load_json(_artifact_path(project_id, name))


def _save_artifact(project_id: str, name: str, data: dict[str, Any]) -> None:
    _save_json(_artifact_path(project_id, name), data)


def _update_checkpoint(
    project_id: str,
    stage: str,
    status: str,
    data: Optional[dict[str, Any]] = None,
) -> None:
    write_checkpoint(PIPELINE_DIR, project_id, stage, status, data or {})


# ---------------------------------------------------------------------------
# Artifact generators
# ---------------------------------------------------------------------------

_DEFAULT_BRIEF = "Tạo một video ngắn giới thiệu chủ đề do người dùng cung cấp."


def _system_prompt_for_json(schema_hint: str, task: str) -> str:
    return (
        "Bạn là OpenMontage Assistant — một agent sản xuất video chuyên nghiệp.\n"
        f"Nhiệm vụ: {task}\n"
        "Trả về KHÔNG gì khác ngoài một object JSON hợp lệ, không markdown, không giải thích.\n"
        f"Cấu trúc yêu cầu:\n{schema_hint}\n"
    )


def _generate_script(project_id: str, brief: str, duration: int, language: str) -> dict[str, Any]:
    system = _system_prompt_for_json(
        schema_hint=(
            '{"version":"1.0","title":"...","total_duration_seconds":<int>,'
            '"sections":[{"id":"s1","label":"...","text":"narration text",'
            '"start_seconds":0,"end_seconds":<int>,'
            '"enhancement_cues":[{"type":"overlay|broll","description":"...","timestamp_seconds":0}],'
            '"pronunciation_guides":[{"word":"...","phonetic":"..."]}]}'
        ),
        task="viết kịch bản lồng tiếng (narration script) cho video dựa trên Lịch sử Chat ý tưởng. Chỉ lấy những thông tin được thống nhất cuối cùng.",
    )
    messages = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": (
                f"Lịch sử Chat ý tưởng (Brief):\n{brief}\n\n"
                f"Ngôn ngữ: {language}\n"
                f"Thời lượng mục tiêu: {duration} giây\n"
                "Yêu cầu: Hãy tổng hợp ý tưởng cuối cùng mà khách hàng chốt trong lịch sử chat trên. "
                "Sau đó viết kịch bản gồm 3-5 section, mỗi section có text lồng tiếng rõ ràng, "
                "timestamp start/end liên tục, và một vài enhancement_cues phù hợp (overlay, broll, stat_card)."
            ),
        },
    ]
    script = _call_llm_json(messages, temperature=0.7)
    script.setdefault("version", "1.0")
    script.setdefault("title", brief[:60])
    _save_artifact(project_id, "script", script)
    return script


def _generate_scene_plan(
    project_id: str,
    script: dict[str, Any],
    brief: str,
    pipeline: str,
    renderer_family: str,
) -> dict[str, Any]:
    system = _system_prompt_for_json(
        schema_hint=(
            '{"version":"1.0","style_playbook":"...","scenes":['
            '{"id":"sc1","type":"text_card|broll|animation|generated",'
            '"description":"...","start_seconds":0,"end_seconds":5,"script_section_id":"s1",'
            '"framing":"...","movement":"...","transition_in":"fade","transition_out":"fade",'
            '"narrative_role":"establish_context|introduce_subject|build_tension|deliver_payload|transition|emotional_beat|evidence|comparison|resolution|call_to_action",'
            '"required_assets":[{"type":"image|video|audio",'
            '"description":"...","source":"generate"}]}]}'
        ),
        task="chuyển kịch bản thành scene plan với các cảnh quay và asset cần thiết",
    )
    sections = script.get("sections", [])
    messages = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": (
                f"Pipeline: {pipeline}\n"
                f"Renderer family: {renderer_family}\n"
                f"Lịch sử Chat ý tưởng (Brief):\n{brief}\n\n"
                f"Script sections: {json.dumps(sections, ensure_ascii=False)}\n"
                "Yêu cầu: Dựa vào thông tin chốt trong lịch sử chat và script, tạo scene plan với mỗi section ít nhất một scene. "
                "Mỗi scene phải có id, type, description, start/end, script_section_id, "
                "và required_assets mô tả asset cần generate hoặc source."
            ),
        },
    ]
    plan = _call_llm_json(messages, temperature=0.7)
    plan.setdefault("version", "1.0")
    plan.setdefault("style_playbook", "default")
    _save_artifact(project_id, "scene_plan", plan)
    return plan


def _generate_asset_manifest(
    project_id: str,
    scene_plan: dict[str, Any],
    script: dict[str, Any],
    brief: str,
    language: str,
) -> dict[str, Any]:
    """Generate a lightweight asset manifest.

    For a first-pass chat workflow we avoid expensive paid generation by:
      - creating a narration audio via the cheapest available TTS (or ffmpeg silence)
      - generating placeholder images via the configured image tool when possible
      - falling back to simple colored placeholder PNGs if image generation is absent
    """
    scenes = scene_plan.get("scenes", [])
    assets_dir = _project_path(project_id) / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    # 1. Narration audio: try openai_tts first, then ffmpeg silence.
    narration_text = " ".join(s.get("text", "") for s in script.get("sections", []))
    narration_path = assets_dir / "narration.mp3"
    _generate_narration_audio(narration_text, str(narration_path), language)

    # 2. Placeholder visuals for scenes that need them.
    assets: list[dict[str, Any]] = []
    narration_asset = {
        "id": "narration_01",
        "type": "narration",
        "path": str(narration_path.relative_to(_project_path(project_id)).as_posix()),
        "source_tool": "openai_tts_or_ffmpeg",
        "scene_id": "all",
        "duration_seconds": script.get("total_duration_seconds", 30),
        "format": "mp3",
    }
    assets.append(narration_asset)

    from tools.video.pexels_video import PexelsVideo
    
    for i, scene in enumerate(scenes):
        asset_type = "image"
        if scene.get("type") in {"broll", "generated"}:
            asset_type = "video"
        elif scene.get("type") in {"animation", "text_card"}:
            asset_type = "image"

        if asset_type == "video":
            video_path = assets_dir / f"scene_{i:03d}.mp4"
            query = scene.get("description", brief)[:50]
            # Gọi tool PexelsVideo để tải video
            tool = PexelsVideo()
            res = tool.execute({"query": query, "output_path": str(video_path), "orientation": "landscape"})
            if res.success:
                assets.append({
                    "id": f"asset_{i:03d}",
                    "type": "video",
                    "path": str(video_path.relative_to(_project_path(project_id)).as_posix()),
                    "source_tool": "pexels",
                    "scene_id": scene.get("id", f"sc{i}"),
                    "prompt": query,
                    "resolution": "1920x1080",
                    "format": "mp4",
                    "provider": "pexels",
                })
                continue
                
        # Fallback: Nếu không phải video hoặc tải video thất bại, sinh ảnh placeholder
        placeholder_path = assets_dir / f"scene_{i:03d}_image.png"
        _generate_placeholder_image(str(placeholder_path), scene.get("description", brief)[:80])
        assets.append({
            "id": f"asset_{i:03d}",
            "type": "image",
            "path": str(placeholder_path.relative_to(_project_path(project_id)).as_posix()),
            "source_tool": "placeholder",
            "scene_id": scene.get("id", f"sc{i}"),
            "prompt": scene.get("description", ""),
            "resolution": "1920x1080",
            "format": "png",
            "provider": "openmontage_placeholder",
        })

    manifest = {
        "version": "1.0",
        "assets": assets,
        "total_cost_usd": 0.0,
        "metadata": {"brief": brief, "language": language},
    }
    _save_artifact(project_id, "asset_manifest", manifest)
    return manifest


def _generate_narration_audio(text: str, output_path: str, language: str) -> None:
    """Best-effort narration audio. Falls back to ffmpeg silence on any failure."""
    
    # 1. Try LucyLab TTS first (Default voice for OpenMontage)
    if os.environ.get("LUCYLAB_API_KEY"):
        try:
            registry.ensure_discovered("tools")
            tool = registry.get_tool("lucylab_tts")
            if tool is not None:
                result = tool.execute({
                    "text": text,
                    "output_path": output_path,
                    "voice": "7Tb4dvGZyJMPjnnfxVBgik", # Lucylab default voice ID
                    "speed": 1.0,
                })
                if result.success:
                    return
        except Exception as exc:
            log.warning("lucylab_tts failed, falling back to openai_tts: %s", exc)

    # 2. Try OpenAI TTS if LucyLab fails or key is missing
    if os.environ.get("OPENAI_API_KEY"):
        try:
            registry.ensure_discovered("tools")
            tool = registry.get_tool("openai_tts")
            if tool is not None:
                result = tool.execute({
                    "operation": "synthesize",
                    "text": text,
                    "output_path": output_path,
                    "voice": "alloy",
                    "model": "tts-1",
                })
                if result.success:
                    return
        except Exception as exc:
            log.warning("openai_tts failed, falling back to silent audio: %s", exc)

    # Fallback: silent stereo mp3 via ffmpeg.
    duration = max(5, len(text.split()) // 2)
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"anullsrc=channel_layout=stereo:sample_rate=48000",
        "-t", str(duration),
        "-c:a", "libmp3lame", "-q:a", "4",
        output_path,
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)


def _generate_placeholder_image(output_path: str, label: str) -> None:
    """Generate a simple placeholder image with the scene description."""
    # Use image_gen tool if available and configured.
    if os.environ.get("OPENAI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        try:
            registry.ensure_discovered("tools")
            tool = registry.get_tool("image_gen")
            if tool is not None:
                result = tool.execute({
                    "operation": "generate",
                    "prompt": f"Cinematic wide shot, clean background, text-free, {label}",
                    "output_path": output_path,
                    "size": "1920x1080",
                })
                if result.success:
                    return
        except Exception as exc:
            log.warning("image_gen failed, using ffmpeg placeholder: %s", exc)

    # Fallback: colored gradient with text label via ffmpeg.
    safe_label = label.replace("'", "\\'")[:60]
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"gradient=s=1920x1080:r=30:start_color=#1a1a2e:end_color=#0f0f1e",
        "-vf", f"drawtext=text='{safe_label}':fontcolor=white:fontsize=48:x=(w-text_w)/2:y=(h-text_h)/2",
        "-frames:v", "1",
        output_path,
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)


def _generate_edit_decisions(
    project_id: str,
    script: dict[str, Any],
    scene_plan: dict[str, Any],
    asset_manifest: dict[str, Any],
    brief: str,
    render_runtime: str,
    renderer_family: str,
) -> dict[str, Any]:
    system = _system_prompt_for_json(
        schema_hint=(
            '{"version":"1.0","render_runtime":"remotion|hyperframes|ffmpeg",'
            '"renderer_family":"...","cuts":['
            '{"id":"c1","source":"asset_000","in_seconds":0,"out_seconds":5,"layer":"primary",'
            '"transform":{"scale":1,"position":"center","animation":"ken-burns-slow-zoom"},'
            '"transition_in":"fade","transition_out":"fade","reason":"..."}],'
            '"audio":{"narration":{"segments":[{"asset_id":"narration_01","start_seconds":0}]}},'
            '"subtitles":{"enabled":true,"style":"sentence","font":"Arial","font_size":24},'
            '"metadata":{}}'
        ),
        task="tạo edit_decisions từ scene plan và asset manifest để render video",
    )
    assets = asset_manifest.get("assets", [])
    scenes = scene_plan.get("scenes", [])
    messages = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": (
                f"Render runtime: {render_runtime}\n"
                f"Renderer family: {renderer_family}\n"
                f"Brief: {brief}\n"
                f"Scenes: {json.dumps(scenes, ensure_ascii=False)}\n"
                f"Assets: {json.dumps(assets, ensure_ascii=False)}\n"
                "Tạo edit_decisions với cuts[] mỗi scene một cut. "
                "Source của cut phải là asset_id (không phải path). "
                "Thêm audio.narration.segments sử dụng narration_01 từ đầu đến cuối. "
                "Bật subtitles sentence. Giữ render_runtime và renderer_family đúng như đầu vào."
            ),
        },
    ]
    edit = _call_llm_json(messages, temperature=0.5)
    edit.setdefault("version", "1.0")
    edit["render_runtime"] = render_runtime
    edit["renderer_family"] = renderer_family
    edit.setdefault("metadata", {})
    edit["metadata"]["title"] = script.get("title", brief[:60])
    _save_artifact(project_id, "edit_decisions", edit)
    return edit


# ---------------------------------------------------------------------------
# Composition / render
# ---------------------------------------------------------------------------

def _resolve_runtime(project_id: str, pipeline: str, requested: Optional[str]) -> str:
    """Pick the best available render runtime, respecting the requested value."""
    if requested and requested in {"remotion", "hyperframes", "ffmpeg"}:
        return requested

    registry.ensure_discovered("tools")
    info = VideoCompose().get_info()
    engines = info.get("render_engines", {})
    if engines.get("hyperframes"):
        return "hyperframes"
    if engines.get("remotion"):
        return "remotion"
    return "ffmpeg"


def _resolve_asset_path(project_id: str, asset_id: str, asset_manifest: dict[str, Any]) -> Optional[Path]:
    """Resolve an asset id to an absolute Path inside the project."""
    project_path = _project_path(project_id)
    for asset in asset_manifest.get("assets", []):
        if asset.get("id") == asset_id:
            rel = asset.get("path", "")
            if rel:
                return (project_path / rel).resolve()
    return None


def _is_image_path(path: Path) -> bool:
    return path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".tif"}


def _render_ffmpeg_slideshow(
    project_id: str,
    edit_decisions: dict[str, Any],
    asset_manifest: dict[str, Any],
    output_path: Path,
) -> Any:
    """Rich degraded fallback: create a content-bearing video with FFmpeg.

    Combines a dark animated gradient background, per-scene text overlays from
    the scene_plan/script, and the narration audio into a coherent MP4.
    """
    from tools.video.video_compose import VideoCompose

    project_path = _project_path(project_id)
    renders_dir = output_path.parent
    renders_dir.mkdir(parents=True, exist_ok=True)
    temp_dir = renders_dir / ".ffmpeg_fallback_tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)

    # Load scene plan / script for text overlays and subtitles
    scene_plan = _load_json(project_path / "artifacts" / "scene_plan.json") or {}
    script = _load_json(project_path / "artifacts" / "script.json") or {}
    scenes = scene_plan.get("scenes", []) or []
    sections = script.get("sections", []) or []
    total_duration = float(script.get("total_duration_seconds", 60))

    # Build one overlay card per scene
    overlay_cards: list[dict[str, Any]] = []
    for scene in scenes:
        sc_id = scene.get("id", "sc")
        start = float(scene.get("start_seconds", 0))
        end = float(scene.get("end_seconds", start + 5))
        title = ""
        desc = scene.get("description", "")
        sec_id = scene.get("script_section_id")
        if sec_id:
            for sec in sections:
                if sec.get("id") == sec_id:
                    title = sec.get("label", title)
                    body = sec.get("text", "")
                    if body:
                        desc = body
                    break
        overlay_cards.append({
            "id": sc_id,
            "start": start,
            "end": end,
            "title": title,
            "desc": _wrap_text(desc, 70, 2),
        })

    # Build subtitle cues from script sections
    subtitle_cues: list[dict[str, Any]] = []
    for sec in sections:
        start = float(sec.get("start_seconds", 0))
        end = float(sec.get("end_seconds", start + 5))
        subtitle_cues.append({
            "start": start,
            "end": end,
            "text": sec.get("text", ""),
        })

    # Generate a solid color background video (hyperframes style)
    bg_path = temp_dir / "bg_color.mp4"
    VideoCompose().run_command([
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"color=c=#0f172a:s=1920x1080:r=30:d={int(total_duration)}",
        "-t", str(total_duration),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "23", "-preset", "medium",
        str(bg_path),
    ])

    # Compose text overlays per scene on top of background
    segment_paths: list[Path] = []
    prev_end = 0.0
    for i, card in enumerate(overlay_cards):
        seg_start = max(card["start"], prev_end)
        seg_end = max(card["end"], seg_start + 1.0)
        seg_duration = seg_end - seg_start
        prev_end = seg_end

        font_file = _prepare_font_file(temp_dir)

        draw_filters = []
        if card["title"]:
            draw_filters.append(
                f"drawtext=fontfile='{font_file}':"
                f"text='{_escape_text(card['title'])}':"
                f"fontcolor=#00e0ff:fontsize=64:"
                f"x=(w-text_w)/2:y=(h-text_h)/2-80:"
                f"enable='between(t,0,{seg_duration})':"
                f"alpha='if(lt(t,0.5),t/0.5,if(lt(t,{seg_duration}-0.5),1,({seg_duration}-t)/0.5))'"
            )
        if card["desc"]:
            for line_idx, line in enumerate(card["desc"].split("\n")):
                if not line:
                    continue
                y_pos = f"(h-text_h)/2+{30 + line_idx * 70}"
                draw_filters.append(
                    f"drawtext=fontfile='{font_file}':"
                    f"text='{_escape_text(line)}':"
                    f"fontcolor=#e2e8f0:fontsize=38:"
                    f"x=(w-text_w)/2:y={y_pos}:"
                    f"enable='between(t,0,{seg_duration})':"
                    f"alpha='if(lt(t,0.5),t/0.5,if(lt(t,{seg_duration}-0.5),1,({seg_duration}-t)/0.5))'"
                )

        asset_path = _resolve_asset_path(project_id, card["id"], asset_manifest)
        seg_path = temp_dir / f"seg_{i:04d}.mp4"

        vf_parts = [f"fade=t=in:st=0:d=0.5,fade=t=out:st={seg_duration-0.5}:d=0.5"]
        vf_parts.extend(draw_filters)
        vf = ",".join(vf_parts)

        inputs = ["-i", str(bg_path)]
        if asset_path and asset_path.exists() and _is_image_path(asset_path):
            inputs.extend(["-loop", "1", "-i", str(asset_path)])
            overlay_vf = (
                "[1:v]scale=1920:1080:force_original_aspect_ratio=decrease,"
                "format=yuva420p,colorchannelmixer=aa=0.35[img];"
                "[0:v][img]overlay=(W-w)/2:(H-h)/2:enable='between(t,0,999)'[vbg];"
                "[vbg]" + vf + "[v]"
            )
            cmd = [
                "ffmpeg", "-y",
                "-ss", str(seg_start), "-t", str(seg_duration),
            ] + inputs + [
                "-filter_complex", overlay_vf,
                "-map", "[v]",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "23", "-preset", "medium",
                "-an", str(seg_path),
            ]
        else:
            cmd = [
                "ffmpeg", "-y", "-ss", str(seg_start), "-t", str(seg_duration),
                "-i", str(bg_path),
                "-vf", vf,
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "23", "-preset", "medium",
                "-an", str(seg_path),
            ]
        VideoCompose().run_command(cmd, cwd=temp_dir)
        segment_paths.append(seg_path)

    # Concatenate segments, add narration, burn-in subtitles globally
    concat_list = temp_dir / "concat.txt"
    with open(concat_list, "w", encoding="utf-8") as f:
        for seg in segment_paths:
            f.write(f"file '{seg.as_posix()}'\n")

    narration_path = _resolve_asset_path(project_id, "narration_01", asset_manifest)
    font_file = _prepare_font_file(temp_dir)

    sub_filters = []
    for cue in subtitle_cues:
        text = _wrap_text(cue["text"], 80, 2)
        for line_idx, line in enumerate(text.split("\n")):
            if not line:
                continue
            sub_filters.append(
                f"drawtext=fontfile='{font_file}':"
                f"text='{_escape_text(line)}':"
                f"fontcolor=#ffffff:fontsize=32:"
                f"box=1:boxcolor=#00000080:boxborderw=10:"
                f"x=(w-text_w)/2:y=h-{140 + line_idx * 45}:"
                f"enable='between(t,{cue['start']},{cue['end']})'"
            )

    base_cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(concat_list),
    ]
    if narration_path and narration_path.exists():
        base_cmd.extend(["-i", str(narration_path)])
        audio_map = ["-map", "0:v:0", "-map", "1:a:0"]
    else:
        audio_map = ["-map", "0:v:0"]

    vf = "setpts=PTS-STARTPTS"
    if sub_filters:
        vf = vf + "," + ",".join(sub_filters)

    base_cmd.extend(audio_map)
    base_cmd.extend([
        "-vf", vf, "-shortest", "-c:v", "libx264", "-c:a", "aac", "-b:a", "192k",
        "-pix_fmt", "yuv420p", "-crf", "23", "-preset", "medium", str(output_path),
    ])
    VideoCompose().run_command(base_cmd, cwd=temp_dir)

    if not output_path.exists():
        return ToolResult(success=False, error="FFmpeg slideshow output missing")

    duration_seconds = _probe_duration(str(output_path)) or 0.0

    # Cleanup temporary segments
    for f in temp_dir.glob("*"):
        try:
            if f.is_file():
                f.unlink()
        except Exception:
            pass
    try:
        temp_dir.rmdir()
    except Exception:
        pass

    return ToolResult(
        success=True,
        data={
            "operation": "ffmpeg_slideshow",
            "output": str(output_path),
            "cut_count": len(segment_paths),
            "has_narration": bool(narration_path and narration_path.exists()),
        },
        artifacts=[str(output_path)],
        duration_seconds=duration_seconds,
    )



def _run_compose(
    project_id: str,
    edit_decisions: dict[str, Any],
    asset_manifest: dict[str, Any],
) -> dict[str, Any]:
    """Render the final video using the configured runtime."""
    runtime = edit_decisions.get("render_runtime", "ffmpeg")
    project_path = _project_path(project_id)
    output_path = project_path / "renders" / "final.mp4"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Work on a copy so we can mutate render_runtime for fallback without
    # corrupting the persisted edit_decisions artifact.
    working_edit_decisions = dict(edit_decisions)

    def _attempt_render(render_runtime: str) -> Any:
        working_edit_decisions["render_runtime"] = render_runtime
        if render_runtime == "hyperframes":
            from tools.video.hyperframes_compose import HyperFramesCompose
            tool = HyperFramesCompose()
            return tool.execute({
                "operation": "render",
                "workspace_path": str(project_path / "hyperframes"),
                "output_path": str(output_path),
                "edit_decisions": working_edit_decisions,
                "asset_manifest": asset_manifest,
                "profile": "youtube_landscape",
                "fps": 30,
                "quality": "standard",
            })

        # For ffmpeg runtime, if any cut is a still image we cannot use the
        # regular VideoCompose compose path (it rejects images). Use the
        # degraded slideshow fallback instead.
        if render_runtime == "ffmpeg":
            has_image = False
            for cut in working_edit_decisions.get("cuts", []):
                src_path = _resolve_asset_path(project_id, cut.get("source", ""), asset_manifest)
                if src_path and _is_image_path(src_path):
                    has_image = True
                    break
            if has_image:
                return _render_ffmpeg_slideshow(
                    project_id,
                    working_edit_decisions,
                    asset_manifest,
                    output_path,
                )

        tool = VideoCompose()
        return tool.execute({
            "operation": "render",
            "output_path": str(output_path),
            "edit_decisions": working_edit_decisions,
            "asset_manifest": asset_manifest,
            "profile": "youtube_landscape",
        })

    start = time.time()
    result = _attempt_render(runtime)

    # Fallback chain: if the chosen engine failed, try ffmpeg-only as a
    # degraded but user-visible output. This keeps the end-to-end pipeline
    # functional on machines where Remotion/HyperFrames cannot run.
    fallback_runtimes: list[str] = []
    if runtime == "remotion":
        fallback_runtimes = ["ffmpeg"]
    elif runtime == "hyperframes":
        fallback_runtimes = ["remotion", "ffmpeg"]
    elif runtime != "ffmpeg":
        fallback_runtimes = ["ffmpeg"]

    used_runtime = runtime
    fallback_error: Optional[str] = None
    if not result.success and fallback_runtimes:
        fallback_error = result.error
        for fb in fallback_runtimes:
            log.warning(
                "Render with %s failed for %s; falling back to %s. Original error: %s",
                runtime, project_id, fb, fallback_error
            )
            used_runtime = fb
            result = _attempt_render(fb)
            if result.success:
                break
        
        if not result.success and fallback_error:
            result.error = f"Original {runtime} error: {fallback_error}\n\nFallback {used_runtime} error: {result.error}"

    render_time = round(time.time() - start, 2)
    file_size: Optional[int] = None
    duration: Optional[float] = None
    if result.success and output_path.exists():
        try:
            file_size = output_path.stat().st_size
            duration = _probe_duration(str(output_path))
        except Exception:
            pass

    outputs = []
    if output_path.exists():
        outputs.append({
            "path": str(output_path),
            "format": "mp4",
            "codec": "libx264",
            "audio_codec": "aac",
            "resolution": "1920x1080",
            "fps": 30,
            "duration_seconds": duration or 0.0,
            "file_size_bytes": file_size,
            "platform_target": "youtube_landscape",
        })
    else:
        # Schema still requires at least one output; keep path with zero
        # duration when render failed so validation does not block the
        # checkpoint from being written.
        outputs.append({
            "path": str(output_path),
            "format": "mp4",
            "resolution": "1920x1080",
            "duration_seconds": 0.0,
        })

    render_report = {
        "version": "1.0",
        "outputs": outputs,
        "render_time_seconds": render_time,
        "warnings": [],
        "verification_notes": [
            f"Requested runtime: {runtime}",
            f"Used runtime: {used_runtime}",
        ],
        "render_grammar": edit_decisions.get("renderer_family", "explainer-data"),
        "metadata": {
            "render_runtime": used_runtime,
            "requested_runtime": runtime,
            "output_path": str(output_path),
            "success": result.success,
            "error": result.error or fallback_error,
            "file_exists": output_path.exists(),
        },
    }
    if not result.success:
        render_report["warnings"].append(result.error or fallback_error or "Render failed")
    _save_artifact(project_id, "render_report", render_report)
    return render_report


# ---------------------------------------------------------------------------
# Public orchestration
# ---------------------------------------------------------------------------

ProgressCallback = Callable[[str, str, Optional[dict[str, Any]]], None]


def _noop_progress(stage: str, status: str, data: Optional[dict[str, Any]] = None) -> None:
    pass


def run_pipeline(
    project_id: str,
    brief: str,
    pipeline: str,
    duration_seconds: int = 60,
    language: str = "vi",
    render_runtime: Optional[str] = None,
    auto_approve: bool = True,
    progress_callback: ProgressCallback = _noop_progress,
) -> dict[str, Any]:
    """Run the full pipeline from brief → final video.

    Returns a summary dict with the final status, video path, and any error.
    """
    if not _is_safe_project_id(project_id):
        raise ValueError(f"Invalid project_id: {project_id!r}")

    project_path = _project_path(project_id)
    if not project_path.exists():
        raise FileNotFoundError(f"Project {project_id!r} not found")

    metadata = _load_json(project_path / "project.json") or {}
    pipeline = pipeline or metadata.get("pipeline", "animated-explainer")
    manifest = load_pipeline(pipeline)
    stage_order = get_stage_order(manifest)
    renderer_family = manifest.get("renderer_family", "explainer-data")

    runtime = _resolve_runtime(project_id, pipeline, render_runtime)
    progress_callback("proposal", "running", {"render_runtime": runtime, "renderer_family": renderer_family})

    # Proposal / brief stage (creative direction locked via LLM is implicit)
    _save_artifact(project_id, "brief", {"version": "1.0", "text": brief, "language": language})
    proposal = {
        "version": "1.0",
        "concept_options": [
            {
                "id": "c1",
                "title": f"{brief[:40]} — direct explainer",
                "hook": f"Ever wondered about {brief[:30]}?",
                "narrative_structure": "problem_solution",
                "visual_approach": "clean motion graphics with data callouts",
                "target_duration_seconds": duration_seconds,
                "why_this_works": "Directly answers the user's brief.",
                "target_platform": "youtube",
                "key_points": ["Hook", "Core explanation", "Supporting evidence", "Call to action"],
                "core_message": brief[:50],
                "cta": "Learn more",
                "tone": "clean-professional"
            },
            {
                "id": "c2",
                "title": f"{brief[:40]} — story-driven explainer",
                "hook": f"Let me tell you a quick story about {brief[:30]}.",
                "narrative_structure": "story",
                "visual_approach": "character-led scenes with kinetic typography",
                "target_duration_seconds": duration_seconds,
                "why_this_works": "Emotional narrative improves retention.",
                "target_platform": "youtube",
                "key_points": ["Character hook", "Rising action", "Climax", "Resolution"],
                "core_message": brief[:50],
                "cta": "Subscribe for more stories",
                "tone": "clean-professional"
            },
            {
                "id": "c3",
                "title": f"{brief[:40]} — comparison explainer",
                "hook": f"What's the real difference behind {brief[:30]}?",
                "narrative_structure": "comparison",
                "visual_approach": "split-screen comparison with animated diagrams",
                "target_duration_seconds": duration_seconds,
                "why_this_works": "Comparison structures make abstract concepts concrete.",
                "target_platform": "youtube",
                "key_points": ["Side A", "Side B", "Key contrast", "Takeaway"],
                "core_message": brief[:50],
                "cta": "Watch the next video",
                "tone": "clean-professional"
            }
        ],
        "selected_concept": {
            "concept_id": "c1",
            "rationale": "Best alignment with the user's brief and target duration."
        },
        "production_plan": {
            "pipeline": pipeline,
            "render_runtime": runtime,
            "stages": [
                {
                    "stage": stage,
                    "tools": [{"tool_name": "video_compose", "role": "assemble and render the final video", "available": True}],
                    "approach": f"Execute {stage} stage according to the pipeline manifest."
                }
                for stage in stage_order
            ],
            "delivery_promise": {
                "promise_type": "motion_led",
                "motion_required": True,
                "source_required": False,
                "tone_mode": "educational",
                "quality_floor": "presentable",
                "approved_fallback": None
            },
            "renderer_family": renderer_family
        },
        "cost_estimate": {
            "total_estimated_usd": 0.0,
            "line_items": [
                {
                    "tool": "video_compose",
                    "operation": "local composition and render",
                    "quantity": 1,
                    "estimated_usd": 0.0,
                    "notes": "Demo plan; real costs depend on provider usage."
                }
            ],
            "budget_verdict": "no_budget_set"
        },
        "approval": {
            "status": "approved" if auto_approve else "pending"
        }
    }
    _save_artifact(project_id, "proposal_packet", proposal)
    _update_checkpoint(project_id, "proposal", "completed" if auto_approve else "awaiting_human", {"proposal_packet": proposal})
    progress_callback("proposal", "completed", {"proposal_packet": proposal})

    if not auto_approve:
        return {
            "status": "awaiting_approval",
            "stage": "proposal",
            "message": "Kế hoạch đã sẵn sàng. Hãy phê duyệt để tiếp tục.",
        }

    # Script (reuse cached artifact when available to avoid redundant LLM calls)
    progress_callback("script", "running")
    script = _load_artifact(project_id, "script")
    if script is None:
        script = _generate_script(project_id, brief, duration_seconds, language)
    _update_checkpoint(project_id, "script", "completed", {"script": script})
    progress_callback("script", "completed", {"script": script})

    # Scene plan
    progress_callback("scene_plan", "running")
    scene_plan = _load_artifact(project_id, "scene_plan")
    if scene_plan is None:
        scene_plan = _generate_scene_plan(project_id, script, brief, pipeline, renderer_family)
    _update_checkpoint(project_id, "scene_plan", "completed", {"scene_plan": scene_plan})
    progress_callback("scene_plan", "completed", {"scene_plan": scene_plan})

    # Assets
    progress_callback("assets", "running")
    asset_manifest = _load_artifact(project_id, "asset_manifest")
    if asset_manifest is None:
        asset_manifest = _generate_asset_manifest(project_id, scene_plan, script, brief, language)
    _update_checkpoint(project_id, "assets", "completed", {"asset_manifest": asset_manifest})
    progress_callback("assets", "completed", {"asset_manifest": asset_manifest})

    # Edit decisions
    progress_callback("edit", "running")
    edit_decisions = _load_artifact(project_id, "edit_decisions")
    if edit_decisions is None:
        edit_decisions = _generate_edit_decisions(
            project_id, script, scene_plan, asset_manifest, brief, runtime, renderer_family
        )
    _update_checkpoint(project_id, "edit", "completed", {"edit_decisions": edit_decisions})
    progress_callback("edit", "completed", {"edit_decisions": edit_decisions})

    # Compose / render
    progress_callback("compose", "running")
    render_report = _run_compose(project_id, edit_decisions, asset_manifest)
    render_success = render_report.get("metadata", {}).get("success", False)
    _update_checkpoint(
        project_id,
        "compose",
        "completed" if render_success else "failed",
        {"render_report": render_report},
    )
    progress_callback("compose", "completed" if render_success else "failed", {"render_report": render_report})

    return {
        "project_id": project_id,
        "status": "completed" if render_success else "failed",
        "stage": "compose",
        "render_runtime": runtime,
        "renderer_family": renderer_family,
        "video_path": render_report.get("metadata", {}).get("output_path"),
        "error": render_report.get("metadata", {}).get("error"),
        "completed_stages": stage_order,
    }


def chat_message(
    project_id: str,
    message: str,
    history: Optional[list[dict[str, str]]] = None,
) -> dict[str, Any]:
    """Handle a chat message and optionally detect a generation intent.

    Returns a dict with:
      - reply: assistant text to show
      - intent: one of 'chat', 'generate', 'approve'
      - args: optional args extracted for generate/approve
    """
    if not _is_safe_project_id(project_id):
        raise ValueError(f"Invalid project_id: {project_id!r}")

    history = history or []
    lower = message.lower()

    # Bỏ qua hardcode triggers để LLM thực sự có cơ hội trò chuyện và gợi ý.
    # Người dùng sẽ dùng nút Generate trên UI sau khi chốt ý tưởng.

    # General chat: answer with LLM
    system = (
        "Bạn là Đạo diễn Video AI cao cấp của nền tảng OpenMontage, một trợ lý thông minh như một IDE agent dành cho sáng tạo nội dung. "
        "NHIỆM VỤ CỦA BẠN: KHÔNG bao giờ đồng ý làm video ngay với một câu lệnh ngắn gọn. Bạn phải dẫn dắt người dùng để khai thác tối đa tiềm năng của video. "
        "1. KHAI THÁC & ĐỀ XUẤT (Proactive Suggestion): Dựa vào ý tưởng cơ bản, hãy đề xuất ngay 1-2 hướng phát triển (ví dụ: phong cách kể chuyện (narrative), nhịp độ video (fast-paced vs chill), mood (cảm xúc). "
        "2. TƯ VẤN CÔNG NGHỆ: Giới thiệu 2 luồng công nghệ của OpenMontage một cách tự nhiên: "
        "   - 'Animated Explainer' (Remotion/Motion Graphics): Nếu chủ đề cần truyền đạt kiến thức, số liệu, hướng dẫn, đồ họa UI. "
        "   - 'Cinematic' (Pexels/Stock Footage): Nếu chủ đề cần cảm xúc, du lịch, phong cảnh, trải nghiệm thực tế. "
        "3. TƯ VẤN ÂM THANH: Đề xuất kiểu nhạc nền (BGM) và giọng đọc (Voice AI) phù hợp với mood của video. "
        "Luôn trả lời bằng tiếng Việt, phong cách thông minh, sắc bén, chuyên nghiệp và đầy tính gợi mở (tương tự một chuyên gia Creative Director). "
        "Khi đã chốt xong mọi thứ, hãy nhắc người dùng chọn Pipeline phù hợp trên giao diện và nhấn nút 'Tạo video ngay' (Generate)."
    )
    messages = [{"role": "system", "content": system}]
    for h in history[-6:]:
        messages.append({"role": h.get("role", "user"), "content": h.get("content", "")})
    messages.append({"role": "user", "content": message})
    reply = _call_llm(messages, temperature=0.7, max_tokens=1024)
    return {"reply": reply, "intent": "chat"}


def approve_and_run(
    project_id: str,
    brief: str,
    pipeline: str,
    duration_seconds: int = 60,
    language: str = "vi",
    render_runtime: Optional[str] = None,
    progress_callback: ProgressCallback = _noop_progress,
) -> dict[str, Any]:
    """Approve the current checkpoint (if any) and run the rest of the pipeline."""
    return run_pipeline(
        project_id=project_id,
        brief=brief,
        pipeline=pipeline,
        duration_seconds=duration_seconds,
        language=language,
        render_runtime=render_runtime,
        auto_approve=True,
        progress_callback=progress_callback,
    )

