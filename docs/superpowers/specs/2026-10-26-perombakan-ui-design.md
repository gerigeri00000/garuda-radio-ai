# Perombakan UI/UX GARUDA — Desain

Tanggal: 2026-10-26
Status: disetujui operator (4 keputusan dijawab)

## Masalah

Operator menyebut tampilan "agak ribet". Penyebabnya bukan sekadar halaman
Approval yang baru, melainkan pola yang berulang di seluruh aplikasi:

| Pola | Jumlah | Akibat |
|---|---|---|
| Kotak **ketik ID angka** untuk melakukan aksi | 5 | operator harus menghafal/menyalin ID |
| Tombol **Refresh** terpisah per halaman | 4 | tidak jelas mana yang memperbarui apa |
| Tabel mentah tak bisa diklik | 6 | data terlihat, tapi tak bisa diperlakukan apa pun |
| Tab | 7 | banyak tab isinya mirip (Dashboard vs Riwayat) |
| Tombol berbahaya sejajar tombol aman | Approval, Peta | rawan salah klik |

## Keputusan operator

| Pertanyaan | Keputusan |
|---|---|
| Cakupan | **Rombak semua halaman** |
| Ganti ketik-ID | **Klik baris di tabel** |
| Jumlah tab | **7 → 5 tab** |
| Tombol berbahaya | **Pisahkan + sembunyikan saat tak berlaku** |

## Temuan teknis (diverifikasi di browser sungguhan)

`gr.Dataframe.select` di Gradio 6.31 memberikan **seluruh isi baris**:

```
index=[1, 1]
row_value=[22, 'LOGISTICS', 'air', 'Approved']
selected=True
```

Jadi pemilihan baris bisa diandalkan **tanpa** menebak lewat teks DOM.
Sebaliknya: **membaca isi tabel lewat scraping teks DOM TIDAK boleh dipakai**
untuk uji, karena tabel Gradio merender lewat grid virtual — baris bisa
belum ada saat dibaca, dan `<tbody>` sering hanya berisi satu baris kosong.
Uji klik harus memakai `role='gridcell'` / `td` yang benar-benar berisi.

## Desain

### Struktur tab (7 → 5)

| Baru | Isi | Berasal dari |
|---|---|---|
| 🎙 **Monitor** | mode dengar terus, antrian, transkrip, proses file manual, demo | Tab Monitor (tetap) |
| ✅ **Tiket** | approval + seluruh daftar tiket + peringatan | Approval + Riwayat & Tiket |
| 🗺 **Peta** | peta + pembaruan peta | Tab Peta (tetap) |
| 📑 **Rekap** | rekap + ekspor | Tab Rekap (tetap) |
| ⚙ **Pengaturan** | model & perangkat | Tab Pengaturan (tetap) |

Dashboard dilebur: KPI + status laporan terbaru dipindah ke **atas tab
Monitor**, sehingga tab Dashboard yang isinya mirip dihapus. Riwayat laporan
dan peringatan pindah ke tab **Tiket** (semuanya soal tindak lanjut data).

### Pemilihan baris (menggantikan semua kotak ketik-ID)

Satu pola dipakai di semua halaman:

1. Operator **klik satu baris** pada tabel.
2. Baris terpilih disimpan di `gr.State(int | None)`.
3. Panel aksi menampilkan **ringkasan baris yang dipilih** dalam kalimat biasa,
   mis. "Dipilih: Tiket #46 — LOGISTICS, air 2 dus (Pending)".
4. Tombol aksi bekerja pada baris itu. Bila belum ada yang dipilih, semua
   tombol aksi dinonaktifkan (`interactive=False`) dan muncul petunjuk
   "Klik salah satu baris dulu".

Keuntungan: tidak ada lagi angka yang diketik, dan operator selalu melihat
**apa** yang akan ia ubah sebelum menekan tombol.

### Tombol berbahaya

| Aksi | Warna | Muncul bila |
|---|---|---|
| ✅ Approve | primary (hijau) | baris terpilih berstatus `Pending` |
| 🚫 Decline | stop (merah, terpisah) | status `Pending`/`Approved` |
| 🗑 Hapus | stop (merah, terpisah) | status **`Rejected`** saja |

Tombol yang tidak berlaku **disembunyikan** (`visible=False`), bukan sekadar
dinonaktifkan: lebih mudah dipahami daripada tombol kelabu yang tak bisa ditekan.
Hapus tetap memakai konfirmasi dua langkah yang sudah diuji.

### Satu tombol "Muat ulang data" global

Empat tombol Refresh dihapus, diganti **satu** tombol di header tiap tab yang
memang perlu (Monitor, Tiket, Peta). Tidak ada lagi "Refresh" vs "Muat ulang"
vs "Refresh data" yang membingungkan.

Timer otomatis (antrian 2 detik) tetap ada dan **tidak** perlu tombol — itu
sudah berjalan sendiri.

### Teks panduan dihapus dari layar

Saat ini banyak paragraf penjelasan di atas setiap kontrol. Diganti dengan
label yang jelas + satu baris petunjuk singkat per halaman. Penjelasan
panjang tetap ada di `README`/`AGENTS.md`, bukan di layar operator.

## Risiko yang sudah diperhitungkan

- **Baris yang hilang karena filter** — pilihan disimpan sebagai `gr.State(int)`,
  jadi bila baris hilang setelah filter berubah, tombol dinonaktifkan kembali
  dan pesan "pilihan dibatalkan" muncul. Tidak ada aksi yang mengenai baris salah.
- **Data basi** — setelah setiap aksi, tabel dan panel pilihan dimuat ulang.
- **Klik ganda** — approve dua kali pada tiket yang sama ditolak oleh
  `actions.approve_ticket` (status bukan `Pending` lagi) dan dilaporkan jujur.

## Uji

1. `_uji_approval.py` diperluas: klik baris → state terisi; filter menghapus
   baris → state dikosongkan; tombol sesuai status.
2. Uji browser (Playwright) **wajib** memakai `role='gridcell'`/`td` berisi,
   bukan scraping `<tbody>`.
3. Skenario yang diuji di browser: klik baris → Approve; klik baris Rejected →
   hanya Hapus yang muncul; Hapus 2 langkah; filter mengosongkan pilihan.
4. Seluruh suite lama harus tetap lulus: `test_scenarios.py` 47,
   `test_live.py` 32, `test_ingest_endpoint.py` 13, `test_vad_gerbang.js` 14,
   `smoke_ui.py`.
