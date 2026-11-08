# Halaman Approval Tiket — Desain

Tanggal: 2026-10-26
Status: disetujui operator (4 keputusan dijawab)

## Masalah

Tab "Riwayat & Tiket" sekarang tidak punya cara **memilih** tiket. Untuk
menyetujui tiket, operator harus membaca ID di tabel lalu **mengetik ulang**
ID itu ke kotak `gr.Number("ID Tiket")` sebelum menekan tombol. Tidak ada
filter pada tabel tiket, tidak ada tombol Decline, dan tidak ada tombol Hapus
sama sekali di kode.

Akibatnya operator bingung: "tiket mana yang saya setujui, dan bagaimana
caranya?"

## Keputusan operator

| Pertanyaan | Keputusan |
|---|---|
| Cara memilih tiket | **Checkbox per baris + aksi massal** |
| Arti "Decline" | **Status → `Rejected`** (data tetap tersimpan) |
| Arti "Hapus" | **Hanya tiket berstatus `Rejected`** boleh dihapus, dengan konfirmasi |
| Filter | **Status, Prioritas, Jenis, Rentang tanggal** (tanpa pencarian teks) |

## Desain

### Lokasi UI

Tab baru **"✅ Approval Tiket"** di antara "Dashboard" dan "Riwayat & Tiket".
Tab lama tetap ada, tetapi blok "Persetujuan & perubahan status" yang
mengharuskan mengetik ID **dipindahkan** ke tab baru dalam bentuk yang benar.
Menyisakan dua jalur persetujuan yang berbeda akan membuat operator bingung
lagi, jadi jalur ketik-ID untuk *approve/decline* dihapus, bukan diduplikasi.

### Tabel

`gr.Dataframe` dengan kolom pertama `Aktif` bertipe **bool** — ini checkbox
sungguhan per baris di Gradio 6.

Kolom:
`Aktif | ID | Jenis | Item | Jumlah | Satuan | Pelapor | Prioritas | Status | Dibuat`

`interactive=True` agar checkbox bisa diklik, tetapi `static_columns`
menunjuk semua kolom selain `Aktif` sehingga **isi tiket tidak bisa diubah
dari tabel**. Nomor ID juga tidak boleh bisa diubah, karena seluruh tombol
bekerja berdasarkan ID.

### Filter

Empat kontrol di atas tabel, plus tombol **Terapkan filter** dan **Reset**.

- **Status** — `CheckboxGroup` berisi kelima status. Default: semua tercentang.
  (Checkbox, bukan dropdown, supaya operator bisa memilih "Pending + Approved"
  sekaligus.)
- **Prioritas** — `CheckboxGroup` HIGH/MEDIUM/LOW. Default: semua.
- **Jenis** — `Dropdown`, isinya jenis yang benar-benar ada di database,
  ditambah "Semua".
- **Rentang tanggal** — dua `DateTime` (Dari / Sampai), `include_time=False`.

Filter diterapkan di SQL (`database.list_tickets(...)`), bukan disaring di
Python setelah semua baris diambil, supaya `limit` tetap bermakna.

### Tombol aksi

Empat tombol, semuanya bekerja pada **baris yang dicentang**:

| Tombol | Aksi | Syarat |
|---|---|---|
| ✅ **Approve** | `Pending` → `Approved` | hanya baris `Pending` |
| 🚫 **Decline** | `Pending`/`Approved` → `Rejected` | bukan `Completed`/`Rejected` |
| 🗑 **Hapus** | hapus permanen | **hanya `Rejected`**, dengan konfirmasi |
| 🔄 **Muat ulang** | baca ulang dari DB | — |

### Aturan penting

1. **Tidak ada baris tercentang → tolak dengan pesan jelas**, jangan diam.
2. **Aksi massal harus jujur per baris.** Bila 3 tiket dicentang dan 1 tidak
   memenuhi syarat, 2 tetap diproses dan hasilnya dilaporkan:
   "2 berhasil, 1 dilewati (tiket #7 berstatus Completed)". Aksi tidak
   dibatalkan seluruhnya hanya karena satu baris gagal.
3. **Hapus wajib konfirmasi.** Tanpa konfirmasi, salah klik menghapus data
   permanen. Konfirmasi memakai `gr.Warning`/dialog yang menjelaskan bahwa
   ini tidak bisa dibatalkan.
4. **Hapus hanya untuk `Rejected`.** Tiket `Pending`/`Approved` harus
   di-decline dulu. Ini mencegah tiket yang masih berjalan terhapus.
5. Setiap perubahan status sudah tercatat di `events_log` lewat
   `db.update_ticket_status` — jangan lewati fungsi itu agar audit tetap utuh.

### Perubahan database

Tambah satu fungsi, mengikuti pola `update_ticket_status`:

```python
def delete_ticket(ticket_id: int, actor: str = "operator") -> bool:
    # Hanya boleh menghapus tiket Rejected. Mengembalikan True bila terhapus.
```

Penjagaan status dilakukan **di dalam** fungsi database, bukan hanya di UI,
supaya pemanggil lain tidak bisa melewatinya.

## Uji

Uji baru di `test_live.py` (statis + DB sungguhan, tanpa browser):

1. Filter status mengembalikan hanya status yang dipilih.
2. Filter prioritas bekerja.
3. Filter jenis bekerja.
4. Filter rentang tanggal bekerja.
5. Filter gabungan (status + prioritas) bekerja.
6. Approve hanya mengubah `Pending`; `Completed` tidak ikut berubah.
7. Decline mengubah `Pending` → `Rejected`.
8. `delete_ticket` menolak tiket `Pending`.
9. `delete_ticket` menolak tiket `Approved`.
10. `delete_ticket` berhasil untuk `Rejected`.
11. `delete_ticket` mengembalikan `False` untuk ID yang tidak ada.
12. Aksi massal melaporkan jumlah berhasil dan dilewati dengan benar.
13. Tidak ada baris dicentang → pesan "pilih dulu", bukan diam.
14. Setelah approve, tabel memantulkan status baru (bukan data basi).
