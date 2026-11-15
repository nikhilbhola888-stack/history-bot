import os, sys, json, time, random, asyncio, datetime, requests, textwrap
import edge_tts
from PIL import Image, ImageDraw, ImageFont
from moviepy.editor import (ImageClip, AudioFileClip, CompositeVideoClip,
                            concatenate_videoclips)
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

KIND = sys.argv[1] if len(sys.argv) > 1 else "short"   # short | long
UA = {"User-Agent": "HistoryBot/1.0 (personal YouTube project)"}
W, H = (720, 1280) if KIND == "short" else (1280, 720)
VOICE = os.getenv("VOICE", "en-US-GuyNeural")
MODELS = [m for m in [os.getenv("GEMINI_MODEL"), "gemini-flash-latest",
          "gemini-2.5-flash", "gemini-flash-lite-latest", "gemini-2.5-flash-lite"] if m]

def get_event():
    d = datetime.datetime.utcnow()
    url = f"https://api.wikimedia.org/feed/v1/wikipedia/en/onthisday/events/{d.month:02d}/{d.day:02d}"
    ev = requests.get(url, headers=UA, timeout=30).json()["events"]
    ev = [e for e in ev if e.get("pages")]
    e = random.choice(ev)
    return f"{e['year']}: {e['text']}"

def gen_script(event):
    if KIND == "short":
        spec = "7 scenes, each narration 1-2 punchy sentences (about 110 words total, under 50 seconds). Scene 1 must be a shocking 3-second hook."
    else:
        spec = "14 scenes, each narration about 55 words (about 6 minutes total). Start with a hook, build the story, end with why it still matters."
    prompt = f"""You write viral, accurate history YouTube scripts.
Event: {event}
Write {spec}
Only include facts you are certain are true. No made-up quotes.
Return ONLY JSON: {{"title": "...", "description": "...", "tags": ["..."],
"scenes": [{{"narration": "...", "caption": "max 8 words", "image_query": "2-4 word search for a historical photo/painting"}}]}}"""
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
                    print("Gemini OK with", model)
                    return data
                print(f"Gemini {model} round {rnd}: HTTP {r.status_code}", r.text[:200])
            except Exception as e:
                print(f"Gemini {model} round {rnd} failed:", repr(e))
            time.sleep(5)
        time.sleep(30)
    raise SystemExit("Gemini failed with all models")

def find_image(q):
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
                return Image.open("tmp_img").convert("RGB")
    except Exception as e:
        print("image fail", q, e)
    return None

def cover(img):
    img = img or Image.new("RGB", (W, H), (25, 25, 40))
    s = max(W / img.width, H / img.height) * 1.0
    img = img.resize((int(img.width * s) + 1, int(img.height * s) + 1))
    l, t = (img.width - W) // 2, (img.height - H) // 2
    return img.crop((l, t, l + W, t + H))

def caption_png(text, path):
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 44 if KIND == "short" else 40)
    except Exception:
        font = ImageFont.load_default()
    lines = textwrap.wrap(text, 22 if KIND == "short" else 40)
    ch = 70 * len(lines) + 30
    im = Image.new("RGBA", (W, ch), (0, 0, 0, 0))
    dr = ImageDraw.Draw(im)
    dr.rectangle((0, 0, W, ch), fill=(0, 0, 0, 140))
    y = 15
    for ln in lines:
        w = dr.textlength(ln, font=font)
        dr.text(((W - w) / 2, y), ln, font=font, fill="white")
        y += 70
    im.save(path)
    return ch

def scene_clip(i, sc):
    ap = f"v{i}.mp3"
    asyncio.run(edge_tts.Communicate(sc["narration"], VOICE).save(ap))
    audio = AudioFileClip(ap)
    d = audio.duration + 0.3
    cover(find_image(sc["image_query"])).save(f"s{i}.jpg")
    bg = (ImageClip(f"s{i}.jpg").set_duration(d)
          .resize(lambda t: 1 + 0.05 * t / d).set_position("center"))
    ch = caption_png(sc["caption"], f"c{i}.png")
    top = int(H * 0.68) if KIND == "short" else H - ch - 40
    cap = ImageClip(f"c{i}.png").set_duration(d).set_position((0, top))
    return CompositeVideoClip([bg, cap], size=(W, H)).set_duration(d).set_audio(audio)

def upload(path, meta):
    cid = os.environ["YT_CLIENT_ID"].strip()
    csec = os.environ["YT_CLIENT_SECRET"].strip()
    rtok = os.environ["YT_REFRESH_TOKEN"].strip()
    print("DEBUG id:", len(cid), cid[:12], cid[-30:])
    print("DEBUG secret:", len(csec), csec[:7])
    print("DEBUG token:", len(rtok), rtok[:4])
    creds = Credentials(None, refresh_token=rtok,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=cid, client_secret=csec,
        scopes=["https://www.googleapis.com/auth/youtube.upload"])
    yt = build("youtube", "v3", credentials=creds)
    title = meta["title"][:90] + (" #Shorts" if KIND == "short" else "")
    desc = (meta["description"] + "\n\nImages: Wikimedia Commons. Narration: AI voice. "
            "Script written with AI and based on Wikipedia.\n" + ("#Shorts #history" if KIND == "short" else "#history"))
    body = {"snippet": {"title": title, "description": desc, "tags": meta.get("tags", [])[:15], "categoryId": "27"},
            "status": {"privacyStatus": os.getenv("VISIBILITY", "public"),
                       "selfDeclaredMadeForKids": False, "containsSyntheticMedia": True}}
    req = yt.videos().insert(part="snippet,status", body=body,
                             media_body=MediaFileUpload(path, chunksize=-1, resumable=True))
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    print("Uploaded:", resp["id"])

if __name__ == "__main__":
    event = get_event()
    print("Event:", event)
    meta = gen_script(event)
    clips = [scene_clip(i, s) for i, s in enumerate(meta["scenes"])]
    video = concatenate_videoclips(clips, method="compose")
    out = f"{KIND}.mp4"
    video.write_videofile(out, fps=24, codec="libx264", audio_codec="aac",
                          preset="ultrafast", threads=2)
    upload(out, meta)
