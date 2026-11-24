"""
Autopost klien Kelola Sosmed (dokter klien MARKBRO di infodokter.id).

Dijalankan GitHub Actions tiap 30 menit. Sumber data = Supabase infodokter (tabel sosmed_materi):
materi berstatus 'terjadwal' yang jadwal_at-nya sudah lewat diposting ke Instagram & Facebook Page
akun dokter, lalu ditandai 'terposting' (dashboard dokter otomatis ikut berubah).

Jenis posting ditentukan dari media_urls:
  1 gambar        → feed foto
  2–10 gambar     → karusel
  1 video .mp4    → Reels (Instagram) / video (Facebook)

Secrets (tidak pernah dicetak):
  META_TOKEN                 token system user infodokter-bot (akun dokter harus dibagikan ke Business Portfolio)
  SUPABASE_SERVICE_ROLE_KEY  kunci service_role Supabase infodokter
Threads klien belum didukung (butuh token per akun dokter).
"""
import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

GRAPH = "https://graph.facebook.com/v21.0"
SB_URL = os.environ.get("SUPABASE_URL", "https://ilrysfrtxjxafyhmbglt.supabase.co").rstrip("/")
BUCKET_PREFIX = f"{SB_URL}/storage/v1/object/public/sosmed-media/"
DRY_RUN = os.environ.get("DRY_RUN") == "1"
LOCK_MENIT = 30


def http(method, url, body=None, headers=None, form=None):
    data = None
    h = dict(headers or {})
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
    elif body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        try:
            err = json.load(e)
            msg = err.get("error", {}).get("message") if isinstance(err.get("error"), dict) else err.get("message")
        except Exception:
            msg = str(e)
        raise RuntimeError(msg or str(e)) from None


# ---------- Supabase (service_role) ----------
def sb_headers():
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"].strip()
    return {"apikey": key, "Authorization": f"Bearer {key}"}


def sb(method, path, body=None, prefer=None):
    h = sb_headers()
    if prefer:
        h["Prefer"] = prefer
    return http(method, f"{SB_URL}/rest/v1/{path}", body=body, headers=h)


def iso(dt):
    return dt.astimezone(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def antrean():
    now = datetime.datetime.now(datetime.timezone.utc)
    basi = iso(now - datetime.timedelta(minutes=LOCK_MENIT))
    q = urllib.parse.urlencode({
        "select": "id,doctor_id,jenis,judul,caption,hashtag,platform,media_urls,ig_post_id,fb_post_id,post_lock",
        "status": "eq.terjadwal",
        "jadwal_at": f"lte.{iso(now)}",
        "post_error": "is.null",
        "or": f"(post_lock.is.null,post_lock.lt.{basi})",
        "order": "jadwal_at.asc",
        "limit": "10",
    })
    return sb("GET", f"sosmed_materi?{q}") or []


def klaim(m):
    """Kunci baris supaya dua worker tidak memposting materi yang sama."""
    now = datetime.datetime.now(datetime.timezone.utc)
    basi = urllib.parse.quote(iso(now - datetime.timedelta(minutes=LOCK_MENIT)))
    cond = "post_lock=is.null" if m.get("post_lock") is None else f"post_lock=lt.{basi}"
    rows = sb("PATCH", f"sosmed_materi?id=eq.{m['id']}&status=eq.terjadwal&{cond}",
              {"post_lock": iso(datetime.datetime.now(datetime.timezone.utc))}, prefer="return=representation")
    return bool(rows)


def klien(doctor_id):
    rows = sb("GET", f"sosmed_klien?doctor_id=eq.{doctor_id}&select=ig_user_id,fb_page_id,autopost")
    return rows[0] if rows else None


def masih_klien(doctor_id):
    return sb("POST", "rpc/doctor_tier", {"d": doctor_id}) == "markbro"


def simpan(mid, **kolom):
    sb("PATCH", f"sosmed_materi?id=eq.{mid}", kolom)


def hapus_media(urls):
    paths = [u[len(BUCKET_PREFIX):] for u in urls if u.startswith(BUCKET_PREFIX)]
    if paths:
        try:
            http("DELETE", f"{SB_URL}/storage/v1/object/sosmed-media", body={"prefixes": paths}, headers=sb_headers())
        except Exception as e:  # tidak fatal: file hanya memakan kuota
            print(f"  (hapus media gagal: {e})")


# ---------- Meta Graph ----------
def g(method, path, **params):
    tok = params.pop("token")
    if method == "GET":
        return http("GET", f"{GRAPH}/{path}?{urllib.parse.urlencode({**params, 'access_token': tok})}")
    return http("POST", f"{GRAPH}/{path}", form={**params, "access_token": tok})


def tunggu(container, token, menit=10):
    for _ in range(menit * 4):
        st = g("GET", container, fields="status_code,status", token=token)
        if st.get("status_code") == "FINISHED":
            return
        if st.get("status_code") == "ERROR":
            raise RuntimeError(f"Instagram gagal memproses media: {st.get('status')}")
        time.sleep(15)
    raise RuntimeError("Instagram terlalu lama memproses media")


def is_video(u):
    return urllib.parse.urlparse(u).path.lower().endswith(".mp4")


def post_instagram(ig, media, caption, token):
    if len(media) == 1 and is_video(media[0]):
        c = g("POST", f"{ig}/media", media_type="REELS", video_url=media[0], caption=caption,
              share_to_feed="true", token=token)["id"]
    elif len(media) == 1:
        c = g("POST", f"{ig}/media", image_url=media[0], caption=caption, token=token)["id"]
    else:
        anak = [g("POST", f"{ig}/media", image_url=u, is_carousel_item="true", token=token)["id"] for u in media]
        for a in anak:
            tunggu(a, token, menit=3)
        c = g("POST", f"{ig}/media", media_type="CAROUSEL", children=",".join(anak), caption=caption, token=token)["id"]
    tunggu(c, token)
    pid = g("POST", f"{ig}/media_publish", creation_id=c, token=token)["id"]
    link = g("GET", pid, fields="permalink", token=token).get("permalink")
    return pid, link


def post_facebook(page, media, caption, token):
    ptok = g("GET", page, fields="access_token", token=token)["access_token"]
    if len(media) == 1 and is_video(media[0]):
        return g("POST", f"{page}/videos", file_url=media[0], description=caption, token=ptok).get("id")
    if len(media) == 1:
        return g("POST", f"{page}/photos", url=media[0], message=caption, token=ptok).get("post_id")
    ids = [g("POST", f"{page}/photos", url=u, published="false", token=ptok)["id"] for u in media]
    form = {"message": caption}
    for i, fid in enumerate(ids):
        form[f"attached_media[{i}]"] = json.dumps({"media_fbid": fid})
    return g("POST", f"{page}/feed", token=ptok, **form).get("id")


# ---------- Utama ----------
def proses(m, token):
    mid = m["id"]
    media = m.get("media_urls") or []
    caption = "\n\n".join(x for x in [(m.get("caption") or "").strip(), (m.get("hashtag") or "").strip()] if x)
    k = klien(m["doctor_id"])
    gagal = []
    if not k or not k.get("autopost", True):
        return simpan(mid, post_lock=None, post_error="Autopost dimatikan untuk klien ini.")
    if not masih_klien(m["doctor_id"]):
        return simpan(mid, post_lock=None, post_error="Kontrak klien tidak aktif.")
    if not media:
        return simpan(mid, post_lock=None, post_error="Belum ada media final.")
    plat = m.get("platform") or ["instagram"]
    hasil = {}

    if "instagram" in plat and not m.get("ig_post_id"):
        if not k.get("ig_user_id"):
            gagal.append("ID akun Instagram belum diisi")
        else:
            try:
                pid, link = post_instagram(k["ig_user_id"], media, caption, token)
                hasil.update(ig_post_id=pid, post_url=link)
                simpan(mid, **hasil)  # simpan segera: jangan posting ulang ke IG bila FB gagal
                print("  Instagram OK")
            except Exception as e:
                gagal.append(f"Instagram: {e}")
    if "facebook" in plat and not m.get("fb_post_id"):
        if not k.get("fb_page_id"):
            gagal.append("ID Facebook Page belum diisi")
        else:
            try:
                hasil["fb_post_id"] = post_facebook(k["fb_page_id"], media, caption, token)
                simpan(mid, fb_post_id=hasil["fb_post_id"])
                print("  Facebook OK")
            except Exception as e:
                gagal.append(f"Facebook: {e}")

    if gagal:
        simpan(mid, post_lock=None, post_error="; ".join(gagal)[:1000])
        print("  GAGAL: " + "; ".join(gagal))
        return False
    simpan(mid, status="terposting", posted_at=iso(datetime.datetime.now(datetime.timezone.utc)),
           post_lock=None, post_error=None)
    hapus_media(media)
    return True


def main():
    if not os.environ.get("SUPABASE_SERVICE_ROLE_KEY"):
        print("SUPABASE_SERVICE_ROLE_KEY belum dipasang di Secrets. Autopost klien dilewati.")
        return
    items = antrean()
    print(f"{len(items)} materi klien siap tayang.")
    if DRY_RUN or not items:
        for m in items:
            print(f"- {m['judul']} ({m['jenis']}, {len(m.get('media_urls') or [])} media)")
        return
    token = os.environ["META_TOKEN"].strip()
    ada_gagal = False
    for m in items:
        if not klaim(m):
            continue
        print(f"Memposting: {m['judul']}")
        try:
            if proses(m, token) is False:
                ada_gagal = True
        except Exception as e:  # error tak terduga: lepas kunci & catat
            simpan(m["id"], post_lock=None, post_error=f"Error worker: {e}"[:1000])
            print(f"  ERROR: {e}")
            ada_gagal = True
    if ada_gagal:
        sys.exit(1)


if __name__ == "__main__":
    main()
