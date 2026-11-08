"""
GARUDA Radio AI - UI Gradio satu halaman.

Tab: Monitor & Analisis | Dashboard | Riwayat & Tiket | Peta | Rekap | Pengaturan,
plus panel persetujuan operator untuk tiket / update peta / peringatan.
"""
from __future__ import annotations

import html
import os
import threading
import time
import traceback
from datetime import date
from pathlib import Path

import gradio as gr

import actions
import ai
import audio
import config
import database as db
import loop
import map_view
import pipeline
import reports
import samples_gen

cfg = config.load_config()
_PROC_LOCK = threading.Lock()

CATEGORY_LABELS = {
    "LOGISTICS": "Logistik", "MEDICAL": "Medis", "EQUIPMENT": "Peralatan",
    "INFRASTRUCTURE": "Infrastruktur", "STATUS_UPDATE": "Pembaruan Status",
    "GENERAL": "Umum", "UNKNOWN": "Tidak Dikenal",
}

CSS = """
body, .gradio-container { font-family: 'Inter', 'Segoe UI', system-ui, sans-serif !important; }
.gradio-container { max-width: 1500px !important; margin: 0 auto !important; }
.hero { background: linear-gradient(120deg,#0b1220 0%,#122238 55%,#1d3a2f 100%);
        border:1px solid #1f2a3f; border-radius:16px; padding:18px 22px; color:#e5e7eb; }
.hero h1 { margin:0; font-size:24px; letter-spacing:.06em; }
.hero .tag { display:inline-block; background:#0f2f22; color:#4ade80; border:1px solid #14532d;
             border-radius:999px; padding:2px 10px; font-size:11px; margin-left:8px; vertical-align:middle; }
.hero p { margin:6px 0 0; color:#9ca3af; font-size:13px; }
.demo-strip { background:#3b1d1d; color:#fca5a5; border:1px solid #7f1d1d; border-radius:10px;
              padding:8px 14px; font-size:12px; margin-top:10px; }
.kpi-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; }
.kpi { background:#0f172a; border:1px solid #1f2a3f; border-radius:14px; padding:14px 16px; }
.kpi .v { font-size:28px; font-weight:700; color:#e5e7eb; }
.kpi .l { font-size:12px; color:#94a3b8; text-transform:uppercase; letter-spacing:.05em; }
.kpi.warn { border-color:#7f1d1d; } .kpi.warn .v { color:#f87171; }
.kpi.ok { border-color:#14532d; } .kpi.ok .v { color:#4ade80; }
.panel { border-radius:12px; padding:12px 16px; margin:8px 0; border:1px solid #263244;
         background:#0f172a; color:#e5e7eb; }
.panel.warn { border-color:#92400e; background:#1c1407; color:#fcd34d; }
.panel.danger { border-color:#991b1b; background:#1f0a0a; color:#fca5a5; }
.panel.ok { border-color:#166534; background:#052e16; color:#86efac; }
.muted { color:#94a3b8; font-size:12px; }
.badge { display:inline-block; padding:2px 8px; border-radius:999px; font-size:11px; font-weight:600; }
.badge.HIGH, .badge.CRITICAL { background:#7f1d1d; color:#fecaca; }
.badge.MEDIUM { background:#78350f; color:#fde68a; }
.badge.LOW { background:#14532d; color:#bbf7d0; }
.badge.REVIEW { background:#78350f; color:#fde68a; }
.badge.NEW { background:#1e3a8a; color:#bfdbfe; }
ul.tight { margin:4px 0; padding-left:18px; }
"""


# ---------------------------------------------------------------------------
# Render helpers
# ---------------------------------------------------------------------------
def _badge(text, cls=""):
    return f"<span class='badge {cls}'>{html.escape(str(text))}</span>"


def dashboard_html() -> str:
    s = db.stats()
    latest = db.latest_status()
    rep = latest["report"]
    tic = latest["ticket"]
    alr = latest["alert"]
    items = [
        (s["reports"], "Laporan", ""),
        (s["reports_today"], "Laporan hari ini", "ok"),
        (s["tickets_open"], "Tiket terbuka", ""),
        (s["tickets_pending"], "Tiket Pending", "warn" if s["tickets_pending"] else "ok"),
        (s["alerts_open"], "Peringatan aktif", "warn" if s["alerts_open"] else "ok"),
        (s["alerts_new"], "Peringatan baru", "warn" if s["alerts_new"] else "ok"),
        (s["map_pending"], "Peta menunggu", ""),
        (s["needs_verification"], "Perlu verifikasi", "warn" if s["needs_verification"] else "ok"),
    ]
    kpis = "".join(
        f"<div class='kpi {cls}'><div class='v'>{v}</div><div class='l'>{l}</div></div>"
        for v, l, cls in items)

    def last_row(label, row, extra):
        if not row:
            return f"<li><b>{label}:</b> <span class='muted'>belum ada</span></li>"
        return (f"<li><b>{label}:</b> #{row['id']} {html.escape(str(row.get('summary') or row.get('message') or row.get('item') or ''))[:90]} "
                f"<span class='muted'>({html.escape(extra)})</span></li>")

    latest_list = (
        "<ul class='tight'>"
        + last_row("Laporan terbaru", rep, f"{rep['category']}/{rep['priority']} &middot; {rep['created_at']}" if rep else "")
        + last_row("Tiket terbaru", tic, f"{tic['status']} &middot; {tic['item']}" if tic else "")
        + last_row("Peringatan terbaru", alr, f"{alr['priority']}/{alr['status']}" if alr else "")
        + "</ul>")

    alerts = [a for a in db.list_alerts(8) if a["status"] != "Resolved"]
    alert_html = ("<div class='panel danger'><b>⚠ Peringatan operator:</b><ul class='tight'>" + "".join(
        f"<li>#{a['id']} {_badge(a['priority'], a['priority'])} {html.escape(a['message'])} "
        f"<span class='muted'>({html.escape(a['location'] or '-')} &middot; {a['status']})</span></li>"
        for a in alerts) + "</ul><span class='muted'>Sistem tidak mengirim instruksi atau mengerahkan personel.</span></div>"
        if alerts else "<div class='panel ok'>Tidak ada peringatan aktif.</div>")

    pending_maps = [u for u in db.list_map_updates(20) if u["status"] == "Pending Approval"]
    map_html = ("<div class='panel warn'><b>Pembaruan peta menunggu persetujuan:</b><ul class='tight'>" + "".join(
        f"<li>#{u['id']} {html.escape(u['location_name'] or u['location_key'])} → {html.escape(u['new_status'])}</li>"
        for u in pending_maps) + "</ul></div>" if pending_maps else "")

    return (f"<div class='kpi-grid'>{kpis}</div>"
            f"<div class='panel'><b>Status laporan terbaru</b>{latest_list}</div>"
            f"{alert_html}{map_html}")


def approval_html() -> str:
    pend = [t for t in db.list_tickets(50) if t["status"] == "Pending"]
    updates = [u for u in db.list_map_updates(50) if u["status"] == "Pending Approval"]
    alerts = [a for a in db.list_alerts(50) if a["status"] == "Needs Review"]
    parts = ["<b>Pusat Persetujuan Operator</b>"]
    if not (pend or updates or alerts):
        parts.append("<div class='panel ok'>Tidak ada tindakan yang menunggu persetujuan.</div>")
        return "".join(parts)
    if pend:
        parts.append("<div class='panel warn'><b>Tiket Pending</b><ul class='tight'>" + "".join(
            f"<li>#{t['id']} {html.escape(t['kind'] or '')} — {html.escape(str(t['item']))} "
            f"{t['quantity'] or ''} {html.escape(t['unit_measure'] or '')} "
            f"({html.escape(t['requester'] or '-')})</li>" for t in pend) + "</ul></div>")
    if updates:
        parts.append("<div class='panel warn'><b>Pembaruan peta</b><ul class='tight'>" + "".join(
            f"<li>#{u['id']} {html.escape(u['location_name'] or '')} → {html.escape(u['new_status'])}</li>"
            for u in updates) + "</ul></div>")
    if alerts:
        parts.append("<div class='panel danger'><b>Peringatan baru</b><ul class='tight'>" + "".join(
            f"<li>#{a['id']} {_badge(a['priority'], a['priority'])} {html.escape(a['message'])}</li>"
            for a in alerts) + "</ul></div>")
    return "".join(parts)


def analysis_html(res: dict) -> str:
    if not res:
        return ("<div class='muted'>Belum ada analisis. Tekan "
                "<b>Mulai dengar terus</b> agar mikrofon diproses otomatis, "
                "atau unggah file audio lama lalu tekan Proses audio.</div>")
    trans = res.get("transcript", "")
    an = res.get("analysis") or {}
    act = res.get("action") or {}
    head = (f"<div class='panel'><b>Transkripsi</b> <span class='muted'>"
            f"({res.get('transcribe_seconds')}s &middot; audio {res.get('duration')}s &middot; "
            f"avg_logprob {res.get('avg_logprob')})</span>"
            f"<div style='font-size:15px;margin-top:6px'>{html.escape(trans) or '<i>(kosong)</i>'}</div></div>")

    if not an or an.get("llm_valid") is False:
        err = html.escape(str(an.get("llm_error") or res.get("ai_error") or "AI gagal/absen"))
        return head + (f"<div class='panel danger'><b>AI tidak menghasilkan klasifikasi valid.</b><br>"
                       f"{err}<br><span class='muted'>Transkrip tetap disimpan tanpa tindakan otomatis.</span></div>")

    missing = an.get("missing_information") or []
    ver = ("<div class='panel warn'>⚠ <b>PERLU VERIFIKASI</b> — informasi belum lengkap / keyakinan rendah.</div>"
           if an.get("needs_verification") else "<div class='panel ok'>Data terlihat lengkap.</div>")
    rows = [
        ("Kategori", CATEGORY_LABELS.get(an.get("category"), an.get("category"))),
        ("Ringkasan", an.get("summary")),
        ("Lokasi", an.get("location") or "— (tidak disebut)"),
        ("Unit pelapor", an.get("unit") or "—"),
        ("Barang/kegiatan", an.get("item") or "—"),
        ("Jumlah", f"{an.get('quantity')} {an.get('unit_measure') or ''}" if an.get("quantity") is not None else "—"),
        ("Prioritas", _badge(an.get("priority"), an.get("priority"))),
        ("Aksi diusulkan", _badge(an.get("action"))),
        ("Butuh persetujuan", "Ya" if an.get("needs_approval") else "Tidak"),
        ("Keyakinan", f"{an.get('confidence')}"),
        ("Sumber analisis", {"llm": "LLM lokal (Qwen)", "rule_fallback": "Aturan cadangan (LLM gagal)"}
         .get(an.get("analysis_source"), "—")),
        ("Model / waktu", f"{cfg.get('llm_model')} &middot; {an.get('llm_seconds')}s"),
    ]
    table = "".join(f"<tr><td style='color:#94a3b8;width:170px'>{k}</td><td>{v}</td></tr>"
                    for k, v in rows)
    missing_html = (f"<div class='panel warn'><b>Informasi hilang:</b> {html.escape(', '.join(missing))}</div>"
                    if missing else "")
    steps = "".join(f"<li>{html.escape(s)}</li>" for s in act.get("steps", []))
    steps_html = f"<div class='panel'><b>Tindakan yang dijalankan Python</b><ul class='tight'>{steps}</ul></div>"
    total = f"<span class='muted'>Total waktu: {res.get('total_seconds')}s</span>" if res.get("total_seconds") else ""
    return head + ver + f"<table style='width:100%;border-collapse:collapse'>{table}</table>" + missing_html + steps_html + total

def raw_json(res: dict) -> str:
    if not res:
        return ""
    import json

    return json.dumps(res.get("analysis") or {}, indent=2, ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# Proses utama audio
# ---------------------------------------------------------------------------
def _refresh_all():
    """Semua panel yang menampilkan data dari database."""
    return (dashboard_html(), approval_html(), map_view.build_map(),
            _tickets_table(), _reports_table(), _map_pending_table(), _alerts_table())


LIVE_COLS = ["Waktu", "Status", "Ringkasan", "Kategori", "Aksi", "ID Laporan"]

_LISTENING = threading.Event()


def vad_report(gabung):
    """Terjemahkan status VAD dari browser menjadi pesan yang jujur ke operator.

    `gabung` berformat "status|info" dari skrip VAD. Dipanggil berkala setelah
    Mulai. Tanpa ini, kegagalan memuat WASM atau penolakan izin mikrofon
    membuat operator melihat "AKTIF" padahal tidak ada suara yang terdeteksi.
    """
    teks = str(gabung or "belum|")
    status, _, info = teks.partition("|")
    status = status.strip()
    info = info.strip()
    if not _LISTENING.is_set():
        return "Status: **nonaktif**."
    if status == "aktif":
        dasar = (f"Status: **MENDENGARKAN** — mikrofon streaming. Sunyi "
                 f"{config.SILENCE_SECONDS:.0f} detik = potong & proses otomatis.")
        return f"{dasar}\n\n<span class='muted'>Terakhir: {html.escape(info[:160])}</span>"
    if status.startswith("gagal"):
        return (f"❌ Deteksi suara otomatis **GAGAL**: "
                f"`{status[5:].strip()[:200]}`. Periksa izin mikrofon browser, "
                "lalu tekan Hentikan dan Mulai lagi.")
    if status == "menyiapkan":
        return "Status: **menyiapkan** — memuat model deteksi suara..."
    if status in ("nonaktif", "belum"):
        return ("⚠️ Mode menyala tetapi deteksi suara **belum menyala**. "
                "Tekan Hentikan lalu Mulai lagi, dan izinkan akses mikrofon.")
    return f"Status: **{status}**"


def do_start_listening():
    """Nyalakan mode dengar terus (antrian + thread pekerja).

    Aset VAD hanya dibutuhkan untuk deteksi suara otomatis. Tanpa aset itu
    mikrofon tidak bisa dipotong otomatis sama sekali - jadi mode tetap
    dinyalakan dengan peringatan jelas, dan operator masih bisa memproses
    file lewat panel upload manual.
    """
    loop.start_worker(cfg)
    _LISTENING.set()
    if not config.vad_assets_ready():
        return ("Mode dengar terus: **AKTIF (tanpa deteksi otomatis)** — aset VAD "
                "belum ada, jadi mikrofon TIDAK akan dipotong otomatis. Jalankan: "
                "`.venv\\Scripts\\python.exe setup_models.py vad` lalu muat ulang "
                "halaman. Sementara itu pakai panel 'Proses file manual'.")
    return ("Status: **MENDENGARKAN** — mikrofon streaming. Setiap transmisi "
            f"dipotong setelah sunyi {config.SILENCE_SECONDS:.0f} detik dan "
            "langsung diproses. Tidak perlu menekan tombol apa pun.")


def do_start_listening_and_watch():
    """Mulai mode + nyalakan pemantau status VAD.

    Nilai balik harus NILAI BIASA (string / Update), bukan objek komponen
    Gradio: mengembalikan `gr.Textbox(...)` membuat Gradio memperlakukannya
    sebagai argumen kata kunci dan klik tombol langsung gagal dengan
    "Parameter `0` is not a valid keyword argument" - tombol Mulai jadi
    tidak bereaksi sama sekali.
    """
    return do_start_listening(), gr.Timer(2.0, active=True), "menyiapkan|"


def do_stop_listening_and_watch():
    """Hentikan mode + matikan pemantau status VAD."""
    return do_stop_listening(), gr.Timer(2.0, active=False), "nonaktif|"


def do_stop_listening():
    _LISTENING.clear()
    hasil = loop.stop_worker()
    if not hasil["stopped"]:
        return (f"Status: **{hasil['message']}** "
                "Potongan yang sudah masuk antrian tetap diproses.")
    return "Status: **nonaktif**. Potongan yang sudah masuk antrian tetap diproses."


def _folder_unggahan_gradio() -> list[Path]:
    """Folder tempat Gradio menaruh berkas hasil unggahan.

    Dipakai untuk MEMBATASI berkas mana yang boleh diproses lewat endpoint
    ingest. Tanpa batas ini, siapa pun yang bisa menjangkau port aplikasi
    (selagi mode menyala) dapat menyuruh GARUDA memutar ulang audio apa pun
    di disk - termasuk rekaman lama operator lain - dan memicu alert
    otomatis dari situ.
    """
    import tempfile
    akar = [config.DATA_DIR / "unggahan"]
    tmp = Path(tempfile.gettempdir()) / "gradio"
    if tmp.exists():
        akar.append(tmp)
    return akar


def _berkas_unggahan_sah(berkas: Path) -> bool:
    """True bila berkas berada di folder unggahan Gradio (atau data kita)."""
    try:
        nyata = berkas.resolve()
    except OSError:
        return False
    for akar in _folder_unggahan_gradio():
        try:
            akar_nyata = akar.resolve()
        except OSError:
            continue
        if nyata == akar_nyata or akar_nyata in nyata.parents:
            return True
    return False


def do_ingest_clip(audio_path, durasi=None):
    """Terima satu potongan transmisi dari browser dan masukkan ke antrian.

    Dipanggil OTOMATIS oleh skrip VAD setiap kali terdeteksi sunyi cukup lama.
    Tidak ada tombol yang perlu ditekan operator.

    Endpoint ini terbuka lewat HTTP. Karena klip yang masuk diproses dengan
    auto_apply=True (alert langsung berlaku), ada tiga penjagaan:
      1. mode harus menyala (operator sudah menekan Mulai),
      2. berkas harus benar-benar hasil unggahan Gradio, bukan path sembarang,
      3. hanya ekstensi audio yang diterima.
    """
    if not _LISTENING.is_set():
        return "Mode dengar terus nonaktif — potongan diabaikan."
    if not audio_path:
        return "Potongan kosong — diabaikan."
    # Jangan percaya path mentah dari browser.
    berkas = Path(str(audio_path))
    if not berkas.is_file() or berkas.suffix.lower() not in (
            ".wav", ".mp3", ".ogg", ".m4a", ".flac", ".webm"):
        return "❌ Klip ditolak: bukan berkas audio yang sah."
    if not _berkas_unggahan_sah(berkas):
        return ("❌ Klip ditolak: berkas bukan hasil unggahan sesi ini. "
                "Hanya transmisi dari mikrofon yang boleh diproses otomatis.")
    try:
        paksa = float(durasi) if durasi is not None else None
    except (TypeError, ValueError):
        paksa = None
    # Durasi kiriman browser tidak boleh dipakai untuk memalsukan panjang klip.
    # Batas atas longgar (10 menit) sudah cukup menolak nilai karangan, dan
    # durasi yang jauh melebihi isi berkas dianggap tidak masuk akal.
    if paksa is not None:
        if not (0 < paksa <= 600):
            paksa = None
        else:
            nyata = loop._durasi(berkas)
            if nyata > 0 and paksa > nyata * 1.5 + 1.0:
                paksa = None
    try:
        res = loop.enqueue_clip(berkas, source="live", _force_duration=paksa)
    except Exception as exc:
        traceback.print_exc()
        return f"❌ Gagal memasukkan klip: {exc}"
    return res["message"]



def live_queue_table():
    """Tabel antrian mode dengar-terus (dibaca dari DB, bukan dari memori)."""
    try:
        return [[r["waktu"], r["status"], r["ringkas"], r["kategori"], r["aksi"],
                 r["report_id"]] for r in db.loop_feed(30)]
    except Exception:
        traceback.print_exc()
        return []


def process_audio_fn(audio_file):
    if not audio_file:
        return (gr.update(), analysis_html({}), "", "⚠ Tidak ada audio. Unggah file audio dulu.",
                *_refresh_all())
    try:
        with _PROC_LOCK:
            res = pipeline.process_audio(audio_file, cfg, source="upload")
        status = "✅ Selesai diproses." if res.get("action") else "ℹ Tidak ada yang diproses."
        if res.get("ai_error"):
            status += f" — ⚠ {res['ai_error'].splitlines()[0]}"
        return (res.get("transcript", ""), analysis_html(res), raw_json(res), status, *_refresh_all())
    except (audio.ModelNotInstalled, ai.LLMNotAvailable) as exc:
        return ("", analysis_html({"ai_error": str(exc), "analysis": {"llm_valid": False, "llm_error": str(exc)}}),
                "", f"❌ {exc}", *_refresh_all())
    except Exception as exc:
        traceback.print_exc()
        return ("", f"<div class='panel danger'>❌ {html.escape(str(exc))}</div>", "",
                f"❌ Gagal: {exc}", *_refresh_all())


def process_samples_fn():
    """Proses semua sample audio otomatis (uji alur penuh, memakai inferensi sungguhan)."""
    with _PROC_LOCK:
        lines = []
        try:
            samples_gen.ensure_samples()
        except Exception as exc:
            lines.append(f"<li class='muted'>Sample TTS gagal dibuat: {html.escape(str(exc))}</li>")
        for sc in samples_gen.SCENARIOS:
            path = config.SAMPLES_DIR / f"{sc['id']}.wav"
            if not path.exists():
                lines.append(f"<li>{sc['title']}: <span class='muted'>audio belum ada</span></li>")
                continue
            try:
                res = pipeline.process_audio(path, cfg, source="sample")
                an = res.get("analysis") or {}
                steps = (res.get("action") or {}).get("steps", [])
                dup = " (duplikat: audio sudah pernah diproses)" if (res.get("action") or {}).get("transcript_id") and not steps else ""
                lines.append(
                    f"<li><b>{sc['title']}</b> — {an.get('category')} / {an.get('action')} "
                    f"<span class='muted'>({res.get('total_seconds')}s)</span>{dup}<br>"
                    f"<span class='muted'>{html.escape(' • '.join(steps))}</span></li>")
            except Exception as exc:
                lines.append(f"<li><b>{sc['title']}</b> — ❌ {html.escape(str(exc))}</li>")
    return (f"<div class='panel'><b>Hasil uji 6 skenario (inferensi sungguhan)</b>"            f"<ul class='tight'>{''.join(lines)}</ul></div>", *_refresh_all())


def save_transcript_text(text):
    if not text or not text.strip():
        return gr.update(value="⚠ Transkrip kosong."), *_refresh_all()
    with _PROC_LOCK:
        try:
            res = pipeline.process_text(text, cfg)
            net = res["action"]
            if net.get("ticket"):
                msg = f"✅ Tiket #{net['ticket']['id']} (Pending) dibuat dari teks."
            elif net.get("alert"):
                msg = f"✅ Alert #{net['alert']['id']} dibuat dari teks."
            elif net.get("map_update"):
                msg = f"✅ Update peta #{net['map_update']['id']} menunggu persetujuan."
            else:
                msg = "✅ Teks dianalisis & disimpan."
            return gr.update(value=msg), *_refresh_all()
        except (ai.LLMNotAvailable, audio.ModelNotInstalled) as exc:
            return gr.update(value=f"❌ {exc}"), *_refresh_all()
        except Exception as exc:
            traceback.print_exc()
            return gr.update(value=f"❌ {exc}"), *_refresh_all()


# ---------------------------------------------------------------------------
# Tabel
# ---------------------------------------------------------------------------
TICKET_COLS = ["ID", "Jenis", "Item", "Jumlah", "Satuan", "Pelapor", "Prioritas", "Status", "Dibuat"]
ALERT_COLS = ["ID", "Kategori", "Prioritas", "Pesan", "Lokasi", "Status", "Dibuat"]
REPORT_COLS = ["ID", "Waktu", "Kategori", "Unit", "Ringkasan", "Item", "Lokasi", "Prioritas",
               "Aksi", "Keyakinan", "Verifikasi", "Status"]
MAP_COLS = ["ID", "Lokasi", "Status Baru", "Catatan", "Persetujuan", "Dibuat"]
_ID_IX = TICKET_COLS.index("ID")

PETUNJUK_PILIH = "👉 **Klik satu baris pada tabel** untuk melihat aksi yang tersedia."
PETUNJUK_ALERT = "👉 **Klik satu baris peringatan** untuk menandainya."


def _ticket_rows(statuses=None, priorities=None, kind="Semua",
                 date_from=None, date_to=None):
    """Baris tabel tiket (hanya-baca)."""
    return [[t["id"], t["kind"], t["item"], t["quantity"], t["unit_measure"],
             t["requester"], t["priority"], t["status"], t["created_at"][:16]]
            for t in db.list_tickets(
                200, statuses=statuses, priorities=priorities, kind=kind,
                date_from=_d(date_from), date_to=_d(date_to))]


def _tickets_table():
    return _ticket_rows()



def _alerts_table():
    return [[a["id"], a["category"], a["priority"], a["message"], a["location"], a["status"],
             a["created_at"][:16]] for a in db.list_alerts(200)]


def _reports_table(date_from=None, date_to=None, category="Semua"):
    rows = db.list_reports(limit=300, category=category,
                           date_from=_d(date_from), date_to=_d(date_to))
    return [[r["id"], r["created_at"][:16], r["category"], r["unit"], r["summary"], r["item"],
             r["location"], r["priority"], r["action"], r["confidence"],
             "Ya" if r["needs_verification"] else "Tidak", r["status"]] for r in rows]


def _map_pending_table():
    return [[u["id"], u["location_name"], u["new_status"], u["note"], u["status"], u["created_at"][:16]]
            for u in db.list_map_updates(100)]


def _d(val):
    """Tanggal dari Gradio DateTime bisa datetime/str/None."""
    if not val:
        return None
    if isinstance(val, (int, float)):
        return date.fromtimestamp(val).isoformat()
    if isinstance(val, str):
        return val[:10]
    return val.strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Aksi operator
#
# Semua aksi bekerja pada baris yang DIKLIK, bukan ID yang diketik.
# `gr.Dataframe.select` memberi `evt.row_value` = seluruh isi baris, jadi ID
# dan statusnya terbaca tanpa menebak dari teks layar.
# ---------------------------------------------------------------------------
def _filter_status():
    return gr.CheckboxGroup(choices=db.TICKET_STATUSES, value=list(db.TICKET_STATUSES),
                            label="Status", scale=3)


def _filter_prioritas():
    return gr.CheckboxGroup(choices=["HIGH", "MEDIUM", "LOW"],
                            value=["HIGH", "MEDIUM", "LOW"], label="Prioritas", scale=2)


def _jenis_tiket():
    """Pilihan jenis tiket: yang ada di DB, atau daftar baku bila DB kosong."""
    jenis = db.ticket_kinds()
    return ["Semua"] + (jenis or ["LOGISTICS", "MEDICAL", "EQUIPMENT", "INFRASTRUCTURE"])


def _baris_terpilih(evt, kolom_id=0):
    baris = getattr(evt, "row_value", None)
    if not baris:
        return None
    try:
        return int(baris[kolom_id])
    except (TypeError, ValueError, IndexError):
        return None


def _tombol_untuk(status):
    """(approve, decline, hapus) - tampil hanya yang BERLAKU untuk status itu."""
    return (gr.update(visible=status == "Pending"),
            gr.update(visible=status in ("Pending", "Approved")),
            gr.update(visible=status == "Rejected"))


def saat_baris_tiket_diklik(evt: gr.SelectData):
    """Klik baris: simpan pilihan, tampilkan ringkasan + tombol yang berlaku."""
    tid = _baris_terpilih(evt, _ID_IX)
    t = db.get_ticket(tid) if tid else None
    if not t:
        return PETUNJUK_PILIH, None, *_tombol_untuk(None)
    jumlah = f"{t['quantity'] or ''} {t['unit_measure'] or ''}".strip()
    ringkas = (f"**Dipilih: Tiket #{t['id']}** — {t['kind'] or '-'}: {t['item'] or '-'} "
               f"{jumlah} · pelapor {t['requester'] or '-'} · prioritas "
               f"{t['priority'] or '-'} · status **{t['status']}**")
    return ringkas, t["id"], *_tombol_untuk(t["status"])


def do_filter_tiket(statuses, priorities, kind, date_from, date_to):
    rows = _ticket_rows(statuses, priorities, kind, date_from, date_to)
    return (rows, PETUNJUK_PILIH, None, *_tombol_untuk(None),
            f"Menampilkan **{len(rows)}** tiket.")


def do_reset_filter_tiket():
    rows = _ticket_rows()
    return (list(db.TICKET_STATUSES), ["HIGH", "MEDIUM", "LOW"], "Semua",
            None, None, rows, PETUNJUK_PILIH, None, *_tombol_untuk(None),
            f"Filter direset. Menampilkan **{len(rows)}** tiket.")


def _pesan(hasil: dict, label: str) -> str:
    return f"{'✅' if hasil['ok'] else '⚠'} {label}: {hasil['message']}"


def _setelah_aksi(hasil: dict, label: str):
    """Pesan + tabel segar + pilihan dikosongkan (tombol disembunyikan lagi).

    Pilihan sengaja dikosongkan: setelah bertindak, status baris itu sudah
    berubah, jadi membiarkannya "terpilih" akan menampilkan tombol yang salah.
    """
    return (_pesan(hasil, label), _ticket_rows(), PETUNJUK_PILIH, None,
            *_tombol_untuk(None))


def do_approve_tiket(tid):
    if not tid:
        return _setelah_aksi({"ok": False, "message": "Klik satu baris dulu."}, "Setujui")
    return _setelah_aksi(actions.approve_ticket(int(tid)), "Setujui")


def do_decline_tiket(tid):
    if not tid:
        return _setelah_aksi({"ok": False, "message": "Klik satu baris dulu."}, "Tolak")
    return _setelah_aksi(actions.reject_ticket(int(tid)), "Tolak")


# Hapus permanen butuh konfirmasi. ID yang sudah "dipersenjatai" pada klik
# pertama; klik kedua untuk ID yang SAMA baru benar-benar menghapus.
_HAPUS_ARM = {"ids": []}


def do_hapus_tiket(tid):
    """Klik pertama = konfirmasi, klik kedua (ID sama) = hapus permanen."""
    if not tid:
        _HAPUS_ARM["ids"] = []
        return _setelah_aksi({"ok": False, "message": "Klik satu baris dulu."}, "Hapus")
    tid = int(tid)
    if _HAPUS_ARM["ids"] != [tid]:
        _HAPUS_ARM["ids"] = [tid]
        return (f"⚠️ **Konfirmasi hapus permanen tiket #{tid}.** "
                "Data yang terhapus tidak bisa dikembalikan. "
                "Tekan **🗑 Hapus permanen** sekali lagi untuk melanjutkan.",
                gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip())
    _HAPUS_ARM["ids"] = []
    ok = db.delete_ticket(tid)
    hasil = ({"ok": True, "message": f"Tiket #{tid} dihapus permanen."} if ok
             else {"ok": False, "message": f"Tiket #{tid} tidak bisa dihapus "
                                           "(hanya tiket berstatus Rejected)."})
    return _setelah_aksi(hasil, "Hapus")


def saat_baris_alert_diklik(evt: gr.SelectData):
    aid = _baris_terpilih(evt)
    a = db.get_alert(aid) if aid else None
    if not a:
        return (PETUNJUK_ALERT, None,
                gr.update(visible=False), gr.update(visible=False))
    ringkas = (f"**Dipilih: Peringatan #{a['id']}** — {a['priority']} · "
               f"{a['message']} · status **{a['status']}**")
    return (ringkas, a["id"],
            gr.update(visible=a["status"] != "Acknowledged"),
            gr.update(visible=a["status"] != "Resolved"))


def do_tandai_alert(aid, status):
    if not aid:
        return ("⚠ Klik satu baris dulu.", _alerts_table(), PETUNJUK_ALERT, None,
                gr.update(visible=False), gr.update(visible=False))
    r = actions.acknowledge_alert(int(aid), status)
    return (_pesan(r, "Peringatan"), _alerts_table(), PETUNJUK_ALERT, None,
            gr.update(visible=False), gr.update(visible=False))


def saat_baris_peta_diklik(evt: gr.SelectData):
    uid = _baris_terpilih(evt)
    u = db.get_map_update(uid) if uid else None
    if not u:
        return ("👉 **Klik satu baris pembaruan peta** untuk memilihnya.", None,
                gr.update(visible=False), gr.update(visible=False))
    ringkas = (f"**Dipilih: Pembaruan #{u['id']}** — {u['location_name'] or u['location_key']} "
               f"→ {u['new_status']} · status **{u['status']}**")
    berlaku = u["status"] == "Pending Approval"
    return (ringkas, u["id"], gr.update(visible=berlaku), gr.update(visible=berlaku))


def do_map_decision(uid, approve):
    """Setujui/tolak pembaruan peta untuk ID yang dipilih lewat klik baris."""
    kosong = ("👉 **Klik satu baris pembaruan peta** untuk memilihnya.", None,
              gr.update(visible=False), gr.update(visible=False))
    if not uid:
        return ("⚠ Klik satu baris dulu.", _map_pending_table(), *kosong)
    r = (actions.approve_map_update if approve else actions.reject_map_update)(int(uid))
    return (_pesan(r, "Peta"), _map_pending_table(), *kosong)



def do_recap(date_from, date_to, category):
    try:
        rec = reports.build_recap(_d(date_from), _d(date_to),
                                  None if not category or category == "Semua" else category, cfg)
        msg = ("✅ Rekap dibuat." if cfg.get("llm_model") else "✅ Rekap dibuat (tanpa naratif LLM).")
        return reports.recap_markdown(rec), msg, gr.update(value=rec), None, None
    except Exception as exc:
        traceback.print_exc()
        return f"❌ Gagal membuat rekap: {exc}", f"❌ {exc}", gr.update(), None, None


def do_export(rec, kind):
    if not rec:
        return "⚠ Buat rekap dulu."
    try:
        path = reports.export_csv(rec) if kind == "csv" else reports.export_pdf(rec)
        return path
    except Exception as exc:
        traceback.print_exc()
        gr.Warning(f"Gagal ekspor: {exc}")
        return None


def save_settings(whisper_model, device, llm_model, gpu_layers, ctx):
    cfg.update({
        "whisper_model": whisper_model,
        "whisper_device": "auto" if device == "auto" else device,
        "llm_model": llm_model,
        "llm_gpu_layers": int(gpu_layers),
        "llm_ctx": int(ctx),
    })
    config.save_config(cfg)
    audio._WHISPER.clear()
    ai._LLM.clear()
    return settings_status()


def settings_status() -> str:
    info = config.device_info()
    rows = [
        ("GPU terdeteksi", info["gpu"] or "-"),
        ("CUDA Whisper (ctranslate2)", "Ya" if info["cuda"] else "Tidak"),
        ("llama-cpp-python", "Terpasang" if info["llama_cpp"] else "BELUM (jalankan setup.bat)"),
        ("llama.cpp GPU offload", "Ya" if info["llama_cpp_gpu"] else "Tidak (mode CPU)"),
        ("Whisper aktif", f"{cfg['whisper_model']} ({'ada' if config.whisper_installed(cfg['whisper_model']) else 'BELUM'})"),
        ("LLM aktif", f"{cfg['llm_model']} ({'ada' if config.llm_installed(cfg['llm_model']) else 'BELUM'})"),
        ("Database", str(config.DB_PATH)),
        ("Offline", "Tidak ada API cloud yang dipakai"),
    ]
    return "".join(f"<tr><td style='color:#94a3b8;width:230px'>{k}</td><td>{html.escape(str(v))}</td></tr>"
                   for k, v in rows)


def generate_samples_fn():
    try:
        files = samples_gen.ensure_samples()
        missing = [f for f in files if not Path(f).exists()]
        if missing:
            return f"⚠ {len(missing)} sample gagal dibuat (TTS): " + ", ".join(Path(m).name for m in missing)
        return f"✅ {len(files)} sample audio siap di folder samples/. {samples_gen.describe_tts()}"
    except Exception as exc:
        return f"❌ Gagal membuat sample: {exc}"


def preview_map():
    return map_view.build_map()


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------
def build_ui() -> gr.Blocks:
    db.init_db()
    with gr.Blocks(title=config.APP_TITLE) as demo:
        gr.HTML(f"""<div class='hero'>
            <h1>{config.APP_TITLE}<span class='tag'>LOCAL AI · OFFLINE</span></h1>
            <p>{config.APP_SUBTITLE}</p>
            <div class='demo-strip'>⚠ {config.DEMO_BANNER} Skrip: Pos Alfa/Bravo/Charlie/Delta,
            Jembatan Desa Contoh. AI tidak punya akses eksekusi perintah sistem.</div>
        </div>""")


        with gr.Tabs():
            # ---------------- Tab 1: Monitor (KPI + dengar terus + antrian) ----
            with gr.Tab("🎙 Monitor"):
                dashboard_box = gr.HTML(dashboard_html())
                approval_box = gr.HTML(approval_html())
                gr.Markdown("---")
                gr.Markdown("### 🎙 Dengar terus")
                with gr.Row():
                    btn_listen_on = gr.Button("🟢 Mulai dengar terus", variant="primary",
                                              scale=2)
                    btn_listen_off = gr.Button("⏹ Hentikan", scale=1)
                live_status = gr.Markdown("Status: **nonaktif**.", elem_id="live_status")
                # Satu kotak tersembunyi berisi "status|info" dari skrip VAD.
                vad_state = gr.Textbox(value="belum|", visible=False,
                                       elem_id="vad_state", label="status VAD")
                vad_timer = gr.Timer(2.0, active=False)
                # Pintu masuk API untuk potongan audio dari skrip VAD.
                # Tidak terlihat; hanya dipakai lewat endpoint "ingest_clip".
                vad_path = gr.Textbox(visible=False, label="path potongan")
                vad_durasi_info = gr.Number(value=None, visible=False,
                                            label="durasi potongan (detik)")
                btn_ingest_hidden = gr.Button(visible=False)

                gr.Markdown("### 📡 Antrian transmisi")
                live_df = gr.Dataframe(headers=LIVE_COLS, value=live_queue_table(),
                                       interactive=False, wrap=True,
                                       elem_id="live_queue")

                with gr.Accordion("📁 Proses file manual (upload audio lama)", open=False):
                    with gr.Row():
                        with gr.Column(scale=5):
                            audio_in = gr.Audio(sources=["upload"], type="filepath",
                                                label="Unggah audio (wav/mp3/m4a/ogg)",
                                                elem_id="audio_in")
                            with gr.Row():
                                btn_process = gr.Button("▶ Proses audio", variant="secondary",
                                                        scale=2)
                                btn_clear = gr.ClearButton([audio_in], value="Bersihkan",
                                                           scale=1)
                            gr.Markdown(f"<span class='muted'>Perangkat audio input "
                                        f"terdeteksi: "
                                        f"{html.escape('; '.join(audio.list_input_devices())[:300])}</span>")
                        with gr.Column(scale=7):
                            status_box = gr.Markdown("Siap.")
                            transcript_box = gr.Textbox(
                                label="Transkripsi (Whisper, bahasa Indonesia)",
                                lines=3, interactive=False)
                            analysis_box = gr.HTML(analysis_html({}))
                            with gr.Accordion("JSON mentah AI (audit)", open=False):
                                json_box = gr.Code(label="JSON", language="json", value="")

                with gr.Row():
                    with gr.Accordion("Transkrip manual (uji tanpa audio)", open=False):
                        txt_in = gr.Textbox(label="Teks laporan",
                                            placeholder="Contoh: Pos Bravo membutuhkan tambahan 30 liter solar.")
                        btn_text = gr.Button("Proses teks", size="sm")
                    with gr.Accordion("Demo & uji 6 skenario", open=False):
                        with gr.Row():
                            btn_samples = gr.Button("🎧 Buat contoh audio", size="sm")
                            btn_test_all = gr.Button("▶ Uji 6 skenario", variant="secondary",
                                                     size="sm")
                        btn_refresh_main = gr.Button("🔄 Muat ulang data", size="sm")
                        sample_msg = gr.Markdown("")
                        demo_result = gr.HTML("")

                outputs_common = []  # didefinisikan setelah komponen dibuat di bawah

            # ---------------- Tab 2: Tiket (approval + riwayat + peringatan) ----
            with gr.Tab("✅ Tiket"):
                gr.Markdown("### Tindak lanjut tiket")
                gr.Markdown("<span class='muted'>Klik satu baris untuk melihat aksi "
                            "yang tersedia untuk tiket itu.</span>")

                with gr.Accordion("🔎 Filter", open=False):
                    with gr.Row():
                        f_status = _filter_status()
                        f_prioritas = _filter_prioritas()
                    with gr.Row():
                        f_jenis = gr.Dropdown(_jenis_tiket(), value="Semua",
                                              label="Jenis", scale=1)
                        f_dari = gr.DateTime(label="Dari tanggal", include_time=False, scale=1)
                        f_sampai = gr.DateTime(label="Sampai tanggal", include_time=False,
                                               scale=1)
                    with gr.Row():
                        btn_terapkan_filter = gr.Button("🔎 Terapkan filter",
                                                        variant="secondary", size="sm")
                        btn_reset_filter = gr.Button("↺ Reset filter", size="sm")
                        btn_approval_refresh = gr.Button("🔄 Muat ulang", size="sm")
                filter_info = gr.Markdown(f"Menampilkan **{len(_ticket_rows())}** tiket.")

                tickets_df = gr.Dataframe(headers=TICKET_COLS, value=_ticket_rows(),
                                          interactive=False, wrap=True,
                                          label="Klik satu baris untuk memilih")

                tiket_dipilih = gr.State(None)
                approval_msg = gr.Markdown("")
                ringkas_pilih = gr.Markdown(PETUNJUK_PILIH)

                # Tombol aman & berbahaya dipisah, dan hanya muncul bila berlaku.
                btn_approve = gr.Button("✅ Approve tiket", variant="primary", visible=False)
                with gr.Row():
                    btn_decline = gr.Button("🚫 Decline (Rejected)", variant="stop",
                                            visible=False)
                    btn_hapus = gr.Button("🗑 Hapus permanen", variant="stop", visible=False)

                gr.Markdown("---")
                gr.Markdown("### 📋 Riwayat laporan")
                with gr.Row():
                    hist_cat = gr.Dropdown(["Semua"] + ai.CATEGORIES, value="Semua",
                                           label="Kategori", scale=1)
                    hist_from = gr.DateTime(label="Dari tanggal", include_time=False, scale=1)
                    hist_to = gr.DateTime(label="Sampai tanggal", include_time=False, scale=1)
                    btn_hist = gr.Button("🔎 Filter", size="sm", scale=1)
                reports_df_on_history = gr.Dataframe(headers=REPORT_COLS,
                                                     value=_reports_table(),
                                                     interactive=False, wrap=True)

                gr.Markdown("### 🔔 Peringatan")
                alerts_df = gr.Dataframe(headers=ALERT_COLS, value=_alerts_table(),
                                         interactive=False, wrap=True,
                                         label="Klik satu baris untuk menandainya")
                alert_dipilih = gr.State(None)
                alert_msg = gr.Markdown("")
                ringkas_alert = gr.Markdown(PETUNJUK_ALERT)
                with gr.Row():
                    btn_alert_ack = gr.Button("👁 Tandai dibaca", visible=False)
                    btn_alert_resolve = gr.Button("✔ Tandai selesai", variant="primary",
                                                  visible=False)

            # ---------------- Tab 3: Peta ----------------
            with gr.Tab("🗺 Peta"):
                gr.Markdown("### Peta status lokasi")
                map_box = gr.HTML(map_view.build_map(), elem_id="map_html")
                gr.Markdown("### 📍 Pembaruan peta menunggu persetujuan")
                gr.Markdown("<span class='muted'>Klik satu baris untuk menyetujui "
                            "atau menolak pembaruan itu.</span>")
                with gr.Row():
                    btn_map_refresh = gr.Button("🔄 Muat ulang peta", size="sm")
                map_df = gr.Dataframe(headers=MAP_COLS, value=_map_pending_table(),
                                      interactive=False, wrap=True,
                                      label="Klik satu baris untuk memilih")
                map_upd_id = gr.State(None)
                map_msg = gr.Markdown("")
                ringkas_map = gr.Markdown("👉 **Klik satu baris pembaruan peta** untuk memilihnya.")
                with gr.Row():
                    btn_map_approve = gr.Button("✅ Setujui", variant="primary", visible=False)
                    btn_map_reject = gr.Button("✖ Tolak", variant="stop", visible=False)

            # ---------------- Tab 5: Rekap ----------------
            with gr.Tab("📑 Rekap"):
                with gr.Row():
                    recap_from = gr.DateTime(label="Dari tanggal", include_time=False, scale=1)
                    recap_to = gr.DateTime(label="Sampai tanggal", include_time=False, scale=1)
                    recap_cat = gr.Dropdown(["Semua"] + ai.CATEGORIES, value="Semua",
                                            label="Kategori", scale=1)
                    btn_recap = gr.Button("Buat Rekap", variant="primary", scale=1)
                recap_msg = gr.Markdown("")
                recap_box = gr.Markdown("Tekan **Buat Rekap** untuk menghitung rekap dari database.")
                recap_state = gr.State({})
                with gr.Row():
                    btn_csv = gr.Button("⬇ Ekspor CSV", size="sm")
                    btn_pdf = gr.Button("⬇ Ekspor PDF", size="sm")
                csv_file = gr.File(label="CSV", visible=True)
                pdf_file = gr.File(label="PDF", visible=True)

            # ---------------- Tab 6: Pengaturan ----------------
            with gr.Tab("⚙ Pengaturan"):
                gr.Markdown("### Model lokal & perangkat")
                gr.HTML(f"<table style='width:100%'>{settings_status()}</table>")
                with gr.Row():
                    set_whisper = gr.Dropdown(list(config.WHISPER_MODELS),
                                              value=cfg["whisper_model"], label="Model Whisper")
                    set_device = gr.Dropdown(["auto", "cuda", "cpu"], value=cfg["whisper_device"],
                                             label="Perangkat Whisper")
                    set_llm = gr.Dropdown(list(config.LLM_MODELS), value=cfg["llm_model"],
                                          label="Model LLM (GGUF)")
                with gr.Row():
                    set_gpu = gr.Number(value=cfg["llm_gpu_layers"], label="llama.cpp GPU layers (-1 = semua bila GPU build)", precision=0)
                    set_ctx = gr.Number(value=cfg["llm_ctx"], label="Konteks LLM", precision=0)
                    btn_save_cfg = gr.Button("Simpan pengaturan", variant="primary")
                cfg_msg = gr.Markdown("")
                gr.Markdown("""<span class='muted'>Model tidak diunduh otomatis. Jalankan
                `setup.bat` (atau `python setup_models.py whisper-small llm-3b`) saat online,
                setelah itu aplikasi berjalan 100% offline.</span>""")

            # ---------------- wiring ----------------
            all_outputs = [dashboard_box, approval_box, map_box, tickets_df,
                           reports_df_on_history, map_df, alerts_df]
            # Output setelah aksi tiket: pesan, tabel, ringkasan, pilihan, 3 tombol
            out_tiket = [approval_msg, tickets_df, ringkas_pilih, tiket_dipilih,
                         btn_approve, btn_decline, btn_hapus]
            out_map = [map_msg, map_df, ringkas_map, map_upd_id,
                       btn_map_approve, btn_map_reject]
            out_alert = [alert_msg, alerts_df, ringkas_alert, alert_dipilih,
                         btn_alert_ack, btn_alert_resolve]

            btn_process.click(process_audio_fn, [audio_in],
                              [transcript_box, analysis_box, json_box, status_box, *all_outputs])
            btn_text.click(save_transcript_text, [txt_in], [status_box, *all_outputs])
            btn_refresh_main.click(_refresh_all, None, all_outputs)
            # Mulai: nyalakan stream mikrofon + VAD (klik "Mulai" = izin mic).
            # JS mengirim status nyata ke hidden textbox supaya kegagalan
            # (izin mic ditolak, WASM gagal dimuat) TIDAK diam-diam.
            # `js=` WAJIB mengembalikan LIST sepanjang `outputs`, bukan satu
            # nilai. Kalau hanya satu string, Gradio menganggapnya objek
            # masukan dan klik gagal dengan
            # "Parameter `0` is not a valid keyword argument".
            btn_listen_on.click(do_start_listening_and_watch, None,
                                [live_status, vad_timer, vad_state],
                                js=("async () => { await window.__GARUDA_SET_LISTENING__?.(true); "
                                    "const s = window.__GARUDA_VAD_STATUS__ || 'belum'; "
                                    "return [null, null, s + '|' + (window.__GARUDA_VAD_INFO__ || '')]; }"),
                                concurrency_limit=None, concurrency_id="garuda_listen")
            btn_listen_off.click(do_stop_listening_and_watch, None,
                                 [live_status, vad_timer, vad_state],
                                 js=("async () => { await window.__GARUDA_SET_LISTENING__?.(false); "
                                     "return [null, null, 'nonaktif|']; }"),
                                 concurrency_limit=None, concurrency_id="garuda_listen")
            # Timer membaca status VAD langsung dari browser lewat js=, lalu
            # server menerjemahkannya jadi pesan. Ini yang membuat kegagalan
            # (izin mic, WASM, upload) terlihat operator, bukan diam.
            vad_timer.tick(vad_report, [vad_state], live_status,
                           js=("() => (window.__GARUDA_VAD_STATUS__ || 'belum') + '|' "
                               "+ (window.__GARUDA_VAD_INFO__ || '')"))
            # Pintu masuk potongan audio dari browser. Skrip VAD memanggil
            # endpoint ini setiap kali sunyi cukup lama - tanpa klik operator.
            # Memakai tombol tersembunyi supaya event-nya punya api_name tetap.
            btn_ingest_hidden.click(do_ingest_clip, [vad_path, vad_durasi_info],
                                    live_status,
                                    api_name="ingest_clip",
                                    concurrency_limit=None,
                                    concurrency_id="garuda_ingest")
            live_timer = gr.Timer(2.0, active=True)
            live_timer.tick(live_queue_table, None, live_df)
            btn_samples.click(generate_samples_fn, None, sample_msg)
            btn_test_all.click(process_samples_fn, None, [demo_result, *all_outputs])

            # ---- Tab Tiket: klik baris -> aksi sesuai status ----
            tickets_df.select(saat_baris_tiket_diklik, None,
                              [ringkas_pilih, tiket_dipilih,
                               btn_approve, btn_decline, btn_hapus])
            btn_approve.click(do_approve_tiket, [tiket_dipilih], out_tiket)
            btn_decline.click(do_decline_tiket, [tiket_dipilih], out_tiket)
            btn_hapus.click(do_hapus_tiket, [tiket_dipilih], out_tiket)
            btn_terapkan_filter.click(do_filter_tiket,
                                      [f_status, f_prioritas, f_jenis, f_dari, f_sampai],
                                      [tickets_df, ringkas_pilih, tiket_dipilih,
                                       btn_approve, btn_decline, btn_hapus, filter_info])
            btn_reset_filter.click(do_reset_filter_tiket, None,
                                   [f_status, f_prioritas, f_jenis, f_dari, f_sampai,
                                    tickets_df, ringkas_pilih, tiket_dipilih,
                                    btn_approve, btn_decline, btn_hapus, filter_info])
            btn_approval_refresh.click(
                lambda: (_ticket_rows(), PETUNJUK_PILIH, None, *_tombol_untuk(None),
                         f"Menampilkan **{len(_ticket_rows())}** tiket."),
                None, [tickets_df, ringkas_pilih, tiket_dipilih,
                       btn_approve, btn_decline, btn_hapus, filter_info])
            btn_hist.click(lambda f, t, c: _reports_table(f, t, c),
                           [hist_from, hist_to, hist_cat], reports_df_on_history)

            # ---- Peringatan: klik baris -> tandai ----
            alerts_df.select(saat_baris_alert_diklik, None,
                             [ringkas_alert, alert_dipilih,
                              btn_alert_ack, btn_alert_resolve])
            btn_alert_ack.click(lambda a: do_tandai_alert(a, "Acknowledged"),
                                [alert_dipilih], out_alert)
            btn_alert_resolve.click(lambda a: do_tandai_alert(a, "Resolved"),
                                    [alert_dipilih], out_alert)

            # ---- Peta: klik baris -> setujui/tolak ----
            map_df.select(saat_baris_peta_diklik, None,
                          [ringkas_map, map_upd_id, btn_map_approve, btn_map_reject])
            btn_map_refresh.click(preview_map, None, map_box)
            btn_map_approve.click(lambda u: do_map_decision(u, True), [map_upd_id], out_map)
            btn_map_reject.click(lambda u: do_map_decision(u, False), [map_upd_id], out_map)
            btn_recap.click(do_recap, [recap_from, recap_to, recap_cat],
                            [recap_box, recap_msg, recap_state, csv_file, pdf_file])
            btn_csv.click(lambda r: do_export(r, "csv"), [recap_state], csv_file)
            btn_pdf.click(lambda r: do_export(r, "pdf"), [recap_state], pdf_file)
            btn_save_cfg.click(save_settings, [set_whisper, set_device, set_llm, set_gpu, set_ctx],
                               cfg_msg)

        # panel persetujuan ikut refresh
        return demo


VAD_BOOT_JS = """
<script src="__VAD_BASE__/ort.min.js" type="text/javascript"></script>
<script src="__VAD_BASE__/vad-web.bundle.min.js" type="text/javascript"></script>
<script>
// GARUDA - mode dengar terus.
//
// Mikrofon dibuka SEKALI dan streaming terus selama mode aktif. VAD memantau
// tiap frame; ketika terdeteksi bicara lalu SUNYI __SILENCE_SEC__ detik,
// potongan audio itu langsung diubah jadi WAV dan dikirim ke server untuk
// diproses. Tidak ada tombol record/stop, tidak ada tombol "Proses audio".
//
// Kedua pustaka adalah skrip UMD (bukan ES module), jadi WAJIB dimuat lewat
// <script> biasa sebelum skrip ini. Urutan penting:
//   1. ort.min.js       -> window.ort (onnxruntime-web)
//   2. vad-web.bundle   -> window.vad = factory(window.ort)
//
// Aset (silero_vad.onnx, vad.worklet.bundle.min.js, ort-wasm-*.wasm) dicari
// relatif ke src tag <script> vad-web terakhir, yaitu folder models/vad.
(function () {
  var BASE = "__VAD_BASE__/";
  var SILENCE_SEC = __SILENCE_SEC__;
  var SUBMIT_PATH = "__SUBMIT_PATH__";
  var log = function () { console.log.apply(console, ["[garuda-vad]"].concat([].slice.call(arguments))); };

  var FRAME_SAMPLES = 1536;      // frameSamples bawaan pustaka
  var SR = 16000;                // sample rate keluaran pustaka
  var FRAME_SEC = FRAME_SAMPLES / SR;   // 0,096 s per frame

  // Dua hal BERBEDA yang jangan dicampur:
  //
  //  1. REDEMPTION_FRAMES = kapan pustaka menganggap "bicara selesai".
  //     Ini harus PENDEK (~0,77 s). Kalau dibuat 3 detik, setiap transmisi
  //     pendek ("Alfa, terima") menunggu 3 detik sunyi dulu baru ditutup,
  //     sehingga transmisi yang berjeda <3 detik DIGABUNG jadi satu klip
  //     raksasa, dan jeda napas memecah/menghapus klip.
  //
  //  2. GERBANG_SUNYI = kapan KITA mengirim klip, yaitu setelah sunyi
  //     SILENCE_SEC. Bila transmisi baru datang sebelum gerbang lepas,
  //     segmennya digabung dengan segmen yang sedang menunggu - persis
  //     seperti operator radio mendengar satu kalimat utuh.
  var REDEMPTION_FRAMES = 8;

  var state = null;      // { vad } setelah mikrofon benar-benar aktif
  var pending = null;    // MicVAD yang sedang dibuat (belum boleh dianggap aktif)
  var generasi = 0;      // dinaikkan tiap Mulai/Hentikan; membatalkan init usang
  var terkirim = 0;
  var gagal = 0;

  // Gerbang sunyi dihitung dalam FRAME (0,096 s), bukan detik jam dinding.
  var SILENCE_FRAMES = Math.max(1, Math.round(SILENCE_SEC / FRAME_SEC));   // 3 s -> 31
  var BATAS_BICARA = 0.5;   // positiveSpeechThreshold bawaan pustaka

  // Penampung transmisi yang menunggu gerbang sunyi.
  var segmen = [];        // daftar { audio: Float32Array, ekor: frame sunyi di ujung }
  var sunyiFrame = 0;     // frame sunyi berturut-turut setelah segmen terakhir

  window.__GARUDA_VAD_STATUS__ = "belum";
  window.__GARUDA_VAD_INFO__ = "";

  function setStatus(s) { window.__GARUDA_VAD_STATUS__ = s; }
  function setInfo(s) { window.__GARUDA_VAD_INFO__ = s; }

  // Float32Array 16 kHz -> WAV 16-bit mono -> Blob
  function keWav(float32) {
    var n = float32.length;
    var buf = new ArrayBuffer(44 + n * 2);
    var v = new DataView(buf);
    function teks(off, s) { for (var i = 0; i < s.length; i++) v.setUint8(off + i, s.charCodeAt(i)); }
    teks(0, "RIFF");
    v.setUint32(4, 36 + n * 2, true);
    teks(8, "WAVE");
    teks(12, "fmt ");
    v.setUint32(16, 16, true);
    v.setUint16(20, 1, true);          // PCM
    v.setUint16(22, 1, true);          // mono
    v.setUint32(24, SR, true);
    v.setUint32(28, SR * 2, true);
    v.setUint16(32, 2, true);
    v.setUint16(34, 16, true);
    teks(36, "data");
    v.setUint32(40, n * 2, true);
    var off = 44;
    for (var i = 0; i < n; i++) {
      var s = Math.max(-1, Math.min(1, float32[i]));
      v.setInt16(off, s < 0 ? s * 0x8000 : s * 0x7fff, true);
      off += 2;
    }
    return new Blob([buf], { type: "audio/wav" });
  }

  // Baca hasil dari endpoint call. Gradio menjawab POST dengan {event_id},
  // hasil sebenarnya ada di GET .../<event_id> sebagai SSE. Tanpa ini,
  // penolakan server (mis. mode sudah nonaktif) tidak pernah terlihat dan
  // kita akan berbohong "terkirim".
  async function bacaHasil(eventId) {
    try {
      var r = await fetch(SUBMIT_PATH + "/" + encodeURIComponent(eventId));
      if (!r.ok || !r.body) return "";
      var teks = await r.text();
      var akhir = "";
      teks.split("\\n").forEach(function (baris) {
        if (baris.indexOf("data:") === 0) {
          var isi = baris.slice(5).trim();
          if (isi && isi !== "null") akhir = isi;
        }
      });
      return akhir;
    } catch (e) {
      return "";
    }
  }

  async function kirim(blob, detik) {
    var nama = "garuda-" + Date.now() + ".wav";
    var fd = new FormData();
    fd.append("files", blob, nama);
    var up = await fetch("/gradio_api/upload", { method: "POST", body: fd });
    if (!up.ok) throw new Error("unggah gagal HTTP " + up.status);
    var daftar = await up.json();
    var path = daftar && daftar[0];
    if (!path) throw new Error("server tidak mengembalikan path berkas");

    var r = await fetch(SUBMIT_PATH, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ data: [path, detik] }),
    });
    if (!r.ok) throw new Error("kirim gagal HTTP " + r.status);
    var j = await r.json();
    if (j && j.error) throw new Error(String(j.error));

    // Tunggu hasil sungguhan, supaya penolakan server tidak dianggap sukses.
    var hasil = j && j.event_id ? await bacaHasil(j.event_id) : "";
    if (hasil) {
      var pesan = hasil;
      try { pesan = JSON.parse(hasil); } catch (e) {}
      if (Array.isArray(pesan)) pesan = pesan[0];
      pesan = String(pesan || "");
      if (/nonaktif|ditolak|pendek|Gagal/i.test(pesan)) throw new Error(pesan.slice(0, 140));
    }
    log("transmisi " + detik.toFixed(1) + " s terkirim ke antrian");
    return hasil;
  }

  // Kirim semua segmen yang menunggu sebagai SATU klip.
  function lepasGerbang() {
    if (!segmen.length) return;
    var total = 0;
    segmen.forEach(function (s) { total += s.audio.length; });
    var gab = new Float32Array(total);
    var o = 0;
    segmen.forEach(function (s) { gab.set(s.audio, o); o += s.audio.length; });

    // Buang sunyi ekor segmen terakhir; sunyi itulah yang memicu pengiriman
    // dan tidak berisi apa pun. Simpan sampai kata terakhir.
    var ekor = segmen[segmen.length - 1].ekor * FRAME_SAMPLES;
    var potong = Math.min(ekor, Math.max(0, gab.length - 1));
    var bersih = potong > 0 ? gab.subarray(0, gab.length - potong) : gab;

    segmen = [];
    sunyiFrame = 0;

    var detik = bersih.length / SR;
    if (detik < 0.2) {   // sisa derau, bukan transmisi
      // Jangan buang diam-diam: catat supaya operator bisa membedakan
      // "tidak ada suara" dari "suara terlalu pendek".
      log("segmen " + detik.toFixed(2) + " s diabaikan (terlalu pendek)");
      return;
    }
    setInfo("mengirim transmisi " + detik.toFixed(1) + " s...");
    kirim(keWav(bersih), detik).then(function () {
      terkirim += 1;
      setInfo("mendengarkan (" + terkirim + " transmisi terkirim)");
    }).catch(function (e) {
      gagal += 1;
      console.error("[garuda-vad] kirim gagal:", e);
      setInfo("GAGAL mengirim: " + String(e).slice(0, 120));
    });
  }

  // Gerbang sunyi berbasis FRAME, bukan jam dinding. Dipanggil pustaka
  // untuk setiap frame lewat onFrameProcessed, jadi hitungannya mengikuti
  // audio yang benar-benar masuk - tidak meleset saat tab di-latar atau
  // saat thread sibuk.
  function periksaGerbang() {
    if (!segmen.length) return;
    if (sunyiFrame < SILENCE_FRAMES) return;
    lepasGerbang();
  }

  function bereskan(v) {
    if (!v) return;
    try { v.pause(); } catch (e) {}
    try {
      if (v.stream && v.stream.getTracks) v.stream.getTracks().forEach(function (t) { t.stop(); });
    } catch (e) {}
    try {
      if (v.audioNodeVAD && v.audioNodeVAD.entryNode && v.audioNodeVAD.entryNode.disconnect) {
        v.audioNodeVAD.entryNode.disconnect();
      }
    } catch (e) {}
    try { if (v.audioContext && v.audioContext.close) v.audioContext.close(); } catch (e) {}
  }

  window.__GARUDA_SET_LISTENING__ = async function (on) {
    var gen = ++generasi;
    try {
      if (on) {
        if (state) { setStatus("aktif"); return true; }
        if (pending) { setStatus("menyiapkan"); return true; }
        if (typeof window.ort === "undefined") throw new Error("ort.min.js tidak termuat");
        if (!window.vad || !window.vad.MicVAD) throw new Error("vad-web.bundle.min.js tidak termuat");

        setStatus("menyiapkan");
        window.ort.env.wasm.wasmPaths = BASE;
        segmen = [];
        sunyiFrame = 0;

        pending = window.vad.MicVAD.new({
          redemptionFrames: REDEMPTION_FRAMES,
          // Satu-satunya sumber kebenaran soal sunyi: probabilitas bicara
          // per frame dari model. Hitungannya tidak bisa meleset karena
          // tidak bergantung pada timer atau beban thread.
          onFrameProcessed: function (probs) {
            if (gen !== generasi) return;
            var bicara = (probs && probs.isSpeech !== undefined)
              ? probs.isSpeech : probs;
            if (typeof bicara !== "number") return;
            if (bicara >= BATAS_BICARA) {
              sunyiFrame = 0;
              return;
            }
            if (!segmen.length) return;   // belum ada transmisi, tidak perlu dihitung
            sunyiFrame += 1;
            periksaGerbang();
          },
          onSpeechStart: function () {
            if (gen !== generasi) return;
            setInfo(segmen.length ? "menyambung transmisi..." : "mendengar suara...");
          },
          onVADMisfire: function () {
            if (gen !== generasi) return;
            setInfo(segmen.length ? "menunggu sunyi " + SILENCE_SEC + " s..." : "mendengarkan");
          },
          onSpeechEnd: function (audio) {
            if (gen !== generasi) return;
            // Pustaka sudah menutup segmen setelah REDEMPTION_FRAMES sunyi
            // dan menyertakan sunyi itu di ujung audio. Catat untuk dipangkas.
            segmen.push({ audio: audio, ekor: REDEMPTION_FRAMES });
            sunyiFrame = 0;
            setInfo("transmisi " + (audio.length / SR).toFixed(1) + " s, menunggu sunyi "
                    + SILENCE_SEC + " s...");
          },
        });

        var vad;
        try {
          vad = await pending;
        } finally {
          pending = null;
        }
        if (gen !== generasi) {
          // Hentikan ditekan saat masih menyiapkan: lepas mikrofon yang
          // terlanjur terbuka, JANGAN klaim aktif.
          bereskan(vad);
          log("init dibatalkan karena Hentikan");
          return false;
        }
        await vad.start();
        state = { vad: vad };
        setStatus("aktif");
        setInfo("mendengarkan (sunyi " + SILENCE_SEC + " s = potong & proses)");
        log("aktif - pustaka potong setelah " + REDEMPTION_FRAMES
            + " frame, kirim setelah sunyi " + SILENCE_SEC + " s");
      } else {
        var s = state;
        state = null;
        var p = pending;
        pending = null;
        // PENTING: bereskan() menerima INSTANS MicVAD, bukan pembungkus
        // { vad }. Melewatkan pembungkus membuat .stream tidak ketemu dan
        // mikrofon tidak pernah dilepas (bug nyata, tertangkap uji).
        if (s) bereskan(s.vad);
        if (!s && p) {
          // Init masih jalan; gen sudah dinaikkan sehingga hasilnya dibuang
          // dan mikrofonnya dilepas begitu getUserMedia selesai.
          log("Hentikan saat menyiapkan - mikrofon akan dilepas");
        }
        segmen = [];
        sunyiFrame = 0;
        setStatus("nonaktif");
        setInfo("mikrofon dilepas");
        log("nonaktif - mikrofon dilepas");
      }
      return true;
    } catch (e) {
      console.error("[garuda-vad] gagal:", e);
      window.__GARUDA_VAD_ERROR__ = String(e);
      pending = null;
      segmen = [];
      setStatus("gagal: " + String(e));
      setInfo("gagal: " + String(e));
      return false;
    }
  };

  log("skrip dimuat (menunggu tombol Mulai)");
})();
</script>
"""



def launch():  # pragma: no cover
    db.init_db()
    port = int(os.environ.get("GARUDA_PORT", "7860"))
    demo = build_ui()
    demo.queue(default_concurrency_limit=2)
    vad_base = "/gradio_api/file=" + config.VAD_DIR.as_posix()
    head = ""
    if config.vad_assets_ready():
        head = (VAD_BOOT_JS
                .replace("__VAD_BASE__", vad_base)
                .replace("__SILENCE_SEC__", f"{config.SILENCE_SECONDS:g}")
                .replace("__SUBMIT_PATH__", "/gradio_api/call/ingest_clip"))
    print(f"GARUDA Radio AI siap di http://127.0.0.1:{port}")
    if not head:
        print("Catatan: aset VAD belum ada - deteksi suara otomatis nonaktif. "
              "Jalankan: setup_models.py vad")
    demo.launch(server_name="127.0.0.1", server_port=port, inbrowser=True,
                footer_links=[], ssr_mode=False, theme=gr.themes.Soft(), css=CSS,
                js="", head=head,
                allowed_paths=[str(config.EXPORT_DIR), str(config.SAMPLES_DIR),
                               str(config.VAD_DIR)])


if __name__ == "__main__":
    launch()
