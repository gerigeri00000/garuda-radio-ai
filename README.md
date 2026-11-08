# GARUDA Radio AI

> **Agent AI / kontributor:** mulai dari [`AGENTS.md`](AGENTS.md) — ringkasan
> arsitektur, aturan wajib, dan perintah. README ini dokumentasi pengguna.

Aplikasi AI **lokal & offline** untuk memantau komunikasi radio: audio -> **Whisper**
(transkripsi bahasa Indonesia) -> **LLM kecil lokal** (Qwen 1.5B/3B via llama.cpp)
-> JSON terstruktur -> **validasi Python** -> tindakan otomatis di SQLite ->
dashboard, tiket, peringatan, peta, rekap.

> **DEMO DATA.** Seluruh lokasi, unit, dan skenario adalah simulasi. Tidak ada
> rekaman komunikasi militer asli. Sistem **tidak pernah** mengirim instruksi
> penanganan, mengerahkan personel, atau mengeksekusi perintah sistem secara otomatis.
> Semua tindakan berdampak menunggu persetujuan operator.

## Prasyarat

- Windows 11, **Python 3.11 atau 3.12**
- GPU NVIDIA (target: RTX 4050 Laptop, 6 GB VRAM) — CPU juga bisa (lebih lambat)
- Internet **hanya saat setup** untuk mengunduh dependensi + model

## Instalasi

```bat
setup.bat
run.bat
```

`setup.bat` akan:
1. membuat `.venv`,
2. memasang dependensi (`requirements.txt`) + `llama-cpp-python` (build CUDA cu124, fallback CPU),
3. menanyakan apakah model diunduh (Whisper `small` + Qwen2.5 3B + suara TTS Piper ≈ 2.7 GB;
   opsional Whisper `large-v3-turbo`),
4. menampilkan status model.

Unduh model manual (kapan saja, saat online):

```bat
.venv\Scripts\python.exe setup_models.py whisper-small llm-3b tts-id
.venv\Scripts\python.exe setup_models.py whisper-large-v3-turbo   # opsional
.venv\Scripts\python.exe setup_models.py status
```

Model **tidak pernah** diunduh diam-diam saat `run.bat`. Bila belum ada, UI menolak
dengan pesan jelas dan transkrip yang berhasil tetap disimpan sebagai belum terklasifikasi.

## Struktur file

```text
garuda-radio-ai/
├── app.py             UI Gradio satu halaman (6 tab)
├── audio.py           konversi audio + faster-whisper lokal
├── ai.py              prompt klasifikasi + validasi JSON (Pydantic)
├── actions.py         action engine Python + aturan persetujuan + anti-duplikasi
├── pipeline.py        process_audio() / process_text(): satu jalur untuk UI & test
├── database.py        SQLite (laporan, tiket, alert, peta, audit log)
├── map_view.py        peta Folium (fallback SVG offline) - DEMO DATA
├── reports.py         rekap + ekspor CSV/PDF
├── config.py          path, model, daftar lokasi simulasi
├── setup_models.py    unduh & cek model (dipanggil setup.bat)
├── samples_gen.py     generator audio simulasi (TTS Piper Indonesia, offline) + data demo
├── test_scenarios.py  uji end-to-end 6 skenario + permintaan tiket
├── smoke_ui.py        uji cepat server UI (HTTP 200)
├── setup.bat / run.bat
├── models/            whisper/<size>/, llm/*.gguf, tts/   (diisi setup.bat)
├── data/              garuda.db, audio/, exports/
└── samples/           audio simulasi (hasil TTS)
```

## Alur

```text
Mikrofon browser / upload file / rekaman radio  (wav, mp3, m4a, ogg, webm)
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
- Semua langkah tercatat di tabel `events_log` (actor: `system` / `operator`).

## Tab UI

1. **Monitor & Analisis** — rekam/unggah audio, transkrip, kategori, ringkasan, lokasi,
   unit, prioritas, keyakinan, tanda *perlu verifikasi*, JSON mentah (audit).
2. **Dashboard** — jumlah laporan, tiket terbuka, peringatan, peta pending, status terbaru,
   panel persetujuan operator.
3. **Riwayat & Tiket** — daftar laporan (filter tanggal/kategori) dan tiket
   (Pending / Approved / In Progress / Completed / Rejected), ubah status.
4. **Peta (DEMO DATA)** — marker pos/fasilitas/infrastruktur + status; alert ditandai.
5. **Rekap** — tombol *Buat Rekap*, ringkasan per kategori, kronologi penting,
   ekspor **CSV** dan **PDF**.
6. **Pengaturan** — pilih model Whisper (small / large-v3-turbo) & LLM, device, status GPU.

## Pengujian

```bat
.venv\Scripts\python.exe test_scenarios.py     REM reset DB + uji end-to-end (47 pemeriksaan)
.venv\Scripts\python.exe smoke_ui.py           REM uji server UI: HTTP 200 + judul
```

Menguji skenario end-to-end **dengan audio TTS lokal sungguhan** (bukan mock):
logistik, peralatan, penyelesaian kegiatan, medis, infrastruktur, suara berisik,
dan permintaan tiket (solar). Termasuk uji anti-duplikasi audio & tiket, alert medis
tanpa instruksi penanganan, dashboard, serta ekspor CSV/PDF.

Di UI: tombol **"Buat contoh audio"** lalu **"Uji semua 6 skenario"**.
Hasil terakhir: **47/47 PASS**, UI `SMOKE PASS` (HTTP 200).

## Performa

Diukur pada mesin pengembangan (RTX 4050 6 GB), transkripsi CPU + LLM build **CPU**:

| Tahap | Model | Waktu terukur |
|---|---|---|
| Transkripsi ±8 s audio | Whisper `small` (CPU, int8) | ±2–3 s |
| Analisis JSON | Qwen2.5 3B Q4_K_M (CPU) | ±15–20 s |
| Analisis JSON | Qwen2.5 1.5B Q4_K_M (CPU) | ±7–10 s |

Transkripsi memakai GPU otomatis bila `ctranslate2` menemukan CUDA (di mesin uji:
Whisper berjalan di CPU karena CUDA tidak terdeteksi).

**GPU untuk LLM (lebih cepat):** `setup.bat` mencoba memasang wheel `llama-cpp-python`
CUDA (cu124) lebih dulu; bila gagal, dipasang build CPU agar aplikasi tetap berfungsi.
Build CPU yang terpasang saat pengujian: `llama_supports_gpu_offload() == False`.
Dengan wheel CUDA + `n_gpu_layers=-1`, analisis Qwen2.5 3B turun ke orde 1–3 s
(angka ini **belum** terverifikasi di mesin uji).

## Privasi & batasan

- Tidak ada API cloud: Whisper, LLM, dan database semuanya lokal.
- Tidak ada login / multi-user (MVP lokal).
- AI tidak punya akses ke shell, jaringan, atau perangkat.
- Aplikasi ini adalah alat bantu pencatatan & informasi untuk data simulasi; ia tidak
  mengambil keputusan operasional, penargetan, atau tindakan militer apa pun.
