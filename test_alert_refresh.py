"""Regresi refresh halaman: data awal Gradio tidak boleh menghidupkan alert lama."""
from __future__ import annotations

import gc
import tempfile
from pathlib import Path

import app
import config
import database as db


def main():
    asal = config.DB_PATH
    with tempfile.TemporaryDirectory() as folder:
        config.DB_PATH = Path(folder) / "uji.db"
        try:
            db.init_db()
            aid = db.add_alert(category="MEDICAL", priority="HIGH",
                               message="DEMO uji refresh", location="Pos Alfa")
            ui = app.build_ui()  # Snapshot awal: Needs Review.
            app.do_tandai_alert(aid, "Acknowledged")
            app.do_tandai_alert(aid, "Resolved")
            assert db.get_alert(aid)["status"] == "Resolved"
            pemuat = [d for d in ui.config["dependencies"]
                      if any(event == "load" for _, event in d["targets"])]
            assert pemuat, "Refresh browser belum memuat ulang data dari DB"
            handler = ui.fns[pemuat[0]["id"]].fn
            hasil = handler()
            assert len(hasil) == len(pemuat[0]["outputs"])
            assert next(r for r in hasil[6] if r[0] == aid)[5] == "Resolved"
            assert "DEMO uji refresh" not in hasil[1]  # Bukan menunggu persetujuan.
            print("PASS: refresh halaman membaca Resolved dari DB, bukan snapshot awal")
        finally:
            gc.collect()  # SQLite context manager menutup transaksi, bukan koneksi.
            config.DB_PATH = asal


if __name__ == "__main__":
    main()
