@echo off
REM ===========================================================================
REM  GARUDA Radio AI - setup.bat
REM  Menyiapkan virtual environment, dependensi, dan model lokal.
REM  Diperlukan internet HANYA saat setup. Setelah itu aplikasi 100%% offline.
REM ===========================================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0"
title GARUDA Radio AI - Setup

echo ============================================================
echo   GARUDA Radio AI - SETUP
echo ============================================================
echo.

REM --- 1. Cari Python -------------------------------------------------------
set "PY="
where py >nul 2>nul && set "PY=py -3.12"
if not defined PY ( where python >nul 2>nul && set "PY=python" )
if not defined PY (
    echo [ERROR] Python tidak ditemukan. Pasang Python 3.11 atau 3.12 dari python.org
    echo         lalu jalankan setup.bat lagi.
    pause & exit /b 1
)
echo [1/5] Python: %PY%
%PY% -c "import sys; assert sys.version_info >= (3,11), 'butuh 3.11+'" || (
    echo [ERROR] Butuh Python 3.11 atau lebih baru.
    pause & exit /b 1
)

REM --- 2. Virtual environment ------------------------------------------------
if not exist ".venv\Scripts\python.exe" (
    echo [2/5] Membuat virtual environment .venv ...
    %PY% -m venv .venv
    if errorlevel 1 ( echo [ERROR] Gagal membuat venv. & pause & exit /b 1 )
) else (
    echo [2/5] Virtual environment .venv sudah ada.
)
set "VPY=.venv\Scripts\python.exe"

REM --- 3. Dependensi ---------------------------------------------------------
echo [3/5] Memasang dependensi (butuh internet) ...
"%VPY%" -m pip install --upgrade pip -q
"%VPY%" -m pip install -r requirements.txt
if errorlevel 1 ( echo [ERROR] Gagal memasang dependensi. & pause & exit /b 1 )

echo.
echo   --- llama-cpp-python ---
echo   Build CUDA (disarankan, RTX 4050 = CUDA 12.4/12.5):
"%VPY%" -m pip install llama-cpp-python==0.3.36 --only-binary=:all: ^
    --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124
if errorlevel 1 (
    echo   [perhatian] CUDA wheel gagal. Pakai build CPU (lebih lambat tapi tetap jalan):
    "%VPY%" -m pip install llama-cpp-python==0.3.36 --only-binary=:all: ^
        --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu
)
"%VPY%" -c "import llama_cpp; print('   llama-cpp-python OK, GPU offload:', llama_cpp.llama_supports_gpu_offload())"

REM --- ffmpeg (untuk mp3/m4a) ----------------------------------------------
where ffmpeg >nul 2>nul
if errorlevel 1 (
    echo   [info] ffmpeg tidak ada di PATH. Format wav tetap didukung.
    echo          Untuk mp3/m4a: winget install Gyan.FFmpeg  lalu buka cmd baru.
)

REM --- 4. Model --------------------------------------------------------------
echo.
echo [4/5] Model lokal (diunduh ke folder models/ - HANYA saat setup)
set /p DL="      Unduh model sekarang? Whisper small + Qwen2.5 3B (~2.6 GB) [y/N]: "
if /i "!DL!"=="y" (
    "%VPY%" setup_models.py whisper-small
    "%VPY%" setup_models.py llm-3b
    "%VPY%" setup_models.py tts-id
    echo.
    echo      Opsional (lebih akurat, VRAM ~1.6 GB): Whisper Large-v3-Turbo
    set /p TURBO="      Unduh Whisper large-v3-turbo sekarang? (~1.6 GB) [y/N]: "
    if /i "!TURBO!"=="y" "%VPY%" setup_models.py whisper-large-v3-turbo
) else (
    echo      Dilewati. Unduh manual nanti dengan:
    echo        .venv\Scripts\python.exe setup_models.py whisper-small llm-3b tts-id
)

echo.
echo   --- Status model ---
"%VPY%" setup_models.py status

REM --- 5. Struktur data ------------------------------------------------------
if not exist "data" mkdir data
if not exist "data\audio" mkdir data\audio
if not exist "data\exports" mkdir data\exports
if not exist "samples" mkdir samples

echo.
echo [5/5] Selesai.
echo   Jalankan:  run.bat
echo   Catatan  : setelah model terunduh, aplikasi berjalan tanpa internet.
echo.
pause
