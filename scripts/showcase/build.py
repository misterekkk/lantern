"""The "See it in action" showcase: one animated SVG (CSS keyframes, plays in a README) that also becomes a video.

Scenes: Lantern's panel (a recording, ⌘Y preview from the moment), Raycast (photos), the CLI (a code definition, then
`--json | jq`). Every file name, path and text is invented: nothing from a real Mac. One timeline drives every
element: `show()` gives an element its entry, exit and movement, and `typing()` reveals a string one prefix at a time
(works with any font, so no measuring of proportional text).

    python3 scripts/showcase/build.py                 # writes site/public/showcase.svg
    scripts/showcase/video.sh                         # frames with WebKit, then MP4 with ffmpeg
"""

from html import escape
from pathlib import Path

W, H = 1280, 720
T = 27.0  # loop length, seconds
OUT = Path(__file__).resolve().parents[2] / "site" / "public" / "showcase.svg"

SANS = "-apple-system, BlinkMacSystemFont, 'SF Pro Text', 'Inter', 'Segoe UI', 'Helvetica Neue', Arial, sans-serif"
MONO = "ui-monospace, 'SF Mono', Menlo, Consolas, 'Liberation Mono', monospace"

css: list[str] = ["g[id]{transform-box:fill-box;transform-origin:50% 50%}"]  # scale around each element's centre
_n = 0


def pct(t: float) -> str:
    return f"{max(0.0, min(100.0, t / T * 100)):.3f}%"


def keyframes(el_id: str, frames: list[tuple[float, str, str]]) -> None:
    """frames: (time s, css properties, easing towards the next frame)."""
    global _n
    _n += 1
    name = f"k{_n}"
    body = " ".join(f"{pct(t)}{{{props};animation-timing-function:{ease}}}" for t, props, ease in frames)
    css.append(f"@keyframes {name}{{{body}}}")
    css.append(f"#{el_id}{{animation:{name} {T}s linear infinite both}}")


EASE_OUT = "cubic-bezier(.2,.8,.2,1)"
EASE_IN = "cubic-bezier(.6,0,.8,.2)"


def show(el_id: str, t_in: float, t_out: float, fade: float = 0.35, enter: str = "", exit: str = "",
         rest: str = "translate(0px,0px) scale(1)", moves: list[tuple[float, str]] | None = None) -> None:
    """Visible from t_in to t_out; enter/exit are the transforms it comes from and leaves to; moves: (t, transform)
    while visible."""
    enter = enter or rest
    exit = exit or rest
    frames = [(0, f"opacity:0;transform:{enter}", "step-end")]
    frames.append((max(0.0, t_in - 0.001), f"opacity:0;transform:{enter}", EASE_OUT))
    frames.append((t_in + fade, f"opacity:1;transform:{rest}", "linear"))
    current = rest
    for t, tr in moves or []:
        frames.append((t, f"opacity:1;transform:{current}", EASE_OUT))
        current = tr
        frames.append((t + 0.45, f"opacity:1;transform:{tr}", "linear"))
    frames.append((t_out - fade, f"opacity:1;transform:{current}", EASE_IN))
    frames.append((t_out, f"opacity:0;transform:{exit}", "step-end"))
    frames.append((T, f"opacity:0;transform:{exit}", "linear"))
    keyframes(el_id, sorted(frames, key=lambda f: f[0]))


def blink(el_id: str, times: list[tuple[float, float]]) -> None:
    """On (no fade) only within the given windows."""
    frames = [(0, "opacity:0", "step-end")]
    for a, b in times:
        frames += [(a, "opacity:1", "step-end"), (b, "opacity:0", "step-end")]
    frames.append((T, "opacity:0", "step-end"))
    keyframes(el_id, frames)


def typing(prefix_id: str, text: str, t0: float, cps: float, until: float, x: float, y: float, attrs: str,
           caret: bool = True) -> str:
    """One <text> per prefix, each shown for its slot; the last one stays until `until`."""
    out = []
    n = len(text)
    for i in range(1, n + 1):
        el = f"{prefix_id}{i}"
        a = t0 + (i - 1) / cps
        b = t0 + i / cps if i < n else until
        shown = escape(text[:i]) + ("▏" if caret and i < n else "")
        out.append(f'<text id="{el}" x="{x}" y="{y}" {attrs}>{shown}</text>')
        blink(el, [(a, b)])
    return "\n".join(out)


def bold_words(text: str, words: set[str], weight_on: int = 700) -> str:
    """tspans: query words in bold, as the app marks them."""
    parts = []
    for w in text.split(" "):
        key = w.strip(".,…“”\"'").lower()
        parts.append(f'<tspan font-weight="{weight_on}">{escape(w)}</tspan>' if key in words else escape(w))
    return " ".join(parts)


# ---------- drawings ----------


def wallpaper() -> str:
    return f"""
<defs>
  <radialGradient id="wp1" cx="18%" cy="20%" r="70%"><stop offset="0" stop-color="#3b2a6b"/><stop offset="1" stop-color="#3b2a6b" stop-opacity="0"/></radialGradient>
  <radialGradient id="wp2" cx="85%" cy="85%" r="70%"><stop offset="0" stop-color="#0f5c6e"/><stop offset="1" stop-color="#0f5c6e" stop-opacity="0"/></radialGradient>
  <radialGradient id="wp3" cx="70%" cy="10%" r="50%"><stop offset="0" stop-color="#b8692a" stop-opacity=".55"/><stop offset="1" stop-color="#b8692a" stop-opacity="0"/></radialGradient>
  <filter id="shadow" x="-20%" y="-20%" width="140%" height="150%"><feDropShadow dx="0" dy="18" stdDeviation="22" flood-color="#000" flood-opacity=".45"/></filter>
  <filter id="soft" x="-10%" y="-10%" width="120%" height="130%"><feDropShadow dx="0" dy="6" stdDeviation="8" flood-color="#000" flood-opacity=".25"/></filter>
  <linearGradient id="glass" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#ffffff" stop-opacity=".86"/><stop offset="1" stop-color="#f2f4f8" stop-opacity=".8"/></linearGradient>
</defs>
<rect width="{W}" height="{H}" fill="#0d1220"/>
<rect width="{W}" height="{H}" fill="url(#wp1)"/><rect width="{W}" height="{H}" fill="url(#wp2)"/><rect width="{W}" height="{H}" fill="url(#wp3)"/>
"""


def lantern_icon(x: float, y: float, s: float) -> str:
    """Lantern's mark, drawn here (graphite tile, magnifier, amber spark)."""
    k = s / 100
    return f"""<g transform="translate({x},{y}) scale({k})">
  <rect width="100" height="100" rx="23" fill="#1d2027"/><rect x=".75" y=".75" width="98.5" height="98.5" rx="22.3" fill="none" stroke="#ffffff" stroke-opacity=".12" stroke-width="1.5"/>
  <circle cx="44" cy="44" r="23" fill="none" stroke="#f4f4f6" stroke-width="8"/>
  <line x1="61" y1="61" x2="78" y2="78" stroke="#f4f4f6" stroke-width="10" stroke-linecap="round"/>
  <path d="M44 31 L47.5 40.5 L57 44 L47.5 47.5 L44 57 L40.5 47.5 L31 44 L40.5 40.5 Z" fill="#f2b84b"/>
</g>"""


def pitch(x: float, y: float, w: float, h: float, clip: str) -> str:
    """A video frame: a football match seen from the stand."""
    players = [(.22, .42, "#e4572e"), (.35, .6, "#e4572e"), (.48, .35, "#e4572e"), (.58, .55, "#2e86de"),
               (.7, .4, "#2e86de"), (.8, .62, "#2e86de"), (.42, .75, "#e4572e"), (.64, .25, "#2e86de")]
    dots = "".join(f'<g><ellipse cx="{x + w * px}" cy="{y + h * py + 7}" rx="7" ry="2.5" fill="#000" opacity=".25"/>'
                   f'<rect x="{x + w * px - 4}" y="{y + h * py - 12}" width="8" height="18" rx="4" fill="{c}"/>'
                   f'<circle cx="{x + w * px}" cy="{y + h * py - 15}" r="4" fill="#f1c9a5"/></g>'
                   for px, py, c in players)  # fmt: skip
    stripes = "".join(f'<rect x="{x + i * w / 8}" y="{y}" width="{w / 16}" height="{h}" fill="#ffffff" opacity=".05"/>'
                      for i in range(8))  # fmt: skip
    return f"""<g clip-path="url(#{clip})">
  <rect x="{x}" y="{y}" width="{w}" height="{h}" fill="#2f8a43"/>{stripes}
  <rect x="{x}" y="{y}" width="{w}" height="{h * .16}" fill="#1b3a4b"/>
  <rect x="{x}" y="{y + h * .16}" width="{w}" height="3" fill="#ffffff" opacity=".7"/>
  <line x1="{x + w / 2}" y1="{y + h * .16}" x2="{x + w / 2}" y2="{y + h}" stroke="#fff" stroke-opacity=".75" stroke-width="2.5"/>
  <ellipse cx="{x + w / 2}" cy="{y + h * .58}" rx="{w * .12}" ry="{h * .14}" fill="none" stroke="#fff" stroke-opacity=".75" stroke-width="2.5"/>
  {dots}
  <circle cx="{x + w * .53}" cy="{y + h * .5}" r="4" fill="#fff"/>
</g>"""


def beach(x: float, y: float, w: float, h: float, clip: str, variant: int = 0) -> str:
    """A photo: a dog on the beach (four variants for the grid)."""
    skies = [("#8ecae6", "#ffe5b4"), ("#f6a35b", "#fde2c4"), ("#a7c7e7", "#e8f1f8"), ("#6d8fc7", "#f7c59f")]
    top, low = skies[variant % 4]
    gid = f"sky{clip}"
    dogx = [0.42, 0.6, 0.3, 0.52][variant % 4]
    d = 1.0 if variant != 2 else -1.0
    cx, base = x + w * dogx, y + h * 0.78
    s = h / 260
    dog = f"""<g transform="translate({cx},{base}) scale({d * s},{s})">
    <ellipse cx="0" cy="18" rx="46" ry="6" fill="#000" opacity=".15"/>
    <rect x="-34" y="-4" width="9" height="24" rx="4" fill="#7a4a24"/><rect x="18" y="-4" width="9" height="24" rx="4" fill="#7a4a24"/>
    <rect x="-22" y="-2" width="9" height="22" rx="4" fill="#8b5a2b"/><rect x="28" y="-2" width="9" height="22" rx="4" fill="#8b5a2b"/>
    <ellipse cx="0" cy="-14" rx="42" ry="20" fill="#a0672f"/>
    <path d="M-40 -20 Q-62 -40 -56 -52" stroke="#a0672f" stroke-width="7" fill="none" stroke-linecap="round"/>
    <circle cx="44" cy="-34" r="17" fill="#a0672f"/><ellipse cx="60" cy="-30" rx="11" ry="8" fill="#b9814a"/>
    <circle cx="69" cy="-31" r="3.5" fill="#2b1a0e"/><circle cx="47" cy="-38" r="2.6" fill="#2b1a0e"/>
    <path d="M34 -46 Q30 -26 40 -22" fill="#6e4220"/>
  </g>"""
    return f"""<g clip-path="url(#{clip})">
  <defs><linearGradient id="{gid}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{top}"/><stop offset="1" stop-color="{low}"/></linearGradient></defs>
  <rect x="{x}" y="{y}" width="{w}" height="{h}" fill="url(#{gid})"/>
  <circle cx="{x + w * (0.78 - variant * 0.12)}" cy="{y + h * 0.22}" r="{h * 0.08}" fill="#fff6d5" opacity=".9"/>
  <rect x="{x}" y="{y + h * 0.5}" width="{w}" height="{h * 0.14}" fill="#2a9dbd"/>
  <path d="M{x} {y + h * 0.62} Q{x + w * .25} {y + h * 0.58} {x + w * .5} {y + h * 0.63} T{x + w} {y + h * 0.62} V{y + h} H{x} Z" fill="#f1d39b"/>
  <path d="M{x} {y + h * 0.63} Q{x + w * .25} {y + h * 0.6} {x + w * .5} {y + h * 0.645} T{x + w} {y + h * 0.63}" stroke="#fff" stroke-opacity=".8" stroke-width="2" fill="none"/>
  {dog}
</g>"""


def clip_rect(cid: str, x: float, y: float, w: float, h: float, r: float) -> str:
    return f'<clipPath id="{cid}"><rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}"/></clipPath>'


def keycap(el_id: str, x: float, y: float, label: str) -> str:
    w = 22 + 13 * len(label)
    return f"""<g id="{el_id}" style="transform-box:fill-box;transform-origin:center"><rect x="{x}" y="{y}" width="{w}" height="46" rx="11" fill="#ffffff" fill-opacity=".14" stroke="#ffffff" stroke-opacity=".35"/>
  <text x="{x + w / 2}" y="{y + 30}" text-anchor="middle" font-family="{SANS}" font-size="20" font-weight="600" fill="#fff">{escape(label)}</text></g>"""


def caption(el_id: str, title: str, sub: str) -> str:
    return f"""<g id="{el_id}"><text x="64" y="72" font-family="{SANS}" font-size="15" font-weight="600" fill="#f2b84b" letter-spacing="1.5">{escape(sub.upper())}</text>
  <text x="64" y="104" font-family="{SANS}" font-size="28" font-weight="700" fill="#fff">{escape(title)}</text></g>"""


# ---------- scenes ----------

parts: list[str] = [wallpaper()]

# Intro
parts.append(f"""<g id="intro">{lantern_icon(W / 2 - 52, 236, 104)}
  <text x="{W / 2}" y="404" text-anchor="middle" font-family="{SANS}" font-size="54" font-weight="700" fill="#fff">Lantern</text>
  <text x="{W / 2}" y="448" text-anchor="middle" font-family="{SANS}" font-size="22" fill="#cfd5e2">Search your Mac by what is inside. Offline.</text></g>""")
show("intro", 0.0, 2.6, fade=0.5, rest="scale(1)", exit="scale(1.06)")
css.append("#intro{transform-box:view-box;transform-origin:50% 50%}")

# Scene 1: Lantern's panel, a recording, ⌘Y
S1, S1_END = 2.6, 10.4
q1 = "when we talked about the match"
px, py, pw = 290, 150, 700
parts.append(caption("cap1", "Lantern’s own panel", "⌥ Space"))
show("cap1", S1 + 0.2, S1_END, enter="translate(-20px,0px)", exit="translate(-20px,0px)")
panel_narrow = f"""<g id="panel1" style="transform-box:fill-box;transform-origin:50% 0%" filter="url(#shadow)">
  <rect x="{px}" y="{py}" width="{pw}" height="330" rx="26" fill="url(#glass)" stroke="#fff" stroke-opacity=".8"/>
  <circle cx="{px + 34}" cy="{py + 36}" r="9" fill="none" stroke="#6b7280" stroke-width="2.2"/><line x1="{px + 41}" y1="{py + 43}" x2="{px + 47}" y2="{py + 49}" stroke="#6b7280" stroke-width="2.6" stroke-linecap="round"/>
  <line x1="{px + 18}" y1="{py + 70}" x2="{px + pw - 18}" y2="{py + 70}" stroke="#000" stroke-opacity=".07"/>
</g>"""
parts.append(panel_narrow)
show("panel1", S1, S1_END, enter="translate(0px,14px) scale(.97)", exit="translate(-90px,0px) scale(.96)")
parts.append(f'<text id="ph1" x="{px + 62}" y="{py + 44}" font-family="{SANS}" font-size="22" fill="#9aa0aa">Search Lantern</text>')
blink("ph1", [(S1 + 0.3, S1 + 0.8)])
parts.append(typing("q1_", q1, S1 + 0.8, 17, S1_END - 0.3, px + 62, py + 44,
                    f'font-family="{SANS}" font-size="22" fill="#14181f"'))
# results
rx0, ry0 = px + 14, py + 86
r1 = f"""<g id="res1">
  <text x="{rx0 + 12}" y="{ry0 + 8}" font-family="{SANS}" font-size="12.5" font-weight="600" fill="#6b7280">Recordings and videos</text>
  <rect x="{rx0}" y="{ry0 + 18}" width="{pw - 28}" height="62" rx="16" fill="#3b82f6"/>
  <rect x="{rx0 + 12}" y="{ry0 + 30}" width="38" height="38" rx="8" fill="#1f5fd1"/><path d="M{rx0 + 26} {ry0 + 40} l12 9 l-12 9 Z" fill="#fff"/>
  <text x="{rx0 + 62}" y="{ry0 + 45}" font-family="{SANS}" font-size="16" font-weight="600" fill="#fff">Season review.mov</text>
  <text x="{rx0 + 62}" y="{ry0 + 66}" font-family="{SANS}" font-size="13.5" fill="#e6efff">{bold_words("… and when we talked about the match on Sunday, the second half …", {"when", "talked", "about", "match"})}</text>
  <text x="{rx0 + pw - 46}" y="{ry0 + 45}" text-anchor="end" font-family="{SANS}" font-size="14" font-weight="600" fill="#fff">1:48</text>
  <text x="{rx0 + pw - 46}" y="{ry0 + 67}" text-anchor="end" font-family="{SANS}" font-size="12.5" fill="#dbe7ff">⌘Y</text>
  <rect x="{rx0 + 12}" y="{ry0 + 96}" width="38" height="38" rx="8" fill="#e5e7eb"/><path d="M{rx0 + 26} {ry0 + 106} l12 9 l-12 9 Z" fill="#6b7280"/>
  <text x="{rx0 + 62}" y="{ry0 + 111}" font-family="{SANS}" font-size="16" font-weight="600" fill="#14181f">Team meeting, March.m4a</text>
  <text x="{rx0 + 62}" y="{ry0 + 131}" font-family="{SANS}" font-size="13.5" fill="#6b7280">{bold_words("… we can talk about the match schedule next week …", {"talk", "about", "match"})}</text>
  <text x="{rx0 + pw - 46}" y="{ry0 + 111}" text-anchor="end" font-family="{SANS}" font-size="14" fill="#6b7280">12:05</text>
  <text x="{rx0 + 12}" y="{ry0 + 172}" font-family="{SANS}" font-size="12.5" font-weight="600" fill="#6b7280">Files</text>
  <rect x="{rx0 + 12}" y="{ry0 + 184}" width="38" height="38" rx="8" fill="#eef2ff"/><text x="{rx0 + 31}" y="{ry0 + 209}" text-anchor="middle" font-family="{SANS}" font-size="12" font-weight="700" fill="#4f46e5">MD</text>
  <text x="{rx0 + 62}" y="{ry0 + 199}" font-family="{SANS}" font-size="16" font-weight="600" fill="#14181f">notes-match-day.md</text>
  <text x="{rx0 + 62}" y="{ry0 + 219}" font-family="{SANS}" font-size="13.5" fill="#6b7280">~/Documents/Club · line 14</text>
</g>"""
parts.append(r1)
show("res1", S1 + 2.75, S1_END, fade=0.3, enter="translate(0px,8px)", exit="translate(-90px,0px)")
# ⌘Y keycap and the preview
parts.append(keycap("key1", 1090, 610, "⌘Y"))
show("key1", S1 + 3.9, S1 + 5.0, fade=0.15, enter="scale(.8)", exit="scale(1.1)")
vx, vy, vw, vh = 150, 120, 980, 470
parts.append(f"<defs>{clip_rect('vclip', vx + 20, vy + 20, vw - 40, 330, 14)}</defs>")
prog_from, prog_len = 0.52, vw - 200
preview = f"""<g id="prev1" style="transform-box:fill-box;transform-origin:50% 50%" filter="url(#shadow)">
  <rect x="{vx}" y="{vy}" width="{vw}" height="{vh}" rx="26" fill="#1e2028" stroke="#ffffff" stroke-opacity=".12"/>
  {pitch(vx + 20, vy + 20, vw - 40, 330, "vclip")}
  <rect x="{vx + 20}" y="{vy + 300}" width="{vw - 40}" height="50" fill="#000" opacity=".35"/>
  <path d="M{vx + 44} {vy + 314} h6 v22 h-6 Z M{vx + 56} {vy + 314} h6 v22 h-6 Z" fill="#fff"/>
  <rect x="{vx + 90}" y="{vy + 323}" width="{prog_len}" height="5" rx="2.5" fill="#fff" opacity=".3"/>
  <rect id="prog1" x="{vx + 90}" y="{vy + 323}" width="{prog_len}" height="5" rx="2.5" fill="#fff" style="transform-box:fill-box;transform-origin:0% 50%"/>
  <text x="{vx + vw - 34}" y="{vy + 331}" text-anchor="end" font-family="{MONO}" font-size="14" fill="#fff">3:27</text>
  <text x="{vx + 24}" y="{vy + 386}" font-family="{SANS}" font-size="17" fill="#c9ced8">{bold_words("“… and when we talked about the match on Sunday, the second half was the turning point,", {"when", "talked", "about", "match"}, 800)}</text>
  <text x="{vx + 24}" y="{vy + 412}" font-family="{SANS}" font-size="17" fill="#c9ced8">so let’s start the review from there.”</text>
  <text x="{vx + 24}" y="{vy + 448}" font-family="{SANS}" font-size="14" fill="#8a909c">Season review.mov · from 1:48 · drag back to the start any time</text>
</g>"""
parts.append(preview)
show("prev1", S1 + 4.35, S1_END, fade=0.4, enter="scale(.9)", exit="translate(-120px,0px) scale(.94)")
keyframes("prog1", [(0, f"transform:scaleX({prog_from})", "linear"), (S1 + 4.6, f"transform:scaleX({prog_from})", "linear"),
                    (S1_END, f"transform:scaleX({prog_from + 0.03})", "linear"), (T, f"transform:scaleX({prog_from})", "linear")])

# Scene 2: Raycast, photos
S2, S2_END = 10.2, 17.6
q2 = "dog on the beach"
rx, ry, rw, rh = 260, 130, 760, 480
parts.append(caption("cap2", "Inside Raycast", "Search Lantern"))
show("cap2", S2 + 0.4, S2_END, enter="translate(-20px,0px)", exit="translate(-20px,0px)")
thumbs = []
tw, th, gap = 168, 112, 14
gx, gy = rx + 22, ry + 106
defs = []
names = ["IMG_2041.HEIC", "IMG_1877.HEIC", "beach-day.jpg", "IMG_0990.HEIC"]
for i in range(4):
    cid = f"t{i}"
    x = gx + i * (tw + gap)
    defs.append(clip_rect(cid, x, gy, tw, th, 8))
    sel = f'<rect x="{x - 4}" y="{gy - 4}" width="{tw + 8}" height="{th + 8}" rx="11" fill="none" stroke="#ff6363" stroke-width="3"/>' if i == 0 else ""
    thumbs.append(f'{beach(x, gy, tw, th, cid, i)}{sel}<text x="{x + 2}" y="{gy + th + 22}" font-family="{SANS}" font-size="13" fill="#e5e5e7">{names[i]}</text>'
                  f'<text x="{x + 2}" y="{gy + th + 40}" font-family="{SANS}" font-size="12" fill="#8e8e93">{["Lisbon · Jul 2025", "Porto · Aug 2024", "Gdańsk · Jun 2023", "Lisbon · Jul 2025"][i]}</text>')
for i in range(4):  # second row, other photos further down the list
    cid = f"u{i}"
    x = gx + i * (tw + gap)
    defs.append(clip_rect(cid, x, gy + 190, tw, th, 8))
    thumbs.append(beach(x, gy + 190, tw, th, cid, (i + 2) % 4))
parts.append(f"<defs>{''.join(defs)}</defs>")
raycast = f"""<g id="ray" filter="url(#shadow)">
  <rect x="{rx}" y="{ry}" width="{rw}" height="{rh}" rx="14" fill="#1c1c1e" stroke="#ffffff" stroke-opacity=".1"/>
  <path d="M{rx + 30} {ry + 34} l-8 8 l8 8" stroke="#8e8e93" stroke-width="2.4" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
  <rect x="{rx + rw - 230}" y="{ry + 22}" width="206" height="38" rx="9" fill="#2c2c2e"/>
  <text x="{rx + rw - 214}" y="{ry + 47}" font-family="{SANS}" font-size="15" fill="#e5e5e7">Photos  ⌃3</text>
  <line x1="{rx}" y1="{ry + 78}" x2="{rx + rw}" y2="{ry + 78}" stroke="#fff" stroke-opacity=".08"/>
  <rect x="{rx}" y="{ry + rh - 52}" width="{rw}" height="52" rx="14" fill="#232325"/>
  <rect x="{rx}" y="{ry + rh - 52}" width="{rw}" height="12" fill="#232325"/>
  {lantern_icon(rx + 18, ry + rh - 40, 28)}
  <text x="{rx + 56}" y="{ry + rh - 20}" font-family="{SANS}" font-size="14" fill="#c7c7cc">Search Lantern</text>
  <text x="{rx + rw - 150}" y="{ry + rh - 20}" font-family="{SANS}" font-size="14" fill="#e5e5e7">Quick Look  ⌘Y</text>
</g>"""
parts.append(raycast)
show("ray", S2, S2_END, enter="translate(140px,0px) scale(.96)", exit="scale(1.04)")
parts.append(typing("q2_", q2, S2 + 0.7, 15, S2_END - 0.3, rx + 52, ry + 49,
                    f'font-family="{SANS}" font-size="21" fill="#f2f2f7"'))
parts.append(f'<g id="grid">{"".join(thumbs)}</g>')
show("grid", S2 + 1.95, S2_END, fade=0.3, enter="translate(0px,10px)")
parts.append(keycap("key2", 1090, 610, "⌘Y"))
show("key2", S2 + 3.3, S2 + 4.3, fade=0.15, enter="scale(.8)", exit="scale(1.1)")
qx, qy, qw, qh = 300, 96, 680, 470
parts.append(f"<defs>{clip_rect('qclip', qx, qy + 44, qw, qh - 44 - 66, 0)}</defs>")
ql = f"""<g id="ql" style="transform-box:fill-box;transform-origin:50% 50%" filter="url(#shadow)">
  <rect x="{qx}" y="{qy}" width="{qw}" height="{qh}" rx="16" fill="#2a2a2c"/>
  <circle cx="{qx + 22}" cy="{qy + 22}" r="6" fill="#ff5f57"/><circle cx="{qx + 42}" cy="{qy + 22}" r="6" fill="#febc2e"/><circle cx="{qx + 62}" cy="{qy + 22}" r="6" fill="#28c840"/>
  <text x="{qx + qw / 2}" y="{qy + 27}" text-anchor="middle" font-family="{SANS}" font-size="14" font-weight="600" fill="#e5e5e7">IMG_2041.HEIC</text>
  {beach(qx, qy + 44, qw, qh - 44 - 66, "qclip", 0)}
  <text x="{qx + 22}" y="{qy + qh - 38}" font-family="{SANS}" font-size="15" fill="#e5e5e7">Found by what is in it: no tags, no file name.</text>
  <text x="{qx + 22}" y="{qy + qh - 16}" font-family="{SANS}" font-size="13.5" fill="#9a9aa0">14 July 2025 · Lisbon, Portugal</text>
</g>"""
parts.append(ql)
show("ql", S2 + 3.7, S2_END, fade=0.35, enter="scale(.6)", exit="scale(1.04)")

# Scene 3: the terminal
S3, S3_END = 17.4, 24.6
tx, ty, tw3, th3 = 200, 110, 880, 500
parts.append(caption("cap3", "From the command line", "lantern"))
show("cap3", S3 + 0.4, S3_END, enter="translate(-20px,0px)", exit="translate(-20px,0px)")
term = f"""<g id="term" filter="url(#shadow)">
  <rect x="{tx}" y="{ty}" width="{tw3}" height="{th3}" rx="14" fill="#0f1115" stroke="#ffffff" stroke-opacity=".12"/>
  <circle cx="{tx + 22}" cy="{ty + 22}" r="6" fill="#ff5f57"/><circle cx="{tx + 42}" cy="{ty + 22}" r="6" fill="#febc2e"/><circle cx="{tx + 62}" cy="{ty + 22}" r="6" fill="#28c840"/>
  <text x="{tx + tw3 / 2}" y="{ty + 27}" text-anchor="middle" font-family="{SANS}" font-size="13" fill="#8a909c">zsh — ~/Projects</text>
</g>"""
parts.append(term)
show("term", S3, S3_END, enter="translate(0px,40px) scale(.97)", exit="scale(.97)")
L = 26
c1 = 'lantern search "def add_two" --only code'
c2 = "lantern search \"electricity invoice\" --json | jq -r '.results[0].path'"
mono = f'font-family="{MONO}" font-size="15.5"'
lx, l0 = tx + 26, ty + 70
parts.append(f'<text id="pr1" x="{lx}" y="{l0}" {mono} fill="#7ee0a8">$</text>')
show("pr1", S3 + 0.3, S3_END, fade=0.1)
parts.append(typing("c1_", c1, S3 + 0.6, 30, S3_END - 0.2, lx + 20, l0, f'{mono} fill="#e8eaf0"'))
out1 = [
    ("#9aa0aa", "definitions"),
    ("#e8eaf0", "  1. ~/Projects/calc/ops.py:200        def add_two(x: int) -> int"),
    ("#e8eaf0", "  2. ~/Projects/calc/tests/test_ops.py:12   def test_add_two()"),
    ("#9aa0aa", "files"),
    ("#e8eaf0", "  3. ~/Projects/calc/README.md:31      “add_two adds two to any number …”"),
]
for i, (col, line) in enumerate(out1):
    el = f"o1_{i}"
    style = ' font-weight="700"' if i == 1 else ""
    parts.append(f'<text id="{el}" x="{lx}" y="{l0 + L * (i + 1)}" {mono} fill="{col}"{style} xml:space="preserve">{escape(line)}</text>')
    show(el, S3 + 2.2 + i * 0.12, S3_END, fade=0.15)
l1 = l0 + L * 7
parts.append(f'<text id="pr2" x="{lx}" y="{l1}" {mono} fill="#7ee0a8">$</text>')
show("pr2", S3 + 3.0, S3_END, fade=0.1)
parts.append(typing("c2_", c2, S3 + 3.2, 34, S3_END - 0.2, lx + 20, l1, f'{mono} fill="#e8eaf0"'))
t_done = S3 + 3.2 + len(c2) / 34 + 0.35
for i, line in enumerate(["/Users/me/Documents/Rachunki/faktura_prad_pazdziernik.pdf"]):
    el = f"o2_{i}"
    parts.append(f'<text id="{el}" x="{lx}" y="{l1 + L * (i + 1)}" {mono} fill="#f2b84b">{escape(line)}</text>')
    show(el, t_done + i * 0.12, S3_END, fade=0.15)
parts.append(f'<text id="note3" x="{lx}" y="{l1 + L * 3}" font-family="{SANS}" font-size="15" fill="#8a909c">An English question found a Polish invoice: search goes by meaning, across languages.</text>')
show("note3", t_done + 0.5, S3_END, fade=0.3)

# Outro
parts.append(f"""<g id="outro">{lantern_icon(W / 2 - 40, 210, 80)}
  <text x="{W / 2}" y="350" text-anchor="middle" font-family="{SANS}" font-size="40" font-weight="700" fill="#fff">Files, code, photos, recordings.</text>
  <text x="{W / 2}" y="396" text-anchor="middle" font-family="{SANS}" font-size="22" fill="#cfd5e2">Found by meaning, entirely on your Mac. No account, no cloud.</text>
  <text x="{W / 2}" y="470" text-anchor="middle" font-family="{MONO}" font-size="20" fill="#f2b84b">github.com/misterekkk/lantern</text></g>""")
show("outro", S3_END, T, fade=0.5, enter="scale(.96)")
css.append("#outro{transform-box:view-box;transform-origin:50% 50%}")

svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" aria-label="Lantern in action: its search panel, Raycast and the command line">
<title>Lantern in action</title>
<style>{''.join(css)}</style>
{''.join(parts)}
</svg>
"""
OUT.write_text(svg)
print(f"{OUT} ({len(svg) // 1024} KB, {T:.0f} s loop)")
