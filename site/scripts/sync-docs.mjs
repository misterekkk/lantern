// Builds the docs pages from the repository's README.md and docs/*.md, so the website never drifts from them.
// README sections become pages; docs/ files are copied with front matter. Links between them are rewritten.
import { readFileSync, writeFileSync, mkdirSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const out = join(root, "site", "src", "content", "docs", "docs");
const repo = "https://github.com/misterekkk/lantern/blob/main/";
rmSync(out, { recursive: true, force: true });
mkdirSync(out, { recursive: true });

const pages = {
  "docs/PRIVACY.md": "privacy", "docs/CLI.md": "cli", "docs/ARCHITECTURE.md": "architecture",
};
function links(text) {
  return text
    .replace(/\]\((?:\.\.\/)?docs\/(PRIVACY|CLI|ARCHITECTURE)\.md(#[^)]*)?\)/g, (_, f, h = "") => `](../${f.toLowerCase()}/${h})`)
    .replace(/\]\((?!https?:|#|\.\.\/)([^)]+)\)/g, (_, p) => `](${repo}${p.replace(/^\.\//, "")})`);
}
function page(slug, title, description, body) {
  const front = `---\ntitle: ${JSON.stringify(title)}\ndescription: ${JSON.stringify(description)}\n---\n\n`;
  writeFileSync(join(out, `${slug}.md`), front + links(body.trim()) + "\n");
}

// README: one page per section (## heading)
const readme = readFileSync(join(root, "README.md"), "utf8");
const sections = {};
let current = null;
for (const line of readme.split("\n")) {
  const h = line.match(/^## (.+)$/);
  if (h) { current = h[1].trim(); sections[current] = []; continue; }
  if (current) sections[current].push(line);
}
const text = (name) => (sections[name] || []).join("\n").replace(/^###/gm, "##");
page("install", "Install", "Requirements and installation, step by step.", text("Requirements") + "\n\n## Installation\n\n" + text("Installation"));
page("app", "The app", "The menu bar app and the search panel: keys, filters, previews.", text("The app"));
page("search", "How search works", "Meaning and words together; PDFs, code, photos and recordings.", text("How search works"));
page("folders", "Folders and exclusions", "What gets indexed and how to leave things out.", text("Folders and exclusions"));
page("launchers", "Raycast and Alfred", "Search with Lantern from Raycast or Alfred, step by step.", text("Use with Raycast or Alfred"));
page("daemon", "Background daemon", "How Lantern runs in the background and saves battery.", text("Background daemon"));
page("limitations", "Known limitations", "What Lantern does not do yet.", text("Known limitations"));

// docs/: whole files, their first heading becomes the title
for (const [file, slug] of Object.entries(pages)) {
  const raw = readFileSync(join(root, file), "utf8");
  const m = raw.match(/^# (.+)\n/);
  const title = m ? m[1].trim() : slug;
  page(slug, title, `${title} — from the repository's ${file}.`, m ? raw.slice(m[0].length) : raw);
}
console.log("docs synced into src/content/docs/docs");
