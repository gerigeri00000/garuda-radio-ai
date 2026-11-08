"""Uji endpoint ingest_clip seperti yang dipanggil skrip VAD di browser.

Skrip ini menyalakan server Gradio sungguhan, lalu meniru persis dua panggilan
HTTP yang dilakukan browser:

    1. POST /gradio_api/upload          (kirim blob WAV)
    2. POST /gradio_api/call/ingest_clip (data: [path, durasi])

Kalau protokol ini tidak jalan, mode dengar terus TIDAK PERNAH memproses apa
pun, seberapa pun bagusnya deteksi suara di browser. Karena itu uji ini ada.

Jalankan:  .venv\\Scripts\\python.exe test_ingest_endpoint.py
"""
from __future__ import annotations

import json
import os
import struct
import sys
import time
import wave
from pathlib import Path

import requests

import app
import config
import database as db

PORT = int(os.environ.get("GARUDA_TEST_PORT", "7871"))
BASE = f"http://127.0.0.1:{PORT}"

_hasil: list[bool] = []


def check(nama: str, ok: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {nama}" + (f" — {detail}" if detail else ""))
    _hasil.append(ok)
    return ok


def buat_wav(path: Path, detik: float = 2.0, sr: int = 16000) -> Path:
    """WAV 16-bit mono berisi nada sederhana - meniru keluaran keWav() browser."""
    n = int(sr * detik)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        bingkai = bytearray()
        for i in range(n):
            # gelombang kotak pelan supaya bukan digital silence
            nilai = 3000 if (i // 80) % 2 else -3000
            bingkai += struct.pack("<h", nilai)
        w.writeframes(bytes(bingkai))
    return path


def tunggu_server(timeout: float = 120.0) -> bool:
    batas = time.time() + timeout
    while time.time() < batas:
        try:
            r = requests.get(BASE + "/", timeout=3)
            if r.status_code == 200:
                return True
        except requests.RequestException:
            pass
        time.sleep(0.5)
    return False


def unggah(wav: Path) -> str | None:
    """Tiru persis POST /gradio_api/upload dari skrip browser."""
    with open(wav, "rb") as f:
        r = requests.post(BASE + "/gradio_api/upload",
                          files={"files": (wav.name, f, "audio/wav")}, timeout=60)
    if r.status_code != 200:
        print(f"    (unggah HTTP {r.status_code}: {r.text[:200]})")
        return None
    data = r.json()
    return data[0] if data else None


def panggil(path: str, durasi: float) -> tuple[bool, str]:
    """Tiru persis POST /gradio_api/call/ingest_clip.

    Gradio 6 menjawab dengan {"event_id": ...} lalu hasilnya diambil lewat
    GET /gradio_api/call/ingest_clip/<event_id> sebagai SSE.
    """
    r = requests.post(BASE + "/gradio_api/call/ingest_clip",
                      json={"data": [path, durasi]}, timeout=120)
    if r.status_code != 200:
        return False, f"POST HTTP {r.status_code}: {r.text[:300]}"
    ev = r.json()
    eid = ev.get("event_id")
    if not eid:
        return False, f"tidak ada event_id: {ev}"

    hasil = requests.get(f"{BASE}/gradio_api/call/ingest_clip/{eid}",
                         timeout=300, stream=True)
    if hasil.status_code != 200:
        return False, f"GET HTTP {hasil.status_code}: {hasil.text[:300]}"

    akhir = ""
    for baris in hasil.iter_lines(decode_unicode=True):
        if not baris:
            continue
        if baris.startswith("data:"):
            isi = baris[5:].strip()
            if isi:
                akhir = isi
        elif baris.startswith("event:") and "error" in baris:
            return False, f"SSE error: {baris}"
    return True, akhir


def baris_antrian() -> list:
    return db.loop_feed(limit=30)


def run() -> int:
    print("=" * 70)
    print("Uji endpoint ingest_clip (protokol browser -> server)")
    print("=" * 70)

    db.init_db()
    database_awal = len(baris_antrian())

    # --- 1. Klip ditolak saat mode nonaktif -------------------------------
    demo = app.build_ui()
    demo.queue(default_concurrency_limit=2)
    demo.launch(server_name="127.0.0.1", server_port=PORT, prevent_thread_lock=True,
                quiet=True, show_error=True,
                allowed_paths=[str(config.EXPORT_DIR), str(config.SAMPLES_DIR),
                               str(config.VAD_DIR)])

    try:
        if not check("server menyala", tunggu_server()):
            return 1

        # Pastikan daftar API benar-benar memuat ingest_clip.
        info = requests.get(BASE + "/gradio_api/info", timeout=30).json()
        nama_api = list((info.get("named_endpoints") or {}).keys())
        check("endpoint /ingest_clip terdaftar",
              "/ingest_clip" in nama_api, f"{len(nama_api)} endpoint")

        wav = buat_wav(config.SAMPLES_DIR / "_uji_ingest.wav" if config.SAMPLES_DIR.exists()
                       else Path("_uji_ingest.wav"), detik=2.0)

        # --- 2. Mode NONAKTIF: klip harus diabaikan -----------------------
        app._LISTENING.clear()
        jalur = unggah(wav)
        if check("unggah WAV berhasil", bool(jalur) and Path(jalur).is_file(),
                 str(jalur)[:80] if jalur else "gagal unggah"):
            ok, pesan = panggil(jalur, 2.0)
            check("panggilan ingest_clip dijawab server", ok, pesan[:160])
            check("klip DITOLAK saat mode nonaktif",
                  "nonaktif" in pesan.lower() or "diabaikan" in pesan.lower(), pesan[:160])
            check("antrian tidak bertambah saat mode nonaktif",
                  len(baris_antrian()) == database_awal)

        # --- 3. Mode AKTIF: klip harus masuk antrian ----------------------
        app._LISTENING.set()
        import loop
        loop.start_worker(config.load_config())
        try:
            jalur = unggah(wav)
            ok, pesan = panggil(jalur, 2.0)
            check("panggilan ingest_clip diterima", ok, pesan[:160])
            check("server melaporkan klip masuk antrian",
                  "antrian" in pesan.lower(), pesan[:160])

            # Tunggu pekerja memproses (Whisper bisa lambat di CPU).
            masuk = False
            batas = time.time() + 600
            while time.time() < batas:
                baris = baris_antrian()
                if len(baris) > database_awal:
                    masuk = True
                    break
                time.sleep(2.0)
            check("baris antrian muncul di database", masuk,
                  f"{len(baris_antrian())} baris")

            # --- 4. Klip terlalu pendek ditolak --------------------------
            pendek = buat_wav(Path(wav).with_name("_uji_pendek.wav"), detik=0.5)
            jalur2 = unggah(pendek)
            ok2, pesan2 = panggil(jalur2, 0.5)
            check("klip 0.5s ditolak (terlalu pendek)",
                  "pendek" in pesan2.lower() or "derau" in pesan2.lower(), pesan2[:160])
        finally:
            app._LISTENING.clear()
            loop.stop_worker(timeout=30)

        # --- 5. Durasi dari browser dipakai, bukan hasil probe server -----
        # Berkasnya ~2 detik, tapi kita kirim 1.9. Pesan server HARUS memuat
        # angka yang dikirim browser, bukan hasil probe berkas.
        app._LISTENING.set()
        loop.start_worker(config.load_config())
        try:
            jalur3 = unggah(wav)
            ok3, pesan3 = panggil(jalur3, 1.9)
            check("server memakai durasi kiriman browser (1.9), bukan hasil probe",
                  ok3 and "1.9" in pesan3, pesan3[:160])
        finally:
            app._LISTENING.clear()
            loop.stop_worker(timeout=30)

        # --- 6. Path palsu / bukan audio ditolak -------------------------
        app._LISTENING.set()
        loop.start_worker(config.load_config())
        try:
            ok4, pesan4 = panggil(str(Path("tidak_ada.wav").resolve()), 3.0)
            check("path tidak ada ditolak",
                  ok4 and ("tidak sah" in pesan4.lower() or "ditolak" in pesan4.lower()),
                  pesan4[:160])
            ok5, pesan5 = panggil(str(Path("app.py").resolve()), 3.0)
            check("berkas bukan audio ditolak",
                  ok5 and ("tidak sah" in pesan5.lower() or "ditolak" in pesan5.lower()),
                  pesan5[:160])
        finally:
            app._LISTENING.clear()
            loop.stop_worker(timeout=30)

    finally:
        try:
            demo.close()
        except Exception:
            pass
        for sisa in ("_uji_ingest.wav", "_uji_pendek.wav"):
            for kandidat in (Path(sisa), config.SAMPLES_DIR / sisa):
                try:
                    if kandidat.is_file():
                        kandidat.unlink()
                except OSError:
                    pass

    hits = sum(_hasil)
    print(f"\nHASIL: {hits}/{len(_hasil)} PASS")
    return 0 if hits == len(_hasil) else 1


if __name__ == "__main__":
    sys.exit(run())
