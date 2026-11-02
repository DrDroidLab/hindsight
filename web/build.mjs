import { build } from "esbuild";
import { mkdir, copyFile } from "node:fs/promises";
const out = "../src/hindsight/static";
await mkdir(`${out}/assets`, { recursive: true });
await build({
  entryPoints: ["src/app.ts"],
  bundle: true,
  minify: true,
  format: "esm",
  target: "es2022",
  outfile: `${out}/assets/app.js`,
});
await copyFile("index.html", `${out}/index.html`);
await copyFile("style.css", `${out}/assets/style.css`);
