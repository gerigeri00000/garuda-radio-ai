"""
GARUDA Radio AI - antrian klip audio untuk mode dengar terus.

Tanggung jawab tunggal: menerima klip audio, menjalankannya satu per satu lewat
pipeline.process_audio(), dan mencatat setiap transisi ke database.

Tidak berisi aturan aksi (itu di actions.py) dan tidak menyentuh UI.
"""
from __future__ import annotations

import queue
import threading
import time
from pathlib import Path

import audio
import config
import database as db
import pipeline

MIN_CLIP_SECONDS = 1.2
QUEUE_MAX = 20

_Q: "queue.Queue[tuple[Path, str]]" = queue.Queue(maxsize=QUEUE_MAX)
_CFG: dict = {}
_THREAD: threading.Thread | None = None
_LOCK = threading.Lock()
_STATS = {"queued": 0, "processed": 0, "failed": 0, "discarded": 0}

# Hanya untuk pengujian: proses klip langsung di thread pemanggil.
_SYNC_FOR_TEST = False


def _durasi(path: Path) -> float:
    try:
        return audio.audio_duration(path)
    except Exception:
        return 0.0


def _catat(action: str, path: Path, source: str, extra: dict | None = None) -> None:
    detail = {"path": str(path), "source": source}
    if extra:
        detail.update(extra)
    try:
        db.log("loop", None, action, detail, "system")
    except Exception:
        pass  # audit tidak boleh menghentikan antrian


def enqueue_clip(path: str | Path, source: str = "live",
                 _force_duration: float | None = None) -> dict:
    """Terima satu klip. Cepat & tidak memblokir. Return status untuk UI."""
    path = Path(path)

    durasi = _force_duration if _force_duration is not None else _durasi(path)
    if _force_duration is None and not path.exists():
        return {"ok": False, "reason": "missing",
                "message": f"Klip tidak ditemukan: {path.name}"}

    if durasi < MIN_CLIP_SECONDS:
        return {"ok": False, "reason": "too_short",
                "message": f"Klip {durasi:.1f}s terlalu pendek (minimum "
                           f"{MIN_CLIP_SECONDS}s). Kemungkinan hanya derau - "
                           "tidak diproses."}

    _catat("queued", path, source)

    if _SYNC_FOR_TEST:
        _proses(path, source)
        _STATS["queued"] += 1
        return {"ok": True, "reason": "ok",
                "message": f"Klip {durasi:.1f}s masuk antrian."}

    try:
        _Q.put_nowait((path, source))
    except queue.Full:
        # buang klip tertua supaya transmisi terbaru tetap terlayani
        try:
            _Q.get_nowait()
            _Q.task_done()
            _STATS["discarded"] += 1
            _catat("discarded", path, source, {"reason": "antrian_penuh"})
        except queue.Empty:
            pass
        try:
            _Q.put_nowait((path, source))
        except queue.Full:
            return {"ok": False, "reason": "full",
                    "message": "Antrian penuh - klip dibuang."}
        _STATS["queued"] += 1
        return {"ok": False, "reason": "full",
                "message": "Antrian penuh - klip terlama dibuang agar yang "
                           "terbaru terlayani."}

    _STATS["queued"] += 1
    return {"ok": True, "reason": "ok", "message": f"Klip {durasi:.1f}s masuk antrian."}


def _proses(path: Path, source: str) -> None:
    _catat("processing", path, source)
    try:
        res = pipeline.process_audio(path, _CFG, source=source, auto_apply=True)
        _STATS["processed"] += 1
        # report_id & transcript_id ada di dalam res["action"], bukan di res.
        act = res.get("action") or {}
        _catat("processed", path, source,
               {"report_id": act.get("report_id"),
                "category": (res.get("analysis") or {}).get("category"),
                "transcript_id": act.get("transcript_id")})
    except Exception as exc:
        _STATS["failed"] += 1
        _catat("failed", path, source, {"error": str(exc)[:300]})


def _worker() -> None:
    while True:
        item = _Q.get()
        if item is None:  # sentinel berhenti
            _Q.task_done()
            return
        path, source = item
        try:
            _proses(path, source)
        finally:
            _Q.task_done()


def start_worker(cfg: dict) -> None:
    """Nyalakan satu thread pekerja. Idempotent."""
    global _THREAD
    _CFG.update(cfg)
    with _LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return
        _THREAD = threading.Thread(target=_worker, name="garuda-live-loop",
                                   daemon=True)
        _THREAD.start()


def stop_worker() -> None:
    global _THREAD
    with _LOCK:
        if _THREAD is None or not _THREAD.is_alive():
            return
        _Q.put(None)
        _THREAD = None


def drain(timeout: float = 60.0) -> None:
    """Tunggu sampai semua klip dalam antrian selesai diproses (untuk uji)."""
    batas = time.time() + timeout
    while time.time() < batas:
        if _Q.empty() and _STATS["queued"] <= (_STATS["processed"]
                                               + _STATS["failed"]
                                               + _STATS["discarded"]):
            return
        time.sleep(0.1)


def worker_stats() -> dict:
    """Diagnostik. UI TIDAK memakai ini - panel membaca DB."""
    out = dict(_STATS)
    out["antrian"] = _Q.qsize()
    return out


# --- hanya untuk pengujian -------------------------------------------------
def set_sync_for_test(sync: bool) -> None:
    global _SYNC_FOR_TEST
    _SYNC_FOR_TEST = sync


def reset_for_test() -> None:
    """Kosongkan antrian & statistik di antara test."""
    global _SYNC_FOR_TEST
    _SYNC_FOR_TEST = False
    while True:
        try:
            _Q.get_nowait()
            _Q.task_done()
        except queue.Empty:
            break
    for k in _STATS:
        _STATS[k] = 0
