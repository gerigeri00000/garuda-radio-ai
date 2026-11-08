"""
GARUDA Radio AI - action engine (Python murni, bukan LLM).

LLM hanya mengusulkan JSON. Modul ini memvalidasi & menjalankan tindakan
sesuai aturan otomatisasi, mencatat semuanya ke database (events_log).
Tidak ada eksekusi perintah sistem dari LLM.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

import config
import database as db


# ---------------------------------------------------------------------------
# Utilitas
# ---------------------------------------------------------------------------
def _norm_unit(u: str | None) -> str:
    if not u:
        return ""
    return re.sub(r"\s+", " ", u.strip().lower())


def _same_requester(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    a, b = a.lower(), b.lower()
    return a == b or a in b or b in a


def is_duplicate_ticket(item, qty, requester) -> dict | None:
    """Cegah tiket duplikat: item + jumlah + pelapor sama dalam tiket terbuka."""
    for t in db.open_tickets():
        if (_norm_unit(t["item"]) == _norm_unit(item)
                and t["quantity"] == qty
                and _same_requester(t["requester"], requester)):
            return t
    return None


def is_duplicate_map_update(location_key: str, new_status: str) -> dict | None:
    for m in db.list_map_updates():
        if (m["location_key"] == location_key and m["new_status"] == new_status
                and m["status"] == "Pending Approval"):
            return m
    return None


# ---------------------------------------------------------------------------
# Pemrosesan utama
# ---------------------------------------------------------------------------
def process_audio_result(audio_path: Path | str, trans: dict, analysis: dict, cfg: dict,
                         source: str = "upload", auto_apply: bool = False) -> dict:
    """Simpan transkrip + laporan + jalankan aksi. Return ringkasan untuk UI."""
    out: dict = {"steps": [], "ticket": None, "alert": None, "map_update": None,
                 "duplicate_ticket": None, "duplicate_map_update": None}

    # 1) transkrip
    audio_path = Path(audio_path)
    ahash = db.audio_hash(audio_path)
    dup = db.find_transcript_by_hash(ahash)
    if dup:
        out["steps"].append(
            f"Audio identik sudah pernah diproses (transkrip #{dup['id']}). "
            "Laporan & tiket tidak dibuat ulang."
        )
        out["transcript_id"] = dup["id"]
        return out

    stored = config.AUDIO_DIR / f"{ahash[:16]}{audio_path.suffix or '.wav'}"
    try:
        if Path(audio_path).resolve() != stored.resolve():
            shutil.copy2(audio_path, stored)
    except Exception:
        stored = audio_path

    tid = db.add_transcript(
        audio_hash=ahash, audio_path=str(stored), source=source,
        text=trans["text"], language=trans.get("language"),
        duration=trans.get("duration"), language_probability=trans.get("language_probability"),
        avg_logprob=trans.get("avg_logprob"), transcribe_seconds=trans.get("transcribe_seconds"),
    )
    out["transcript_id"] = tid
    out["steps"].append(f"Transkripsi tersimpan (transcript #{tid}).")

    # 2) laporan (selalu disimpan; gagal klasifikasi = belum terklasifikasi)
    valid = bool(analysis.get("llm_valid", False)) and analysis.get("action") != "NONE"
    report_id = db.add_report(
        transcript_id=tid,
        category=analysis.get("category", "UNKNOWN") if analysis.get("llm_valid") else "UNKNOWN",
        unit=analysis.get("unit"),
        summary=analysis.get("summary") or (trans["text"][:160]),
        item=analysis.get("item"),
        quantity=analysis.get("quantity"),
        unit_measure=analysis.get("unit_measure"),
        location=analysis.get("location"),
        location_key=analysis.get("location_key"),
        priority=analysis.get("priority", "MEDIUM"),
        action=analysis.get("action", "NONE"),
        confidence=analysis.get("confidence"),
        needs_approval=analysis.get("needs_approval"),
        needs_verification=analysis.get("needs_verification"),
        missing_info=analysis.get("missing_information") or [],
        llm_raw=analysis.get("llm_raw"),
        llm_valid=analysis.get("llm_valid", False),
        llm_seconds=analysis.get("llm_seconds"),
        status="REVIEW" if analysis.get("needs_verification") else "NEW",
    )
    out["report_id"] = report_id
    if not analysis.get("llm_valid"):
        out["steps"].append(
            f"Laporan #{report_id} disimpan sebagai BELUM TERKLASIFIKASI. "
            "Tindakan otomatis tidak dijalankan."
        )
        db.log("report", report_id, "unclassified", analysis.get("llm_error"), "system")
        return out

    out["steps"].append(f"Laporan #{report_id} dibuat ({analysis.get('category')}).")
    act = run_action(report_id, analysis, trans["text"], auto_apply=auto_apply)
    out["steps"].extend(act["steps"])
    for k in ("ticket", "alert", "map_update", "duplicate_ticket",
              "duplicate_map_update", "status_candidate"):
        if k in act:
            out[k] = act[k]
    return out


def store_unclassified_text(text: str, analysis: dict, cfg: dict) -> dict:
    """Teks manual yang gagal diklasifikasi: simpan transkrip + laporan, tanpa aksi."""
    tid = db.add_transcript(source="manual", text=text, language=cfg.get("language"),
                            duration=0, language_probability=None, avg_logprob=None,
                            transcribe_seconds=0)
    rid = db.add_report(
        transcript_id=tid, category="UNKNOWN", summary=text[:160], action="NONE",
        needs_verification=True, missing_info=["AI gagal memvalidasi hasil"],
        llm_raw=analysis.get("llm_raw"), llm_valid=False,
        llm_seconds=analysis.get("llm_seconds"), status="REVIEW",
    )
    db.log("report", rid, "unclassified", analysis.get("llm_error"), "system")
    return {"report_id": rid, "transcript_id": tid, "ticket": None, "alert": None,
            "map_update": None,
            "steps": [f"Laporan #{rid} disimpan BELUM TERKLASIFIKASI. Tidak ada tindakan otomatis."]}


def process_text_result(text: str, analysis: dict, cfg: dict) -> dict:
    """Jalur teks manual: transkrip -> laporan -> aksi (sama seperti audio)."""
    tid = db.add_transcript(source="manual", text=text, language=cfg.get("language"),
                            duration=0, language_probability=None, avg_logprob=None,
                            transcribe_seconds=0)
    rid = db.add_report(
        transcript_id=tid, category=analysis.get("category", "UNKNOWN"),
        unit=analysis.get("unit"), summary=analysis.get("summary") or text[:160],
        item=analysis.get("item"), quantity=analysis.get("quantity"),
        unit_measure=analysis.get("unit_measure"), location=analysis.get("location"),
        location_key=analysis.get("location_key"), priority=analysis.get("priority"),
        action=analysis.get("action"), confidence=analysis.get("confidence"),
        needs_approval=analysis.get("needs_approval"),
        needs_verification=analysis.get("needs_verification"),
        missing_info=analysis.get("missing_information"), llm_raw=analysis.get("llm_raw"),
        llm_valid=analysis.get("llm_valid"), llm_seconds=analysis.get("llm_seconds"),
        status="REVIEW" if analysis.get("needs_verification") else "NEW",
    )
    out = {"report_id": rid, "transcript_id": tid, "ticket": None, "alert": None,
           "map_update": None, "duplicate_ticket": None, "duplicate_map_update": None,
           "steps": [f"Transkrip manual tersimpan (transcript #{tid}) & laporan #{rid}."]}
    act = run_action(rid, analysis, text)
    out["steps"].extend(act["steps"])
    for k in ("ticket", "alert", "map_update", "duplicate_ticket",
              "duplicate_map_update", "status_candidate"):
        if k in act:
            out[k] = act[k]
    return out


def run_action(report_id: int, analysis: dict, text: str,
               auto_apply: bool = False) -> dict:
    """Aturan otomatisasi. Return dict berisi langkah + objek yang dibuat."""
    action = analysis.get("action", "NONE")
    out: dict = {"steps": [], "ticket": None, "alert": None, "map_update": None,
                 "duplicate_ticket": None, "duplicate_map_update": None}
    if action == "CREATE_REPORT":
        out["steps"].append("CREATE_REPORT: laporan disimpan otomatis.")

    elif action == "CREATE_TICKET":
        kind = _ticket_kind(analysis)
        dup_t = is_duplicate_ticket(analysis.get("item"), analysis.get("quantity"), analysis.get("unit"))
        if dup_t:
            out["duplicate_ticket"] = dup_t
            out["steps"].append(
                f"CREATE_TICKET: tiket duplikat terdeteksi (#{dup_t['id']}, status {dup_t['status']}) - tidak dibuat baru."
            )
            db.log("ticket", dup_t["id"], "duplicate_blocked", {"report_id": report_id}, "system")
        else:
            t_id = db.add_ticket(
                report_id=report_id, kind=kind, item=analysis.get("item"),
                quantity=analysis.get("quantity"), unit_measure=analysis.get("unit_measure"),
                requester=analysis.get("unit"), priority=analysis.get("priority"),
                status="Pending",
                notes=analysis.get("summary"),
            )
            out["ticket"] = db.get_ticket(t_id)
            out["steps"].append(
                f"CREATE_TICKET: draft tiket #{t_id} dibuat (Pending) - menunggu persetujuan operator."
            )
            db.log("ticket", t_id, "created", {"report_id": report_id}, "system")

    elif action == "UPDATE_STATUS":
        cand = _match_activity(analysis)
        out["status_candidate"] = cand
        if cand["ticket_id"]:
            out["steps"].append(
                f"UPDATE_STATUS: kandidat tiket #{cand['ticket_id']} ({cand['reason']}). "
                "Perlu persetujuan operator - tidak diubah otomatis."
            )
        else:
            out["steps"].append(
                "UPDATE_STATUS: tidak ada tiket/kegiatan yang cocok dengan pasti. "
                "Laporan tetap disimpan, status tidak diubah."
            )
        db.log("report", report_id, "status_update_proposed", cand, "system")

    elif action == "CREATE_ALERT":
        a_id = db.add_alert(
            report_id=report_id, category=analysis.get("category"),
            priority=analysis.get("priority", "HIGH"),
            message=analysis.get("summary") or text[:200],
            location=analysis.get("location"), status="Needs Review",
        )
        out["alert"] = [a for a in db.list_alerts(5) if a["id"] == a_id][0]
        out["steps"].append(
            f"CREATE_ALERT: peringatan #{a_id} ({out['alert']['priority']}) tampil di dashboard. "
            "Sistem tidak mengirim instruksi penanganan."
        )
        db.log("alert", a_id, "created", {"report_id": report_id}, "system")
        if auto_apply and action in config.AUTO_APPLY_ACTIONS:
            out["steps"].append("Diterapkan otomatis (mode dengar terus).")
            db.log("alert", a_id, "auto_applied", {"action": action}, "system")

    elif action == "UPDATE_MAP":
        loc_key = analysis.get("location_key")
        if not loc_key:
            out["steps"].append("UPDATE_MAP: lokasi tidak dikenali - laporan disimpan untuk verifikasi.")
        else:
            new_status = _map_status(analysis)
            dup_m = is_duplicate_map_update(loc_key, new_status)
            if dup_m:
                out["duplicate_map_update"] = dup_m
                out["steps"].append(f"UPDATE_MAP: pembaruan identik sudah pending (#{dup_m['id']}).")
            else:
                m_id = db.add_map_update(
                    report_id=report_id, location_key=loc_key,
                    location_name=analysis.get("location"), new_status=new_status,
                    note=analysis.get("summary"), status="Pending Approval",
                )
                out["map_update"] = [m for m in db.list_map_updates(5) if m["id"] == m_id][0]
                out["steps"].append(
                    f"UPDATE_MAP: pembaruan #{m_id} untuk {analysis.get('location')} "
                    f"-> '{new_status}' menunggu persetujuan operator."
                )
                db.log("map_update", m_id, "created", {"report_id": report_id}, "system")

    elif action == "NONE":
        out["steps"].append("NONE: hanya transkrip & laporan yang disimpan.")

    db.log("report", report_id, "action", {"action": action}, "system")
    return out


def _ticket_kind(analysis: dict) -> str:
    cat = analysis.get("category")
    if cat == "LOGISTICS":
        return "Permintaan logistik"
    if cat == "MEDICAL":
        return "Permintaan bantuan medis"
    if cat == "EQUIPMENT":
        return "Permintaan perbaikan peralatan"
    if cat == "INFRASTRUCTURE":
        return "Permintaan perbaikan infrastruktur"
    return "Permintaan umum"


def _map_status(analysis: dict) -> str:
    text = ((analysis.get("summary") or "") + " " + (analysis.get("item") or "")).lower()
    if any(w in text for w in ("rusak", "putus", "tidak bisa dilalui", "banjir", "longsor")):
        return "Rusak - tidak bisa dilalui"
    if any(w in text for w in ("gangguan", "terhambat", "perbaikan")):
        return "Gangguan"
    if any(w in text for w in ("normal", "selesai", "pulih")):
        return "Normal"
    return "Perlu verifikasi"


def _match_activity(analysis: dict) -> dict:
    """Cocokkan UPDATE_STATUS ke tiket terbuka. Identitas harus jelas."""
    item = _norm_unit(analysis.get("item"))
    unit = (analysis.get("unit") or "").lower()
    summary = (analysis.get("summary") or "").lower()
    best = {"ticket_id": None, "reason": "tidak ada referensi cocok", "confidence": 0.0}
    for t in db.open_tickets():
        t_item = _norm_unit(t["item"])
        score = 0.0
        reasons = []
        if t_item and (t_item in summary or t_item in item or (item and item in t_item)):
            score += 0.5
            reasons.append(f"item '{t['item']}' disebut")
        if unit and t["requester"] and _same_requester(t["requester"], unit):
            score += 0.4
            reasons.append(f"unit '{t['requester']}' cocok")
        if score > best["confidence"]:
            best = {"ticket_id": t["id"], "reason": ", ".join(reasons) or "kecocokan lemah",
                    "confidence": round(score, 2)}
    # hanya tawarkan bila identitas tiket cukup pasti
    if best["confidence"] < 0.7:
        return {"ticket_id": None, "reason": "identitas tiket belum pasti", "confidence": best["confidence"]}
    return best


# ---------------------------------------------------------------------------
# Aksi yang butuh persetujuan operator (dipicu tombol di UI)
# ---------------------------------------------------------------------------
def approve_ticket(ticket_id: int, actor: str = "operator") -> dict:
    t = db.get_ticket(ticket_id)
    if not t:
        return {"ok": False, "message": f"Tiket #{ticket_id} tidak ditemukan."}
    if t["status"] != "Pending":
        return {"ok": False, "message": f"Tiket #{ticket_id} berstatus {t['status']}, bukan Pending."}
    db.update_ticket_status(ticket_id, "Approved", actor)
    return {"ok": True, "message": f"Tiket #{ticket_id} disetujui. Status: Approved."}


def apply_status_update(report_id: int, ticket_id: int, new_status: str = "Completed",
                        actor: str = "operator") -> dict:
    rep = db.get_report(report_id)
    t = db.get_ticket(ticket_id)
    if not rep:
        return {"ok": False, "message": f"Laporan #{report_id} tidak ditemukan."}
    if not t:
        return {"ok": False, "message": f"Tiket #{ticket_id} tidak ditemukan."}
    db.update_ticket_status(ticket_id, new_status, actor)
    db.set_report_status(report_id, "VERIFIED")
    db.log("report", report_id, "status_applied",
           {"ticket_id": ticket_id, "to": new_status}, actor)
    return {"ok": True, "message": f"Tiket #{ticket_id} -> {new_status} (disetujui operator)."}


def approve_map_update(update_id: int, actor: str = "operator") -> dict:
    upd = db.get_map_update(update_id)
    if not upd:
        return {"ok": False, "message": f"Pembaruan peta #{update_id} tidak ditemukan."}
    if upd["status"] != "Pending Approval":
        return {"ok": False, "message": f"Pembaruan #{update_id} sudah {upd['status']}."}
    db.resolve_map_update(update_id, True, actor)
    return {"ok": True, "message": f"Peta {upd['location_name']} -> {upd['new_status']}."}


def reject_map_update(update_id: int, actor: str = "operator") -> dict:
    upd = db.get_map_update(update_id)
    if not upd:
        return {"ok": False, "message": f"Pembaruan peta #{update_id} tidak ditemukan."}
    if upd["status"] != "Pending Approval":
        return {"ok": False, "message": f"Pembaruan #{update_id} sudah {upd['status']}."}
    db.resolve_map_update(update_id, False, actor)
    return {"ok": True, "message": f"Pembaruan #{update_id} ditolak."}


def acknowledge_alert(alert_id: int, status: str, actor: str = "operator") -> dict:
    if status not in db.ALERT_STATUSES:
        return {"ok": False, "message": "Status alert tidak valid."}
    db.update_alert_status(alert_id, status)
    db.log("alert", alert_id, "status_change", {"to": status}, actor)
    return {"ok": True, "message": f"Alert #{alert_id} -> {status}."}


def set_ticket_status(ticket_id: int, status: str, actor: str = "operator") -> dict:
    if status not in db.TICKET_STATUSES:
        return {"ok": False, "message": "Status tiket tidak valid."}
    if not db.get_ticket(ticket_id):
        return {"ok": False, "message": f"Tiket #{ticket_id} tidak ditemukan."}
    db.update_ticket_status(ticket_id, status, actor)
    return {"ok": True, "message": f"Tiket #{ticket_id} -> {status}."}

