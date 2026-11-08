"""Uji mode dengar-terus: aturan aksi otomatis & antrian klip.

Jalankan:  .venv\\Scripts\\python.exe test_live.py
Berkas ini RESET database sendiri di awal dan di akhir, terpisah dari
test_scenarios.py (baseline 47/47 tidak disentuh).
"""
from __future__ import annotations

import sys
import threading
import time

import actions
import config
import database as db
import pipeline

cfg = config.load_config()


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
    lok = [x for x in db.list_locations() if x["key"] == "jembatan desa contoh"][0]
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


def test_klip_terlalu_pendek():
    import loop
    paths = sorted(config.SAMPLES_DIR.glob("*.wav"))
    if not paths:
        return check("dilewati: tidak ada sample audio", True)
    res = loop.enqueue_clip(paths[0], source="live", _force_duration=0.4)
    return (check("klip terlalu pendek ditolak",
                  res["ok"] is False and res["reason"] == "too_short")
            and check("alasan dijelaskan ke operator", bool(res["message"])))


def test_antrian_penuh_buang_tertua():
    import loop
    paths = sorted(config.SAMPLES_DIR.glob("*.wav"))
    if not paths:
        return check("dilewati: tidak ada sample audio", True)
    loop.reset_for_test()
    # JANGAN pakai mode sync: antrian penuh hanya terjadi di mode async.
    hasil = []
    for _ in range(loop.QUEUE_MAX + 3):
        hasil.append(loop.enqueue_clip(paths[0], source="live",
                                       _force_duration=5.0))
    stats = loop.worker_stats()
    # 3 klip pertama dibuang; sisanya masuk (maks QUEUE_MAX)
    ok = stats["discarded"] >= 3 and stats["antrian"] <= loop.QUEUE_MAX
    loop.reset_for_test()
    return check("antrian penuh membuang klip tertua", ok, str(stats))


def test_loop_feed_dari_db():
    import loop
    paths = sorted(config.SAMPLES_DIR.glob("*.wav"))
    if not paths:
        return check("dilewati: tidak ada sample audio", True)
    loop.reset_for_test()
    loop.set_sync_for_test(True)
    loop.enqueue_clip(paths[0], source="live")
    feed = db.loop_feed(30)
    ok = bool(feed) and all({"waktu", "status", "ringkas"} <= set(r) for r in feed)
    loop.reset_for_test()
    return check("feed antrian dibaca dari database", ok, f"{len(feed)} baris")


def test_vad_assets_ready_graceful():
    return check("vad_assets_ready() tidak crash saat aset belum ada",
                 isinstance(config.vad_assets_ready(), bool))


def test_feed_menampilkan_kategori_dan_aksi():
    """Baris 'Selesai' harus memuat kategori & aksi, bukan kosong."""
    import loop
    paths = sorted(config.SAMPLES_DIR.glob("*.wav"))
    if not paths:
        return check("dilewati: tidak ada sample audio", True)
    loop.reset_for_test()
    loop.set_sync_for_test(True)
    loop.enqueue_clip(paths[0], source="live")
    feed = db.loop_feed(10)
    loop.reset_for_test()
    selesai = [r for r in feed if r["status"] == "Selesai"]
    if not selesai:
        return check("ada baris Selesai di feed", False, f"{len(feed)} baris")
    r = selesai[0]
    return (check("baris Selesai punya kategori", bool(r["kategori"]), r["kategori"])
            and check("baris Selesai punya aksi", bool(r["aksi"]), r["aksi"])
            and check("baris Selesai punya report_id", bool(r["report_id"]),
                      str(r["report_id"])))


def test_feed_tahan_detail_json_rusak():
    """Satu baris audit rusak tidak boleh mengosongkan seluruh panel."""
    db.log("loop", None, "queued", "bukan json {{{", "system")
    try:
        feed = db.loop_feed(10)
        ok = isinstance(feed, list)
    except Exception as exc:
        return check("loop_feed tahan detail rusak", False, str(exc)[:80])
    return check("loop_feed tahan detail rusak", ok, f"{len(feed)} baris")


def test_stop_worker_tidak_hang_saat_antrian_penuh():
    """Hentikan harus selalu kembali, walau antrian penuh DAN worker hidup."""
    import loop
    loop.reset_for_test()
    loop.start_worker(cfg)
    for _ in range(loop.QUEUE_MAX):
        loop.enqueue_clip("x.wav", source="live", _force_duration=5.0)
    t0 = time.time()
    hasil = loop.stop_worker(timeout=2.0)
    durasi = time.time() - t0
    loop.reset_for_test()
    return check("stop_worker tidak hang saat antrian penuh & worker hidup",
                 durasi < 4.0 and isinstance(hasil, dict), f"{durasi:.2f}s")


def test_stop_worker_jujur_saat_masih_memproses():
    """Reviewer Critical #3: 'nonaktif' tidak boleh diucapkan sementara
    thread masih hidup memproses klip."""
    import loop
    loop.reset_for_test()
    asli = pipeline.process_audio
    tahan = threading.Event()

    def lambat(*a, **k):
        tahan.wait(10)
        raise RuntimeError("uji: proses lambat")

    pipeline.process_audio = lambat
    try:
        loop.start_worker(cfg)
        loop.enqueue_clip("x.wav", source="live", _force_duration=5.0)
        time.sleep(0.5)  # biarkan worker masuk ke _proses
        hasil = loop.stop_worker(timeout=0.5)
    finally:
        tahan.set()
        time.sleep(0.3)
        pipeline.process_audio = asli
        loop.reset_for_test()
    return check("stop_worker tidak bohong saat masih memproses",
                 hasil["stopped"] is False and "menyelesaikan" in hasil["message"],
                 str(hasil))


def test_baris_processing_yatim_ditandai_terputus():
    """Aplikasi mati saat klip sedang diproses: baris jangan selamanya
    "Diproses" - operator harus tahu transmisi itu tidak selesai."""
    from pathlib import Path
    p = str(Path("samples") / "mati_di_tengah.wav")
    db.log("loop", None, "processing", {"path": p, "source": "live"}, "system")
    feed = db.loop_feed(50, grace_seconds=0)  # anggap sudah lewat masa tenggang
    baris = [r for r in feed if r["ringkas"].endswith("mati_di_tengah.wav")]
    if not baris:
        return check("baris pemrosesan lama ditemukan", False, f"{len(feed)} baris")
    return check("baris processing yatim ditandai Terputus",
                 baris[0]["status"] == "Terputus", baris[0]["status"])


def test_klip_baru_diproses_bukan_terputus():
    """Critical #2: klip yang BARU saja diproses tidak boleh tampil Terputus
    padahal hasilnya belum ada (masih dikerjakan)."""
    from pathlib import Path
    p = str(Path("samples") / "sedang_dikerjakan.wav")
    db.log("loop", None, "processing", {"path": p, "source": "live"}, "system")
    feed = db.loop_feed(50)  # masa tenggang default
    baris = [r for r in feed if r["ringkas"].endswith("sedang_dikerjakan.wav")]
    if not baris:
        return check("baris pemrosesan baru ditemukan", False)
    return check("klip yang masih dikerjakan bukan Terputus",
                 baris[0]["status"] == "Diproses", baris[0]["status"])


def test_processing_yatim_luar_jendela_tampilan_tetap_terdeteksi():
    """Critical #1: baris yatim harus tetap terdeteksi walau panel hanya
    menampilkan 30 baris terakhir."""
    from pathlib import Path
    p = str(Path("samples") / "crash_lama.wav")
    db.log("loop", None, "processing", {"path": p, "source": "live"}, "system")
    for i in range(40):  # banjiri dengan klip lain
        db.log("loop", None, "queued",
               {"path": str(Path("samples") / f"banjir_{i}.wav"), "source": "live"},
               "system")
    feed = db.loop_feed(30, grace_seconds=0)
    tampil = [r for r in feed if r["ringkas"].endswith("crash_lama.wav")]
    return check("panel 30 baris tidak lagi menampilkan baris yatim",
                 len(tampil) == 0, f"{len(tampil)} baris")


def test_baris_processing_selesai_tetap_selesai():
    import loop
    paths = sorted(config.SAMPLES_DIR.glob("*.wav"))
    if not paths:
        return check("dilewati: tidak ada sample audio", True)
    loop.reset_for_test()
    loop.set_sync_for_test(True)
    loop.enqueue_clip(paths[0], source="live")
    feed = db.loop_feed(20)
    loop.reset_for_test()
    selesai = [r for r in feed if r["status"] == "Selesai"]
    diproses = [r for r in feed if r["ringkas"].endswith(paths[0].name)]
    return (check("ada baris Selesai", bool(selesai))
            and check("baris processing yang selesai bukan Terputus",
                      all(r["status"] != "Terputus" for r in diproses),
                      str([r["status"] for r in diproses])))

def test_klip_tidak_yatim_saat_antrian_penuh_lalu_stop():
    """Reviewer #7/#8: saat antrian penuh, klip yang dipotong harus DICATAT,
    dan stop_worker tidak boleh meninggalkan sentinel yang menghabiskan
    satu slot sehingga klip berikutnya hilang tanpa jejak.

    Catatan: tanpa pekerja berjalan, 2 klip terakhir memang terpotong.
    """
    import loop
    loop.reset_for_test()
    for _ in range(loop.QUEUE_MAX + 2):
        loop.enqueue_clip("x.wav", source="live", _force_duration=5.0)
    sebelum = loop.worker_stats()
    loop.stop_worker()
    sesudah = loop.worker_stats()
    loop.reset_for_test()
    return (check("klip yang dipotong tercatat 'discarded'",
                  sebelum["discarded"] >= 2, str(sebelum))
            and check("sentinel stop tidak memakan slot antrian",
                      sesudah["antrian"] == sebelum["antrian"], str(sesudah)))


def test_stop_lalu_start_tetap_memproses_klip_baru():
    """Stop lalu start harus melayani klip berikutnya (worker benar-benar
    menyala lagi, bukan tertinggal dengan event berhenti yang masih set)."""
    import loop
    loop.reset_for_test()
    loop.start_worker(cfg)
    loop.stop_worker()
    loop.start_worker(cfg)
    paths = sorted(config.SAMPLES_DIR.glob("*.wav"))
    if not paths:
        loop.reset_for_test()
        return check("dilewati: tidak ada sample audio", True)
    loop.enqueue_clip(paths[0], source="live")
    loop.drain(120)
    st = loop.worker_stats()
    loop.reset_for_test()
    return check("worker hidup lagi setelah stop lalu start",
                 st["processed"] >= 1, str(st))


def test_stop_lalu_start_tidak_menumpuk_worker():
    """Critical #4: Start->Stop->Start saat pemrosesan lambat tidak boleh
    menyisakan dua worker hidup yang bersaing di file audio yang sama."""
    import loop
    loop.reset_for_test()
    asli = pipeline.process_audio
    tahan = threading.Event()

    def lambat(*a, **k):
        tahan.wait(10)
        raise RuntimeError("uji: proses lambat")

    pipeline.process_audio = lambat
    try:
        loop.start_worker(cfg)
        loop.enqueue_clip("x.wav", source="live", _force_duration=5.0)
        time.sleep(0.5)
        loop.stop_worker(timeout=0.3)   # gagal berhenti: klip masih jalan
        loop.start_worker(cfg)          # tidak boleh membuat worker kedua
        hidup = [t for t in threading.enumerate() if t.name == "garuda-live-loop"]
    finally:
        tahan.set()
        time.sleep(0.5)
        pipeline.process_audio = asli
        loop.reset_for_test()
    return check("tidak ada dua worker hidup setelah stop lalu start",
                 len(hidup) <= 1, f"{len(hidup)} worker")


def test_klip_tetap_diproses_setelah_stop_lalu_start():
    """Stop lalu start tidak boleh meninggalkan klip yatim di antrian."""
    import loop
    loop.reset_for_test()
    loop.start_worker(cfg)
    paths = sorted(config.SAMPLES_DIR.glob("*.wav"))
    if not paths:
        loop.reset_for_test()
        return check("dilewati: tidak ada sample audio", True)
    loop.enqueue_clip(paths[0], source="live")
    loop.drain(120)
    st = loop.worker_stats()
    loop.reset_for_test()
    return check("klip diproses jadi Selesai",
                 st["processed"] >= 1 and st["failed"] == 0, str(st))


def test_dua_klip_jadi_dua_baris():
    import pipeline
    paths = sorted(config.SAMPLES_DIR.glob("*.wav"))
    if len(paths) < 2:
        return check("dilewati: butuh 2 sample audio", True)
    a = pipeline.process_audio(paths[0], cfg, source="live", auto_apply=True)
    b = pipeline.process_audio(paths[1], cfg, source="live", auto_apply=True)
    ta = (a.get("action") or {}).get("transcript_id")
    tb = (b.get("action") or {}).get("transcript_id")
    return (check("klip pertama tersimpan", bool(ta))
            and check("klip kedua tersimpan (tidak terdedupe)", bool(tb))
            and check("dua transcript berbeda", ta != tb, f"{ta} vs {tb}"))


def test_llm_mati_transkrip_tetap_ada():
    import pipeline
    paths = sorted(config.SAMPLES_DIR.glob("*.wav"))
    if not paths:
        return check("dilewati: tidak ada sample audio", True)
    asli = pipeline.ai.analyze

    def mati(*a, **k):
        raise pipeline.ai.LLMNotAvailable("LLM tidak tersedia (uji)")

    pipeline.ai.analyze = mati
    try:
        res = pipeline.process_audio(paths[0], cfg, source="live", auto_apply=True)
    finally:
        pipeline.ai.analyze = asli
    return (check("transkrip tetap tersimpan saat LLM mati",
                  bool(res.get("transcript")) and bool(
                      (res.get("action") or {}).get("transcript_id")))
            and check("laporan ditandai belum terklasifikasi",
                      (res.get("analysis") or {}).get("llm_valid") is False))


def run() -> int:
    results: list[bool] = []
    print("=" * 70)
    print("Uji mode dengar-terus")
    print("=" * 70)
    reset_db()
    for fn in (test_auto_apply_alert, test_map_tetap_pending,
               test_tiket_tetap_pending, test_status_update_tetap_usulan,
               test_jalur_manual_tidak_berubah, test_klip_terlalu_pendek,
               test_antrian_penuh_buang_tertua, test_loop_feed_dari_db,
               test_vad_assets_ready_graceful, test_feed_menampilkan_kategori_dan_aksi,
               test_feed_tahan_detail_json_rusak, test_stop_worker_tidak_hang_saat_antrian_penuh,
               test_stop_worker_jujur_saat_masih_memproses,
               test_baris_processing_yatim_ditandai_terputus,
               test_klip_baru_diproses_bukan_terputus,
               test_processing_yatim_luar_jendela_tampilan_tetap_terdeteksi,
               test_baris_processing_selesai_tetap_selesai,
               test_klip_tidak_yatim_saat_antrian_penuh_lalu_stop,
               test_stop_lalu_start_tetap_memproses_klip_baru,
               test_stop_lalu_start_tidak_menumpuk_worker,
               test_klip_tetap_diproses_setelah_stop_lalu_start,
               test_dua_klip_jadi_dua_baris,
               test_llm_mati_transkrip_tetap_ada):
        reset_db()
        results.append(fn())
    hits = sum(results)
    print(f"\nHASIL: {hits}/{len(results)} PASS")
    reset_db()
    return 0 if hits == len(results) else 1


if __name__ == "__main__":
    sys.exit(run())
