"""
GARUDA Radio AI - konfigurasi terpusat.

Semua path, model, dan lokasi simulasi ada di sini supaya mudah diubah.
Tidak ada download model otomatis: model harus disiapkan lewat setup.bat
(Windows) atau setup.sh (macOS).
"""
from __future__ import annotations

import os
import platform
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

# ---------------------------------------------------------------------------
# Platform. Windows + NVIDIA = jalur asli (CUDA). macOS Apple Silicon memakai
# GPU terintegrasi lewat MLX (Whisper) dan Metal (llama.cpp).
# ---------------------------------------------------------------------------
IS_WINDOWS = os.name == "nt"
IS_MAC = sys.platform == "darwin"
# arm64 asli. Python x86_64 lewat Rosetta tidak bisa memakai MLX/Metal.
IS_APPLE_SILICON = IS_MAC and platform.machine() == "arm64"

# Perintah setup & interpreter venv sesuai platform (dipakai di pesan UI/error).
SETUP_CMD = "setup.bat" if IS_WINDOWS else "./setup.sh"
VENV_PY = r".venv\Scripts\python.exe" if IS_WINDOWS else ".venv/bin/python"


def _sysctl(key: str) -> str | None:
    try:
        out = subprocess.run(["sysctl", "-n", key], capture_output=True, text=True, timeout=5)
        val = out.stdout.strip()
        return val if out.returncode == 0 and val else None
    except Exception:
        return None


@lru_cache(maxsize=1)
def perf_cores() -> int | None:
    """Jumlah core performa (P-core) Apple Silicon, mis. 8 pada M4 Pro 12-core.

    E-core jauh lebih lambat: thread inferensi yang jatuh ke E-core membuat
    thread lain menunggu, jadi total lebih lambat. None di luar macOS.
    """
    if not IS_MAC:
        return None
    try:
        n = int(_sysctl("hw.perflevel0.physicalcpu") or 0)
    except ValueError:
        return None
    return n if n > 0 else None


def whisper_cpu_threads() -> int:
    """Thread CPU faster-whisper. Di luar macOS tetap 4 (nilai asli yang sudah diuji)."""
    return perf_cores() or 4


def llm_threads() -> int:
    """Thread CPU llama.cpp. Di luar macOS tetap nilai asli (jumlah core - 2)."""
    return perf_cores() or max(2, (os.cpu_count() or 4) - 2)


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
    # nama  ->  folder lokal di models/whisper/<nama>  (format CTranslate2, faster-whisper)
    "base": "models/whisper/base",
    "small": "models/whisper/small",
    "large-v3-turbo": "models/whisper/large-v3-turbo",
}

# Bobot format MLX (GPU Apple Silicon). Nama sama dengan WHISPER_MODELS supaya
# pilihan di UI tetap satu daftar; mesin transkripsi dipilih di audio.py.
WHISPER_MLX_MODELS = {
    "base": "models/whisper-mlx/base",
    "small": "models/whisper-mlx/small",
    "large-v3-turbo": "models/whisper-mlx/large-v3-turbo",
}

LLM_MODELS = {
    # nama  ->  file GGUF di models/llm/
    "qwen2.5-1.5b-instruct-q4_k_m": "models/llm/qwen2.5-1.5b-instruct-q4_k_m.gguf",
    "qwen2.5-3b-instruct-q4_k_m": "models/llm/qwen2.5-3b-instruct-q4_k_m.gguf",
}


def _default_whisper_model() -> str:
    """Mac Apple Silicon: large-v3-turbo bila bobot MLX-nya terpasang dan paket
    mlx-whisper ada (paling akurat dan tetap ±0,5 s per klip di GPU M4 Pro).
    Selain itu small (asli). Bila MLX ternyata tidak bisa dimuat saat jalan,
    audio.whisper_backend() memakai small di CPU sebagai cadangan."""
    if not IS_APPLE_SILICON:
        return "small"
    import importlib.util

    turbo = BASE_DIR / WHISPER_MLX_MODELS["large-v3-turbo"]
    weights = (turbo / "weights.safetensors").exists() or (turbo / "weights.npz").exists()
    try:
        has_pkg = importlib.util.find_spec("mlx_whisper") is not None
    except Exception:
        has_pkg = False
    if (turbo / "config.json").exists() and weights and has_pkg:
        return "large-v3-turbo"
    return "small"


# Perangkat Whisper yang berlaku di platform ini (pilihan di tab Pengaturan).
WHISPER_DEVICES = ("auto", "mlx", "cpu") if IS_APPLE_SILICON else ("auto", "cuda", "cpu")

DEFAULTS = {
    "whisper_model": _default_whisper_model(),
    "whisper_device": "auto",      # auto | cuda | cpu | mlx (GPU Apple Silicon)
    "whisper_compute": "auto",     # auto | float16 | int8_float16 | int8
    "llm_model": "qwen2.5-3b-instruct-q4_k_m",
    "llm_ctx": 4096,
    "llm_gpu_layers": -1,          # -1 = semua layer ke GPU bila tersedia
    # True: generate tanpa grammar JSON dulu, grammar hanya bila keluaran bukan
    # objek JSON (diukur di M4 Pro: 50/50 valid, analisis ~2x lebih cepat).
    # False = perilaku asli (selalu grammar). Default menyala hanya di Apple
    # Silicon karena baru diverifikasi di sana.
    "llm_fast_json": IS_APPLE_SILICON,
    # Muat model di latar belakang saat app mulai supaya transmisi pertama
    # tidak menanggung waktu muat/kompilasi kernel GPU (lihat pipeline.warmup).
    "warmup": IS_APPLE_SILICON,
    "llm_timeout": 120,
    "language": "id",
}

# Aksi yang boleh langsung berlaku di mode dengar-terus ("jalankan terus").
# Aksi di luar daftar ini TETAP menunggu persetujuan operator:
#   CREATE_TICKET -> Pending, UPDATE_MAP -> Pending Approval, UPDATE_STATUS -> usulan.
AUTO_APPLY_ACTIONS = {"CREATE_ALERT"}

# Lama sunyi (detik) sebelum satu transmisi dianggap selesai dan dipotong.
SILENCE_SECONDS = 3.0

# Aset VAD browser (deteksi suara) - diunduh sekali lewat setup_models.py vad.
# Versi dipin supaya nama file aset tidak bergeser tanpa disadari.
VAD_ASSET_VERSION = "0.0.9"
VAD_ONNXRT_VERSION = "1.18.0"
VAD_DIR = MODELS_DIR / "vad"

VAD_REQUIRED_FILES = [
    "silero_vad.onnx",
    "vad-web.bundle.min.js",
    "vad.worklet.bundle.min.js",
    "ort.min.js",
    "ort-wasm-simd-threaded.wasm",
    # Cadangan untuk halaman tanpa cross-origin isolation (tanpa SAB).
    "ort-wasm-simd.wasm",
]


def vad_assets_ready() -> bool:
    """True bila semua aset VAD ada. Tidak pernah crash."""
    try:
        return all((VAD_DIR / f).exists() for f in VAD_REQUIRED_FILES)
    except Exception:
        return False



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
    # Perangkat yang tidak berlaku di platform ini (mis. config.json "cuda" dibawa
    # dari laptop Windows ke Mac) -> auto, supaya UI, status, dan mesin sejalan.
    if cfg.get("whisper_device") not in WHISPER_DEVICES:
        cfg["whisper_device"] = "auto"
    return cfg


def _saved_keys() -> set[str]:
    try:
        import json

        saved = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        return set(saved) if isinstance(saved, dict) else set()
    except Exception:
        return set()


def save_config(cfg: dict) -> None:
    """Simpan hanya nilai yang memang diubah (beda dari default) atau yang sudah
    ada di config.json. Default yang bergantung platform/instalasi
    (whisper_model, llm_fast_json, warmup) jadi tidak terkunci di berkas."""
    import json

    on_disk = _saved_keys()
    clean = {k: cfg[k] for k in DEFAULTS if k in cfg and (k in on_disk or cfg[k] != DEFAULTS[k])}
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
    """Path absolut model lokal. kind: whisper | whisper-mlx | llm."""
    if kind == "whisper":
        return BASE_DIR / WHISPER_MODELS.get(name, f"models/whisper/{name}")
    if kind == "whisper-mlx":
        return BASE_DIR / WHISPER_MLX_MODELS.get(name, f"models/whisper-mlx/{name}")
    return BASE_DIR / LLM_MODELS.get(name, f"models/llm/{name}.gguf")


def whisper_installed(name: str) -> bool:
    p = model_path(name, "whisper")
    return (p / "model.bin").exists()


def whisper_mlx_installed(name: str) -> bool:
    """True bila bobot MLX lengkap (config.json + weights.safetensors/npz)."""
    p = model_path(name, "whisper-mlx")
    return (p / "config.json").exists() and (
        (p / "weights.safetensors").exists() or (p / "weights.npz").exists())


def llm_installed(name: str) -> bool:
    p = model_path(name, "llm")
    return p.exists() and p.stat().st_size > 100 * 1024 * 1024


def device_info() -> dict:
    """Info runtime untuk ditampilkan di UI (tidak meng-crash bila tidak ada GPU)."""
    info = {"cuda": False, "gpu": None, "llama_cpp": False, "llama_cpp_gpu": False,
            "apple_silicon": IS_APPLE_SILICON, "mlx": False, "mlx_whisper": False,
            "perf_cores": perf_cores()}
    try:
        import ctranslate2

        info["cuda"] = ctranslate2.get_cuda_device_count() > 0
    except Exception:
        pass
    if IS_MAC:
        chip = _sysctl("machdep.cpu.brand_string")
        if chip and IS_APPLE_SILICON:
            info["gpu"] = f"{chip} (GPU terintegrasi, Metal)"
        try:
            import mlx.core as mx

            info["mlx"] = bool(mx.metal.is_available())
        except Exception:
            pass
        try:
            import importlib.util

            info["mlx_whisper"] = importlib.util.find_spec("mlx_whisper") is not None
        except Exception:
            pass
    else:
        try:
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
