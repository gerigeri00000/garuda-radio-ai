@echo off
REM ===========================================================================
REM  GARUDA Radio AI - run.bat  (100%% offline setelah setup)
REM ===========================================================================
setlocal
cd /d "%~dp0"
title GARUDA Radio AI

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment belum ada. Jalankan setup.bat dulu.
    pause & exit /b 1
)
set "VPY=.venv\Scripts\python.exe"

REM Pastikan model ada (cek lokal, tidak mengunduh apa pun)
"%VPY%" setup_models.py status
if errorlevel 1 (
    echo.
    echo [PERHATIAN] Model Whisper small / LLM 3B belum lengkap.
    echo             Aplikasi tetap terbuka, tetapi transkripsi/analisis akan menolak
    echo             dengan pesan jelas. Unduh lewat setup.bat saat ada internet.
    echo.
)

REM Mode offline: cegah library melakukan request jaringan tak terduga
set "HF_HUB_OFFLINE=1"
set "TRANSFORMERS_OFFLINE=1"
set "GRADIO_ANALYTICS_ENABLED=False"

echo Menjalankan GARUDA Radio AI di http://127.0.0.1:7860 ...
"%VPY%" app.py
pause
