from __future__ import annotations

import base64
import hashlib
import html
import importlib.util
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import Lock, Thread
from typing import Any

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from openai import OpenAI
from pydantic import BaseModel, Field

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data")).resolve()
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "library.json"
JOBS_PATH = DATA_DIR / "jobs.json"
MAX_DIRECT_TRANSCRIBE_BYTES = 25 * 1024 * 1024
TEXT_EXTENSIONS = {".txt", ".srt", ".vtt", ".md"}
MEDIA_EXTENSIONS = {".mp3", ".mp4", ".mpeg", ".mpga", ".m4a", ".wav", ".webm"}
TEMP_MEDIA_EXTENSIONS = MEDIA_EXTENSIONS | {".mkv", ".flv", ".ts", ".opus", ".aac", ".ogg"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
_LOCAL_WHISPER_MODELS: dict[tuple[str, str, str, str], Any] = {}
_OCR_ENGINE: Any = None
_DB_LOCK = Lock()
_JOBS_LOCK = Lock()
_IMPORT_LOCK = Lock()

AGENTS = {
    "cet4": {
        "name": "四级阅读与写作助教",
        "exam": "大学英语四级（CET-4）",
        "focus": "阅读定位、词义判断、段落主旨、写作提纲、基础衔接与准确表达",
        "level": "用清晰、分步的中文解释，阅读材料与写作例句控制在四级常见范围。",
    },
    "cet6": {
        "name": "六级阅读与写作助教",
        "exam": "大学英语六级（CET-6）",
        "focus": "长篇阅读的信息筛选、论证结构、写作立意、段落展开、语域和精确用词",
        "level": "保留逻辑链与语域差异，帮助学生从正确提升到准确、自然、有层次。",
    },
    "postgrad": {
        "name": "考研阅读与写作助教",
        "exam": "考研英语",
        "focus": "考研阅读的论证结构、态度题、推断题、长难句分析与议论文写作",
        "level": "强调证据链、干扰项识别、观点展开和严密的英文表达。",
    },
}


def validate_agent(agent: str) -> str:
    if agent not in AGENTS:
        raise HTTPException(400, "请选择四级、六级或考研智能体。")
    return agent


def ensure_storage() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "tmp").mkdir(parents=True, exist_ok=True)
    if not DB_PATH.exists():
        DB_PATH.write_text(json.dumps({"sources": [], "profiles": {key: default_profile() for key in AGENTS}, "crawl_queue": []}, ensure_ascii=False, indent=2), encoding="utf-8")
    if not JOBS_PATH.exists():
        JOBS_PATH.write_text(json.dumps({"items": []}, ensure_ascii=False, indent=2), encoding="utf-8")


def cleanup_stale_temp_files() -> None:
    temp_root = DATA_DIR / "tmp"
    temp_root.mkdir(parents=True, exist_ok=True)
    active_job_ids: set[str] = set()
    if JOBS_PATH.exists():
        try:
            active_job_ids = {
                item["id"]
                for item in json.loads(JOBS_PATH.read_text(encoding="utf-8")).get("items", [])
                if item.get("status") in {"queued", "running"}
            }
        except (json.JSONDecodeError, KeyError):
            active_job_ids = set()
    for entry in temp_root.iterdir():
        if entry.name in active_job_ids:
            continue
        if entry.is_dir():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            entry.unlink(missing_ok=True)


def default_profile() -> dict[str, Any]:
    return {
        "teaching_principles": ["先看任务再读原文", "每个结论都要回到句子证据", "写作先搭结构再润色语言"],
        "answer_sequence": ["识别题型", "定位证据", "排除干扰", "整理表达", "安排针对性练习"],
        "reading_methods": ["题干关键词定位", "段落主旨与逻辑关系优先", "用原文证据核验选项"],
        "writing_methods": ["先确定中心论点", "用主题句组织段落", "控制衔接词密度并检查语法准确"],
        "correction_style": "先保留有效信息，再指出一个最关键的问题，并给可执行的修改方法。",
        "signature": "课程资料尚不足；当前使用通用阅读与写作教学框架。",
    }


def load_db() -> dict[str, Any]:
    ensure_storage()
    db = json.loads(DB_PATH.read_text(encoding="utf-8"))
    if not isinstance(db.get("profiles"), dict):
        legacy = db.pop("profile", default_profile())
        db["profiles"] = {key: legacy for key in AGENTS}
    for key in AGENTS:
        db["profiles"].setdefault(key, default_profile())
    return db


def save_db(data: dict[str, Any]) -> None:
    with _DB_LOCK:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        temp = DB_PATH.with_suffix(".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(DB_PATH)


def load_jobs() -> dict[str, Any]:
    ensure_storage()
    with _JOBS_LOCK:
        return json.loads(JOBS_PATH.read_text(encoding="utf-8"))


def save_jobs(data: dict[str, Any]) -> None:
    with _JOBS_LOCK:
        temp = JOBS_PATH.with_suffix(".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(JOBS_PATH)


def update_job(job_id: str, **changes: Any) -> dict[str, Any]:
    with _JOBS_LOCK:
        data = json.loads(JOBS_PATH.read_text(encoding="utf-8")) if JOBS_PATH.exists() else {"items": []}
        job = next((item for item in data["items"] if item["id"] == job_id), None)
        if job is None:
            raise KeyError(job_id)
        job.update(changes)
        job["updated_at"] = int(time.time())
        temp = JOBS_PATH.with_suffix(".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(JOBS_PATH)
        return dict(job)


def create_job(job: dict[str, Any]) -> None:
    with _JOBS_LOCK:
        data = json.loads(JOBS_PATH.read_text(encoding="utf-8")) if JOBS_PATH.exists() else {"items": []}
        data["items"].insert(0, job)
        temp = JOBS_PATH.with_suffix(".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(JOBS_PATH)


def now_id() -> str:
    return f"src_{int(time.time() * 1000)}_{secrets.token_hex(3)}"


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def parse_timestamp(value: str) -> float | None:
    value = value.strip().replace(",", ".")
    parts = value.split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
    except ValueError:
        return None
    return None


def parse_subtitles(content: str, suffix: str) -> list[dict[str, Any]]:
    """Parse common SRT/VTT timing blocks and gracefully fall back to paragraphs."""
    content = content.replace("\ufeff", "")
    if suffix == ".vtt":
        content = re.sub(r"^WEBVTT[^\n]*\n", "", content, flags=re.I)
    blocks = re.split(r"\n\s*\n", content.replace("\r\n", "\n"))
    chunks: list[dict[str, Any]] = []
    pattern = re.compile(r"(?P<start>\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}|\d{1,2}:\d{2}[,.]\d{1,3})\s*-->\s*(?P<end>\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}|\d{1,2}:\d{2}[,.]\d{1,3})")
    for block in blocks:
        match = pattern.search(block)
        lines = [line.strip() for line in block.splitlines()]
        if match:
            body = clean_text(" ".join(line for line in lines if "-->" not in line and not line.isdigit()))
            if body:
                chunks.append({"text": body, "start": parse_timestamp(match.group("start")), "end": parse_timestamp(match.group("end"))})
    if chunks:
        return merge_small_chunks(chunks)
    paragraphs = [clean_text(x) for x in re.split(r"\n{2,}", content) if clean_text(x)]
    return [{"text": p, "start": None, "end": None} for p in paragraphs]


def merge_small_chunks(chunks: list[dict[str, Any]], minimum: int = 180) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    pending: dict[str, Any] | None = None
    for chunk in chunks:
        if pending is None:
            pending = dict(chunk)
        else:
            pending["text"] += " " + chunk["text"]
            pending["end"] = chunk["end"]
        if len(pending["text"]) >= minimum:
            merged.append(pending)
            pending = None
    if pending:
        if merged and len(pending["text"]) < minimum:
            merged[-1]["text"] += " " + pending["text"]
            merged[-1]["end"] = pending["end"]
        else:
            merged.append(pending)
    return merged


def seconds_label(value: float | None) -> str | None:
    if value is None:
        return None
    total = int(value)
    return f"{total // 3600:02}:{(total % 3600) // 60:02}:{total % 60:02}"


def keyword_score(question: str, text: str) -> int:
    english_words = [word.lower() for word in re.findall(r"[A-Za-z]{2,}", question)]
    chinese_runs = re.findall(r"[\u4e00-\u9fff]{2,}", question)
    chinese_tokens: list[str] = []
    for run in chinese_runs:
        for width in (2, 3, 4):
            chinese_tokens.extend(run[index:index + width] for index in range(len(run) - width + 1))
    words = set(english_words + chinese_tokens)
    lowered = text.lower()
    return sum(lowered.count(word) * max(1, len(word) // 2) for word in words)


def retrieve(question: str, agent: str, limit: int = 5) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for source in load_db().get("sources", []):
        if source.get("agent", "shared") not in {"shared", agent}:
            continue
        for chunk in source.get("chunks", []):
            score = keyword_score(question, chunk["text"])
            if score:
                candidates.append({**chunk, "score": score, "source_id": source["id"], "source_title": source["title"]})
    return sorted(candidates, key=lambda x: x["score"], reverse=True)[:limit]


def client() -> OpenAI | None:
    return OpenAI() if os.getenv("OPENAI_API_KEY") else None


def ollama_ready() -> bool:
    base_url = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
    try:
        urllib.request.urlopen(f"{base_url}/api/tags", timeout=0.8).read(1)
        return True
    except Exception:
        return False


def text_generation_provider() -> str | None:
    configured = os.getenv("LLM_PROVIDER", "auto").strip().lower()
    if configured in {"compatible", "openai-compatible"}:
        ready = all(
            os.getenv(name)
            for name in ("COMPATIBLE_BASE_URL", "COMPATIBLE_API_KEY", "COMPATIBLE_MODEL")
        )
        return "compatible" if ready else None
    if configured == "ollama":
        return "ollama" if ollama_ready() else None
    if configured == "openai":
        return "openai" if os.getenv("OPENAI_API_KEY") else None
    if ollama_ready():
        return "ollama"
    if os.getenv("OPENAI_API_KEY"):
        return "openai"
    return None


def generate_text(instructions: str, prompt: str, json_mode: bool = False) -> str:
    provider = text_generation_provider()
    if provider == "ollama":
        base_url = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
        if not base_url.endswith("/v1"):
            base_url += "/v1"
        local = OpenAI(base_url=base_url, api_key="ollama")
        request: dict[str, Any] = {
            "model": os.getenv("OLLAMA_MODEL", "qwen3:8b"),
            "messages": [
                {"role": "system", "content": instructions},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.45,
        }
        if json_mode:
            request["response_format"] = {"type": "json_object"}
        response = local.chat.completions.create(
            **request
        )
        return response.choices[0].message.content or ""
    if provider == "compatible":
        base_url = os.environ["COMPATIBLE_BASE_URL"].rstrip("/")
        if not base_url.endswith("/v1"):
            base_url += "/v1"
        remote = OpenAI(base_url=base_url, api_key=os.environ["COMPATIBLE_API_KEY"])
        request = {
            "model": os.environ["COMPATIBLE_MODEL"],
            "messages": [
                {"role": "system", "content": instructions},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.45,
        }
        if json_mode:
            request["response_format"] = {"type": "json_object"}
        response = remote.chat.completions.create(
            **request
        )
        return response.choices[0].message.content or ""
    if provider == "openai":
        api = client()
        response = api.responses.create(
            model=os.getenv("OPENAI_TEXT_MODEL", "gpt-6-astra"),
            instructions=instructions,
            input=prompt,
            store=False,
        )
        return response.output_text
    raise RuntimeError("没有可用的文本生成模型。")


def parse_json_object(value: str) -> dict[str, Any] | None:
    cleaned = re.sub(r"<think>[\s\S]*?</think>", "", value, flags=re.IGNORECASE)
    cleaned = cleaned.strip().removeprefix("```json").removeprefix("```").strip()
    start = cleaned.find("{")
    if start < 0:
        return None
    try:
        parsed, _ = json.JSONDecoder().raw_decode(cleaned[start:])
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def profile_from_text(value: str) -> dict[str, Any]:
    base = default_profile()
    lines: list[str] = []
    meta_terms = (
        "最终结论",
        "材料中",
        "涉及语法",
        "笔误",
        "需注意材料",
        "分析说明",
        "返回",
        "JSON",
        "模型",
        "转写",
    )
    fragments = re.split(r"(?<=[。！？!?；;])|\n+", value.replace("\r", ""))
    for fragment in fragments:
        for raw_line in re.split(r"[，,]", fragment):
            line = re.sub(r"^[\s#>*\-\d.、（）()]+", "", raw_line).strip()
            line = re.sub(r"\s+", " ", line)
            if (
                6 <= len(line) <= 180
                and line not in lines
                and not any(term in line for term in meta_terms)
            ):
                lines.append(line)
    reading_terms = ("阅读", "定位", "中心", "主旨", "细节", "选项", "关键词", "段落", "长篇", "选词", "题")
    writing_terms = ("写作", "句子", "语法", "词汇", "单词", "逻辑", "模板", "翻译", "表达")

    def choose(terms: tuple[str, ...], fallback: list[str]) -> list[str]:
        scored = sorted(
            lines,
            key=lambda line: (
                sum(term in line for term in terms),
                min(len(line), 80),
            ),
            reverse=True,
        )
        picked = [line for line in scored if any(term in line for term in terms)][:4]
        return picked or fallback

    reading_methods = choose(reading_terms, base["reading_methods"])
    writing_methods = choose(writing_terms, base["writing_methods"])
    methods = reading_methods + writing_methods
    return {
        **base,
        "teaching_principles": methods[:4],
        "reading_methods": reading_methods,
        "writing_methods": writing_methods,
        "correction_style": writing_methods[0] if writing_methods else base["correction_style"],
        "signature": methods[0] if methods else "基于已导入课程的学习方法档案",
    }


def profile_from_output_or_transcript(output: str, transcript: str) -> dict[str, Any]:
    profile = profile_from_text(output)
    if profile["signature"] == default_profile()["signature"]:
        profile = profile_from_text(transcript)
    return profile


def extract_profile(transcript: str, agent: str) -> dict[str, Any]:
    """Extract transferable reading and writing pedagogy, never identity cues."""
    if text_generation_provider() is None:
        return default_profile()
    info = AGENTS[agent]
    prompt = f"""Analyse this English lesson transcript for a private study assistant.
Exam: {info['exam']}. Focus only on reading comprehension and writing: {info['focus']}.
Return strict JSON only. Extract methods that transfer to new questions, not biographical claims.
Do NOT identify, impersonate, imitate vocal style, or reproduce distinctive catchphrases.
Schema: {{"teaching_principles":[string],"answer_sequence":[string],"reading_methods":[string],"writing_methods":[string],"correction_style":string,"signature":string}}
Transcript:\n{transcript[:18000]}"""
    try:
        output = generate_text(
            "Extract transferable English reading and writing pedagogy. Return one valid JSON object only, with no Markdown or commentary.",
            prompt,
            json_mode=True,
        )
        parsed = parse_json_object(output)
        if parsed is None:
            return profile_from_output_or_transcript(output, transcript)
        required_keys = (
            "teaching_principles",
            "answer_sequence",
            "reading_methods",
            "writing_methods",
            "correction_style",
            "signature",
        )
        if not all(key in parsed for key in required_keys):
            try:
                normalized_output = generate_text(
                    "Convert the supplied study summary into one strict JSON object with exactly the required keys. "
                    "Do not use Markdown, commentary, nested objects, or extra keys.",
                    f"""Required schema:
{{
  "teaching_principles": ["中文方法"],
  "answer_sequence": ["中文步骤"],
  "reading_methods": ["中文阅读方法"],
  "writing_methods": ["中文写作方法"],
  "correction_style": "中文纠错方式",
  "signature": "一句中文方法摘要"
}}
If the source lacks reading or writing details, preserve these defaults:
{json.dumps(default_profile(), ensure_ascii=False)}

Study summary:
{output[:9000]}""",
                    json_mode=True,
                )
            except Exception:
                normalized_output = ""
            normalized = parse_json_object(normalized_output)
            if normalized:
                parsed = normalized
        if (
            all(isinstance(parsed.get(key), list) for key in ("teaching_principles", "answer_sequence", "reading_methods", "writing_methods"))
            and parsed.get("signature") != default_profile()["signature"]
        ):
            return {**default_profile(), **parsed}
        return profile_from_output_or_transcript(output, transcript)
    except Exception:
        pass
    return default_profile()


def ffmpeg_executable() -> str | None:
    installed = shutil.which("ffmpeg")
    if installed:
        return installed
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError):
        return None


def prepare_audio_chunks(path: Path, temp_dir: Path) -> list[Path]:
    if path.stat().st_size <= MAX_DIRECT_TRANSCRIBE_BYTES:
        return [path]
    ffmpeg = ffmpeg_executable()
    if not ffmpeg:
        raise HTTPException(500, "缺少 FFmpeg，无法处理长音视频。请重新安装 requirements.txt。")
    pattern = str(temp_dir / "audio-%03d.mp3")
    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(path),
        "-vn", "-ac", "1", "-ar", "16000", "-b:a", "64k",
        "-f", "segment", "-segment_time", "480", pattern,
    ]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=1800, check=False)
    chunks = sorted(temp_dir.glob("audio-*.mp3"))
    if completed.returncode or not chunks:
        raise HTTPException(422, f"音轨转换失败：{completed.stderr[-400:] or '未生成音频片段'}")
    return chunks


def transcription_provider() -> str | None:
    configured = os.getenv("TRANSCRIBE_PROVIDER", "auto").strip().lower()
    local_ready = importlib.util.find_spec("faster_whisper") is not None
    cloud_ready = bool(os.getenv("OPENAI_API_KEY"))
    if configured == "local":
        return "local" if local_ready else None
    if configured == "openai":
        return "openai" if cloud_ready else None
    if local_ready:
        return "local"
    if cloud_ready:
        return "openai"
    return None


def ensure_transcription_available() -> str:
    provider = transcription_provider()
    if provider:
        return provider
    raise HTTPException(
        500,
        "没有可用的语音识别器。请执行 pip install -r requirements.txt 安装本地 Whisper，"
        "或在 .env 配置 OPENAI_API_KEY。",
    )


def transcribe_local(path: Path) -> str:
    from faster_whisper import WhisperModel

    model_name = os.getenv("LOCAL_WHISPER_MODEL", "small").strip()
    device = os.getenv("LOCAL_WHISPER_DEVICE", "cpu").strip()
    compute_type = os.getenv("LOCAL_WHISPER_COMPUTE_TYPE", "int8").strip()
    model_dir = str(Path(os.getenv("LOCAL_WHISPER_MODEL_DIR", DATA_DIR / "models")).resolve())
    key = (model_name, device, compute_type, model_dir)
    model = _LOCAL_WHISPER_MODELS.get(key)
    if model is None:
        model = WhisperModel(
            model_name,
            device=device,
            compute_type=compute_type,
            download_root=model_dir,
        )
        _LOCAL_WHISPER_MODELS[key] = model
    segments, _ = model.transcribe(
        str(path),
        vad_filter=True,
        beam_size=5,
        condition_on_previous_text=True,
    )
    texts = [segment.text.strip() for segment in segments if segment.text.strip()]
    if not texts:
        raise HTTPException(422, "本地语音识别没有检测到可用语音。")
    return "\n".join(texts)


def transcribe(path: Path) -> str:
    provider = ensure_transcription_available()
    if provider == "local":
        return transcribe_local(path)
    api = client()
    if not api:
        raise HTTPException(500, "OpenAI 转写已选择，但缺少 OPENAI_API_KEY。")
    temp_dir = DATA_DIR / "tmp" / now_id()
    temp_dir.mkdir(parents=True, exist_ok=True)
    transcripts: list[str] = []
    try:
        for chunk in prepare_audio_chunks(path, temp_dir):
            with chunk.open("rb") as media:
                result = api.audio.transcriptions.create(
                    model=os.getenv("OPENAI_TRANSCRIBE_MODEL", "gpt-transcribe"),
                    file=media,
                    languages=["en", "zh-cn"],
                )
            if result.text.strip():
                transcripts.append(result.text.strip())
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
    if not transcripts:
        raise HTTPException(422, "没有从音视频中识别出可用语音。")
    return "\n\n".join(transcripts)


def ocr_image(path: Path) -> str:
    """Extract text locally; the uploaded image is never retained."""
    global _OCR_ENGINE
    rapidocr_available = (
        importlib.util.find_spec("rapidocr") is not None
        or importlib.util.find_spec("rapidocr_onnxruntime") is not None
    )
    if not rapidocr_available:
        raise HTTPException(500, "本地 OCR 尚未安装，请执行 pip install -r requirements.txt。")
    if _OCR_ENGINE is None:
        try:
            from rapidocr import RapidOCR
        except ImportError:
            from rapidocr_onnxruntime import RapidOCR

        _OCR_ENGINE = RapidOCR()
    output = _OCR_ENGINE(str(path))
    if hasattr(output, "txts"):
        lines = [str(line).strip() for line in (output.txts or []) if str(line).strip()]
    else:
        result, _ = output
        lines = [str(item[1]).strip() for item in (result or []) if len(item) > 1 and str(item[1]).strip()]
    if not lines:
        raise HTTPException(422, "图片中没有识别到可用文字，请靠近拍摄并保持光线均匀。")
    return "\n".join(lines)


async def extract_uploaded_image_text(file: UploadFile) -> str:
    suffix = Path(file.filename or "photo.jpg").suffix.lower()
    if suffix not in IMAGE_EXTENSIONS:
        raise HTTPException(415, "请上传 JPG、PNG、WEBP、BMP 或 GIF 图片。")
    temp_dir = DATA_DIR / "tmp" / now_id()
    temp_dir.mkdir(parents=True, exist_ok=True)
    image_path = temp_dir / f"question{suffix}"
    try:
        written = 0
        with image_path.open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                written += len(chunk)
                if written > 20 * 1024 * 1024:
                    raise HTTPException(413, "图片不能超过 20MB。")
                output.write(chunk)
        return ocr_image(image_path)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def add_source(title: str, transcript: str, source_type: str, agent: str, original_name: str | None = None, url: str | None = None) -> dict[str, Any]:
    agent = validate_agent(agent)
    chunks = parse_subtitles(transcript, Path(original_name or "text.txt").suffix.lower())
    if not chunks:
        raise HTTPException(400, "没有在文件中找到可用文本。")
    db = load_db()
    item = {
        "id": now_id(), "title": title.strip() or "未命名课程", "type": source_type, "agent": agent,
        "original_name": original_name, "url": url, "created_at": int(time.time()),
        "transcript": transcript, "chunks": chunks,
    }
    db["sources"].append(item)
    agent_text = "\n".join(s["transcript"] for s in db["sources"] if s.get("agent") == agent)[-50000:]
    db.setdefault("profiles", {})[agent] = extract_profile(agent_text, agent)
    save_db(db)
    return item


def answer_question(question: str, mode: str, agent: str) -> dict[str, Any]:
    agent = validate_agent(agent)
    question = clean_text(question)
    if not question:
        raise HTTPException(400, "请输入问题。")
    db = load_db()
    sources = retrieve(question, agent)
    grounded = bool(sources)
    agent_sources = [source for source in db.get("sources", []) if source.get("agent") == agent]
    has_agent_sources = bool(agent_sources)
    classification = "课程依据" if grounded else ("课程方法档案" if has_agent_sources else "延伸推演")
    context = "\n\n".join(f"[{s['source_title']} {seconds_label(s.get('start')) or '无时间点'}] {s['text']}" for s in sources)
    profile = json.dumps(db.get("profiles", {}).get(agent, default_profile()), ensure_ascii=False)
    agent_info = AGENTS[agent]
    instructions = f"""你是{agent_info['name']}，服务范围仅限阅读理解与英语写作；其他题型要简短说明不在本智能体范围内，并建议切换到合适的练习。你不是视频中的老师本人。你可以采用从课程资料中提炼出的阅读判断链、写作组织方法和解释顺序，但不可自称该老师、声称对方说过未出现的话，也不可复写独特口头禅。{agent_info['level']} 用自然中文教学，配准确、简短的英文示例。
不要只复述资料。必须提炼可迁移方法，并结合当前新问题做原创推演：阅读理解要生成一道结构相似但内容不同的原创迷你题；写作要同时提供一个稳妥版本和一个更有表现力的版本。原创部分必须标为“迁移应用”，不得伪装成老师原话。"""
    if grounded:
        directive = "这是课程依据回答。只将资料中明确支持的观点说成课程内容；资料不足的部分要注明。"
    elif has_agent_sources:
        directive = "这是课程方法档案回答。下面的教学风格档案来自已导入、已转写的课程；可以明确说“课程方法档案强调”，但不要声称找到了某句视频原话。迁移到新题的部分必须标为原创推演。"
    else:
        directive = "这是延伸推演。第一行必须是：**以下是基于课程方法的延伸推演，不是老师的原话。** 然后作出原创、可检验的分析。"
    mode_hint = {
        "english": "尽量用英语回答，必要时用中文点拨。",
        "reading": "按：题型判断、原文证据、选项辨析、可复用方法 输出。",
        "correction": "按：原稿、保留内容、关键问题、修改稿 输出。",
        "writing": "先判断写作任务和读者，再给提纲、主题句、关键表达与润色要点。",
    }.get(mode, "阅读理解先给证据链；写作先给结构再润色。")
    if text_generation_provider() is None:
        content = ("课程资料已匹配，但本地 Ollama 未运行且没有配置 OPENAI_API_KEY，无法生成教学回答。" if grounded else "**以下是基于课程方法的延伸推演，不是老师的原话。**\n\n本地 Ollama 未运行且没有配置 OPENAI_API_KEY，因此只能完成识别与检索，不能生成新的英语讲解。")
    else:
        prompt = f"""考试：{agent_info['exam']}\n本智能体重点：{agent_info['focus']}\n教学风格档案：{profile}\n\n{directive}\n回答模式：{mode_hint}\n\n课程资料：\n{context or '没有直接匹配的课程资料。'}\n\n学生问题：{question}"""
        content = generate_text(instructions, prompt)
    if sources:
        citations = [{"title": s["source_title"], "time": seconds_label(s.get("start")), "excerpt": s["text"][:180]} for s in sources]
    else:
        citations = [
            {"title": source["title"], "time": None, "excerpt": "来自该课程的已提炼阅读/写作方法档案。"}
            for source in agent_sources[:3]
        ]
    return {"classification": classification, "answer": content, "citations": citations}


class TextImport(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1)
    agent: str = "cet4"


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    mode: str = "auto"
    agent: str = "cet4"


class LinkImport(BaseModel):
    title: str = Field(default="", max_length=200)
    url: str
    agent: str = "cet4"
    force_transcribe: bool = False


class BatchLinkImport(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=20)
    agent: str = "cet4"
    force_transcribe: bool = False


class CrawlSource(BaseModel):
    url: str
    platform: str = Field(min_length=1, max_length=80)
    agent: str = "cet4"
    note: str = Field(default="", max_length=500)


class ProfileRefresh(BaseModel):
    agent: str | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    ensure_storage()
    cleanup_stale_temp_files()
    job_data = load_jobs()
    changed = False
    for job in job_data.get("items", []):
        if job.get("status") == "running":
            job["status"] = "interrupted"
            job["error"] = "服务重启，任务已中断，请重新提交。"
            job["updated_at"] = int(time.time())
            changed = True
    if changed:
        save_jobs(job_data)
    yield


app = FastAPI(title="英语老师式私人学习助手", version="0.1.0", lifespan=lifespan)


def cloud_session_token(username: str, password: str) -> str:
    return hashlib.sha256(f"english-studio:{username}:{password}".encode()).hexdigest()


@app.middleware("http")
async def protect_cloud_app(request: Request, call_next):
    password = os.getenv("APP_ACCESS_PASSWORD", "")
    public_paths = ("/login", "/health", "/manifest.webmanifest", "/sw.js", "/static/")
    if not password or request.url.path.startswith(public_paths) or request.url.path.startswith("/wecom/"):
        return await call_next(request)
    expected = cloud_session_token(os.getenv("APP_ACCESS_USER", "student"), password)
    supplied = request.cookies.get("english_studio_session", "")
    if secrets.compare_digest(supplied, expected):
        return await call_next(request)
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "请先登录。"}, status_code=401)
    return RedirectResponse("/login", status_code=303)


@app.get("/login", response_class=HTMLResponse, include_in_schema=False)
def login_page(request: Request) -> Response:
    if not os.getenv("APP_ACCESS_PASSWORD"):
        return RedirectResponse("/", status_code=303)
    return HTMLResponse((BASE_DIR / "app" / "static" / "login.html").read_text(encoding="utf-8"))


@app.post("/login", include_in_schema=False)
async def login(request: Request) -> Response:
    password = os.getenv("APP_ACCESS_PASSWORD", "")
    username = os.getenv("APP_ACCESS_USER", "student")
    form = await request.form()
    if not password:
        return RedirectResponse("/", status_code=303)
    supplied_user = str(form.get("username", ""))
    supplied_password = str(form.get("password", ""))
    valid = secrets.compare_digest(supplied_user, username) and secrets.compare_digest(
        supplied_password, password
    )
    if not valid:
        return RedirectResponse("/login?error=1", status_code=303)
    response = RedirectResponse("/", status_code=303)
    secure_cookie = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    response.set_cookie(
        "english_studio_session",
        cloud_session_token(username, password),
        max_age=30 * 24 * 60 * 60,
        httponly=True,
        samesite="lax",
        secure=secure_cookie,
    )
    return response


@app.get("/logout", include_in_schema=False)
def logout() -> Response:
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("english_studio_session")
    return response


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    return (BASE_DIR / "app" / "static" / "index.html").read_text(encoding="utf-8")


@app.get("/manifest.webmanifest", include_in_schema=False)
def manifest() -> Response:
    return Response(
        content=(BASE_DIR / "app" / "static" / "manifest.webmanifest").read_text(encoding="utf-8"),
        media_type="application/manifest+json",
    )


@app.get("/sw.js", include_in_schema=False)
def service_worker() -> Response:
    return Response(
        content=(BASE_DIR / "app" / "static" / "sw.js").read_text(encoding="utf-8"),
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/"},
    )


app.mount("/static", StaticFiles(directory=BASE_DIR / "app" / "static"), name="static")


@app.get("/health")
def health() -> dict[str, Any]:
    text_provider = text_generation_provider()
    if text_provider == "ollama":
        text_model = os.getenv("OLLAMA_MODEL", "qwen3:8b")
    elif text_provider == "compatible":
        text_model = os.getenv("COMPATIBLE_MODEL", "")
    else:
        text_model = os.getenv("OPENAI_TEXT_MODEL", "gpt-6-astra")
    return {
        "ok": True,
        "ai_configured": bool(os.getenv("OPENAI_API_KEY")),
        "transcription_provider": transcription_provider(),
        "text_provider": text_provider,
        "text_model": text_model,
        "sources": len(load_db().get("sources", [])),
    }


@app.get("/api/library")
def library() -> dict[str, Any]:
    db = load_db()
    return {
        "profiles": db.get("profiles", {}),
        "agents": AGENTS,
        "sources": [
            {k: v for k, v in source.items() if k not in {"transcript", "chunks"}}
            | {"chunk_count": len(source["chunks"])}
            for source in db["sources"]
        ],
        "crawl_queue": db.get("crawl_queue", []),
    }


def rebuild_agent_profile(db: dict[str, Any], agent: str) -> dict[str, Any]:
    sources = [source for source in db.get("sources", []) if source.get("agent") == agent]
    per_source = max(1200, 10000 // max(1, len(sources)))
    text = "\n\n".join(
        f"《{source['title']}》\n{source['transcript'][:per_source]}"
        for source in sources
    )[:10000]
    profile = extract_profile(text, agent)
    db.setdefault("profiles", {})[agent] = profile
    return profile


@app.post("/api/profiles/rebuild")
def rebuild_profiles(payload: ProfileRefresh | None = None) -> dict[str, Any]:
    db = load_db()
    targets = [validate_agent(payload.agent)] if payload and payload.agent else list(AGENTS)
    profiles: dict[str, Any] = {}
    for agent in targets:
        profiles[agent] = rebuild_agent_profile(db, agent)
    save_db(db)
    return {"profiles": profiles}


@app.post("/api/import/text")
def import_text(payload: TextImport) -> dict[str, Any]:
    item = add_source(payload.title, payload.content, "text", payload.agent, "text.txt")
    return {"id": item["id"], "title": item["title"], "chunks": len(item["chunks"])}


@app.post("/api/import/upload")
async def import_upload(file: UploadFile = File(...), title: str | None = None, agent: str = "cet4") -> dict[str, Any]:
    suffix = Path(file.filename or "upload").suffix.lower()
    if suffix not in TEXT_EXTENSIONS | MEDIA_EXTENSIONS:
        raise HTTPException(415, "仅支持 TXT、SRT、VTT、MD、MP3、MP4、M4A、WAV、WEBM 等格式。")
    target = UPLOAD_DIR / f"{now_id()}{suffix}"
    with target.open("wb") as output:
        shutil.copyfileobj(file.file, output)
    try:
        text = target.read_text(encoding="utf-8-sig") if suffix in TEXT_EXTENSIONS else transcribe(target)
        target.unlink(missing_ok=True)
        item = add_source(title or Path(file.filename or "课程").stem, text, "upload", agent, file.filename)
        return {"id": item["id"], "title": item["title"], "chunks": len(item["chunks"])}
    finally:
        target.unlink(missing_ok=True)


@app.post("/api/import/link")
def import_link(payload: LinkImport) -> dict[str, Any]:
    validate_agent(payload.agent)
    parsed = urllib.parse.urlparse(payload.url)
    if parsed.scheme not in {"http", "https"}:
        raise HTTPException(400, "链接必须以 http:// 或 https:// 开头。")
    try:
        import yt_dlp  # noqa: F401
    except ImportError as exc:
        raise HTTPException(500, "未安装 yt-dlp，请执行 pip install -r requirements.txt。") from exc

    temp_dir = DATA_DIR / "tmp" / now_id()
    temp_dir.mkdir(parents=True, exist_ok=True)
    base_command = [sys.executable, "-m", "yt_dlp", "--no-warnings", "--no-playlist"]
    cookie_file = os.getenv("YTDLP_COOKIEFILE", "").strip()
    if cookie_file:
        if not Path(cookie_file).is_file():
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise HTTPException(400, "YTDLP_COOKIEFILE 指向的 cookie 文件不存在。")
        base_command.extend(["--cookies", cookie_file])
    try:
        metadata_result = subprocess.run(
            [*base_command, "--dump-single-json", "--skip-download", payload.url],
            capture_output=True, text=True, timeout=120, check=False,
        )
        if metadata_result.returncode:
            message = metadata_result.stderr.strip().splitlines()
            raise HTTPException(422, f"无法读取公开页面：{(message[-1] if message else '平台拒绝访问')[:300]}")
        try:
            metadata = json.loads(metadata_result.stdout)
        except json.JSONDecodeError as exc:
            raise HTTPException(422, "平台返回了无法识别的页面信息。") from exc

        title = clean_text(payload.title or metadata.get("title") or "未命名课程")[:200]
        platform = metadata.get("extractor_key") or metadata.get("extractor") or parsed.netloc
        subtitles: list[Path] = []
        if not payload.force_transcribe:
            subtitle_template = str(temp_dir / "subtitle.%(ext)s")
            subprocess.run(
                [
                    *base_command, "--skip-download", "--write-subs", "--write-auto-subs",
                    "--sub-langs", "en.*,zh.*,en,zh", "--sub-format", "vtt/srt/best",
                    "-o", subtitle_template, payload.url,
                ],
                capture_output=True, text=True, timeout=180, check=False,
            )
            subtitles = sorted(
                [p for p in temp_dir.iterdir() if p.suffix.lower() in {".vtt", ".srt"}],
                key=lambda p: ("auto" not in p.name.lower(), ".en." not in p.name.lower()),
            )
        method = "公开字幕"
        if subtitles:
            transcript = subtitles[0].read_text(encoding="utf-8-sig", errors="replace")
            original_name = subtitles[0].name
        else:
            ensure_transcription_available()
            media_template = str(temp_dir / "media.%(ext)s")
            media_result = subprocess.run(
                [
                    *base_command, "-f", "bestaudio/best",
                    "--max-filesize", "4294967296", "-o", media_template, payload.url,
                ],
                capture_output=True, text=True, timeout=3600, check=False,
            )
            media_files = [
                path for path in temp_dir.iterdir()
                if path.suffix.lower() in TEMP_MEDIA_EXTENSIONS and not path.name.startswith("subtitle.")
            ]
            if media_result.returncode or not media_files:
                message = media_result.stderr.strip().splitlines()
                raise HTTPException(422, f"未能取得公开字幕或音轨：{(message[-1] if message else '无可用媒体')[:300]}")
            transcript = transcribe(max(media_files, key=lambda path: path.stat().st_size))
            method = "临时音轨转写"
            original_name = None

        # Delete all downloaded media before the slower profile extraction step.
        shutil.rmtree(temp_dir, ignore_errors=True)
        item = add_source(
            title, transcript, "personal-study-link", payload.agent,
            original_name, payload.url,
        )
        return {
            "id": item["id"], "title": item["title"], "chunks": len(item["chunks"]),
            "platform": platform, "method": method,
            "privacy": "临时媒体已删除，仅保留文字课程资料。",
        }
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(504, "公开平台响应超时，请稍后重试或改用字幕文件。") from exc
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@app.post("/api/import/links")
def import_links(payload: BatchLinkImport) -> dict[str, Any]:
    """Process each provided URL independently so one failure does not stop the batch."""
    validate_agent(payload.agent)
    results: list[dict[str, Any]] = []
    for raw_url in payload.urls:
        url = raw_url.strip()
        if not url:
            continue
        try:
            item = import_link(
                LinkImport(
                    url=url,
                    agent=payload.agent,
                    force_transcribe=payload.force_transcribe,
                )
            )
            results.append({"url": url, "ok": True, "result": item})
        except HTTPException as exc:
            results.append({"url": url, "ok": False, "error": str(exc.detail)})
        except Exception as exc:
            results.append({"url": url, "ok": False, "error": f"处理失败：{exc}"})
    return {"items": results}


def run_link_job(job_id: str, urls: list[str], agent: str, force_transcribe: bool) -> None:
    with _IMPORT_LOCK:
        update_job(job_id, status="running", current=0, total=len(urls))
        results: list[dict[str, Any]] = []
        for index, url in enumerate(urls, start=1):
            try:
                response = import_link(
                    LinkImport(url=url, agent=agent, force_transcribe=force_transcribe)
                )
                results.append({"url": url, "ok": True, "result": response})
            except HTTPException as exc:
                results.append({"url": url, "ok": False, "error": str(exc.detail)})
            except Exception as exc:
                results.append({"url": url, "ok": False, "error": f"处理失败：{exc}"})
            success_count = sum(1 for item in results if item["ok"])
            update_job(
                job_id,
                current=index,
                success_count=success_count,
                items=results,
            )
        success_count = sum(1 for item in results if item["ok"])
        status = "completed" if success_count == len(urls) else ("partial" if success_count else "failed")
        update_job(job_id, status=status, current=len(urls), success_count=success_count)


@app.post("/api/import/links/jobs", status_code=202)
def start_link_job(payload: BatchLinkImport) -> dict[str, Any]:
    validate_agent(payload.agent)
    urls = [url.strip() for url in payload.urls if url.strip()]
    if not urls:
        raise HTTPException(400, "请至少提供一个视频链接。")
    job = {
        "id": now_id(),
        "status": "queued",
        "agent": payload.agent,
        "force_transcribe": payload.force_transcribe,
        "urls": urls,
        "total": len(urls),
        "current": 0,
        "success_count": 0,
        "items": [],
        "created_at": int(time.time()),
        "updated_at": int(time.time()),
    }
    create_job(job)
    Thread(
        target=run_link_job,
        args=(job["id"], urls, payload.agent, payload.force_transcribe),
        daemon=True,
    ).start()
    return job


@app.get("/api/jobs")
def jobs(limit: int = 10) -> dict[str, Any]:
    items = load_jobs().get("items", [])[:max(1, min(limit, 50))]
    return {"items": items}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> dict[str, Any]:
    job = next((item for item in load_jobs().get("items", []) if item["id"] == job_id), None)
    if job is None:
        raise HTTPException(404, "任务不存在。")
    return job


@app.post("/api/crawl/queue")
def queue_public_source(payload: CrawlSource) -> dict[str, Any]:
    """Bookmark a public page for later personal-study import."""
    validate_agent(payload.agent)
    parsed = urllib.parse.urlparse(payload.url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(400, "请提供有效的公开网页链接。")
    db = load_db()
    entry = {"id": now_id(), "url": payload.url, "platform": payload.platform, "agent": payload.agent, "note": payload.note, "status": "等待本机字幕采集", "created_at": int(time.time())}
    db.setdefault("crawl_queue", []).append(entry)
    save_db(db)
    return entry


@app.post("/api/ask")
def ask(payload: AskRequest) -> dict[str, Any]:
    return answer_question(payload.question, payload.mode, payload.agent)


@app.post("/api/ask/photo")
async def ask_photo(
    file: UploadFile = File(...),
    question: str = "",
    mode: str = "auto",
    agent: str = "cet4",
) -> dict[str, Any]:
    validate_agent(agent)
    ocr_text = await extract_uploaded_image_text(file)
    combined = (
        f"下面是从学生拍摄的图片中本地 OCR 识别出的题目或作文：\n{ocr_text}\n\n"
        f"学生的附加问题：{clean_text(question) or '请识别题型并完整讲解，再给出原创练习。'}"
    )
    result = answer_question(combined, mode, agent)
    return {**result, "ocr_text": ocr_text}


@app.post("/api/ocr/photo")
async def ocr_photo(file: UploadFile = File(...)) -> dict[str, Any]:
    ocr_text = await extract_uploaded_image_text(file)
    return {"ocr_text": ocr_text, "privacy": "图片已在本机识别并删除。"}


def sha1_signature(*parts: str) -> str:
    return hashlib.sha1("".join(sorted(parts)).encode()).hexdigest()


@dataclass
class WeComCrypto:
    token: str
    encoding_key: str
    corp_id: str

    @property
    def key(self) -> bytes:
        return base64.b64decode(self.encoding_key + "=")

    def verify(self, signature: str, timestamp: str, nonce: str, encrypted: str) -> bool:
        return secrets.compare_digest(sha1_signature(self.token, timestamp, nonce, encrypted), signature)

    def decrypt(self, encrypted: str) -> str:
        raw = base64.b64decode(encrypted)
        decryptor = Cipher(algorithms.AES(self.key), modes.CBC(self.key[:16])).decryptor()
        padded = decryptor.update(raw) + decryptor.finalize()
        pad = padded[-1]
        content = padded[:-pad]
        size = int.from_bytes(content[16:20], "big")
        message = content[20:20 + size]
        receiver = content[20 + size:].decode("utf-8")
        if receiver != self.corp_id:
            raise ValueError("企业 ID 不匹配")
        return message.decode("utf-8")

    def encrypt(self, message: str, timestamp: str, nonce: str) -> tuple[str, str]:
        raw = secrets.token_bytes(16) + len(message.encode()).to_bytes(4, "big") + message.encode() + self.corp_id.encode()
        pad = 32 - len(raw) % 32
        padded = raw + bytes([pad]) * pad
        encryptor = Cipher(algorithms.AES(self.key), modes.CBC(self.key[:16])).encryptor()
        encrypted = base64.b64encode(encryptor.update(padded) + encryptor.finalize()).decode()
        return encrypted, sha1_signature(self.token, timestamp, nonce, encrypted)


def wecom_crypto() -> WeComCrypto | None:
    values = (os.getenv("WECOM_TOKEN"), os.getenv("WECOM_ENCODING_AES_KEY"), os.getenv("WECOM_CORP_ID"))
    return WeComCrypto(*values) if all(values) else None


def xml_text_reply(to_user: str, from_user: str, content: str) -> str:
    return f"<xml><ToUserName><![CDATA[{to_user}]]></ToUserName><FromUserName><![CDATA[{from_user}]]></FromUserName><CreateTime>{int(time.time())}</CreateTime><MsgType><![CDATA[text]]></MsgType><Content><![CDATA[{content[:1800]}]]></Content></xml>"


def parse_wecom_question(content: str) -> tuple[str, str]:
    """Use an explicit exam prefix; default to CET-4 when none is supplied."""
    text = clean_text(content)
    prefixes = {"四级": "cet4", "六级": "cet6", "考研": "postgrad"}
    for label, agent in prefixes.items():
        if text == label:
            return agent, text
        for separator in ("：", ":", " ", "，"):
            prefix = label + separator
            if text.startswith(prefix):
                return agent, text[len(prefix):].strip()
    return "cet4", text


@app.get("/wecom/callback")
def wecom_verify(msg_signature: str = "", timestamp: str = "", nonce: str = "", echostr: str = "", signature: str = "") -> Response:
    token = os.getenv("WECOM_TOKEN", "")
    if not token:
        raise HTTPException(503, "尚未配置 WECOM_TOKEN")
    crypto = wecom_crypto()
    supplied = msg_signature or signature
    if crypto:
        if not crypto.verify(supplied, timestamp, nonce, echostr):
            raise HTTPException(403, "无效签名")
        return PlainTextResponse(crypto.decrypt(echostr))
    if not secrets.compare_digest(sha1_signature(token, timestamp, nonce), supplied):
        raise HTTPException(403, "无效签名")
    return PlainTextResponse(echostr)


@app.post("/wecom/callback")
async def wecom_message(request: Request, msg_signature: str = "", timestamp: str = "", nonce: str = "", signature: str = "") -> Response:
    raw = (await request.body()).decode("utf-8")
    token = os.getenv("WECOM_TOKEN", "")
    if not token:
        raise HTTPException(503, "尚未配置 WECOM_TOKEN")
    crypto = wecom_crypto()
    try:
        if crypto:
            outer = ET.fromstring(raw)
            encrypted = outer.findtext("Encrypt", "")
            if not crypto.verify(msg_signature, timestamp, nonce, encrypted):
                raise HTTPException(403, "无效签名")
            raw = crypto.decrypt(encrypted)
        elif not secrets.compare_digest(sha1_signature(token, timestamp, nonce), signature):
            raise HTTPException(403, "无效签名")
        message = ET.fromstring(raw)
        if message.findtext("MsgType") != "text":
            reply = "目前请发送文字版阅读或作文问题；可用“六级：……”或“考研：……”指定智能体。"
        else:
            agent, question = parse_wecom_question(message.findtext("Content", ""))
            if not question:
                reply = "请在“四级：”“六级：”或“考研：”后面附上阅读或写作问题。"
            else:
                reply = answer_question(question, "auto", agent)["answer"]
        response_xml = xml_text_reply(message.findtext("FromUserName", ""), message.findtext("ToUserName", ""), reply)
        if not crypto:
            return Response(content=response_xml, media_type="application/xml")
        encrypted, reply_signature = crypto.encrypt(response_xml, timestamp, nonce)
        outer = f"<xml><Encrypt><![CDATA[{encrypted}]]></Encrypt><MsgSignature><![CDATA[{reply_signature}]]></MsgSignature><TimeStamp>{timestamp}</TimeStamp><Nonce><![CDATA[{nonce}]]></Nonce></xml>"
        return Response(content=outer, media_type="application/xml")
    except ET.ParseError as exc:
        raise HTTPException(400, "无法解析企业微信消息") from exc

