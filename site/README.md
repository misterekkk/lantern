# Lantern demo site

An interactive tour of Lantern on a simulated Mac desktop, published with GitHub Pages
(`.github/workflows/pages.yml`, from the `main` branch).

- Plain HTML, CSS and JavaScript: no build step, no dependencies.
- No Apple assets: own wallpaper and SVG icons; text uses the visitor's system font (`-apple-system`).
- No analytics and no third-party requests; the only external link is the GitHub repository.

Preview locally:

```bash
python3 -m http.server -d site 8000   # then open http://localhost:8000
```

Jump to a step while working on it: `index.html?step=menu|found|indexing|ready|shortcut|type|results|preview`
(with `&scenario=photo|screenshot|video|code|pdf|song` for the search steps, `&typed=N` to start with N characters).

The search panel mirrors the app: placeholder, the grey completion (→), filter pills (click, Tab / ⇧Tab, ⌘1…⌘6),
the hint on the selected row (↩ / ⌘Y) and matched words in bold (`highlight()` is a port of the app's
`QueryHighlight` and `TextFold`). When the panel changes in the app, change it here too.

Going live: make the repository public, then Settings → Pages → Source: **GitHub Actions**, and push to `main`. The
site will be at `https://misterekkk.github.io/lantern/`.
