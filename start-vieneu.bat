@echo off
setlocal
cd /d %~dp0

rem QUAN TRONG: bo PYTHONPATH de khong bi tron packages voi venv khac
set "PYTHONPATH="

rem Tim Python 3.11 (thu launcher "py" truoc, fallback "python")
set PY_CMD=
py -3.11 --version >nul 2>nul && set PY_CMD=py -3.11
if not defined PY_CMD (
  python --version >nul 2>nul && set PY_CMD=python
)
if not defined PY_CMD (
  echo Khong tim thay Python. Cai Python 3.11 tu python.org roi chay lai.
  pause
  exit /b 1
)

if not exist .venv (
  echo Tao venv lan dau...
  %PY_CMD% -m venv .venv || (echo Loi tao venv & pause & exit /b 1)
  .venv\Scripts\python -m pip install --upgrade pip
  .venv\Scripts\pip install -r requirements.txt || (echo Loi cai dependencies & pause & exit /b 1)
)

echo Chay tts-vieneu tai http://127.0.0.1:8767/ (Ctrl+C de dung)
echo Lan dau tien request se tai model tu HuggingFace - kien nhan nhe!
.venv\Scripts\python server-vieneu.py
pause
