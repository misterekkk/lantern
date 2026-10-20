"""Drawings shared by the showcase (build.py) and the website demo (demo_art.py): own vector art, no photos."""

from html import escape

W, H = 1280, 720
SANS = "-apple-system, BlinkMacSystemFont, 'SF Pro Text', 'Inter', 'Segoe UI', 'Helvetica Neue', Arial, sans-serif"
MONO = "ui-monospace, 'SF Mono', Menlo, Consolas, 'Liberation Mono', monospace"


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




def park(x: float, y: float, w: float, h: float, clip: str) -> str:
    """A photo: the same dog in a park (the demo's second photo result)."""
    s = h / 260
    cx, base = x + w * 0.46, y + h * 0.8
    return f"""<g clip-path="url(#{clip})">
  <defs><linearGradient id="psky{clip}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#9fd3f5"/><stop offset="1" stop-color="#e6f4fb"/></linearGradient></defs>
  <rect x="{x}" y="{y}" width="{w}" height="{h}" fill="url(#psky{clip})"/>
  <circle cx="{x + w * 0.2}" cy="{y + h * 0.2}" r="{h * 0.07}" fill="#fff6d5"/>
  <path d="M{x} {y + h * 0.6} Q{x + w * .35} {y + h * 0.5} {x + w * .7} {y + h * 0.58} T{x + w} {y + h * 0.55} V{y + h} H{x} Z" fill="#7cc36a"/>
  <path d="M{x} {y + h * 0.72} Q{x + w * .4} {y + h * 0.64} {x + w} {y + h * 0.72} V{y + h} H{x} Z" fill="#5fae52"/>
  <rect x="{x + w * 0.8}" y="{y + h * 0.36}" width="{w * 0.035}" height="{h * 0.3}" fill="#7a4a24"/>
  <circle cx="{x + w * 0.82}" cy="{y + h * 0.32}" r="{h * 0.16}" fill="#3f8f3a"/>
  <g transform="translate({cx},{base}) scale({s})">
    <ellipse cx="0" cy="18" rx="46" ry="6" fill="#000" opacity=".15"/>
    <rect x="-34" y="-4" width="9" height="24" rx="4" fill="#cfcfcf"/><rect x="18" y="-4" width="9" height="24" rx="4" fill="#cfcfcf"/>
    <rect x="-22" y="-2" width="9" height="22" rx="4" fill="#e2e2e2"/><rect x="28" y="-2" width="9" height="22" rx="4" fill="#e2e2e2"/>
    <ellipse cx="0" cy="-14" rx="42" ry="20" fill="#ececec"/>
    <path d="M-40 -20 Q-62 -40 -56 -52" stroke="#ececec" stroke-width="7" fill="none" stroke-linecap="round"/>
    <circle cx="44" cy="-34" r="17" fill="#ececec"/><ellipse cx="60" cy="-30" rx="11" ry="8" fill="#f5f5f5"/>
    <circle cx="69" cy="-31" r="3.5" fill="#2b1a0e"/><circle cx="47" cy="-38" r="2.6" fill="#2b1a0e"/>
    <path d="M34 -46 Q30 -26 40 -22" fill="#bdbdbd"/>
  </g>
  <circle cx="{cx + w * 0.22}" cy="{base + h * 0.04}" r="{h * 0.035}" fill="#e4572e"/>
</g>"""
