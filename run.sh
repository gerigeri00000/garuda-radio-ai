#!/usr/bin/env bash
# ===========================================================================
#  GARUDA Radio AI - run.sh  (macOS; 100% offline setelah setup)
#  Padanan run.bat untuk Windows.
# ===========================================================================
set -u
cd "$(dirname "$0")" || exit 1

VPY=".venv/bin/python"
if [ ! -x "$VPY" ]; then
    echo "[ERROR] Virtual environment belum ada. Jalankan ./setup.sh dulu."
    exit 1
fi

# Pastikan model ada (cek lokal, tidak mengunduh apa pun)
if ! "$VPY" setup_models.py status; then
    echo ""
    echo "[PERHATIAN] Model aktif (Whisper / LLM) belum lengkap - lihat baris 'aktif' di atas."
    echo "            Aplikasi tetap terbuka, tetapi transkripsi/analisis akan menolak"
    echo "            dengan pesan jelas. Unduh lewat ./setup.sh saat ada internet."
    echo ""
fi

# Mode offline: cegah library melakukan request jaringan tak terduga
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export GRADIO_ANALYTICS_ENABLED=False

# Batas lunak berkas terbuka dari Terminal macOS hanya 256; naikkan (bila masih
# di bawah 4096) supaya mode dengar terus yang berjalan berhari-hari tetap aman.
LIMIT="$(ulimit -n)"
if [ "$LIMIT" != "unlimited" ] && [ "$LIMIT" -lt 4096 ] 2>/dev/null; then
    ulimit -n 4096 2>/dev/null || true
fi

echo "Menjalankan GARUDA Radio AI di http://127.0.0.1:${GARUDA_PORT:-7860} ..."
# caffeinate -i: Mac tidak tidur saat idle selama aplikasi berjalan (mode dengar
# terus di Mac mini tidak terputus). Layar tetap boleh mati.
if command -v caffeinate >/dev/null 2>&1 && [ "${GARUDA_NO_CAFFEINATE:-0}" != "1" ]; then
    exec caffeinate -i "$VPY" app.py
fi
exec "$VPY" app.py
