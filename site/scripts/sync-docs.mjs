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
const written = [];
function page(slug, title, description, body) {
  const front = `---\ntitle: ${JSON.stringify(title)}\ndescription: ${JSON.stringify(description)}\n---\n\n`;
  writeFileSync(join(out, `${slug}.md`), front + links(body.trim()) + "\n");
  written.push({ slug, title, description, body: body.trim() });
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
// llms.txt (llmstxt.org) and llms-full.txt for language models, generated from the same Markdown as the docs pages, so
// they can never describe features, commands or requirements that the README and docs/ do not (both are gitignored).
const site = "https://misterekkk.github.io/lantern/";
const order = ["install", "app", "search", "folders", "launchers", "cli", "privacy", "daemon", "architecture", "limitations"];
const ordered = order.map((slug) => written.find((p) => p.slug === slug)).filter(Boolean);
const intro = readme.split("\n## ")[0].split("\n").filter((l) => !l.startsWith("<") && !l.startsWith("</")).join("\n")
  .replace(/^# .*\n/m, "").trim();
const summary =
  "> Lantern is a local, offline semantic search app for macOS 26.2 (Tahoe) or newer on Apple Silicon. It finds files, " +
  "code definitions, PDFs, photos and the moments in recordings and videos by meaning and by exact words, next to apps, " +
  "System Settings, Shortcuts and music, in a Spotlight-like panel, in Raycast or Alfred, or from the `lantern` command " +
  "line. A Swift menu bar app and a Python daemon run the models (EmbeddingGemma 2, Whisper large-v3-turbo, SigLIP 2) " +
  "with Apple MLX on the Mac; after the one-time model download nothing leaves the Mac, except an opt-in Spotify " +
  "library sync. Open source under the Apache License 2.0 (the Raycast extension: MIT).";
const pub = join(root, "site", "public");
writeFileSync(join(pub, "llms.txt"), [
  "# Lantern", "", summary, "",
  "Generated from the repository's README.md and docs/ when the website is built.", "",
  "## Docs", "",
  ...ordered.map((p) => `- [${p.title}](${site}docs/${p.slug}/): ${p.description}`), "",
  "## Optional", "",
  `- [Full documentation in one file](${site}llms-full.txt)`,
  `- [Source code](https://github.com/misterekkk/lantern)`,
  `- [Changelog](https://github.com/misterekkk/lantern/blob/main/CHANGELOG.md)`, "",
].join("\n"));
writeFileSync(join(pub, "llms-full.txt"), [
  "# Lantern", "", summary, "", links(intro), "",
  ...ordered.map((p) => `\n---\n\n# ${p.title}\n\nSource: ${site}docs/${p.slug}/\n\n${links(p.body.replace(/^#/gm, "##"))}\n`),
].join("\n"));
console.log("docs synced into src/content/docs/docs; llms.txt and llms-full.txt written to public/");
