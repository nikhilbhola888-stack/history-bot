import os, sys, json, time, glob, wave, random, asyncio, datetime, requests
import xml.etree.ElementTree as ET
import numpy as np
import edge_tts
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance, features
from moviepy.editor import (ImageClip, VideoFileClip, AudioFileClip, CompositeVideoClip,
                            CompositeAudioClip, concatenate_videoclips)
import moviepy.video.fx.all as vfx
import moviepy.audio.fx.all as afx
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

KIND = sys.argv[1] if len(sys.argv) > 1 else "short"   # short | long
IDX = int(sys.argv[2]) if len(sys.argv) > 2 else 0
UA = {"User-Agent": "ExplainerBot/1.0 (personal YouTube project)"}
W, H = (720, 1280) if KIND == "short" else (1280, 720)
VOICE = os.getenv("VOICE", "hi-IN-MadhurNeural")
PEXELS = os.getenv("PEXELS_API_KEY", "").strip()
MODELS = [m for m in [os.getenv("GEMINI_MODEL"), "gemini-flash-latest",
          "gemini-2.5-flash", "gemini-flash-lite-latest", "gemini-2.5-flash-lite"] if m]
STATE = {"last": None}

# ---------- Hindi font ----------
def find_font():
    c = glob.glob("/usr/share/fonts/**/NotoSansDevanagari*.ttf", recursive=True)
    c.sort(key=lambda p: ("Bold" not in p, len(p)))
    return c[0] if c else "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT = find_font()
LAYOUT = ImageFont.LAYOUT_RAQM if features.check("raqm") else ImageFont.LAYOUT_BASIC
SCRATCH = ImageDraw.Draw(Image.new("RGBA", (10, 10)))

def load_font(size):
    try:
        return ImageFont.truetype(FONT, size, layout_engine=LAYOUT)
    except Exception:
        return ImageFont.load_default()

def wrap_px(text, font, maxw):
    lines, cur = [], ""
    for w in text.split():
        t = (cur + " " + w).strip()
        if SCRATCH.textlength(t, font=font) <= maxw:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines

# ---------- topics ----------
FEEDS = [f"https://news.google.com/rss/headlines/section/topic/{t}?hl=hi&gl=IN&ceid=IN:hi"
         for t in ("BUSINESS", "TECHNOLOGY", "SCIENCE")]
EVERGREEN = ["पेट्रोल-डीज़ल के दाम क्यों बदलते हैं", "UPI कैसे काम करता है",
             "महंगाई क्या होती है", "GST क्या है और कैसे काम करता है",
             "रुपया डॉलर के मुकाबले क्यों गिरता है", "क्रेडिट स्कोर क्या होता है",
             "शेयर बाज़ार कैसे काम करता है", "रेपो रेट क्या है"]

def get_candidates():
    per = []
    for u in FEEDS:
        try:
            root = ET.fromstring(requests.get(u, headers=UA, timeout=30).content)
            items = [(i.findtext("title") or "").strip() for i in root.iter("item")]
            per.append([t for t in items if t][:12])
        except Exception as e:
            print("feed fail", e)
    mixed = []
    for k in range(12):
        for lst in per:
            if k < len(lst):
                mixed.append(lst[k])
    chunk = mixed[IDX * 6:(IDX + 1) * 6]
    if len(chunk) < 3:
        d = datetime.datetime.utcnow().toordinal()
        chunk = [EVERGREEN[(d + IDX) % len(EVERGREEN)], EVERGREEN[(d + IDX + 3) % len(EVERGREEN)]]
    return chunk

def gen_script(cands):
    n = 3 if KIND == "short" else 4
    if KIND == "short":
        spec = "7 scenes, each narration 1-2 short punchy sentences (about 100 words total, under 50 seconds). Scene 1 is a curiosity hook question in 3 seconds. Last scene: a one-line takeaway."
    else:
        spec = "12 scenes, each narration about 60 words (about 5 minutes total). Hook, then explain step by step from the basics, one simple imaginary example, then why it matters to a common person, then a short summary."
    prompt = f"""You are a Hindi YouTube explainer creator. Style: simple, friendly, like a smart elder brother explaining to common people in India.
Today's headlines (Hindi):
{chr(10).join('- ' + c for c in cands)}
Pick ONE headline that works as a "क्यों / कैसे / क्या है" explainer about economy, money, technology or science. Skip politics, elections, parties, crime, religion, celebrities and tragedies.
Write in simple everyday Hindi (Devanagari). Common English words (petrol, GST, UPI) are fine.
Structure: {spec}
Rules: Use ONLY facts stated in the headline plus well-established general knowledge of how the thing works. Do NOT state any specific number, price, percentage, date or name unless it appears in the headline. For examples use clearly labelled imaginary numbers ("मान लीजिए ..."). Never blame any party, government or person. Neutral tone. If the topic is about money or finance, the last scene ends with "यह सिर्फ जानकारी के लिए है, निवेश की सलाह नहीं।"
Each scene has "narration" (Hindi) and exactly {n} "shots" shown one after another while it is spoken. Shot "type" is "footage" (generic ENGLISH stock-video search such as "petrol pump fuel nozzle", "city traffic night", "indian rupee notes", "oil refinery", "person paying with smartphone") or "photo" (something findable on Wikimedia Commons). Mostly use footage.
Return ONLY JSON: {{"title": "catchy Hindi title under 70 characters", "description": "Hindi, 2-3 lines, mention the source headline", "tags": ["..."], "caption_title": "Hindi banner, max 4 words", "scenes": [{{"narration": "...", "shots": [{{"type": "footage", "query": "..."}}]}}]}}"""
    headers = {"x-goog-api-key": os.environ["GEMINI_API_KEY"].strip()}
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json"}}
    for rnd in range(1, 4):
        for model in MODELS:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            try:
                r = requests.post(url, headers=headers, json=body, timeout=180)
                if r.status_code == 200:
                    text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
                    text = text.strip().removeprefix("```json").removesuffix("```").strip()
                    data = json.loads(text)
                    print("Gemini OK with", model, "|", data.get("title"))
                    return data
                print(f"Gemini {model} round {rnd}: HTTP {r.status_code}", r.text[:200])
            except Exception as e:
                print(f"Gemini {model} round {rnd} failed:", repr(e))
            time.sleep(5)
        time.sleep(30)
    raise SystemExit("Gemini failed with all models")

# ---------- visuals ----------
def commons(q):
    try:
        r = requests.get("https://commons.wikimedia.org/w/api.php", headers=UA, timeout=30, params={
            "action": "query", "generator": "search", "gsrsearch": q, "gsrnamespace": 6,
            "gsrlimit": 8, "prop": "imageinfo", "iiprop": "url|mime", "iiurlwidth": 1280, "format": "json"})
        pages = list(r.json().get("query", {}).get("pages", {}).values())
        random.shuffle(pages)
        for p in pages:
            ii = p["imageinfo"][0]
            if ii["mime"] in ("image/jpeg", "image/png"):
                data = requests.get(ii["thumburl"], headers=UA, timeout=60).content
                open("tmp_img", "wb").write(data)
                im = Image.open("tmp_img").convert("RGB")
                if im.width >= 500:
                    return im
    except Exception as e:
        print("image fail", q, e)
    return None

def find_image(q):
    return commons(q) or commons(" ".join(q.split()[:2]))

def cover(img):
    s = max(W / img.width, H / img.height)
    img = img.resize((int(img.width * s) + 1, int(img.height * s) + 1))
    l, t = (img.width - W) // 2, (img.height - H) // 2
    return img.crop((l, t, l + W, t + H))

def compose_frame(img):
    if abs((img.width / img.height) / (W / H) - 1) < 0.25:
        return cover(img)
    bg = cover(img).filter(ImageFilter.GaussianBlur(30))
    bg = ImageEnhance.Brightness(bg).enhance(0.5)
    s = min(W / img.width, H / img.height)
    fg = img.resize((int(img.width * s), int(img.height * s)))
    bg.paste(fg, ((W - fg.width) // 2, (H - fg.height) // 2))
    return bg

def img_to_clip(img, sd, key):
    frame = np.array(compose_frame(img))
    zin = sum(map(ord, key)) % 2 == 0
    f = (lambda t: 1 + 0.07 * t / sd) if zin else (lambda t: 1.07 - 0.07 * t / sd)
    c = ImageClip(frame).set_duration(sd).resize(f).set_position("center")
    return CompositeVideoClip([c], size=(W, H)).set_duration(sd)

def footage_clip(q, sd, key):
    try:
        r = requests.get("https://api.pexels.com/videos/search", headers={"Authorization": PEXELS},
            params={"query": q, "orientation": "portrait" if KIND == "short" else "landscape",
                    "per_page": 10}, timeout=30)
        vids = r.json().get("videos", [])
        random.shuffle(vids)
        for v in vids:
            if v.get("duration", 0) < 3:
                continue
            files = [f for f in v["video_files"] if f.get("file_type") == "video/mp4"
                     and f.get("width") and 600 <= f["width"] <= 1920]
            if not files:
                continue
            f = min(files, key=lambda x: abs(x["width"] - W))
            path = f"f{key}.mp4"
            open(path, "wb").write(requests.get(f["link"], timeout=120).content)
            c = VideoFileClip(path).without_audio()
            s = max(W / c.w, H / c.h) * 1.01
            c = c.resize(s)
            c = c.crop(x_center=c.w / 2, y_center=c.h / 2, width=W, height=H)
            return c.fx(vfx.loop, duration=sd) if c.duration < sd else c.subclip(0, sd)
    except Exception as e:
        print("footage fail", q, e)
    return None

def make_shot(key, shot, sd):
    if isinstance(shot, str):
        shot = {"type": "footage", "query": shot}
    q = shot.get("query") or "city india"
    order = ["footage", "photo"] if shot.get("type") != "photo" else ["photo", "footage"]
    clip = None
    for kind in order:
        if kind == "footage":
            clip = footage_clip(q, sd, key) if PEXELS else None
        else:
            img = find_image(q)
            if img:
                STATE["last"] = img
                clip = img_to_clip(img, sd, key)
        if clip is not None:
            break
    if clip is None:
        img = STATE["last"] or Image.new("RGB", (W, H), (20, 40, 80))
        clip = img_to_clip(img, sd, key)
    return clip.fx(vfx.fadein, 0.2).fx(vfx.fadeout, 0.2)

# ---------- text overlays ----------
def banner_clip(text, dur):
    size = 50 if KIND == "short" else 40
    font = load_font(size)
    lines = wrap_px(text, font, W - 80)[:2]
    lh = size + 26
    bh = lh * len(lines) + 24
    im = Image.new("RGBA", (W, bh), (200, 30, 30, 235))
    dr = ImageDraw.Draw(im)
    y = 12
    for ln in lines:
        w = dr.textlength(ln, font=font)
        dr.text(((W - w) / 2, y), ln, font=font, fill="white")
        y += lh
    im.save("banner.png")
    return ImageClip("banner.png").set_duration(dur).set_position((0, 110 if KIND == "short" else 0))

def caption_png(text, path):
    size = 54 if KIND == "short" else 46
    font = load_font(size)
    lines = wrap_px(text, font, W - 100)
    lh = size + 30
    ch = lh * len(lines) + 20
    im = Image.new("RGBA", (W, ch), (0, 0, 0, 0))
    dr = ImageDraw.Draw(im)
    y = 10
    for ln in lines:
        w = dr.textlength(ln, font=font)
        dr.text(((W - w) / 2, y), ln, font=font, fill="white", stroke_width=5, stroke_fill="black")
        y += lh
    im.save(path)
    return ch

def caption_clips(text, speech, key):
    words = text.split()
    n = 3 if KIND == "short" else 5
    chunks = [" ".join(words[i:i + n]) for i in range(0, len(words), n)]
    total = sum(len(c) for c in chunks) or 1
    t, out = 0.0, []
    for k, c in enumerate(chunks):
        dur = speech * len(c) / total
        path = f"cap{key}_{k}.png"
        ch = caption_png(c, path)
        top = int(H * 0.64) if KIND == "short" else H - ch - 70
        out.append(ImageClip(path).set_start(t).set_duration(dur).set_position((0, top)))
        t += dur
    return out

# ---------- scene + music ----------
def build_scene(i, sc):
    ap = f"v{i}.mp3"
    asyncio.run(edge_tts.Communicate(sc["narration"], VOICE).save(ap))
    audio = AudioFileClip(ap)
    d = audio.duration + 0.35
    shots = sc.get("shots") or [{"type": "footage", "query": "city india"}]
    sd = d / len(shots)
    parts = [make_shot(f"{i}_{j}", s, sd) for j, s in enumerate(shots)]
    bg = concatenate_videoclips(parts, method="compose").set_duration(d)
    caps = caption_clips(sc["narration"], audio.duration, i)
    return CompositeVideoClip([bg] + caps, size=(W, H)).set_duration(d).set_audio(audio)

def make_music(nch, path="ambient.wav", seconds=30, sr=22050):
    t = np.arange(seconds * sr) / sr
    sig = np.zeros_like(t)
    for f, a in [(110, 0.5), (138.59, 0.35), (164.81, 0.35), (220, 0.25)]:
        lfo = 0.6 + 0.4 * np.sin(2 * np.pi * (1 / 12) * t + f)
        sig += a * np.sin(2 * np.pi * f * t) * lfo
    sig /= np.abs(sig).max()
    data = (sig * 0.8 * 32767).astype("<i2")
    if nch == 2:
        data = np.column_stack([data, data])
    with wave.open(path, "wb") as w:
        w.setnchannels(nch)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(data.tobytes())

# ---------- upload ----------
def upload(path, meta):
    creds = Credentials(None, refresh_token=os.environ["YT_REFRESH_TOKEN"].strip(),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["YT_CLIENT_ID"].strip(),
        client_secret=os.environ["YT_CLIENT_SECRET"].strip(),
        scopes=["https://www.googleapis.com/auth/youtube.upload"])
    yt = build("youtube", "v3", credentials=creds)
    title = meta["title"][:90] + (" #Shorts" if KIND == "short" else "")
    desc = (meta["description"] + "\n\nसूत्र: Google News हेडलाइन। फुटेज: Pexels।"
            "\nयह वीडियो AI की मदद से बनाया गया है। जानकारी सामान्य है, यह वित्तीय या कानूनी सलाह नहीं है।\n"
            + ("#Shorts #explained #hindi" if KIND == "short" else "#explained #hindi"))
    body = {"snippet": {"title": title, "description": desc, "tags": meta.get("tags", [])[:15],
                        "categoryId": "27", "defaultLanguage": "hi", "defaultAudioLanguage": "hi"},
            "status": {"privacyStatus": os.getenv("VISIBILITY", "public"),
                       "selfDeclaredMadeForKids": False, "containsSyntheticMedia": True}}
    req = yt.videos().insert(part="snippet,status", body=body,
                             media_body=MediaFileUpload(path, chunksize=-1, resumable=True))
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    print("Uploaded:", resp["id"])

if __name__ == "__main__":
    meta = gen_script(get_candidates())
    clips = [build_scene(i, s) for i, s in enumerate(meta["scenes"])]
    base = concatenate_videoclips(clips, method="compose")
    banner = banner_clip(meta.get("caption_title") or meta["title"], base.duration)
    speech = base.audio
    video = CompositeVideoClip([base, banner], size=(W, H)).set_duration(base.duration)
    make_music(speech.nchannels)
    music = (AudioFileClip("ambient.wav").fx(afx.audio_loop, duration=base.duration)
             .fx(afx.volumex, 0.07).fx(afx.audio_fadeout, 2))
    video = video.set_audio(CompositeAudioClip([speech, music]))
    out = f"{KIND}{IDX}.mp4"
    video.write_videofile(out, fps=24, codec="libx264", audio_codec="aac",
                          preset="ultrafast", threads=2)
    upload(out, meta)
