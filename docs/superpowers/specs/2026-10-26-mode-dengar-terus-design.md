# Desain: Mode Dengar Terus (Continuous Listening) — GARUDA Radio AI

Tanggal: 2026-10-26
Status: menunggu persetujuan

---

## 1. Intent & alasan

Hari ini alurnya **rekam → stop → tekan "Proses audio"**. Satu klip, satu tekan,
satu transmisi. Operator harus hadir di depan layar setiap kali ada trafik radio.

Yang diinginkan: aplikasi **terus mendengarkan sendiri**. Setiap kali ada
transmisi, sistem otomatis mentranskripsi, menganalisis, dan menjalankan aksi.
Operator tidak lagi menekan tombol per transmisi — ia hanya meninjau antrian.

**Prototipe** memakai mikrofon browser. **Production** nanti memakai handy-talkie
yang tersambung ke PC/laptop lewat kabel line-in. Desain ini harus membuat jalur
production murah dikerjakan kemudian, bukan menghalanginya.

---

## 2. Goals

1. Sistem mendeteksi awal/akhir ucapan radio **tanpa operator menekan apa pun**.
2. Setiap transmisi otomatis: transkripsi → analisis LLM → aksi Python → DB.
3. Operator melihat **antrian live + log berjalan** yang memperbarui diri.
4. Aksi diklasifikasikan: **aman = otomatis**, **berdampak = tetap butuh persetujuan**.
5. Tetap 100% offline — tidak ada dependensi yang mengunduh dari internet saat runtime.
6. Tidak memutus jalur lama: rekam manual + "Proses audio" tetap berfungsi.

## 3. Non-goals

- Bukan sistem real-time streaming audio ke Whisper (tidak ada transkripsi
  kata-per-kata saat orang bicara). Kita tetap memproses **per transmisi utuh**.
- Bukan mode multi-operator / multi-browser. Satu instance, satu operator.
- Tidak mengubah skema JSON LLM maupun `ai.AnalysisModel`.
- Tidak mengubah `README.md` (dokumentasi pengguna ditangani terpisah).

## 4. Anti-goals (gagal walau "teknis jalan")

- Aksi berdampak (tiket dibuat berlaku, status tiket berubah) terjadi tanpa
  persetujuan manusia.
- Transkrip hilang saat LLM gagal/down.
- Loop diam-diam mati di tengah jalan tanpa operator sadar.
- Aplikasi mengunduh apa pun dari internet saat runtime.
- Satu klip audio diproses dua kali (transkrip/tiket ganda).

---

## 5. Batasan (constraints)

| # | Batasan | Sumber | Konsekuensi desain |
|---|---|---|---|
| B1 | LLM hanya mengusulkan JSON; eksekusi selalu di `actions.py` | AGENTS.md §1 | Loop tidak boleh mengeksekusi apa pun langsung |
| B2 | Tindakan berdampak menunggu persetujuan operator | AGENTS.md §1 | Butuh daftar putih aksi otomatis (lihat §7) |
| B3 | Tidak ada perintah sistem/jaringan dari AI | AGENTS.md §1 | VAD tidak boleh memicu shell; murni browser |
| B4 | Satu jalur pemrosesan: `pipeline.py` | AGENTS.md §6.2 | Loop memanggil `pipeline.process_audio()`, bukan logika baru |
| B5 | 100% offline; unduhan hanya lewat `setup_models.py` | requirements.txt, AGENTS.md §7 | Aset VAD diunduh saat setup, disajikan lokal |
| B6 | Tidak ada git / version control di folder ini | AGENTS.md §6.6 | Perubahan besar butuh backup `.bak` + konfirmasi |
| B7 | Whisper small di CPU ≈ beberapa detik/klip; LLM 3B ≈ 15–20 s | AGENTS.md §7 | Wajib antrian serial; klip menumpuk, tidak paralel |
| B8 | Gradio 6.31.0 (dipin) | requirements.txt | Pakai API `launch(js=)`, `.start_recording()/.stop_recording()` |
| B9 | Emoji rusak di konsol Windows | AGENTS.md §7 | Kosmetik — jangan andalkan emoji untuk status |

---

## 6. Pendekatan yang dipertimbangkan

Tiga cara membuat browser mengirim audio secara berkelanjutan:

### A. Segmentasi di browser dengan VAD (`@ricky0123/vad-web`) — **DIPILIH**

VAD berbasis WASM/ONNX jalan di browser, mendeteksi awal/akhir ucapan, lalu
memerintahkan komponen `gr.Audio` baku untuk mulai/berhenti merekam. Tiap klip
memicu `.stop_recording()` → fungsi Python.

- **Kelebihan:** tidak butuh Node.js, tidak butuh custom component package,
  tidak butuh build Svelte. Memakai `gr.Audio` baku (UI rekam/stop gratis).
  Ini pola yang didokumentasikan resmi Gradio ("Automatic Voice Detection").
- **Kekurangan:** VAD di sisi browser menyumbang latensi deteksi kecil; aset
  harus diunduh sekali (B5). Gradio harus sudah merender tombol rekam saat
  skrip menyala → butuh retry/tunggu.

### B. Custom component (MediaRecorder + `gradio cc build`) — **ditolak**

Kontrol penuh atas `MediaRecorder`, tapi butuh Node.js 24+, `gradio cc build`,
dan menghasilkan paket `.whl` yang harus dipasang. Menambah satu lapisan build
ke proyek yang sengaja dibuat sederhana & tanpa version control (B6). Keuntungan
kontrolnya tidak sepadan.

### C. Rekam panjang, potong di server — **ditolak**

Operator tetap menekan "Mulai", lalu "Stop" sebelum diproses. Itu bukan
"jalan terus", hanya batch. Melanggar goal #1.

**Catatan penting:** jalur production (HT → line-in) nanti akan memakai loop
**server-side** (`sounddevice` membaca line-in, potong pada senyap), bukan VAD
browser. Karena kedua jalur berujung pada `pipeline.process_audio()`, desain ini
menyiapkan abstraksi **sumber audio** agar penambahan jalur server nanti murah.

---

## 7. Keputusan: tingkat otomatisasi

**Aman → otomatis. Berdampak → menunggu persetujuan.**

| Aksi | Status sekarang | Di mode dengar-terus | Alasan |
|---|---|---|---|
| `CREATE_REPORT` | otomatis | **otomatis** | tidak menciptakan kewajiban apa pun |
| `CREATE_ALERT` | Needs Review | **otomatis, semua prioritas** | sifatnya memberi tahu; ada tombol Ack; tidak memerintah siapa pun |
| `CREATE_TICKET` | `Pending` | **tetap `Pending`** | menciptakan kewajiban kerja nyata |
| `UPDATE_MAP` | Pending Approval | **tetap Pending Approval** | `resolve_map_update()` ikut mengubah `locations.status` — lihat catatan di bawah |
| `UPDATE_STATUS` | usulan | **tetap usulan** | mengubah status tiket; `_match_activity()` hanya mencocokkan kata (ambang 0.7) dan berisiko menutup tiket yang belum selesai |

**Catatan mengapa `UPDATE_MAP` tidak otomatis** (keputusan Anda, setelah saya
tunjukkan konsekuensinya): `db.resolve_map_update()` (database.py:396) **juga
menulis `locations.status`**. Menerapkannya otomatis berarti tabel DEMO
`locations` berubah tanpa ada yang menekan tombol, dan tiga tempat menyatakan
peta **wajib** disetujui operator (`AGENTS.md` §1, tab Peta `app.py:535–536`,
panel dashboard). Dengan `UPDATE_MAP` tetap `Pending Approval`, **tidak ada
aturan produk yang dilonggarkan** dan tidak ada teks dokumentasi yang perlu
diubah. Yang berubah hanyalah *siapa yang membuat draft*-nya: sistem, otomatis,
tanpa operator menekan apa pun.

**Keputusan saya (bukan keputusan Anda):** `UPDATE_STATUS` **tetap** di luar
daftar otomatis, meski Anda memilih "semua otomatis". Alasannya di tabel:
`_match_activity()` mencocokkan kata dengan ambang 0.7 dan bisa menutup tiket
yang belum selesai. Ini menyimpang dari pilihan Anda — mohon dibantah bila
tidak setuju.

### Implementasi daftar putih

Daftar putih di `config.py` agar terlihat dan mudah diubah:

```python
# Aksi yang boleh langsung berlaku di mode dengar-terus.
# Aksi di luar daftar ini TETAP menunggu persetujuan operator.
AUTO_APPLY_ACTIONS = {"CREATE_ALERT"}
```

`run_action()` menerima parameter `auto_apply: bool = False`. Bila `True`:

- `CREATE_ALERT` → dibuat seperti sekarang, ditambah `db.log(... "auto_applied")`.
- `CREATE_TICKET` → **tidak berubah**, tetap `Pending`.
- `UPDATE_MAP` → **tidak berubah**, tetap `Pending Approval` (draft dibuat
  otomatis, penerapan tetap lewat `actions.approve_map_update()`).
- `UPDATE_STATUS` → **tidak berubah**, tetap usulan.

Jalur lama (`process_audio_fn`, `process_text`) memanggil `run_action()` **tanpa**
`auto_apply`, jadi perilaku manual tidak berubah sama sekali.

---

## 8. Keputusan: pemantauan operator

Panel **antrian live + log berjalan** di tab "Monitor & Analisis":

- Tabel/timeline yang tumbuh sendiri, satu baris per transmisi: waktu, durasi
  klip, transkrip (dipotong), kategori, prioritas, aksi, status.
- Tombol persetujuan per baris **tidak bisa** ditanam di `gr.HTML`.
  **Keputusan saya:** panel = `gr.Dataframe(interactive=False)` untuk daftar +
  satu form ID (kolom "ID Aksi", dropdown jenis, tombol Setujui/Tolak) persis di
  bawahnya. Tidak memakai `interactive=True` (klik-baris), karena tabel otomatis
  di-refresh tiap 2 s dan klik bisa "terlewat" saat re-render.
- **Tidak ada duplikasi kontrol**: panel ini adalah satu-satunya tempat approval
  untuk item yang berasal dari mode dengar-terus. `approval_html()` di Tab 2 tetap
  ada (ia menampilkan ringkas semua pending), tetapi panel live memakai ID yang
  sama sehingga menyetujui dari mana pun berujung pada `actions.approve_*` yang
  sama. Ini duplikasi *tampilan*, bukan duplikasi *jalur*.
- Sumber data baris = **DB**.

### Queue vs DB: pembagian state

Kontradiksi yang harus diselesaikan: status antrian ada di memori, status hasil
ada di DB.

**Keputusan saya:** setiap transisi ditulis ke DB supaya panel tetap benar setelah
browser di-refresh:

- `enqueue_clip` menulis satu baris `events_log` (`entity="loop"`,
  `action="queued"`, payload `{path, source, seq}`).
- Pekerja menulis `action="processing"` lalu `action="processed"` / `"failed"`.
- Klip yang dibuang karena antrian penuh → `action="discarded"`.

Panel menjadi **query DB murni** (gabungan `events_log` mode-loop + `reports`),
tanpa membaca state memori. `worker_stats()` tetap ada tetapi hanya untuk
diagnostik/konsol, bukan untuk UI. Ini menghapus kontradiksi dan membuat refresh
browser tidak mengubah apa pun.

Biaya: `events_log` tumbuh lebih cepat. **Keputusan saya:** diterima untuk
sekarang — tabel ini sudah untuk audit dan volume prototipe kecil; bila menjadi
masalah, tambahkan pemangkasan di luar lingkup desain ini.


---

## 9. Arsitektur

### Alur pemrosesan

```
Browser: VAD (vad-web) ─memicu─> gr.Audio .start_recording()
                                      │
                            pengguna bicara; berhenti bicara
                                      ▼
                            gr.Audio .stop_recording()  → filepath klip
                                      ▼
                       app.do_ingest_clip(path)        [baru]
                                      ▼
                       loop.ingest()                   [modul baru]
                        ├─ ukuran/ durasi minimum?      (buang klip < 1.2 s)
                        ├─ antrian.put(path)            (FIFO, non-blocking)
                        └─ kembalikan status ringkas
                                      ▼
                       loop._worker()  [thread daemon, 1 saja]
                        ├─ pipeline.process_audio(path, cfg, source="live")
                        │      (auto_apply=True untuk mode ini)
                        ├─ simpan hasil ke _RECENT (deque ringkas)
                        └─ db.add_event(... "live_ingest")
                                      ▼
                       panel antrian: gr.Timer(2s) → baca DB → HTML
```

### Modul baru: `loop.py`

Tanggung jawab tunggal: **antrian + thread pekerja**. Tidak berisi aturan aksi.

```python
enqueue_clip(path: Path, source: str) -> dict   # cepat, non-blocking
worker_stats() -> dict                          # antrian, diproses, gagal, terakhir
start_worker() / stop_worker()                  # idempotent
```

Desain internal:
- `queue.Queue(maxsize=20)`. Bila penuh (B7: LLM lambat), klip **tertua dibuang**
  dengan pesan jelas di UI — lebih baik daripada menumpuk 10 menit tertinggal.
  Keputusan saya: transmisi terbaru lebih berharga daripada yang tertunda.
- Satu thread daemon. `pipeline.process_audio()` masih dipanggil di bawah
  `_PROC_LOCK` yang sudah ada, sehingga tidak bisa menabrak jalur manual.
- Worker tidak pernah mati karena exception: `try/except` lebar per klip,
  dicatat, lanjut klip berikutnya.
- Tidak ada state global yang dipakai UI secara langsung → UI membaca DB.

### Perubahan `audio.py`

Fungsi kecil `trim/sanity` untuk klip terlalu pendek. **Tidak** mengubah
`transcribe()` supaya baseline test tetap 47/47.

### Perubahan `actions.py`

- `run_action(report_id, analysis, text, auto_apply: bool = False)`.
- `process_audio_result(..., auto_apply: bool = False)` meneruskan parameter.
- Log `auto_applied` untuk setiap aksi yang langsung berlaku.

### Perubahan `pipeline.py`

- `process_audio(path, cfg, source="upload", auto_apply: bool = False)`.
  Parameter diteruskan ke `process_audio_result`. Default `False` →
  semua pemanggil lama tidak berubah perilakunya.

### Perubahan `app.py`

- `do_ingest_clip(audio_path)` — dipanggil `.stop_recording()`.
- Panel antrian: `gr.HTML` + `gr.Timer(interval=2)` → fungsi murni baca DB.
- Tombol **"🟢 Mulai dengar terus" / "⏹ Hentikan"**: tombol hanya menyalakan
  flag `gr.State` dan menyuntik/menghentikan VAD (lewat `js=`) — flag itulah yang
  membuat `do_ingest_clip` menerima klip atau mengabaikannya.
- Indikator status: `listening / memproses / menjeda / VAD tidak tersedia`.
- Pertahankan `btn_process` & tab lain tanpa perubahan.

### Aset VAD & offline (B5)

`@ricky0123/vad-web` adalah paket **npm**, bukan repo HuggingFace. Mekanisme
unduhan yang ada di proyek ini hanya `huggingface_hub` + satu URL `urllib` untuk
TTS, jadi ini bukan "satu baris".

- **Keputusan saya:** `setup_models.py vad` mengunduh tarball dari
  `registry.npmjs.org` memakai `urllib` + `tarfile` (tanpa Node/npm), lalu
  **menormalkan** hasilnya ke `models/vad/` yang datar:
  `silero_vad*.onnx`, `*.wasm`, audio worklet, dan bundle `onnxruntime-web`.
  Daftar file ditulis eksplisit di kode, dan `vad` gagal keras bila ada yang
  hilang — supaya versi tidak pernah "setengah terpasang".
- Versi dipin di `config.py` (`VAD_ASSET_VERSION`), sejalan dengan cara proyek
  ini memin versi di `requirements.txt`.
- `run.bat` sudah memaksa offline → tidak ada jaring. Bila `models/vad/` kosong
  atau tidak lengkap, tombol mode menampilkan pesan jelas ("Aset VAD belum ada.
  Jalankan `setup_models.py vad`") dan mode manual tetap jalan.
- Penyajian: `models/vad` ditambahkan ke `allowed_paths` (pola yang sama dengan
  `samples/` dan `exports/`).
- JS yang disuntikkan **wajib** diberi `baseAssetPath` dan
  `onnxWASMBasePath` yang menunjuk ke folder tersaji itu — tanpa ini
  `vad-web` akan mencari aset relatif ke halaman dan gagal.
- Alasan tidak menyalin bundle ke repo: repo ini tanpa version control (B6),
  dan file biner besar tidak layak jadi bagian kode.

### Cara JS VAD dipasang ke halaman

- `launch(js=...)` jalan **sekali saat load**, sebelum komponen `gr.Audio` ada →
  skrip menunggu (retry berbatas) sampai tombol rekam terlihat.
- `<script>` di dalam `gr.HTML` **tidak** dieksekusi oleh Gradio (di-sanitasi),
  jadi panel antrian bukan tempat host skrip.
- **Keputusan saya:** satu skrip di `launch(js=)` yang mengelola siklus
  mulai/berhenti VAD dari flag yang dibaca lewat elemen status di DOM; tidak
  disebar ke banyak handler tombol. Bila ternyata `js=` Gradio 6 tidak
  memadai saat implementasi, mundur ke `gr.HTML` dengan aset yang disajikan
  dari `allowed_paths` dan `<iframe>`-based loader — dicatat di §16.

---

## 10. Penanganan kasus tepi

| Kasus | Perilaku |
|---|---|
| Klip < 1.2 s / hanya derau | Ditolak di `loop.enqueue_clip`, tidak masuk antrian |
| Whisper menghasilkan teks kosong | `pipeline` sudah mengembalikan "tidak ada yang disimpan" — dibiarkan |
| LLM down / JSON rusak | Tetap tersimpan `UNKNOWN`, `llm_valid=0`, aksi `NONE` (perilaku B1/B4 dipertahankan) |
| Audio identik terkirim dua kali | `audio_hash` UNIQUE di `actions.py` sudah memblokir — tidak ada perubahan |
| Transmisi sama diulang dalam 1 jam, status peta sama | Diblokir sebagai duplikat pending (perilaku yang ada, tidak berubah) |
| Antrian penuh | Klip tertua dibuang, penghitung "dibuang" tampil di UI |
| Browser ditutup / tab di-refresh | VAD berhenti; antrian tetap diproses di server sampai habis |
| Dua tab terbuka | Keduanya bisa kirim klip. Keputusan saya: diterima (satu instance, satu operator), dicatat di UI |
| VAD menyala di halaman tanpa izin mic | Browser meminta izin; bila ditolak, tampilkan pesan jelas, mode manual tetap ada |

---

## 11. Yang diputuskan Anda

1. **Aksi aman = otomatis**: `CREATE_ALERT` (semua prioritas) otomatis;
   `CREATE_TICKET` → Pending; `UPDATE_MAP` → Pending Approval; `UPDATE_STATUS`
   → usulan. Tidak ada aturan produk yang dilonggarkan.
2. **Pemantauan**: antrian live + log berjalan.
3. **Pemutar file ke loop**: ya — dimasukkan sebagai alat uji (lihat §12).
4. **Platform prototipe**: mikrofon browser; production nanti line-in HT.
5. **Verifikasi browser**: Anda menjalankan `setup.bat`; saya verifikasi di
   mesin ini setelahnya.

## 12. Pemutar file sebagai input uji (dari jawaban Anda)

Untuk menguji loop tanpa harus terus bicara ke mic: tombol "putar file contoh"
memutar `samples/*.wav` sebagai input radio. Implementasi termurah: file diputar
di browser **melalui `MediaStreamDestination`** saat perangkat audio sistem
di-set ke "Stereo Mix"/"What U Hear", sehingga VAD menangkapnya persis seperti
suara mic. **Keputusan saya:** jangan menambahkan abstraksi pemutar tersendiri
di server sekarang — itu akan menduplikasi jalur dan menambah permukaan uji.
Bila ternyata Stereo Mix tidak tersedia di mesin demo, alternatifnya adalah
kolom "uji cepat" yang memanggil `do_ingest_clip()` dengan path file sample
(tanpa VAD). Keduanya tidak menyentuh `pipeline.py`.

## 13. Yang diserahkan ke builder

- Ambang minimum panjang klip, interval `gr.Timer`, dan `maxsize` antrian:
  angka awal 1.2 s / 2 s / 20; builder boleh menyetel sambil menguji.
- Pemilihan retry untuk menunggu tombol rekam dirender (skrip `js`).
- Apakah test baru masuk `test_scenarios.py` yang sama atau file terpisah —
  **keputusan saya:** file terpisah `test_live.py`, karena skenario baseline
  melakukan reset DB di awal. Baseline tetap **47/47 PASS** tak tersentuh;
  `test_live.py` berdiri sendiri dan melakukan reset sendiri di akhir.

## 14. Ukuran pekerjaan

**Proyek kecil–menengah** (±4 file disentuh + 1 modul baru + 1 langkah setup).
Desain ini dianggap "full" karena menyentuh aturan produk (B2) dan menambah
modul baru; bukan sekadar ubah UI.

## 15. Rencana verifikasi

1. `test_scenarios.py` tetap **47/47 PASS** (jalur lama tidak berubah).
2. `smoke_ui.py` tetap **SMOKE PASS**.
3. Berkas baru `test_live.py` (melakukan reset DB sendiri, tidak menyentuh
   `test_scenarios.py`), dijalankan lewat `pipeline.process_audio(...,
   auto_apply=True)` langsung — tanpa browser:
   - `CREATE_ALERT` → alert dibuat **dan** `events_log` berisi `auto_applied`.
   - `UPDATE_MAP` dengan `auto_apply=True` → draft dibuat tetapi status tetap
     **`Pending Approval`**, dan `locations.status` **tidak berubah**
     (bukti tidak ada aturan yang dilonggarkan).
   - `CREATE_TICKET` dengan `auto_apply=True` → status tetap **`Pending`**
     (bukti daftar putih tidak bocor).
   - `UPDATE_STATUS` dengan `auto_apply=True` → tiket **tidak** berubah status.
   - `auto_apply=False` → perilaku identik dengan baseline.
   - `loop.enqueue_clip` dengan klip terlalu pendek → tidak masuk antrian.
   - Antrian penuh → klip tertua dibuang dan tercatat `discarded`.
4. Uji browser manual: ucapkan satu kalimat → baris muncul di antrian dalam
   hitungan detik; ucapkan lagi → baris kedua muncul. (Tidak bisa dijalankan
   otomatis tanpa browser; dicatat jujur apa adanya.)
5. `AGENTS.md` diperbarui: modul `loop.py`, `AUTO_APPLY_ACTIONS`, langkah setup
   VAD, peringatan offline. **Tidak ada** perubahan teks aturan, karena
   `UPDATE_MAP` tetap butuh persetujuan.

### Tahapan kerja

`.venv` belum ada di mesin ini; Anda akan menjalankan `setup.bat`. Urutan kerja:

- **Fase 0 (verifikasi asumsi, ±15 menit, setelah `.venv` ada):** buktikan
  `.stop_recording()` di Gradio 6.31 benar-benar mengirim file ke server dan
  memicu fungsi Python. Bila gagal → **berhenti**, kembali ke brainstorming
  (opsi B: custom component). Ini mencegah membangun UI di atas asumsi yang salah.
- **Fase 1 (bisa dibuktikan tanpa browser):** daftar putih `AUTO_APPLY_ACTIONS`,
  parameter `auto_apply` di `actions.py`/`pipeline.py`, `loop.py`, `test_live.py`.
  Ini menguji aturan produk — bagian paling berisiko — lebih dulu.
- **Fase 2:** panel antrian, `gr.Timer`, `do_ingest_clip`.
- **Fase 3:** `setup_models.py vad`, skrip VAD di `launch(js=)`, uji ucap-langsung.
- **Fase 4:** `README.md` bagian "Mode Dengar Terus" (ditulis **setelah** kode
  jalan dan teruji, sesuai keputusan Anda).


## 16. Risiko

| Risiko | Dampak | Mitigasi |
|---|---|---|
| `.venv` belum ada di mesin ini | Desain belum bisa dijalankan, hanya dibaca | Anda menjalankan `setup.bat`; verifikasi setelahnya |
| API Gradio 6 berbeda dari contoh dokumentasi (v5) | `launch(js=)` / `.stop_recording()` bisa beda | Fase 0: verifikasi sebelum menulis UI |
| `.stop_recording()` tidak memicu event dengan filepath di Gradio 6 | **Seluruh pendekatan A batal** | Fase 0; bila gagal, eskalasi ke opsi B dan kembali ke brainstorming |
| Klip berurutan menabrak path temp yang sama | Dua transmisi bisa termerge / terdedupe | Cek path unik per klip; `audio_hash` jadi jaring terakhir |
| Aset VAD tidak tersedia offline | Mode dengar-terus tidak bisa dipakai | Nonaktif dengan pesan; mode manual tetap; `setup_models.py vad` |
| Nama file/struktur aset VAD berubah antar versi | `baseAssetPath` salah → VAD gagal senyap | Versi dipin; daftar file eksplisit; gagal keras |
| Llama 3B lambat (15–20 s/klip) | Antrian menumpuk pada trafik padat | Antrian berbatas + buang-tertua + tampilkan lag |
| VAD salah deteksi (derau radio) | Klip palsu | Ambang minimum durasi + filter Whisper VAD yang sudah ada |
| `events_log` tumbuh cepat | Tabel audit membengkak | Diterima untuk prototipe (§8); pemangkasan di luar lingkup |

## 17. Pertanyaan yang sudah dijawab

- **Peta** → tetap `Pending Approval`; draft dibuat otomatis, penerapan tetap
  lewat persetujuan operator. Tidak ada aturan produk yang dilonggarkan.
- **Alert** → semua prioritas otomatis.
- **Uji browser** → Anda menjalankan `setup.bat`; saya verifikasi di mesin ini.
- **Panduan README** → ditulis setelah kodenya ada dan teruji (Fase 4).

Tidak ada pertanyaan terbuka yang menghalangi. Satu-satunya hal yang bisa
membatalkan rencana ini adalah hasil Fase 0.

## 18. Batas permintaan README

Permintaan Anda untuk panduan di `README.md` **ditunda**, sesuai pilihan Anda,
sampai fitur benar-benar ada dan teruji. Alasan mencatatnya di sini: `README.md`
adalah dokumentasi pengguna dan saat ini hanya berisi hal yang bisa dijalankan.
Menulis panduan untuk fitur yang belum ada akan membuat janji palsu. Isi yang
direncanakan untuk Fase 4:

1. Cara memulai / menghentikan mode dengar terus.
2. Arti indikator status (`mendengarkan / memproses / menjeda / VAD tidak tersedia`).
3. Cara membaca antrian live dan di mana menyetujui draft tiket/peta.
4. Tabel aksi otomatis vs menunggu persetujuan (dari §7).
5. Langkah `setup_models.py vad` untuk aset offline.
6. Batasan: butuh izin mikrofon, satu operator, lag LLM 3B di CPU.
7. Panduan production: HT → line-in + rencana loop server-side.



| Risiko | Dampak | Mitigasi |
|---|---|---|
| `.venv` belum ada di mesin ini | Desain belum bisa dijalankan, hanya dibaca | Nyatakan terus terang; verifikasi setelah `setup.bat` |
| API Gradio 6 berbeda dari contoh dokumentasi (v5) | `launch(js=)` / `.stop_recording()` bisa beda | Verifikasi kecil saat implementasi sebelum menulis UI penuh |
| Aset VAD tidak tersedia offline | Mode dengar-terus tidak bisa dipakai | Nonaktif dengan pesan; mode manual tetap; `setup_models.py vad` |
| Llama 3B lambat (15–20 s/klip) | Antrian menumpuk pada trafik padat | Antrian berbatas + buang-tertua + tampilkan lag |
| VAD salah deteksi (derau radio) | Klip palsu | Ambang minimum durasi + filter Whisper VAD yang sudah ada |
