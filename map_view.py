"""
GARUDA Radio AI - peta interaktif sederhana (Folium -> HTML di Gradio).

Semua koordinat berasal dari tabel `locations` (DEMO DATA).
AI tidak pernah mengarang koordinat: pembaruan peta hanya mengubah *status* lokasi.
"""
from __future__ import annotations

import html

import config
import database as db

COLORS = {
    "Normal": "green",
    "Gangguan": "orange",
    "Rusak - tidak bisa dilalui": "red",
    "Perlu verifikasi": "gray",
}

KIND_ICON = {"pos": "flag", "fasilitas": "home", "infrastruktur": "bridge"}


def _popup(loc: dict) -> str:
    reports = [r for r in db.list_reports(limit=50) if r.get("location_key") == loc["key"]]
    last = reports[0] if reports else None
    last_html = (
        f"<div style='margin-top:6px'>Laporan terbaru #{last['id']}: "
        f"{html.escape(last['summary'] or '')}</div>" if last else "<div>Belum ada laporan.</div>"
    )
    return f"""<div style="font-family:system-ui;min-width:220px">
      <div style="font-weight:700;font-size:14px">{html.escape(loc['name'])}</div>
      <div style="font-size:11px;color:#888">DEMO DATA &middot; {html.escape(loc['kind'])}</div>
      <div style="margin-top:6px">Status: <b>{html.escape(loc['status'])}</b></div>
      <div style="font-size:11px;color:#777">{html.escape(loc.get('note') or '')}</div>
      {last_html}
    </div>"""


def build_map() -> str:
    """Bangun HTML peta. Pakai Folium bila tersedia, fallback ke SVG sederhana."""
    locs = db.list_locations()
    alerts = [a for a in db.list_alerts(limit=20) if a["status"] != "Resolved"]
    try:
        return _folium_map(locs, alerts)
    except Exception as exc:  # pragma: no cover - folium opsional
        out = _svg_fallback(locs, alerts)
        return out.replace("Folium tidak terpasang", f"Folium gagal: {html.escape(str(exc))[:80]}")


def _folium_map(locs: list[dict], alerts: list[dict]) -> str:
    import folium
    from folium.plugins import MarkerCluster

    fmap = folium.Map(location=list(config.MAP_CENTER), zoom_start=config.MAP_ZOOM,
                      tiles="OpenStreetMap", control_scale=True)
    cluster = MarkerCluster(name="Pos & Fasilitas").add_to(fmap)

    for loc in locs:
        color = COLORS.get(loc["status"], "blue")
        folium.Marker(
            location=[loc["lat"], loc["lon"]],
            popup=folium.Popup(_popup(loc), max_width=280),
            tooltip=f"{loc['name']} - {loc['status']}",
            icon=folium.Icon(color=color,
                             icon=KIND_ICON.get(loc["kind"], "info-sign"), prefix="glyphicon"),
        ).add_to(cluster)

    for a in alerts:
        loc = next((l for l in locs if l["name"] == a["location"]), None)
        if not loc:
            continue
        folium.Marker(
            location=[loc["lat"] + 0.0025, loc["lon"] + 0.0025],
            popup=folium.Popup(
                f"<b>PERINGATAN #{a['id']}</b><br>{html.escape(a['message'])}<br>"
                f"Prioritas: {a['priority']} &middot; {a['status']}", max_width=260),
            tooltip=f"Alert #{a['id']} ({a['priority']})",
            icon=folium.Icon(color="red", icon="warning-sign", prefix="glyphicon"),
        ).add_to(fmap)

    folium.LayerControl().add_to(fmap)

    legend = """
    <div style="position:fixed;bottom:24px;left:24px;z-index:9999;background:#111827;color:#e5e7eb;
        padding:10px 14px;border-radius:10px;font-family:system-ui;font-size:12px;
        box-shadow:0 4px 14px rgba(0,0,0,.35)">
      <div style="font-weight:700;margin-bottom:4px">DEMO DATA</div>
      <div>🟢 Normal &nbsp; 🟠 Gangguan &nbsp; 🔴 Rusak &nbsp; ⚪ Perlu verifikasi</div>
      <div style="opacity:.7;margin-top:4px">Koordinat fiktif untuk simulasi.</div>
    </div>"""
    fmap.get_root().html.add_child(folium.Element(legend))
    return fmap.get_root().render()


def _svg_fallback(locs: list[dict], alerts: list[dict]) -> str:
    """Peta offline tanpa internet (Folium tak tersedia): SVG dari koordinat DEMO."""
    lat0, lon0 = config.MAP_CENTER
    w, h = 900, 520
    pts = []
    for loc in locs:
        x = w / 2 + (loc["lon"] - lon0) * 6000
        y = h / 2 - (loc["lat"] - lat0) * 6000
        color = COLORS.get(loc["status"], "blue")
        pts.append(f"""<circle cx="{x:.0f}" cy="{y:.0f}" r="9" fill="{color}" stroke="#fff" stroke-width="2"/>
        <text x="{x + 14:.0f}" y="{y + 4:.0f}" font-size="12" fill="#e5e7eb">{html.escape(loc['name'])}</text>
        <text x="{x + 14:.0f}" y="{y + 18:.0f}" font-size="10" fill="#9ca3af">{html.escape(loc['status'])}</text>""")
    return f"""<div style="background:#0b1220;border-radius:12px;padding:8px">
      <div style="color:#f87171;font-weight:700;font-size:12px;margin:6px">DEMO DATA - peta offline (Folium tidak terpasang)</div>
      <svg viewBox="0 0 {w} {h}" width="100%" height="480" style="background:#0b1220">
        <rect width="{w}" height="{h}" fill="#0b1220"/>
        {''.join(pts)}
      </svg></div>"""


def pending_updates_html() -> str:
    ups = [u for u in db.list_map_updates(50) if u["status"] == "Pending Approval"]
    if not ups:
        return "<div class='muted'>Tidak ada pembaruan peta yang menunggu persetujuan.</div>"
    rows = "".join(
        f"<li><b>#{u['id']}</b> {html.escape(u['location_name'] or u['location_key'])} → "
        f"<b>{html.escape(u['new_status'])}</b> <span class='muted'>({html.escape(u['note'] or '')})</span></li>"
        for u in ups
    )
    return f"<div class='panel warn'><b>Pembaruan peta menunggu persetujuan:</b><ul>{rows}</ul></div>"
