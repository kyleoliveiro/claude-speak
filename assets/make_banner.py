"""Builds assets/banner.html from a real recording, then screenshot it to banner.png.

  python3 assets/make_banner.py line.wav
  google-chrome --headless=new --hide-scrollbars --force-device-scale-factor=2 \
      --window-size=1280,640 --screenshot=assets/banner.png assets/banner.html

line.wav is Kokoro (af_heart, 1.0x) reading LINE, e.g.
  bin/claude-speak say --speed 1.0 --out line.wav "<LINE>"
"""
import array
import sys
import wave
from pathlib import Path

LINE = "I fixed the flaky auth test. It was a race in token refresh, and all tests pass."
PLAYHEAD = 0.57          # fraction of the clip already spoken
WIDTH, PITCH = 1152, 4   # waveform width in px, one bar every PITCH px
HEIGHT = 100

with wave.open(sys.argv[1]) as w:
    rate = w.getframerate()
    samples = array.array("h", w.readframes(w.getnframes()))
duration = len(samples) / rate

n = WIDTH // PITCH
size = len(samples) // n
peaks = [max(abs(s) for s in samples[i * size:(i + 1) * size]) for i in range(n)]
top = max(peaks)
bars = []
for i, p in enumerate(peaks):
    h = max(2, round((p / top) ** 0.7 * HEIGHT))
    x = i * PITCH
    cls = "on" if i / n < PLAYHEAD else "off"
    bars.append(f'<rect class="{cls}" x="{x}" y="{(HEIGHT - h) / 2:.1f}" width="2" height="{h}"/>')

ticks = []
step = 0.5
t = 0.0
while t <= duration:
    x = t / duration * WIDTH
    major = abs(t - round(t)) < 1e-6
    ticks.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="0" y2="{8 if major else 4}"/>')
    if major:
        end = x > WIDTH - 40
        ticks.append(f'<text x="{x - 4 if end else x + 4:.1f}" y="18"'
                     f'{" text-anchor=\"end\"" if end else ""}>00:{int(t):02d}</text>')
    t += step

px = PLAYHEAD * WIDTH
stamp = PLAYHEAD * duration

html = f"""<!doctype html>
<html><head><meta charset="utf-8">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter+Tight:wght@500;600;700&family=Instrument+Serif:ital@1&family=JetBrains+Mono:wght@400;500&display=block" rel="stylesheet">
<style>
  :root {{ --paper:#EEEAE2; --ink:#15140F; --muted:#8A857A; --rule:#CFC9BD; --accent:#D97757; }}
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  html, body {{ width:1280px; height:640px; background:var(--paper); overflow:hidden; }}
  body {{ padding:56px 64px 48px; color:var(--ink); font-family:'Inter Tight',sans-serif;
         display:flex; flex-direction:column; -webkit-font-smoothing:antialiased; }}
  .mono {{ font-family:'JetBrains Mono',monospace; font-size:12px; letter-spacing:.08em; text-transform:uppercase; color:var(--muted); }}
  header, footer {{ display:flex; justify-content:space-between; align-items:center; }}
  header {{ padding-bottom:14px; border-bottom:1px solid var(--rule); }}
  .brand {{ display:flex; align-items:center; gap:14px; }} .brand img {{ width:40px; height:40px; }}
  header b {{ color:var(--ink); font-weight:500; }}
  .mark {{ margin-top:28px; font-size:150px; font-weight:700; letter-spacing:-.058em; line-height:.86; }}
  .mark span {{ color:var(--accent); }}
  .sub {{ margin-top:44px; display:flex; justify-content:space-between; align-items:flex-end; gap:48px; }}
  .tag {{ font-size:25px; font-weight:500; letter-spacing:-.015em; line-height:1.25; max-width:520px; }}
  .tag span {{ color:#4A463E; }} .tag i {{ font-style:normal; white-space:nowrap; }}
  .quote {{ font-family:'Instrument Serif',serif; font-style:italic; font-size:25px; line-height:1.2; color:#4A463E;
            max-width:500px; text-align:right; }}
  .wave {{ margin-top:auto; position:relative; }}
  .wave svg {{ display:block; overflow:visible; }}
  .on {{ fill:var(--ink); }} .off {{ fill:#BDB6A8; }}
  .head {{ position:absolute; top:-26px; bottom:-8px; width:0; border-left:1.5px solid var(--accent); }}
  .head::before {{ content:''; position:absolute; top:0; left:-5px; width:8.5px; height:8.5px; background:var(--accent); }}
  .head em {{ position:absolute; top:-3px; left:10px; font-style:normal; color:var(--accent); white-space:nowrap; }}
  .ruler {{ margin-top:12px; }}
  .ruler line {{ stroke:var(--muted); stroke-width:1; }}
  .ruler text {{ font-family:'JetBrains Mono',monospace; font-size:10px; fill:var(--muted); letter-spacing:.04em; }}
  footer {{ margin-top:22px; }}
</style></head>
<body>
  <header class="mono"><span class="brand"><img src="icon.svg" alt=""><span><b>Claude Code</b> plugin</span></span><span>kyleoliveiro/claude-speak</span></header>
  <div class="mark">claude<span>-</span>speak</div>
  <div class="sub">
    <p class="tag">Stop babysitting the terminal. <span>Claude Code tells you what it did, out loud, using a free local <i>text-to-speech</i> model.</span></p>
    <p class="quote">“{LINE}”</p>
  </div>
  <div class="wave">
    <div class="head mono" style="left:{px:.1f}px"><em>af_heart &nbsp;00:{stamp:05.2f}</em></div>
    <svg width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}">{''.join(bars)}</svg>
    <svg class="ruler" width="{WIDTH}" height="20">{''.join(ticks)}</svg>
  </div>
</body></html>
"""
Path(__file__).with_name("banner.html").write_text(html)
print(f"{duration:.2f}s, {n} bars")
