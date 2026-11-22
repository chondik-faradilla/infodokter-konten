# Panduan naskah harian INFODOKTER (untuk sesi Claude terjadwal)

Setiap hari buat **2 naskah**: `produksi/<YYYY-MM-DD>-siang/spec.json` (tayang 11.30 WIB) dan `produksi/<YYYY-MM-DD>-malam/spec.json` (tayang 19.00 WIB), tanggal = **hari ini WIB**. Push ke `main`, lalu workflow **Produksi video INFODOKTER** otomatis membuat VO (Gemini, cadangan Piper), merender, dan menaruh hasil di `antrean/<folder>/`. Autopost memposting sesuai jam.

Contoh lengkap yang sudah lolos QA: `produksi/2026-11-23-siang/spec.json` dan `produksi/2026-11-23-malam/spec.json`. **Salin strukturnya.**

## 1. Riset topik
- Cari topik kesehatan yang sedang ramai di Indonesia (berita 7 hari terakhir, musim, peringatan hari kesehatan, isu viral). Satu topik "waspada/data" dan satu "kebiasaan/mitos/pencegahan" per hari, beda tema.
- Jangan ulangi topik 14 hari terakhir (lihat `sumber/*.md`).
- Sumber angka & klaim: **Kemenkes, WHO, IDAI/PAPDI/IDI, jurnal, atau media arus utama yang mengutip mereka**. Setiap angka di layar wajib punya elemen `source`.
- Dilarang: dosis obat, diagnosis, klaim "menyembuhkan", testimoni, before-after, promosi produk/merek, "terbaik", topik bunuh diri/menyakiti diri, konten yang menakut-nakuti berlebihan. Selalu arahkan ke tenaga kesehatan bila ada gejala.
- Catat sumber (judul, tanggal, URL) di `sumber/<YYYY-MM-DD>.md`.

## 2. Struktur 8 scene (wajib)
| # | Isi | Catatan |
|---|---|---|
| 1 | Hook: angka/fakta mengejutkan | `number` + `text` + `icon` + `source` |
| 2 | Konteks/artinya | 2 elemen `text` |
| 3 | Penyebab/mekanisme singkat | `icon` + `text` |
| 4 | **Maskot pertama** | hanya 1 elemen `mascot`, VO 1 kalimat pendek, `"min": 3.6` |
| 5 | 3 poin inti | `text` judul + 3 `card` |
| 6 | **Maskot kedua** (pose berbeda) | hanya 1 elemen `mascot`, VO 1 kalimat pendek, `"min": 3.6` |
| 7 | Langkah aksi | `text` judul + 3–4 `card` |
| 8 | CTA (pertanyaan pancingan komentar / ajakan bagikan) + logo | `text`, `text` kecil, `logo`, `"tail": 2.5` |

Maskot **tepat 2 kali** (scene 4 dan 6), tidak di tempat lain.

## 3. Format spec.json
```json
{
 "voice": "Achernar",
 "vo_style": "Kamu narator video edukasi kesehatan INFODOKTER. Bacakan naskah berikut dalam Bahasa Indonesia dengan suara hangat, tenang, jelas, tempo sedang, beri jeda singkat antar paragraf:",
 "accent": [226,76,75],
 "scenes": [ {"vo": "kalimat narasi", "elements": [ ... ]} , ... 8 scene ],
 "cover": {"kicker": "TEKS PILL", "lines": ["Baris hook", "*sorotan_kata*"], "pose": "dokter-03-punya-ide", "accent": [226,76,75], "badge": ["73%", "keterangan singkat"]},
 "caption": "caption IG/FB lengkap + tagar",
 "threads": "teks Threads pemancing komentar"
}
```
- `vo`: kalimat pendek, angka **ditulis dengan huruf** ("dua ratus enam puluh lima"), singkatan yang sulit dibaca dieja ("pe-es-en"). 1 scene ≈ 1–2 kalimat.
- `at` = detik munculnya elemen sejak scene mulai. Susun `at` kartu mengikuti urutan kalimat VO (kira-kira 13 karakter VO ≈ 1 detik).
- Teks layar: `*kata*` = sorotan pill toska; `_` di dalam sorotan = spasi yang tidak boleh dipisah (`*genangan_air*`). Maks ±28 karakter per baris pada size 80–92; kartu maks 2 baris ±24 karakter.
- Warna: merah `[226,76,75]` (waspada), biru `[31,111,209]`, navy `[18,48,94]`, oranye `[240,150,60]`, toska `[22,179,163]`.

### Jenis elemen
| type | field |
|---|---|
| `text` | `lines`, `size` (56–96), `y`, opsional `kind:"body"`, `weight`, `color` |
| `number` | `value`, `size`, `y`, `color`, `count` (dtk), `dots` (false untuk angka < 1000), `suffix` ("%") |
| `card` | `icon`, `text` ("baris1\nbaris2"), `y`, `h` (190–220), `size` (44–54), `from` (-1 kiri / 1 kanan), `accent` |
| `icon` | `icon`, `size`, `x`, `y`, opsional `rot` |
| `mascot` | `pose`, `size` 800, `y` 1130, `bubble` ("baris1\nbaris2", maks ±22 karakter/baris) |
| `source` | `text` ("Sumber: ...") |
| `logo` | `w` 780, `y` 1120 |

Ikon tersedia: `drop mosquito fridge dispenser tub check bucket lid recycle shield meter food walk scale clinic calendar`.

Area aman: konten di `y` 400–1500. Tata letak baku kartu: 3 kartu → y 690/960/1230 (h 220) atau 780/1030/1280 (h 210); 4 kartu → y 650/870/1090/1310 (h 190).

### Pose maskot (`engine/assets/mascot/`)
`dokter-01-jempol-semangat`, `02-tangan-bersedekap`, `03-punya-ide`, `04-mencatat-clipboard`, `05-melambai-halo`, `06-peluk-hati-sehat`, `07-menjelaskan-presentasi`, `08-kerja-laptop`, `09-oke-mengedip`, `10-merayakan-sukses`, `11-bingung-bertanya`, `12-baca-rontgen`, `13-masker-jempol`, `14-siap-operasi`, `15-konsultasi-online` (tulis lengkap dengan prefiks `dokter-`). Jangan pakai pose ceria untuk topik duka.

## 4. Caption & Threads
- Caption: hook 1 kalimat + emoji secukupnya, poin inti, langkah aksi, ajakan simpan/bagikan/komentar, baris `Sumber: ...`, lalu 7–9 tagar (selalu ada `#infodokter #inidokterku`).
- Threads: teks saja, 3–5 baris, pertanyaan yang membuat orang ingin bercerita/memilih jawaban. Tanpa klaim medis, tanpa tagar berlebihan.

## 5. Sebelum push
1. `python3 -c "import json;json.load(open('produksi/<folder>/spec.json'))"` untuk kedua file.
2. Uji cepat lokal (opsional, tanpa VO): `python3 scripts/produksi.py <folder>` lalu hapus `antrean/<folder>` dan `.work` sebelum commit (biar GitHub yang memproduksi dengan Gemini).
3. Commit hanya `produksi/`, `sumber/`. Push ke `main`.
4. Tunggu run **Produksi video INFODOKTER** selesai, cek `antrean/<folder>/produksi.json` (`"vo": "gemini"` diharapkan) dan `qa_*.jpg`.
