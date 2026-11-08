# Mode Dengar Terus (Continuous Listening) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Membuat GARUDA Radio AI terus mendengarkan mikrofon, memotong tiap
transmisi radio secara otomatis, lalu menjalankan transkripsi → analisis → aksi
tanpa operator menekan tombol per transmisi.

**Architecture:** VAD di browser (`@ricky0123/vad-web`) memicu tombol rekam
`gr.Audio` baku untuk mulai/berhenti. Tiap klip dari `.stop_recording()` masuk
antrian FIFO di `loop.py` (satu thread pekerja), lalu diproses lewat
`pipeline.process_audio(..., auto_apply=True)`. Panel antrian dibaca dari DB
lewat `gr.Timer`, sehingga refresh browser tidak mengubah apa pun.

**Tech Stack:** Python 3.12, Gradio 6.31.0, faster-whisper 1.2.1, llama-cpp-python
(Qwen2.5 3B GGUF), SQLite, `@ricky0123/vad-web` + `onnxruntime-web` (aset lokal).

**Spec:** `docs/superpowers/specs/2026-10-26-mode-dengar-terus-design.md`

## Global Constraints

- **Tidak ada git di folder ini** (`AGENTS.md` §6.6). Setiap "commit" di bawah
  diganti: buat `<file>.bak` **sebelum** mengubah file yang sudah ada, dengan
  perintah `Copy-Item <file> <file>.bak -Force`.
- **100% offline saat runtime.** Tidak ada unduhan dari internet kecuali lewat
  `setup_models.py`.
- **Satu jalur pemrosesan:** logika baru masuk `pipeline.py`/`actions.py`, bukan
  diduplikasi di `app.py` (`AGENTS.md` §6.2).
- **LLM hanya mengusulkan JSON.** Eksekusi selalu di `actions.py`.
- **Tidak ada aturan produk yang dilonggarkan:** `CREATE_TICKET` tetap `Pending`,
  `UPDATE_MAP` tetap `Pending Approval`, `UPDATE_STATUS` tetap usulan.
- **`AUTO_APPLY_ACTIONS = {"CREATE_ALERT"}`** — persis satu aksi.
- **Jangan ubah `README.md`** (panduan ditulis di Fase 4, setelah kode teruji).
- **Baseline uji wajib tetap:** `test_scenarios.py` → **47/47 PASS**,
  `smoke_ui.py` → **SMOKE PASS**.
- Komentar & string UI **berbahasa Indonesia**, konsisten dengan kode yang ada.
- `from __future__ import annotations` di setiap modul.
- Parameter awal: klip minimum **1.2 s**, interval timer **2 s**, `maxsize` antrian **20**.

## Review Focus

Kegagalan yang paling mungkin menggigit pengguna, dan diuji di task yang memiliki kodenya:

1. **Klip terlalu pendek / hanya derau** — orang mengharapkan tidak ada baris
   sampah di antrian. Diuji di Task 2.
2. **LLM mati saat transmisi masuk** — orang mengharapkan transkrip tetap
   tersimpan, bukan hilang. Diuji di Task 2.
3. **Antrian penuh saat trafik padat (LLM 3B lambat)** — orang mengharapkan
   sistem tetap hidup dan memberi tahu, bukan diam atau crash. Diuji di Task 2.
4. **Klip berurutan menabrak path temp yang sama** — orang mengharapkan dua
   transmisi jadi dua baris, bukan satu. Diuji di Task 3.
5. **`auto_apply` bocor ke jalur manual** — orang mengharapkan tombol "Proses
   audio" tetap membuat tiket `Pending` seperti biasa. Diuji di Task 1.

---

## Fase 0 — Verifikasi asumsi Gradio 6 (gerbang, bukan fitur)

Fase ini **tidak menghasilkan kode produk**. Ia membuktikan asumsi yang
menopang seluruh rencana. Bila gagal, **berhenti** dan kembali ke brainstorming.

### Task 0: Buktikan `.stop_recording()` mengirim file ke server

**Files:**
- Create (sementara, hapus setelah selesai): `_probe_v6.py`

**Interfaces:**
- Consumes: `gradio==6.31.0` yang sudah terpasang lewat `setup.bat`
- Produces: keputusan go/no-go untuk Task 6 dan Task 7

- [ ] **Step 1: Tulis probe**

```python
"""Probe sementara: apakah stop_recording mengirim file ke server di Gradio 6?"""
import gradio as gr

def on_stop(path):
    print("STOP_RECORDING_PATH:", repr(path))
    return f"terima: {path}"

with gr.Blocks() as demo:
    a = gr.Audio(sources=["microphone"], type="filepath", elem_id="audio_in")
    out = gr.Markdown("")
    a.stop_recording(on_stop, [a], [out])
    # buktikan start_recording bisa dipanggil berkali-kali
    gr.Markdown(f"punya start_recording: {hasattr(a, 'start_recording')}")

if __name__ == "__main__":
    demo.launch(server_port=7871, inbrowser=True)
```

- [ ] **Step 2: Jalankan**

Run: `.\.venv\Scripts\python.exe _probe_v6.py`

- [ ] **Step 3: Uji manual di browser (rekam dua kali berturut-turut)**

Rekam sekali, tekan stop, lihat terminal. Rekam lagi, tekan stop, lihat lagi.

Expected:
- `STOP_RECORDING_PATH: '/..../audio.wav'` (string path, bukan `None`) dua kali.
- Kedua path **berbeda**.
- `punya start_recording: True`

- [ ] **Step 4: Catat hasil, hapus probe**

Bila ketiga expected terpenuhi → lanjut Task 1.
Bila path `None` / hanya blob / path sama → **berhenti**, laporkan ke manusia,
jangan lanjutkan rencana ini.

Run: `Remove-Item _probe_v6.py`

---

## Fase 1 — Aturan produk (bisa dibuktikan tanpa browser)

### Task 1: Daftar putih aksi otomatis di `actions.py` / `pipeline.py`

**Files:**
- Modify: `config.py` (tambah konstanta setelah `DEFAULTS`)
- Modify: `actions.py:55-56` (`process_audio_result`), `actions.py:178` (`run_action`), `actions.py:224-236` (cabang `CREATE_ALERT`)
- Modify: `pipeline.py:19` (`process_audio`)
- Test: `test_live.py` (dibuat di task ini)

**Interfaces:**
- Consumes: fungsi yang sudah ada `actions.process_audio_result()`, `run_action()`
- Produces:
  - `config.AUTO_APPLY_ACTIONS: set[str]` — nilainya persis `{"CREATE_ALERT"}`
  - `actions.process_audio_result(audio_path, trans, analysis, cfg, source="upload", auto_apply=False) -> dict`
  - `actions.run_action(report_id, analysis, text, auto_apply=False) -> dict`
  - `pipeline.process_audio(path, cfg, source="upload", auto_apply=False) -> dict`

- [ ] **Step 1: Buat `test_live.py` dengan test yang gagal**

```python
"""Uji mode dengar-terus: aturan aksi otomatis & antrian klip.

Jalankan:  .venv\\Scripts\\python.exe test_live.py
Berkas ini RESET database sendiri di awal dan di akhir, terpisah dari
test_scenarios.py (baseline 47/47 tidak disentuh).
"""
from __future__ import annotations

import sys

import actions
import config
import database as db

cfg = config.load_config()
LAYANAN = ["LOGISTICS", "MEDICAL", "EQUIPMENT", "INFRASTRUCTURE",
           "STATUS_UPDATE", "GENERAL", "UNKNOWN"]


def reset_db() -> None:
    db.init_db()
    for t in ("transcripts", "reports", "tickets", "alerts", "map_updates", "events_log"):
        with db.connect() as c:
            c.execute(f"DELETE FROM {t}")
            c.execute("UPDATE sqlite_sequence SET seq=0 WHERE name=?", (t,))
    for loc in db.list_locations():
        with db.connect() as c:
            c.execute("UPDATE locations SET status='Normal' WHERE key=?", (loc["key"],))


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return ok


def analysis(kategori="MEDICAL", action="CREATE_ALERT", prioritas="HIGH", **kw) -> dict:
    """AnalysisResult minimal yang meniru keluaran ai.validate()."""
    out = {
        "category": kategori, "priority": prioritas, "action": action,
        "unit": "Pos Bravo", "summary": "Pos Bravo minta bantuan medis",
        "item": None, "quantity": None, "unit_measure": None,
        "location": "Pos Bravo", "location_key": "pos bravo",
        "confidence": 0.9, "needs_approval": True, "needs_verification": False,
        "missing_information": [], "llm_raw": "{}", "llm_valid": True, "llm_seconds": 0.1,
    }
    out.update(kw)
    return out


def event_actions() -> list[str]:
    return [(e["action"] or "") for e in db.recent_log(200)]


def test_auto_apply_alert():
    an = analysis()
    res = actions.process_text_result("Pos Bravo minta bantuan medis", an, cfg)
    actions.run_action(res["report_id"], an, "Pos Bravo minta bantuan medis",
                       auto_apply=True)
    return check("CREATE_ALERT tercatat auto_applied",
                 "auto_applied" in event_actions())


def test_map_tetap_pending():
    an = analysis(kategori="INFRASTRUCTURE", action="UPDATE_MAP", prioritas="MEDIUM",
                  summary="Jembatan Desa Contoh rusak", item="jembatan",
                  location="Jembatan Desa Contoh", location_key="jembatan desa contoh")
    res = actions.process_text_result("Jembatan Desa Contoh rusak", an, cfg)
    actions.run_action(res["report_id"], an, "Jembatan Desa Contoh rusak", auto_apply=True)
    rows = db.list_map_updates(10)
    ok = bool(rows) and all(r["status"] == "Pending Approval" for r in rows)
    lok = [l for l in db.list_locations() if l["key"] == "jembatan desa contoh"][0]
    return (check("UPDATE_MAP tetap Pending Approval", ok)
            and check("locations tidak berubah oleh auto_apply", lok["status"] == "Normal",
                      lok["status"]))


def test_tiket_tetap_pending():
    an = analysis(kategori="LOGISTICS", action="CREATE_TICKET", prioritas="MEDIUM",
                  summary="Pos Bravo butuh solar", item="solar", quantity=30,
                  unit_measure="liter")
    res = actions.process_text_result("Pos Bravo butuh 30 liter solar", an, cfg)
    actions.run_action(res["report_id"], an, "Pos Bravo butuh 30 liter solar",
                       auto_apply=True)
    t = db.list_tickets(10)
    ok = bool(t) and all(x["status"] == "Pending" for x in t)
    return check("CREATE_TICKET tetap Pending di auto_apply", ok)


def test_status_update_tetap_usulan():
    an = analysis(kategori="LOGISTICS", action="CREATE_TICKET", prioritas="MEDIUM",
                  summary="Pos Bravo butuh solar", item="solar", quantity=30,
                  unit_measure="liter")
    res = actions.process_text_result("Pos Bravo butuh 30 liter solar", an, cfg)
    tiket_id = db.list_tickets(10)[0]["id"]
    an2 = analysis(kategori="STATUS_UPDATE", action="UPDATE_STATUS", item="solar",
                   quantity=30, summary="Solar untuk Pos Bravo sudah diterima")
    actions.run_action(res["report_id"], an2, "Solar sudah diterima", auto_apply=True)
    t = db.get_ticket(tiket_id)
    return check("UPDATE_STATUS tidak mengubah status tiket", t["status"] == "Pending",
                 t["status"])


def test_jalur_manual_tidak_berubah():
    an = analysis()
    res = actions.process_text_result("Pos Bravo minta bantuan medis", an, cfg)
    actions.run_action(res["report_id"], an, "Pos Bravo minta bantuan medis")
    return check("tanpa auto_apply tidak ada auto_applied",
                 "auto_applied" not in event_actions())


def run() -> int:
    results: list[bool] = []
    print("=" * 70)
    print("Uji mode dengar-terus")
    print("=" * 70)
    reset_db()
    for fn in (test_auto_apply_alert, test_map_tetap_pending,
               test_tiket_tetap_pending, test_status_update_tetap_usulan,
               test_jalur_manual_tidak_berubah):
        reset_db()
        results.append(fn())
    hits = sum(results)
    print(f"\nHASIL: {hits}/{len(results)} PASS")
    reset_db()
    return 0 if hits == len(results) else 1


if __name__ == "__main__":
    sys.exit(run())
```

- [ ] **Step 2: Jalankan, pastikan GAGAL**

Run: `.\.venv\Scripts\python.exe test_live.py`

Expected: FAIL — `TypeError: run_action() got an unexpected keyword argument 'auto_apply'`

- [ ] **Step 3: Tambah `AUTO_APPLY_ACTIONS` di `config.py`**

Setelah blok `DEFAULTS = {...}` (baris ~49), tambahkan:

```python
# Aksi yang boleh langsung berlaku di mode dengar-terus ("jalankan terus").
# Aksi di luar daftar ini TETAP menunggu persetujuan operator.
# CREATE_TICKET -> Pending, UPDATE_MAP -> Pending Approval, UPDATE_STATUS -> usulan.
AUTO_APPLY_ACTIONS = {"CREATE_ALERT"}
```

- [ ] **Step 4: Teruskan `auto_apply` di `actions.py`**

Buat backup dulu: `Copy-Item actions.py actions.py.bak -Force`

Ubah tanda tangan (baris 55-56) menjadi:

```python
def process_audio_result(audio_path: Path | str, trans: dict, analysis: dict, cfg: dict,
                         source: str = "upload", auto_apply: bool = False) -> dict:
```

Di dalamnya, ubah pemanggilan `run_action(report_id, analysis, trans["text"])`
(baris 122) menjadi:

```python
    act = run_action(report_id, analysis, trans["text"], auto_apply=auto_apply)
```

Ubah tanda tangan `run_action` (baris 178) menjadi:

```python
def run_action(report_id: int, analysis: dict, text: str,
               auto_apply: bool = False) -> dict:
```

Di cabang `CREATE_ALERT` (baris 224-236), setelah `db.log("alert", a_id, "created",
{"report_id": report_id}, "system")`, tambahkan:

```python
        if auto_apply and action in config.AUTO_APPLY_ACTIONS:
            out["steps"].append("Diterapkan otomatis (mode dengar terus).")
            db.log("alert", a_id, "auto_applied", {"action": action}, "system")
```

Catatan: `config` sudah di-import di `actions.py:14`, jadi tidak perlu import baru.

- [ ] **Step 5: Teruskan `auto_apply` di `pipeline.py`**

Buat backup dulu: `Copy-Item pipeline.py pipeline.py.bak -Force`

Ubah tanda tangan (baris 19) menjadi:

```python
def process_audio(path: str | Path, cfg: dict, source: str = "upload",
                  auto_apply: bool = False) -> dict:
```

Pada pemanggilan `actions.process_audio_result(path, trans, analysis, cfg, source)`
(baris 59), ubah menjadi:

```python
    out["action"] = actions.process_audio_result(
        path, trans, analysis, cfg, source, auto_apply=auto_apply)
```

- [ ] **Step 6: Jalankan test, pastikan LULUS**

Run: `.\.venv\Scripts\python.exe test_live.py`

Expected: `HASIL: 5/5 PASS`

- [ ] **Step 7: Pastikan baseline tidak rusak**

Run: `.\.venv\Scripts\python.exe test_scenarios.py`

Expected: **47/47 PASS** (sama seperti sebelumnya)

- [ ] **Step 8: Backup, bukan commit**

Run: `Copy-Item test_live.py test_live.py.bak -Force`

---

### Task 2: `loop.py` — antrian klip + thread pekerja

**Files:**
- Create: `loop.py`
- Modify: `test_live.py` (tambah test antrian)

**Interfaces:**
- Consumes: `pipeline.process_audio(path, cfg, source, auto_apply)` dari Task 1
- Produces:
  - `loop.MIN_CLIP_SECONDS: float = 1.2`
  - `loop.QUEUE_MAX: int = 20`
  - `loop.start_worker(cfg: dict) -> None` — idempotent, daemon thread
  - `loop.stop_worker() -> None`
  - `loop.enqueue_clip(path: str | Path, source: str = "live") -> dict` — return `{"ok": bool, "message": str, "reason": str}`; `reason` ∈ `"ok" | "too_short" | "full" | "missing"`
  - `loop.drain(timeout: float = 60.0) -> None` — tunggu antrian kosong (untuk test)
  - `loop.worker_stats() -> dict` — `{"queued": int, "processed": int, "failed": int, "discarded": int}`

- [ ] **Step 1: Tambah test antrian ke `test_live.py`**

Tambahkan sebelum fungsi `run()`:

```python
def test_klip_terlalu_pendek():
    import loop
    import samples_gen
    path = sorted(config.SAMPLES_DIR.glob("*.wav"))[0] if any(config.SAMPLES_DIR.glob("*.wav")) else None
    if path is None:
        return check("dilewati: tidak ada sample audio", True)
    # anggap klip 0.4 detik -> harus ditolak tanpa masuk antrian
    res = loop.enqueue_clip(path, source="live", _force_duration=0.4)
    return (check("klip terlalu pendek ditolak", res["ok"] is False and res["reason"] == "too_short")
            and check("alasan dijelaskan ke operator", bool(res["message"])))


def test_antrian_penuh_buang_tertua():
    import loop
    loop.reset_for_test()
    loop.set_sync_for_test(True)
    hasil = []
    for _ in range(loop.QUEUE_MAX + 3):
        hasil.append(loop.enqueue_clip("dummy.wav", source="live", _force_duration=5.0))
    stats = loop.worker_stats()
    ok = hasil[-1]["ok"] is True and stats["discarded"] >= 3
    loop.reset_for_test()
    return check("antrian penuh membuang klip tertua", ok, str(stats))


def test_llm_mati_transkrip_tetap_ada():
    import pipeline
    import loop
    import samples_gen
    paths = sorted(config.SAMPLES_DIR.glob("*.wav"))
    if not paths:
        return check("dilewati: tidak ada sample audio", True)
    # paksa LLM tidak tersedia
    asli = pipeline.ai.analyze
    def mati(*a, **k):
        raise pipeline.ai.LLMNotAvailable("LLM tidak tersedia (uji)")
    pipeline.ai.analyze = mati
    try:
        res = pipeline.process_audio(paths[0], cfg, source="live", auto_apply=True)
    finally:
        pipeline.ai.analyze = asli
    return (check("transkrip tetap tersimpan saat LLM mati",
                  bool(res.get("transcript")) and res.get("transcript_id"))
            and check("laporan ditandai belum terklasifikasi",
                      (res.get("analysis") or {}).get("llm_valid") is False))
```

Lalu tambahkan `test_klip_terlalu_pendek, test_antrian_penuh_buang_tertua,
test_llm_mati_transkrip_tetap_ada` ke tuple `for fn in (...)` di `run()`.

- [ ] **Step 2: Jalankan, pastikan GAGAL**

Run: `.\.venv\Scripts\python.exe test_live.py`

Expected: FAIL — `ModuleNotFoundError: No module named 'loop'`

- [ ] **Step 3: Tulis `loop.py`**

```python
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
    if not path.exists():
        return {"ok": False, "reason": "missing",
                "message": f"Klip tidak ditemukan: {path.name}"}

    durasi = _force_duration if _force_duration is not None else _durasi(path)
    if durasi < MIN_CLIP_SECONDS:
        return {"ok": False, "reason": "too_short",
                "message": f"Klip {durasi:.1f}s terlalu pendek (minimum {MIN_CLIP_SECONDS}s). "
                           "Kemungkinan hanya derau - tidak diproses."}

    _catat("queued", path, source)
    try:
        if _SYNC_FOR_TEST:
            _proses(path, source)
        else:
            _Q.put_nowait((path, source))
        _STATS["queued"] += 1
        return {"ok": True, "reason": "ok", "message": f"Klip {durasi:.1f}s masuk antrian."}
    except queue.Full:
        # buang klip tertua supaya transmisi terbaru tetap terlayani
        try:
            _Q.get_nowait()
            _STATS["discarded"] += 1
            _catat("discarded", path, source, {"reason": "antrian_penuh"})
        except queue.Empty:
            pass
        try:
            _Q.put_nowait((path, source))
            _STATS["queued"] += 1
        except queue.Full:
            pass
        return {"ok": False, "reason": "full",
                "message": "Antrian penuh - klip terlama dibuang agar yang terbaru terlayani."}


def _proses(path: Path, source: str) -> None:
    _catat("processing", path, source)
    try:
        res = pipeline.process_audio(path, _CFG, source=source, auto_apply=True)
        _STATS["processed"] += 1
        _catat("processed", path, source,
               {"report_id": res.get("report_id"),
                "category": (res.get("analysis") or {}).get("category")})
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
        _THREAD = threading.Thread(target=_worker, name="garuda-live-loop", daemon=True)
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
        if _Q.empty() and _STATS["queued"] <= _STATS["processed"] + _STATS["failed"]:
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
```

- [ ] **Step 4: Jalankan test, pastikan LULUS**

Run: `.\.venv\Scripts\python.exe test_live.py`

Expected: `HASIL: 9/9 PASS`

Catatan: `test_klip_terlalu_pendek` dan `test_llm_mati_transkrip_tetap_ada`
melewati diri sendiri (PASS) bila `samples/*.wav` belum ada. Buat sample dulu
dengan `.\.venv\Scripts\python.exe -c "import samples_gen; samples_gen.ensure_samples()"`
supaya benar-benar teruji.

- [ ] **Step 5: Backup**

Run: `Copy-Item loop.py loop.py.bak -Force`

---

### Task 3: Jaminan dua klip tidak saling menimpa

**Files:**
- Modify: `test_live.py`
- Modify: `loop.py` (bila perlu)

**Interfaces:**
- Consumes: `loop.enqueue_clip()` dari Task 2
- Produces: jaminan bahwa dua klip berbeda dengan isi berbeda menghasilkan dua baris

- [ ] **Step 1: Tulis test**

```python
def test_dua_klip_jadi_dua_baris():
    import pipeline
    paths = sorted(config.SAMPLES_DIR.glob("*.wav"))
    if len(paths) < 2:
        return check("dilewati: butuh 2 sample audio", True)
    a = pipeline.process_audio(paths[0], cfg, source="live", auto_apply=True)
    b = pipeline.process_audio(paths[1], cfg, source="live", auto_apply=True)
    return (check("klip pertama tersimpan", bool(a.get("transcript_id")))
            and check("klip kedua tersimpan (tidak terdedupe)", bool(b.get("transcript_id")))
            and check("dua transcript berbeda",
                      a.get("transcript_id") != b.get("transcript_id")))
```

Tambahkan ke `run()`.

- [ ] **Step 2: Jalankan**

Run: `.\.venv\Scripts\python.exe test_live.py`

Expected: PASS. Bila gagal dengan transcript_id sama, berarti dua klip menulis
path yang sama — perbaiki dengan menyalin klip ke path unik di `enqueue_clip`
sebelum `_Q.put_nowait`, memakai `tempfile.mkstemp(suffix=".wav")`.

- [ ] **Step 3: Backup**

Run: `Copy-Item test_live.py test_live.py.bak -Force`

---

## Fase 2 — Panel antrian & handler UI

### Task 4: Panel antrian live yang dibaca dari DB

**Files:**
- Modify: `app.py` — tambah `live_queue_html()` dan `live_queue_table()`
- Modify: `database.py` — tambah `loop_feed(limit)` (query baca saja)
- Test: `test_live.py` (uji query DB)

**Interfaces:**
- Consumes: `events_log` baris `entity="loop"` dari Task 2
- Produces:
  - `database.loop_feed(limit: int = 30) -> list[dict]` — baris digabung
    `events_log` (loop) + `reports`, terbaru dulu, kolom: `waktu`, `status`,
    `ringkas`, `kategori`, `aksi`, `report_id`
  - `app.live_queue_table() -> list[list]` untuk `gr.Dataframe`

- [ ] **Step 1: Tulis test**

```python
def test_loop_feed_dari_db():
    import loop
    import database as db_local
    loop.set_sync_for_test(True)
    paths = sorted(config.SAMPLES_DIR.glob("*.wav"))
    if not paths:
        return check("dilewati: tidak ada sample audio", True)
    loop.enqueue_clip(paths[0], source="live")
    feed = db_local.loop_feed(30)
    ok = bool(feed) and all(set(r) >= {"waktu", "status", "ringkas"} for r in feed)
    loop.reset_for_test()
    return check("feed antrian dibaca dari database", ok, f"{len(feed)} baris")
```

- [ ] **Step 2: Jalankan, pastikan GAGAL**

Run: `.\.venv\Scripts\python.exe test_live.py`

Expected: FAIL — `AttributeError: module 'database' has no attribute 'loop_feed'`

- [ ] **Step 3: Tambah `loop_feed()` di `database.py`**

Buat backup dulu: `Copy-Item database.py database.py.bak -Force`

Tambahkan setelah `recent_log()` (baris ~188). **Tambahkan `from pathlib import Path`
ke blok import `database.py`** — saat ini modul itu belum mengimpornya, sedangkan
`_dari_detail()` memakainya. `json` sudah ada.

```python
def loop_feed(limit: int = 30) -> list[dict]:
    """Umpan mode dengar-terus: transisi antrian + ringkasan laporan terkait.

    Dibaca langsung dari database supaya panel UI tetap benar setelah refresh.
    """
    with connect() as conn:
        rows = _rows(conn.execute(
            """SELECT e.id, e.action, e.detail, e.created_at,
                      r.id AS report_id, r.category, r.summary, r.action AS r_action
               FROM events_log e
               LEFT JOIN reports r ON r.id = CAST(json_extract(e.detail,'$.report_id') AS INTEGER)
               WHERE e.entity='loop'
               ORDER BY e.id DESC LIMIT ?""", (limit,)).fetchall())
    label = {"queued": "Menunggu", "processing": "Diproses",
             "processed": "Selesai", "failed": "Gagal", "discarded": "Dibuang"}
    out = []
    for r in rows:
        out.append({
            "waktu": r["created_at"],
            "status": label.get(r["action"], r["action"]),
            "ringkas": (r["summary"] or "")[:120] or _dari_detail(r["detail"]),
            "kategori": r["category"] or "",
            "aksi": r["r_action"] or "",
            "report_id": r["report_id"],
        })
    return out


def _dari_detail(detail) -> str:
    """Ambil nama file dari detail JSON untuk baris yang belum punya laporan."""
    try:
        d = json.loads(detail) if isinstance(detail, str) else (detail or {})
        return str(d.get("source", "")) + " " + str(Path(d.get("path", "")).name)
    except Exception:
        return ""
```

Pastikan `json` dan `Path` sudah di-import di bagian atas `database.py`; bila
belum, tambahkan. `json` sudah ada; `Path` **belum** — tambahkan
`from pathlib import Path`.

- [ ] **Step 4: Jalankan test, pastikan LULUS**

Run: `.\.venv\Scripts\python.exe test_live.py`

Expected: PASS

- [ ] **Step 5: Tambah panel UI di `app.py`**

Buat backup dulu: `Copy-Item app.py app.py.bak -Force`

Tambahkan fungsi ini di dekat `_refresh_all()` (baris ~206):

```python
LIVE_COLS = ["Waktu", "Status", "Ringkasan", "Kategori", "Aksi", "ID Laporan"]


def live_queue_table():
    """Tabel antrian mode dengar-terus (dibaca dari DB, bukan dari memori)."""
    return [[r["waktu"], r["status"], r["ringkas"], r["kategori"], r["aksi"],
             r["report_id"]] for r in db.loop_feed(30)]
```

Di dalam `build_ui()`, di bawah `sample_msg`/`demo_result` (baris ~483-484),
tambahkan:

```python
                gr.Markdown("### Antrian mode dengar terus")
                live_status = gr.Markdown("Mode dengar terus: **nonaktif**.")
                live_df = gr.Dataframe(headers=LIVE_COLS, value=live_queue_table(),
                                       interactive=False, wrap=True, elem_id="live_queue")
                live_note = gr.Markdown("<span class='muted'>Panel ini terbaca dari database "
                                        "dan memperbarui diri setiap 2 detik.</span>")
```

Dan di blok wiring (dekat baris 596), tambahkan timer:

```python
            live_timer = gr.Timer(2.0, active=True)
            live_timer.tick(live_queue_table, None, live_df)
```

- [ ] **Step 6: Pastikan UI masih naik**

Run: `.\.venv\Scripts\python.exe smoke_ui.py`

Expected: **SMOKE PASS** (HTTP 200)

- [ ] **Step 7: Backup**

Run: `Copy-Item app.py app.py.bak -Force`

---

### Task 5: `do_ingest_clip()` + tombol mulai/hentikan

**Files:**
- Modify: `app.py` — handler `do_ingest_clip`, `do_start_listening`, `do_stop_listening`
- Modify: `app.py` `launch()` — tambahkan `allowed_paths` untuk `models/vad`

**Interfaces:**
- Consumes: `loop.enqueue_clip()`, `loop.start_worker()`, `loop.stop_worker()`
- Produces:
  - `app.do_ingest_clip(audio_path: str | None) -> tuple[str, ...]` — dipanggil `.stop_recording()`
  - `app.do_start_listening() -> str`
  - `app.do_stop_listening() -> str`

- [ ] **Step 1: Tulis handler di `app.py`** (di dekat `process_audio_fn`)

```python
_LISTENING = threading.Event()


def do_start_listening():
    if not config.vad_assets_ready():
        return ("❌ Aset VAD belum ada. Jalankan: `.venv\\Scripts\\python.exe "
                "setup_models.py vad` lalu muat ulang halaman.")
    loop.start_worker(cfg)
    _LISTENING.set()
    return "Mode dengar terus: **AKTIF** — berbicara ke mikrofon, transmisi diproses otomatis."


def do_stop_listening():
    _LISTENING.clear()
    loop.stop_worker()
    return "Mode dengar terus: **nonaktif**. Antrian yang tersisa tetap diproses."


def do_ingest_clip(audio_path):
    """Dipanggil setiap kali satu klip rekaman selesai (dari VAD browser)."""
    if not _LISTENING.is_set():
        return "Mode dengar terus nonaktif — klip diabaikan."
    if not audio_path:
        return "Klip kosong — diabaikan."
    res = loop.enqueue_clip(audio_path, source="live")
    return res["message"]
```

Tambahkan `import loop` di blok import `app.py` (baris ~19-27).

- [ ] **Step 2: Tambah tombol di `build_ui()`**

Di dalam tab "Monitor & Analisis", tepat sebelum `gr.Markdown("### Antrian mode dengar terus")`:

```python
                with gr.Row():
                    btn_listen_on = gr.Button("🟢 Mulai dengar terus", variant="primary", scale=1)
                    btn_listen_off = gr.Button("⏹ Hentikan", scale=1)
```

- [ ] **Step 3: Wiring**

```python
            btn_listen_on.click(do_start_listening, None, live_status)
            btn_listen_off.click(do_stop_listening, None, live_status)
            audio_in.stop_recording(do_ingest_clip, [audio_in], [live_status])
```

- [ ] **Step 4: Jalankan smoke**

Run: `.\.venv\Scripts\python.exe smoke_ui.py`

Expected: SMOKE PASS

- [ ] **Step 5: Uji manual (wajib, tanpa VAD)**

Gunakan mode manual: klik "Mulai dengar terus", rekam satu klip lewat tombol
rekam `gr.Audio`, tekan stop.

Expected: `live_status` menampilkan "Klip 3.2s masuk antrian."; dalam ≤30 detik
baris baru muncul di tabel antrian dengan status "Selesai".

Catat hasil apa adanya. Bila tidak muncul, laporkan — jangan tandai selesai.

- [ ] **Step 6: Backup**

Run: `Copy-Item app.py app.py.bak -Force`

---

## Fase 3 — VAD di browser & aset offline

### Task 6: `setup_models.py vad` + `config.vad_assets_ready()`

**Files:**
- Modify: `config.py` — `VAD_DIR`, `VAD_ASSET_VERSION`, `vad_assets_ready()`
- Modify: `setup_models.py` — sub-perintah `vad`
- Test: `test_live.py`

**Interfaces:**
- Consumes: `config.MODELS_DIR`
- Produces:
  - `config.VAD_DIR: Path` = `MODELS_DIR / "vad"`
  - `config.vad_assets_ready() -> bool`
  - `setup_models.py vad` — mengunduh & menormalkan aset, gagal keras bila tidak lengkap

- [ ] **Step 1: Tulis test**

```python
def test_vad_assets_ready_graceful():
    return check("vad_assets_ready() tidak crash saat aset belum ada",
                 isinstance(config.vad_assets_ready(), bool))
```

- [ ] **Step 2: Jalankan, pastikan GAGAL**

Run: `.\.venv\Scripts\python.exe test_live.py`

Expected: FAIL — `AttributeError: module 'config' has no attribute 'vad_assets_ready'`

- [ ] **Step 3: Tambah ke `config.py`**

Setelah `AUTO_APPLY_ACTIONS`:

```python
# Aset VAD browser (deteksi suara) - diunduh sekali lewat setup_models.py vad.
# Versi dipin supaya nama file aset tidak bergeser tanpa disadari.
VAD_ASSET_VERSION = "0.0.9"
VAD_ONNXRT_VERSION = "1.18.0"
VAD_DIR = MODELS_DIR / "vad"

VAD_REQUIRED_FILES = [
    "silero_vad.onnx",
    "vad-web.bundle.min.js",
    "ort.min.js",
    "ort-wasm-simd-threaded.wasm",
]


def vad_assets_ready() -> bool:
    """True bila semua aset VAD ada. Tidak pernah crash."""
    try:
        return all((VAD_DIR / f).exists() for f in VAD_REQUIRED_FILES)
    except Exception:
        return False
```

- [ ] **Step 4: Tambah sub-perintah `vad` di `setup_models.py`**

```python
VAD_CDN = ("https://cdn.jsdelivr.net/npm/@ricky0123/vad-web@{ver}/dist/"
           "bundle.min.js")
VAD_CDN_ASSETS = [
    ("https://cdn.jsdelivr.net/npm/@ricky0123/vad-web@{ver}/dist/silero_vad.onnx",
     "silero_vad.onnx"),
    ("https://cdn.jsdelivr.net/npm/@ricky0123/vad-web@{ver}/dist/bundle.min.js",
     "vad-web.bundle.min.js"),
    ("https://cdn.jsdelivr.net/npm/onnxruntime-web@{ort}/dist/ort.min.js",
     "ort.min.js"),
    ("https://cdn.jsdelivr.net/npm/onnxruntime-web@{ort}/dist/ort-wasm-simd-threaded.wasm",
     "ort-wasm-simd-threaded.wasm"),
]


def install_vad() -> int:
    """Unduh aset VAD sekali (butuh internet). Gagal keras bila tidak lengkap."""
    import urllib.request
    config.VAD_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Mengunduh aset VAD ke {config.VAD_DIR} ...")
    for url_tpl, name in VAD_CDN_ASSETS:
        url = url_tpl.format(ver=config.VAD_ASSET_VERSION, ort=config.VAD_ONNXRT_VERSION)
        dst = config.VAD_DIR / name
        try:
            with urllib.request.urlopen(url, timeout=120) as r, open(dst, "wb") as f:
                f.write(r.read())
            print(f"  OK  {name}")
        except Exception as exc:
            print(f"  GAGAL {name}: {exc}")
    if config.vad_assets_ready():
        print("Aset VAD lengkap.")
        return 0
    hilang = [f for f in config.VAD_REQUIRED_FILES if not (config.VAD_DIR / f).exists()]
    print(f"BELUM LENGKAP, file hilang: {', '.join(hilang)}")
    return 1
```

Tambahkan cabang di `main()`:

```python
        elif arg == "vad":
            return install_vad()
```

Tambahkan baris status di `status()`:

```python
    print(f"  vad (browser)                  : {'OK' if config.vad_assets_ready() else 'BELUM ADA (opsional, untuk mode dengar terus)'}")
```

- [ ] **Step 5: Jalankan**

Run: `.\.venv\Scripts\python.exe setup_models.py vad`
Then: `.\.venv\Scripts\python.exe test_live.py`

Expected: `HASIL: 10/10 PASS`

- [ ] **Step 6: Backup**

Run: `Copy-Item config.py config.py.bak -Force; Copy-Item setup_models.py setup_models.py.bak -Force`

---

### Task 7: Skrip VAD di `launch(js=)`

**Files:**
- Modify: `app.py` — `VAD_JS` konstanta + `launch(js=VAD_JS)`
- Modify: `app.py` `launch()` — `allowed_paths` + `config.VAD_DIR`

**Interfaces:**
- Consumes: `config.VAD_DIR` (disajikan lewat `allowed_paths`), elemen `#audio_in`,
  `#live_queue`
- Produces: VAD yang memicu tombol rekam `gr.Audio` saat suara mulai/berhenti

- [ ] **Step 1: Tambah `VAD_JS` di `app.py`**

```python
VAD_JS = """
async function initGarudaVad() {
  if (!window.__GARUDA_VAD_READY__) return;
  const btn = () => document.querySelector('#audio_in .record-button');
  const stopBtn = () => document.querySelector('#audio_in .stop-button');
  let myvad = null;
  try {
    const mod = await import('/gradio_api/file=__VAD_BASE__/vad-web.bundle.min.js');
    const vad = mod.default || mod;
    myvad = await vad.MicVAD.new({
      baseAssetPath: '__VAD_BASE__/',
      onnxWASMBasePath: '__VAD_BASE__/',
      onSpeechStart: () => { const b = btn(); if (b) b.click(); },
      onSpeechEnd: () => { const s = stopBtn(); if (s) s.click(); },
    });
    myvad.start();
    window.__GARUDA_VAD__ = myvad;
    console.log('[garuda] VAD aktif');
  } catch (e) {
    console.error('[garuda] VAD gagal:', e);
  }
}
setTimeout(initGarudaVad, 1500);
"""
```

- [ ] **Step 2: Ubah `launch()`**

```python
def launch():  # pragma: no cover
    db.init_db()
    port = int(os.environ.get("GARUDA_PORT", "7860"))
    demo = build_ui()
    demo.queue(default_concurrency_limit=2)
    js = VAD_JS.replace("__VAD_BASE__", "/gradio_api/file=" + config.VAD_DIR.as_posix())
    print(f"GARUDA Radio AI siap di http://127.0.0.1:{port}")
    demo.launch(server_name="127.0.0.1", server_port=port, inbrowser=True,
                footer_links=[], ssr_mode=False, theme=gr.themes.Soft(), css=CSS,
                js=js,
                allowed_paths=[str(config.EXPORT_DIR), str(config.SAMPLES_DIR),
                               str(config.VAD_DIR)])
```

- [ ] **Step 3: Catat asumsi yang belum terbukti**

Buka halaman, cek konsol browser. Expected: `[garuda] VAD aktif` dan tidak ada
error 404 untuk aset. Bila selector `#audio_in .record-button` tidak ada,
periksa DOM dan sesuaikan selector — **ini bagian paling rapuh** dan harus
diverifikasi di browser sungguhan, bukan ditebak.

- [ ] **Step 4: Uji ucap-langsung**

Klik "Mulai dengar terus", bicara satu kalimat, diam. Expected: satu baris
muncul di antrian tanpa menyentuh tombol apa pun.

- [ ] **Step 5: Backup + dokumentasi agent**

Run: `Copy-Item app.py app.py.bak -Force`

Perbarui `AGENTS.md`: tambah `loop.py` ke tabel file, catat `AUTO_APPLY_ACTIONS`,
langkah `setup_models.py vad`, dan peringatan bahwa mode ini butuh aset VAD.
Jangan sentuh `README.md`.

---

## Fase 4 — Panduan pengguna

### Task 8: Bagian "Mode Dengar Terus" di `README.md`

**Files:**
- Modify: `README.md` — bagian baru setelah "Tab UI"

Hanya dikerjakan **setelah** Task 7 lulus uji ucap-langsung.

- [ ] **Step 1: Tulis bagian README** berisi: cara mulai/hentikan, arti indikator
  status, cara membaca antrian, tabel aksi otomatis vs menunggu persetujuan,
  langkah `setup_models.py vad`, batasan (izin mic, satu operator, lag LLM di CPU),
  dan panduan production HT → line-in.
- [ ] **Step 2: Pastikan setiap kalimat benar-benar teruji** — hapus atau tandai
  "belum terverifikasi" untuk apa pun yang belum dijalankan.

---

## Self-Review

**Spec coverage:**
- §7 daftar putih → Task 1 ✅
- §8 panel antrian + state DB → Task 2, Task 4 ✅
- §9 `loop.py`, `audio.py`, `actions.py`, `pipeline.py` → Task 1, 2 ✅
- §9 aset VAD + cara JS dipasang → Task 6, 7 ✅
- §10 kasus tepi → Task 2 (pendek, LLM mati, antrian penuh), Task 3 (path) ✅
- §12 pemutar file → sengaja tidak diimplementasi; alternatif "uji cepat" sudah
  tersedia lewat Task 5 Step 5 (rekam manual). Spec menyatakan ini keputusan
  builder: **tidak ditambahkan** karena duplikasi jalur.
- §13 angka awal → Global Constraints ✅
- §15 verifikasi → Task 1 Step 7, Task 4 Step 6, `test_live.py` ✅
- §18 README ditunda → Task 8 di akhir ✅

**Catatan penyimpangan:** `AGENTS.md` melarang git, jadi langkah "commit" diganti
backup `.bak` di setiap task.
