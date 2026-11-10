"""
GARUDA Radio AI - audio & speech-to-text (100% offline).

- Audio dari potongan mikrofon (mode dengar terus), upload file, atau rekaman radio.
- Dikonversi ke 16 kHz mono WAV lalu ditranskripsikan dengan model lokal.
- Dua mesin transkripsi, dipilih otomatis oleh whisper_backend():
    * faster-whisper (CTranslate2): CPU / CUDA NVIDIA - jalur asli (Windows).
    * mlx-whisper: GPU Apple Silicon (Metal) lewat MLX - dipakai di Mac bila
      bobot MLX terpasang. Bila MLX gagal saat jalan, otomatis jatuh ke
      faster-whisper CPU supaya transkrip tidak hilang.
  Keduanya memakai VAD Silero, prompt awal, dan bahasa yang sama.
- Model TIDAK pernah diunduh otomatis: bila belum ada, raise ModelNotInstalled.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path

import config

SAMPLE_RATE = 16000

# Dipakai kedua mesin supaya hasilnya sebanding.
INITIAL_PROMPT = (
    "Percakapan radio operasional bahasa Indonesia. "
    "Pos Alfa, Pos Bravo, Pos Charlie, Pos Delta, Gudang Logistik Pusat, "
    "solar, genset, logistik, dermaga."
)
VAD_PARAMS = {"min_silence_duration_ms": 400}


class ModelNotInstalled(RuntimeError):
    pass


_WHISPER = {}
_WHISPER_LOCK = threading.Lock()

# MLX memakai stream GPU per thread: array yang dibuat di satu thread lalu baru
# dievaluasi di thread lain gagal ("There is no Stream(gpu, N) in current
# thread"), padahal transkripsi datang dari banyak thread (UI Gradio, pekerja
# mode dengar terus, pemanasan). Karena itu SEMUA kerja MLX dijalankan di satu
# thread khusus yang hidup terus - sekaligus menserialkan akses ke GPU.
_MLX_EXECUTOR: ThreadPoolExecutor | None = None
_MLX_EXECUTOR_LOCK = threading.Lock()
_MLX_THREAD_PREFIX = "garuda-mlx"


def _on_mlx_thread(fn, *args, **kwargs):
    """Jalankan fn di thread MLX khusus dan tunggu hasilnya (exception diteruskan)."""
    global _MLX_EXECUTOR
    if threading.current_thread().name.startswith(_MLX_THREAD_PREFIX):
        return fn(*args, **kwargs)  # sudah di thread MLX: jangan menunggu diri sendiri
    with _MLX_EXECUTOR_LOCK:
        if _MLX_EXECUTOR is None:
            _MLX_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix=_MLX_THREAD_PREFIX)
    return _MLX_EXECUTOR.submit(fn, *args, **kwargs).result()


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


def _temp_wav() -> Path:
    """Nama berkas WAV sementara. fd dari mkstemp WAJIB ditutup: bila dibiarkan,
    setiap klip membocorkan satu fd dan proses yang dijalankan dari Terminal macOS
    (batas lunak 256 berkas terbuka) mati setelah ±250 klip mode dengar terus."""
    fd, name = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    return Path(name)


def to_wav16k(src: str | Path, dst: str | Path | None = None) -> Path:
    """Konversi audio apa pun ke WAV mono 16 kHz.

    Pakai soundfile bila sudah didukung, selain itu ffmpeg (untuk mp3/m4a/webm).
    Tanpa dst: hasil di berkas sementara (dihapus lagi bila konversi gagal).
    """
    src = Path(src)
    if dst is not None:
        return _convert_wav16k(src, Path(dst))
    tmp = _temp_wav()
    try:
        return _convert_wav16k(src, tmp)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _convert_wav16k(src: Path, dst: Path) -> Path:
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
    """Perangkat & compute untuk faster-whisper (CTranslate2): cuda atau cpu."""
    import ctranslate2

    dev = cfg.get("whisper_device", "auto")
    has_cuda = ctranslate2.get_cuda_device_count() > 0
    if dev not in ("cuda", "cpu"):  # auto, atau mlx yang jatuh ke cadangan
        dev = "cuda" if has_cuda else "cpu"
    if dev == "cuda" and not has_cuda:
        dev = "cpu"
    compute = cfg.get("whisper_compute", "auto")
    if compute == "auto":
        compute = "float16" if dev == "cuda" else "int8"
    return dev, compute


@lru_cache(maxsize=1)
def mlx_available() -> bool:
    """True bila mlx-whisper bisa dipakai: Mac Apple Silicon + paket + GPU Metal.
    Alasan bila tidak bisa dicetak sekali ke log (hasil di-cache)."""
    if not config.IS_APPLE_SILICON:
        return False
    try:
        import mlx.core as mx
        import mlx_whisper  # noqa: F401
    except Exception as exc:
        print(f"[audio] mlx-whisper tidak bisa dimuat ({exc!r}); transkripsi memakai "
              "faster-whisper (CPU)", flush=True)
        return False
    try:
        ok = bool(mx.metal.is_available())
    except Exception:
        ok = False
    if not ok:
        print("[audio] GPU Metal tidak tersedia; transkripsi memakai faster-whisper (CPU)",
              flush=True)
    return ok


def _fw_backend(cfg: dict, name: str, note: str = "") -> dict:
    device, compute = _resolve_device(dict(cfg, whisper_model=name))
    return {"engine": "faster-whisper", "name": name, "device": device, "compute": compute,
            "label": f"{name} · faster-whisper {device} {compute}{note}"}


def whisper_backend(cfg: dict) -> dict:
    """Mesin transkripsi untuk cfg ini, tanpa memuat model.

    mlx dipakai bila perangkat 'auto'/'mlx', Mac Apple Silicon, dan bobot MLX
    model terpilih ada. Selain itu faster-whisper (jalur asli). engine=None
    berarti model belum terinstal untuk mesin mana pun. `name` hasil bisa beda
    dengan cfg bila dipakai cadangan CPU - pemanggil wajib memakai `name` ini.
    """
    name = cfg.get("whisper_model", "small")
    dev = cfg.get("whisper_device", "auto")
    mlx_weights = config.whisper_mlx_installed(name)
    if dev in ("auto", "mlx") and mlx_weights and mlx_available():
        return {"engine": "mlx", "name": name, "device": "mlx", "compute": "float16",
                "label": f"{name} · mlx (GPU Apple)"}
    if config.whisper_installed(name):
        return _fw_backend(cfg, name)
    # Model ini hanya ada dalam format MLX (default Mac: large-v3-turbo), tetapi
    # MLX tidak dipakai: perangkat 'cpu' dipilih atau MLX/Metal tidak bisa dimuat.
    # Pakai cadangan CPU (small) daripada menolak transmisi. Tanpa bobot MLX
    # (Windows) cabang ini tidak pernah aktif.
    if mlx_weights:
        cadangan = _cpu_fallback_model(name)
        if cadangan:
            return _fw_backend(cfg, cadangan, f" (cadangan CPU: {name} hanya ada versi MLX)")
    return {"engine": None, "name": name, "device": None, "compute": None,
            "label": f"{name} · BELUM terinstal"}


def _cpu_fallback_model(name: str) -> str | None:
    """Model faster-whisper untuk cadangan bila MLX tidak dipakai/gagal: model
    yang sama, atau small (diunduh setup.sh sebagai cadangan CPU)."""
    for cand in (name, "small"):
        if config.whisper_installed(cand):
            return cand
    return None


def _not_installed(name: str) -> ModelNotInstalled:
    path = config.model_path(name, "whisper")
    if config.whisper_mlx_installed(name):
        # Bobot MLX ada, tetapi MLX tidak dipakai dan tidak ada model CPU cadangan.
        return ModelNotInstalled(
            f"Model Whisper '{name}' hanya ada dalam format MLX (GPU Apple), tetapi MLX tidak "
            "dipakai (perangkat 'cpu', atau MLX/Metal tidak bisa dimuat - lihat log terminal).\n"
            f"Pasang model CPU cadangan:  {config.VENV_PY} setup_models.py whisper-small\n"
            "atau pilih perangkat 'auto' di tab Pengaturan."
        )
    alt = (f"\n      atau  {config.VENV_PY} setup_models.py whisper-mlx-{name}  (GPU Apple)"
           if config.IS_APPLE_SILICON else "")
    return ModelNotInstalled(
        f"Model Whisper '{name}' belum terinstal di {path}.\n"
        f"Jalankan:  {config.SETUP_CMD}{alt}\n"
        f"({config.SETUP_CMD} mengunduh model ke folder models/ - aplikasi tidak mengunduh otomatis)"
    )


def get_whisper(cfg: dict):
    """Load (cache) model faster-whisper lokal."""
    name = cfg.get("whisper_model", "small")
    if not config.whisper_installed(name):
        raise _not_installed(name)
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
            cpu_threads=config.whisper_cpu_threads(),
        )
        _WHISPER.clear()
        _WHISPER[key] = model
        return model


def _transcribe_faster_whisper(wav: Path, cfg: dict) -> dict:
    model = get_whisper(cfg)
    segments, info = model.transcribe(
        str(wav),
        language=cfg.get("language", "id"),
        vad_filter=True,
        vad_parameters=VAD_PARAMS,
        beam_size=5,
        condition_on_previous_text=False,
        initial_prompt=INITIAL_PROMPT,
    )
    parts, logprobs = [], []
    for seg in segments:
        txt = seg.text.strip()
        if txt:
            parts.append(txt)
            logprobs.append(seg.avg_logprob)
    return {
        "parts": parts, "logprobs": logprobs,
        "language": getattr(info, "language", cfg.get("language", "id")),
        "language_probability": float(getattr(info, "language_probability", 0.0) or 0.0),
        "duration": float(getattr(info, "duration", 0.0) or audio_duration(wav)),
    }


def _speech_only(audio):
    """Potong bagian sunyi dengan Silero VAD milik faster-whisper.

    Sama persis dengan vad_filter=True di jalur faster-whisper, sehingga
    sunyi/derau tidak ditranskripsi (mencegah halusinasi "terima kasih").
    """
    import numpy as np
    from faster_whisper.vad import VadOptions, collect_chunks, get_speech_timestamps

    chunks = get_speech_timestamps(audio, VadOptions(**VAD_PARAMS))
    if not chunks:
        return None
    pieces, _ = collect_chunks(audio, chunks)
    speech = np.concatenate(pieces, axis=0) if pieces else None
    return speech if speech is not None and speech.size else None


def _mlx_load(name: str) -> None:
    """Muat bobot MLX ke cache mlx-whisper dan materialisasi SELURUH array model
    di thread MLX. load_model hanya mengevaluasi parameters(); array privat
    (_positional_embedding, _mask) tetap lazy sampai inferensi pertama."""
    def _load():
        try:
            import mlx.core as mx
            from mlx_whisper.transcribe import ModelHolder
        except ImportError:
            return  # API internal berubah: model tetap dimuat saat transcribe pertama
        model = ModelHolder.get_model(str(config.model_path(name, "whisper-mlx")), mx.float16)
        mx.eval(model.state)

    _on_mlx_thread(_load)


def _mlx_transcribe_array(speech, name: str, language: str) -> tuple[dict, float]:
    """Inferensi mlx-whisper di thread MLX. Return (hasil, detik inferensi) -
    detik diukur di thread MLX sehingga waktu antre tidak ikut terhitung."""
    def _run():
        import mlx_whisper

        t0 = time.perf_counter()
        res = mlx_whisper.transcribe(
            speech,
            path_or_hf_repo=str(config.model_path(name, "whisper-mlx")),
            language=language,
            condition_on_previous_text=False,
            initial_prompt=INITIAL_PROMPT,
            fp16=True,
            verbose=None,
        )
        return res, time.perf_counter() - t0

    return _on_mlx_thread(_run)


def _transcribe_mlx(wav: Path, cfg: dict, name: str) -> dict:
    """GPU Apple Silicon. Decoding greedy (mlx-whisper belum punya beam search)
    dengan fallback temperatur bawaan Whisper."""
    import numpy as np
    import soundfile as sf

    audio, _sr = sf.read(str(wav), dtype="float32", always_2d=True)
    audio = audio.mean(axis=1) if audio.shape[1] > 1 else audio[:, 0]
    audio = np.ascontiguousarray(audio, dtype=np.float32)
    language = cfg.get("language", "id")
    out = {"parts": [], "logprobs": [], "language": language,
           "language_probability": 1.0, "duration": len(audio) / SAMPLE_RATE}
    t0 = time.perf_counter()
    speech = _speech_only(audio)
    out["seconds"] = time.perf_counter() - t0
    if speech is None:
        return out
    res, infer_seconds = _mlx_transcribe_array(speech, name, language)
    out["seconds"] += infer_seconds
    for seg in res.get("segments") or []:
        txt = str(seg.get("text") or "").strip()
        if txt:
            out["parts"].append(txt)
            out["logprobs"].append(float(seg.get("avg_logprob", -1.0)))
    return out


def transcribe(path: str | Path, cfg: dict) -> dict:
    """Transkripsi satu file audio. Return dict teks + metrik."""
    wav = to_wav16k(path)
    try:
        return _transcribe_wav(wav, cfg)
    finally:
        Path(wav).unlink(missing_ok=True)  # WAV sementara, tidak dipakai lagi


def _transcribe_wav(wav: Path, cfg: dict) -> dict:
    be = whisper_backend(cfg)
    if be["engine"] is None:
        raise _not_installed(be["name"])
    # transcribe_seconds = waktu inferensi saja (muat model tidak dihitung),
    # sama dengan perilaku asli.
    if be["engine"] == "mlx":
        try:
            _mlx_load(be["name"])
            res = _transcribe_mlx(wav, cfg, be["name"])
            elapsed = res["seconds"]
        except Exception as exc:
            cadangan = _cpu_fallback_model(be["name"])
            if cadangan is None:
                raise
            # Cadangan: transkrip tidak boleh hilang hanya karena GPU/MLX bermasalah.
            print(f"[audio] mlx-whisper gagal ({exc}); memakai faster-whisper CPU "
                  f"({cadangan})", flush=True)
            cpu_cfg = dict(cfg, whisper_device="cpu", whisper_model=cadangan)
            be = whisper_backend(cpu_cfg)
            be["label"] += " (cadangan: mlx gagal)"
            get_whisper(cpu_cfg)
            t0 = time.perf_counter()
            res = _transcribe_faster_whisper(wav, cpu_cfg)
            elapsed = time.perf_counter() - t0
    else:
        run_cfg = dict(cfg, whisper_model=be["name"])  # bisa model cadangan CPU
        get_whisper(run_cfg)
        t0 = time.perf_counter()
        res = _transcribe_faster_whisper(wav, run_cfg)
        elapsed = time.perf_counter() - t0
    parts, logprobs = res["parts"], res["logprobs"]
    text = " ".join(parts).strip()
    avg_lp = sum(logprobs) / len(logprobs) if logprobs else -1.0
    return {
        "text": text,
        "language": res["language"],
        "language_probability": res["language_probability"],
        "duration": res["duration"],
        "avg_logprob": round(float(avg_lp), 3),
        "transcribe_seconds": round(elapsed, 2),
        "backend": be["label"],
    }


def warmup(cfg: dict) -> str:
    """Muat model transkripsi lebih awal (MLX: sekaligus kompilasi kernel GPU)
    supaya transmisi pertama tidak menanggung waktu muat. Tidak pernah raise."""
    try:
        be = whisper_backend(cfg)
        if be["engine"] == "mlx":
            import numpy as np

            _mlx_load(be["name"])
            _mlx_transcribe_array(np.zeros(SAMPLE_RATE, dtype=np.float32), be["name"],
                                  cfg.get("language", "id"))
        elif be["engine"] == "faster-whisper":
            get_whisper(dict(cfg, whisper_model=be["name"]))
        return be["label"]
    except Exception as exc:
        return f"gagal: {exc}"


def release_models() -> None:
    """Lepas model Whisper dari memori (dipanggil saat pengaturan disimpan,
    supaya model MLX ±1,6 GB tidak tertinggal saat beralih ke CPU/model lain)."""
    with _WHISPER_LOCK:
        _WHISPER.clear()
    if _MLX_EXECUTOR is None:
        return

    def _drop():
        try:
            from mlx_whisper.transcribe import ModelHolder
        except ImportError:
            return
        ModelHolder.model = None
        ModelHolder.model_path = None

    try:
        _on_mlx_thread(_drop)
    except Exception:
        pass


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
    out = _temp_wav()
    rec = sd.rec(int(seconds * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1,
                 dtype="int16", device=device_index)
    sd.wait()
    sf.write(str(out), rec, SAMPLE_RATE)
    return out
