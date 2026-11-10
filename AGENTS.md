# AGENTS.md — Bootstrap untuk agent berikutnya

> **Baca file ini dulu (≈2 menit), bukan README.md dari baris 0.**
> README.md = dokumentasi untuk manusia. File ini = konteks ringkas untuk agent.
> Path proyek: `D:\Project\Python\TniWhisper\garuda-radio-ai` (Windows, PowerShell).
> Juga berjalan di **macOS Apple Silicon** (setup.sh/run.sh) — lihat §7c.

---

## 1. Apa proyek ini

**GARUDA Radio AI** — app desktop lokal & offline: audio radio → **faster-whisper**
(atau **mlx-whisper** di GPU Mac Apple Silicon) (transkripsi ID) →
**Qwen2.5 1.5B/3B GGUF via llama.cpp** (CUDA / Metal / CPU) → JSON terstruktur →
**validasi Python** → aksi otomatis ke SQLite → UI Gradio (5 tab).

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
| `config.py` | path, model, `DEMO_LOCATIONS`, `DEFAULTS`, `load/save_config`, `device_info()`, deteksi platform (`IS_APPLE_SILICON`, `perf_cores()`, `SETUP_CMD`, `VENV_PY`) | ubah lokasi/model/default |
| `database.py` | skema SQLite 8 tabel + semua query (`db.*`), `_LOCK = RLock` | ubah skema/query |
| `audio.py` | konversi ke 16 kHz mono + `whisper_backend()` memilih faster-whisper atau mlx-whisper (Mac), cadangan CPU bila MLX gagal, `warmup()` | ubah transkripsi |
| `ai.py` | prompt, `CATEGORIES/PRIORITIES/ACTIONS`, `analyze()`, `validate()`, `rule_based()`, `AnalysisResult` | ubah prompt/output LLM |
| `actions.py` | action engine: `process_audio_result()`, anti-duplikasi tiket/map, tulis ke DB | ubah aturan aksi |
| `pipeline.py` | `process_audio()` / `process_text()` — **satu-satunya jalur** dipakai UI & test | entry baru |
| `loop.py` | antrian klip mode dengar-terus + thread pekerja; tulis transisi ke `events_log` | ubah antrian/ingest |
| `map_view.py` | Folium + fallback SVG offline | ubah peta |
| `reports.py` | rekap + ekspor CSV/PDF | ubah rekap |
| `app.py` | UI Gradio satu halaman `build_ui()` (5 tab), handler `do_*`, `_refresh_all()` | ubah UI |
| `setup_models.py` | unduh & cek model (`status`, `whisper-small`, `whisper-mlx-<size>`, `llm-3b`, `tts-id`, `vad`) | ubah model |
| `samples_gen.py` | generator audio TTS Piper offline + data demo | ubah sampel |
| `test_scenarios.py` | 47 pemeriksaan end-to-end (audio TTS nyata, reset DB) | verifikasi |
| `test_platform.py` | 42 pemeriksaan tanpa model unduhan: pemilihan mesin Whisper + cadangan CPU, mode JSON cepat, pengaman template, fd/berkas sementara, config, MLX lintas thread | verifikasi |
| `smoke_ui.py` | uji server UI HTTP 200 | verifikasi |
| `setup.sh` / `run.sh` / `requirements-mac.txt` | padanan setup.bat/run.bat untuk macOS (MLX + Metal) | ubah instalasi Mac |

Data runtime: `data/garuda.db`, `data/audio/`, `data/exports/`, `data/config.json`.
Model: `models/whisper/<size>/`, `models/whisper-mlx/<size>/` (Mac), `models/llm/*.gguf`, `models/tts/`. **Jangan commit/hapus isi `models/`.**

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
.\.venv\Scripts\python.exe test_platform.py      # 42 pemeriksaan tanpa model unduhan (cepat)
```

macOS (Apple Silicon):

```bash
./setup.sh                                       # venv + MLX + llama.cpp Metal + model (±4,3 GB)
./run.sh                                         # http://127.0.0.1:7860 (offline, caffeinate)
.venv/bin/python setup_models.py status
.venv/bin/python test_scenarios.py               # baseline Mac mini M4 Pro: 47/47 PASS
.venv/bin/python test_platform.py                # baseline: 42/42 PASS
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

### 7a. UI: pilih baris, bukan ketik ID

Struktur tab final (**5**): `🎙 Monitor` · `✅ Tiket` · `🗺 Peta` · `📑 Rekap` · `⚙ Pengaturan`.

Dua tab lama sudah dihapus dan digabung: **Dashboard** (KPI + banner "persetujuan
operator" pindah ke atas tab Monitor) dan **Riwayat & Tiket** (riwayat laporan +
peringatan pindah ke tab Tiket).

**Tidak ada lagi kotak "ketik ID".** Semua aksi pada tiket, pembaruan peta, dan
peringatan bekerja lewat **klik baris**:

```python
df.select(saat_baris_tiket_diklik, None, [ringkas_pilih, tiket_dipilih,
                                          btn_approve, btn_decline, btn_hapus])
```

- `evt.row_value` berisi **seluruh isi baris** (terverifikasi di Gradio 6.31):
  contoh `[22, 'LOGISTICS', 'air', 'Approved']`. Ambil ID lewat indeks kolom
  (`_ID_IX`), lalu `db.get_ticket(id)` untuk data segar.
- Baris terpilih disimpan di `gr.State(int | None)` (`tiket_dipilih`,
  `map_upd_id`, `alert_dipilih`); aksi memakai state itu sebagai input.
- `_tombol_untuk(status)` mengembalikan `gr.update(visible=...)` supaya **hanya
  tombol yang berlaku** yang tampil: Approve hanya untuk `Pending`, Decline untuk
  `Pending`/`Approved`, **Hapus hanya untuk `Rejected`**.
- Setiap aksi mengosongkan kembali pilihan (`_setelah_aksi`, `do_filter_tiket`,
  `do_reset_filter_tiket`) agar tombol tidak mengenai baris yang statusnya sudah
  berubah.
- Hapus permanen = konfirmasi **dua langkah di server** lewat `_HAPUS_ARM`
  (klik 1 minta konfirmasi, klik 2 dengan ID sama baru menghapus). Jangan ganti
  dengan `confirm()` browser.

**Aturan uji browser (penting):** isi `gr.Dataframe` **tidak boleh** dibaca dengan
scraping `<tbody>`/teks baris — tabel Gradio memakai grid virtual, sehingga
`<tbody>` sering hanya berisi satu baris kosong atau baris yang belum ter-render.
Klik/verifikasi harus menyasar `td` / `[role='gridcell']` yang berisi teks, dan
**dibatasi ke tab aktif** (`:visible`) karena sel di tab tersembunyi tidak bisa diklik.

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
  Ia juga **wajib** memastikan berkas berada di folder unggahan Gradio
  (`_berkas_unggahan_sah`), bukan path sembarang di disk. Tanpa batas folder ini,
  siapa pun yang bisa menjangkau port aplikasi selagi mode menyala dapat memutar
  ulang rekaman lama di disk dan memicu alert otomatis dari situ. Durasi kiriman
  browser dipakai bila masuk akal; nilai karangan (di luar 0–600 s, atau jauh
  melebihi isi berkas) jatuh kembali ke hasil probe.
- Aset VAD diunduh **hanya** lewat `setup_models.py vad`, tidak pernah saat runtime.
  Tanpa aset, mode tetap menyala tapi tanpa deteksi otomatis; operator masih bisa
  memakai panel upload manual.

**Perintah terkait:**
```powershell
.\.venv\Scripts\python.exe setup_models.py vad        # unduh aset VAD (butuh internet, sekali)
.\.venv\Scripts\python.exe test_live.py               # 32 pemeriksaan mode dengar terus
.\.venv\Scripts\python.exe test_ingest_endpoint.py    # 13 pemeriksaan protokol browser->server
node test_vad_gerbang.js                              # 14 pemeriksaan gerbang sunyi (dipanggil test_live.py)
```

Dua uji ini menutup dua celah berbeda:

- `test_ingest_endpoint.py` menyalakan server Gradio sungguhan lalu melakukan
  panggilan HTTP yang sama dengan browser (upload → `call/ingest_clip` → baca SSE).
  Ia membuktikan potongan audio benar-benar sampai ke antrian. Ia **tidak**
  menjalankan kode browser, jadi tidak bisa menangkap regresi di `VAD_BOOT_JS`.
- `test_vad_gerbang.js` menjalankan `VAD_BOOT_JS` hasil substitusi terhadap
  `models/vad/vad-web.bundle.min.js` **asli**, dengan probabilitas bicara buatan.
  Ini satu-satunya uji yang menangkap: desain lama (VAD mengklik tombol rekam),
  sunyi yang menghasilkan klip, transmisi pendek yang hilang/tergabung salah,
  dan mikrofon yang bocor saat Hentikan atau saat Mulai ditekan dua kali.

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
- **Jangan atur "sunyi 3 detik" lewat `redemptionFrames`.** `redemptionFrames` adalah
  *penduga akhir bicara*, dan pustaka memasukkan seluruh frame sunyi ke dalam
  `audioBuffer` sebelum menutup segmen. Bila disetel 31 (≈3 s), aksiomatiknya jadi:
  transmisi 0,5 s menghasilkan klip 3,5 s (sunyi mati menempel), dan dua transmisi
  berjeda <3 s **digabung jadi satu klip raksasa** — sudah dibuktikan dengan bundle
  asli. Pakai `REDEMPTION_FRAMES = 8` (≈0,77 s) supaya akhir bicara responsif,
  lalu terapkan gerbang sunyi sendiri: `SILENCE_FRAMES` dihitung dari
  `SILENCE_SECONDS / FRAME_SEC` (±31 frame) dan diuji di `onFrameProcessed`.
  Segmen yang datang sebelum gerbang lepas digabung — itu memang yang diinginkan
  (satu kalimat radio utuh).
- `frameSamples: 1536` pada 16 kHz = 0,096 s per frame. Pustaka hanya menerima
  512/1024/1536; jangan ganti tanpa menghitung ulang `FRAME_SEC`.
- `onFrameProcessed(probs)` menerima objek `{notSpeech, isSpeech}`, dipanggil untuk
  **setiap** frame (termasuk frame yang menutup segmen). Ini satu-satunya sumber
  kebenaran soal sunyi — jangan pakai timer/jam dinding, karena hitungannya
  meleset saat tab di latar atau thread sibuk.
- `onSpeechEnd(audio)` menerima **`Float32Array` 16 kHz** yang ujungnya masih
  mengandung ±8 frame sunyi. `lepasGerbang()` memangkas ekor itu sebelum
  `keWav()` mengubahnya jadi WAV 16-bit mono.
- `bereskan()` menerima **instans `MicVAD`**, bukan pembungkus `{vad}`. Memanggil
  `bereskan(state)` membuat `.stream` tidak ketemu dan mikrofon tidak pernah
  dilepas (bug nyata, tertangkap `test_vad_gerbang.js`).
- Mulai/Hentikan memakai `pending` + `generasi`: klik ganda tidak membuka dua
  stream mikrofon, dan Hentikan saat masih "menyiapkan" melepas mikrofon yang
  terlanjur terbuka tanpa mengklaim "aktif".
- VAD baru menyala setelah operator menekan **Mulai** (`js=` pada `click`),
  karena membuka mikrofon memerlukan izin eksplisit pengguna. `js=` mengembalikan
  `"status|info"` ke `vad_state`, lalu `vad_report()` menampilkannya ke `live_status`
  lewat `vad_timer.tick(..., js=...)` yang membaca `__GARUDA_VAD_STATUS__` /
  `__GARUDA_VAD_INFO__` setiap 2 detik.
  **Jangan hapus jalur pelaporan ini** — tanpa itu operator melihat "AKTIF"
  padahal tidak ada suara yang pernah terdeteksi.
- **`js=` WAJIB mengembalikan LIST sepanjang `outputs`** (atau `Update`), bukan
  satu nilai. Fungsi event juga tidak boleh mengembalikan objek komponen
  (`gr.Textbox(...)`) — kembalikan nilainya saja.
  Bug nyata: `js=` mengembalikan satu string, Gradio 6 memperlakukannya sebagai
  objek **masukan**, klik gagal dengan `Parameter \`0\` is not a valid keyword
  argument`, fungsi server tidak pernah jalan, dan operator hanya melihat status
  "nonaktif" + tulisan "Error" tanpa penjelasan apa pun. Dijaga oleh
  `test_js_tombol_mengembalikan_list` dan
  `test_tombol_mulai_tidak_mengembalikan_komponen` (uji statis, tidak butuh browser).
- `live_status` memakai `elem_id="live_status"` supaya bisa diperiksa otomatis
  dari browser (dipakai uji klik tombol sungguhan).
- `kirim()` **wajib** membaca SSE hasil (`bacaHasil`). Tanpa itu, penolakan server
  (mis. mode sudah nonaktif) tidak pernah terlihat dan UI berbohong "terkirim".
- Saat Hentikan, ambil `v.stream` untuk `getTracks().stop()`.
  `v.listening` adalah **boolean**, bukan MediaStream — memakainya membuat
  mikrofon tidak pernah dilepas (bug nyata yang pernah terjadi).
- `ort-wasm-simd.wasm` ikut diunduh sebagai cadangan untuk halaman tanpa
  cross-origin isolation (tanpa `SharedArrayBuffer`).

**Belum terverifikasi:** deteksi suara sungguhan dengan mikrofon manusia di browser
nyata — termasuk apakah ambang bawaan Silero (0,5/0,35) cukup untuk radio yang
berderau. Yang sudah terbukti dengan bundle asli: sunyi tidak menghasilkan klip,
transmisi pendek tidak hilang, jeda pendek digabung, jeda ≥3 s memisah klip,
mikrofon dilepas saat Hentikan, dan klik ganda tidak membuka dua stream.
Rantai `ort.min.js` → `vad-web.bundle.min.js` menghasilkan `window.vad.MicVAD`
berupa fungsi, keenam aset HTTP 200, substitusi skrip benar, `audio_in` hanya
`upload`, nol handler `stop_recording`, dan protokol browser→antrian lulus 13/13.

---

## 7c. macOS Apple Silicon (MLX + Metal)

Prinsip: **default Windows tidak berubah.** Perilaku khusus Mac dipilih lewat
`config.IS_APPLE_SILICON` (arm64 asli, bukan Rosetta) atau ketersediaan bobot MLX.
Yang SENGAJA berlaku di semua platform (perbaikan, belum diuji di Windows):
`ai.echoes_template()` + penetral NaN/Infinity di `validate()`, `ai._INFER_LOCK`,
kode keluar `setup_models.py status` (mengikuti model aktif → peringatan run.bat kini
muncul), fd/WAV sementara di `audio.to_wav16k()` ditutup & dihapus, `save_config()`
hanya menyimpan nilai yang diubah, `load_config()` menormalkan `whisper_device` yang tidak
berlaku di platform ini ke `auto`, label mesin Whisper di UI.

- **Whisper**: `audio.whisper_backend(cfg)` memilih `mlx` bila perangkat `auto`/`mlx`,
  paket mlx-whisper + Metal ada, dan bobot `models/whisper-mlx/<size>/` terpasang;
  selain itu faster-whisper (jalur asli). `_resolve_device()` tidak pernah meneruskan
  `mlx` ke CTranslate2. Bila model hanya ada versi MLX tetapi MLX tidak dipakai
  (perangkat `cpu`, MLX/Metal tak bisa dimuat), `whisper_backend()` langsung memilih
  `_cpu_fallback_model()` (model sama atau `small`) — **pemanggil wajib memakai `be["name"]`**,
  bukan `cfg["whisper_model"]`. Bila MLX gagal saat jalan → cadangan yang sama, label
  "(cadangan: mlx gagal)". Transkrip tidak boleh hilang.
- **MLX = satu thread khusus** (`audio._on_mlx_thread`, executor 1 pekerja "garuda-mlx").
  MLX 0.32 memakai stream GPU per thread: array lazy dari thread A yang dievaluasi di
  thread B gagal "There is no Stream(gpu, N) in current thread" (bug nyata: klip sunyi
  pertama memuat model tanpa inferensi → semua klip berikutnya dari thread lain gagal).
  `_mlx_load()` juga `mx.eval(model.state)`. Jangan panggil MLX di luar `_on_mlx_thread`.
  Dijaga `test_platform.py` bagian 6 (model MLX mini acak).
- `audio.release_models()` melepas model (dipanggil `save_settings`, lalu pemanasan ulang).
- mlx-whisper **tidak punya beam search** (greedy + fallback temperatur). VAD-nya memakai
  `faster_whisper.vad` dengan `VAD_PARAMS` yang sama supaya sunyi tidak ditranskripsi
  persis seperti `vad_filter=True`. `INITIAL_PROMPT` dipakai bersama kedua mesin.
- `transcribe_seconds` = waktu inferensi saja (muat model & antre thread MLX di luar
  timer), sama seperti asli. Hasil `transcribe()` tidak lagi memuat key `wav` (berkas
  sementaranya dihapus; tidak ada pemakai).
- `config.DEFAULTS["whisper_model"]` = `large-v3-turbo` bila bobot MLX turbo + paket
  mlx-whisper ada di Apple Silicon (WER = kata salah, makin kecil makin baik: 4,9 % vs
  8,4 % small; ±0,5 s/klip), selain itu `small`.
- **LLM**: `ai._llama_kwargs()` → `n_threads = perf_cores()` (P-core; E-core memperlambat)
  dan `flash_attn=True` hanya di Apple Silicon. Windows: nilai asli.
- **`llm_fast_json`** (default hanya Apple Silicon): generate tanpa grammar dulu
  (stop pada `TEMPLATE_MARK`), grammar JSON hanya bila keluaran bukan objek JSON.
  Grammar llama-cpp-python memindai seluruh ±152 rb token kosakata Qwen tiap langkah
  → ±2× lebih lambat (diukur 41,7 vs 84 tok/s). Belum diuji di Windows; boleh dinyalakan
  di `data/config.json` setelah `test_scenarios.py` lulus di sana.
- **`ai.echoes_template()`** (semua platform): Qwen 3B kadang **menyalin template skema**
  `SYSTEM_PROMPT` untuk transkrip ambigu (`"unit": "Pos Alfa atau null"`, `"quantity": 10`).
  JSON-nya valid, sehingga dulu bisa menjadi tiket karangan (10 + "barang atau kegiatan
  atau null") dan lokasi karangan (Pos Alfa). Kini `validate()` menolaknya → klasifikasi
  aturan + perlu verifikasi. Dijaga `test_platform.py`. Jangan dihapus.
- **`warmup`** (default hanya Apple Silicon): `pipeline.warmup()` di thread latar saat
  `app.launch()` dan setelah `save_settings` — muat Whisper + LLM, kompilasi kernel, cache
  prompt sistem. Tanpa DB.
- `ai._INFER_LOCK`: satu objek Llama dipakai UI manual + pekerja mode dengar terus. Jangan dihapus.
- **fd**: `audio._temp_wav()` menutup fd `mkstemp`. Dulu tiap klip membocorkan satu fd;
  Terminal macOS punya batas lunak 256 → mode dengar terus mati setelah ±250 klip.
  `run.sh` juga menaikkan `ulimit -n` ke 4096.

Jebakan instalasi Mac (sudah ditangani `setup.sh`):
- Wheel Metal resmi `llama-cpp-python 0.3.36` di index abetlen **rusak** (`Bad CRC-32`);
  `uv pip` bahkan menolak zip-nya. setup.sh membangun dari source:
  `CMAKE_ARGS="-DGGML_METAL=on" pip install --no-binary llama-cpp-python` (±1 menit, butuh
  Xcode CLT), dengan `--no-deps` supaya numpy tidak ter-upgrade ke 2.x.
- `mlx-whisper` mewajibkan torch di metadata (±600 MB) padahal tidak di-import saat
  transkripsi → dipasang `--no-deps` + `requirements-mac.txt`.
- `requirements-mac.txt` **wajib** mengulang `numpy<2`: dipasang di proses pip terpisah,
  dan di Python 3.12 scipy 1.18 (butuh numpy 2) akan diam-diam meng-upgrade numpy.
- MLX 0.32 hanya punya wheel macOS 14+ → di macOS 13 langkah MLX dilewati (tidak fatal),
  model MLX tidak diunduh, transkripsi memakai CPU.
- Python 3.13+ tidak didukung (numpy<2 tidak punya wheel) → setup.sh menolak venv selain
  3.11/3.12; di Apple Silicon juga wajib arm64 (Homebrew `/opt/homebrew` didahulukan,
  `GARUDA_PYTHON` untuk memilih). Dijalankan lewat Rosetta → skrip `exec arch -arm64` ulang.
- TTS Piper (espeak-ng) gagal bila path `.venv/.../piper/espeak-ng-data` > ±160 karakter
  (fallback ke path mesin build `/Users/runner/...`). Simpan proyek di path pendek atau set
  `ESPEAK_DATA_PATH` ke folder `piper/` lewat symlink pendek.
- Qwen2.5 7B sudah dicoba di M4 Pro: lebih lambat dan `test_scenarios.py` 46/47
  (genset rusak → INFRASTRUCTURE). Prompt di-tune untuk 3B; jangan jadikan default.

---

## 8. Rencana singkat untuk agent baru

1. Baca file ini (selesai) → `config.py` dan `pipeline.py` (≈130 baris) → baru `actions.py` / `ai.py` sesuai kebutuhan.
2. Buka `README.md` hanya bagian relevan (JSON schema, tabel aksi, tab UI) — jangan baca penuh.
3. Untuk bug: reproduksi via `test_scenarios.py` atau `pipeline.process_text()` (tanpa audio).
4. Untuk fitur: ikuti jalur `ai` → `actions` → `db` → `app`, plus test di skenario yang sama.
5. Selesai: jalankan `test_scenarios.py` + `smoke_ui.py` dan laporkan hasil aktual.
