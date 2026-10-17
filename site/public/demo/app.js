/* Lantern demo: a scripted walkthrough on a simulated Mac desktop.
 * Steps: open Lantern in the menu bar → start it → index new files → close the menu → ⌥Space → type a suggested
 * phrase (the grey completion is accepted with →) → results with filter icons → ⌘Y preview → another search … →
 * the end card. The panel mirrors the app: "Search Lantern", inline completion, filter icons beside the field (a
 * chosen one becomes a chip with its ⌘ number), hints on the selected row, matched words in bold (same folding as the
 * app), apps, settings and quick answers, and the web row.
 * Development: ?step=menu|found|indexing|ready|shortcut|type|results|preview
 *   &scenario=photo|screenshot|video|code|pdf|app|setting|music|answer (&typed=N: the first N characters already typed)
 */
"use strict";

const $ = (sel) => document.querySelector(sel);
const REPO = "https://github.com/misterekkk/lantern";

/* ---------- icons (own SVG drawings) ---------- */

const ICON = {
  lantern: `<svg viewBox="0 0 20 20"><circle cx="8.5" cy="8.5" r="5.2" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M12.4 12.4 16.5 16.5" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/><path d="M8.5 5.3 9.2 7.8 11.7 8.5 9.2 9.2 8.5 11.7 7.8 9.2 5.3 8.5 7.8 7.8Z" fill="currentColor"/></svg>`,
  wifi: `<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M2.5 7.5a11 11 0 0 1 15 0"/><path d="M5.2 10.4a7 7 0 0 1 9.6 0"/><path d="M7.9 13.2a3 3 0 0 1 4.2 0"/><circle cx="10" cy="15.8" r="1" fill="currentColor" stroke="none"/></svg>`,
  battery: `<svg viewBox="0 0 26 20"><rect x="1" y="5" width="21" height="10" rx="3" fill="none" stroke="currentColor" stroke-opacity=".55" stroke-width="1.3"/><rect x="2.8" y="6.8" width="15" height="6.4" rx="1.6" fill="currentColor"/><rect x="23" y="8" width="1.8" height="4" rx=".9" fill="currentColor" fill-opacity=".55"/></svg>`,
  search: `<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="8.5" cy="8.5" r="5"/><path d="M12.3 12.3 16 16" stroke-linecap="round"/></svg>`,
  control: `<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="2" y="3.5" width="16" height="5.5" rx="2.75"/><circle cx="5.2" cy="6.25" r="1.6" fill="currentColor"/><rect x="2" y="11" width="16" height="5.5" rx="2.75"/><circle cx="14.8" cy="13.75" r="1.6" fill="currentColor"/></svg>`,
  glyph: `<svg viewBox="0 0 26 26" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="M16.2 16.2 22 22" stroke-linecap="round"/><path d="M11 6.6 11.9 10.1 15.4 11 11.9 11.9 11 15.4 10.1 11.9 6.6 11 10.1 10.1Z" fill="currentColor" stroke="none"/></svg>`,
  doc: `<svg viewBox="0 0 56 64"><path d="M6 2h30l14 14v44a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2z" fill="#f4f5f7"/><path d="M36 2v12a2 2 0 0 0 2 2h12" fill="#d7dbe2"/><g stroke="#9aa3b2" stroke-width="2.4" stroke-linecap="round"><path d="M13 28h28M13 36h28M13 44h20"/></g></svg>`,
  pdf: `<svg viewBox="0 0 56 64"><path d="M6 2h30l14 14v44a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2z" fill="#f4f5f7"/><path d="M36 2v12a2 2 0 0 0 2 2h12" fill="#d7dbe2"/><rect x="9" y="34" width="38" height="16" rx="3" fill="#dc2626"/><text x="28" y="46" font-family="-apple-system,Helvetica,Arial" font-size="11" font-weight="800" text-anchor="middle" fill="#fff">PDF</text></svg>`,
  md: `<svg viewBox="0 0 56 64"><path d="M6 2h30l14 14v44a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2z" fill="#f4f5f7"/><path d="M36 2v12a2 2 0 0 0 2 2h12" fill="#d7dbe2"/><text x="27" y="44" font-family="-apple-system,Helvetica,Arial" font-size="15" font-weight="800" text-anchor="middle" fill="#6b4fbb">MD</text></svg>`,
  py: `<svg viewBox="0 0 56 64"><path d="M6 2h30l14 14v44a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2z" fill="#f4f5f7"/><path d="M36 2v12a2 2 0 0 0 2 2h12" fill="#d7dbe2"/><rect x="13" y="26" width="15" height="15" rx="4" fill="#3b77a8"/><rect x="26" y="34" width="15" height="15" rx="4" fill="#f2c94c"/></svg>`,
  txt: `<svg viewBox="0 0 56 64"><path d="M6 2h30l14 14v44a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2z" fill="#f4f5f7"/><path d="M36 2v12a2 2 0 0 0 2 2h12" fill="#d7dbe2"/><g stroke="#9aa3b2" stroke-width="2.4" stroke-linecap="round"><path d="M13 30h28M13 38h28M13 46h14"/></g></svg>`,
  video: `<svg viewBox="0 0 56 64"><rect x="2" y="10" width="52" height="44" rx="6" fill="#26313f"/><g fill="#fff" fill-opacity=".85"><rect x="6" y="14" width="5" height="4" rx="1"/><rect x="6" y="46" width="5" height="4" rx="1"/><rect x="45" y="14" width="5" height="4" rx="1"/><rect x="45" y="46" width="5" height="4" rx="1"/></g><path d="M23 24v16l13-8z" fill="#fff"/></svg>`,
  audio: `<svg viewBox="0 0 56 64"><rect x="4" y="8" width="48" height="48" rx="11" fill="url(#ga)"/><defs><linearGradient id="ga" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#fb7185"/><stop offset="1" stop-color="#e11d48"/></linearGradient></defs><path d="M33 19v19.5a5 5 0 1 1-3-4.6V23l-9 2.3V41a5 5 0 1 1-3-4.6V22.5z" fill="#fff"/></svg>`,
  folder: `<svg viewBox="0 0 56 64"><path d="M3 16a4 4 0 0 1 4-4h14l5 5h23a4 4 0 0 1 4 4v29a4 4 0 0 1-4 4H7a4 4 0 0 1-4-4z" fill="#5aa9f8"/><path d="M3 24a4 4 0 0 1 4-4h42a4 4 0 0 1 4 4v26a4 4 0 0 1-4 4H7a4 4 0 0 1-4-4z" fill="#79bdfb"/></svg>`,
  calc: `<svg viewBox="0 0 56 64"><rect x="6" y="6" width="44" height="52" rx="10" fill="#3a3d44"/><rect x="12" y="12" width="32" height="12" rx="3" fill="#cfd3da"/><g fill="#8b9099">${[0, 1, 2].map((r) => [0, 1, 2].map((c) => `<circle cx="${17 + c * 11}" cy="${33 + r * 9}" r="3.4"/>`).join("")).join("")}</g><circle cx="39" cy="51" r="3.4" fill="#f59e0b"/></svg>`,
  music: `<svg viewBox="0 0 56 64"><rect x="6" y="9" width="44" height="44" rx="11" fill="#f43f5e"/><path d="M34 19v18.5a4.6 4.6 0 1 1-2.7-4.2V25.5l-9.6 2.4v13a4.6 4.6 0 1 1-2.7-4.2V23.2z" fill="#fff"/></svg>`,
  gear: `<svg viewBox="0 0 56 64"><rect x="6" y="9" width="44" height="44" rx="11" fill="#2563eb"/><g transform="translate(28 31)" fill="#fff"><circle r="9"/>${[0, 45, 90, 135, 180, 225, 270, 315].map((r) => `<rect x="-2.6" y="-14.5" width="5.2" height="7" rx="1.4" transform="rotate(${r})"/>`).join("")}</g><circle cx="28" cy="31" r="4" fill="#2563eb"/></svg>`,
  globe: `<svg viewBox="0 0 56 64"><circle cx="28" cy="32" r="21" fill="#0ea5e9"/><g fill="none" stroke="#fff" stroke-opacity=".9" stroke-width="2"><ellipse cx="28" cy="32" rx="9" ry="21"/><path d="M7 32h42M10 22h36M10 42h36"/></g></svg>`,
  play: `<svg viewBox="0 0 16 16"><path d="M4 2.5v11l9-5.5z" fill="#fff"/></svg>`,
  pause: `<svg viewBox="0 0 16 16" fill="#fff"><rect x="3.5" y="2.5" width="3.2" height="11" rx="1"/><rect x="9.3" y="2.5" width="3.2" height="11" rx="1"/></svg>`,
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
  ["music", false, `<svg viewBox="0 0 58 58"><defs><linearGradient id="dm" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#fb7185"/><stop offset="1" stop-color="#e11d48"/></linearGradient></defs><rect width="58" height="58" fill="url(#dm)"/><path transform="translate(4.2 -1)" d="M39 13v23.5a6.2 6.2 0 1 1-3.6-5.6V21.2l-12.8 3.2V40a6.2 6.2 0 1 1-3.6-5.6V18.7z" fill="#fff"/></svg>`],
  ["code", true, `<svg viewBox="0 0 58 58"><defs><linearGradient id="dcode" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#1e3a8a"/><stop offset="1" stop-color="#0ea5e9"/></linearGradient></defs><rect width="58" height="58" fill="url(#dcode)"/><path d="M21 19l-10 10 10 10M37 19l10 10-10 10" fill="none" stroke="#fff" stroke-width="3.6" stroke-linecap="round" stroke-linejoin="round"/><path d="M32 16l-6 26" stroke="#bae6fd" stroke-width="3" stroke-linecap="round"/></svg>`],
  ["terminal", false, `<svg viewBox="0 0 58 58"><rect width="58" height="58" fill="#1f2328"/><rect x="5" y="5" width="48" height="48" rx="9" fill="#0d1014" stroke="#3a4048"/><path d="M15 22l8 6-8 6" stroke="#e6edf3" stroke-width="3" fill="none" stroke-linecap="round" stroke-linejoin="round"/><path d="M27 36h15" stroke="#e6edf3" stroke-width="3" stroke-linecap="round"/></svg>`],
  ["settings", false, `<svg viewBox="0 0 58 58"><defs><linearGradient id="dset" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#d1d5db"/><stop offset="1" stop-color="#6b7280"/></linearGradient></defs><rect width="58" height="58" fill="url(#dset)"/><g fill="#374151"><circle cx="29" cy="29" r="12"/><g id="teeth">${[0, 45, 90, 135, 180, 225, 270, 315].map((r) => `<rect x="26" y="9" width="6" height="9" rx="1.5" transform="rotate(${r} 29 29)"/>`).join("")}</g></g><circle cx="29" cy="29" r="5.5" fill="#d1d5db"/></svg>`],
  "divider",
  ["lantern", true, `<svg viewBox="0 0 58 58"><rect width="58" height="58" fill="#1f2126"/><circle cx="25.7" cy="25.7" r="10.4" fill="none" stroke="#f3f4f6" stroke-width="3.5"/><path d="M33.6 33.6 41.1 41.1" stroke="#f3f4f6" stroke-width="4" stroke-linecap="round"/><path d="M25.7 20 C26.4 23.8 27.6 25 31.4 25.7 C27.6 26.4 26.4 27.6 25.7 31.4 C25 27.6 23.8 26.4 20 25.7 C23.8 25 25 23.8 25.7 20 Z" fill="#f2b84b"/></svg>`],
  ["trash", false, `<svg viewBox="0 0 58 58" class="bare"><path d="M16 17h26l-2.2 31a3 3 0 0 1-3 2.8H21.2a3 3 0 0 1-3-2.8z" fill="rgba(255,255,255,.28)" stroke="rgba(255,255,255,.75)" stroke-width="1.6"/><path d="M13 15h32" stroke="rgba(255,255,255,.85)" stroke-width="2.4" stroke-linecap="round"/><g stroke="rgba(255,255,255,.55)" stroke-width="1.4"><path d="M24 22l1 24M29 22v24M34 22l-1 24"/></g></svg>`],
];

/* ---------- illustrations for results (own drawings, no stock photos) ---------- */

const ART = {
  beachDog: `<svg viewBox="0 0 160 120" preserveAspectRatio="xMidYMid slice"><defs><linearGradient id="bs" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fdba74"/><stop offset=".55" stop-color="#fda4af"/><stop offset="1" stop-color="#93c5fd"/></linearGradient></defs><rect width="160" height="120" fill="url(#bs)"/><circle cx="118" cy="48" r="14" fill="#fff7ed" opacity=".9"/><rect y="62" width="160" height="22" fill="#60a5fa"/><path d="M0 70q20-5 40 0t40 0 40 0 40 0v6H0z" fill="#bfdbfe" opacity=".8"/><path d="M0 84h160v36H0z" fill="#fcd9a8"/><g fill="#5b3a29"><ellipse cx="62" cy="92" rx="17" ry="8"/><circle cx="80" cy="84" r="7"/><path d="M84 80l7-4-2 8z"/><rect x="49" y="96" width="4" height="13" rx="2"/><rect x="58" y="97" width="4" height="12" rx="2"/><rect x="67" y="97" width="4" height="12" rx="2"/><rect x="74" y="95" width="4" height="13" rx="2"/><path d="M45 89q-9-8-6-14" stroke="#5b3a29" stroke-width="4" fill="none" stroke-linecap="round"/></g><circle cx="82" cy="83" r="1.2" fill="#fff"/></svg>`,
  parkDog: `<svg viewBox="0 0 160 120" preserveAspectRatio="xMidYMid slice"><rect width="160" height="120" fill="#bae6fd"/><circle cx="30" cy="26" r="10" fill="#fef3c7"/><path d="M0 70q40-18 80-6t80-4v60H0z" fill="#4ade80"/><path d="M0 86q50-10 100 0t60 0v34H0z" fill="#22c55e"/><circle cx="128" cy="52" r="20" fill="#16a34a"/><rect x="125" y="62" width="6" height="22" fill="#78350f"/><g fill="#e5e7eb"><ellipse cx="70" cy="90" rx="15" ry="7"/><circle cx="85" cy="82" r="6.5"/><path d="M88 76l5-5 1 8z"/><rect x="59" y="94" width="4" height="11" rx="2"/><rect x="67" y="95" width="4" height="10" rx="2"/><rect x="75" y="94" width="4" height="11" rx="2"/></g><circle cx="98" cy="96" r="4" fill="#ef4444"/></svg>`,
  sunset: `<svg viewBox="0 0 160 120" preserveAspectRatio="xMidYMid slice"><defs><linearGradient id="ss" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#7c3aed"/><stop offset=".5" stop-color="#f97316"/><stop offset="1" stop-color="#fde047"/></linearGradient></defs><rect width="160" height="120" fill="url(#ss)"/><circle cx="80" cy="74" r="20" fill="#fff7ed"/><rect y="74" width="160" height="46" fill="#1e3a8a"/><g stroke="#fde68a" stroke-width="2" opacity=".7"><path d="M60 82h40M66 90h28M72 98h16"/></g></svg>`,
  vlog: `<svg viewBox="0 0 320 200" preserveAspectRatio="xMidYMid slice"><rect width="320" height="200" fill="#14532d"/><rect y="0" width="320" height="70" fill="#1e293b"/><g fill="#fde68a" opacity=".9"><circle cx="40" cy="20" r="5"/><circle cx="280" cy="20" r="5"/></g><g fill="#334155"><rect x="0" y="40" width="320" height="30"/></g><g fill="#64748b"><circle cx="20" cy="55" r="4"/><circle cx="40" cy="52" r="4"/><circle cx="60" cy="56" r="4"/><circle cx="80" cy="53" r="4"/><circle cx="100" cy="55" r="4"/><circle cx="120" cy="52" r="4"/><circle cx="140" cy="56" r="4"/><circle cx="160" cy="53" r="4"/><circle cx="180" cy="55" r="4"/><circle cx="200" cy="52" r="4"/><circle cx="220" cy="56" r="4"/><circle cx="240" cy="53" r="4"/><circle cx="260" cy="55" r="4"/><circle cx="280" cy="52" r="4"/><circle cx="300" cy="56" r="4"/></g><g stroke="#e2e8f0" stroke-width="2" fill="none" opacity=".75"><rect x="20" y="82" width="280" height="110"/><path d="M160 82v110"/><circle cx="160" cy="137" r="20"/></g><g fill="#ef4444"><circle cx="120" cy="120" r="4"/><circle cx="190" cy="150" r="4"/></g><g fill="#3b82f6"><circle cx="210" cy="118" r="4"/><circle cx="140" cy="160" r="4"/></g><circle cx="168" cy="134" r="3" fill="#fff"/></svg>`,
  screenshot: `<svg viewBox="0 0 320 200" preserveAspectRatio="xMidYMid slice"><rect width="320" height="200" fill="#1d4ed8"/><rect x="34" y="26" width="252" height="148" rx="10" fill="#f8fafc"/><rect x="34" y="26" width="252" height="24" rx="10" fill="#e2e8f0"/><rect x="34" y="40" width="252" height="10" fill="#e2e8f0"/><g fill="#94a3b8"><circle cx="48" cy="38" r="4"/><circle cx="61" cy="38" r="4"/><circle cx="74" cy="38" r="4"/></g><circle cx="66" cy="86" r="14" fill="#ef4444"/><path d="M60 80l12 12M72 80 60 92" stroke="#fff" stroke-width="3.2" stroke-linecap="round"/><g font-family="-apple-system,Helvetica,Arial" fill="#0f172a"><text x="92" y="82" font-size="14" font-weight="700">Windows Update</text><text x="92" y="102" font-size="12">Error code: 0x80070002</text><text x="50" y="128" font-size="11" fill="#475569">The system cannot find the file specified.</text></g><rect x="206" y="146" width="66" height="18" rx="5" fill="#2563eb"/><text x="239" y="159" font-family="-apple-system,Helvetica,Arial" font-size="10" fill="#fff" text-anchor="middle">Try again</text></svg>`,
  cover: `<svg viewBox="0 0 160 160" preserveAspectRatio="xMidYMid slice"><defs><linearGradient id="cv" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#1e1b4b"/><stop offset="1" stop-color="#be185d"/></linearGradient></defs><rect width="160" height="160" fill="url(#cv)"/><circle cx="110" cy="50" r="18" fill="#fde68a" opacity=".85"/><path d="M0 120q40-30 80-10t80-20v70H0z" fill="#0f172a" opacity=".7"/><g fill="#fff" opacity=".8"><circle cx="30" cy="30" r="1.2"/><circle cx="60" cy="20" r="1"/><circle cx="140" cy="100" r="1"/><circle cx="20" cy="80" r="1"/></g></svg>`,
};

/* ---------- scenarios ---------- */

// Rows: cat = filter icon (apps, settings, code, docs, pdf, photos, media), action = ↩ without preview, preview = plain text (matched words are bolded at render
// time, like the app). complete = characters typed before the grey completion appears.
const SCENARIOS = [
  {
    id: "photo", label: "a photo", phrase: "dog on the beach", complete: 5,
    typeHint: "No file is called that — Lantern looks at what is in your photos.",
    blurb: "Photos come first because that’s what you meant — Lantern looks at what is <b>in</b> the picture, not its file name.",
    sections: [
      ["Photos", [
        { thumb: ART.beachDog, title: "IMG_2041.heic", sub: "~/Pictures/Seaside 2025", source: "image", cat: "photos",
          show: { kind: "image", art: ART.beachDog } },
        { thumb: ART.parkDog, title: "rex_park.jpg", sub: "~/Pictures/Rex", source: "image", cat: "photos",
          show: { kind: "image", art: ART.parkDog } },
        { thumb: ART.sunset, title: "IMG_2077.heic", sub: "~/Pictures/Seaside 2025", source: "image", cat: "photos",
          show: { kind: "image", art: ART.sunset } },
      ]],
      ["Files", [
        { icon: ICON.md, title: "trip-notes.md · Day 3", sub: "~/Documents — lines 12–20", cat: "docs", source: "meaning · words",
          preview: "…took Rex to the beach at sunrise, the dog chased the waves for an hour…" },
      ]],
    ],
  },
  {
    id: "screenshot", label: "text in a screenshot", phrase: "error 0x80070002", complete: 8,
    typeHint: "Lantern reads the text in your screenshots and scans — on your Mac.",
    blurb: "macOS reads the text in images on your Mac, so a screenshot is found by <b>what it says</b> — the matching text is shown under it.",
    sections: [
      ["Photos", [
        { thumb: ART.screenshot, title: "Screenshot 2025-10-14 at 09.12.33.png", sub: "~/Desktop", cat: "photos",
          source: "text in image", preview: "Windows Update · Error code: 0x80070002 · The system cannot find the file specified.",
          show: { kind: "image", art: ART.screenshot } },
      ]],
      ["Files", [
        { icon: ICON.md, title: "pc-fixes.md · Windows", sub: "~/Documents — lines 4–9", cat: "docs", source: "words · meaning",
          preview: "Update error 0x80070002: stop the update service, clear SoftwareDistribution, try again." },
      ]],
    ],
  },
  {
    id: "video", label: "a moment in a video", phrase: "when we talked about the match", complete: 9,
    typeHint: "Describe it in your own words — Lantern understands what was said in your videos.",
    blurb: "Lantern transcribes recordings on your Mac, so it finds <b>the moment</b> something was said — here at 1:48.",
    sections: [
      ["Files", [
        { thumb: ART.vlog, badge: "1:48", title: "vlog_weekend.mp4 · 1:48", sub: "~/Movies — 1:48–2:13", cat: "media", media: true,
          source: "meaning · words", preview: "“…and then the match, two–one in the very last minute, the whole stadium went silent…”",
          show: { kind: "video", art: ART.vlog, start: 105, length: 412,
            transcript: "“…and then the match, two–one in the very last minute, the whole stadium went silent and then just exploded…”" } },
        { icon: ICON.txt, title: "football.txt", sub: "~/Documents — lines 1–8", cat: "docs", source: "meaning",
          preview: "Saturday: Lech vs Legia, tickets in the drawer…" },
      ]],
    ],
  },
  {
    id: "code", label: "code at its line", phrase: "def add_two(x, y):", complete: 6,
    typeHint: "Paste a line of code — Lantern finds where it is defined.",
    blurb: "Paste a line of code and Lantern jumps to the <b>exact line</b> where it is defined — in your own editor.",
    sections: [
      ["Definitions", [
        { icon: ICON.py, title: "add_two · math_utils.py", sub: "~/Projects/calc — line 200", preview: "def add_two(x, y)", code: true,
          source: "definition", cat: "code",
          show: { kind: "code", title: "math_utils.py — line 200", from: 194, hit: [200, 201], lines: [
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
          ] } },
      ]],
      ["Files", [
        { icon: ICON.py, title: "calculator.py · total", sub: "~/Projects/calc — lines 41–48", preview: "result = add_two(subtotal, tax)",
          code: true, source: "meaning · words", cat: "code" },
        { icon: ICON.md, title: "README.md · Helpers", sub: "~/Projects/calc — lines 18–24", cat: "docs", source: "words",
          preview: "add_two(x, y) returns the sum; see math_utils.py for the other helpers." },
      ]],
    ],
  },
  {
    id: "pdf", label: "a page in a PDF", phrase: "electricity invoice for october", complete: 7,
    typeHint: "Lantern reads PDFs page by page — even scanned ones.",
    blurb: "PDFs are searched page by page, so the result is <b>the page</b> — ⌘Y shows it with your words highlighted.",
    sections: [
      ["Files", [
        { icon: ICON.pdf, title: "invoices_2025.pdf · page 10", sub: "~/Documents/Bills — page 10", cat: "pdf", source: "meaning · words",
          preview: "Invoice 2025/10/1187 — electricity, October 2025. Total due: 245.30 PLN by 14 November.",
          show: { kind: "pdf", page: 10, lines: [
            "Invoice 2025/10/1187", "", "Period: 1–31 October 2025", "Electricity, tariff G11 — 412 kWh",
            "Distribution fee", "", "Total due: 245.30 PLN", "Payment by 14 November 2025",
          ] } },
        { icon: ICON.md, title: "budget.md · Bills", sub: "~/Documents — lines 30–36", cat: "docs", source: "meaning",
          preview: "October: rent paid, electricity invoice still open, internet 59 PLN." },
      ]],
    ],
  },
  {
    id: "app", label: "an app", phrase: "calcu", complete: 3,
    typeHint: "Apps and System Settings by name — even misspelt or in another language.",
    blurb: "Like Spotlight, Lantern finds apps by any of their names, and one letter brings the app you use most. ↩ opens it.",
    sections: [
      ["Applications", [
        { icon: ICON.calc, title: "Calculator", sub: "Application", cat: "apps", source: "", action: "open",
          enterHint: "In Lantern, ↩ opens <b>Calculator</b>." },
      ]],
      ["Files", [
        { icon: ICON.py, title: "billing.py · calculate_total", sub: "~/Projects/shop — lines 12–30", code: true, cat: "code",
          source: "words", preview: "def calculate_total(items, tax=0.23):" },
      ]],
    ],
  },
  {
    id: "setting", label: "a setting", phrase: "24 hour", complete: 99,
    typeHint: "Every pane and setting of System Settings, by its words.",
    blurb: "Hundreds of settings inside System Settings, found by their words — ↩ opens the right pane.",
    sections: [
      ["System Settings", [
        { icon: ICON.gear, title: "Use 24-hour time in the menu bar", sub: "System Settings › Date & Time", cat: "settings",
          source: "", action: "open", enterHint: "In Lantern, ↩ opens <b>Date &amp; Time</b> in System Settings." },
        { icon: ICON.gear, title: "Date & Time", sub: "System Settings", cat: "settings", source: "", action: "open",
          enterHint: "In Lantern, ↩ opens <b>Date &amp; Time</b> in System Settings." },
      ]],
    ],
  },
  {
    id: "music", label: "a song", phrase: "fog over the pier", complete: 4,
    typeHint: "Your Apple Music songs and playlists, by title, artist or album.",
    blurb: "Songs and playlists of your library come up by name or artist (“low tide club” works too); ↩ plays.",
    sections: [
      ["Music", [
        { icon: ICON.music, title: "Fog over the Pier", sub: "Low Tide Club · Salt & Static  ·  from playlist “Evening walk”", cat: "music",
          source: "", action: "play", enterHint: "In Lantern, ↩ plays <b>Fog over the Pier</b> in Music; ⌘↩ shows it there." },
        { icon: ICON.music, title: "Evening walk", sub: "Playlist · 34 songs", cat: "music", source: "", action: "play",
          enterHint: "In Lantern, ↩ plays the playlist <b>Evening walk</b>." },
      ]],
    ],
  },
  {
    id: "answer", label: "a quick answer", phrase: "2+2*20", complete: 99,
    typeHint: "Arithmetic, units and the time in other cities — computed on your Mac.",
    blurb: "Quick answers come first: arithmetic, units (“5 km to mi”) and world time (“time in tokyo”), all offline.",
    sections: [
      ["Answer", [
        { icon: ICON.calc, title: "42", sub: "2+2×20", cat: null, source: "", action: "copy",
          enterHint: "In Lantern, ↩ copies <b>42</b>." },
      ]],
    ],
  },
];

// Filter icons in the app's order (⌘1… never changes) with its labels; own line drawings (no Apple symbols).
const GLYPH = (d) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`;
const CATEGORIES = [
  ["apps", "Applications", GLYPH('<rect x="4" y="4" width="7" height="7" rx="2"/><rect x="13" y="4" width="7" height="7" rx="2"/><rect x="4" y="13" width="7" height="7" rx="2"/><rect x="13" y="13" width="7" height="7" rx="2"/>')],
  ["settings", "System Settings", GLYPH('<circle cx="12" cy="12" r="3.2"/><path d="M12 3v2.5M12 18.5V21M3 12h2.5M18.5 12H21M5.6 5.6l1.8 1.8M16.6 16.6l1.8 1.8M5.6 18.4l1.8-1.8M16.6 7.4l1.8-1.8"/>')],
  ["shortcuts", "Shortcuts", GLYPH('<path d="m12 4 8 4-8 4-8-4z"/><path d="m4 12 8 4 8-4M4 16l8 4 8-4"/>')],
  ["clipboard", "Clipboard", GLYPH('<rect x="6" y="5" width="12" height="16" rx="2"/><path d="M9 5V3.5h6V5M9 11h6M9 15h4"/>')],
  ["code", "Code", GLYPH('<path d="M8.5 7 4 12l4.5 5M15.5 7 20 12l-4.5 5M13.5 5l-3 14"/>')],
  ["docs", "Documents", GLYPH('<path d="M7 3h7l4 4v14H7z"/><path d="M14 3v4h4M10 12h5M10 15.5h5"/>')],
  ["pdf", "PDFs", GLYPH('<path d="M7 3h7l4 4v14H7z"/><path d="M14 3v4h4"/><rect x="5" y="12" width="10" height="5" rx="1"/>')],
  ["photos", "Photos", GLYPH('<rect x="3.5" y="5" width="17" height="14" rx="2.5"/><circle cx="9" cy="10" r="1.6"/><path d="m4 17 5-4.5 4 3.5 2.5-2 4.5 3.5"/>')],
  ["media", "Recordings and videos", GLYPH('<rect x="3.5" y="5" width="17" height="14" rx="2.5"/><path d="m10.5 9.2 4.3 2.8-4.3 2.8z" fill="currentColor"/>')],
  ["music", "Music", GLYPH('<path d="M9 18V6l10-2v12"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="16.5" cy="16" r="2.5"/>')],
];
// The same kinds and keys as the app (⌘1…⌘9, then ⌘0 for the tenth).
const CATEGORY = Object.fromEntries(CATEGORIES.map(([id, label, glyph], k) => [id, { label, glyph, key: k === 9 ? 0 : k + 1 }]));
const FOLDED = GLYPH('<path d="M5 7h14M8 12h8M10.5 17h3"/>');  // the filter button (line.3.horizontal.decrease)
const MAX_UNFOLDED = 3;

/* ---------- matched words (a port of the app's QueryHighlight + TextFold) ---------- */

// Letters without a Unicode decomposition: diacritic stripping leaves them, so they are spelled out (as in the app).
const FOLD = { "ł": "l", "ø": "o", "đ": "d", "ð": "d", "ħ": "h", "ŧ": "t", "ŋ": "n", "ı": "i", "ĸ": "k", "ß": "ss", "æ": "ae", "œ": "oe", "þ": "th" };
const SKIP = new Set("the and for with about when that this from into what where was were jak dla czy nie sie oraz przez przy jest gdzie kiedy albo lub".split(" "));
const WORD = /[\p{L}\p{N}]/u;
const foldChar = (c) => [...c.normalize("NFKC").toLowerCase()].map((x) => FOLD[x] ?? x).join("").normalize("NFKD").replace(/\p{M}/gu, "");

function stems(query) {
  const out = new Set();
  for (const part of query.split(/[^\p{L}\p{N}]+/u)) {
    const word = [...part].map(foldChar).join("");
    const number = /^\p{N}+$/u.test(word);
    if (word.length < (number ? 2 : 3) || SKIP.has(word)) continue;
    out.add(word.length >= 7 ? word.slice(0, -2) : word);
  }
  return [...out];
}

/** HTML of text with the whole words that start with one of the query's stems in bold. */
function highlight(text, query) {
  const chars = [...text];
  let folded = "";
  const origin = [];  // folded index -> index in chars
  chars.forEach((c, k) => { for (const f of foldChar(c)) { folded += f; origin.push(k); } });
  const marked = new Array(chars.length).fill(false);
  for (const stem of stems(query)) {
    for (let at = folded.indexOf(stem); at !== -1; at = folded.indexOf(stem, at + stem.length)) {
      let k = origin[at];
      if (k > 0 && WORD.test(chars[k - 1])) continue;  // word starts only
      for (; k < chars.length && WORD.test(chars[k]); k++) marked[k] = true;
    }
  }
  let html = "", open = false;
  chars.forEach((c, k) => {
    if (marked[k] !== open) { html += marked[k] ? '<b class="match">' : "</b>"; open = marked[k]; }
    html += escapeHtml(c);
  });
  return open ? `${html}</b>` : html;
}

/* ---------- state ---------- */

const state = {
  step: "intro",
  scenario: null,
  typed: "",
  category: null,  // the chosen filter icon (null = everything)
  selection: 0,  // index into the visible rows
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
  // the trademark note sits under the desktop, never over it: the desktop fits the space above the note
  const stage = $(".stage");
  stage.style.bottom = `${$(".legal").offsetHeight}px`;
  const s = Math.min((innerWidth * 0.96) / 1440, (stage.clientHeight * 0.96) / 900, 1.25);
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

/* The status dot of the app's menu: Apple's colours (green ready, yellow busy or waiting for you, grey off); its word
 * is the tooltip, as in the app. */
function setChip(text, color = "") {
  const dot = $("#menuChip");
  const colour = { green: "ready", blue: "busy", purple: "busy" }[color] || "off";
  dot.className = `status-dot ${colour}`;
  dot.title = text;
  dot.setAttribute("aria-label", text);
}

function menuBody(html) { $("#menuBody").innerHTML = html; }

function menuActions() {  // as in the app: one row, like "Battery Settings…" in macOS's Battery menu
  return `<div class="menu-actions"><button class="menu-action" type="button">Lantern Settings…</button></div>`;
}

/* ---------- steps ---------- */

const STEPS = {
  intro() {
    closeMenu();
    attention($("#lanternItem"));
    hint("Lantern lives in your menu bar. <b>Click its icon</b> to open it.", { x: 990, y: 44, arrow: "up-right" });
  },

  menu() {
    go("starting");  // Lantern starts with the Mac; the first time it looks for files and asks before reading them
  },

  starting() {
    openMenu();
    attention(null);
    setChip("Starting…");
    menuBody(`<div class="menu-card"><p>Starting…</p><p class="muted">Loading the model (it never leaves this Mac).</p>
      <div class="progress"><i style="width:35%;transition:width .9s"></i></div></div>`);
    hint("Lantern runs quietly in the background, <b>only on your Mac</b>.", { x: 640, y: 108, arrow: "none" });
    later(1400, () => go("found"));
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
    menuBody(`<p class="stats-line">1,248 files · 9,410 chunks · 412 MB</p>${menuActions()}`);
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
    const filters = categoriesOf(sc).length > 1
      ? "<br><br>The icons beside the field narrow it to one kind — click one or press <b>Tab</b>." : "";
    const action = visibleRows()[0]?.action;
    const next = action ? `Press <b>↩</b> to ${action} it.` : "Press <b>⌘Y</b> to preview it, right here.";
    hint(`${sc.blurb}${filters}<br><br>${next}`, {
      x: 40, y: 196, arrow: "none",
      actions: [{ label: action ? `${action[0].toUpperCase()}${action.slice(1)} (↩)` : "Preview (⌘Y)", onClick: () => go("preview") }],
    });
  },

  preview() {
    const sc = state.scenario;
    const row = visibleRows()[state.selection];
    state.done.add(sc.id);
    const left = SCENARIOS.filter((s) => !state.done.has(s.id));
    const actions = nextSearchActions();
    if (row.action) {  // apps, settings, answers, the web row: nothing to preview, ↩ acts at once (as in the app)
      state.step = "results";
      hint(`${row.enterHint}<br><br>${left.length ? "Try another search:" : "That’s all of them."}`, { x: 40, y: 196, actions });
      return;
    }
    renderPreview(row);
    hint(left.length
      ? "In Lantern, <b>↩</b> opens it at this exact spot — the line in your editor, the moment in QuickTime.<br><br>Try another search:"
      : "That’s all of them. <b>↩</b> would open each one at the exact spot.", { x: 40, y: 196, actions });
  },

  finale() {
    clearTimers();
    $("#panel").hidden = true;
    closeMenu();
    hint(`<span class="app-icon"></span><h2>That’s Lantern.</h2>
      <p>Search your files, code, photos and recordings by meaning — and apps, settings and quick answers, like Spotlight. <b>100% on your Mac</b>, no cloud, no
      telemetry. Open source under the Apache 2.0 license.</p>`, {
      x: 510, y: 250, cls: "finale",
      actions: [
        { label: "Get Lantern on GitHub", href: REPO, cls: "btn primary" },
        { label: "Start over", onClick: restart },
      ],
    });
  },
};

function nextSearchActions() {
  const left = SCENARIOS.filter((s) => !state.done.has(s.id));
  const actions = left.map((s) => ({ label: `Find ${s.label}`, onClick: () => pickScenario(s) }));
  actions.push({ label: left.length ? "Finish the tour" : "See what’s next", cls: "pill", onClick: () => go("finale") });
  return actions;
}

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
  n.classList.remove("leaving");
  n.hidden = false;
  later(5200, () => {  // like macOS: the banner slides out past the right edge instead of vanishing
    n.classList.add("leaving");
    n.addEventListener("animationend", () => { n.hidden = true; n.classList.remove("leaving"); }, { once: true });
  });
}

/* ---------- search panel ---------- */

function pickScenario(sc) {
  state.scenario = sc;
  state.step = "type";
  state.typed = "";
  state.category = null;
  state.selection = 0;
  $("#panelContent").hidden = true;
  renderQuery();
  renderScope();
  $("#queryField").focus({ preventScroll: true });
  typeHintFor(sc);
}

function typeHintFor(sc) {
  hint(`Type <b>“${escapeHtml(sc.phrase)}”</b><br>${sc.typeHint}`, { x: 534, y: 252, arrow: "up" });
}

/** The grey completion, as in the app: it appears once enough is typed and completes from earlier searches and the
 * names in the index (here: the scenario's phrase). */
function completion() {
  const sc = state.scenario;
  return sc && state.step === "type" && state.typed.length >= sc.complete ? sc.phrase.slice(state.typed.length) : "";
}

function renderQuery() {
  state.scopeOpen = false;  // typing folds the filter icons again, as in the app
  const sc = state.scenario;
  const ghost = completion();
  $("#typed").textContent = state.typed;
  $("#ghost").textContent = ghost;
  // Before the first key, the field suggests what to type: the phrase, its letters rising in a slow wave.
  const placeholder = $("#placeholder");
  const suggest = sc && state.step === "type" && !state.typed;
  placeholder.hidden = state.typed.length > 0;
  placeholder.classList.toggle("suggest", Boolean(suggest));
  placeholder.innerHTML = suggest
    ? [...sc.phrase].map((c, k) => `<span style="--i:${k}">${escapeHtml(c)}</span>`).join("")
    : "Search Lantern — by meaning, in code, recordings…";
  $("#typeForMe").hidden = !sc || state.step !== "type";
  if (sc && state.step === "type") {
    if (ghost && !state.completionHinted) {
      state.completionHinted = true;
      hint("The grey text completes your query — from your earlier searches and the names in your index, never from "
        + "the internet. Press <b>→</b> to accept it.", { x: 534, y: 252, arrow: "up",
        actions: [{ label: "Accept (→)", onClick: acceptCompletion }] });
    } else if (!ghost && state.completionHinted) {
      state.completionHinted = false;
      typeHintFor(sc);
    }
  }
}

function acceptCompletion() {
  const sc = state.scenario;
  if (!sc || state.step !== "type" || !completion()) return;
  state.typed = sc.phrase;
  state.completionHinted = false;
  renderQuery();
  later(220, () => go("results"));
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
  if (state.typed === sc.phrase) state.completionHinted = false;
  renderQuery();
  if (state.typed === sc.phrase) later(220, () => go("results"));
}

/** ↩ while typing: search with the completion if it is showing, otherwise show what is still missing. */
function submitQuery() {
  const sc = state.scenario;
  if (completion()) return acceptCompletion();
  if (state.typed === sc.phrase) return go("results");
  const f = $("#queryField");
  f.classList.remove("shake");
  void f.offsetWidth;
  f.classList.add("shake");
  hint(`Keep typing <b>“${escapeHtml(sc.phrase)}”</b> — or press <b>Type it for me</b>.`, { x: 534, y: 252, arrow: "up" });
}

/** Types up to the completion, pauses so it can be seen, then accepts it with →. */
function typeForMe() {
  const sc = state.scenario;
  if (!sc || state.step !== "type") return;
  const next = () => {
    if (state.step !== "type" || state.typed === sc.phrase) return;
    if (completion()) { later(900, acceptCompletion); return; }
    typeChar(sc.phrase[state.typed.length]);
    later(60 + Math.random() * 50, next);
  };
  next();
}

function categoriesOf(sc) {
  const present = new Set(sc.sections.flatMap(([, rows]) => rows.map((r) => r.cat)));
  return CATEGORIES.filter(([id]) => present.has(id));
}

function visibleSections() {
  const sc = state.scenario;
  const web = ["Web", [{ icon: ICON.globe, title: `Search the web for “${sc.phrase}”`, sub: "Google", cat: null, source: "",
    action: "open", enterHint: "In Lantern this opens your default browser with the search — Lantern itself sends nothing." }]];
  return [...sc.sections, web]
    .map(([title, rows]) => [title, rows.filter((r) => !state.category || r.cat === state.category)])
    .filter(([, rows]) => rows.length);
}
const visibleRows = () => visibleSections().flatMap(([, rows]) => rows);

/** The filter icons beside the field, as in the app: every kind before results, then only the kinds with matches;
 * the chosen one sits in the field as a chip with its ⌘ number. Hidden while previewing. */
function shownCategories() {
  const sc = state.scenario;
  const results = sc && (state.step === "results" || state.step === "preview");
  return results ? categoriesOf(sc) : CATEGORIES;
}

let foldTimer = null;
function renderScope() {
  const icons = $("#scopeIcons");
  const chip = $("#scopeChip");
  icons.hidden = state.step === "preview";
  const list = shownCategories().filter(([id]) => id !== state.category);
  // As in the app: more than three kinds fold into one filter button; pointing at it unfolds them, one after another.
  if (list.length > MAX_UNFOLDED && !state.scopeOpen) {
    icons.innerHTML = `<button type="button" class="scope-icon folded" title="Filters  ⌘1–⌘0" aria-label="Filters">${FOLDED}</button>`;
    const open = () => { state.scopeOpen = true; renderScope(); };
    icons.firstElementChild.addEventListener("mouseenter", () => { clearTimeout(foldTimer); foldTimer = setTimeout(open, 120); });
    icons.firstElementChild.addEventListener("mouseleave", () => clearTimeout(foldTimer));
    icons.firstElementChild.addEventListener("click", open);
  } else {
    icons.innerHTML = list.map(([id, label, glyph], k) =>
      `<button type="button" class="scope-icon${state.scopeOpen ? " unfolding" : ""}" style="--i:${list.length - 1 - k}" data-cat="${id}" title="${label}  ⌘${CATEGORY[id].key}">${glyph}<span class="label">${label}</span></button>`).join("");
    icons.onmouseenter = () => clearTimeout(foldTimer);
    icons.onmouseleave = () => {
      if (!state.scopeOpen) return;
      clearTimeout(foldTimer);
      foldTimer = setTimeout(() => { state.scopeOpen = false; renderScope(); }, 1200);
    };
  }
  icons.querySelectorAll(".scope-icon[data-cat]").forEach((b) => b.addEventListener("click", () => { state.scopeOpen = false; setCategory(b.dataset.cat); }));
  const c = state.category && CATEGORY[state.category];
  chip.hidden = !c;
  chip.innerHTML = c ? `${c.glyph}<span>${c.label}</span><span class="key">⌘${c.key}</span>` : "";
}

function setCategory(cat) {
  if (state.step === "preview") state.step = "results";
  state.category = cat === state.category ? null : cat;
  state.selection = 0;
  if (state.step === "results") renderResults(state.scenario, false);
  else renderScope();  // chosen before typing, like a Spotlight mode
}

function cyclePill(step) {
  const ids = [null, ...shownCategories().map(([id]) => id)];
  if (ids.length < 3) return;
  const next = ids[(ids.indexOf(state.category) + step + ids.length) % ids.length];
  state.category = null;
  setCategory(next);
}

function renderResults(sc, animate = true) {
  const content = $("#panelContent");
  content.hidden = false;
  $("#typeForMe").hidden = true;
  renderScope();
  let i = 0;
  const sections = visibleSections();
  content.innerHTML = sections.map(([title, rows]) => `
    ${sections.length > 1 ? `<div class="section-title">${title}</div>` : ""}
    ${rows.map((r) => {
      const media = r.thumb
        ? `<span class="thumb">${r.thumb}${r.badge ? `<span class="badge">${r.badge}</span>` : ""}</span>`
        : `<span class="icon">${r.icon}</span>`;
      const index = i++;
      const selected = index === state.selection;
      const verb = r.action || (r.media ? "play" : "open");
      const right = selected
        ? `<span class="row-hint">${r.action ? "" : "preview <kbd>⌘Y</kbd> "}${verb} <kbd>↩</kbd></span>`
        : `<span class="source">${r.source}</span>`;
      return `<div class="result${selected ? " selected" : ""}${animate ? "" : " still"}" data-index="${index}" style="animation-delay:${index * 60}ms">${media}
        <div class="body"><div class="title"><span>${escapeHtml(r.title)}</span>${right}</div>
        <div class="sub">${r.sub}</div>${r.preview ? `<div class="preview${r.code ? " code" : ""}">${highlight(r.preview, sc.phrase)}</div>` : ""}</div></div>`;
    }).join("")}`).join("");
  content.querySelectorAll(".result").forEach((row) => {
    row.addEventListener("click", () => select(Number(row.dataset.index)));
    row.addEventListener("dblclick", () => { select(Number(row.dataset.index)); go("preview"); });
  });
}

function select(index) {
  const count = visibleRows().length;
  state.selection = Math.max(0, Math.min(count - 1, index));
  if (state.step === "preview") state.step = "results";
  renderResults(state.scenario, false);
}

function renderPreview(row) {
  const sc = state.scenario;
  const p = row.show || { kind: "text" };
  const content = $("#panelContent");
  content.hidden = false;
  $("#scopeIcons").hidden = true;  // the app hides the filter icons while previewing
  let body = "";
  if (p.kind === "image") {
    body = `<div class="preview-media">${p.art}</div>${row.preview ? `<p class="transcript">${highlight(row.preview, sc.phrase)}</p>` : ""}`;
  } else if (p.kind === "video" || p.kind === "audio") {
    // Same as the app's preview: the system player starts 3 s before the words, the transcript below, and a button
    // for QuickTime Player. Audio has no picture, so the player is just its control bar (as in the app).
    const screen = p.kind === "video" ? `<div class="preview-media">${p.art}${playerBar(p)}</div>`
      : `<div class="preview-media audio">${playerBar(p)}</div>`;
    body = `${screen}<p class="transcript">${highlight(p.transcript, sc.phrase)}</p>
      <div class="preview-actions"><button class="btn" type="button" id="openQT">Open in QuickTime Player</button></div>`;
  } else if (p.kind === "code") {
    body = `<div class="code-preview">${p.lines.map((l, k) => {
      const n = p.from + k;
      return `<div class="${n >= p.hit[0] && n <= p.hit[1] ? "hit" : ""}"><span class="ln">${n}</span><span>${l[0] || "&nbsp;"}</span></div>`;
    }).join("")}</div>`;
  } else if (p.kind === "pdf") {
    // The app shows the matching page (PDFKit) zoomed in, with the query's words highlighted.
    body = `<div class="pdf-page">${p.lines.map((l) => `<p>${l ? highlight(l, sc.phrase).replaceAll('class="match"', 'class="pdf-hit"') : "&nbsp;"}</p>`).join("")}
      <span class="pdf-folio">${p.page}</span></div>`;
  } else {
    body = `<div class="text-preview">${highlight(row.preview || "", sc.phrase)}</div>`;
  }
  const title = p.title || row.title;
  content.innerHTML = `<div class="preview-pane"><h3>${escapeHtml(title)}</h3>${body}</div>`;
  if (p.kind === "video" || p.kind === "audio") {
    playFrom(p);
    $("#openQT").addEventListener("click", () => hint(
      `In Lantern this opens QuickTime Player at <b>${clock(p.start)}</b>, playing. Here, keep exploring:`,
      { x: 40, y: 196, actions: nextSearchActions() }));
  }
}

function playerBar(p) {
  return `<div class="player-bar glass"><button class="play" id="playBtn" type="button" aria-label="Pause">${ICON.pause}</button>
    <span id="tNow">${clock(p.start)}</span>
    <span class="track"><i id="tBar" style="width:${(p.start / p.length) * 100}%"></i></span>
    <span>${clock(p.length)}</span></div>`;
}

/** Plays from p.start (autoplay, like the app); the button pauses and resumes. */
function playFrom(p) {
  let t = p.start, last = performance.now(), playing = true;
  const btn = $("#playBtn");
  btn.addEventListener("click", () => {
    playing = !playing;
    btn.innerHTML = playing ? ICON.pause : ICON.play;
    btn.setAttribute("aria-label", playing ? "Pause" : "Play");
  });
  const tick = (now) => {
    if (state.step !== "preview" || !$("#tBar")) return;
    if (playing) t = Math.min(p.length, t + (now - last) / 1000);
    last = now;
    $("#tBar").style.width = `${(t / p.length) * 100}%`;
    $("#tNow").textContent = clock(t);
    requestAnimationFrame(tick);
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
  if (e.key === "Escape") {  // like the app: the preview closes first, then the filter goes
    e.preventDefault();
    if (state.step === "preview") go("results");
    else if (state.category) setCategory(state.category);
    return;
  }
  if (e.key === "Enter") {
    e.preventDefault();
    if (state.step === "results") go("preview");
    else if (state.step === "type") submitQuery();
    return;
  }
  if (state.step === "results" || state.step === "preview") {
    if (e.key === "Tab") { e.preventDefault(); cyclePill(e.shiftKey ? -1 : 1); return; }
    if ((e.metaKey || e.ctrlKey) && /^[0-9]$/.test(e.key)) {  // ⌘N: always the same kind, again = off (⌘0: the tenth)
      e.preventDefault();
      const id = CATEGORIES[e.key === "0" ? 9 : Number(e.key) - 1][0];
      if (shownCategories().some(([c]) => c === id) || state.category === id) setCategory(id);
      return;
    }
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      select(state.selection + (e.key === "ArrowDown" ? 1 : -1));
      return;
    }
  }
  if (state.step !== "type") return;
  if (e.key === "ArrowRight") {  // → accepts the grey completion, as in the app
    if (completion()) { e.preventDefault(); acceptCompletion(); }
    return;
  }
  if (e.key === "Backspace") {
    e.preventDefault();
    if (!state.typed && state.category) { setCategory(state.category); return; }  // ⌫ in an empty field: the chip goes
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
  $("#scopeChip").addEventListener("click", (e) => { e.stopPropagation(); setCategory(state.category); });
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
