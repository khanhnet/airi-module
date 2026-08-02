"""
tts-vieneu — VieNeu-TTS v3 Turbo server (OpenAI-compatible) cho Project AIRI.
Chạy cạnh tts-edge (Edge TTS, port 8766) — port mặc định 8767.

Ưu điểm VieNeu v3 Turbo (chạy 100% local, torch-free ONNX int8 trên CPU):
  - 48 kHz, 14 giọng preset (Bắc/Trung/Nam, nam/nữ), 3 style đọc
  - Clone giọng zero-shot từ audio 3–8s (ref_audio)
  - Emotion cues: [cười] [thở dài] [hắng giọng] ngay trong text
  - Offline hoàn toàn, RTF < 1 trên CPU (phản hồi gần real-time)

API giống hệt OpenAI:
  POST /v1/audio/speech   {model, voice, input, speed?, response_format?} -> audio bytes
  GET  /v1/audio/voices
  GET  /v1/models
  GET  /health

Lưu ý:
  - Lần request đầu tiên sẽ TẢI MODEL (~vài trăm MB) từ HuggingFace — chờ lâu hơn bình thường.
  - voice: tên giọng preset (vd "Trúc Ly", "Phạm Tuyên") hoặc "tên:style"
    (style: tu_nhien | tin_tuc | doc_truyen).
  - speed: engine không có tham số tốc độ -> dùng ffmpeg atempo (giữ nguyên pitch).
  - Cần ffmpeg cho mọi định dạng ngoài wav.
"""
from __future__ import annotations

import asyncio
import io
import os
import shutil
import subprocess
import threading
import wave

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel

HOST = os.getenv("TTS_VIENEU_HOST", "127.0.0.1")
PORT = int(os.getenv("TTS_VIENEU_PORT", "8767"))
APP_NAME = "tts-vieneu"
VERSION = "1.1"  # 1.1: giọng mặc định = Trúc Ly (nữ)

SAMPLE_RATE = 48000
STYLES = ["tu_nhien", "tin_tuc", "doc_truyen"]
STYLE_LABEL = {"tu_nhien": "Tự nhiên", "tin_tuc": "Tin tức", "doc_truyen": "Kể chuyện"}
DEFAULT_VOICE = "Trúc Ly"  # Nữ · Bắc · tự nhiên — giọng mặc định khi client không chỉ định

# ---------------------------------------------------------------------------
# Engine (lazy — model chỉ tải khi có request đầu tiên)
# ---------------------------------------------------------------------------
_engine = None
_engine_lock = threading.Lock()


def get_engine():
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                from vieneu import Vieneu
                print("[tts-vieneu] Khởi tạo engine v3 Turbo (có thể tải model lần đầu)...")
                _engine = Vieneu(mode="v3turbo")  # auto -> ONNX/CPU int8
                print("[tts-vieneu] Engine sẵn sàng.")
    return _engine


def _voice_map():
    """{lower_id: full_id, lower_label: full_id} -> (id, label)."""
    eng = get_engine()
    out = {}
    for label, vid in eng.list_preset_voices():
        out[label.strip().lower()] = (vid, label)
        out[str(vid).strip().lower()] = (vid, label)
    return out


def parse_voice(voice: str) -> tuple[str, str]:
    """'Trúc Ly', 'Trúc Ly:doc_truyen', '' -> (voice_id, style). Trống/alloy.. -> giọng nữ mặc định."""
    v = (voice or "").strip()
    if not v or v in ("alloy", "nova", "shimmer", "echo", "onyx", "fable", "tts-1"):
        return DEFAULT_VOICE, "tu_nhien"
    style = "tu_nhien"
    if ":" in v:
        v, style = v.split(":", 1)
        v, style = v.strip(), style.strip().lower()
    if style not in STYLES:
        raise ValueError(f"Style không hợp lệ: {style!r}. Có: {', '.join(STYLES)}")
    vmap = _voice_map()
    hit = vmap.get(v.lower())
    if not hit:
        raise ValueError(
            f"Giọng không hợp lệ: {voice!r}. Có {len(vmap) // 2} giọng preset — "
            f"vd: {', '.join(k for k in list(vmap)[:6])}... (thêm ':style' nếu muốn)"
        )
    return hit[0], style


# ---------------------------------------------------------------------------
# Encoding: numpy float32 48k -> wav / ffmpeg transcode
# ---------------------------------------------------------------------------
FFMPEG = shutil.which("ffmpeg")
FFMPEG_ARGS = {
    "mp3": ["-f", "mp3", "-ar", "48000", "-ac", "1", "-c:a", "libmp3lame", "-b:a", "128k"],
    "ogg": ["-f", "ogg", "-ar", "48000", "-ac", "1", "-c:a", "libvorbis"],
    "opus": ["-f", "ogg", "-ar", "48000", "-ac", "1", "-c:a", "libopus"],
    "webm": ["-f", "webm", "-ar", "48000", "-ac", "1", "-c:a", "libopus"],
    "aac": ["-f", "adts", "-ar", "48000", "-ac", "1", "-c:a", "aac", "-b:a", "128k"],
    "flac": ["-f", "flac", "-ar", "48000", "-ac", "1", "-c:a", "flac"],
}
MEDIA_TYPE = {
    "wav": "audio/wav", "mp3": "audio/mpeg", "ogg": "audio/ogg", "opus": "audio/ogg",
    "webm": "audio/webm", "aac": "audio/aac", "flac": "audio/flac",
}

_sem = asyncio.Semaphore(2)


def _numpy_to_wav_bytes(audio) -> bytes:
    """numpy float32 [-1,1] @48k -> WAV PCM16 bytes."""
    import numpy as np
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def _ffmpeg_convert(wav_bytes: bytes, fmt: str, speed: float) -> bytes:
    if not FFMPEG:
        raise HTTPException(status_code=400, detail="Cần ffmpeg. Cài: winget install ffmpeg")
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-i", "pipe:0"]
    if abs(speed - 1.0) > 0.01:
        cmd += ["-filter:a", f"atempo={max(0.5, min(2.0, speed))}"]
    cmd += [*FFMPEG_ARGS[fmt], "pipe:1"]
    try:
        proc = subprocess.run(cmd, input=wav_bytes, capture_output=True, timeout=120)
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="ffmpeg timeout")
    if proc.returncode != 0:
        raise HTTPException(status_code=502, detail=f"ffmpeg lỗi: {proc.stderr.decode(errors='replace')[:300]}")
    return proc.stdout


async def synth(text: str, voice: str, style: str, speed: float, fmt: str) -> bytes:
    def _run():
        eng = get_engine()
        audio = eng.infer(text, voice=voice, style=style)
        return _numpy_to_wav_bytes(audio)

    try:
        wav_bytes = await asyncio.to_thread(_run)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Lỗi VieNeu: {e}")
    if fmt == "wav" and abs(speed - 1.0) <= 0.01:
        return wav_bytes
    return await asyncio.to_thread(_ffmpeg_convert, wav_bytes, fmt, speed)


# ---------------------------------------------------------------------------
def _safe_header(v: str) -> str:
    """HTTP header bắt buộc latin-1 — loại ký tự không encode được (tên giọng tiếng Việt)."""
    return v.encode("latin-1", errors="ignore").decode("latin-1")


app = FastAPI(title=APP_NAME)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class SpeechRequest(BaseModel):
    model: str = "tts-1"
    voice: str = ""
    input: str = ""
    speed: float = 1.0
    response_format: str = "mp3"


@app.post("/v1/audio/speech")
async def audio_speech(req: SpeechRequest):
    if not req.input.strip():
        raise HTTPException(status_code=400, detail="input trống")
    if req.response_format not in MEDIA_TYPE:
        raise HTTPException(status_code=400, detail=f"response_format không hỗ trợ: {req.response_format}")
    try:
        voice, style = parse_voice(req.voice)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    data = await synth(req.input, voice, style, req.speed, req.response_format)
    return Response(
        content=data,
        media_type=MEDIA_TYPE[req.response_format],
        headers={"X-TTS-Engine": APP_NAME, "X-TTS-Voice": _safe_header(voice), "X-TTS-Style": style},
    )


@app.get("/v1/audio/speech")
async def audio_speech_get(
    input: str = Query(..., description="Văn bản cần đọc"),
    voice: str = "",
    speed: float = 1.0,
    response_format: str = "wav",
):
    if not input.strip():
        raise HTTPException(status_code=400, detail="input trống")
    if response_format not in MEDIA_TYPE:
        raise HTTPException(status_code=400, detail=f"response_format không hỗ trợ: {response_format}")
    try:
        voice, style = parse_voice(voice)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    data = await synth(input, voice, style, speed, response_format)
    return Response(content=data, media_type=MEDIA_TYPE[response_format])


@app.get("/v1/models")
async def models():
    return {"object": "list", "data": [{"id": "tts-1", "object": "model", "owned_by": APP_NAME}]}


@app.get("/v1/audio/voices")
async def voices():
    try:
        eng = get_engine()
        data = []
        for label, vid in eng.list_preset_voices():
            for st in STYLES:
                data.append({
                    "voice": str(vid),
                    "label": str(label),
                    "id": f"{vid}:{st}",
                    "style": st,
                    "style_label": STYLE_LABEL[st],
                    "language": "vi",
                })
        return {"object": "list", "data": data}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Lỗi đọc danh sách giọng (engine chưa sẵn sàng?): {e}")


@app.get("/health")
async def health():
    return {
        "status": "ok", "engine": APP_NAME, "version": VERSION,
        "engine_ready": _engine is not None,
        "default_voice": DEFAULT_VOICE,
        "ffmpeg": bool(FFMPEG), "sample_rate": SAMPLE_RATE,
        "port": PORT,
    }


INDEX_HTML = """<!doctype html><html lang="vi"><head><meta charset="utf-8"><title>tts-vieneu — test tiếng Việt</title>
<style>body{font-family:system-ui;max-width:720px;margin:40px auto;padding:0 16px;background:#111;color:#eee}
textarea{width:100%;height:90px;background:#1c1c1c;color:#eee;border:1px solid #333;border-radius:8px;padding:10px;font-size:15px}
select,button{background:#222;color:#eee;border:1px solid #444;border-radius:8px;padding:8px 12px;font-size:14px;margin:4px 4px 0 0}
button{background:#2d6cdf;border:none;cursor:pointer}audio{width:100%;margin-top:12px}
small{color:#999}</style></head><body>
<h2>🦜 tts-vieneu — VieNeu v3 Turbo (local, 48kHz)</h2>
<p><small>Lần đầu gọi sẽ tải model (~vài trăm MB) — hãy đợi.</small></p>
<textarea id="txt">[cười] Xin chào! Mình là trợ lý ảo của bạn. Rất vui được trò chuyện cùng bạn hôm nay!</textarea>
<div><select id="voice"></select><select id="style"></select>
<select id="fmt"><option value="mp3">mp3</option><option value="wav">wav</option><option value="ogg">ogg</option></select>
<button onclick="speak()">▶ Nghe thử</button></div>
<small>Emotion cues: [cười] [thở dài] [hắng giọng] · Style: tu_nhien/tin_tuc/doc_truyen</small>
<audio id="player" controls></audio>
<script>
const S=['tu_nhien','tin_tuc','doc_truyen'];
const vSel=document.getElementById('voice'),sSel=document.getElementById('style');
fetch('/v1/audio/voices').then(r=>r.json()).then(d=>{
  const seen=new Set();
  d.data.forEach(x=>{ if(!seen.has(x.voice)){seen.add(x.voice);
    const o=document.createElement('option');o.value=x.voice;o.textContent=x.label+' — '+x.voice;vSel.appendChild(o);} });
});
S.forEach(s=>{const o=document.createElement('option');o.value=s;o.textContent=s;sSel.appendChild(o)});
async function speak(){const r=await fetch('/v1/audio/speech',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({model:'tts-1',voice:vSel.value+':'+sSel.value,input:txt.value,speed:1.0,response_format:fmt.value})});
if(!r.ok){alert(await r.text());return}const b=await r.blob();player.src=URL.createObjectURL(b);player.play()}
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(INDEX_HTML)


if __name__ == "__main__":
    import uvicorn
    print(f"[{APP_NAME} v{VERSION}] API: http://{HOST}:{PORT}/v1/audio/speech  (chuẩn OpenAI)")
    print(f"[{APP_NAME}] Engine: VieNeu-TTS v3 Turbo (ONNX int8, 48kHz) | ffmpeg: {'có' if FFMPEG else 'KHÔNG'}")
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
