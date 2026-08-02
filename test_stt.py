"""Test STT roundtrip: TTS sinh tiếng Việt -> POST /v1/audio/transcriptions -> so sánh text.
Lần đầu chạy sẽ tải model whisper (small, ~244MB) từ HuggingFace.
"""
import json
import sys
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8766"
FAILS = []


def check(name, cond, extra=""):
    print(f"{'PASS' if cond else 'FAIL'} | {name} {extra}")
    if not cond:
        FAILS.append(name)


# 1. TTS sinh audio tiếng Việt
tts_payload = json.dumps({
    "model": "tts-1", "voice": "vi-VN-HoaiMyNeural",
    "input": "Xin chào bạn, mình là trợ lý ảo, rất vui được gặp bạn hôm nay.",
}, ensure_ascii=False).encode("utf-8")
req = urllib.request.Request(BASE + "/v1/audio/speech", data=tts_payload,
                             headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=120) as r:
    audio = r.read()
print(f"TTS audio: {len(audio)} bytes")

# 2. multipart POST tới /v1/audio/transcriptions
boundary = "----hermesstt" + "123456"
parts = []
parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"speech.mp3\"\r\n"
             f"Content-Type: audio/mpeg\r\n\r\n".encode("utf-8"))
parts.append(audio)
parts.append(f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\nwhisper-1\r\n".encode())
parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"language\"\r\n\r\nvi\r\n".encode())
parts.append(f"--{boundary}--\r\n".encode())
body = b"".join(parts)

req2 = urllib.request.Request(BASE + "/v1/audio/transcriptions", data=body, method="POST",
                              headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
t0 = __import__("time").time()
with urllib.request.urlopen(req2, timeout=600) as r:
    res = json.load(r)
dt = __import__("time").time() - t0
text = res.get("text", "")
print(f"STT ({dt:.1f}s): {text!r}")
check("transcriptions trả text không rỗng", bool(text.strip()), f"({len(text)} ký tự)")
check("text chứa từ khóa tiếng Việt", any(k in text.lower() for k in ["xin chào", "trợ lý", "hôm nay", "bạn"]),
      f"(đầu text: {text[:60]!r})")

# 3. translations (tiếng Việt -> tiếng Anh)
parts3 = []
parts3.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"speech.mp3\"\r\n"
              f"Content-Type: audio/mpeg\r\n\r\n".encode("utf-8"))
parts3.append(audio)
parts3.append(f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\nwhisper-1\r\n".encode())
parts3.append(f"--{boundary}--\r\n".encode())
req3 = urllib.request.Request(BASE + "/v1/audio/translations", data=b"".join(parts3), method="POST",
                              headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
with urllib.request.urlopen(req3, timeout=600) as r:
    tr = json.load(r)
print(f"TRANSLATE: {tr.get('text', '')!r}")
check("translations ra tiếng Anh", any(k in tr.get("text", "").lower() for k in ["hello", "assistant", "today"]),
      f"(đầu: {tr.get('text', '')[:60]!r})")

# 4. response_format=text
parts4 = []
parts4.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"speech.mp3\"\r\n"
              f"Content-Type: audio/mpeg\r\n\r\n".encode("utf-8"))
parts4.append(audio)
parts4.append(f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"response_format\"\r\n\r\ntext\r\n".encode())
parts4.append(f"--{boundary}--\r\n".encode())
req4 = urllib.request.Request(BASE + "/v1/audio/transcriptions", data=b"".join(parts4), method="POST",
                              headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
with urllib.request.urlopen(req4, timeout=600) as r:
    plain = r.read().decode("utf-8", errors="replace")
check("response_format=text (plain)", bool(plain.strip()), f"({len(plain)} ký tự)")

print("---")
if FAILS:
    print(f"RESULT: {len(FAILS)} FAIL -> {FAILS}")
    sys.exit(1)
print("RESULT: ALL PASS")
