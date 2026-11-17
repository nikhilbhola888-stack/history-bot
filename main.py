import os, sys, re, json, time, glob, wave, math, html, random, asyncio, datetime
from io import BytesIO
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse, quote
import xml.etree.ElementTree as ET
import requests
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

SLOTS = ["short0", "short1", "short2", "short3", "long"]
SLOT = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] in SLOTS else "short0"
FORMAT = {"short0": "BREAKING", "short1": "EXPLAIN", "short2": "RAPID",
          "short3": "TREND", "long": "ROUNDUP"}[SLOT]
KIND = "long" if SLOT == "long" else "short"
W, H = (720, 1280) if KIND == "short" else (1280, 720)
NOW = time.time()
IST = datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)
DATE_STR = IST.strftime("%A, %d %B %Y")
RNG = random.Random(IST.toordinal() * 10 + SLOTS.index(SLOT))
VOICES = {"short0": "hi-IN-MadhurNeural", "short1": "hi-IN-SwaraNeural", "short2": "hi-IN-MadhurNeural",
          "short3": "hi-IN-SwaraNeural", "long": "hi-IN-MadhurNeural"}
VOICE = os.getenv("VOICE") or VOICES[SLOT]
RATE = os.getenv("RATE") or ("+25%" if KIND == "short" else "+15%")
PITCH = os.getenv("PITCH") or "+2Hz"
KEYS = {k: os.getenv(k, "").strip() for k in
        ("GEMINI_API_KEY", "TAVILY_API_KEY", "PEXELS_API_KEY", "PIXABAY_API_KEY")}
MODELS = [m for m in [os.getenv("GEMINI_MODEL"), "gemini-flash-latest", "gemini-flash-lite-latest"] if m]
UA = {"User-Agent": "NewsBot/2.0 (personal YouTube project)"}
STATE_FILE = "state.json"

# ---------------- look & feel ----------------
STYLES = [
    dict(bn=(170, 20, 20, 240), tag=(255, 200, 0, 255), tag_fg=(0, 0, 0), cap=(255, 255, 255), capst=(0, 0, 0),
         box=None, tint=(1.05, 1.0, 0.9), sat=0.85, shape="center", chord=[98, 146.83, 196, 293.66], acc=(200, 30, 30)),
    dict(bn=(15, 60, 150, 240), tag=(255, 255, 255, 255), tag_fg=(15, 60, 150), cap=(255, 235, 59), capst=(0, 0, 0),
         box=None, tint=(0.95, 1.0, 1.08), sat=0.9, shape="left", chord=[110, 164.81, 220, 329.63], acc=(20, 80, 190)),
    dict(bn=(25, 25, 25, 240), tag=(255, 193, 7, 255), tag_fg=(0, 0, 0), cap=(255, 255, 255), capst=(0, 0, 0),
         box=(0, 0, 0, 150), tint=(1.0, 1.0, 1.0), sat=0.9, shape="center", chord=[87.31, 130.81, 174.61, 261.63], acc=(255, 193, 7)),
    dict(bn=(0, 110, 70, 240), tag=(255, 255, 255, 255), tag_fg=(0, 110, 70), cap=(255, 255, 255), capst=(0, 50, 30),
         box=None, tint=(0.97, 1.04, 0.97), sat=0.95, shape="left", chord=[110, 138.59, 207.65, 277.18], acc=(0, 140, 90)),
    dict(bn=(190, 0, 60, 240), tag=(255, 214, 0, 255), tag_fg=(0, 0, 0), cap=(255, 255, 255), capst=(60, 0, 30),
         box=None, tint=(1.08, 0.98, 0.95), sat=1.0, shape="center", chord=[98, 123.47, 185, 246.94], acc=(210, 0, 70)),
]
STYLE = RNG.choice(STYLES)
_yy, _xx = np.mgrid[0:H, 0:W]
_d = np.sqrt(((_xx - W / 2) / (W / 2)) ** 2 + ((_yy - H / 2) / (H / 2)) ** 2)
VIG = np.clip(1.12 - 0.40 * _d ** 2, 0.6, 1.0)[..., None].astype("float32")
TINT = np.array(STYLE["tint"], dtype="float32")


def grade(frame):
    if frame.shape[0] != H or frame.shape[1] != W:
        return frame
    f = frame.astype("float32")
    g = f.mean(axis=2, keepdims=True)
    s = STYLE["sat"]
    f = (g * (1 - s) + f * s) * TINT * VIG
    return np.clip(f, 0, 255).astype("uint8")


# ---------------- Hindi font (checked, never empty boxes) ----------------
LAYOUT = ImageFont.LAYOUT_RAQM if features.check("raqm") else ImageFont.LAYOUT_BASIC
SCRATCH = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
FONT_URLS = [
    "https://github.com/google/fonts/raw/main/ofl/mukta/Mukta-ExtraBold.ttf",
    "https://github.com/google/fonts/raw/main/ofl/hind/Hind-Bold.ttf",
    "https://github.com/google/fonts/raw/main/ofl/notosansdevanagari/NotoSansDevanagari%5Bwdth%2Cwght%5D.ttf",
]


def _render(f, ch):
    im = Image.new("L", (90, 90), 0)
    ImageDraw.Draw(im).text((10, 5), ch, font=f, fill=255)
    return im.tobytes()


def font_ok(path):
    try:
        f = ImageFont.truetype(path, 48, layout_engine=LAYOUT)
        a = _render(f, "\u0915")
        return a != _render(f, "\u0378") and any(a)
    except Exception:
        return False


def find_font():
    os.makedirs("fonts", exist_ok=True)
    found = []
    for pat in ("**/*Mukta*Bold*.ttf", "**/*Hind*Bold*.ttf", "**/*Devanagari*Bold*.ttf",
                "**/*Devanagari*.ttf", "**/*Lohit*Deva*.ttf", "**/*Sarai*.ttf"):
        found += sorted(glob.glob("/usr/share/fonts/" + pat, recursive=True), key=len)
    for i, u in enumerate(FONT_URLS):
        p = f"fonts/dl{i}.ttf"
        try:
            r = requests.get(u, timeout=60)
            if r.status_code == 200 and len(r.content) > 20000:
                open(p, "wb").write(r.content)
                found.insert(0 if i == 0 else len(found), p)
        except Exception as e:
            print("font download fail", u[-30:], e)
    for p in found:
        if font_ok(p):
            return p
    print("WARNING: no working Devanagari font found; candidates:", found[:5])
    return found[0] if found else "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


FONT_PATH = find_font()
_FONTS = {}


def load_font(size):
    if size not in _FONTS:
        try:
            _FONTS[size] = ImageFont.truetype(FONT_PATH, size, layout_engine=LAYOUT)
        except Exception:
            _FONTS[size] = ImageFont.load_default()
    return _FONTS[size]


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
    return lines or [""]


_PNG = [0]


def png_clip(im):
    _PNG[0] += 1
    p = f"ov{_PNG[0]}.png"
    im.save(p)
    return ImageClip(p)


def text_block(text, size, maxw, fill, stroke_fill=(0, 0, 0), stroke=0, box=None, pad=18, max_lines=4):
    font = load_font(size)
    lines = wrap_px(text, font, maxw)[:max_lines]
    lh = int(size * 1.5)
    wmax = max(SCRATCH.textlength(l, font=font) for l in lines)
    im = Image.new("RGBA", (int(wmax) + 2 * pad + 2 * stroke + 4, lh * len(lines) + 2 * pad), (0, 0, 0, 0))
    dr = ImageDraw.Draw(im)
    if box:
        dr.rounded_rectangle((0, 0, im.width - 1, im.height - 1), radius=22, fill=box)
    y = pad
    for l in lines:
        w = dr.textlength(l, font=font)
        dr.text(((im.width - w) / 2, y), l, font=font, fill=fill, stroke_width=stroke, stroke_fill=stroke_fill)
        y += lh
    return im


# ---------------- state (no repeats across videos/days) ----------------
def load_state():
    try:
        return json.load(open(STATE_FILE))
    except Exception:
        return {"stories": [], "used": []}


ST = load_state()
USED = dict.fromkeys(ST.get("used", []))


def save_state(titles):
    ST["stories"] = [s for s in ST.get("stories", []) if NOW - s["ts"] < 5 * 86400]
    ST["stories"] += [{"t": t, "ts": NOW} for t in titles]
    ST["used"] = list(USED)[-900:]
    json.dump(ST, open(STATE_FILE, "w"), ensure_ascii=False)


def toks(s):
    return set(w for w in re.split(r"[\s\-\u2013\u2014|:,.\"'()\u2018\u2019\u201c\u201d]+", s.lower()) if len(w) > 1)


def sim(a, b):
    return len(a & b) / max(1, len(a | b))


def seen(title):
    t = toks(title)
    return any(sim(t, toks(s["t"])) > 0.45 for s in ST.get("stories", []) if NOW - s["ts"] < 72 * 3600)


# ---------------- Gemini ----------------
def speakable(t):
    return sum(1 for ch in str(t) if (ch.isalnum() or "\u0900" <= ch <= "\u097f") and ch not in "\u0964\u0965") >= 3


def call_gemini(prompt, tools=None, json_mode=True, rounds=3):
    headers = {"x-goog-api-key": KEYS["GEMINI_API_KEY"]}
    body = {"contents": [{"parts": [{"text": prompt}]}]}
    if json_mode and not tools:
        body["generationConfig"] = {"responseMimeType": "application/json"}
    if tools:
        body["tools"] = tools
    for rnd in range(1, rounds + 1):
        for model in MODELS:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            try:
                r = requests.post(url, headers=headers, json=body, timeout=240)
                if r.status_code == 200:
                    parts = r.json()["candidates"][0]["content"]["parts"]
                    txt = "".join(p.get("text", "") for p in parts)
                    if txt.strip():
                        print("Gemini OK with", model)
                        return txt
                else:
                    print(f"Gemini {model} round {rnd}: HTTP {r.status_code}", r.text[:150])
            except Exception as e:
                print(f"Gemini {model} round {rnd} failed:", repr(e))
            time.sleep(3)
        if rnd < rounds:
            time.sleep(30)
    return None


def parse_json(text):
    if not text:
        return None
    t = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        return json.loads(t)
    except Exception:
        m = re.search(r"\{.*\}", t, re.S)
        try:
            return json.loads(m.group(0)) if m else None
        except Exception:
            return None


# ---------------- fresh news ----------------
GN = "hl=hi&gl=IN&ceid=IN:hi"
GT = "https://news.google.com/rss/headlines/section/topic/"
FEEDS = [("TOP", f"https://news.google.com/rss?{GN}"),
         ("देश", f"{GT}NATION?{GN}"), ("दुनिया", f"{GT}WORLD?{GN}"), ("कारोबार", f"{GT}BUSINESS?{GN}"),
         ("तकनीक", f"{GT}TECHNOLOGY?{GN}"), ("खेल", f"{GT}SPORTS?{GN}"),
         ("मनोरंजन", f"{GT}ENTERTAINMENT?{GN}"), ("सेहत", f"{GT}HEALTH?{GN}"), ("विज्ञान", f"{GT}SCIENCE?{GN}"),
         ("ताज़ा", f"https://news.google.com/rss/search?q={quote('भारत when:1d')}&{GN}"),
         ("BBC हिंदी", "https://feeds.bbci.co.uk/hindi/rss.xml")]


def clean(t):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(t or ""))).strip()


def fetch_feed(label, url):
    out = []
    try:
        root = ET.fromstring(requests.get(url, headers=UA, timeout=25).content)
        for it in root.iter("item"):
            title, src = clean(it.findtext("title")), clean(it.findtext("source"))
            if not title:
                continue
            if src and title.endswith(" - " + src):
                title = title[:-len(src) - 3]
            elif not src and " - " in title:
                title, src = title.rsplit(" - ", 1)
            try:
                ts = parsedate_to_datetime(it.findtext("pubDate")).timestamp()
            except Exception:
                ts = NOW - 8 * 3600
            desc = "" if label in ("TOP", "देश", "दुनिया", "कारोबार", "तकनीक", "खेल", "मनोरंजन", "सेहत", "विज्ञान", "ताज़ा") \
                else clean(it.findtext("description"))[:400]
            out.append({"title": title, "src": src or label, "ts": ts, "desc": desc, "label": label, "tk": toks(title)})
    except Exception as e:
        print("feed fail", label, repr(e)[:80])
    return out


def collect_news():
    items = []
    for label, url in FEEDS:
        items += fetch_feed(label, url)
    items = [i for i in items if NOW - i["ts"] < 40 * 3600 and not seen(i["title"])]
    items.sort(key=lambda i: -i["ts"])
    out = []
    for i in items:
        if not any(sim(i["tk"], o["tk"]) > 0.6 for o in out):
            out.append(i)
    print("fresh headlines:", len(out))
    return out


def get_trends():
    for u in ("https://trends.google.com/trending/rss?geo=IN",
              "https://trends.google.com/trends/trendingsearches/daily/rss?geo=IN"):
        try:
            root = ET.fromstring(requests.get(u, headers=UA, timeout=25).content)
            out = []
            for it in root.iter("item"):
                term, traffic, news = clean(it.findtext("title")), "", []
                for ch in it:
                    tag = ch.tag.split("}")[-1]
                    if tag == "approx_traffic":
                        traffic = clean(ch.text)
                    if tag == "news_item":
                        for c in ch:
                            if c.tag.split("}")[-1] == "news_item_title":
                                news.append(clean(c.text))
                if term and not seen(term):
                    out.append({"term": term, "traffic": traffic, "news": news[:3]})
            if out:
                return out
        except Exception as e:
            print("trends fail", repr(e)[:80])
    return []


INSTR = {
    "BREAKING": "the single biggest breaking story, strongly prefer the newest (last 12 hours)",
    "EXPLAIN": "ONE important story of the last 24 hours that viewers need explained (policy, law, economy, science, tech, health or world affairs), not a pure crime or accident story",
    "RAPID": "FIVE different stories from five different areas (nation, world, business, sports, tech/science/entertainment), newest and most important",
    "TREND": "the single most newsworthy story",
    "ROUNDUP": "the FIVE most important stories of the day in a good mix of areas, at most one crime story",
}


def pick_stories(cands, n):
    cands = cands[:70]
    lines = [f"[{i}] ({(NOW - c['ts']) / 3600:.0f}h ago{', TOP' if c['label'] == 'TOP' else ''}, {c['src']}) {c['title']}"
             for i, c in enumerate(cands)]
    prompt = (f"Today is {DATE_STR} (IST). Fresh Hindi news headlines from India:\n" + "\n".join(lines) +
              f"\n\nChoose {INSTR[FORMAT]}. Skip adult content, celebrity gossip, unverified rumours, suicide details "
              f"and details of sexual violence. Choose exactly {n} distinct indices.\n"
              'Return ONLY JSON: {"picks": [index, ...]}')
    data = parse_json(call_gemini(prompt)) or {}
    idx = []
    for i in data.get("picks", []):
        if isinstance(i, int) and 0 <= i < len(cands) and i not in idx:
            idx.append(i)
    for i, c in enumerate(cands):
        if len(idx) >= n:
            break
        if i not in idx:
            idx.append(i)
    return [cands[i] for i in idx[:n]]


def select_for_slot():
    if FORMAT == "TREND":
        tr = get_trends()[:15]
        if tr:
            lines = [f"[{i}] {t['term']} ({t['traffic']}) - {' | '.join(t['news'])}" for i, t in enumerate(tr)]
            data = parse_json(call_gemini(
                "Google Trends India, searches trending right now:\n" + "\n".join(lines) +
                "\n\nChoose the ONE that is best for an informative news video (skip adult content, gossip and "
                'tragedies). Return ONLY JSON: {"pick": index}')) or {}
            i = data.get("pick", 0)
            t = tr[i if isinstance(i, int) and 0 <= i < len(tr) else 0]
            return [{"title": f"{t['term']} (गूगल पर ट्रेंड कर रहा है) " + " | ".join(t["news"]),
                     "src": "Google Trends", "ts": NOW, "desc": " | ".join(t["news"]), "label": "TREND", "term": t["term"]}]
    cands = collect_news()
    if not cands:
        raise SystemExit("No fresh headlines found")
    return pick_stories(cands, 5 if FORMAT in ("RAPID", "ROUNDUP") else 1)


def tavily(q):
    if not KEYS["TAVILY_API_KEY"]:
        return []
    try:
        r = requests.post("https://api.tavily.com/search", headers={"Authorization": "Bearer " + KEYS["TAVILY_API_KEY"]},
                          json={"query": q, "topic": "news", "days": 3, "max_results": 6}, timeout=40)
        if r.status_code == 200:
            return r.json().get("results", [])
        print("tavily", r.status_code, r.text[:120])
    except Exception as e:
        print("tavily fail", repr(e)[:80])
    return []


def grounded(q):
    txt = call_gemini(f"Today is {DATE_STR}. Use Google Search for reliable reporting from the last 48 hours on: {q}\n"
                      "Write a factual BRIEF in English: what happened, who, where, when, confirmed numbers, who said what "
                      "(attributed), short background, what is disputed or unconfirmed, outlets relied on. No opinions. "
                      "If you cannot find reliable reporting reply exactly NO_RELIABLE_INFO.",
                      tools=[{"google_search": {}}], json_mode=False, rounds=1)
    return None if not txt or "NO_RELIABLE_INFO" in txt else txt


def brief_for(c, light=False):
    parts, srcs = [f"HEADLINE ({c['src']}, {(NOW - c['ts']) / 3600:.0f}h ago): {c['title']}"], [c["src"]]
    if c.get("desc"):
        parts.append(f"RSS SUMMARY: {c['desc']}")
    if not light:
        res = tavily(c.get("term") or c["title"])
        for r in res[:5]:
            dom = urlparse(r.get("url", "")).netloc.replace("www.", "")
            txt = clean(r.get("content", ""))[:650]
            if txt:
                parts.append(f"SOURCE {dom} ({r.get('published_date', 'recent')}): {txt}")
                srcs.append(dom)
        if not res:
            g = grounded(c.get("term") or c["title"])
            if g:
                parts.append("SEARCH NOTES: " + g[:1800])
    return "\n".join(parts), srcs


# ---------------- script ----------------
RULES = """STRICT EDITORIAL RULES:
- Use ONLY facts present in the BRIEFS. Never invent numbers, names, dates, quotes or details. If a brief is thin, say less.
- Attribute every claim: "रिपोर्ट्स के मुताबिक", "पुलिस के अनुसार", "सरकार का कहना है", "विपक्ष का आरोप है".
- Crime: say "आरोप" and "आरोपी"; never state guilt before a court verdict; say "जांच जारी है" where relevant.
- Politics: neutral and balanced; each side's position is that side's own claim; no praise or attack on any party or leader.
- Religion and communities: factual only; never generalise; no inflammatory words; never guess motives.
- Tragedies and crime: respectful, no graphic detail; never name minors or victims of sexual violence; no suicide methods.
- If something is unconfirmed say "अभी इसकी पुष्टि नहीं हुई". No legal, medical or financial advice. No misleading clickbait."""

SPEC = {
    "BREAKING": "7 scenes, about 100 words in total (under 45 seconds spoken). Scene 1 is the hook that states the news in under 12 words. Then: what happened, key confirmed facts, who said what (attributed), one line of background, what is unconfirmed or what happens next. The last scene asks viewers a question to answer in the comments.",
    "EXPLAIN": "7 scenes, about 110 words in total. Scene 1: a curiosity question about the story ('क्यों' or 'कैसे'). Then: what happened, simple background from the basics, one easy everyday analogy, why it matters to an ordinary person, what happens next. The last scene asks viewers a question to answer in the comments.",
    "RAPID": "Scene 1 is an intro of at most 14 words: today's 5 biggest news in under 60 seconds. Then EXACTLY 5 scenes, one per story in the order given, each ONE sentence of at most 16 words with attribution; the label of each is like 'खबर 1: ...' (max 5 words). No other scenes.",
    "TREND": "6 scenes, about 100 words in total. Scene 1 is a hook in the style 'आज पूरा देश गूगल पर यही सर्च कर रहा है' naming the trend. Then: what it is, why it is trending today (only from the notes), key facts people should know, what to watch next. The last scene asks viewers a question to answer in the comments.",
    "ROUNDUP": "EXACTLY 12 scenes. Scene 1: greeting and a quick tease of the 5 stories (about 45 words). Then for EACH of the 5 stories in order, 2 scenes of about 60-70 words each: scene A = what happened plus key facts with attribution; scene B = background, what each side says, what is unconfirmed, what happens next. Set 'chapter' (short Hindi chapter title) on the first scene of each story. Scene 12 is a closing summary of about 40 words ending with a question for the comments. About 5 minutes spoken in total.",
}
HOOKS = ["start with a surprising question", "start with the most shocking confirmed fact", "start with a direct bulletin line like a TV anchor",
         "start with what this means for the viewer's pocket or daily life", "start with a number or time from the brief"]
TITLE_STYLES = ["a question title", "a title with the key number or fact", "an alert-style title", "a plain bulletin title", "a 'what happened and why' title"]
CTA = {"short": "ऐसी ही ताज़ा खबरों के लिए चैनल को सब्सक्राइब करें और वीडियो को लाइक करें।",
       "long": "अगर यह वीडियो आपके काम की लगी, तो इसे लाइक करें, दोस्तों के साथ शेयर करें, चैनल को सब्सक्राइब करें और बेल आइकन दबाना न भूलें, ताकि हर बड़ी खबर सबसे पहले आप तक पहुंचे। धन्यवाद।"}


def gen_script(stories, briefs):
    n = 2 if FORMAT == "RAPID" else (5 if KIND == "long" else 3)
    blocks = "\n\n".join(f"=== STORY {i + 1} ===\n{b}" for i, b in enumerate(briefs))
    prompt = f"""You are the lead scriptwriter of a popular Hindi YouTube news channel for Indian viewers. Date: {DATE_STR} (IST).
Voice: HIGH-ENERGY, gripping and fast, like a top YouTube news presenter, but always trustworthy. Simple everyday spoken Hindi in Devanagari (common English words like GST, UPI, FIR are fine). Very short punchy sentences (max 12 words), direct address ("आप", "सोचिए"), contrast and suspense. The first sentence must grab attention in 3 seconds (a shocking confirmed fact, a number or a bold question). Create curiosity gaps ("लेकिन असली बात आगे है") and pay them off. Every scene adds a NEW fact: no slow introductions, no repeating the headline, no filler like "तो दोस्तों" more than once. Never exaggerate beyond the BRIEFS.

{blocks}

{RULES}

FORMAT: {SPEC[FORMAT]}
Opening hook style: {RNG.choice(HOOKS)}.
Title style: {RNG.choice(TITLE_STYLES)} (factual, max 75 characters, contains the main keyword).
Each scene has "narration", "label" (on-screen label in Hindi, max 5 words) and exactly {n} "shots". Only if the FORMAT asks for chapters, also add "chapter" to those scenes.
SHOT RULES: the shots of a scene are shown one after another while the narration is spoken, in order, and each shot must visually match the part of the narration spoken at that moment. Every shot in the whole video must show a DIFFERENT subject (never repeat). Each shot has "type" and "q" (list of 3 English search queries, from most specific to most generic):
- "wiki": a specific named place, building, institution, organisation, company, product, map, event or landmark that exists on Wikipedia/Commons (e.g. "Reserve Bank of India headquarters").
- "stock_video": generic cinematic footage of the situation (e.g. "police lights at night", "crowd protest street", "stock market screen red", "flood water village aerial").
- "stock_photo": a generic photo of the theme (e.g. "petrol pump nozzle", "farmer in field India").
Mix the types. For a named real person (only when the story is not about a crime, accident or dispute) the FIRST query must be their exact English Wikipedia name (e.g. "Piyush Goyal") and the LAST query must be a generic non-person query (e.g. "indian minister press conference"). Never show or name a real person in crime, accident or dispute stories. Nothing graphic or disturbing.
Also return: "thumb_text" (2-4 punchy Hindi words for the thumbnail, factual), "thumb_query" (English search for a thumbnail photo of the main subject), "caption_title" (Hindi, max 6 words), "tag" (one of: ब्रेकिंग, ताज़ा खबर, समझिए, ट्रेंडिंग, आज की खबरें), "description_lines" (2-3 short Hindi lines summarising the video; the first is a hook), "hashtags" (6 hashtags, Hindi or English, that Indians search for about this exact story), "tags" (10 search keywords), "sources" (outlet names from the briefs).
Return ONLY JSON of this form: {{"title": "...", "thumb_text": "...", "thumb_query": "...", "caption_title": "...", "tag": "...", "description_lines": ["..."], "hashtags": ["#..."], "tags": ["..."], "sources": ["..."], "scenes": [{{"narration": "...", "label": "...", "shots": [{{"type": "wiki", "q": ["...", "...", "..."]}}]}}]}}"""
    for _ in range(3):
        data = parse_json(call_gemini(prompt))
        if data and isinstance(data.get("scenes"), list) and len(data["scenes"]) >= 3:
            print("Title:", data.get("title"))
            return data
    raise SystemExit("Script generation failed")


def fact_check(briefs, script):
    prompt = ("BRIEFS:\n" + "\n\n".join(briefs) + "\n\nSCRIPT (JSON):\n" + json.dumps(script, ensure_ascii=False) +
              "\n\nYou are a strict news fact-checker and standards editor. Edit the script: remove or soften every claim, number, "
              "name or quote not supported by the BRIEFS; make sure allegations are attributed and never stated as fact; remove loaded, "
              "one-sided or inflammatory wording; keep it neutral toward every party, religion and community; remove graphic detail. "
              "Keep exactly the same JSON structure, the same number of scenes and the same shots. Return ONLY the JSON.")
    new = parse_json(call_gemini(prompt, rounds=1))
    try:
        if new and len(new["scenes"]) == len(script["scenes"]):
            for a, b in zip(new["scenes"], script["scenes"]):
                if not speakable(a.get("narration", "")):
                    a["narration"] = b.get("narration", "")
                if not a.get("shots"):
                    a["shots"] = b.get("shots")
                if b.get("chapter") and not a.get("chapter"):
                    a["chapter"] = b["chapter"]
            for k in script:
                new.setdefault(k, script[k])
            print("Fact-check applied")
            return new
    except Exception as e:
        print("fact-check skipped:", repr(e)[:80])
    return script


# ---------------- visuals (many different, matching, never repeated) ----------------
_CACHE = {}


def jget(url, params=None, headers=None):
    k = (url, json.dumps(params, sort_keys=True), json.dumps(headers, sort_keys=True))
    if k in _CACHE:
        return _CACHE[k]
    for t in range(2):
        try:
            r = requests.get(url, params=params, headers=headers or UA, timeout=30)
            if r.status_code == 200:
                _CACHE[k] = r.json()
                return _CACHE[k]
            if r.status_code in (429, 503):
                time.sleep(2 + 3 * t)
            else:
                break
        except Exception as e:
            print("jget fail", url[8:40], repr(e)[:60])
            time.sleep(1)
    return None


def img_from(url):
    try:
        r = requests.get(url, headers=UA, timeout=60)
        r.raise_for_status()
        im = Image.open(BytesIO(r.content))
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
            bg.alpha_composite(im)
            im = bg
        im = im.convert("RGB")
        return im if im.width >= 600 and im.height >= 380 else None
    except Exception:
        return None


def _pages(j):
    return sorted(((j or {}).get("query") or {}).get("pages", {}).values(), key=lambda p: p.get("index", 99))


_STOP = {"of", "the", "and", "in", "for", "a", "an", "on", "at", "to"}


def _words(s):
    return set(re.findall(r"[a-z0-9]+", re.sub(r"\(.*?\)", " ", s.lower()))) - _STOP


def title_ok(q, title):
    a, b = _words(q), _words(title)
    return bool(b) and len(a & b) / len(b) >= 0.6


def file_ok(q, title):
    a, b = _words(q), _words(title)
    return bool(a) and len(a & b) / len(a) >= 0.6


def s_wiki(q):
    j = jget("https://en.wikipedia.org/w/api.php", {"action": "query", "generator": "search", "gsrsearch": q, "gsrlimit": 6,
                                                     "prop": "pageimages", "piprop": "thumbnail", "pithumbsize": 1400, "format": "json"})
    for p in _pages(j):
        th = (p.get("thumbnail") or {}).get("source")
        if th and th not in USED and title_ok(q, p.get("title", "")):
            im = img_from(th)
            if im:
                return ("img", im, th)


def s_commons(q):
    j = jget("https://commons.wikimedia.org/w/api.php", {"action": "query", "generator": "search", "gsrsearch": q, "gsrnamespace": 6,
                                                          "gsrlimit": 10, "prop": "imageinfo", "iiprop": "url|mime", "iiurlwidth": 1400, "format": "json"})
    for p in _pages(j):
        ii = (p.get("imageinfo") or [{}])[0]
        u = ii.get("thumburl")
        if u and ii.get("mime") in ("image/jpeg", "image/png") and u not in USED and file_ok(q, p.get("title", "")):
            im = img_from(u)
            if im:
                return ("img", im, u)


def s_pixabay_photo(q):
    if not KEYS["PIXABAY_API_KEY"]:
        return None
    j = jget("https://pixabay.com/api/", {"key": KEYS["PIXABAY_API_KEY"], "q": q[:90], "image_type": "photo", "per_page": 20,
                                           "safesearch": "true", "orientation": "vertical" if KIND == "short" else "horizontal"})
    for h in (j or {}).get("hits", []):
        uid = f"pxb{h['id']}"
        if uid not in USED:
            im = img_from(h.get("largeImageURL") or h["webformatURL"])
            if im:
                return ("img", im, uid)


def s_pexels_photo(q):
    if not KEYS["PEXELS_API_KEY"]:
        return None
    j = jget("https://api.pexels.com/v1/search", {"query": q, "per_page": 15, "orientation": "portrait" if KIND == "short" else "landscape"},
             {"Authorization": KEYS["PEXELS_API_KEY"], **UA})
    for p in (j or {}).get("photos", []):
        uid = f"pex{p['id']}"
        if uid not in USED:
            im = img_from(p["src"].get("large2x") or p["src"]["large"])
            if im:
                return ("img", im, uid)


def _dl_video(url, uid):
    try:
        path = f"vid_{uid}.mp4"
        open(path, "wb").write(requests.get(url, timeout=120).content)
        return path
    except Exception as e:
        print("video dl fail", repr(e)[:60])


def s_pexels_video(q):
    if not KEYS["PEXELS_API_KEY"]:
        return None
    j = jget("https://api.pexels.com/videos/search", {"query": q, "per_page": 12, "orientation": "portrait" if KIND == "short" else "landscape"},
             {"Authorization": KEYS["PEXELS_API_KEY"], **UA})
    for v in (j or {}).get("videos", []):
        uid = f"pev{v['id']}"
        if uid in USED or v.get("duration", 0) < 3:
            continue
        fl = [f for f in v["video_files"] if f.get("file_type") == "video/mp4" and f.get("width") and 500 <= f["width"] <= 1920]
        if fl:
            path = _dl_video(min(fl, key=lambda x: abs(x["width"] - W))["link"], uid)
            if path:
                return ("vid", path, uid)


def s_pixabay_video(q):
    if not KEYS["PIXABAY_API_KEY"]:
        return None
    j = jget("https://pixabay.com/api/videos/", {"key": KEYS["PIXABAY_API_KEY"], "q": q[:90], "per_page": 15, "safesearch": "true"})
    for h in (j or {}).get("hits", []):
        uid = f"pbv{h['id']}"
        vd = h.get("videos", {})
        vid = vd.get("medium") or vd.get("small") or vd.get("large")
        if uid in USED or h.get("duration", 0) < 3 or not vid or not vid.get("url"):
            continue
        path = _dl_video(vid["url"], uid)
        if path:
            return ("vid", path, uid)


SRC = {"wiki": [s_wiki, s_commons], "photo": [s_pixabay_photo, s_pexels_photo], "video": [s_pexels_video, s_pixabay_video]}
CHAIN = {"wiki": ["wiki", "photo", "video"], "stock_photo": ["photo", "wiki", "video"], "stock_video": ["video", "photo", "wiki"]}


def cover_to(img, w, h):
    s = max(w / img.width, h / img.height)
    img = img.resize((int(img.width * s) + 1, int(img.height * s) + 1))
    l, t = (img.width - w) // 2, (img.height - h) // 2
    return img.crop((l, t, l + w, t + h))


def compose_frame(img):
    if abs((img.width / img.height) / (W / H) - 1) < 0.3:
        return cover_to(img, W, H)
    bg = ImageEnhance.Brightness(cover_to(img, W, H).filter(ImageFilter.GaussianBlur(28))).enhance(0.5)
    s = min(W / img.width, H / img.height)
    fg = img.resize((int(img.width * s), int(img.height * s)))
    bg.paste(fg, ((W - fg.width) // 2, (H - fg.height) // 2))
    return bg


def img_to_clip(img, sd):
    mode = RNG.choice(["in", "out", "left", "right"])
    fits = abs((img.width / img.height) / (W / H) - 1) < 0.3
    if fits and mode in ("left", "right"):
        arr = np.array(cover_to(img, int(W * 1.18), int(H * 1.18)))
        dx = W * 0.18
        pos = (lambda t: (-dx * t / sd, -H * 0.09)) if mode == "left" else (lambda t: (-dx + dx * t / sd, -H * 0.09))
        c = ImageClip(arr).set_duration(sd).set_position(pos)
    else:
        f = (lambda t: 1 + 0.08 * t / sd) if mode in ("in", "left") else (lambda t: 1.08 - 0.08 * t / sd)
        c = ImageClip(np.array(compose_frame(img))).set_duration(sd).resize(f).set_position("center")
    return CompositeVideoClip([c], size=(W, H)).set_duration(sd)


def vid_to_clip(path, sd):
    c = VideoFileClip(path).without_audio()
    c = c.resize(max(W / c.w, H / c.h) * 1.01)
    c = c.crop(x_center=c.w / 2, y_center=c.h / 2, width=W, height=H)
    if c.duration < sd:
        return c.fx(vfx.loop, duration=sd)
    st = RNG.uniform(0, c.duration - sd)
    return c.subclip(st, st + sd)


def card_clip(text, sd):
    a, b = np.array(STYLE["acc"], dtype="float32"), np.array((15, 15, 25), dtype="float32")
    g = np.linspace(0, 1, H)[:, None, None]
    arr = (a * (1 - g) * 0.8 + b * g).astype("uint8") * np.ones((1, W, 1), dtype="uint8")
    im = Image.fromarray(arr.astype("uint8")).convert("RGBA")
    tb = text_block(text, 70 if KIND == "short" else 64, W - 140, (255, 255, 255), (0, 0, 0), 5)
    im.alpha_composite(tb, ((W - tb.width) // 2, (H - tb.height) // 2))
    return img_to_clip(im.convert("RGB"), sd)


def get_shot(key, shot, sd, label):
    typ = shot.get("type", "stock_photo")
    qs = shot.get("q") or shot.get("query") or []
    qs = [qs] if isinstance(qs, str) else qs
    qs = [q for q in qs if isinstance(q, str) and q.strip()][:3] or ["india news"]
    for gi, grp in enumerate(CHAIN.get(typ, CHAIN["stock_photo"])):
        for q in (qs if gi == 0 else [qs[-1]]):
            for fn in SRC[grp]:
                res = fn(q)
                if not res:
                    continue
                try:
                    clip = img_to_clip(res[1], sd) if res[0] == "img" else vid_to_clip(res[1], sd)
                except Exception as e:
                    print("clip fail", repr(e)[:80])
                    continue
                USED[res[2]] = None
                return clip.fl_image(grade).fx(vfx.fadein, 0.15).fx(vfx.fadeout, 0.15)
            time.sleep(0.1)
    print("no visual found for", qs[0], "-> card")
    return card_clip(label or "ताज़ा खबर", sd).fl_image(grade)


def find_still(queries):
    for q in queries:
        for fn in (s_wiki, s_commons, s_pixabay_photo, s_pexels_photo):
            res = fn(q)
            if res:
                return res[1]
    return None


# ---------------- overlays ----------------
def banner_clip(tag, label, dur):
    S = STYLE
    sz = 44 if KIND == "short" else 36
    tb = text_block(label, sz, W - 110, (255, 255, 255), box=S["bn"], pad=20, max_lines=2)
    chip = text_block(tag, 30 if KIND == "short" else 26, W, S["tag_fg"], box=S["tag"], pad=10, max_lines=1)
    out = Image.new("RGBA", (max(tb.width, chip.width + 20), tb.height + chip.height - 8), (0, 0, 0, 0))
    out.alpha_composite(tb, (0, chip.height - 8))
    out.alpha_composite(chip, (0, 0))
    x = 24 if S["shape"] == "left" else (W - out.width) // 2
    y = 125 if KIND == "short" else 28
    return png_clip(out).set_duration(dur).set_position((x, y))


def caption_clips(text, speech, last_end=0):
    S = STYLE
    words = text.split()
    n = 3 if KIND == "short" else 5
    chunks = [" ".join(words[i:i + n]) for i in range(0, len(words), n)]
    total = sum(len(c) for c in chunks) or 1
    t, out = 0.0, []
    for c in chunks:
        dur = speech * len(c) / total
        im = text_block(c, 56 if KIND == "short" else 46, W - 120, S["cap"], S["capst"], 6, S["box"], pad=14, max_lines=3)
        top = int(H * 0.58) if KIND == "short" else H - im.height - 80
        out.append(png_clip(im).set_start(t).set_duration(dur).set_position(((W - im.width) // 2, top)))
        t += dur
    return out


def hook_card(text, dur):
    im = text_block(text, 92 if KIND == "short" else 84, W - 100, (255, 255, 255), (0, 0, 0), 7, STYLE["bn"], pad=26, max_lines=3)
    return (png_clip(im).set_duration(min(2.6, dur)).set_position(((W - im.width) // 2, int(H * 0.30)))
            .fx(vfx.fadeout, 0.3))


def cta_card(d):
    im = text_block("सब्सक्राइब करें", 66, W - 120, (255, 255, 255), box=(215, 20, 20, 255), pad=22, max_lines=1)
    sub = text_block("लाइक  •  शेयर  •  बेल आइकन", 38, W - 100, (255, 255, 255), (0, 0, 0), 4, max_lines=1)
    out = Image.new("RGBA", (max(im.width, sub.width), im.height + sub.height + 6), (0, 0, 0, 0))
    out.alpha_composite(im, ((out.width - im.width) // 2, 0))
    out.alpha_composite(sub, ((out.width - sub.width) // 2, im.height + 6))
    top = int(H * 0.36) if KIND == "short" else int(H * 0.30)
    return png_clip(out).set_start(max(0, d - 3.8)).set_duration(min(3.8, d)).set_position(((W - out.width) // 2, top))


def source_img(sources):
    txt = "स्रोत: " + ", ".join(dict.fromkeys(sources))[:90]
    return text_block(txt, 26 if KIND == "short" else 24, W - 80, (255, 255, 255, 235), (0, 0, 0), 3, pad=6, max_lines=1)


# ---------------- scenes, music, thumbnail ----------------
FIRST = {"frame": None}


def tts(text, path):
    text = re.sub(r"[#*_`\u2022|<>]+", " ", str(text)).strip()
    if not speakable(text):
        raise ValueError("empty narration")
    for rate, pitch in ((RATE, PITCH), (RATE, "+0Hz"), ("+0%", "+0Hz")):
        try:
            asyncio.run(edge_tts.Communicate(text, VOICE, rate=rate, pitch=pitch).save(path))
            return
        except Exception as e:
            print("tts retry", rate, pitch, repr(e)[:80])
            time.sleep(3)
    raise ValueError("TTS failed for: " + text[:40])


def build_scene(i, sc, tag, meta, src_im, last):
    ap = f"v{i}.mp3"
    tts(sc["narration"], ap)
    audio = AudioFileClip(ap)
    d = audio.duration + 0.08
    shots = sc.get("shots") or [{"type": "stock_photo", "q": ["india news"]}]
    sd = d / len(shots)
    parts = [get_shot(f"{i}_{j}", s, sd, sc.get("label")) for j, s in enumerate(shots)]
    if i == 0:
        try:
            FIRST["frame"] = parts[0].get_frame(min(0.5, sd / 2))
        except Exception:
            pass
    bg = concatenate_videoclips(parts, method="compose").set_duration(d)
    layers = [bg, banner_clip(tag, sc.get("label") or meta.get("caption_title") or "ताज़ा खबर", d)]
    layers += caption_clips(sc["narration"], audio.duration)
    layers.append(png_clip(src_im).set_duration(d).set_position(((W - src_im.width) // 2, int(H * 0.74) if KIND == "short" else H - 48)))
    if i == 0 and KIND == "short" and meta.get("thumb_text"):
        layers.append(hook_card(meta["thumb_text"], d))
    if last:
        layers.append(cta_card(d))
    sfx = AudioFileClip(make_whoosh(audio.nchannels)).fx(afx.volumex, 0.22)
    scene_audio = CompositeAudioClip([audio, sfx]) if i > 0 else audio
    return CompositeVideoClip(layers, size=(W, H)).set_duration(d).set_audio(scene_audio)


def write_wav(path, sig, nch, sr=22050):
    data = (sig / max(1e-9, np.abs(sig).max()) * 0.8 * 32767).astype("<i2")
    if nch == 2:
        data = np.column_stack([data, data])
    with wave.open(path, "wb") as w:
        w.setnchannels(nch)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(data.tobytes())


def make_music(nch, path="ambient.wav", sr=22050):
    beat = 60 / RNG.choice([104, 112, 120])
    seconds = round(64 * beat, 3)
    t = np.arange(int(seconds * sr)) / sr
    sig = np.zeros_like(t)
    for f, a in zip(STYLE["chord"], [0.5, 0.35, 0.3, 0.2]):
        sig += 0.5 * a * np.sin(2 * np.pi * f * t) * (0.6 + 0.4 * np.sin(2 * np.pi * t / 10 + f))
    rs = np.random.RandomState(3)
    for kt in np.arange(0, seconds, beat):
        i0, n = int(kt * sr), int(0.18 * sr)
        if i0 + n > len(sig):
            break
        tt = np.arange(n) / sr
        sig[i0:i0 + n] += 0.9 * np.sin(2 * np.pi * (55 + 90 * np.exp(-tt * 28)) * tt) * np.exp(-tt * 16)
    for kt in np.arange(beat / 2, seconds, beat):
        i0, n = int(kt * sr), int(0.05 * sr)
        if i0 + n > len(sig):
            break
        sig[i0:i0 + n] += 0.18 * rs.randn(n) * np.exp(-(np.arange(n) / sr) * 70)
    write_wav(path, sig, nch, sr)


def make_whoosh(nch, path="whoosh.wav", sr=22050):
    n = int(0.45 * sr)
    t = np.arange(n) / sr
    noise = np.convolve(np.random.RandomState(7).randn(n), np.ones(6) / 6, mode="same")
    sweep = np.sin(2 * np.pi * (200 + 1800 * t / 0.45) * t)
    write_wav(path, (0.6 * noise + 0.4 * sweep) * np.sin(np.pi * t / 0.45) ** 2, nch, sr)
    return path


def make_thumb(text, tag, img):
    TW, TH = 1280, 720
    base = cover_to(img, TW, TH) if img is not None else Image.new("RGB", (TW, TH), STYLE["acc"])
    base = ImageEnhance.Brightness(ImageEnhance.Contrast(ImageEnhance.Color(base).enhance(1.35)).enhance(1.15)).enhance(0.85)
    a = np.clip(225 * (1 - np.linspace(0, 1, TW) / 0.78), 0, 225)[None, :].repeat(TH, 0).astype("uint8")
    shade = np.zeros((TH, TW, 4), dtype="uint8")
    shade[..., 3] = a
    im = base.convert("RGBA")
    im.alpha_composite(Image.fromarray(shade, "RGBA"))
    dr = ImageDraw.Draw(im)
    for size in range(150, 80, -10):
        font = load_font(size)
        lines = wrap_px(text, font, 760)
        if len(lines) <= 3:
            break
    lh, y = int(size * 1.4), (TH - int(size * 1.4) * len(lines)) // 2 + 20
    for k, l in enumerate(lines[:3]):
        dr.text((55, y), l, font=font, fill=(255, 221, 0) if k == 0 else (255, 255, 255), stroke_width=9, stroke_fill=(0, 0, 0))
        y += lh
    chip = text_block(tag, 44, 600, (0, 0, 0), box=(255, 221, 0, 255), pad=12, max_lines=1)
    im.alpha_composite(chip, (45, 40))
    dr.rectangle((0, TH - 26, TW, TH), fill=STYLE["acc"] + (255,))
    im.convert("RGB").save("thumb.jpg", quality=88)
    return "thumb.jpg"


# ---------------- metadata & upload ----------------
BASE_TAGS = ["#HindiNews", "#BreakingNews", "#TrendingNews", "#India", "#LatestNews", "#AajKiKhabar"]


def hashtags(meta):
    out = []
    for h in (meta.get("hashtags") or []) + BASE_TAGS:
        h = "#" + re.sub(r"[\s#.,!?:;'\"()\-/]+", "", str(h))
        if len(h) > 2 and h.lower() not in [x.lower() for x in out]:
            out.append(h)
    if KIND == "short":
        out.insert(2, "#Shorts")
    return out[:12]


def fmt_t(s):
    s = int(s)
    return f"{s // 60:02d}:{s % 60:02d}"


def build_desc(meta, sources, chapters):
    d = "\n".join(l for l in meta.get("description_lines", []) if l)
    if len(chapters) >= 3:
        if chapters[0][0] > 1:
            chapters.insert(0, (0, "परिचय"))
        d += "\n\n⏱ चैप्टर्स:\n" + "\n".join(f"{fmt_t(t)} {n}" for t, n in chapters)
    d += ("\n\n🔔 रोज़ की ताज़ा खबरों के लिए चैनल को सब्सक्राइब करें और बेल आइकन दबाएं।"
          "\n👍 वीडियो पसंद आए तो लाइक और शेयर करें।\n💬 आपकी राय क्या है? कमेंट में बताइए।")
    d += "\n\nस्रोत: " + ", ".join(dict.fromkeys(sources))[:300]
    d += "\nतस्वीरें/फुटेज: Wikimedia, Pixabay, Pexels"
    d += ("\n\nयह वीडियो AI की मदद से बनाया गया है और सार्वजनिक रिपोर्ट्स पर आधारित है। कोई त्रुटि दिखे तो कमेंट में बताएं। "
          "यह कानूनी, चिकित्सा या वित्तीय सलाह नहीं है।")
    return d + "\n\n" + " ".join(hashtags(meta))


def upload(path, meta, desc, thumb=None):
    creds = Credentials(None, refresh_token=os.environ["YT_REFRESH_TOKEN"].strip(), token_uri="https://oauth2.googleapis.com/token",
                        client_id=os.environ["YT_CLIENT_ID"].strip(), client_secret=os.environ["YT_CLIENT_SECRET"].strip(),
                        scopes=["https://www.googleapis.com/auth/youtube.upload"])
    yt = build("youtube", "v3", credentials=creds)
    title = (meta["title"][:80] + (" #Shorts" if KIND == "short" else "")).strip()
    tags = [t.lstrip("#") for t in (meta.get("tags") or [])][:12] + ["Hindi news", "India news", "ताज़ा खबर"]
    body = {"snippet": {"title": title, "description": desc[:4900], "tags": tags, "categoryId": "25",
                        "defaultLanguage": "hi", "defaultAudioLanguage": "hi"},
            "status": {"privacyStatus": os.getenv("VISIBILITY", "private"), "selfDeclaredMadeForKids": False,
                       "containsSyntheticMedia": True}}
    req = yt.videos().insert(part="snippet,status", body=body, media_body=MediaFileUpload(path, chunksize=-1, resumable=True))
    resp = None
    try:
        while resp is None:
            _, resp = req.next_chunk()
    except Exception as e:
        print("UPLOAD ERROR:", getattr(e, "content", b"")[:400] or repr(e)[:400])
        raise
    print("Uploaded:", resp["id"])
    if thumb:
        try:
            yt.thumbnails().set(videoId=resp["id"], media_body=MediaFileUpload(thumb)).execute()
            print("Thumbnail set")
        except Exception as e:
            print("Thumbnail skipped:", repr(e)[:120])


def main():
    print("SLOT", SLOT, FORMAT, "| style shape", STYLE["shape"], "| font", FONT_PATH, "| raqm", LAYOUT == ImageFont.LAYOUT_RAQM)
    stories = select_for_slot()
    briefs, sources = [], []
    for s in stories:
        b, src = brief_for(s, light=(FORMAT == "RAPID"))
        briefs.append(b)
        sources += src
    print("Stories:", [s["title"][:60] for s in stories])
    meta = fact_check(briefs, gen_script(stories, briefs))
    sources = (meta.get("sources") or []) + sources
    tag = meta.get("tag") or "ताज़ा खबर"
    scenes = [x for x in meta["scenes"] if speakable(x.get("narration", ""))]
    if len(scenes) < 3:
        raise SystemExit("Too few valid scenes")
    scenes.append({"cta": True, "narration": CTA[KIND], "label": "लाइक • शेयर • सब्सक्राइब", "shots": [
        {"type": "stock_video", "q": ["india flag waving", "city skyline india", "news studio"]},
        {"type": "stock_photo", "q": ["india map", "new delhi india gate", "india city night"]}]})
    src_im = source_img(sources)
    built = []
    for sc in scenes:
        try:
            built.append((sc, build_scene(len(built), sc, tag, meta, src_im, bool(sc.get("cta")))))
        except (Exception, SystemExit) as e:
            print("SCENE SKIPPED:", repr(e)[:120])
    if len(built) < 3:
        raise SystemExit("Too few scenes could be built")
    clips = [c for _, c in built]
    chapters, t = [], 0.0
    for sc, c in built:
        if KIND == "long" and sc.get("chapter"):
            chapters.append((t, str(sc["chapter"])[:50]))
        t += c.duration
    base = concatenate_videoclips(clips, method="compose")
    speech = base.audio
    make_music(speech.nchannels)
    music = AudioFileClip("ambient.wav").fx(afx.audio_loop, duration=base.duration).fx(afx.volumex, 0.08).fx(afx.audio_fadeout, 2)
    video = base.set_audio(CompositeAudioClip([speech, music]))
    out = f"{SLOT}.mp4"
    video.write_videofile(out, fps=24, codec="libx264", audio_codec="aac", preset="veryfast", threads=4,
                          ffmpeg_params=["-crf", "24"])
    thumb = None
    if KIND == "long":
        img = find_still([q for q in [meta.get("thumb_query")] if q] + ["india news"])
        if img is None and FIRST["frame"] is not None:
            img = Image.fromarray(FIRST["frame"])
        thumb = make_thumb(meta.get("thumb_text") or meta.get("caption_title") or meta["title"], tag, img)
    upload(out, meta, build_desc(meta, sources, chapters), thumb)
    save_state([s["title"] for s in stories])


if __name__ == "__main__":
    main()
