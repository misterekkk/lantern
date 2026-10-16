/* Lantern demo: a scripted walkthrough on a simulated Mac desktop.
 * Steps: open Lantern in the menu bar → start it → index new files → close the menu → ⌥Space → type a suggested
 * phrase → results → ⌘Y preview → another search … → the end card.
 * Development: ?step=menu|found|indexing|ready|shortcut|type|results|preview&scenario=photo|video|code|song
 *   (&typed=N: the first N characters already typed)
 */
"use strict";

const $ = (sel) => document.querySelector(sel);
const REPO = "https://github.com/misterekkk/lantern";

/* ---------- icons (own SVG drawings) ---------- */

const ICON = {
  lantern: `<svg viewBox="0 0 20 20"><circle cx="8.5" cy="8.5" r="5.2" fill="none" stroke="#fff" stroke-width="1.8"/><path d="M12.4 12.4 16.5 16.5" stroke="#fff" stroke-width="2.2" stroke-linecap="round"/><path d="M8.5 5.3 9.2 7.8 11.7 8.5 9.2 9.2 8.5 11.7 7.8 9.2 5.3 8.5 7.8 7.8Z" fill="#fff"/></svg>`,
  wifi: `<svg viewBox="0 0 20 20" fill="none" stroke="#fff" stroke-width="1.8" stroke-linecap="round"><path d="M2.5 7.5a11 11 0 0 1 15 0"/><path d="M5.2 10.4a7 7 0 0 1 9.6 0"/><path d="M7.9 13.2a3 3 0 0 1 4.2 0"/><circle cx="10" cy="15.8" r="1" fill="#fff" stroke="none"/></svg>`,
  battery: `<svg viewBox="0 0 26 20"><rect x="1" y="5" width="21" height="10" rx="3" fill="none" stroke="#fff" stroke-opacity=".55" stroke-width="1.3"/><rect x="2.8" y="6.8" width="15" height="6.4" rx="1.6" fill="#fff"/><rect x="23" y="8" width="1.8" height="4" rx=".9" fill="#fff" fill-opacity=".55"/></svg>`,
  search: `<svg viewBox="0 0 20 20" fill="none" stroke="#fff" stroke-width="1.8"><circle cx="8.5" cy="8.5" r="5"/><path d="M12.3 12.3 16 16" stroke-linecap="round"/></svg>`,
  control: `<svg viewBox="0 0 20 20" fill="none" stroke="#fff" stroke-width="1.5"><rect x="2" y="3.5" width="16" height="5.5" rx="2.75"/><circle cx="5.2" cy="6.25" r="1.6" fill="#fff"/><rect x="2" y="11" width="16" height="5.5" rx="2.75"/><circle cx="14.8" cy="13.75" r="1.6" fill="#fff"/></svg>`,
  glyph: `<svg viewBox="0 0 26 26" fill="none" stroke="#fff" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="M16.2 16.2 22 22" stroke-linecap="round"/><path d="M11 6.6 11.9 10.1 15.4 11 11.9 11.9 11 15.4 10.1 11.9 6.6 11 10.1 10.1Z" fill="#fff" stroke="none"/></svg>`,
  doc: `<svg viewBox="0 0 56 64"><path d="M6 2h30l14 14v44a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2z" fill="#f4f5f7"/><path d="M36 2v12a2 2 0 0 0 2 2h12" fill="#d7dbe2"/><g stroke="#9aa3b2" stroke-width="2.4" stroke-linecap="round"><path d="M13 28h28M13 36h28M13 44h20"/></g></svg>`,
  md: `<svg viewBox="0 0 56 64"><path d="M6 2h30l14 14v44a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2z" fill="#f4f5f7"/><path d="M36 2v12a2 2 0 0 0 2 2h12" fill="#d7dbe2"/><text x="27" y="44" font-family="-apple-system,Helvetica,Arial" font-size="15" font-weight="800" text-anchor="middle" fill="#6b4fbb">MD</text></svg>`,
  py: `<svg viewBox="0 0 56 64"><path d="M6 2h30l14 14v44a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2z" fill="#f4f5f7"/><path d="M36 2v12a2 2 0 0 0 2 2h12" fill="#d7dbe2"/><rect x="13" y="26" width="15" height="15" rx="4" fill="#3b77a8"/><rect x="26" y="34" width="15" height="15" rx="4" fill="#f2c94c"/></svg>`,
  txt: `<svg viewBox="0 0 56 64"><path d="M6 2h30l14 14v44a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2z" fill="#f4f5f7"/><path d="M36 2v12a2 2 0 0 0 2 2h12" fill="#d7dbe2"/><g stroke="#9aa3b2" stroke-width="2.4" stroke-linecap="round"><path d="M13 30h28M13 38h28M13 46h14"/></g></svg>`,
  video: `<svg viewBox="0 0 56 64"><rect x="2" y="10" width="52" height="44" rx="6" fill="#26313f"/><g fill="#fff" fill-opacity=".85"><rect x="6" y="14" width="5" height="4" rx="1"/><rect x="6" y="46" width="5" height="4" rx="1"/><rect x="45" y="14" width="5" height="4" rx="1"/><rect x="45" y="46" width="5" height="4" rx="1"/></g><path d="M23 24v16l13-8z" fill="#fff"/></svg>`,
  audio: `<svg viewBox="0 0 56 64"><rect x="4" y="8" width="48" height="48" rx="11" fill="url(#ga)"/><defs><linearGradient id="ga" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#fb7185"/><stop offset="1" stop-color="#e11d48"/></linearGradient></defs><path d="M33 19v19.5a5 5 0 1 1-3-4.6V23l-9 2.3V41a5 5 0 1 1-3-4.6V22.5z" fill="#fff"/></svg>`,
  folder: `<svg viewBox="0 0 56 64"><path d="M3 16a4 4 0 0 1 4-4h14l5 5h23a4 4 0 0 1 4 4v29a4 4 0 0 1-4 4H7a4 4 0 0 1-4-4z" fill="#5aa9f8"/><path d="M3 24a4 4 0 0 1 4-4h42a4 4 0 0 1 4 4v26a4 4 0 0 1-4 4H7a4 4 0 0 1-4-4z" fill="#79bdfb"/></svg>`,
  play: `<svg viewBox="0 0 16 16"><path d="M4 2.5v11l9-5.5z" fill="#fff"/></svg>`,
  menuSearch: `<svg viewBox="0 0 16 16" fill="none" stroke="#fff" stroke-width="1.5"><circle cx="7" cy="7" r="4.2"/><path d="M10.2 10.2 13.5 13.5" stroke-linecap="round"/></svg>`,
  menuPause: `<svg viewBox="0 0 16 16" fill="#fff"><rect x="4" y="3" width="2.6" height="10" rx="1"/><rect x="9.4" y="3" width="2.6" height="10" rx="1"/></svg>`,
  menuFolder: `<svg viewBox="0 0 16 16" fill="none" stroke="#fff" stroke-width="1.4"><path d="M2 4.5h4l1.5 1.5H14v6.5H2z"/><path d="M11 8.5v3M9.5 10h3"/></svg>`,
  menuGear: `<svg viewBox="0 0 16 16" fill="none" stroke="#fff" stroke-width="1.4"><circle cx="8" cy="8" r="2.3"/><path d="M8 1.8v2M8 12.2v2M1.8 8h2M12.2 8h2M3.6 3.6l1.4 1.4M11 11l1.4 1.4M3.6 12.4 5 11M11 5l1.4-1.4"/></svg>`,
  menuPower: `<svg viewBox="0 0 16 16" fill="none" stroke="#fff" stroke-width="1.5" stroke-linecap="round"><path d="M8 2v5.5"/><path d="M4.6 4.2a5 5 0 1 0 6.8 0"/></svg>`,
};

// Dock icons: own drawings on a 58×58 canvas; the rounded shape comes from CSS (clip), so drawings may bleed.
const DOCK = [
  ["files", true, `<svg viewBox="0 0 58 58"><rect width="58" height="58" fill="#e9eef6"/><path d="M11 19a3 3 0 0 1 3-3h10l4 4h16a3 3 0 0 1 3 3v17a3 3 0 0 1-3 3H14a3 3 0 0 1-3-3z" fill="#5aa9f8"/><path d="M11 24a3 3 0 0 1 3-3h30a3 3 0 0 1 3 3v15a3 3 0 0 1-3 3H14a3 3 0 0 1-3-3z" fill="#79bdfb"/></svg>`],
  ["browser", true, `<svg viewBox="0 0 58 58"><defs><linearGradient id="db" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#38bdf8"/><stop offset="1" stop-color="#2563eb"/></linearGradient></defs><rect width="58" height="58" fill="#f8fafc"/><circle cx="29" cy="29" r="20" fill="url(#db)"/><g fill="none" stroke="#fff" stroke-opacity=".85" stroke-width="1.6"><ellipse cx="29" cy="29" rx="8" ry="20"/><path d="M9 29h40M12 19h34M12 39h34"/></g></svg>`],
  ["mail", false, `<svg viewBox="0 0 58 58"><defs><linearGradient id="dmail" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#60a5fa"/><stop offset="1" stop-color="#1d4ed8"/></linearGradient></defs><rect width="58" height="58" fill="url(#dmail)"/><rect x="11" y="17" width="36" height="25" rx="3" fill="#fff"/><path d="M12 19l17 13 17-13" fill="none" stroke="#93c5fd" stroke-width="2.4" stroke-linejoin="round"/></svg>`],
  ["chat", false, `<svg viewBox="0 0 58 58"><defs><linearGradient id="dc" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#4ade80"/><stop offset="1" stop-color="#16a34a"/></linearGradient></defs><rect width="58" height="58" fill="url(#dc)"/><path d="M29 14c-10 0-18 6.5-18 14.5 0 4.6 2.6 8.6 6.7 11.3L16 46l7.6-3.9c1.7.5 3.5.7 5.4.7 10 0 18-6.5 18-14.3S39 14 29 14z" fill="#fff"/></svg>`],
  ["calendar", false, `<svg viewBox="0 0 58 58"><rect width="58" height="58" fill="#fff"/><rect width="58" height="17" fill="#ef4444"/><text x="29" y="13" font-family="-apple-system,Helvetica,Arial" font-size="9" font-weight="700" fill="#fff" text-anchor="middle" id="calMonth">OCT</text><text x="29" y="46" font-family="-apple-system,Helvetica,Arial" font-size="26" font-weight="300" fill="#1f2937" text-anchor="middle" id="calDay">16</text></svg>`],
  ["notes", false, `<svg viewBox="0 0 58 58"><rect width="58" height="58" fill="#fff6d1"/><rect width="58" height="15" fill="#f6c443"/><g stroke="#d5c79a" stroke-width="2"><path d="M10 26h38M10 34h38M10 42h28"/></g></svg>`],
  ["pictures", false, `<svg viewBox="0 0 58 58"><defs><linearGradient id="dp" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#7dd3fc"/><stop offset="1" stop-color="#38bdf8"/></linearGradient></defs><rect width="58" height="58" fill="url(#dp)"/><circle cx="40" cy="18" r="6" fill="#fde68a"/><path d="M0 44l16-16 12 12 8-7 22 18v7H0z" fill="#16a34a"/><path d="M0 50l18-12 16 20H0z" fill="#15803d"/></svg>`],
  ["music", false, `<svg viewBox="0 0 58 58"><defs><linearGradient id="dm" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#fb7185"/><stop offset="1" stop-color="#e11d48"/></linearGradient></defs><rect width="58" height="58" fill="url(#dm)"/><path d="M39 13v23.5a6.2 6.2 0 1 1-3.6-5.6V21.2l-12.8 3.2V40a6.2 6.2 0 1 1-3.6-5.6V18.7z" fill="#fff"/></svg>`],
  ["code", true, `<svg viewBox="0 0 58 58"><defs><linearGradient id="dcode" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#1e3a8a"/><stop offset="1" stop-color="#0ea5e9"/></linearGradient></defs><rect width="58" height="58" fill="url(#dcode)"/><path d="M21 19l-10 10 10 10M37 19l10 10-10 10" fill="none" stroke="#fff" stroke-width="3.6" stroke-linecap="round" stroke-linejoin="round"/><path d="M32 16l-6 26" stroke="#bae6fd" stroke-width="3" stroke-linecap="round"/></svg>`],
  ["terminal", false, `<svg viewBox="0 0 58 58"><rect width="58" height="58" fill="#1f2328"/><rect x="5" y="5" width="48" height="48" rx="9" fill="#0d1014" stroke="#3a4048"/><path d="M15 22l8 6-8 6" stroke="#e6edf3" stroke-width="3" fill="none" stroke-linecap="round" stroke-linejoin="round"/><path d="M27 36h15" stroke="#e6edf3" stroke-width="3" stroke-linecap="round"/></svg>`],
  ["settings", false, `<svg viewBox="0 0 58 58"><defs><linearGradient id="dset" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#d1d5db"/><stop offset="1" stop-color="#6b7280"/></linearGradient></defs><rect width="58" height="58" fill="url(#dset)"/><g fill="#374151"><circle cx="29" cy="29" r="12"/><g id="teeth">${[0, 45, 90, 135, 180, 225, 270, 315].map((r) => `<rect x="26" y="9" width="6" height="9" rx="1.5" transform="rotate(${r} 29 29)"/>`).join("")}</g></g><circle cx="29" cy="29" r="5.5" fill="#d1d5db"/></svg>`],
  "divider",
  ["lantern", true, `<svg viewBox="0 0 58 58"><defs><linearGradient id="dl" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#a855f7"/><stop offset="1" stop-color="#3b82f6"/></linearGradient></defs><rect width="58" height="58" fill="url(#dl)"/><circle cx="26" cy="26" r="11" fill="none" stroke="#fff" stroke-width="3.6"/><path d="M34.5 34.5l8.5 8.5" stroke="#fff" stroke-width="4.6" stroke-linecap="round"/><path d="M26 18.7l1.5 5.8 5.8 1.5-5.8 1.5-1.5 5.8-1.5-5.8-5.8-1.5 5.8-1.5z" fill="#fff"/></svg>`],
  ["trash", false, `<svg viewBox="0 0 58 58" class="bare"><path d="M16 17h26l-2.2 31a3 3 0 0 1-3 2.8H21.2a3 3 0 0 1-3-2.8z" fill="rgba(255,255,255,.28)" stroke="rgba(255,255,255,.75)" stroke-width="1.6"/><path d="M13 15h32" stroke="rgba(255,255,255,.85)" stroke-width="2.4" stroke-linecap="round"/><g stroke="rgba(255,255,255,.55)" stroke-width="1.4"><path d="M24 22l1 24M29 22v24M34 22l-1 24"/></g></svg>`],
];

/* ---------- illustrations for results (own drawings, no stock photos) ---------- */

const ART = {
  beachDog: `<svg viewBox="0 0 160 120" preserveAspectRatio="xMidYMid slice"><defs><linearGradient id="bs" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fdba74"/><stop offset=".55" stop-color="#fda4af"/><stop offset="1" stop-color="#93c5fd"/></linearGradient></defs><rect width="160" height="120" fill="url(#bs)"/><circle cx="118" cy="48" r="14" fill="#fff7ed" opacity=".9"/><rect y="62" width="160" height="22" fill="#60a5fa"/><path d="M0 70q20-5 40 0t40 0 40 0 40 0v6H0z" fill="#bfdbfe" opacity=".8"/><path d="M0 84h160v36H0z" fill="#fcd9a8"/><g fill="#5b3a29"><ellipse cx="62" cy="92" rx="17" ry="8"/><circle cx="80" cy="84" r="7"/><path d="M84 80l7-4-2 8z"/><rect x="49" y="96" width="4" height="13" rx="2"/><rect x="58" y="97" width="4" height="12" rx="2"/><rect x="67" y="97" width="4" height="12" rx="2"/><rect x="74" y="95" width="4" height="13" rx="2"/><path d="M45 89q-9-8-6-14" stroke="#5b3a29" stroke-width="4" fill="none" stroke-linecap="round"/></g><circle cx="82" cy="83" r="1.2" fill="#fff"/></svg>`,
  parkDog: `<svg viewBox="0 0 160 120" preserveAspectRatio="xMidYMid slice"><rect width="160" height="120" fill="#bae6fd"/><circle cx="30" cy="26" r="10" fill="#fef3c7"/><path d="M0 70q40-18 80-6t80-4v60H0z" fill="#4ade80"/><path d="M0 86q50-10 100 0t60 0v34H0z" fill="#22c55e"/><circle cx="128" cy="52" r="20" fill="#16a34a"/><rect x="125" y="62" width="6" height="22" fill="#78350f"/><g fill="#e5e7eb"><ellipse cx="70" cy="90" rx="15" ry="7"/><circle cx="85" cy="82" r="6.5"/><path d="M88 76l5-5 1 8z"/><rect x="59" y="94" width="4" height="11" rx="2"/><rect x="67" y="95" width="4" height="10" rx="2"/><rect x="75" y="94" width="4" height="11" rx="2"/></g><circle cx="98" cy="96" r="4" fill="#ef4444"/></svg>`,
  sunset: `<svg viewBox="0 0 160 120" preserveAspectRatio="xMidYMid slice"><defs><linearGradient id="ss" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#7c3aed"/><stop offset=".5" stop-color="#f97316"/><stop offset="1" stop-color="#fde047"/></linearGradient></defs><rect width="160" height="120" fill="url(#ss)"/><circle cx="80" cy="74" r="20" fill="#fff7ed"/><rect y="74" width="160" height="46" fill="#1e3a8a"/><g stroke="#fde68a" stroke-width="2" opacity=".7"><path d="M60 82h40M66 90h28M72 98h16"/></g></svg>`,
  vlog: `<svg viewBox="0 0 320 200" preserveAspectRatio="xMidYMid slice"><rect width="320" height="200" fill="#14532d"/><rect y="0" width="320" height="70" fill="#1e293b"/><g fill="#fde68a" opacity=".9"><circle cx="40" cy="20" r="5"/><circle cx="280" cy="20" r="5"/></g><g fill="#334155"><rect x="0" y="40" width="320" height="30"/></g><g fill="#64748b"><circle cx="20" cy="55" r="4"/><circle cx="40" cy="52" r="4"/><circle cx="60" cy="56" r="4"/><circle cx="80" cy="53" r="4"/><circle cx="100" cy="55" r="4"/><circle cx="120" cy="52" r="4"/><circle cx="140" cy="56" r="4"/><circle cx="160" cy="53" r="4"/><circle cx="180" cy="55" r="4"/><circle cx="200" cy="52" r="4"/><circle cx="220" cy="56" r="4"/><circle cx="240" cy="53" r="4"/><circle cx="260" cy="55" r="4"/><circle cx="280" cy="52" r="4"/><circle cx="300" cy="56" r="4"/></g><g stroke="#e2e8f0" stroke-width="2" fill="none" opacity=".75"><rect x="20" y="82" width="280" height="110"/><path d="M160 82v110"/><circle cx="160" cy="137" r="20"/></g><g fill="#ef4444"><circle cx="120" cy="120" r="4"/><circle cx="190" cy="150" r="4"/></g><g fill="#3b82f6"><circle cx="210" cy="118" r="4"/><circle cx="140" cy="160" r="4"/></g><circle cx="168" cy="134" r="3" fill="#fff"/></svg>`,
  cover: `<svg viewBox="0 0 160 160" preserveAspectRatio="xMidYMid slice"><defs><linearGradient id="cv" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#1e1b4b"/><stop offset="1" stop-color="#be185d"/></linearGradient></defs><rect width="160" height="160" fill="url(#cv)"/><circle cx="110" cy="50" r="18" fill="#fde68a" opacity=".85"/><path d="M0 120q40-30 80-10t80-20v70H0z" fill="#0f172a" opacity=".7"/><g fill="#fff" opacity=".8"><circle cx="30" cy="30" r="1.2"/><circle cx="60" cy="20" r="1"/><circle cx="140" cy="100" r="1"/><circle cx="20" cy="80" r="1"/></g></svg>`,
};

/* ---------- scenarios ---------- */

const SCENARIOS = [
  {
    id: "photo", label: "a photo", phrase: "dog on the beach",
    typeHint: "No file is called that — Lantern looks at what is in your photos.",
    blurb: "Photos come first because that’s what you meant — Lantern looks at what is <b>in</b> the picture, not its file name.",
    sections: [
      ["Photos", [
        { thumb: ART.beachDog, title: "IMG_2041.heic", sub: "~/Pictures/Hel 2025", source: "image" },
        { thumb: ART.parkDog, title: "rex_park.jpg", sub: "~/Pictures/Rex", source: "image" },
        { thumb: ART.sunset, title: "IMG_2077.heic", sub: "~/Pictures/Hel 2025", source: "image" },
      ]],
      ["Files", [
        { icon: ICON.md, title: "trip-notes.md · Day 3", sub: "~/Documents — lines 12–20", preview: "…took Rex to the <mark>beach</mark> at sunrise, he chased the waves for an hour…", source: "meaning · words" },
      ]],
    ],
    preview: { kind: "image", art: ART.beachDog, title: "IMG_2041.heic" },
  },
  {
    id: "video", label: "a moment in a video", phrase: "when we talked about the match",
    typeHint: "Describe it in your own words — Lantern understands what was said in your videos.",
    blurb: "Lantern transcribes recordings on your Mac, so it finds <b>the moment</b> something was said — here at 1:48.",
    sections: [
      ["Files", [
        { thumb: ART.vlog, badge: "1:48", title: "vlog_weekend.mp4 · 1:48", sub: "~/Movies — 1:48–2:13", preview: "“…and then the <mark>match</mark>, two–one in the very last minute, the whole stadium went silent…”", source: "meaning · words" },
        { icon: ICON.txt, title: "football.txt", sub: "~/Documents — lines 1–8", preview: "Saturday: Lech vs Legia, tickets in the drawer…", source: "meaning" },
      ]],
    ],
    preview: { kind: "video", art: ART.vlog, title: "vlog_weekend.mp4 · 1:48", start: 105, length: 412,
      transcript: "“…and then the match, two–one in the very last minute, the whole stadium went silent and then just exploded…”" },
  },
  {
    id: "code", label: "code at its line", phrase: "def add_two(x, y):",
    typeHint: "Paste a line of code — Lantern finds where it is defined.",
    blurb: "Paste a line of code and Lantern jumps to the <b>exact line</b> where it is defined — in your own editor.",
    sections: [
      ["Definitions", [
        { icon: ICON.py, title: "add_two · math_utils.py", sub: "~/Projects/calc — line 200", preview: "def add_two(x, y)", code: true, source: "definition" },
      ]],
      ["Files", [
        { icon: ICON.py, title: "calculator.py · total", sub: "~/Projects/calc — lines 41–48", preview: "result = add_two(subtotal, tax)", code: true, source: "meaning · words" },
      ]],
    ],
    preview: { kind: "code", title: "math_utils.py — line 200", from: 194, hit: [200, 201], lines: [
      ['<span class="tok-c"># --- arithmetic helpers ---</span>'],
      [""],
      ['<span class="tok-k">def</span> <span class="tok-f">add_one</span>(x):'],
      ['    <span class="tok-k">return</span> x + <span class="tok-n">1</span>'],
      [""],
      [""],
      ['<span class="tok-k">def</span> <span class="tok-f">add_two</span>(x, y):'],
      ['    <span class="tok-k">return</span> x + y'],
      [""],
      [""],
      ['<span class="tok-k">def</span> <span class="tok-f">mean</span>(values):'],
      ['    <span class="tok-k">return</span> <span class="tok-f">sum</span>(values) / <span class="tok-f">len</span>(values)'],
    ]},
  },
  {
    id: "song", label: "a song by its lyrics", phrase: "song about a summer night",
    typeHint: "The title says nothing about it — Lantern listens to the lyrics.",
    blurb: "Songs and podcasts are searchable by what is sung or said — even if the title says nothing about it.",
    sections: [
      ["Files", [
        { thumb: ART.cover, badge: "0:42", title: "track_07.m4a · 0:42", sub: "~/Music/Demos — 0:42–1:07", preview: "“…we drove all night under the <mark>summer</mark> sky, windows down…”", source: "words · meaning" },
        { icon: ICON.md, title: "setlist.md", sub: "~/Documents — lines 3–6", preview: "7. the slow one about July, capo on 2", source: "meaning" },
      ]],
    ],
    preview: { kind: "audio", title: "track_07.m4a · 0:42", start: 39, length: 214,
      transcript: "“…we drove all night under the summer sky, windows down, singing every word we knew…”" },
  },
];

/* ---------- state ---------- */

const state = {
  step: "intro",
  scenario: null,
  typed: "",
  done: new Set(),
  timers: [],
};

function later(ms, fn) {
  const id = setTimeout(fn, ms);
  state.timers.push(id);
  return id;
}
function clearTimers() {
  state.timers.forEach(clearTimeout);
  state.timers = [];
}

/* ---------- static parts ---------- */

function buildChrome() {
  $("#lanternItem").innerHTML = ICON.lantern;
  document.querySelectorAll("[data-icon]").forEach((el) => { el.innerHTML = ICON[el.dataset.icon]; });
  document.querySelectorAll(".desktop-file").forEach((el) => {
    el.querySelector("i").innerHTML = ICON[el.dataset.kind] || ICON.doc;
  });
  $(".panel-glyph").innerHTML = ICON.glyph;
  $("#dock").innerHTML = DOCK.map((d) =>
    d === "divider" ? `<span class="dock-divider"></span>` :
      `<span class="dock-icon${d[1] ? " running" : ""}${d[0] === "trash" ? " bare" : ""}" title="${d[0]}">${d[2]}</span>`).join("");
  const now = new Date();  // the calendar icon shows today, like the real one
  $("#calMonth").textContent = now.toLocaleDateString("en-US", { month: "short" }).toUpperCase();
  $("#calDay").textContent = String(now.getDate());
  tickClock();
  setInterval(tickClock, 15000);
}

function tickClock() {
  const now = new Date();
  const day = now.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });
  const time = now.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
  $("#clock").textContent = `${day.replace(",", "")}  ${time}`;
}

function fitStage() {
  const desktop = $("#desktop");
  const s = Math.min((innerWidth * 0.96) / 1440, (innerHeight * 0.96) / 900, 1.25);
  desktop.style.transform = `translate(-50%, -50%) scale(${s})`;
  const small = innerWidth < 760 && !document.body.dataset.anyway;
  $("#smallScreen").hidden = !small;
}

/* ---------- hint bubble ---------- */

function hint(html, { x, y, arrow = "none", actions = [], cls = "" } = {}) {
  const el = $("#hint");
  el.hidden = false;
  el.className = `hint glass ${cls}`;
  el.dataset.arrow = arrow;
  el.style.left = `${x}px`;
  el.style.top = `${y}px`;
  $("#hintText").innerHTML = html;
  const box = $("#hintActions");
  box.innerHTML = "";
  for (const a of actions) {
    const b = document.createElement(a.href ? "a" : "button");
    b.className = a.cls || "pill";
    b.innerHTML = a.label;
    if (a.href) { b.href = a.href; b.target = "_blank"; b.rel = "noopener"; }
    else { b.type = "button"; b.addEventListener("click", a.onClick); }
    box.appendChild(b);
  }
  el.style.animation = "none";
  void el.offsetWidth;  // restart the entrance animation
  el.style.animation = "";
}
function hideHint() { $("#hint").hidden = true; }

function attention(el, on = true) {
  document.querySelectorAll(".attention").forEach((e) => e.classList.remove("attention"));
  if (el && on) el.classList.add("attention");
}

/* ---------- menu ---------- */

function openMenu() {
  $("#menu").hidden = false;
  $("#lanternItem").setAttribute("aria-expanded", "true");
}
function closeMenu() {
  $("#menu").hidden = true;
  $("#lanternItem").setAttribute("aria-expanded", "false");
}

function setChip(text, color = "") {
  const chip = $("#menuChip");
  chip.textContent = text;
  chip.className = `chip ${color}`;
}

function menuBody(html) { $("#menuBody").innerHTML = html; }

function menuActions() {
  return `<div class="menu-actions">
    <button class="menu-action" type="button" data-act="search">${ICON.menuSearch}Search…<kbd>⌥ Space</kbd></button>
    <button class="menu-action" type="button">${ICON.menuPause}Pause Indexing</button>
    <button class="menu-action" type="button">${ICON.menuFolder}Add Folder…</button>
    <button class="menu-action" type="button">${ICON.menuGear}Settings…</button>
    <button class="menu-action" type="button">${ICON.menuPower}Quit Lantern</button>
  </div>`;
}

/* ---------- steps ---------- */

const STEPS = {
  intro() {
    closeMenu();
    attention($("#lanternItem"));
    hint("Lantern lives in your menu bar. <b>Click its icon</b> to open it.", { x: 1052, y: 44, arrow: "up-right" });
  },

  menu() {
    openMenu();
    setChip("Off");
    menuBody(`<div class="menu-card"><p>Lantern is not running.</p>
      <p class="muted">Start it to search your folders by meaning.</p>
      <div class="row"><button class="btn primary wide" id="startBtn" type="button">Start Lantern</button></div></div>`);
    attention($("#startBtn"));
    $("#startBtn").addEventListener("click", () => go("starting"));
    hint("Start Lantern. It runs quietly in the background — and <b>only on your Mac</b>.", { x: 640, y: 108, arrow: "none" });
  },

  starting() {
    openMenu();
    attention(null);
    setChip("Starting…");
    menuBody(`<div class="menu-card"><p>Starting…</p><p class="muted">Loading the model (it never leaves this Mac).</p>
      <div class="progress"><i style="width:35%;transition:width .9s"></i></div></div>`);
    hideHint();
    later(900, () => go("found"));
  },

  found() {
    openMenu();
    setChip("Waiting for approval", "blue");
    menuBody(`<div class="menu-card"><p>Found <b>1,248</b> new files</p>
      <p class="muted">in Documents, Pictures, Movies, Music and Projects</p>
      <div class="row"><button class="btn primary" id="indexBtn" type="button">Index Now</button>
      <button class="btn" type="button" id="laterBtn">Later</button></div></div>`);
    attention($("#indexBtn"));
    $("#indexBtn").addEventListener("click", () => go("indexing"));
    $("#laterBtn").addEventListener("click", () => hint("In this tour, let’s index them now. 🙂", { x: 640, y: 108 }));
    hint("Lantern never reads new files without asking. <b>Index them now.</b>", { x: 640, y: 108, arrow: "none" });
  },

  indexing() {
    openMenu();
    attention(null);
    setChip("Indexing", "purple");
    menuBody(`<div class="menu-card"><p id="idxLabel">Indexing documents…</p>
      <div class="progress"><i id="idxBar"></i></div><p class="muted" id="idxCount">0 of 1,248</p></div>`);
    hint("Everything is processed <b>on your Mac</b> — text, code, photos and recordings. Nothing is uploaded.", { x: 640, y: 108 });
    const kinds = ["documents", "code", "photos", "recordings", "videos"];
    const total = 1248, duration = 4200, t0 = performance.now();
    const tick = () => {
      if (state.step !== "indexing") return;
      const p = Math.min(1, (performance.now() - t0) / duration);
      const eased = 1 - Math.pow(1 - p, 2);
      $("#idxBar").style.width = `${eased * 100}%`;
      $("#idxCount").textContent = `${Math.round(eased * total).toLocaleString("en-US")} of 1,248`;
      $("#idxLabel").textContent = `Indexing ${kinds[Math.min(kinds.length - 1, Math.floor(eased * kinds.length))]}…`;
      if (p < 1) requestAnimationFrame(tick); else later(250, () => go("ready"));
    };
    requestAnimationFrame(tick);
  },

  ready() {
    openMenu();
    setChip("Ready", "green");
    menuBody(`<div class="stats"><div class="stat"><b>1,248</b><span>files</span></div>
      <div class="stat"><b>9,410</b><span>chunks</span></div><div class="stat"><b>412 MB</b><span>memory</span></div></div>
      ${menuActions()}`);
    $("#menuBody [data-act=search]").addEventListener("click", () => { closeMenu(); go("panel"); });
    notify("Indexed 1,248 files. Press ⌥ Space to search.");
    hint("All set. <b>Close the menu</b> — click anywhere on the desktop.", { x: 640, y: 108 });
  },

  shortcut() {
    closeMenu();
    $("#panel").hidden = true;
    $("#shortcut").hidden = false;
    attention($("#shortcut"));
    hint("Search from anywhere with <b>⌥ Option + Space</b> — press it, or click the keys.", { x: 570, y: 266, arrow: "down" });
  },

  panel() {
    closeMenu();
    $("#shortcut").hidden = true;
    attention(null);
    $("#panel").hidden = false;
    pickScenario(state.scenario || SCENARIOS[0]);
  },

  type() { /* entered through pickScenario */ },

  results() {
    const sc = state.scenario;
    renderResults(sc);
    hint(`${sc.blurb}<br><br>Press <b>⌘Y</b> to preview it, right here.`, {
      x: 40, y: 196, arrow: "none", actions: [{ label: "Preview (⌘Y)", onClick: () => go("preview") }],
    });
  },

  preview() {
    const sc = state.scenario;
    renderPreview(sc);
    state.done.add(sc.id);
    const left = SCENARIOS.filter((s) => !state.done.has(s.id));
    const actions = left.map((s) => ({ label: `Find ${s.label}`, onClick: () => pickScenario(s) }));
    actions.push({ label: left.length ? "Finish the tour" : "See what’s next", cls: "pill", onClick: () => go("finale") });
    hint(left.length
      ? "In Lantern, <b>↩</b> opens it at this exact spot — the line in your editor, the moment in QuickTime.<br><br>Try another search:"
      : "That’s all four. <b>↩</b> would open each one at the exact spot.", { x: 40, y: 196, actions });
  },

  finale() {
    clearTimers();
    $("#panel").hidden = true;
    closeMenu();
    hint(`<span class="app-icon"></span><h2>That’s Lantern.</h2>
      <p>Search your files, code, photos and recordings by meaning — <b>100% on your Mac</b>, no cloud, no
      telemetry.</p>`, {
      x: 510, y: 250, cls: "finale",
      actions: [
        { label: "Get Lantern on GitHub", href: REPO, cls: "btn primary" },
        { label: "Start over", onClick: restart },
      ],
    });
  },
};

function go(step) {
  state.step = step;
  STEPS[step]();
}

function restart() {
  clearTimers();
  state.done.clear();
  state.scenario = null;
  $("#panel").hidden = true;
  $("#shortcut").hidden = true;
  $("#notification").hidden = true;
  go("intro");
}

/* ---------- notification ---------- */

function notify(text) {
  $("#notificationText").textContent = text;
  const n = $("#notification");
  n.hidden = false;
  later(5200, () => { n.hidden = true; });
}

/* ---------- search panel ---------- */

function pickScenario(sc) {
  state.scenario = sc;
  state.step = "type";
  state.typed = "";
  $("#panelContent").hidden = true;
  $("#panelHints").hidden = true;
  renderQuery();
  $("#queryField").focus({ preventScroll: true });
  hint(`Type <b>“${escapeHtml(sc.phrase)}”</b><br>${sc.typeHint}`, {
    x: 534, y: 252, arrow: "up",
  });
}

function renderQuery() {
  const sc = state.scenario;
  $("#typed").textContent = state.typed;
  $("#ghost").textContent = sc ? sc.phrase.slice(state.typed.length) : "";
  $("#typeForMe").hidden = !sc || state.step !== "type";
}

function typeChar(ch) {
  const sc = state.scenario;
  if (!sc || state.step !== "type") return;
  const expected = sc.phrase[state.typed.length];
  if (expected === undefined) return;
  if (ch.toLowerCase() !== expected.toLowerCase()) {  // only the suggested phrase can be typed
    const f = $("#queryField");
    f.classList.remove("shake");
    void f.offsetWidth;
    f.classList.add("shake");
    return;
  }
  state.typed += expected;
  renderQuery();
  if (state.typed === sc.phrase) later(220, () => go("results"));
}

function typeForMe() {
  const sc = state.scenario;
  if (!sc || state.step !== "type") return;
  const next = () => {
    if (state.step !== "type" || state.typed === sc.phrase) return;
    typeChar(sc.phrase[state.typed.length]);
    later(45 + Math.random() * 40, next);
  };
  next();
}

function renderResults(sc) {
  const content = $("#panelContent");
  content.hidden = false;
  $("#panelHints").hidden = false;
  $("#previewHint").textContent = "preview";
  $("#typeForMe").hidden = true;
  let i = 0;
  content.innerHTML = sc.sections.map(([title, rows]) => `
    <div class="section-title">${title}</div>
    ${rows.map((r) => {
      const media = r.thumb
        ? `<span class="thumb">${r.thumb}${r.badge ? `<span class="badge">${r.badge}</span>` : ""}</span>`
        : `<span class="icon">${r.icon}</span>`;
      const delay = (i++) * 60;
      return `<div class="result${delay === 0 ? " selected" : ""}" style="animation-delay:${delay}ms">${media}
        <div class="body"><div class="title"><span>${r.title}</span><span class="source">${r.source}</span></div>
        <div class="sub">${r.sub}</div>${r.preview ? `<div class="preview${r.code ? " code" : ""}">${r.preview}</div>` : ""}</div></div>`;
    }).join("")}`).join("");
}

function renderPreview(sc) {
  const p = sc.preview;
  const content = $("#panelContent");
  content.hidden = false;
  $("#panelHints").hidden = false;
  $("#previewHint").textContent = "back";
  let body = "";
  if (p.kind === "image") {
    body = `<div class="preview-media">${p.art}</div>`;
  } else if (p.kind === "video") {
    body = `<div class="preview-media">${p.art}${playerBar(p)}</div><p class="transcript">${p.transcript}</p>`;
  } else if (p.kind === "audio") {
    const bars = Array.from({ length: 90 }, (_, k) => 18 + Math.abs(Math.sin(k * 0.55) * 46 + Math.sin(k * 1.7) * 26));
    body = `<div class="preview-media" style="height:200px;background:linear-gradient(135deg,#1e1b4b,#831843)">
      <div class="waveform" id="wave">${bars.map((h) => `<i style="height:${h}px"></i>`).join("")}</div>${playerBar(p)}</div>
      <p class="transcript">${p.transcript}</p>`;
  } else if (p.kind === "code") {
    body = `<div class="code-preview">${p.lines.map((l, k) => {
      const n = p.from + k;
      return `<div class="${n >= p.hit[0] && n <= p.hit[1] ? "hit" : ""}"><span class="ln">${n}</span><span>${l[0] || "&nbsp;"}</span></div>`;
    }).join("")}</div>`;
  }
  content.innerHTML = `<div class="preview-pane"><h3>${p.title}</h3>${body}</div>`;
  if (p.kind === "video" || p.kind === "audio") playFrom(p);
}

function playerBar(p) {
  const matchStart = ((p.start + 3) / p.length) * 100;
  return `<div class="player-bar glass">${ICON.play}<span id="tNow">${clock(p.start)}</span>
    <span class="track"><b style="left:${matchStart}%;width:${(25 / p.length) * 100}%"></b><i id="tBar" style="width:${(p.start / p.length) * 100}%"></i></span>
    <span>${clock(p.length)}</span></div>`;
}

function playFrom(p) {
  const t0 = performance.now();
  const tick = () => {
    if (state.step !== "preview" || state.scenario.preview !== p) return;
    const t = p.start + (performance.now() - t0) / 1000;
    if (!$("#tBar")) return;
    $("#tBar").style.width = `${Math.min(100, (t / p.length) * 100)}%`;
    $("#tNow").textContent = clock(t);
    const wave = $("#wave");
    if (wave) {
      const played = Math.floor((t / p.length) * wave.children.length);
      [...wave.children].forEach((bar, k) => bar.classList.toggle("played", k < played));
    }
    if (t < p.length) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

const clock = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
const escapeHtml = (s) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

/* ---------- input ---------- */

function onKey(e) {
  const panelOpen = !$("#panel").hidden;
  if (e.code === "Space" && e.altKey) {  // ⌥Space (also opens the panel from any later step)
    e.preventDefault();
    if (state.step === "shortcut" || state.step === "ready") {
      $("#shortcut").classList.add("pressed");
      later(140, () => { $("#shortcut").classList.remove("pressed"); go("panel"); });
    }
    return;
  }
  if (!panelOpen) return;
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "y") {
    e.preventDefault();
    if (state.step === "results") go("preview");
    else if (state.step === "preview") go("results");
    return;
  }
  if (e.key === "Escape") {
    e.preventDefault();
    if (state.step === "preview") go("results");
    return;
  }
  if (e.key === "Enter") {
    e.preventDefault();
    if (state.step === "results") go("preview");
    return;
  }
  if (state.step !== "type") return;
  if (e.key === "Backspace") {
    e.preventDefault();
    state.typed = state.typed.slice(0, -1);
    renderQuery();
    return;
  }
  if (e.key.length === 1 && !e.metaKey && !e.ctrlKey) {
    e.preventDefault();
    typeChar(e.key);
  }
}

function wire() {
  $("#lanternItem").addEventListener("click", (e) => {
    e.stopPropagation();
    if (state.step === "intro") go("menu");
    else if (!$("#menu").hidden) {
      closeMenu();
      if (state.step === "ready") go("shortcut");
    } else if (["ready", "shortcut", "results", "preview", "type"].includes(state.step)) {
      openMenu();
    }
  });
  $("#menu").addEventListener("click", (e) => e.stopPropagation());
  $("#panel").addEventListener("click", (e) => e.stopPropagation());
  $("#hint").addEventListener("click", (e) => e.stopPropagation());
  $("#desktop").addEventListener("click", () => {
    if (state.step === "ready" && !$("#menu").hidden) go("shortcut");
    else if (!$("#menu").hidden && state.step !== "starting" && state.step !== "indexing") closeMenu();
  });
  $("#shortcut").addEventListener("click", (e) => {
    e.stopPropagation();
    $("#shortcut").classList.add("pressed");
    later(140, () => { $("#shortcut").classList.remove("pressed"); go("panel"); });
  });
  $("#typeForMe").addEventListener("click", typeForMe);
  $("#continueAnyway").addEventListener("click", () => { document.body.dataset.anyway = "1"; fitStage(); });
  document.addEventListener("keydown", onKey);
  addEventListener("resize", fitStage);
}

/* ---------- start (with ?step=… for development screenshots) ---------- */

function start() {
  buildChrome();
  wire();
  fitStage();
  const q = new URLSearchParams(location.search);
  const step = q.get("step");
  const sc = SCENARIOS.find((s) => s.id === q.get("scenario")) || SCENARIOS[0];
  if (!step) return go("intro");
  if (["type", "results", "preview"].includes(step)) {
    $("#panel").hidden = false;
    pickScenario(sc);
    if (step === "type" && q.get("typed")) { state.typed = sc.phrase.slice(0, Number(q.get("typed"))); renderQuery(); }
    if (step !== "type") { state.typed = sc.phrase; renderQuery(); go("results"); }
    if (step === "preview") go("preview");
    return;
  }
  go(STEPS[step] ? step : "intro");
}

start();
