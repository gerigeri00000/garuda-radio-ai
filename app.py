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
        return "<div class='muted'>Belum ada analisis. Rekam atau unggah audio, lalu tekan Proses.</div>"
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
    return (dashboard_html(), approval_html(), map_view.build_map(),
            _tickets_table(), _reports_table(), _map_pending_table(), _alerts_table())


def process_audio_fn(audio_file):
    if not audio_file:
        return (gr.update(), analysis_html({}), "", "⚠ Tidak ada audio. Rekam atau unggah dulu.",
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


def _tickets_table():
    return [[t["id"], t["kind"], t["item"], t["quantity"], t["unit_measure"], t["requester"],
             t["priority"], t["status"], t["created_at"][:16]] for t in db.list_tickets(200)]


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
# ---------------------------------------------------------------------------
def do_approve_ticket(tid):
    if tid in (None, ""):
        return "⚠ Isi ID tiket.", *_refresh_all()
    r = actions.approve_ticket(int(tid))
    return ("✅ " if r["ok"] else "❌ ") + r["message"], *_refresh_all()


def do_set_ticket_status(tid, status):
    if tid in (None, ""):
        return "⚠ Isi ID tiket.", *_refresh_all()
    r = actions.set_ticket_status(int(tid), status)
    return ("✅ " if r["ok"] else "❌ ") + r["message"], *_refresh_all()


def do_apply_status(rid, tid, status):
    if rid in (None, "") or tid in (None, ""):
        return "⚠ Isi ID laporan & tiket.", *_refresh_all()
    r = actions.apply_status_update(int(rid), int(tid), status)
    return ("✅ " if r["ok"] else "❌ ") + r["message"], *_refresh_all()


def do_map_decision(uid, approve):
    if uid in (None, ""):
        return "⚠ Isi ID pembaruan peta.", *_refresh_all()
    r = (actions.approve_map_update if approve else actions.reject_map_update)(int(uid))
    return ("✅ " if r["ok"] else "❌ ") + r["message"], *_refresh_all()


def do_alert_status(aid, status):
    if aid in (None, ""):
        return "⚠ Isi ID alert.", *_refresh_all()
    r = actions.acknowledge_alert(int(aid), status)
    return ("✅ " if r["ok"] else "❌ ") + r["message"], *_refresh_all()


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
            # ---------------- Tab 1: Monitor & Analisis ----------------
            with gr.Tab("🎙 Monitor & Analisis"):
                with gr.Row():
                    with gr.Column(scale=5):
                        with gr.Group():
                            gr.Markdown("**Input audio** (mikrofon browser, upload file, atau rekaman radio)")
                            audio_in = gr.Audio(sources=["microphone", "upload"], type="filepath",
                                                label="Rekam atau unggah audio (wav/mp3/m4a/ogg)",
                                                elem_id="audio_in")
                            with gr.Row():
                                btn_process = gr.Button("▶ Proses audio", variant="primary", scale=2)
                                btn_clear = gr.ClearButton([audio_in], value="Bersihkan", scale=1)
                            gr.Markdown("<span class='muted'>Pemrosesan berjalan di perangkat ini. "
                                        "File audio identik tidak akan membuat tiket/laporan ganda.</span>")
                            gr.Markdown(f"<span class='muted'>Perangkat audio input terdeteksi: "
                                        f"{html.escape('; '.join(audio.list_input_devices())[:300])}</span>")
                        with gr.Accordion("Transkrip manual (uji tanpa audio)", open=False):
                            txt_in = gr.Textbox(label="Teks laporan",
                                                placeholder="Contoh: Pos Bravo membutuhkan tambahan 30 liter solar.")
                            btn_text = gr.Button("Proses teks", size="sm")
                        with gr.Accordion("Konfigurasi model (lokal)", open=False):
                            gr.HTML(settings_status(), elem_id="cfg_status_small")
                    with gr.Column(scale=7):
                        status_box = gr.Markdown("Siap.")
                        transcript_box = gr.Textbox(label="Transkripsi (Whisper, bahasa Indonesia)",
                                                    lines=3, interactive=False)
                        analysis_box = gr.HTML(analysis_html({}))
                        with gr.Accordion("JSON mentah AI (audit)", open=False):
                            json_box = gr.Code(label="JSON", language="json", value="")

                outputs_common = []  # didefinisikan setelah komponen dibuat di bawah

                gr.Markdown("### Demo: 6 skenario simulasi")
                with gr.Row():
                    btn_samples = gr.Button("🎧 Buat contoh audio (TTS lokal)", size="sm")
                    btn_test_all = gr.Button("▶ Uji semua 6 skenario", variant="secondary", size="sm")
                    btn_refresh_main = gr.Button("🔄 Refresh data", size="sm")
                sample_msg = gr.Markdown("")
                demo_result = gr.HTML("")

            # ---------------- Tab 2: Dashboard ----------------
            with gr.Tab("📊 Dashboard"):
                dashboard_box = gr.HTML(dashboard_html())
                with gr.Row():
                    btn_dash_refresh = gr.Button("🔄 Refresh", size="sm")
                with gr.Row():
                    btn_seed = gr.Button("Isi data demo (hanya bila database kosong)", size="sm")
                seed_msg = gr.Markdown("")
                gr.Markdown("### Persetujuan operator")
                approval_box = gr.HTML(approval_html())

            # ---------------- Tab 3: Riwayat & Tiket ----------------
            with gr.Tab("🗂 Riwayat & Tiket"):
                gr.Markdown("### Persetujuan & perubahan status")
                with gr.Row():
                    ticket_id = gr.Number(label="ID Tiket", precision=0, scale=1)
                    btn_approve = gr.Button("✅ Setujui tiket (Pending → Approved)", variant="primary", scale=2)
                    ticket_status = gr.Dropdown(db.TICKET_STATUSES, value="In Progress", label="Ubah status", scale=1)
                    btn_ticket_set = gr.Button("Terapkan status", scale=1)
                with gr.Row():
                    status_report_id = gr.Number(label="ID Laporan (UPDATE_STATUS)", precision=0, scale=1)
                    status_ticket_id = gr.Number(label="ID Tiket terkait", precision=0, scale=1)
                    status_new = gr.Dropdown(db.TICKET_STATUSES, value="Completed", label="Status baru", scale=1)
                    btn_apply_status = gr.Button("Terapkan pembaruan status", scale=2)
                ticket_msg = gr.Markdown("")
                gr.Markdown("### Tiket")
                tickets_df = gr.Dataframe(headers=TICKET_COLS, value=_tickets_table(),
                                          interactive=False, wrap=True)
                gr.Markdown("### Riwayat laporan")
                with gr.Row():
                    hist_cat = gr.Dropdown(["Semua"] + ai.CATEGORIES, value="Semua", label="Kategori",
                                           scale=1)
                    hist_from = gr.DateTime(label="Dari tanggal", include_time=False, scale=1)
                    hist_to = gr.DateTime(label="Sampai tanggal", include_time=False, scale=1)
                    btn_hist = gr.Button("Filter", size="sm", scale=1)
                reports_df_on_history = gr.Dataframe(headers=REPORT_COLS, value=_reports_table(),
                                                     interactive=False, wrap=True)
                gr.Markdown("### Peringatan")
                with gr.Row():
                    alert_id = gr.Number(label="ID Alert", precision=0, scale=1)
                    alert_status = gr.Dropdown(db.ALERT_STATUSES, value="Acknowledged",
                                               label="Status alert", scale=1)
                    btn_alert = gr.Button("Ubah status alert", scale=1)
                alert_msg = gr.Markdown("")
                alerts_df = gr.Dataframe(headers=ALERT_COLS, value=_alerts_table(),
                                         interactive=False, wrap=True)

            # ---------------- Tab 4: Peta ----------------
            with gr.Tab("🗺 Peta (DEMO DATA)"):
                gr.Markdown("Marker memakai lokasi simulasi. Pembaruan status lokasi "
                            "**wajib disetujui operator**.")
                with gr.Row():
                    btn_map_refresh = gr.Button("🔄 Refresh peta", size="sm")
                map_box = gr.HTML(map_view.build_map(), elem_id="map_html")
                gr.Markdown("### Pembaruan peta")
                map_df = gr.Dataframe(headers=MAP_COLS, value=_map_pending_table(),
                                      interactive=False, wrap=True)
                with gr.Row():
                    map_upd_id = gr.Number(label="ID Pembaruan Peta", precision=0, scale=1)
                    btn_map_approve = gr.Button("✅ Setujui", variant="primary", scale=1)
                    btn_map_reject = gr.Button("✖ Tolak", scale=1)
                map_msg = gr.Markdown("")

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

            def refresh():
                return _refresh_all()

            btn_process.click(process_audio_fn, [audio_in],
                              [transcript_box, analysis_box, json_box, status_box, *all_outputs])
            btn_text.click(save_transcript_text, [txt_in], [status_box, *all_outputs])
            btn_refresh_main.click(_refresh_all, None, all_outputs)
            btn_dash_refresh.click(_refresh_all, None, all_outputs)
            btn_seed.click(_seed_wrapper, None, [seed_msg, *all_outputs])
            btn_samples.click(generate_samples_fn, None, sample_msg)
            btn_test_all.click(process_samples_fn, None, [demo_result, *all_outputs])
            btn_approve.click(do_approve_ticket, [ticket_id], [ticket_msg, *all_outputs])
            btn_ticket_set.click(do_set_ticket_status, [ticket_id, ticket_status],
                                 [ticket_msg, *all_outputs])
            btn_apply_status.click(do_apply_status, [status_report_id, status_ticket_id, status_new],
                                   [ticket_msg, *all_outputs])
            btn_alert.click(do_alert_status, [alert_id, alert_status], [alert_msg, *all_outputs])
            btn_hist.click(lambda f, t, c: _reports_table(f, t, c), [hist_from, hist_to, hist_cat],
                           reports_df_on_history)
            btn_map_refresh.click(preview_map, None, map_box)
            btn_map_approve.click(lambda u: do_map_decision(u, True), [map_upd_id],
                                  [map_msg, *all_outputs])
            btn_map_reject.click(lambda u: do_map_decision(u, False), [map_upd_id],
                                 [map_msg, *all_outputs])
            btn_recap.click(do_recap, [recap_from, recap_to, recap_cat],
                            [recap_box, recap_msg, recap_state, csv_file, pdf_file])
            btn_csv.click(lambda r: do_export(r, "csv"), [recap_state], csv_file)
            btn_pdf.click(lambda r: do_export(r, "pdf"), [recap_state], pdf_file)
            btn_save_cfg.click(save_settings, [set_whisper, set_device, set_llm, set_gpu, set_ctx],
                               cfg_msg)

        # panel persetujuan ikut refresh
        return demo


def launch():  # pragma: no cover
    db.init_db()
    port = int(os.environ.get("GARUDA_PORT", "7860"))
    demo = build_ui()
    demo.queue(default_concurrency_limit=2)
    print(f"GARUDA Radio AI siap di http://127.0.0.1:{port}")
    demo.launch(server_name="127.0.0.1", server_port=port, inbrowser=True,
                footer_links=[], ssr_mode=False, theme=gr.themes.Soft(), css=CSS,
                allowed_paths=[str(config.EXPORT_DIR), str(config.SAMPLES_DIR)])


def _seed_wrapper():
    n = samples_gen.seed_demo_reports()
    msg = (f"✅ {n} laporan demo simulasi dibuat." if n
           else "ℹ Database sudah berisi data, tidak ada data demo tambahan.")
    return (msg, *_refresh_all())


if __name__ == "__main__":
    launch()
