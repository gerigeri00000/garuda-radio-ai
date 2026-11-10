"""
GARUDA Radio AI - dua entry point pemrosesan lengkap (dipakai UI & test):

  process_audio(path, cfg, source) -> transkripsi -> analisis -> aksi
  process_text(text, cfg)          -> analisis -> aksi (untuk pengujian tanpa audio)

UI dan pengujian memakai jalur kode yang sama.
"""
from __future__ import annotations

import time
from pathlib import Path

import actions
import ai
import audio


def process_audio(path: str | Path, cfg: dict, source: str = "upload",
                  auto_apply: bool = False) -> dict:
    t0 = time.perf_counter()
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"File audio tidak ditemukan: {path}")

    trans = audio.transcribe(path, cfg)

    out: dict = {
        "audio_path": str(path),
        "transcript": trans["text"],
        "language": trans.get("language"),
        "duration": round(trans.get("duration") or 0.0, 2),
        "avg_logprob": trans.get("avg_logprob"),
        "transcribe_seconds": trans.get("transcribe_seconds"),
        "whisper_backend": trans.get("backend"),
        "analysis": None,
        "ai_error": None,
        "action": None,
    }
    if not trans["text"].strip():
        out["action"] = {"steps": ["Transkripsi kosong (tidak ada suara terdeteksi). Tidak ada yang disimpan."]}
        out["total_seconds"] = round(time.perf_counter() - t0, 2)
        return out

    try:
        analysis = ai.analyze(trans["text"], cfg)
    except ai.LLMNotAvailable as exc:
        # Audio tetap tercatat sebagai laporan belum terklasifikasi
        out["analysis"] = {"llm_valid": False, "llm_error": str(exc)}
        out["ai_error"] = str(exc)
        out["action"] = actions.process_audio_result(
            path, trans,
            {"llm_valid": False, "action": "NONE", "category": "UNKNOWN",
             "llm_error": str(exc), "llm_raw": "", "llm_seconds": 0},
            cfg, source, auto_apply=auto_apply,
        )
        out["total_seconds"] = round(time.perf_counter() - t0, 2)
        return out

    out["analysis"] = analysis
    out["action"] = actions.process_audio_result(path, trans, analysis, cfg, source,
                                                 auto_apply=auto_apply)
    out["total_seconds"] = round(time.perf_counter() - t0, 2)
    return out


def process_text(text: str, cfg: dict) -> dict:
    """Uji alur tanpa audio: LLM langsung membaca teks (bukan pengganti transkripsi)."""
    t0 = time.perf_counter()
    text = (text or "").strip()
    if not text:
        raise ValueError("Teks kosong")
    analysis = ai.analyze(text, cfg)
    out = {"analysis": analysis, "action": None}
    if not analysis.get("llm_valid"):
        out["action"] = actions.store_unclassified_text(text, analysis, cfg)
    else:
        out["action"] = actions.process_text_result(text, analysis, cfg)
    out["total_seconds"] = round(time.perf_counter() - t0, 2)
    return out


def warmup(cfg: dict) -> dict:
    """Muat Whisper & LLM lebih awal (dipanggil di thread latar saat app mulai).

    Transmisi pertama jadi secepat transmisi berikutnya: bobot model sudah di
    memori, kernel GPU sudah terkompilasi, dan prompt sistem LLM sudah ada di
    cache KV. Tidak menyentuh database dan tidak pernah raise.
    """
    t0 = time.perf_counter()
    whisper = audio.warmup(cfg)
    llm = ai.warmup(cfg)
    return {"whisper": whisper, "llm": llm, "seconds": round(time.perf_counter() - t0, 2)}
