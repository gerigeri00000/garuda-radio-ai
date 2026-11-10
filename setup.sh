#!/usr/bin/env bash
# ===========================================================================
#  GARUDA Radio AI - setup.sh  (macOS; dioptimalkan untuk Apple Silicon M1-M4)
#  Padanan setup.bat untuk Windows. Menyiapkan virtual environment,
#  dependensi, dan model lokal. Internet HANYA dibutuhkan saat setup;
#  setelah itu aplikasi 100% offline.
#
#  Apple Silicon:
#    - Whisper di GPU lewat MLX (mlx-whisper, butuh macOS 14+); cadangan CPU
#      faster-whisper.
#    - llama-cpp-python dibangun dengan Metal (semua layer LLM di GPU).
#
#  Non-interaktif : GARUDA_DOWNLOAD_MODELS=y|n ./setup.sh
#  Python tertentu: GARUDA_PYTHON=/opt/homebrew/bin/python3.12 ./setup.sh
# ===========================================================================
set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)" || exit 1
cd "$SCRIPT_DIR" || exit 1

LLAMA_VERSION="0.3.36"        # sama dengan setup.bat
MLX_WHISPER_VERSION="0.4.3"
VPY=".venv/bin/python"

say()  { printf '%s\n' "$*"; }
fail() { printf '[ERROR] %s\n' "$*" >&2; exit 1; }

# Perangkat keras Apple Silicon? hw.optional.arm64 bernilai 1 bahkan di bawah Rosetta.
APPLE_SILICON=0
if [ "$(uname -s)" = "Darwin" ] && [ "$(sysctl -n hw.optional.arm64 2>/dev/null)" = "1" ]; then
    APPLE_SILICON=1
fi
# Terminal lewat Rosetta (x86_64): GPU Apple (Metal/MLX) hanya bisa dipakai dari
# proses arm64, jadi skrip ini dijalankan ulang sebagai arm64 asli.
if [ "$APPLE_SILICON" = "1" ] && [ "$(sysctl -n sysctl.proc_translated 2>/dev/null)" = "1" ]; then
    if [ "${GARUDA_NO_REEXEC:-0}" != "1" ]; then
        say "[info] Terminal berjalan lewat Rosetta - menjalankan ulang setup sebagai arm64 ..."
        GARUDA_NO_REEXEC=1 exec arch -arm64 /bin/bash "$SCRIPT_DIR/setup.sh" "$@"
    fi
    fail "Tidak bisa berjalan sebagai arm64. Buka Terminal biasa (bukan Rosetta) lalu jalankan ulang."
fi

say "============================================================"
say "  GARUDA Radio AI - SETUP (macOS)"
say "============================================================"
say ""
if [ "$APPLE_SILICON" = "1" ]; then
    CHIP="$(sysctl -n machdep.cpu.brand_string 2>/dev/null)"
    say "Perangkat: ${CHIP:-Apple Silicon} - P-core: $(sysctl -n hw.perflevel0.physicalcpu 2>/dev/null)"
else
    say "Perangkat: $(uname -s) $(uname -m) - tanpa GPU Apple, transkripsi & LLM di CPU."
fi
say ""

# Benar bila $1 adalah Python 3.11/3.12 (dan arm64 di Apple Silicon).
py_ok() {
    "$1" -c 'import sys, platform
ok = (3, 11) <= sys.version_info[:2] <= (3, 12)
if sys.argv[1] == "1":
    ok = ok and platform.machine() == "arm64"
sys.exit(0 if ok else 1)' "$APPLE_SILICON" >/dev/null 2>&1
}
if [ "$APPLE_SILICON" = "1" ]; then WANT="Python 3.11/3.12 arm64"; else WANT="Python 3.11/3.12"; fi

# --- 1. Python 3.11 / 3.12 + virtual environment ---------------------------
if [ -x "$VPY" ]; then
    say "[1/6] Virtual environment .venv sudah ada."
    py_ok "$VPY" || fail "Python di .venv bukan $WANT (dependensi butuh numpy<2; MLX/Metal butuh arm64).
        Buat ulang:  rm -rf .venv && GARUDA_PYTHON=/opt/homebrew/bin/python3.12 ./setup.sh"
else
    PY=""
    if [ -n "${GARUDA_PYTHON:-}" ]; then
        py_ok "$GARUDA_PYTHON" || fail "GARUDA_PYTHON=$GARUDA_PYTHON bukan $WANT."
        PY="$GARUDA_PYTHON"
    else
        # Homebrew arm64 dulu: python3.x pertama di PATH bisa saja build x86_64 (Intel lama).
        for cand in /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.11 \
                    "$(command -v python3.12 2>/dev/null)" "$(command -v python3.11 2>/dev/null)"; do
            if [ -n "$cand" ] && [ -x "$cand" ] && py_ok "$cand"; then
                PY="$cand"
                break
            fi
        done
    fi
    if [ -n "$PY" ]; then
        say "[1/6] Membuat virtual environment .venv dengan $PY ..."
        "$PY" -m venv .venv || fail "Gagal membuat venv."
    elif command -v uv >/dev/null 2>&1; then
        say "[1/6] $WANT tidak ditemukan - memakai uv (mengunduh Python 3.12) ..."
        uv venv --python 3.12 --seed .venv || fail "uv gagal membuat venv."
        py_ok "$VPY" || fail "Python dari uv bukan $WANT. Hapus .venv lalu pasang: brew install python@3.12"
    else
        fail "$WANT tidak ditemukan. Pasang salah satu lalu jalankan ulang:
          brew install python@3.12      (atau: brew install uv)"
    fi
fi
say "      Python: $("$VPY" -c 'import sys, platform; print(sys.version.split()[0], platform.machine())')"

# --- 2. Dependensi dasar (sama dengan Windows) ------------------------------
say "[2/6] Memasang dependensi (butuh internet) ..."
"$VPY" -m pip install --upgrade pip -q || fail "Gagal memperbarui pip."
"$VPY" -m pip install -r requirements.txt || fail "Gagal memasang requirements.txt."
"$VPY" -c 'import numpy' 2>/dev/null \
    || fail "numpy tidak bisa dimuat (venv campuran arsitektur?). Hapus .venv lalu jalankan ulang."

# --- 3. Tambahan Apple Silicon: Whisper di GPU (MLX) ------------------------
MLX_OK=0
if [ "$APPLE_SILICON" = "1" ]; then
    say "[3/6] Memasang MLX + mlx-whisper (Whisper di GPU Apple) ..."
    # --no-deps untuk mlx-whisper: lihat catatan torch di requirements-mac.txt
    if "$VPY" -m pip install -r requirements-mac.txt \
            && "$VPY" -m pip install --no-deps "mlx-whisper==$MLX_WHISPER_VERSION"; then
        if "$VPY" -c 'import sys, mlx.core as mx, mlx_whisper
ok = mx.metal.is_available()
print("      MLX OK, GPU Metal:", ok)
sys.exit(0 if ok else 1)'; then
            MLX_OK=1
        else
            say "  [perhatian] MLX/Metal tidak bisa dipakai - transkripsi memakai CPU (faster-whisper)."
        fi
    else
        say "  [perhatian] MLX tidak bisa dipasang (MLX butuh macOS 14+) - transkripsi memakai"
        say "              CPU (faster-whisper). Langkah lain tetap dilanjutkan."
    fi
else
    say "[3/6] Bukan Apple Silicon - MLX dilewati (transkripsi CPU, faster-whisper)."
fi

# --- 4. llama-cpp-python ------------------------------------------------------
say "[4/6] llama-cpp-python $LLAMA_VERSION ..."
llama_ok() {
    "$VPY" -c 'import sys, llama_cpp
gpu = sys.argv[2] == "0" or llama_cpp.llama_supports_gpu_offload()
sys.exit(0 if llama_cpp.__version__ == sys.argv[1] and gpu else 1)' "$LLAMA_VERSION" "$APPLE_SILICON" 2>/dev/null
}
if llama_ok; then
    say "      sudah terpasang."
else
    # Dependensi Python-nya dulu (numpy sudah dipin oleh requirements.txt), lalu
    # paketnya sendiri dengan --no-deps supaya numpy tidak ikut ter-upgrade.
    "$VPY" -m pip install -q "typing-extensions>=4.5.0" "diskcache>=5.6.1" "jinja2>=2.11.3"
    if [ "$APPLE_SILICON" = "1" ]; then
        xcode-select -p >/dev/null 2>&1 || say "  [perhatian] Xcode Command Line Tools belum ada: jalankan  xcode-select --install"
        # Wheel Metal siap pakai untuk 0.3.36 di index abetlen rusak (Bad CRC-32),
        # jadi dibangun dari source dengan Metal (+-1 menit di M4 Pro).
        say "      Build dengan Metal dari source ..."
        CMAKE_ARGS="-DGGML_METAL=on" "$VPY" -m pip install --no-cache-dir --force-reinstall --no-deps \
            --no-binary llama-cpp-python "llama-cpp-python==$LLAMA_VERSION" \
        || "$VPY" -m pip install --force-reinstall --no-deps --only-binary=:all: \
            "llama-cpp-python==$LLAMA_VERSION" \
            --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/metal
    else
        "$VPY" -m pip install --force-reinstall --no-deps --only-binary=:all: \
            "llama-cpp-python==$LLAMA_VERSION" \
            --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu \
        || "$VPY" -m pip install --no-cache-dir --force-reinstall --no-deps \
            --no-binary llama-cpp-python "llama-cpp-python==$LLAMA_VERSION"
    fi
    if llama_ok; then
        say "      OK"
    elif "$VPY" -c 'import llama_cpp' 2>/dev/null; then
        say "  [perhatian] llama-cpp-python terpasang tanpa Metal / versi berbeda - LLM berjalan"
        say "              di CPU (lebih lambat)."
    else
        say "  [perhatian] llama-cpp-python belum terpasang: laporan disimpan BELUM TERKLASIFIKASI"
        say "              (UNKNOWN, tanpa tiket/alert otomatis). Pasang Xcode Command Line Tools"
        say "              (xcode-select --install) lalu jalankan ./setup.sh lagi."
    fi
fi
"$VPY" -c 'import llama_cpp; print("      llama-cpp-python", llama_cpp.__version__, "- GPU offload:", llama_cpp.llama_supports_gpu_offload())' 2>/dev/null

# --- ffmpeg (untuk mp3/m4a) ------------------------------------------------
if ! command -v ffmpeg >/dev/null 2>&1; then
    say "  [info] ffmpeg tidak ada di PATH. Format wav tetap didukung."
    say "         Untuk mp3/m4a/webm: brew install ffmpeg"
fi

# --- 5. Model ----------------------------------------------------------------
say ""
say "[5/6] Model lokal (diunduh ke folder models/ - HANYA saat setup)"
if [ "$MLX_OK" = "1" ]; then
    MODELS="whisper-mlx-large-v3-turbo whisper-small llm-3b tts-id vad"
    DESC="Whisper large-v3-turbo (MLX/GPU) + small (cadangan CPU) + Qwen2.5 3B + TTS + VAD (~4.3 GB)"
else
    MODELS="whisper-small llm-3b tts-id vad"
    DESC="Whisper small + Qwen2.5 3B + TTS + VAD (~2.7 GB)"
fi
DL="${GARUDA_DOWNLOAD_MODELS:-}"
if [ -z "$DL" ]; then
    if [ -t 0 ]; then
        printf '      Unduh model sekarang? %s [y/N]: ' "$DESC"
        read -r DL
    else
        DL="n"
    fi
fi
case "$DL" in
    y|Y|ya|Ya)
        for m in $MODELS; do
            "$VPY" setup_models.py "$m" || say "  [perhatian] gagal mengunduh $m"
        done
        ;;
    *)
        say "      Dilewati. Unduh manual nanti dengan:"
        say "        $VPY setup_models.py $MODELS"
        ;;
esac

say ""
say "  --- Status model ---"
"$VPY" setup_models.py status

# --- 6. Selesai ----------------------------------------------------------------
mkdir -p data/audio data/exports samples
say ""
say "[6/6] Selesai."
say "  Jalankan:  ./run.sh"
say "  Catatan  : setelah model terunduh, aplikasi berjalan tanpa internet."
