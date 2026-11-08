"""
GARUDA Radio AI - rekap laporan (data SQLite -> CSV / PDF).

Rekap dihitung dari database lokal. Ringkasan naratif memakai LLM bila tersedia;
bila tidak, dipakai ringkasan aturan (tanpa mengarang data).
"""
from __future__ import annotations

import csv
from datetime import date, datetime
from pathlib import Path

import config
import database as db


def _filtered(date_from: str | None, date_to: str | None, category: str | None) -> dict:
    reports = db.list_reports(limit=10000, category=category, date_from=date_from, date_to=date_to)
    tickets = db.list_tickets(limit=10000)
    alerts = db.list_alerts(limit=10000)
    if date_from:
        tickets = [t for t in tickets if t["created_at"][:10] >= date_from]
        alerts = [a for a in alerts if a["created_at"][:10] >= date_from]
    if date_to:
        tickets = [t for t in tickets if t["created_at"][:10] <= date_to]
        alerts = [a for a in alerts if a["created_at"][:10] <= date_to]
    return {"reports": reports, "tickets": tickets, "alerts": alerts}


def build_recap(date_from: str | None = None, date_to: str | None = None,
                category: str | None = None, cfg: dict | None = None) -> dict:
    data = _filtered(date_from, date_to, category)
    reports = data["reports"]
    cats: dict[str, int] = {}
    for r in reports:
        cats[r["category"]] = cats.get(r["category"], 0) + 1
    open_tickets = [t for t in data["tickets"] if t["status"] not in ("Completed", "Rejected")]
    open_alerts = [a for a in data["alerts"] if a["status"] != "Resolved"]
    important = [r for r in reports
                 if r["priority"] in ("HIGH", "CRITICAL") or r["needs_verification"]]
    important.sort(key=lambda r: r["created_at"])

    lines = []
    lines.append(f"Rekap Laporan GARUDA Radio AI (SIMULASI)")
    lines.append(f"Periode: {date_from or '-'} s/d {date_to or '-'} | Kategori: {category or 'Semua'}")
    lines.append(f"Dibuat: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("")
    lines.append(f"Jumlah laporan: {len(reports)}")
    lines.append(f"Tiket terbuka: {len(open_tickets)}")
    lines.append(f"Peringatan belum ditangani: {len(open_alerts)}")
    lines.append("")
    lines.append("Ringkasan per kategori:")
    for c, n in sorted(cats.items(), key=lambda kv: -kv[1]):
        lines.append(f"  - {c}: {n}")
    if not cats:
        lines.append("  (tidak ada data)")
    lines.append("")
    lines.append("Tiket terbuka:")
    for t in open_tickets[:20]:
        lines.append(f"  #{t['id']} [{t['status']}] {t['kind']} - {t['item']} "
                     f"{t['quantity'] or ''} {t['unit_measure'] or ''} ({t['requester'] or '-'})")
    if not open_tickets:
        lines.append("  (tidak ada)")
    lines.append("")
    lines.append("Peringatan belum ditangani:")
    for a in open_alerts[:20]:
        lines.append(f"  #{a['id']} [{a['priority']}/{a['status']}] {a['message']}")
    if not open_alerts:
        lines.append("  (tidak ada)")
    lines.append("")
    lines.append("Kronologi laporan penting (prioritas tinggi / perlu verifikasi):")
    for r in important[:20]:
        lines.append(f"  {r['created_at']} #{r['id']} [{r['category']}/{r['priority']}] "
                     f"{r['summary'] or ''} (lokasi: {r['location'] or '-'}, unit: {r['unit'] or '-'})")
    if not important:
        lines.append("  (tidak ada)")

    narrative = ""
    if cfg is not None:
        try:
            import ai

            narrative = ai.recap_narrative(
                {"jumlah_laporan": len(reports), "per_kategori": cats,
                 "tiket_terbuka": len(open_tickets), "peringatan": len(open_alerts),
                 "penting": [r["summary"] for r in important[:10]]}, cfg)
        except Exception as exc:
            narrative = f"(Rekap naratif tidak tersedia: {exc})"

    return {
        "date_from": date_from, "date_to": date_to, "category": category or "Semua",
        "reports": reports, "cats": cats, "open_tickets": open_tickets,
        "open_alerts": open_alerts, "important": important,
        "text": "\n".join(lines), "narrative": narrative,
    }


def recap_markdown(recap: dict) -> str:
    md = ["### Buat Rekap",
          f"**Periode:** {recap['date_from'] or '-'} → {recap['date_to'] or '-'}  |  "
          f"**Kategori:** {recap['category']}",
          "",
          "| Metrik | Nilai |", "|---|---|",
          f"| Jumlah laporan | {len(recap['reports'])} |",
          f"| Tiket terbuka | {len(recap['open_tickets'])} |",
          f"| Peringatan belum ditangani | {len(recap['open_alerts'])} |",
          f"| Laporan penting | {len(recap['important'])} |",
          ""]
    if recap["cats"]:
        md += ["**Per kategori:** " + ", ".join(f"{k}: {v}" for k, v in recap["cats"].items()), ""]
    if recap["narrative"]:
        md += ["**Ringkasan naratif (LLM lokal):**", recap["narrative"], ""]
    md += ["**Kronologi penting:**"]
    for r in recap["important"][:10]:
        md.append(f"- `{r['created_at']}` #{r['id']} [{r['category']}/{r['priority']}] "
                  f"{r['summary'] or ''}")
    if not recap["important"]:
        md.append("- (tidak ada)")
    return "\n".join(md)


def export_csv(recap: dict) -> str:
    path = config.EXPORT_DIR / f"rekap_{date.today().isoformat()}_{datetime.now():%H%M%S}.csv"
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["id", "waktu", "kategori", "unit", "ringkasan", "item", "jumlah",
                    "satuan", "lokasi", "prioritas", "aksi", "keyakinan",
                    "perlu_verifikasi", "status"])
        for r in recap["reports"]:
            w.writerow([r["id"], r["created_at"], r["category"], r["unit"], r["summary"],
                        r["item"], r["quantity"], r["unit_measure"], r["location"],
                        r["priority"], r["action"], r["confidence"],
                        "ya" if r["needs_verification"] else "tidak", r["status"]])
    return str(path)


def export_pdf(recap: dict) -> str:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas as pdfcanvas
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from reportlab.lib import colors

    path = config.EXPORT_DIR / f"rekap_{date.today().isoformat()}_{datetime.now():%H%M%S}.pdf"
    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm,
                            title="Rekap GARUDA Radio AI")
    styles = getSampleStyleSheet()
    story = [Paragraph("Rekap Laporan - GARUDA Radio AI", styles["Title"]),
             Paragraph("SEMUA DATA SIMULASI (DEMO DATA) - bukan komunikasi militer asli",
                       styles["Italic"]),
             Spacer(1, 6),
             Paragraph(f"Periode: {recap['date_from'] or '-'} s/d {recap['date_to'] or '-'} | "
                       f"Kategori: {recap['category']} | Dibuat: {datetime.now():%Y-%m-%d %H:%M}",
                       styles["Normal"]),
             Spacer(1, 10)]
    summary = [["Metrik", "Nilai"],
               ["Jumlah laporan", str(len(recap["reports"]))],
               ["Tiket terbuka", str(len(recap["open_tickets"]))],
               ["Peringatan belum ditangani", str(len(recap["open_alerts"]))],
               ["Laporan penting", str(len(recap["important"]))]]
    t = Table(summary, colWidths=[80 * mm, 40 * mm])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
                           ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                           ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                           ("FONTSIZE", (0, 0), (-1, -1), 9)]))
    story += [t, Spacer(1, 10)]

    story.append(Paragraph("<b>Ringkasan per kategori</b>", styles["Heading3"]))
    for k, v in recap["cats"].items():
        story.append(Paragraph(f"{k}: {v}", styles["Normal"]))
    if not recap["cats"]:
        story.append(Paragraph("(tidak ada data)", styles["Normal"]))

    if recap["narrative"]:
        story += [Spacer(1, 6), Paragraph("<b>Ringkasan naratif (LLM lokal)</b>", styles["Heading3"]),
                  Paragraph(recap["narrative"].replace("\n", " "), styles["Normal"])]

    story += [Spacer(1, 8), Paragraph("<b>Kronologi laporan penting</b>", styles["Heading3"])]
    rows = [["Waktu", "ID", "Kategori", "Prioritas", "Ringkasan"]]
    for r in recap["important"][:40]:
        rows.append([r["created_at"][5:16], str(r["id"]), r["category"], r["priority"],
                     Paragraph((r["summary"] or "")[:160], styles["Normal"])])
    if len(rows) == 1:
        rows.append(["-", "-", "-", "-", "(tidak ada)"])
    t2 = Table(rows, colWidths=[26 * mm, 10 * mm, 26 * mm, 18 * mm, 90 * mm], repeatRows=1)
    t2.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#374151")),
                            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                            ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
                            ("FONTSIZE", (0, 0), (-1, -1), 7.5),
                            ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(t2)

    doc.build(story,
              onFirstPage=_footer, onLaterPages=_footer)
    return str(path)


def _footer(canvas: pdfcanvas.Canvas, doc):  # pragma: no cover
    from reportlab.lib.units import mm as _mm

    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.drawString(18 * _mm, 8 * _mm, "GARUDA Radio AI - DEMO DATA")
    canvas.restoreState()
