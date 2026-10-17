# Lantern website

The landing page, the docs and the interactive demo, built with [Astro](https://astro.build) and
[Starlight](https://starlight.astro.build) and published with GitHub Pages (`.github/workflows/pages.yml`, from the
`main` branch) at `https://misterekkk.github.io/lantern/`.

```
src/pages/index.astro     the landing page
src/styles/docs.css       the docs theme (graphite and amber, the colours of the icon)
scripts/sync-docs.mjs     builds the docs pages from README.md and docs/*.md (generated, not committed)
public/demo/              the interactive demo: plain HTML, CSS and JavaScript, served as is under /demo/
```

- No analytics and no third-party requests: fonts (Atkinson Hyperlegible Next, Instrument Sans) are bundled from
  fontsource, the only external link is the GitHub repository. Astro's build telemetry is switched off in the npm
  scripts (`ASTRO_TELEMETRY_DISABLED=1`).
- No Apple assets: own wallpaper and SVG icons.
- The docs are never edited here: change `README.md` or `docs/`, and the next build picks it up.

Work on it locally (needs Node.js, `brew install node`):

```bash
cd site
npm ci
npm run dev        # http://localhost:4321/lantern/
npm run build      # into site/dist, with the Pagefind search index
```

## The demo

A tour of Lantern on a simulated Mac desktop. Jump to a step while working on it:
`demo/index.html?step=menu|found|indexing|ready|shortcut|type|results|preview` (with
`&scenario=photo|screenshot|video|code|pdf|app|setting|answer|music` for the search steps, `&typed=N` to start with N
characters).

The search panel mirrors the app: "Search Lantern", the grey completion (→), filter icons beside the field that fold
into one button when the text reaches them (hover shows the name; a chosen one becomes a chip; click, Tab / ⇧Tab,
⌘1…⌘9, ⌘0, Esc or ⌫ removes it), the hint on the selected row (↩ / ⌘Y) and matched words in bold (`highlight()` is a
port of the app's `QueryHighlight` and `TextFold`). When the panel changes in the app, change it here too.

When `app.js` or `styles.css` change, bump their `?v=` in `demo/index.html`: otherwise a browser may combine a cached
page with the new script, and the tour stops responding.

Going live: make the repository public, then Settings → Pages → Source: **GitHub Actions**, and push to `main`.
