"""
STT (ASR) cho tts-edge — faster-whisper, chạy 100% local trên CPU.

Endpoints (chuẩn OpenAI Whisper):
  POST /v1/audio/transcriptions   multipart: file, model, language?, prompt?, response_format?, temperature?
  POST /v1/audio/translations     (dịch sang tiếng Anh)

Cấu hình qua biến môi trường:
  STT_MODEL         small (mặc định) | base | medium | large-v3 | tiny
  STT_DEVICE        cpu (mặc định)
  STT_COMPUTE_TYPE  int8 (mặc định, nhanh + nhẹ RAM) | float16 | float32
  STT_THREADS       4 (số luồng CPU)
  STT_LANGUAGE      vi (mặc định — tiếng Việt) | auto (tự phát hiện)

Model tải tự động từ HuggingFace lần đầu (~244MB cho small/int8), cache ở
~/.cache/huggingface.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

MODEL_SIZE = os.getenv("STT_MODEL", "small")
DEVICE = os.getenv("STT_DEVICE", "cpu")
COMPUTE_TYPE = os.getenv("STT_COMPUTE_TYPE", "int8")
THREADS = int(os.getenv("STT_THREADS", "4"))
DEFAULT_LANG = os.getenv("STT_LANGUAGE", "vi")  # "auto" = tự phát hiện
MAX_FILE_BYTES = 50 * 1024 * 1024  # 50MB giới hạn file audio

# Prompt tiếng Việt mặc định — chứa các từ/cụm từ trợ lý hay dùng
# Để model "nhớ" cách viết đúng: trợ lý, rất vui, xin chào, giúp đỡ...
DEFAULT_VI_PROMPT = (
    "Xin chào! Mình là trợ lý ảo, rất vui được gặp bạn. "
    "Hôm nay bạn cần giúp đỡ gì không? "
    "Mình có thể hỗ trợ bạn tìm kiếm thông tin, dịch thuật, hoặc trò chuyện."
)

_engine = None
_load_lock = asyncio.Lock()
_transcribe_lock = asyncio.Lock()  # CPU-bound: chỉ 1 luồng transcribe cùng lúc


def status() -> str:
    return "ready" if _engine is not None else "not-loaded"


def _load_sync() -> None:
    global _engine
    if _engine is None:
        from faster_whisper import WhisperModel
        _engine = WhisperModel(
            MODEL_SIZE, device=DEVICE, compute_type=COMPUTE_TYPE, cpu_threads=THREADS,
        )


async def ensure_loaded() -> None:
    async with _load_lock:
        await asyncio.to_thread(_load_sync)


_MIME_EXT = {
    "audio/webm": ".webm", "audio/webm;codecs=opus": ".webm",
    "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/wave": ".wav",
    "audio/mpeg": ".mp3", "audio/mp3": ".mp3",
    "audio/ogg": ".ogg", "audio/opus": ".ogg",
    "audio/flac": ".flac", "audio/aac": ".aac",
    "audio/mp4": ".m4a", "audio/x-m4a": ".m4a",
}

def _run_transcribe(data: bytes, suffix: str, language, task, prompt, temperature):
    """Ghi file tạm (để PyAV decode chắc chắn) rồi transcribe. Chạy trong thread."""
    fd, path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        segments, info = _engine.transcribe(
            path, language=language, task=task,
            vad_filter=True, initial_prompt=prompt, temperature=temperature,
        )
        segs = []
        for s in segments:
            segs.append({"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()})
        return info, segs
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _fmt_srt(segs) -> str:
    def ts(t):
        h, r = divmod(int(t * 1000), 3600000)
        m, r = divmod(r, 60000)
        s, ms = divmod(r, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
    lines = []
    for i, seg in enumerate(segs, 1):
        lines.append(f"{i}\n{ts(seg['start'])} --> {ts(seg['end'])}\n{seg['text']}\n")
    return "\n".join(lines)


def _fmt_vtt(segs) -> str:
    def ts(t):
        h, r = divmod(int(t * 1000), 3600000)
        m, r = divmod(r, 60000)
        s, ms = divmod(r, 1000)
        return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"
    lines = ["WEBVTT", ""]
    for seg in segs:
        lines.append(f"{ts(seg['start'])} --> {ts(seg['end'])}\n{seg['text']}\n")
    return "\n".join(lines)


async def transcribe(data: bytes, filename: str = "audio.wav", language: str | None = None,
                     task: str = "transcribe", response_format: str = "json",
                     prompt: str | None = None, temperature: float = 0.0,
                     content_type: str | None = None):
    """Trả về (media_type, bytes) theo response_format — chuẩn OpenAI Whisper.

    content_type: MIME type từ UploadFile.content_type — ưu tiên hơn filename để
    chọn codec đúng (tránh lỗi khi AIRI gửi WebM nhưng filename = recording.wav).
    prompt: context prompt cho whisper — nếu None, dùng DEFAULT_VI_PROMPT cho tiếng Việt.
    """
    if not data:
        raise ValueError("file rỗng")
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(f"file quá lớn (> {MAX_FILE_BYTES // (1024*1024)}MB)")
    lang = language or (None if DEFAULT_LANG.lower() == "auto" else DEFAULT_LANG)
    # Ưu tiên MIME type → filename suffix (AIRI gửi "recording.wav" nhưng MIME type là webm/opus)
    suffix = _MIME_EXT.get((content_type or "").split(";")[0].strip(), None)
    if not suffix:
        suffix = Path(filename).suffix or ".wav"
    # Prompt: nếu không có prompt từ client → dùng default tiếng Việt
    effective_prompt = prompt if prompt else (DEFAULT_VI_PROMPT if lang == "vi" or lang is None else None)
    async with _transcribe_lock:
        await ensure_loaded()
        info, segs = await asyncio.to_thread(
            _run_transcribe, data, suffix, lang, task, effective_prompt, temperature,
        )
    text = "".join(s["text"] for s in segs).strip()

    if response_format == "text":
        return "text/plain; charset=utf-8", text.encode("utf-8")
    if response_format in ("srt",):
        return "application/x-subrip", _fmt_srt(segs).encode("utf-8")
    if response_format == "vtt":
        return "text/vtt; charset=utf-8", _fmt_vtt(segs).encode("utf-8")
    if response_format == "verbose_json":
        import json
        payload = {
            "task": task,
            "language": info.language,
            "duration": round(info.duration, 2),
            "text": text,
            "segments": segs,
        }
        return "application/json", json.dumps(payload, ensure_ascii=False).encode("utf-8")
    # json (mặc định)
    import json
    return "application/json", json.dumps({"text": text}, ensure_ascii=False).encode("utf-8")
