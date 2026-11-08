"""
GARUDA Radio AI - audio & speech-to-text (faster-whisper, 100% offline).

- Audio dari potongan mikrofon (mode dengar terus), upload file, atau rekaman radio.
- Dikonversi ke 16 kHz mono WAV lalu ditranskripsikan dengan model lokal.
- Model TIDAK pernah diunduh otomatis: bila belum ada, raise ModelNotInstalled.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import config

SAMPLE_RATE = 16000


class ModelNotInstalled(RuntimeError):
    pass


_WHISPER = {}
_WHISPER_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Audio utilities
# ---------------------------------------------------------------------------
def find_ffmpeg() -> str | None:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg  # type: ignore

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def to_wav16k(src: str | Path, dst: str | Path | None = None) -> Path:
    """Konversi audio apa pun ke WAV mono 16 kHz.

    Pakai soundfile bila sudah didukung, selain itu ffmpeg (untuk mp3/m4a/webm).
    """
    src = Path(src)
    if dst is None:
        dst = Path(tempfile.mkstemp(suffix=".wav")[1])
    dst = Path(dst)

    try:
        import soundfile as sf

        data, sr = sf.read(str(src), always_2d=True)
        if sr != SAMPLE_RATE:
            data = _resample(data, sr, SAMPLE_RATE)
        mono = data.mean(axis=1) if data.shape[1] > 1 else data[:, 0]
        sf.write(str(dst), mono, SAMPLE_RATE, subtype="PCM_16")
        return dst
    except Exception:
        pass

    ff = find_ffmpeg()
    if not ff:
        raise RuntimeError(
            "File audio ini butuh ffmpeg untuk dikonversi (mp3/m4a/webm). "
            "Pasang ffmpeg dan pastikan ada di PATH, lalu coba lagi."
        )
    cmd = [ff, "-y", "-i", str(src), "-ac", "1", "-ar", str(SAMPLE_RATE), str(dst)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not dst.exists():
        raise RuntimeError(f"Gagal konversi audio: {proc.stderr[-400:]}")
    return dst


def _resample(data, sr_in: int, sr_out: int):
    import numpy as np

    if sr_in == sr_out:
        return data
    n_out = int(round(len(data) * sr_out / sr_in))
    x = np.linspace(0, 1, len(data), endpoint=False)
    x_new = np.linspace(0, 1, n_out, endpoint=False)
    ch = [np.interp(x_new, x, data[:, c]) for c in range(data.shape[1])]
    return np.stack(ch, axis=1)


def audio_duration(path: str | Path) -> float:
    try:
        import soundfile as sf

        return float(sf.info(str(path)).duration)
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Whisper
# ---------------------------------------------------------------------------
def _resolve_device(cfg: dict) -> tuple[str, str]:
    import ctranslate2

    dev = cfg.get("whisper_device", "auto")
    has_cuda = ctranslate2.get_cuda_device_count() > 0
    if dev == "auto":
        dev = "cuda" if has_cuda else "cpu"
    if dev == "cuda" and not has_cuda:
        dev = "cpu"
    compute = cfg.get("whisper_compute", "auto")
    if compute == "auto":
        compute = "float16" if dev == "cuda" else "int8"
    return dev, compute


def get_whisper(cfg: dict):
    """Load (cache) model faster-whisper lokal."""
    name = cfg.get("whisper_model", "small")
    if not config.whisper_installed(name):
        path = config.model_path(name, "whisper")
        raise ModelNotInstalled(
            f"Model Whisper '{name}' belum terinstal di {path}.\n"
            f"Jalankan:  setup.bat\n"
            f"(setup.bat mengunduh model ke folder models/ - aplikasi tidak mengunduh otomatis)"
        )
    device, compute = _resolve_device(cfg)
    key = (name, device, compute)
    with _WHISPER_LOCK:
        if key in _WHISPER:
            return _WHISPER[key]
        from faster_whisper import WhisperModel

        model = WhisperModel(
            str(config.model_path(name, "whisper")),
            device=device,
            compute_type=compute,
            local_files_only=True,
            cpu_threads=4,
        )
        _WHISPER.clear()
        _WHISPER[key] = model
        return model


def transcribe(path: str | Path, cfg: dict) -> dict:
    """Transkripsi satu file audio. Return dict teks + metrik."""
    wav = to_wav16k(path)
    model = get_whisper(cfg)
    t0 = time.perf_counter()
    segments, info = model.transcribe(
        str(wav),
        language=cfg.get("language", "id"),
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 400},
        beam_size=5,
        condition_on_previous_text=False,
        initial_prompt=(
            "Percakapan radio operasional bahasa Indonesia. "
            "Pos Alfa, Pos Bravo, Pos Charlie, Pos Delta, Gudang Logistik Pusat, "
            "solar, genset, logistik, dermaga."
        ),
    )
    parts, logprobs = [], []
    for seg in segments:
        txt = seg.text.strip()
        if txt:
            parts.append(txt)
            logprobs.append(seg.avg_logprob)
    elapsed = time.perf_counter() - t0
    text = " ".join(parts).strip()
    avg_lp = sum(logprobs) / len(logprobs) if logprobs else -1.0
    return {
        "text": text,
        "language": getattr(info, "language", cfg.get("language", "id")),
        "language_probability": float(getattr(info, "language_probability", 0.0) or 0.0),
        "duration": float(getattr(info, "duration", 0.0) or audio_duration(wav)),
        "avg_logprob": round(float(avg_lp), 3),
        "transcribe_seconds": round(elapsed, 2),
        "wav": str(wav),
    }


def list_input_devices() -> list[str]:
    """Daftar perangkat audio input (opsional; Gradio mic memakai device default browser)."""
    try:
        import sounddevice as sd

        names = []
        for d in sd.query_devices():
            if d.get("max_input_channels", 0) > 0:
                names.append(d["name"])
        return names or ["(tidak ada perangkat input terdeteksi)"]
    except Exception:
        return ["sounddevice tidak terpasang - pakai mikrofon browser / upload file"]


def record_from_device(device_index: int | None, seconds: int = 5) -> Path:
    """Rekam dari sound card / perangkat radio (line-in) di server. Opsional."""
    try:
        import sounddevice as sd
        import soundfile as sf
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("sounddevice belum terpasang (pip install sounddevice).") from exc
    out = Path(tempfile.mkstemp(suffix=".wav")[1])
    rec = sd.rec(int(seconds * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1,
                 dtype="int16", device=device_index)
    sd.wait()
    sf.write(str(out), rec, SAMPLE_RATE)
    return out
