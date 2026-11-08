# AGENTS.md — Bootstrap untuk agent berikutnya

> **Baca file ini dulu (≈2 menit), bukan README.md dari baris 0.**
> README.md = dokumentasi untuk manusia. File ini = konteks ringkas untuk agent.
> Path proyek: `D:\Project\Python\TniWhisper\garuda-radio-ai` (Windows, PowerShell).

---

## 1. Apa proyek ini

**GARUDA Radio AI** — app desktop lokal & offline: audio radio → **faster-whisper**
(transkripsi ID) → **Qwen2.5 1.5B/3B GGUF via llama.cpp** → JSON terstruktur →
**validasi Python** → aksi otomatis ke SQLite → UI Gradio (6 tab).

**Batasan yang tidak boleh dilanggar (aturan produk, bukan preferensi):**
- Semua lokasi/unit/skenario = **DEMO DATA** (fiktif). Jangan tambah data dunia nyata.
- LLM **hanya mengusulkan** JSON. Eksekusi tindakan selalu di `actions.py` (Python).
- Tidak ada eksekusi perintah sistem / shell / jaringan oleh AI.
- Tidak ada API cloud, tidak ada login/multi-user, tidak ada download model diam-diam.
- Tindakan berdampak (tiket, alert, map update, status) **menunggu persetujuan operator**.
- AI tidak boleh mengarang koordinat: lokasi hanya dari `config.DEMO_LOCATIONS`, selain itu `null` + `missing_information`.

---

## 2. Peta file (13 modul inti)

| File | Isi | Kapan disentuh |
|---|---|---|
| `config.py` | path, model, `DEMO_LOCATIONS`, `DEFAULTS`, `load/save_config`, `device_info()` | ubah lokasi/model/default |
| `database.py` | skema SQLite 8 tabel + semua query (`db.*`), `_LOCK = RLock` | ubah skema/query |
| `audio.py` | konversi ke 16 kHz mono + faster-whisper | ubah transkripsi |
| `ai.py` | prompt, `CATEGORIES/PRIORITIES/ACTIONS`, `analyze()`, `validate()`, `rule_based()`, `AnalysisResult` | ubah prompt/output LLM |
| `actions.py` | action engine: `process_audio_result()`, anti-duplikasi tiket/map, tulis ke DB | ubah aturan aksi |
| `pipeline.py` | `process_audio()` / `process_text()` — **satu-satunya jalur** dipakai UI & test | entry baru |
| `loop.py` | antrian klip mode dengar-terus + thread pekerja; tulis transisi ke `events_log` | ubah antrian/ingest |
| `map_view.py` | Folium + fallback SVG offline | ubah peta |
| `reports.py` | rekap + ekspor CSV/PDF | ubah rekap |
| `app.py` | UI Gradio satu halaman `build_ui()` (6 tab), handler `do_*`, `_refresh_all()` | ubah UI |
| `setup_models.py` | unduh & cek model (`status`, `whisper-small`, `llm-3b`, `tts-id`) | ubah model |
| `samples_gen.py` | generator audio TTS Piper offline + data demo | ubah sampel |
| `test_scenarios.py` | 47 pemeriksaan end-to-end (audio TTS nyata, reset DB) | verifikasi |
| `smoke_ui.py` | uji server UI HTTP 200 | verifikasi |

Data runtime: `data/garuda.db`, `data/audio/`, `data/exports/`, `data/config.json`.
Model: `models/whisper/<size>/`, `models/llm/*.gguf`, `models/tts/`. **Jangan commit/hapus isi `models/`.**

---

## 3. Alur kode (jejak paling penting)

```
app.py handler  →  pipeline.process_audio(path, cfg, source)
                       ├─ audio.transcribe()          → dict(text, language, duration, avg_logprob, ...)
                       ├─ ai.analyze(text, cfg)       → AnalysisResult(dict)  ← LLM JSON + validate()
                       └─ actions.process_audio_result(path, trans, analysis, cfg, source)
                              ├─ SHA-256 audio → cek duplikat di tabel transcripts  (audio identik = stop)
                              ├─ db.add_transcript / add_report
                              └─ switch action: CREATE_TICKET | UPDATE_STATUS | CREATE_ALERT | UPDATE_MAP | NONE
```

**Bila LLM tidak tersedia / JSON rusak:** `analyze()` tidak crash. Laporan tetap
tersimpan sebagai `category=UNKNOWN`, `llm_valid=0`, aksi `NONE`, tanpa tindakan apa pun.
Pertahankan perilaku ini — transkrip tidak boleh hilang.

Kategori: `LOGISTICS, MEDICAL, EQUIPMENT, INFRASTRUCTURE, STATUS_UPDATE, GENERAL, UNKNOWN`
Prioritas: `LOW, MEDIUM, HIGH, CRITICAL`
Aksi: `CREATE_REPORT, CREATE_TICKET, UPDATE_STATUS, CREATE_ALERT, UPDATE_MAP, NONE`

Skema JSON yang diminta dari LLM ada di README.md §"Output JSON" — jangan ubah tanpa
memperbarui `ai.AnalysisModel` (Pydantic) **dan** `ai.validate()` **dan** fixture test.

---

## 4. Skema DB singkat (`database.py`)

- `transcripts` — `audio_hash UNIQUE` (anti-duplikasi audio), `text`, metrik transkripsi
- `reports` — hasil analisis: category/unit/summary/item/quantity/location/priority/action,
  `llm_raw` (audit), `llm_valid`, `status` ∈ `NEW|REVIEW|VERIFIED|ARCHIVED`
- `tickets` — `status` ∈ `Pending|Approved|In Progress|Completed|Rejected` (buat = **Pending**)
- `alerts` — `status` ∈ `Needs Review|Acknowledged|Resolved`
- `map_updates` — `status` ∈ `Pending Approval|Applied|Rejected`
- `events_log` — audit semua langkah, `actor` ∈ `system|operator`
- `locations` — seed dari `config.DEMO_LOCATIONS`; `settings_kv` — key/value

Perubahan skema: tambah `CREATE TABLE IF NOT EXISTS` / migrasi idempoten di `init_db()`.
DB berisi data demo — aman di-reset lewat `test_scenarios.py`.

---

## 5. Perintah

```powershell
# .venv BELUM ada di mesin ini → jalankan setup dulu (butuh internet, ±2.7 GB model)
.\setup.bat
.\run.bat                                        # http://127.0.0.1:7860

# Bila .venv sudah ada:
.\.venv\Scripts\python.exe setup_models.py status
.\.venv\Scripts\python.exe test_scenarios.py     # reset DB + 47 pemeriksaan (baseline: 47/47 PASS)
.\.venv\Scripts\python.exe smoke_ui.py           # baseline: SMOKE PASS (HTTP 200)
```

Tanpa `.venv`/model, uji end-to-end tidak bisa jalan. Jangan mengaku "terverifikasi"
bila hanya membaca kode — katakan terus terang bahwa belum dijalankan.

---

## 6. Konvensi kerja

1. **Jangan ubah `README.md` untuk keperluan agent.** Perbarui `AGENTS.md` bila
   arsitektur/aturan berubah. README hanya untuk dokumentasi pengguna.
2. Satu jalur pemrosesan: perubahan logika harus di `pipeline.py`/`actions.py`,
   **bukan** diduplikasi di `app.py`.
3. Komentar & string UI berbahasa Indonesia, konsisten dengan kode yang ada.
4. Style: `from __future__ import annotations`, type hint ringan, fungsi kecil,
   akses DB hanya lewat helper `db.*` (jangan bikin koneksi ad-hoc di UI).
5. Setiap aksi baru wajib: entry di `events_log`, aturan anti-duplikasi bila relevan,
   dan persetujuan operator bila berdampak.
6. Tidak ada `git` di folder ini. **Tidak ada version control** → jangan lakukan
   refactor besar/penghapusan massal tanpa konfirmasi; buat backup `.bak` bila perlu.
7. Model GGUF tidak ada di `models/llm/` saat init; `data/garuda.db` ada.
   `models/whisper/small` dan `large-v3-turbo` ada, TTS Piper ada.

## 7. Jebakan yang sudah diketahui

- `run.bat` men-set `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1` → unduhan hanya lewat `setup_models.py`.
- Emoji di `app.py` tampil rusak (`??`) di sebagian konsol Windows — kosmetik, bukan bug fungsional.
- Whisper bisa jalan di CPU walau ada GPU (CUDA tidak terdeteksi oleh `ctranslate2`).
- `llama-cpp-python` build CPU saat pengujian → analisis 3B ±15–20 s. Jangan "optimalkan"
  dengan pindah ke API cloud.
- Jangan menambah dependensi berat tanpa memperbarui `requirements.txt` + langkah `setup.bat`.

---

## 7b. Mode dengar terus (continuous listening)

Alur: VAD browser (`models/vad/`) memicu tombol rekam `gr.Audio` → tiap klip
masuk `loop.enqueue_clip()` → satu thread pekerja memanggil
`pipeline.process_audio(..., auto_apply=True)` → panel UI membaca `db.loop_feed()`.

**Aturan yang tidak boleh dilanggar:**
- `config.AUTO_APPLY_ACTIONS = {"CREATE_ALERT"}` — **hanya** alert yang langsung berlaku.
  `CREATE_TICKET` tetap `Pending`, `UPDATE_MAP` tetap `Pending Approval`,
  `UPDATE_STATUS` tetap usulan. Jangan tambah entri tanpa persetujuan manusia.
- `auto_apply` default `False` di semua fungsi. Jalur manual tidak boleh berubah.
- Panel antrian dibaca dari DB, bukan dari state memori di `loop.py`.
- Aset VAD diunduh **hanya** lewat `setup_models.py vad`, tidak pernah saat runtime.
  Tanpa aset, mode tetap menyala tapi tanpa deteksi otomatis (rekam manual).

**Perintah terkait:**
```powershell
.\.venv\Scripts\python.exe setup_models.py vad   # unduh aset VAD (butuh internet, sekali)
.\.venv\Scripts\python.exe test_live.py          # 19 pemeriksaan mode dengar terus
```

**Hal-hal yang mudah dirusak saat mengubah `loop.py`:**
- `stop_worker()` memakai `threading.Event` per-thread, **bukan** sentinel `None` di
  antrian. Sentinel memakan satu slot dan bisa membuat klip berikutnya yatim
  (terutama saat antrian penuh).
- Setiap klip yang dipotong karena antrian penuh **wajib** dicatat `discarded`
  ke `events_log`; kalau tidak, operator kehilangan jejak transmisi yang hilang.
- `stop_recording` dan tombol Mulai/Hentikan memakai `concurrency_id` terpisah
  (`garuda_ingest`, `garuda_listen`). Jangan biarkan ingest berbagi antrian default
  dengan timer/LLM — kalau tersendat, loop mati diam-diam.
- Baris `processing` tanpa penutup ditampilkan sebagai **Terputus** (aplikasi mati
  di tengah proses). Pemasangannya kronologis per file, bukan berdasarkan id saja.

**Belum terverifikasi:** deteksi suara sungguhan di browser (butuh mic manusia).
Yang sudah terbukti: halaman memuat skrip VAD, kelima aset HTTP 200, dan alur
klip → antrian → aksi lewat handler langsung.

---

## 8. Rencana singkat untuk agent baru

1. Baca file ini (selesai) → `config.py` dan `pipeline.py` (≈130 baris) → baru `actions.py` / `ai.py` sesuai kebutuhan.
2. Buka `README.md` hanya bagian relevan (JSON schema, tabel aksi, tab UI) — jangan baca penuh.
3. Untuk bug: reproduksi via `test_scenarios.py` atau `pipeline.process_text()` (tanpa audio).
4. Untuk fitur: ikuti jalur `ai` → `actions` → `db` → `app`, plus test di skenario yang sama.
5. Selesai: jalankan `test_scenarios.py` + `smoke_ui.py` dan laporkan hasil aktual.
