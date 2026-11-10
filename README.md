# GARUDA Radio AI

Aplikasi **lokal & offline** untuk memantau komunikasi radio: audio →
**faster-whisper** (transkripsi bahasa Indonesia) → **LLM kecil lokal**
(Qwen2.5 1.5B/3B via llama.cpp) → JSON terstruktur → **validasi Python** →
tindakan terkontrol di SQLite → dashboard, tiket, peringatan, peta, rekap.

Dibuat untuk lomba **Kartika TNI AD**.

> **DEMO DATA.** Seluruh lokasi, unit, dan skenario adalah simulasi. Tidak ada
> rekaman komunikasi militer asli. Sistem **tidak pernah** mengirim instruksi
> penanganan, mengerahkan personel, atau mengeksekusi perintah sistem.
> Semua tindakan berdampak menunggu persetujuan operator.

> **Agent AI / kontributor:** mulai dari [`AGENTS.md`](AGENTS.md) — peta modul,
> aturan wajib, dan perintah. README ini dokumentasi untuk manusia.

## Fitur

- **100% offline** setelah setup: Whisper, LLM, TTS, dan database lokal. Tanpa API cloud.
- **Transkripsi Indonesia** dengan VAD (bagian sunyi tidak ditranskripsi).
- **Klasifikasi terstruktur**: kategori, unit, item, jumlah, prioritas, aksi.
- **Mode dengar terus**: mikrofon browser dipantau VAD (Silero, WASM); potongan
  bicara dikirim ke server, diproses berurutan lewat antrian, lalu tampil di UI.
- **Human-in-the-loop**: tiket, alert, perubahan status, dan pembaruan peta
  menunggu persetujuan operator. AI tidak punya akses shell/jaringan.
- **Anti-duplikasi**: hash audio SHA-256 + kunci tiket (item + jumlah + pelapor).
- **Audit**: semua langkah tercatat di `events_log` (aktor `system` / `operator`).
- **Peta offline** (Folium, fallback SVG) dan **rekap CSV/PDF**.

## Prasyarat

- Windows 11, **Python 3.11 atau 3.12** (diuji 3.12.4)
- RAM **minimal 8 GB**, disarankan 16 GB
- Disk **minimal 10 GB** kosong (model + `.venv` ± 5–6 GB)
- GPU NVIDIA **opsional** — tanpa GPU aplikasi tetap jalan, hanya lebih lambat
- Node.js hanya untuk `test_vad_gerbang.js` (opsional)
- Internet **hanya saat setup** untuk mengunduh dependensi + model

## Rekomendasi spesifikasi laptop

RTX 4050 Laptop 6 GB yang disebut di bagian **Performa** adalah **laptop pengujian
pengembang**, bukan syarat minimum. Aplikasi berjalan tanpa GPU sama sekali.

| Komponen | Minimum (jalan, lambat) | Rekomendasi | Ideal (demo lomba) |
|---|---|---|---|
| CPU | 4 core (i5 gen-8 / Ryzen 5 2500U) | 6–8 core (i5-12450H / Ryzen 5 7535HS) | 8 core+ (i7-13700H / Ryzen 7 7840HS) |
| RAM | 8 GB | 16 GB | 32 GB |
| GPU | tidak wajib (iGPU) | NVIDIA **6 GB VRAM** (RTX 3050 6 GB / RTX 4050) | NVIDIA **8 GB+** (RTX 4060 / 4070) |
| Disk | 10 GB kosong, SSD | 20 GB kosong, NVMe | 30 GB kosong, NVMe |
| Driver | – | NVIDIA driver ≥ 550 (CUDA 12.4, wheel `cu124`) | sama |

Perkiraan kebutuhan VRAM (dari ukuran model di `models/`):

| Beban | Perkiraan VRAM |
|---|---|
| Qwen2.5 3B Q4_K_M, ctx 4096 | ± 2,0 GB bobot + ± 0,2 GB KV cache |
| Qwen2.5 1.5B Q4_K_M | ± 1,1 GB bobot + KV cache |
| Whisper `small` (int8) | ± 0,5 GB |
| Whisper `large-v3-turbo` (fp16) | ± 1,6–2,0 GB |

Jadi 6 GB VRAM cukup untuk LLM 3B **dan** Whisper sekaligus. GPU 4 GB masih jalan
dengan `llm_gpu_layers` sebagian (mis. 20–24) atau Whisper di CPU. GPU AMD/Intel
jatuh ke jalur CPU — `llama-cpp-python` dan `ctranslate2` di sini build CUDA.

Tanpa GPU: pilih Qwen2.5 **1.5B** di tab Pengaturan (analisis ±7–10 s); 3B tetap
jalan tapi ±15–20 s per transmisi.

## Instalasi

```bat
setup.bat
run.bat
```

`setup.bat` akan:
1. membuat `.venv`,
2. memasang `requirements.txt` + `llama-cpp-python` (coba wheel CUDA cu124, fallback CPU),
3. menanyakan unduhan model (Whisper `small` + Qwen2.5 3B + TTS Piper ≈ 2.7 GB;
   opsional Whisper `large-v3-turbo`),
4. menampilkan status model.

Unduh model manual (kapan saja, saat online):

```bat
.venv\Scripts\python.exe setup_models.py whisper-small llm-3b tts-id
.venv\Scripts\python.exe setup_models.py whisper-large-v3-turbo   REM opsional
.venv\Scripts\python.exe setup_models.py status
```

Model **tidak pernah** diunduh diam-diam saat `run.bat`. Bila belum ada, UI menolak
dengan pesan jelas dan transkrip yang berhasil tetap disimpan sebagai belum terklasifikasi.

## Struktur file

```text
garuda-radio-ai/
├── app.py                 UI Gradio satu halaman (5 tab) + skrip VAD browser
├── audio.py               konversi audio 16 kHz mono + faster-whisper
├── ai.py                  prompt klasifikasi + validasi JSON (Pydantic)
├── actions.py             action engine + aturan persetujuan + anti-duplikasi
├── pipeline.py            process_audio() / process_text(): satu jalur UI & test
├── loop.py                antrian klip mode dengar terus (thread pekerja)
├── database.py            SQLite (laporan, tiket, alert, peta, audit log)
├── map_view.py            peta Folium (fallback SVG offline) — DEMO DATA
├── reports.py             rekap + ekspor CSV/PDF
├── config.py              path, model, daftar lokasi simulasi
├── setup_models.py        unduh & cek model
├── samples_gen.py         generator audio simulasi (TTS Piper Indonesia)
├── test_scenarios.py      uji end-to-end 6 skenario (47 pemeriksaan)
├── test_live.py           uji mode dengar terus: aturan aksi + antrian
├── test_ingest_endpoint.py uji protokol HTTP upload + call ingest_clip
├── test_vad_gerbang.js    uji gerbang sunyi VAD di browser (bundle asli)
├── test_alert_refresh.py  regresi: refresh halaman baca DB, bukan snapshot
├── smoke_ui.py            uji cepat server UI (HTTP 200)
├── docs/superpowers/      spesifikasi & rencana desain
├── setup.bat / run.bat
├── models/                whisper/<size>/, llm/*.gguf, tts/, vad/   (tidak dilacak git)
├── data/                  garuda.db, audio/, exports/              (tidak dilacak git)
└── samples/               audio simulasi hasil TTS                 (tidak dilacak git)
```

## Alur

```text
Mikrofon browser (VAD) / upload file / rekaman radio  (wav, mp3, m4a, ogg, webm)
        │  audio.py: konversi ke 16 kHz mono
        ▼
faster-whisper (lokal, VAD, bahasa Indonesia)
        ▼
LLM lokal (Qwen2.5 1.5B/3B GGUF, JSON mode)
        ▼
ai.validate(): kategori/aksi/prioritas/angka divalidasi → JSON
        ▼
actions.run_action(): aturan otomatis (tidak ada eksekusi perintah)
        ▼
SQLite → dashboard, tiket, alert, peta, audit log
        ▼
Persetujuan operator untuk tiket / perubahan status / pembaruan peta / alert
```

### Output JSON yang diminta dari LLM

```json
{"category":"LOGISTICS","unit":"Pos Alfa","summary":"Stok air bersih menipis",
 "item":"air bersih","quantity":10,"unit_measure":"dus","location":null,
 "priority":"MEDIUM","action":"CREATE_REPORT","needs_approval":false,
 "confidence":0.9,"missing_information":[]}
```

Lokasi hanya boleh dipilih dari daftar DEMO; lokasi di luar daftar → `null` +
`missing_information`, dan tidak ada koordinat yang dikarang.

## Enam aksi & aturan otomatisasi

| Aksi | Yang dilakukan sistem | Persetujuan |
|---|---|---|
| `CREATE_REPORT` | Simpan laporan | – |
| `CREATE_TICKET` | Draft tiket **Pending** | ✅ sebelum diproses |
| `UPDATE_STATUS` | Cari tiket yang cocok; hanya diusulkan bila identitas pasti | ✅ tombol "Terapkan pembaruan status" |
| `CREATE_ALERT` | Alert "Needs Review", prioritas tampil merah | ✅ (ubah status alert) |
| `UPDATE_MAP` | "Pending Approval" untuk lokasi DEMO | ✅ Setujui / Tolak |
| `NONE` | Simpan transkrip + laporan saja | – |

- **Anti-duplikasi**: hash audio (SHA-256) unik di tabel `transcripts`; audio identik
  tidak membuat laporan/tiket baru. Tiket dengan item + jumlah + pelapor sama (dan masih
  terbuka) tidak dibuat ulang.
- **Kesalahan AI** → laporan tersimpan sebagai `UNKNOWN` / "belum terklasifikasi",
  tanpa tindakan apa pun.
- Semua langkah tercatat di tabel `events_log`.

## Tab UI

1. **🎙 Monitor** — rekam/unggah audio, mode dengar terus, transkrip, kategori,
   ringkasan, lokasi, unit, prioritas, keyakinan, tanda *perlu verifikasi*,
   JSON mentah (audit), dashboard ringkas + panel persetujuan operator.
2. **✅ Tiket** — daftar laporan & tiket, filter status/prioritas/jenis/tanggal,
   pilih baris → tombol aksi sesuai status (setujui, tolak, proses, selesai, hapus).
3. **🗺 Peta (DEMO DATA)** — marker pos/fasilitas/infrastruktur + status; alert ditandai.
4. **📑 Rekap** — buat rekap per kategori, kronologi penting, ekspor **CSV** dan **PDF**.
5. **⚙ Pengaturan** — model Whisper (small / large-v3-turbo) & LLM, device, GPU layers, status GPU.

## Mode dengar terus

- VAD Silero berjalan di browser (WASM, offline) dari `models/vad/`.
- Hanya potongan bersuara yang dikirim; sunyi ±3 detik menutup klip.
- Browser `POST /gradio_api/upload` lalu `/gradio_api/call/ingest_clip`; server
  memvalidasi path, durasi minimum 1,2 s, dan folder unggahan.
- `loop.py` memproses klip berurutan (antrian maks 20) di thread terpisah dan
  mencatat setiap transisi ke `events_log`.
- Klik **Mulai** = izin mikrofon. Kegagalan izin/WASM dilaporkan ke UI, tidak
  gagal diam-diam. **Hentikan** menutup stream dan menunggu antrian selesai.

## Pengujian

```bat
.venv\Scripts\python.exe test_scenarios.py        REM reset DB + 47 pemeriksaan end-to-end
.venv\Scripts\python.exe test_live.py             REM mode dengar terus (reset DB sendiri)
.venv\Scripts\python.exe test_ingest_endpoint.py  REM protokol HTTP ingest (server nyata)
.venv\Scripts\python.exe test_alert_refresh.py    REM regresi refresh alert
node test_vad_gerbang.js                          REM gerbang sunyi VAD di browser
.venv\Scripts\python.exe smoke_ui.py              REM server UI: HTTP 200 + judul
```

`test_scenarios.py` menguji **audio TTS lokal sungguhan** (bukan mock): logistik,
peralatan, penyelesaian kegiatan, medis, infrastruktur, suara berisik, dan
permintaan tiket (solar) — termasuk anti-duplikasi audio & tiket, alert medis
tanpa instruksi penanganan, dashboard, serta ekspor CSV/PDF.

Di UI: tombol **"Buat contoh audio"** lalu **"Uji semua 6 skenario"**.

Status uji terakhir di mesin pengembangan: `test_scenarios.py` 47/47 PASS,
`smoke_ui.py` SMOKE PASS (HTTP 200), `test_alert_refresh.py` PASS.

## Performa

Diukur pada **laptop pengujian pengembang** (Lenovo, Ryzen 7 7735HS, 16 GB RAM,
RTX 4050 Laptop 6 GB), transkripsi CPU + LLM build **CPU**. Angka ini bukan
patokan mesin lain — tanpa GPU hasilnya di kisaran yang sama, dengan GPU bisa
jauh lebih cepat.

| Tahap | Model | Waktu terukur |
|---|---|---|
| Transkripsi ±8 s audio | Whisper `small` (CPU, int8) | ±2–3 s |
| Analisis JSON | Qwen2.5 3B Q4_K_M (CPU) | ±15–20 s |
| Analisis JSON | Qwen2.5 1.5B Q4_K_M (CPU) | ±7–10 s |

Transkripsi memakai GPU otomatis bila `ctranslate2` menemukan CUDA.

**GPU untuk LLM:** `setup.bat` mencoba memasang wheel `llama-cpp-python` CUDA (cu124)
lebih dulu; bila gagal, dipasang build CPU agar aplikasi tetap berfungsi. Build yang
terpasang sekarang mendukung offload GPU (`llama_supports_gpu_offload() == True`,
CUDA terdeteksi). Dengan `n_gpu_layers=-1`, analisis Qwen2.5 3B diperkirakan orde
1–3 s (angka GPU **belum** diukur formal).

## Privasi & batasan

- Tidak ada API cloud: Whisper, LLM, TTS, dan database semuanya lokal.
- Tidak ada login / multi-user (MVP lokal).
- AI tidak punya akses ke shell, jaringan, atau perangkat.
- Aplikasi ini adalah alat bantu pencatatan & informasi untuk data simulasi; ia tidak
  mengambil keputusan operasional, penargetan, atau tindakan militer apa pun.

## Lisensi

Belum ditetapkan. Hubungi pemilik repo sebelum memakai ulang di luar konteks lomba.
