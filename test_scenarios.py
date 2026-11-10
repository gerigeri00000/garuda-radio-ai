"""
GARUDA Radio AI - pengujian enam skenario end-to-end.

Alur yang diuji: audio (TTS lokal) -> Whisper (model lokal) -> LLM lokal ->
JSON -> validasi -> aturan aksi -> SQLite -> dashboard/peta.

Jalankan:  .venv\\Scripts\\python.exe test_scenarios.py
Audio diuji sungguhan (tanpa mock). Struktur hasil AI tetap diverifikasi.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import actions
import ai
import audio
import config
import database as db
import pipeline
import samples_gen

cfg = config.load_config()


def section(t: str):
    print("\n" + "=" * 70)
    print(t)
    print("=" * 70)


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return ok


def ticket_count() -> int:
    return len(db.list_tickets(1000))


def alert_count() -> int:
    return len(db.list_alerts(1000))


def run() -> int:
    db.init_db()
    reset = "--keep-db" not in sys.argv
    if reset:
        for t in ("transcripts", "reports", "tickets", "alerts", "map_updates", "events_log"):
            with db.connect() as c:
                c.execute(f"DELETE FROM {t}")
                c.execute("UPDATE sqlite_sequence SET seq=0 WHERE name=?", (t,))
        for loc in db.list_locations():
            with db.connect() as c:
                c.execute("UPDATE locations SET status='Normal' WHERE key=?", (loc["key"],))
        print("Database di-reset ke kondisi bersih (pakai --keep-db untuk mempertahankan).")
    results: list[bool] = []

    section("0. Prasyarat model & dependensi")
    # Model Whisper yang AKTIF (faster-whisper atau MLX di Apple Silicon).
    whisper_be = audio.whisper_backend(cfg)
    whisper_ok = whisper_be["engine"] is not None
    results.append(check(f"Whisper terinstal ({whisper_be['label']})", whisper_ok))
    if not whisper_ok:
        print(f"  Jalankan: python setup_models.py whisper-{cfg['whisper_model']}")
    results.append(check("TTS Indonesia terinstal (untuk audio demo)", samples_gen.tts_installed()))
    if not samples_gen.tts_installed():
        print("  Jalankan: python setup_models.py tts-id")
    llm_ok = config.llm_installed(cfg["llm_model"])
    results.append(check(f"LLM terinstal ({cfg['llm_model']})", llm_ok))
    if not llm_ok:
        print("  Jalankan: python setup_models.py llm-3b")
    if not (whisper_ok and llm_ok):
        return 1

    section("1. Membuat audio simulasi (TTS lokal)")
    all_sc = samples_gen.SCENARIOS + list(samples_gen.SCENARIOS_EXTRA.values())
    for sc in all_sc:
        path = samples_gen.generate(sc)
        assert Path(path).exists(), path
    print(f"  OK: {len(all_sc)} file audio di", config.SAMPLES_DIR)

    purchases = ticket_count()
    alerts_before = alert_count()

    # ------------------------------------------------------------------
    section("Skenario 1 - Permintaan logistik ('Pos Alfa ... 10 dus')")
    sc = samples_gen.SCENARIOS[0]
    res = pipeline.process_audio(samples_gen.generate(sc), cfg, source="test")
    an, act = res["analysis"], res["action"]
    print("  transkrip :", res["transcript"])
    print("  kategori  :", an.get("category"), "| aksi:", an.get("action"),
          "| qty:", an.get("quantity"), an.get("unit_measure"), "| lokasi:", an.get("location"))
    results.append(check("transkrip tidak kosong", bool(res["transcript"].strip())))
    results.append(check("JSON valid", bool(an.get("llm_valid"))))
    results.append(check("klasifikasi dari LLM (bukan fallback aturan)",
                         an.get("analysis_source") == "llm", str(an.get("analysis_source"))))
    results.append(check("kategori LOGISTICS", an.get("category") == "LOGISTICS"))
    results.append(check("jumlah 10 dus terdeteksi",
                         an.get("quantity") == 10 and (an.get("unit_measure") or "").lower() == "dus"))
    if act.get("ticket"):
        t = act["ticket"]
        results.append(check("draft tiket dibuat", t["status"] == "Pending",
                             f"#{t['id']} {t['item']} {t['quantity']} {t['unit_measure']}"))
        results.append(check("jumlah 10 terdeteksi", t["quantity"] == 10))
    else:
        results.append(check("tiket/draft dibuat atau laporan tersimpan",
                             an.get("action") in ("CREATE_REPORT", "CREATE_TICKET"),
                             f"aksi={an.get('action')}"))

    # ------------------------------------------------------------------
    section("Skenario 2 - Permintaan logistik ('Pos Bravo ... 30 liter solar')")
    sc = samples_gen.SCENARIOS_EXTRA["solar"]
    res = pipeline.process_audio(samples_gen.generate(sc), cfg, source="test")
    an, act = res["analysis"], res["action"]
    print("  transkrip :", res["transcript"])
    print("  kategori  :", an.get("category"), "| aksi:", an.get("action"),
          "| qty:", an.get("quantity"), an.get("unit_measure"))
    results.append(check("JSON valid", bool(an.get("llm_valid"))))
    results.append(check("aksi CREATE_TICKET", an.get("action") == "CREATE_TICKET",
                         f"dapat: {an.get('action')}"))
    results.append(check("draft tiket Pending dibuat", bool(act.get("ticket")),
                         f"tiket={act.get('ticket', {}).get('id') if act.get('ticket') else None}"))
    if act.get("ticket"):
        t = act["ticket"]
        results.append(check("tiket Pending & 30 liter",
                             t["status"] == "Pending" and t["quantity"] == 30
                             and (t["unit_measure"] or "").lower() == "liter",
                             f"#{t['id']} {t['quantity']} {t['unit_measure']}"))
        results.append(check("tiket belum diproses sebelum persetujuan",
                             t["status"] != "Completed"))

    section("Skenario 3 - Kerusakan peralatan ('genset Pos Bravo rusak')")
    sc = samples_gen.SCENARIOS[1]
    res = pipeline.process_audio(samples_gen.generate(sc), cfg, source="test")
    an, act = res["analysis"], res["action"]
    print("  transkrip :", res["transcript"])
    print("  kategori  :", an.get("category"), "| aksi:", an.get("action"), "| unit:", an.get("unit"))
    results.append(check("JSON valid", bool(an.get("llm_valid"))))
    results.append(check("kategori EQUIPMENT", an.get("category") == "EQUIPMENT",
                         f"dapat: {an.get('category')}"))
    results.append(check("unit Pos Bravo terdeteksi",
                         "bravo" in (an.get("unit") or "").lower(), an.get("unit") or "-"))
    results.append(check("tidak ada alert palsu (bukan medis)", an.get("category") != "MEDICAL"))

    # ------------------------------------------------------------------
    section("Skenario 3 - Penyelesaian kegiatan ('genset Pos Alfa selesai')")
    sc = samples_gen.SCENARIOS[2]
    res = pipeline.process_audio(samples_gen.generate(sc), cfg, source="test")
    an, act = res["analysis"], res["action"]
    print("  transkrip :", res["transcript"])
    print("  kategori  :", an.get("category"), "| aksi:", an.get("action"))
    print("  langkah   :", act.get("steps"))
    results.append(check("JSON valid", bool(an.get("llm_valid"))))
    results.append(check("aksi UPDATE_STATUS", an.get("action") == "UPDATE_STATUS",
                         f"dapat: {an.get('action')}"))
    changed = [t for t in db.list_tickets(500) if t["status"] == "Completed"]
    results.append(check("tidak ada tiket yang otomatis diubah jadi Completed", len(changed) == 0,
                         f"{len(changed)} tiket"))

    # ------------------------------------------------------------------
    section("Skenario 4 - Bantuan medis ('warga butuh bantuan medis segera')")
    sc = samples_gen.SCENARIOS[3]
    res = pipeline.process_audio(samples_gen.generate(sc), cfg, source="test")
    an, act = res["analysis"], res["action"]
    print("  transkrip :", res["transcript"])
    print("  kategori  :", an.get("category"), "| aksi:", an.get("action"),
          "| prioritas:", an.get("priority"))
    results.append(check("JSON valid", bool(an.get("llm_valid"))))
    results.append(check("kategori MEDICAL", an.get("category") == "MEDICAL"))
    results.append(check("alert dibuat", alert_count() > alerts_before,
                         f"alert: {alert_count() - alerts_before}"))
    results.append(check("prioritas HIGH/CRITICAL", an.get("priority") in ("HIGH", "CRITICAL")))
    results.append(check("aksi CREATE_ALERT", an.get("action") == "CREATE_ALERT"))

    # ------------------------------------------------------------------
    section("Skenario 5 - Infrastruktur ('Jembatan Desa Contoh rusak')")
    sc = samples_gen.SCENARIOS[4]
    res = pipeline.process_audio(samples_gen.generate(sc), cfg, source="test")
    an, act = res["analysis"], res["action"]
    print("  transkrip :", res["transcript"])
    print("  kategori  :", an.get("category"), "| aksi:", an.get("action"),
          "| lokasi:", an.get("location"))
    results.append(check("JSON valid", bool(an.get("llm_valid"))))
    results.append(check("kategori INFRASTRUCTURE", an.get("category") == "INFRASTRUCTURE"))
    loc = (an.get("location") or "").lower()
    results.append(check("lokasi dari daftar DEMO", loc in config.DEMO_LOCATIONS, loc or "-"))
    if act.get("map_update"):
        u = act["map_update"]
        results.append(check("update peta status Pending Approval",
                             u["status"] == "Pending Approval",
                             f"#{u['id']} {u['location_name']} -> {u['new_status']}"))
        r = actions.approve_map_update(u["id"])
        after = [l for l in db.list_locations() if l["key"] == u["location_key"]][0]
        results.append(check("persetujuan operator mengubah status lokasi",
                             r["ok"] and after["status"] == u["new_status"], after["status"]))
    else:
        results.append(check("laporan infrastruktur tersimpan (update peta opsional)",
                             bool(act.get("report_id")), f"aksi={an.get('action')}"))

    # ------------------------------------------------------------------
    section("Skenario 6 - Suara berisik / informasi tidak lengkap")
    sc = samples_gen.SCENARIOS[5]
    noisy = samples_gen.generate(sc, noise=True)
    res = pipeline.process_audio(noisy, cfg, source="test")
    an, act = res["analysis"], res["action"]
    print("  transkrip :", res["transcript"] or "(kosong)")
    print("  kategori  :", an.get("category"), "| aksi:", an.get("action"),
          "| verifikasi:", an.get("needs_verification"), "| info hilang:", an.get("missing_information"))
    results.append(check("sistem tetap menyimpan (tidak crash)", bool(res.get("action"))))
    results.append(check("ditandai perlu verifikasi / tidak yakin",
                         bool(an.get("needs_verification")),
                         f"ver={an.get('needs_verification')} conf={an.get('confidence')}"))
    results.append(check("confidence rendah / informasi hilang tercatat",
                         an.get("confidence", 1) < 0.65 or bool(an.get("missing_information")),
                         f"conf={an.get('confidence')} miss={an.get('missing_information')}"))
    results.append(check("tidak membuat tiket atas dasar info tidak lengkap",
                         not act.get("ticket"), f"aksi={an.get('action')}"))

    # ------------------------------------------------------------------
    section("Anti-duplikasi: audio identik diproses 2x")
    sc = samples_gen.SCENARIOS[1]
    same = samples_gen.generate(sc)
    n_before = ticket_count()
    r1 = pipeline.process_audio(same, cfg, source="test")
    n_mid = ticket_count()
    r2 = pipeline.process_audio(same, cfg, source="test")
    n_after = ticket_count()
    steps2 = " ".join(r2["action"].get("steps", []))
    print("  run-2 langkah:", steps2)
    results.append(check("run kedua tidak membuat tiket baru", n_after == n_mid,
                         f"{n_before} -> {n_mid} -> {n_after}"))
    results.append(check("run kedua menyebut duplikat", "identik" in steps2 or "duplikat" in steps2.lower()))

    section("Anti-duplikasi tiket: permintaan solar 30 liter diucapkan 2x")
    t_before = ticket_count()
    for txt in ("Gudang Logistik Pusat membutuhkan tambahan 20 liter solar.",
                "Gudang Logistik Pusat membutuhkan tambahan 20 liter solar."):
        pipeline.process_text(txt, cfg)
    t_after = ticket_count()
    results.append(check("tiket solar baru dibuat sekali dari 2 ucapan sama",
                         t_after - t_before == 1, f"tiket baru: {t_after - t_before}"))

    section("Peringatan medis tidak membuat instruksi / personel")
    med = [a for a in db.list_alerts(100) if a["category"] == "MEDICAL"]
    results.append(check("alert medis ada", len(med) > 0))
    results.append(check("alert berstatus Needs Review (bukan otomatis ditangani)",
                         all(a["status"] in ("Needs Review", "Acknowledged") for a in med)))

    section("Dashboard & rekap")
    s = db.stats()
    print("  ", s)
    results.append(check("dashboard menghitung laporan > 0", s["reports"] > 0))
    with db.connect() as c:
        pending = c.execute("SELECT COUNT(*) FROM tickets WHERE status='Pending'").fetchone()[0]
    results.append(check("tiket Pending menunggu persetujuan terlihat di dashboard", pending >= 1,
                         f"{pending} pending"))
    with db.connect() as c:
        n_alerts = c.execute("SELECT COUNT(*) FROM alerts WHERE status='Needs Review'").fetchone()[0]
    results.append(check("peringatan baru tampil di dashboard", n_alerts >= 1, f"{n_alerts} alert"))
    html_dash = __import__("app").dashboard_html()
    results.append(check("dashboard HTML berisi angka laporan", "Laporan" in html_dash))
    import reports

    rec = reports.build_recap(cfg=cfg)
    csv_path = reports.export_csv(rec)
    pdf_path = reports.export_pdf(rec)
    import os

    results.append(check("ekspor CSV", os.path.getsize(csv_path) > 100, csv_path))
    results.append(check("ekspor PDF", os.path.getsize(pdf_path) > 1000, pdf_path))
    print(rec["text"][:600])

    failed = results.count(False)
    section(f"HASIL: {len(results) - failed}/{len(results)} PASS")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(run())
