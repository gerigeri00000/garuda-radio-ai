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

Alur: **satu stream mikrofon** yang tetap terbuka selama mode aktif → VAD memotong
satu transmisi setelah sunyi `config.SILENCE_SECONDS` (3 s) → browser mengubah
`Float32Array` jadi WAV → `POST /gradio_api/upload` → `POST /gradio_api/call/ingest_clip`
dengan `data: [path, durasi]` → `app.do_ingest_clip()` → `loop.enqueue_clip()` → satu
thread pekerja memanggil `pipeline.process_audio(..., auto_apply=True)` → panel UI
membaca `db.loop_feed()`.

**Operator hanya menekan Mulai sekali.** Tidak ada tombol record/stop, tidak ada
"Proses audio" di jalur ini. `gr.Audio` untuk mikrofon sudah **dihapus** dari UI;
`audio_in` hanya `sources=["upload"]` untuk file lama, di dalam Accordion terpisah.
Jangan pernah kembali ke pola "VAD mengklik tombol rekam Gradio" — pola itu membuat
tombol berkedip, threshold sunyi tidak bisa diatur, dan terasa masih manual.

**Aturan yang tidak boleh dilanggar:**
- `config.AUTO_APPLY_ACTIONS = {"CREATE_ALERT"}` — **hanya** alert yang langsung berlaku.
  `CREATE_TICKET` tetap `Pending`, `UPDATE_MAP` tetap `Pending Approval`,
  `UPDATE_STATUS` tetap usulan. Jangan tambah entri tanpa persetujuan manusia.
  `UPDATE_STATUS` sengaja tidak di-auto-apply: `_match_activity` memakai ambang
  kemiripan 0.7, terlalu lemah untuk menutup tiket otomatis.
- `auto_apply` default `False` di semua fungsi. Jalur manual tidak boleh berubah.
- Panel antrian dibaca dari DB, bukan dari state memori di `loop.py`.
- `do_ingest_clip()` **wajib** menolak klip bila `_LISTENING` belum diset. Endpoint ini
  terbuka lewat HTTP; tanpa penjagaan itu siapa pun bisa memicu alert otomatis.
  Ia juga **wajib** memvalidasi path (harus berkas nyata berekstensi audio) dan
  memakai durasi kiriman browser bila ada, karena WAV dibuat di sisi klien.
- Aset VAD diunduh **hanya** lewat `setup_models.py vad`, tidak pernah saat runtime.
  Tanpa aset, mode tetap menyala tapi tanpa deteksi otomatis; operator masih bisa
  memakai panel upload manual.

**Perintah terkait:**
```powershell
.\.venv\Scripts\python.exe setup_models.py vad        # unduh aset VAD (butuh internet, sekali)
.\.venv\Scripts\python.exe test_live.py               # 29 pemeriksaan mode dengar terus
.\.venv\Scripts\python.exe test_ingest_endpoint.py    # 13 pemeriksaan protokol browser->server
```

`test_ingest_endpoint.py` menyalakan server Gradio sungguhan lalu meniru persis dua
panggilan HTTP browser (upload + `call/ingest_clip`, termasuk pembacaan SSE). Uji ini
yang membuktikan potongan audio benar-benar sampai ke antrian — tanpa itu, deteksi
suara sebaik apa pun tidak akan pernah memproses apa pun.

**Hal-hal yang mudah dirusak saat mengubah `loop.py`:**
- `stop_worker()` memakai `threading.Event` per-thread, **bukan** sentinel `None` di
  antrian. Sentinel memakan satu slot dan bisa membuat klip berikutnya yatim
  (terutama saat antrian penuh). Ia mengembalikan `{"stopped": bool, "message": str}`
  — bila pemrosesan masih jalan, UI **wajib** bilang "masih menyelesaikan klip",
  bukan "nonaktif". Worker yang belum berhenti dikembalikan ke registry supaya
  Start berikutnya tidak menciptakan worker kedua.
- Setiap klip yang dipotong karena antrian penuh **wajib** dicatat `discarded`
  ke `events_log`; kalau tidak, operator kehilangan jejak transmisi yang hilang.
- `ingest_clip` dan tombol Mulai/Hentikan memakai `concurrency_id` terpisah
  (`garuda_ingest`, `garuda_listen`). Jangan biarkan ingest berbagi antrian default
  dengan timer/LLM — kalau tersendat, loop mati diam-diam.
- `loop_feed()` membaca 4x lebih banyak baris daripada yang ditampilkan saat
  memasangkan `processing` dengan penutupnya, lalu hanya mengembalikan `limit`
  baris teratas. Baris `processing` tanpa penutup baru disebut **Terputus**
  setelah `grace_seconds` (default 120 s) — kalau tidak, klip yang sedang
  dikerjakan akan tampil "Terputus" padahal baik-baik saja.

**Hal-hal yang mudah dirusak saat mengubah VAD (`app.py` → `VAD_BOOT_JS`):**
- vad-web 0.0.9 adalah **UMD**, bukan ES module. `import()` tidak akan pernah
  menghasilkan `MicVAD`; ia hanya ada sebagai `window.vad` setelah `<script>`.
- **`ort.min.js` wajib dimuat lebih dulu.** Tanpa `window.ort`, `window.vad` tidak
  pernah terbentuk dan deteksi otomatis mati tanpa pesan. Urutan benar:
  `ort.min.js` → `vad-web.bundle.min.js` → skrip GARUDA. Ketiganya dimuat lewat
  `launch(head=...)`, **bukan** `gr.HTML` — Gradio menyaring `<script>` di `gr.HTML`.
- v0.0.9 **tidak punya** opsi `baseAssetPath`/`onnxWASMBasePath`. Aset
  (`silero_vad.onnx`, `vad.worklet.bundle.min.js`) dicari relatif ke `src` tag
  `<script>` vad-web, jadi keenam aset **harus** berada di `models/vad/`.
- Hanya `ort.env.wasm.wasmPaths` yang bisa diarahkan manual.
- **Sunyi 3 detik diatur lewat `redemptionFrames`, bukan konstanta detik.** Pustaka
  memakai `frameSamples: 1536` pada 16 kHz, jadi satu frame = 0,096 s dan
  `redemptionFrames` dihitung `round(SILENCE_SECONDS * 16000 / 1536)` = 31 frame
  (2,98 s). Default pustaka hanya 8 frame (~0,77 s). Jangan ganti `frameSamples`
  tanpa menghitung ulang; pustaka hanya menerima 512/1024/1536.
- `onSpeechEnd(audio)` menerima **`Float32Array` 16 kHz**, bukan Blob/file. `keWav()`
  yang mengubahnya jadi WAV 16-bit mono. `detik = audio.length / 16000` dikirim
  bersama path supaya server tidak perlu memprobe ulang.
- VAD baru menyala setelah operator menekan **Mulai** (`js=` pada `click`),
  karena membuka mikrofon memerlukan izin eksplisit pengguna. `js=` mengembalikan
  `"status|info"` ke `vad_state`, lalu `vad_report()` menampilkannya ke `live_status`
  lewat `vad_timer.tick(..., js=...)` yang membaca `__GARUDA_VAD_STATUS__` /
  `__GARUDA_VAD_INFO__` setiap 2 detik.
  **Jangan hapus jalur pelaporan ini** — tanpa itu operator melihat "AKTIF"
  padahal tidak ada suara yang pernah terdeteksi.
- Saat Hentikan, ambil `v.stream` untuk `getTracks().stop()`.
  `v.listening` adalah **boolean**, bukan MediaStream — memakainya membuat
  mikrofon tidak pernah dilepas (bug nyata yang pernah terjadi).
- `ort-wasm-simd.wasm` ikut diunduh sebagai cadangan untuk halaman tanpa
  cross-origin isolation (tanpa `SharedArrayBuffer`).

**Belum terverifikasi:** deteksi suara sungguhan di browser (butuh mic manusia) dan
apakah `redemptionFrames: 31` memotong transmisi pada jeda bicara normal — jeda
napas >3 s akan memecah satu kalimat jadi dua klip. Yang sudah terbukti: rantai
`ort.min.js` → `vad-web.bundle.min.js` menghasilkan `window.vad.MicVAD` berupa
fungsi, keenam aset HTTP 200, substitusi skrip benar, `audio_in` hanya `upload`,
nol handler `stop_recording`, dan protokol browser→antrian lulus 13/13
(`test_ingest_endpoint.py`).

---

## 8. Rencana singkat untuk agent baru

1. Baca file ini (selesai) → `config.py` dan `pipeline.py` (≈130 baris) → baru `actions.py` / `ai.py` sesuai kebutuhan.
2. Buka `README.md` hanya bagian relevan (JSON schema, tabel aksi, tab UI) — jangan baca penuh.
3. Untuk bug: reproduksi via `test_scenarios.py` atau `pipeline.process_text()` (tanpa audio).
4. Untuk fitur: ikuti jalur `ai` → `actions` → `db` → `app`, plus test di skenario yang sama.
5. Selesai: jalankan `test_scenarios.py` + `smoke_ui.py` dan laporkan hasil aktual.
