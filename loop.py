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
_DETACHED: threading.Thread | None = None  # sudah diminta stop, belum mati
_LOCK = threading.Lock()
_STOP_LOCK = threading.Lock()  # serialkan stop_worker
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
        with _LOCK:
            _STATS["queued"] += 1
        return {"ok": True, "reason": "ok",
                "message": f"Klip {durasi:.1f}s masuk antrian."}

    try:
        _Q.put_nowait((path, source))
    except queue.Full:
        # Buang klip tertua supaya transmisi terbaru tetap terlayani.
        # Klip yang dibuang HARUS tercatat, kalau tidak operator kehilangan
        # jejak bahwa ada transmisi yang tidak pernah diproses.
        tertua = None
        try:
            tertua = _Q.get_nowait()
            _Q.task_done()
        except queue.Empty:
            pass
        with _LOCK:
            _STATS["discarded"] += 1
        if tertua is not None:
            _catat("discarded", tertua[0], tertua[1], {"reason": "antrian_penuh"})
        try:
            _Q.put_nowait((path, source))
        except queue.Full:
            _catat("discarded", path, source, {"reason": "antrian_penuh"})
            return {"ok": False, "reason": "full",
                    "message": "Antrian penuh - klip dibuang."}
        with _LOCK:
            _STATS["queued"] += 1
        return {"ok": False, "reason": "full",
                "message": "Antrian penuh - klip terlama dibuang agar yang "
                           "terbaru terlayani."}

    with _LOCK:
        _STATS["queued"] += 1
    return {"ok": True, "reason": "ok", "message": f"Klip {durasi:.1f}s masuk antrian."}


def _proses(path: Path, source: str) -> None:
    _catat("processing", path, source)
    try:
        res = pipeline.process_audio(path, _CFG, source=source, auto_apply=True)
        with _LOCK:
            _STATS["processed"] += 1
        # report_id & transcript_id ada di dalam res["action"], bukan di res.
        act = res.get("action") or {}
        _catat("processed", path, source,
               {"report_id": act.get("report_id"),
                "category": (res.get("analysis") or {}).get("category"),
                "transcript_id": act.get("transcript_id")})
    except Exception as exc:
        with _LOCK:
            _STATS["failed"] += 1
        _catat("failed", path, source, {"error": str(exc)[:300]})


def _worker(stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            item = _Q.get(timeout=0.2)
        except queue.Empty:
            continue  # periksa event berhenti secara berkala
        path, source = item
        try:
            _proses(path, source)
        finally:
            _Q.task_done()


def start_worker(cfg: dict) -> None:
    """Nyalakan satu thread pekerja. Idempotent.

    Worker yang sudah diminta berhenti tapi belum mati (_DETACHED) tetap
    dihitung: menyalakan worker baru di sampingnya akan membuat dua worker
    bersaing memproses file audio yang sama.
    """
    global _THREAD
    _CFG.update(cfg)
    with _LOCK:
        for t in (_THREAD, _DETACHED):
            if t is not None and t.is_alive():
                return
        stop = threading.Event()
        t = threading.Thread(target=_worker, args=(stop,),
                             name="garuda-live-loop", daemon=True)
        t._garuda_stop = stop  # type: ignore[attr-defined]
        _THREAD = t
        t.start()


def stop_worker(timeout: float = 5.0) -> dict:
    """Minta pekerja berhenti. Klip yang sudah di antrian TIDAK dibuang.

    Tidak memakai sentinel di antrian: sentinel bisa memakan satu slot dan
    membuat klip berikutnya yatim, terutama saat antrian penuh.

    Return {"stopped": bool, "message": str}. Bila pemrosesan sedang berjalan
    lama (LLM 3B bisa 15-30 s), thread belum tentu berhenti dalam `timeout`.
    JANGAN bilang "nonaktif" pada operator bila masih ada yang berjalan -
    thread yang tertinggal tetap memproses klip berikutnya.
    """
    global _THREAD, _DETACHED
    # Satu stop pada satu waktu: dua klik Hentikan beruntun tidak boleh
    # saling menimpa registry dan saling membohongi operator.
    with _STOP_LOCK:
        with _LOCK:
            t = _THREAD
            _THREAD = None
            if t is None:
                # Worker yang terlepas dari registry tapi belum mati (mis.
                # Hentikan kedua saat yang pertama masih join). Tanpa ini,
                # klik Hentikan kedua menjawab "nonaktif" padahal masih proses.
                t = _DETACHED
            if t is not None and not t.is_alive():
                t = None
                _DETACHED = None
        if t is None:
            return {"stopped": True, "message": "nonaktif"}

        stop = getattr(t, "_garuda_stop", None)
        if stop is not None:
            stop.set()
        t.join(timeout=timeout)

        if t.is_alive():
            # Kembalikan ke registry supaya start berikutnya tidak menciptakan
            # worker kedua yang bersaing di file & hash audio yang sama.
            with _LOCK:
                if _THREAD is None:
                    _THREAD = t
                _DETACHED = t
            return {"stopped": False,
                    "message": (f"masih menyelesaikan satu klip (>{timeout:.0f}s). "
                                "Worker akan berhenti sendiri setelah klip ini selesai.")}
        with _LOCK:
            _DETACHED = None
        return {"stopped": True, "message": "nonaktif"}


def drain(timeout: float = 60.0) -> None:
    """Tunggu sampai semua klip dalam antrian selesai diproses (untuk uji)."""
    batas = time.time() + timeout
    while time.time() < batas:
        with _LOCK:
            tuntas = _STATS["queued"] <= (_STATS["processed"]
                                          + _STATS["failed"]
                                          + _STATS["discarded"])
        if _Q.empty() and tuntas:
            return
        time.sleep(0.1)


def worker_stats() -> dict:
    """Diagnostik. UI TIDAK memakai ini - panel membaca DB."""
    with _LOCK:
        out = dict(_STATS)
    out["antrian"] = _Q.qsize()
    return out


# --- hanya untuk pengujian -------------------------------------------------
def set_sync_for_test(sync: bool) -> None:
    global _SYNC_FOR_TEST
    _SYNC_FOR_TEST = sync


def reset_for_test() -> None:
    """Kosongkan antrian & statistik di antara test."""
    global _SYNC_FOR_TEST, _THREAD, _DETACHED
    _SYNC_FOR_TEST = False
    stop_worker(timeout=10.0)
    # Test tidak boleh meninggalkan worker hidup yang memakan klip antar test.
    with _LOCK:
        _THREAD = None
        _DETACHED = None
    while True:
        try:
            _Q.get_nowait()
            _Q.task_done()
        except queue.Empty:
            break
    with _LOCK:
        for k in _STATS:
            _STATS[k] = 0
