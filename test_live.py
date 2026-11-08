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
