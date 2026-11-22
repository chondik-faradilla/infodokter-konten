"""
INFODOKTER.ID autopost: IG Reels + Facebook Page + Threads (teks).

Dipanggil GitHub Actions 2x sehari (slot "siang" 11.30 WIB, "malam" 19.00 WIB).
Mencari folder antrean/<YYYY-MM-DD>-<slot>/ untuk hari ini (WIB); kalau tidak ada,
memakai konten cadangan tertua di antrean/cadangan-*/ supaya akun tidak pernah kosong.

Isi tiap folder konten:
  video.mp4        Reels 9:16
  cover.jpg        cover Reels
  caption.txt      caption IG/FB (sudah termasuk tagar)
  threads.txt      teks Threads (opsional)

Secrets yang dibaca dari environment (tidak pernah dicetak):
  META_TOKEN        token system user infodokter-bot
  THREADS_TOKEN     token Threads (opsional)
  THREADS_USER_ID   id akun Threads (opsional)

Status posting dicatat di status.json di folder konten, supaya tidak ada posting ganda.
"""
import json
import os
import sys
import time
import pathlib
import datetime
import urllib.error
import urllib.parse
import urllib.request

GRAPH = "https://graph.facebook.com/v21.0"
THREADS = "https://graph.threads.net/v1.0"
ROOT = pathlib.Path(__file__).resolve().parent.parent
QUEUE = ROOT / "antrean"
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
WIB = datetime.timezone(datetime.timedelta(hours=7))
DRY_RUN = os.environ.get("DRY_RUN") == "1"


def call(method, url, **params):
    data = urllib.parse.urlencode(params).encode() if method == "POST" else None
    if method == "GET":
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, data=data, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            err = json.load(e).get("error", {})
        except Exception:
            err = {"message": str(e)}
        raise RuntimeError(err.get("message", "error tidak diketahui")) from None


def public_url(path: pathlib.Path) -> str:
    rel = path.relative_to(ROOT).as_posix()
    return f"{CONFIG['public_base_url'].rstrip('/')}/{urllib.parse.quote(rel)}"


def pick_folder(slot: str) -> pathlib.Path | None:
    today = datetime.datetime.now(WIB).strftime("%Y-%m-%d")
    main = QUEUE / f"{today}-{slot}"
    if main.is_dir() and not done(main):
        return main
    spares = sorted(p for p in QUEUE.glob("cadangan-*") if p.is_dir() and not done(p))
    if spares:
        print(f"Konten {main.name} tidak ada, memakai cadangan {spares[0].name}")
        return spares[0]
    return None


def load_status(folder):
    f = folder / "status.json"
    return json.loads(f.read_text()) if f.exists() else {}


def save_status(folder, status):
    (folder / "status.json").write_text(json.dumps(status, indent=2, ensure_ascii=False))


def done(folder):
    s = load_status(folder)
    need = ["instagram", "facebook"] + (["threads"] if (folder / "threads.txt").exists() and os.environ.get("THREADS_TOKEN") else [])
    return all(k in s for k in need)


def page_token(user_token):
    return call("GET", f"{GRAPH}/{CONFIG['page_id']}", fields="access_token", access_token=user_token)["access_token"]


def post_instagram(folder, token, caption):
    ig = CONFIG["ig_user_id"]
    params = dict(media_type="REELS", video_url=public_url(folder / "video.mp4"),
                  caption=caption, share_to_feed="true", access_token=token)
    if (folder / "cover.jpg").exists():
        params["cover_url"] = public_url(folder / "cover.jpg")
    container = call("POST", f"{GRAPH}/{ig}/media", **params)["id"]
    for _ in range(40):  # tunggu Instagram selesai memproses video, maks ~10 menit
        st = call("GET", f"{GRAPH}/{container}", fields="status_code,status", access_token=token)
        if st.get("status_code") == "FINISHED":
            break
        if st.get("status_code") == "ERROR":
            raise RuntimeError(f"Instagram gagal memproses video: {st.get('status')}")
        time.sleep(15)
    else:
        raise RuntimeError("Instagram terlalu lama memproses video")
    return call("POST", f"{GRAPH}/{ig}/media_publish", creation_id=container, access_token=token)["id"]


def post_facebook(folder, ptoken, caption):
    res = call("POST", f"{GRAPH}/{CONFIG['page_id']}/videos",
               file_url=public_url(folder / "video.mp4"), description=caption, access_token=ptoken)
    return res.get("id")


def post_threads(text):
    uid, tok = os.environ["THREADS_USER_ID"], os.environ["THREADS_TOKEN"]
    c = call("POST", f"{THREADS}/{uid}/threads", media_type="TEXT", text=text, access_token=tok)["id"]
    time.sleep(10)
    return call("POST", f"{THREADS}/{uid}/threads_publish", creation_id=c, access_token=tok)["id"]


def main():
    slot = sys.argv[1] if len(sys.argv) > 1 else ("siang" if datetime.datetime.now(WIB).hour < 15 else "malam")
    if not CONFIG.get("aktif", False) and not DRY_RUN:
        print("Autopost belum diaktifkan (config.json: aktif=false). Tidak ada yang diposting.")
        return
    forced = os.environ.get("FOLDER", "").strip()
    folder = (QUEUE / forced) if forced else pick_folder(slot)
    if forced and not folder.is_dir():
        print(f"Folder {forced} tidak ditemukan."); sys.exit(1)
    if not folder:
        print("Antrean kosong: tidak ada konten untuk diposting.")
        sys.exit(1)

    caption = (folder / "caption.txt").read_text(encoding="utf-8").strip()
    status = load_status(folder)
    print(f"Memposting {folder.name} (slot {slot})")
    if DRY_RUN:
        print("DRY_RUN: tidak memposting. Video:", public_url(folder / "video.mp4"))
        return

    token = os.environ["META_TOKEN"]
    errors = []

    if "instagram" not in status:
        try:
            status["instagram"] = post_instagram(folder, token, caption)
            print("Instagram OK")
        except Exception as e:
            errors.append(f"Instagram: {e}")
        save_status(folder, status)

    if "facebook" not in status:
        try:
            status["facebook"] = post_facebook(folder, page_token(token), caption)
            print("Facebook OK")
        except Exception as e:
            errors.append(f"Facebook: {e}")
        save_status(folder, status)

    tfile = folder / "threads.txt"
    if tfile.exists() and os.environ.get("THREADS_TOKEN") and "threads" not in status:
        try:
            status["threads"] = post_threads(tfile.read_text(encoding="utf-8").strip())
            print("Threads OK")
        except Exception as e:
            errors.append(f"Threads: {e}")
        save_status(folder, status)

    if errors:
        print("GAGAL:\n- " + "\n- ".join(errors))
        sys.exit(1)


if __name__ == "__main__":
    main()
