"""
GARUDA Radio AI - konfigurasi terpusat.

Semua path, model, dan lokasi simulasi ada di sini supaya mudah diubah.
Tidak ada download model otomatis: model harus disiapkan lewat setup.bat.
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
MODELS_DIR = BASE_DIR / "models"
SAMPLES_DIR = BASE_DIR / "samples"
AUDIO_DIR = DATA_DIR / "audio"
EXPORT_DIR = DATA_DIR / "exports"
DB_PATH = DATA_DIR / "garuda.db"
CONFIG_FILE = DATA_DIR / "config.json"

for _d in (DATA_DIR, AUDIO_DIR, EXPORT_DIR, MODELS_DIR, SAMPLES_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Model (semua lokal, tidak ada API cloud)
# ---------------------------------------------------------------------------
WHISPER_MODELS = {
    # nama  ->  folder lokal di models/whisper/<nama>
    "base": "models/whisper/base",
    "small": "models/whisper/small",
    "large-v3-turbo": "models/whisper/large-v3-turbo",
}

LLM_MODELS = {
    # nama  ->  file GGUF di models/llm/
    "qwen2.5-1.5b-instruct-q4_k_m": "models/llm/qwen2.5-1.5b-instruct-q4_k_m.gguf",
    "qwen2.5-3b-instruct-q4_k_m": "models/llm/qwen2.5-3b-instruct-q4_k_m.gguf",
}

DEFAULTS = {
    "whisper_model": "small",
    "whisper_device": "auto",      # auto | cuda | cpu
    "whisper_compute": "auto",     # auto | float16 | int8_float16 | int8
    "llm_model": "qwen2.5-3b-instruct-q4_k_m",
    "llm_ctx": 4096,
    "llm_gpu_layers": -1,          # -1 = semua layer ke GPU bila tersedia
    "llm_timeout": 120,
    "language": "id",
}


def load_config() -> dict:
    """Gabungkan default dengan config.json (bila ada)."""
    cfg = dict(DEFAULTS)
    if CONFIG_FILE.exists():
        try:
            import json

            saved = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                cfg.update({k: v for k, v in saved.items() if k in DEFAULTS})
        except Exception:
            pass
    return cfg


def save_config(cfg: dict) -> None:
    import json

    clean = {k: cfg[k] for k in DEFAULTS if k in cfg}
    CONFIG_FILE.write_text(json.dumps(clean, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# Lokasi simulasi (DEMO DATA). Koordinat sengaja kasar & fiktif.
# AI hanya boleh memilih dari daftar ini; AI tidak pernah mengarang koordinat.
# ---------------------------------------------------------------------------
DEMO_LOCATIONS = {
    "pos alfa": {
        "name": "Pos Alfa",
        "lat": -6.9147,
        "lon": 107.6098,
        "kind": "pos",
        "note": "DEMO DATA - pos pengamatan (simulasi)",
    },
    "pos bravo": {
        "name": "Pos Bravo",
        "lat": -6.9321,
        "lon": 107.6042,
        "kind": "pos",
        "note": "DEMO DATA - pos pengamatan (simulasi)",
    },
    "pos charlie": {
        "name": "Pos Charlie",
        "lat": -6.8951,
        "lon": 107.6221,
        "kind": "pos",
        "note": "DEMO DATA - pos pengamatan (simulasi)",
    },
    "pos delta": {
        "name": "Pos Delta",
        "lat": -6.9512,
        "lon": 107.6310,
        "kind": "pos",
        "note": "DEMO DATA - pos cadangan (simulasi)",
    },
    "gudang logistik pusat": {
        "name": "Gudang Logistik Pusat",
        "lat": -6.9255,
        "lon": 107.5850,
        "kind": "fasilitas",
        "note": "DEMO DATA - fasilitas logistik (simulasi)",
    },
    "pos medis lapangan": {
        "name": "Pos Medis Lapangan",
        "lat": -6.9080,
        "lon": 107.6355,
        "kind": "fasilitas",
        "note": "DEMO DATA - fasilitas medis (simulasi)",
    },
    "jembatan desa contoh": {
        "name": "Jembatan Desa Contoh",
        "lat": -6.9420,
        "lon": 107.5905,
        "kind": "infrastruktur",
        "note": "DEMO DATA - jembatan (simulasi)",
    },
    "dermaga timur": {
        "name": "Dermaga Timur",
        "lat": -6.8805,
        "lon": 107.6150,
        "kind": "infrastruktur",
        "note": "DEMO DATA - dermaga (simulasi)",
    },
}

# Titik pusat peta
MAP_CENTER = (-6.9165, 107.6105)
MAP_ZOOM = 13

APP_TITLE = "GARUDA Radio AI"
APP_SUBTITLE = "Radio monitoring lokal - transkripsi, analisis, dan tindakan otomatis dengan persetujuan operator"
DEMO_BANNER = "SEMUA DATA LOKASI = DEMO DATA. Tidak ada komunikasi militer asli."


def model_path(name: str, kind: str) -> Path:
    """Path absolut model lokal."""
    if kind == "whisper":
        return BASE_DIR / WHISPER_MODELS.get(name, f"models/whisper/{name}")
    return BASE_DIR / LLM_MODELS.get(name, f"models/llm/{name}.gguf")


def whisper_installed(name: str) -> bool:
    p = model_path(name, "whisper")
    return (p / "model.bin").exists()


def llm_installed(name: str) -> bool:
    p = model_path(name, "llm")
    return p.exists() and p.stat().st_size > 100 * 1024 * 1024


def device_info() -> dict:
    """Info runtime untuk ditampilkan di UI (tidak meng-crash bila tidak ada GPU)."""
    info = {"cuda": False, "gpu": None, "llama_cpp": False, "llama_cpp_gpu": False}
    try:
        import ctranslate2

        info["cuda"] = ctranslate2.get_cuda_device_count() > 0
    except Exception:
        pass
    try:
        import subprocess

        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0 and out.stdout.strip():
            info["gpu"] = out.stdout.strip().splitlines()[0]
            info["cuda"] = True
    except Exception:
        pass
    try:
        import llama_cpp

        info["llama_cpp"] = True
        info["llama_cpp_gpu"] = bool(getattr(llama_cpp, "llama_supports_gpu_offload", lambda: False)())
    except Exception:
        pass
    return info
