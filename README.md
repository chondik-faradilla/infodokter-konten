# infodokter-konten

Antrean konten dan autopost media sosial **INFODOKTER.ID** (Instagram @inidokterku, Facebook Page IniDokterku, Threads).

## Cara kerja
1. Setiap pagi sesi Claude terjadwal memproduksi 2 konten dan menaruhnya di `antrean/<tanggal>-siang/` dan `antrean/<tanggal>-malam/`.
2. GitHub Actions (`.github/workflows/autopost.yml`) memposting pukul **11.30** dan **19.00 WIB** setiap hari.
3. Kalau konten hari itu tidak ada, dipakai konten `antrean/cadangan-*` tertua supaya akun tidak kosong.

## Isi satu folder konten
| File | Isi |
|---|---|
| `video.mp4` | Reels 9:16 |
| `cover.jpg` | Cover Reels |
| `caption.txt` | Caption IG/FB + tagar |
| `threads.txt` | Teks Threads (opsional) |
| `status.json` | Dibuat otomatis setelah terposting |

## Secrets (Settings → Secrets and variables → Actions)
- `META_TOKEN`: token system user infodokter-bot (wajib)
- `THREADS_TOKEN`, `THREADS_USER_ID`: untuk Threads (menyusul)

## Uji manual
Tab **Actions → Autopost INFODOKTER → Run workflow**, isi `dry_run` = `1` untuk uji tanpa memposting.
