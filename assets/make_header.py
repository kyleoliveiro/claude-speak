"""Builds assets/header.html, the short README header, from a real recording.

  python3 assets/make_header.py line.wav
  uv run --with playwright python assets/make_header.py --png

line.wav is Kokoro (af_heart, 1.0x) reading LINE, e.g.
  bin/claude-speak say --speed 1.0 --out line.wav "<LINE>"

--png screenshots header.html to header.png at 2x with transparent corners, so
the strip sits cleanly on GitHub's light and dark themes. The 2:1 banner.png
(make_banner.py) is kept for the repository's social preview.
"""
import array
import sys
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent
LINE = "I fixed the flaky auth test. It was a race in token refresh, and all tests pass."
PLAYHEAD = 0.57          # fraction of the clip already spoken
WIDTH, PITCH = 1208, 4   # waveform width in px, one bar every PITCH px
HEIGHT = 64
W, H = 1280, 292


def screenshot() -> None:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome")
        page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=2)
        page.goto((HERE / "header.html").as_uri())
        page.evaluate("document.fonts.ready")
        page.screenshot(path=str(HERE / "header.png"), omit_background=True)
        browser.close()
    print("wrote assets/header.png")


if sys.argv[1:] == ["--png"]:
    screenshot()
    sys.exit()

with wave.open(sys.argv[1]) as w:
    rate = w.getframerate()
    samples = array.array("h", w.readframes(w.getnframes()))
loud = [i for i, s in enumerate(samples) if abs(s) > 800]
samples = samples[loud[0]:loud[-1]]
duration = len(samples) / rate

n = WIDTH // PITCH
size = len(samples) // n
peaks = [max(abs(s) for s in samples[i * size:(i + 1) * size]) for i in range(n)]
top = max(peaks)
bars = []
for i, p in enumerate(peaks):
    h = max(2, round((p / top) ** 0.7 * HEIGHT))
    cls = "on" if i / n < PLAYHEAD else "off"
    bars.append(f'<rect class="{cls}" x="{i * PITCH}" y="{(HEIGHT - h) / 2:.1f}" width="2" height="{h}" rx="1"/>')

# Words already spoken, by share of characters.
words, spoken, count = LINE.split(), [], 0
for word in words:
    spoken.append(count / len(LINE) < PLAYHEAD)
    count += len(word) + 1
quote = " ".join(f'<span class="{"said" if s else ""}">{w}</span>' for w, s in zip(words, spoken))

html = f"""<!doctype html>
<html><head><meta charset="utf-8">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter+Tight:wght@600;700&family=Instrument+Serif:ital@1&family=JetBrains+Mono:wght@400;500&display=block" rel="stylesheet">
<style>
  :root {{ --term:#1A1915; --bar:#23221D; --edge:#3A3730; --paper:#EEEAE2; --dim:#8E887C; --faint:#5E5A50; --accent:#D97757; }}
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  html, body {{ width:{W}px; height:{H}px; background:transparent; overflow:hidden; }}
  body {{ font-family:'Inter Tight',sans-serif; -webkit-font-smoothing:antialiased; }}
  .term {{ position:absolute; inset:0; background:var(--term); border:1px solid var(--edge); border-radius:18px; overflow:hidden; }}
  .mono {{ font-family:'JetBrains Mono',monospace; font-size:12px; letter-spacing:.08em; text-transform:uppercase; color:var(--dim); }}
  .tbar {{ height:38px; background:var(--bar); display:flex; align-items:center; gap:8px; padding:0 18px; position:relative; }}
  .tbar i {{ width:11px; height:11px; border-radius:50%; background:#3B3931; }}
  .tbar span {{ position:absolute; left:0; right:0; text-align:center; text-transform:none; letter-spacing:0; }}
  .body {{ padding:24px 36px 0; }}
  .row {{ display:flex; justify-content:space-between; align-items:center; }}
  .brand {{ display:flex; align-items:center; gap:14px; }} .brand img {{ width:34px; height:34px; }}
  .mark {{ font-size:40px; font-weight:700; letter-spacing:-.05em; color:var(--paper); line-height:1; }}
  .mark span {{ color:var(--accent); }}
  .live {{ display:flex; align-items:center; gap:10px; }}
  .live b {{ color:var(--accent); font-weight:500; }}
  .live i {{ width:8px; height:8px; border-radius:50%; background:var(--accent); }}
  .wave {{ position:relative; margin-top:26px; }}
  .wave svg {{ display:block; overflow:visible; }}
  .on {{ fill:var(--accent); }} .off {{ fill:#3E3B33; }}
  .head {{ position:absolute; left:{PLAYHEAD * WIDTH:.1f}px; top:-8px; bottom:-8px; width:0; border-left:1.5px solid var(--paper); }}
  .head::before {{ content:''; position:absolute; top:0; left:-4.5px; width:7.5px; height:7.5px; background:var(--paper); }}
  .foot {{ margin-top:22px; align-items:baseline; }}
  .quote {{ font-family:'Instrument Serif',serif; font-style:italic; font-size:29px; color:var(--faint); white-space:nowrap; }}
  .quote .said {{ color:var(--paper); }}
</style></head>
<body>
  <div class="term">
    <div class="tbar"><i></i><i></i><i></i><span class="mono">claude — ~/acme-app</span></div>
    <div class="body">
      <div class="row">
        <div class="brand"><img src="icon.svg" alt=""><div class="mark">claude<span>-</span>speak</div></div>
        <div class="live mono"><i></i><b>Speaking</b> af_heart · 00:{PLAYHEAD * duration:05.2f} · on your machine</div>
      </div>
      <div class="wave"><div class="head"></div>
        <svg width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}">{''.join(bars)}</svg></div>
      <div class="row foot">
        <p class="quote">“{quote}”</p>
        <span class="mono">Free · Local · No API key</span>
      </div>
    </div>
  </div>
</body></html>
"""
(HERE / "header.html").write_text(html)
print("wrote assets/header.html")
