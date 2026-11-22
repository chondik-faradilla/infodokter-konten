"""
Produksi otomatis: produksi/<folder>/spec.json  ->  antrean/<folder>/{video.mp4, cover.jpg, caption.txt, threads.txt}

1. VO: Gemini TTS (gratis, GEMINI_API_KEY). Gagal/kuota habis -> cadangan Piper TTS (offline, gratis).
2. Durasi tiap scene mengikuti durasi VO (proporsional panjang teks, lalu dikunci ke jeda hening terdekat).
3. Render video dengan engine/motion.py, cover dengan engine/cover.py.

Kunci API dibaca dari environment dan tidak pernah dicetak.
"""
import base64
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))
import motion  # noqa: E402
import cover  # noqa: E402

GEMINI_MODELS = ["gemini-2.5-flash-preview-tts", "gemini-3.1-flash-tts-preview"]
PIPER_VOICE = "id_ID-news_tts-medium"
PIPER_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/id/id_ID/news_tts/medium/"

# ejaan bantu agar TTS membaca istilah dengan benar (hanya untuk audio, tidak tampil di layar)
LAFAL = {"PSN 3M Plus": "pe-es-en tiga em plus", "DBD": "de-be-de", "Kemenkes": "Kemenkes", "faskes": "faskes"}


def lafal(text):
    for k, v in LAFAL.items():
        text = text.replace(k, v)
    return text


def write_wav(path, pcm, sr):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(pcm)


def tts_gemini(text, voice, style, out):
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY tidak ada")
    body = {
        "contents": [{"parts": [{"text": f"{style}\n\n{text}"}]}],
        "generationConfig": {"responseModalities": ["AUDIO"],
                             "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}},
    }
    last = None
    for model in GEMINI_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "x-goog-api-key": key})
        for attempt in range(2):
            try:
                with urllib.request.urlopen(req, timeout=180) as r:
                    res = json.load(r)
                part = res["candidates"][0]["content"]["parts"][0]["inlineData"]
                m = re.search(r"rate=(\d+)", part.get("mimeType", ""))
                write_wav(out, base64.b64decode(part["data"]), int(m.group(1)) if m else 24000)
                print(f"VO: Gemini {model}, suara {voice}")
                return
            except urllib.error.HTTPError as e:
                try:
                    msg = json.load(e).get("error", {}).get("message", str(e))
                except Exception:
                    msg = str(e)
                last = f"{model}: HTTP {e.code} {msg[:160]}"
                if e.code in (429, 500, 503) and attempt == 0:
                    time.sleep(20)
                    continue
                break
            except Exception as e:  # noqa: BLE001
                last = f"{model}: {e}"
                break
    raise RuntimeError(last or "Gemini TTS gagal")


def tts_piper(text, out):
    vdir = ROOT / ".piper"
    vdir.mkdir(exist_ok=True)
    for ext in (".onnx", ".onnx.json"):
        f = vdir / f"{PIPER_VOICE}{ext}"
        if not f.exists():
            urllib.request.urlretrieve(PIPER_URL + PIPER_VOICE + ext, f)
    subprocess.run([sys.executable, "-m", "piper", "-m", str(vdir / f"{PIPER_VOICE}.onnx"), "-f", str(out),
                    "--sentence-silence", "0.35"], input=text.encode(), check=True)
    print("VO: Piper (cadangan)")


def wav_duration(path):
    with wave.open(str(path)) as w:
        return w.getnframes() / w.getframerate()


def silences(path):
    r = subprocess.run(["ffmpeg", "-i", str(path), "-af", "silencedetect=noise=-35dB:d=0.25", "-f", "null", "-"],
                       capture_output=True, text=True)
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", r.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", r.stderr)]
    return [(s + e) / 2 for s, e in zip(starts, ends)]


def scene_times(spec, vo_dur, vo_path, offset):
    """Bagi durasi VO ke tiap scene sesuai panjang teks, kunci ke jeda hening terdekat."""
    lens = [max(len(s["vo"]), 8) for s in spec["scenes"]]
    total = sum(lens)
    gaps = silences(vo_path)
    bounds, acc = [], 0
    for L in lens[:-1]:
        acc += L
        guess = vo_dur * acc / total
        near = min(gaps, key=lambda g: abs(g - guess)) if gaps else guess
        bounds.append(near if abs(near - guess) < 1.6 else guess)
    edges = [0.0] + bounds + [vo_dur]
    scenes, t = [], 0.0
    for i, sc in enumerate(spec["scenes"]):
        d = edges[i + 1] - edges[i] + (offset if i == 0 else 0)
        d = max(d, sc.get("min", 3.2))
        if i == len(spec["scenes"]) - 1:
            d += sc.get("tail", 2.2)
        scenes.append((round(t, 2), round(t + d, 2)))
        t += d
    return scenes


def build_spec(spec, scenes):
    els, cues = [], [{"sfx": "hit", "t": 0.1}]
    for (s0, s1), sc in zip(scenes, spec["scenes"]):
        if s0 > 0:
            cues.append({"sfx": "whoosh", "t": s0})
        for el in sc["elements"]:
            e = dict(el)
            e["start"] = round(s0 + el.get("at", 0.1), 2)
            e["end"] = round(s1 - 0.05, 2)
            e.pop("at", None)
            for k in ("color", "accent"):
                if isinstance(e.get(k), list):
                    e[k] = tuple(e[k])
            els.append(e)
            if e["type"] in ("mascot", "card"):
                cues.append({"sfx": "pop", "t": e["start"] + 0.1})
            if e["type"] == "number":
                cues.append({"sfx": "ticks", "t": e["start"], "dur": e.get("count", 1.5)})
            if e["type"] == "logo":
                cues.append({"sfx": "ding", "t": e["start"]})
    return {"duration": scenes[-1][1], "accent": tuple(spec.get("accent", motion.TURQ)), "scenes": scenes,
            "elements": els, "cues": cues}


def check_mascot(built):
    ms = [e for e in built["elements"] if e["type"] == "mascot"]
    pct = [round(e["start"] / built["duration"] * 100) for e in ms]
    print(f"Maskot: {len(ms)}x di {pct}% durasi")
    if len(ms) != 2:
        raise RuntimeError("Aturan brand: maskot harus tepat 2 kali")


def produce(folder):
    src = ROOT / "produksi" / folder
    spec = json.loads((src / "spec.json").read_text(encoding="utf-8"))
    out = ROOT / "antrean" / folder
    out.mkdir(parents=True, exist_ok=True)
    work = ROOT / ".work" / folder
    work.mkdir(parents=True, exist_ok=True)

    script = " \n\n".join(lafal(s["vo"]) for s in spec["scenes"])
    vo = work / "vo.wav"
    engine_used = "gemini"
    try:
        tts_gemini(script, spec.get("voice", "Achernar"), spec.get("vo_style", "Bacakan dengan hangat, tenang, dan jelas."), vo)
    except Exception as e:  # noqa: BLE001
        print(f"Gemini TTS tidak tersedia ({e}). Pakai cadangan Piper.")
        tts_piper(script, vo)
        engine_used = "piper"

    offset = 0.3
    scenes = scene_times(spec, wav_duration(vo), vo, offset)
    built = build_spec(spec, scenes)
    built["vo_path"] = str(vo)
    built["vo_offset"] = offset
    check_mascot(built)
    motion.render(built, work)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(work / "video.mp4"), "-c:v", "copy", "-af",
                    "loudnorm=I=-16:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "160k", "-ar", "44100",
                    "-movflags", "+faststart", str(out / "video.mp4")], check=True)
    c = spec["cover"]
    cover.make_cover(str(out / "cover.jpg"), c["kicker"], c["lines"], c["pose"],
                     accent=tuple(c.get("accent", motion.RED)), badge=tuple(c["badge"]) if c.get("badge") else None)
    (out / "caption.txt").write_text(spec["caption"].strip() + "\n", encoding="utf-8")
    if spec.get("threads"):
        (out / "threads.txt").write_text(spec["threads"].strip() + "\n", encoding="utf-8")
    (out / "produksi.json").write_text(json.dumps({"vo": engine_used, "durasi": built["duration"], "scenes": scenes}, indent=1))
    # still QA
    for name, t in [("qa_awal", 1.5), ("qa_maskot1", None), ("qa_akhir", built["duration"] - 1)]:
        if t is None:
            t = next(e["start"] + 1.5 for e in built["elements"] if e["type"] == "mascot")
        motion.render_frame(built, t).resize((360, 640)).save(out / f"{name}.jpg", quality=80)
    print(f"Selesai: antrean/{folder} ({built['duration']:.1f} dtk, VO {engine_used})")


def main():
    targets = sys.argv[1:] or [p.name for p in sorted((ROOT / "produksi").iterdir())
                               if (p / "spec.json").exists() and not (ROOT / "antrean" / p.name / "video.mp4").exists()]
    if not targets:
        print("Tidak ada naskah baru untuk diproduksi.")
    for f in targets:
        produce(f)


if __name__ == "__main__":
    main()
