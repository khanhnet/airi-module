# 🎙️ tts-edge — TTS tiếng Việt cho Project AIRI (2 engine song song)

Hai server TTS, cùng API chuẩn OpenAI, cắm thẳng vào **Project AIRI**:

| Server | Engine | Port | Đặc điểm |
|---|---|---|---|
| `server.py` | **Edge TTS** (Microsoft cloud) | **8766** | 7 giọng neural Việt, chất lượng studio, cần internet |
| `server-vieneu.py` | **VieNeu-TTS v3 Turbo** (local) | **8767** | 14 giọng Bắc/Trung/Nam, 48kHz, offline, clone giọng, emotion cues |

> ⚠️ Edge cần internet · VieNeu offline 100% (model tải 1 lần ~vài trăm MB). Chạy song song —
> trong AIRI tạo 2 Service Source và chọn theo nhu cầu.

---

## 🦜 VieNeu-TTS v3 Turbo (server-vieneu.py, port 8767)

Giọng **100% local** (ONNX int8 trên CPU, torch-free — máy AMD không CUDA vẫn nhanh, RTF < 1):

- **14 giọng preset** theo 3 miền: Bắc (`Trúc Ly`, `Minh Đức`, `Đoan Trang`...), Trung (`Quang Sơn`, `Ngọc Trân`), Nam (`Xuân Vĩnh`, `Thùy Dung`...)
- **3 style đọc**: `tu_nhien` (tự nhiên) · `tin_tuc` (tin tức) · `doc_truyen` (kể chuyện)
- **Emotion cues** ngay trong text: `[cười]`, `[thở dài]`, `[hắng giọng]`
- **Clone giọng**: từ clip 3–8s (API nâng cao, xem `vieneu` SDK)
- Âm thanh 48kHz; `wav` native, `mp3/ogg/opus/webm/aac/flac` qua ffmpeg
- `speed` 0.5–2.0 (ffmpeg atempo, giữ nguyên pitch)

### Chạy

```bash
start-vieneu.bat        # tự setup venv lần đầu; server tại http://127.0.0.1:8767/
```

Test nhanh:

```bash
curl -X POST http://127.0.0.1:8767/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d "{\"voice\":\"Trúc Ly:doc_truyen\",\"input\":\"Ngày xửa ngày xưa...\",\"response_format\":\"mp3\"}" \
  -o truyen.mp3
```

Voice: `Trúc Ly`, `Trúc Ly:doc_truyen`, `Mai Anh`, `Quang Sơn`... — xem đủ tại
`GET /v1/audio/voices` hoặc trang test `http://127.0.0.1:8767/`.

> 👩 **Giọng mặc định = `Trúc Ly`** (Nữ · Bắc · tự nhiên). Client không khai `voice`
> (hoặc dùng `alloy/nova/shimmer/...`) sẽ được giọng nữ này. Muốn đổi mặc định, sửa
> `DEFAULT_VOICE` ở đầu `server-vieneu.py` rồi chạy lại server.

> ⚠️ Lần request đầu tiên sẽ tải model từ HuggingFace (vài trăm MB) — hãy đợi.
> Biến môi trường: `TTS_VIENEU_PORT` (8767), `TTS_VIENEU_HOST` (127.0.0.1).

---

## 🗣️ Edge TTS (server.py, port 8766)

Server **TTS + STT** 100% local (trừ giọng TTS Edge cần internet), giao tiếp theo **API chuẩn
OpenAI** nên cắm thẳng vào **Project AIRI**:

- 🗣️ **TTS:** giọng neural tiếng Việt của Microsoft Edge (miễn phí, chất lượng cao, cần internet)
- 👂 **STT:** faster-whisper chạy local CPU — không cần mạng sau khi đã tải model
  (`POST /v1/audio/transcriptions` + `/v1/audio/translations` — chuẩn OpenAI Whisper)

| Giọng | Giới tính | Ghi chú |
|---|---|---|
| `vi-VN-HoaiMyNeural` | Nữ | **Giọng nữ Việt chuẩn duy nhất** của Microsoft |
| `vi-VN-NamMinhNeural` | Nam | Giọng nam Việt chuẩn |
| `en-US-AvaMultilingualNeural` | Nữ | Đa ngữ, nói được tiếng Việt (accent Mỹ) |
| `en-US-EmmaMultilingualNeural` | Nữ | Đa ngữ, nói được tiếng Việt (accent Mỹ) |
| `fr-FR-VivienneMultilingualNeural` | Nữ | Đa ngữ, nói được tiếng Việt (accent Pháp) |
| `de-DE-SeraphinaMultilingualNeural` | Nữ | Đa ngữ, nói được tiếng Việt (accent Đức) |
| `pt-BR-ThalitaMultilingualNeural` | Nữ | Đa ngữ, nói được tiếng Việt (accent Brazil) |

> 💡 5 giọng nữ đa ngữ đã được **kiểm chứng thực nghiệm** nói được tiếng Việt qua Edge endpoint
> (nghe thử trong thư mục `demo/`). Giọng đa ngữ phát âm tiếng Việt khá chuẩn nhưng có chút
> accent nước ngoài — Hoài My vẫn là giọng Việt tự nhiên nhất. Muốn đổi giọng trong AIRI chỉ cần
> gõ tên giọng vào ô voice, vd `en-US-AvaMultilingualNeural` hoặc alias `ava`.

So với Piper (`tts-vi`): tự nhiên hơn hẳn, đọc tốt cả từ tiếng Anh lẫn trong câu Việt, 2 giọng
nam/nữ — nhưng không offline. → **Chạy song song**: Piper port 8765 (offline fallback),
Edge port 8766 (chất lượng cao).

---

## 🚀 Chạy

Nhấp đôi **`start-tts-edge.bat`** — lần đầu tự tạo venv + cài dependencies, rồi chạy server tại:

- API: `http://127.0.0.1:8766/v1/audio/speech`
- Trang test: `http://127.0.0.1:8766/` (nghe thử cả 2 giọng + style ngay trên web)

Test nhanh:

```bash
curl -X POST http://127.0.0.1:8766/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"tts-1\",\"voice\":\"vi-VN-HoaiMyNeural:cheerful\",\"input\":\"Chào cậu! Hôm nay mình vui lắm nè!\",\"response_format\":\"mp3\"}" \
  -o vui.mp3
```

Hỗ trợ `response_format`: `mp3` (mặc định), `wav`, `pcm`, `ogg`, `opus`, `webm`, `aac`, `flac`
(các định dạng ngoài mp3 cần **ffmpeg** — cài: `winget install ffmpeg`).
`speed` từ 0.5 → 2.0 (mặc định 1.3 — nhanh hơn 30%).

---

## 🎭 Style cảm xúc (preset pitch/rate)

Gõ voice dạng `giọng:style`, vd `vi-VN-HoaiMyNeural:cheerful`, `emma:gentle`, `ava` (alias):
`hoaimy` · `namminh` · `ava` · `emma` · `vivienne` · `seraphina` · `thalita`

Style: `normal` · `chat` · `cheerful` · `empathetic` · `gentle` · `sad` · `angry`

> ⚠️ Edge endpoint (free) **không hỗ trợ neural styles thật** (`mstts:express-as`) cho tiếng Việt
> (đã kiểm chứng: `StyleList=None`, service từ chối). "Style" ở đây là preset **pitch/rate**
> để giọng gần đúng cảm xúc. Muốn cảm xúc thật sự thì phải dùng Azure Speech có trả phí.

---

## 🔌 Kết nối với Project AIRI

1. Bật server (`start-tts-edge.bat`).
2. Trong AIRI: **Settings → Service Sources → Speech → OpenAI Compatible API**
   - **API Key:** nhập đại (vd `local`)
   - **Base URL:** `http://127.0.0.1:8766/v1`
   - **Model:** `tts-1`
   - **Voice:** `vi-VN-HoaiMyNeural` (hoặc `vi-VN-NamMinhNeural`)
3. Vào **Settings → Body Modules → Vocalization**:
   - Chọn service source mới → model `tts-1` → giọng tương ứng.
   - Bấm **Test voice** để nghe thử.
4. Xong — nhân vật nói giọng neural Việt. Nếu AIRI không liệt kê giọng, cứ gõ tay vào ô voice.

> 💡 Giữ Piper (port 8765) làm service source thứ hai để chạy offline khi mất mạng.

---

## 👂 STT (Speech-to-Text) — local, không cần mạng

Endpoints chuẩn OpenAI Whisper, chạy **faster-whisper trên CPU** (model tải tự động lần đầu
từ HuggingFace, cache ở `~/.cache/huggingface`):

| Endpoint | Mô tả |
|---|---|
| `POST /v1/audio/transcriptions` | Nhận dạng giọng nói → text (multipart: `file`, `model`, `language?`, `response_format?`, `prompt?`, `temperature?`) |
| `POST /v1/audio/translations` | Dịch giọng nói bất kỳ → tiếng Anh |

`response_format`: `json` (mặc định, `{"text": "..."}`) · `text` · `srt` · `vtt` · `verbose_json`

Cấu hình (biến môi trường):

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `STT_MODEL` | `small` | `tiny` · `base` · `small` · `medium` · `large-v3` (lớn hơn = chính xác hơn, chậm hơn; `small` cân bằng tốt cho tiếng Việt) |
| `STT_LANGUAGE` | `vi` | Ngôn ngữ ép buộc (tăng độ chính xác); `auto` = tự phát hiện |
| `STT_THREADS` | `4` | Số luồng CPU cho whisper |
| `STT_COMPUTE_TYPE` | `int8` | int8 nhanh + nhẹ RAM; float16 chính xác hơn chút |

Test nhanh (sinh audio bằng chính TTS rồi nhận dạng lại):

```bash
curl -X POST http://127.0.0.1:8766/v1/audio/speech -H "Content-Type: application/json" \
  -d "{\"voice\":\"vi-VN-HoaiMyNeural\",\"input\":\"Xin chào bạn, mình là trợ lý ảo.\"}" -o noi.mp3
curl -X POST http://127.0.0.1:8766/v1/audio/transcriptions \
  -F "file=@noi.mp3" -F "model=whisper-1" -F "language=vi"
# -> {"text":"Xin chào bạn, mình là trợ lý ảo."}
```

### 🔌 Kết nối STT với AIRI

1. Trong AIRI: **Settings → Providers → Speech Recognition → OpenAI Compatible API**
   - **API Key:** nhập đại (vd `local`)
   - **Base URL:** `http://127.0.0.1:8766/v1`
   - **Model:** `whisper-1`
2. Vào **Settings → Hearing** (听觉): chọn service source + model vừa tạo, chọn thiết bị micro.
3. Bấm **Start listening** (开始监听), nói thử — text sẽ hiện trong vùng transcription.

> 💡 Lần đầu bấm Start listening, AIRI gửi audio → server tải model whisper (~244MB) nên
> vài phút đầu hơi chậm; các lần sau đã có cache. Muốn nhẹ hơn đặt `STT_MODEL=base`
> trong `start-tts-edge.bat` trước dòng chạy server.

---

## ⚙️ Cấu hình (biến môi trường)

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `TTS_EDGE_PORT` | `8766` | Cổng server (Piper đang dùng 8765) |
| `TTS_EDGE_HOST` | `127.0.0.1` | Bind address |

## 🛠️ Cấu trúc & lưu ý

```
tts-edge/
├── server.py          # FastAPI + edge-tts + ffmpeg transcode
├── requirements.txt
├── start-tts-edge.bat # Tự setup venv + chạy (đã tắt PYTHONPATH)
└── README.md
```

- `.bat` đã chạy `set "PYTHONPATH="` — tránh bị trộn packages với venv khác khi chạy từ terminal
  đang export PYTHONPATH.
- edge-tts 7.x chỉ trả MP3 (`output_format` đã bị gỡ khỏi API) → server transcode qua ffmpeg.

## 🔧 Xử lý sự cố

- **Lỗi 502 từ Edge-TTS** — mất mạng / bị firewall chặn `speech.platform.bing.com`; thử lại sau.
- **Bị throttle** — server giới hạn 4 request đồng thời; đừng bấm test liên tục.
- **ffmpeg báo lỗi** — kiểm tra `winget install ffmpeg` rồi khởi động lại server.
- **Port 8766 bị chiếm** — tắt server cũ: `netstat -ano | grep 8766` rồi `taskkill /F /PID <pid>`.

### 🎯 Cải thiện chất lượng tiếng Việt (Prompt)

Server sử dụng **default Vietnamese prompt** chứa các từ/cụm từ trợ lý ảo hay dùng
(`"Xin chào! Mình là trợ lý ảo, rất vui được gặp bạn..."`) — cải thiện độ chính xác
từ **82.5% → 100%** trên test set tiếng Việt (đã kiểm chứng).

Nếu muốn tùy chỉnh prompt (ví dụ: thêm từ chuyên ngành), gửi `prompt` trong request:
```bash
curl -X POST http://127.0.0.1:8766/v1/audio/transcriptions \
  -F "file=@audio.mp3" -F "model=whisper-1" \
  -F "prompt=Xin chào, mình là trợ lý ảo. Bạn cần giúp đỡ gì?"
```

### 🔧 Xử lý sự cố

- **Lỗi 502 "Invalid data"** — AIRI gửi audio dạng WebM/Opus (MediaRecorder), server tự
  xử lý đúng dựa trên `Content-Type` (không phụ thuộc filename `recording.wav`).
- **Chậm lần đầu** — model whisper small (~244MB) tải từ HuggingFace; các lần sau dùng cache.
- **Không nhận dạng được** — kiểm tra AIRI gửi đúng `Content-Type: audio/webm` hoặc `audio/wav`.
