"""
tts-edge — Edge TTS server (OpenAI-compatible) cho Project AIRI.

Giọng neural tiếng Việt của Microsoft (vi-VN-HoaiMyNeural / vi-VN-NamMinhNeural),
miễn phí, chất lượng cao. Cần internet.

API giống hệt OpenAI:
  POST /v1/audio/speech   {model, voice, input, speed?, response_format?} -> audio bytes
  GET  /v1/audio/speech?input=...   bản đơn giản
  GET  /v1/models
  GET  /v1/audio/voices
  GET  /health

Lưu ý kỹ thuật:
- Edge endpoint (free) KHÔNG hỗ trợ neural styles (mstts:express-as) -> "style" ở đây là
  preset pitch/rate cho gần đúng cảm xúc (cheerful/sad/...).
- edge-tts 7.x chỉ trả MP3 -> transcode sang wav/pcm/ogg/opus/webm/aac/flac bằng ffmpeg nếu có.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess

import edge_tts
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel

import stt

HOST = os.getenv("TTS_EDGE_HOST", "127.0.0.1")
PORT = int(os.getenv("TTS_EDGE_PORT", "8766"))
APP_NAME = "tts-edge"
VERSION = "4.0"  # bump khi sửa logic; in ra lúc khởi động để debug
STT_FORMATS = {"json", "text", "srt", "vtt", "verbose_json"}

VI_VOICES = {
    "vi-VN-HoaiMyNeural": {"gender": "Nữ", "kind": "Tiếng Việt chuẩn (chính thức)", "accent": None},
    "vi-VN-NamMinhNeural": {"gender": "Nam", "kind": "Tiếng Việt chuẩn (chính thức)", "accent": None},
    # Giọng đa ngữ của Microsoft — đã kiểm chứng thực nghiệm nói được tiếng Việt (có accent)
    "en-US-AvaMultilingualNeural": {"gender": "Nữ", "kind": "Đa ngữ (nói được tiếng Việt)", "accent": "Mỹ"},
    "en-US-EmmaMultilingualNeural": {"gender": "Nữ", "kind": "Đa ngữ (nói được tiếng Việt)", "accent": "Mỹ"},
    "fr-FR-VivienneMultilingualNeural": {"gender": "Nữ", "kind": "Đa ngữ (nói được tiếng Việt)", "accent": "Pháp"},
    "de-DE-SeraphinaMultilingualNeural": {"gender": "Nữ", "kind": "Đa ngữ (nói được tiếng Việt)", "accent": "Đức"},
    "pt-BR-ThalitaMultilingualNeural": {"gender": "Nữ", "kind": "Đa ngữ (nói được tiếng Việt)", "accent": "Brazil"},
}
VOICE_ALIASES = {}
for _name in VI_VOICES:
    _base = _name.split("-")[-1].removesuffix("MultilingualNeural").removesuffix("Neural")
    VOICE_ALIASES[_base.lower()] = _name
DEFAULT_VOICE = "vi-VN-HoaiMyNeural"

# style -> (pitch_delta_Hz, rate_delta_pct) — Edge endpoint không có express-as thật
STYLE_PRESETS = {
    "normal": (0, 0),
    "chat": (0, 8),
    "cheerful": (8, 12),
    "empathetic": (-5, -8),
    "gentle": (-10, -12),
    "sad": (-15, -18),
    "angry": (10, 10),
}

# ---------------------------------------------------------------------------
# Định dạng output (mp3 = passthrough; còn lại transcode qua ffmpeg)
# ---------------------------------------------------------------------------
FFMPEG = shutil.which("ffmpeg")
FFMPEG_ARGS = {
    "wav": ["-f", "wav", "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le"],
    "pcm": ["-f", "s16le", "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le"],
    "ogg": ["-f", "ogg", "-ar", "24000", "-ac", "1", "-c:a", "libvorbis"],
    "opus": ["-f", "ogg", "-ar", "24000", "-ac", "1", "-c:a", "libopus"],
    "webm": ["-f", "webm", "-ar", "24000", "-ac", "1", "-c:a", "libopus"],
    "aac": ["-f", "adts", "-ar", "24000", "-ac", "1", "-c:a", "aac", "-b:a", "128k"],
    "flac": ["-f", "flac", "-ar", "24000", "-ac", "1", "-c:a", "flac"],
}
MEDIA_TYPE = {
    "mp3": "audio/mpeg", "wav": "audio/wav", "pcm": "audio/pcm",
    "ogg": "audio/ogg", "opus": "audio/ogg", "webm": "audio/webm",
    "aac": "audio/aac", "flac": "audio/flac",
}

_sem = asyncio.Semaphore(4)  # tránh bị Microsoft throttle


def parse_voice(voice: str) -> tuple[str, str]:
    """'HoaiMy', 'vi-VN-HoaiMyNeural', 'en-US-AvaMultilingualNeural:cheerful' -> (voice, style)."""
    v = (voice or "").strip()
    if not v or v in ("alloy", "nova", "shimmer", "echo", "onyx", "fable", "tts-1"):
        return DEFAULT_VOICE, "normal"
    style = "normal"
    if ":" in v:
        v, style = v.split(":", 1)
        style = style.strip().lower()
    if v.lower() in VOICE_ALIASES:
        v = VOICE_ALIASES[v.lower()]
    match = next((name for name in VI_VOICES if name.lower() == v.lower()), None)
    if not match:
        raise ValueError(
            f"Giọng không hợp lệ: {voice!r}. Hợp lệ: {', '.join(VI_VOICES)} "
            f"(có thể thêm ':style', vd vi-VN-HoaiMyNeural:cheerful)"
        )
    if style not in STYLE_PRESETS:
        raise ValueError(
            f"Style không hợp lệ cho {match}: {style!r}. Có: {', '.join(STYLE_PRESETS)}"
        )
    return match, style


async def synth(text: str, voice: str, style: str, speed: float, fmt: str) -> bytes:
    pitch_delta, rate_delta = STYLE_PRESETS[style]
    rate_pct = max(-50, min(100, rate_delta + round((speed - 1.0) * 100)))
    pitch_hz = max(-50, min(50, pitch_delta))
    mp3: bytes | None = None
    try:
        async with _sem:
            comm = edge_tts.Communicate(
                text, voice,
                rate=f"{rate_pct:+d}%", pitch=f"{pitch_hz:+d}Hz",
                connect_timeout=15, receive_timeout=60,
            )
            audio = bytearray()
            async for chunk in comm.stream():
                if chunk["type"] == "audio":
                    audio.extend(chunk["data"])
            if not audio:
                raise HTTPException(
                    status_code=502,
                    detail="Edge-TTS không trả về audio (kiểm tra internet / giọng có hợp lệ?)",
                )
            mp3 = bytes(audio)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Lỗi Edge-TTS: {e}")

    if fmt == "mp3":
        return mp3
    if not FFMPEG:
        raise HTTPException(
            status_code=400,
            detail="Cần ffmpeg để xuất định dạng này. Cài qua winget: winget install ffmpeg",
        )
    try:
        proc = await asyncio.to_thread(
            subprocess.run,
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-i", "pipe:0", *FFMPEG_ARGS[fmt], "pipe:1"],
            input=mp3, capture_output=True, timeout=60,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Transcode quá lâu (ffmpeg timeout)")
    if proc.returncode != 0:
        raise HTTPException(
            status_code=502,
            detail=f"ffmpeg lỗi (input {len(mp3)}B): {proc.stderr.decode(errors='replace')[:300]}",
        )
    return proc.stdout


app = FastAPI(title=APP_NAME)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class SpeechRequest(BaseModel):
    model: str = "tts-1"
    voice: str = DEFAULT_VOICE
    input: str = ""
    speed: float = 1.3
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
        headers={"X-TTS-Engine": APP_NAME, "X-TTS-Voice": voice},
    )


@app.get("/v1/audio/speech")
async def audio_speech_get(
    input: str = Query(..., description="Văn bản cần đọc"),
    voice: str = DEFAULT_VOICE,
    speed: float = 1.3,
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
    return {
        "object": "list",
        "data": [
            {"id": "tts-1", "object": "model", "owned_by": APP_NAME},
            {"id": "whisper-1", "object": "model", "owned_by": APP_NAME, "backend": stt.MODEL_SIZE},
        ],
    }


async def _run_stt(file: UploadFile, model: str, language, prompt, response_format, temperature, task):
    if response_format not in STT_FORMATS:
        raise HTTPException(status_code=400, detail=f"response_format không hỗ trợ: {response_format}")
    data = await file.read()
    try:
        media, body = await stt.transcribe(
            data, file.filename or "audio.wav", language,
            task=task, response_format=response_format, prompt=prompt, temperature=temperature,
            content_type=file.content_type,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Lỗi STT: {e}")
    return Response(
        content=body, media_type=media,
        headers={"X-TTS-Engine": APP_NAME, "X-STT-Model": stt.MODEL_SIZE, "X-STT-Status": stt.status()},
    )


@app.post("/v1/audio/transcriptions")
async def audio_transcriptions(
    file: UploadFile = File(...),
    model: str = Form("whisper-1"),
    language: str | None = Form(None),
    prompt: str | None = Form(None),
    response_format: str = Form("json"),
    temperature: float = Form(0.0),
):
    """Chuẩn OpenAI Whisper: multipart file -> {"text": "..."}."""
    return await _run_stt(file, model, language, prompt, response_format, temperature, task="transcribe")


@app.post("/v1/audio/translations")
async def audio_translations(
    file: UploadFile = File(...),
    model: str = Form("whisper-1"),
    language: str | None = Form(None),
    prompt: str | None = Form(None),
    response_format: str = Form("json"),
    temperature: float = Form(0.0),
):
    """Chuẩn OpenAI Whisper: dịch giọng nói bất kỳ -> tiếng Anh."""
    return await _run_stt(file, model, language, prompt, response_format, temperature, task="translate")


@app.get("/v1/audio/voices")
async def voices():
    data = []
    for name, meta in VI_VOICES.items():
        for st in STYLE_PRESETS:
            data.append({
                "voice": name,
                "id": name if st == "normal" else f"{name}:{st}",
                "gender": meta["gender"],
                "kind": meta["kind"],
                "accent": meta["accent"],
                "style": st,
                "style_note": "preset pitch/rate" if st != "normal" else None,
                "language": "vi-VN",
            })
    return {"object": "list", "data": data}


@app.get("/health")
async def health():
    return {
        "status": "ok", "engine": APP_NAME,
        "voices": list(VI_VOICES), "online": True,
        "ffmpeg": bool(FFMPEG),
        "stt": {"model": stt.MODEL_SIZE, "status": stt.status(), "language": stt.DEFAULT_LANG},
    }

INDEX_HTML = """<!doctype html><html lang="vi"><head><meta charset="utf-8"><title>tts-edge — test tiếng Việt</title>
<style>body{font-family:system-ui;max-width:720px;margin:40px auto;padding:0 16px;background:#111;color:#eee}
textarea{width:100%;height:90px;background:#1c1c1c;color:#eee;border:1px solid #333;border-radius:8px;padding:10px;font-size:15px}
select,button{background:#222;color:#eee;border:1px solid #444;border-radius:8px;padding:8px 12px;font-size:14px;margin:4px 4px 0 0}
button{background:#2d6cdf;border:none;cursor:pointer}audio{width:100%;margin-top:12px}
small{color:#999}</style></head><body>
<h2>🗣️ tts-edge — Edge TTS tiếng Việt</h2>
<textarea id="txt">Xin chào! Mình là trợ lý AI của bạn, rất vui được trò chuyện cùng bạn hôm nay.</textarea>
<div><select id="voice"></select><select id="style"></select>
<select id="fmt"><option value="mp3">mp3</option><option value="wav">wav</option><option value="ogg">ogg</option><option value="pcm">pcm</option></select>
<button onclick="speak()">▶ Nghe thử</button></div>
<small>Style = preset pitch/rate (Edge endpoint không hỗ trợ neural style thật cho tiếng Việt)</small>
<audio id="player" controls></audio>
<script>
const V={'vi-VN-HoaiMyNeural':'Nữ · tiếng Việt chuẩn','vi-VN-NamMinhNeural':'Nam · tiếng Việt chuẩn','en-US-AvaMultilingualNeural':'Nữ · đa ngữ (accent Mỹ)','en-US-EmmaMultilingualNeural':'Nữ · đa ngữ (accent Mỹ)','fr-FR-VivienneMultilingualNeural':'Nữ · đa ngữ (accent Pháp)','de-DE-SeraphinaMultilingualNeural':'Nữ · đa ngữ (accent Đức)','pt-BR-ThalitaMultilingualNeural':'Nữ · đa ngữ (accent Brazil)'};
const S=['normal','chat','cheerful','empathetic','gentle','sad','angry'];
const vSel=document.getElementById('voice'),sSel=document.getElementById('style');
for(const v in V){const o=document.createElement('option');o.value=v;o.textContent=V[v]+' — '+v;vSel.appendChild(o)}
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
    print(f"[{APP_NAME} v{VERSION}] Giọng: {', '.join(VI_VOICES)} | ffmpeg: {'có' if FFMPEG else 'KHÔNG'} | Port: {PORT}")
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
