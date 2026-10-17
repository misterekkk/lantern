// The website: a landing page (src/pages/index.astro), the interactive demo (public/demo, plain JS) and the docs
// (Starlight; pages generated from the repository's README and docs/ by scripts/sync-docs.mjs). Static output, no
// analytics, fonts served from the site itself.
import { defineConfig } from "astro/config";
import starlight from "@astrojs/starlight";

export default defineConfig({
  site: "https://misterekkk.github.io",
  base: "/lantern",
  trailingSlash: "always",
  integrations: [
    starlight({
      title: "Lantern",
      logo: { src: "./src/assets/lantern-icon.svg", replacesTitle: false },
      favicon: "/favicon.svg",
      customCss: ["@fontsource-variable/atkinson-hyperlegible-next", "@fontsource-variable/instrument-sans", "./src/styles/docs.css"],
      social: [{ icon: "github", label: "GitHub", href: "https://github.com/misterekkk/lantern" }],
      sidebar: [
        { label: "Start", items: [{ slug: "docs/install" }, { slug: "docs/app" }] },
        { label: "Using Lantern", items: [{ slug: "docs/search" }, { slug: "docs/folders" }, { slug: "docs/launchers" }, { slug: "docs/cli" }] },
        { label: "Under the hood", items: [{ slug: "docs/privacy" }, { slug: "docs/daemon" }, { slug: "docs/architecture" }, { slug: "docs/limitations" }] },
      ],
      pagefind: true,
    }),
  ],
});
