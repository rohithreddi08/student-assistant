// Builds the ULTRON interface for the Android app so it runs on every WebView from Android 6's stock
// engine (Chrome 44) to the latest:
//   * JavaScript: Babel (preset-env, target chrome 44) — async/await, classes, arrows, spread, ?., ??, ||=,
//     unicode regexes … are compiled to ES5; core-js adds the missing built-ins (Promise.finally,
//     Object.entries/fromEntries, Array.from, Symbol.iterator, NodeList.forEach, …).
//   * CSS: PostCSS — static fallbacks for CSS variables, inset, min()/max()/clamp(), env(), plus
//     autoprefixer (-webkit-clip-path, -webkit-filter, …) and a grid fallback for engines without grid.
//   * legacy-shims.js — getUserMedia, srcObject, isSecureContext … for old WebViews, plus an error banner
//     instead of a blank screen.
// The laptop keeps using the original static/ files untouched.
//
//   node build.mjs <src static dir> <out static dir>
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { transformAsync } from "@babel/core";
import postcss from "postcss";
import autoprefixer from "autoprefixer";
import coreJsBuilder from "core-js-builder";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SRC = path.resolve(process.argv[2] || path.join(HERE, "..", "..", "static"));
const OUT = path.resolve(process.argv[3] || path.join(HERE, "..", "app", "src", "main", "assets", "static"));
const BROWSERS = ["chrome >= 44", "android >= 6"];

// targets/presets live in babel.config.json (chrome 44 = Android 6 stock WebView)
const babelOpts = (compact) => ({
  cwd: HERE, root: HERE, babelrc: false, configFile: path.join(HERE, "babel.config.json"), compact, comments: !compact,
});

// ---------------------------------------------------------------- CSS fallbacks
const DEFAULT_VARS = { "--st": "#6fe3ff", "--lx": "0px", "--ly": "0px", "--lvl": "0", "--k": ".5", "--m4": ".15" };

function splitArgs(s) {           // split "a, b(c, d), e" on top-level commas
  const out = []; let depth = 0, cur = "";
  for (const ch of s) {
    if (ch === "(") depth++; if (ch === ")") depth--;
    if (ch === "," && depth === 0) { out.push(cur.trim()); cur = ""; } else cur += ch;
  }
  if (cur.trim()) out.push(cur.trim());
  return out;
}
// replace every call fn(...) in value using a callback on its argument list (handles nesting)
function replaceFn(value, fn, cb) {
  const re = new RegExp(`(^|[^\\w-])${fn}\\(`, "i");
  let guard = 0, m;
  while ((m = re.exec(value)) && guard++ < 50) {
    const start = m.index + m[1].length, open = start + fn.length;
    let depth = 0, i = open;
    for (; i < value.length; i++) { if (value[i] === "(") depth++; else if (value[i] === ")" && --depth === 0) break; }
    const inner = value.slice(open + 1, i);
    value = value.slice(0, start) + cb(splitArgs(inner)) + value.slice(i + 1);
  }
  return value;
}
function legacyValue(value, rootVars) {
  let v = value, prev;
  do {   // resolve var(--x, fallback) from :root, nested vars too
    prev = v;
    v = replaceFn(v, "var", ([name, ...fb]) => rootVars[name] ?? (fb.length ? fb.join(", ") : DEFAULT_VARS[name] ?? "initial"));
  } while (v !== prev && /var\(/.test(v));
  v = replaceFn(v, "env", ([, fb]) => fb || "0px");
  const rel = (a) => /vw|vh|%|em/.test(a);
  v = replaceFn(v, "min", (args) => args.find(rel) || args[0]);   // phones: the screen-relative size is the small one
  v = replaceFn(v, "max", (args) => args.find((a) => !rel(a)) || args[0]);
  v = replaceFn(v, "clamp", (args) => args[1] || args[0]);
  return v;
}
const LEGACY_EXTRA = `
/* ---- legacy engines (added by android/web/build.mjs) ---- */
@supports not (display:grid) {
  .grid { display:-webkit-flex; display:flex; -webkit-flex-wrap:wrap; flex-wrap:wrap; margin:0 -5px; }
  .grid > * { width:50%; padding:0 5px; margin-bottom:10px; }
  .grid > .full { width:100%; }
  .home-grid { display:block; }
  .pinpad { display:-webkit-flex; display:flex; -webkit-flex-wrap:wrap; flex-wrap:wrap; width:246px; margin:14px auto; }
  .pinpad button { width:72px; margin:5px; }
  header > * + *, .btnrow > *, nav button + button, .chipset > *, .chips > *, .row > * + * { margin-left:8px; }
  .btnrow > * { margin-bottom:6px; } .btnrow > :first-child, .chipset > :first-child, .chips > :first-child { margin-left:0; }
  #chatlog > * + * { margin-top:10px; }
}
`;
const legacyCss = () => ({
  postcssPlugin: "ultron-legacy",
  Once(root) {
    const rootVars = {};
    root.walkRules((r) => { if (r.selector.trim() === ":root") r.walkDecls(/^--/, (d) => { rootVars[d.prop] = d.value; }); });
    for (const k of Object.keys(rootVars)) rootVars[k] = legacyValue(rootVars[k], rootVars);
    root.walkDecls((d) => {
      if (d.prop.startsWith("--")) return;
      const lv = legacyValue(d.value, rootVars);
      if (d.prop === "inset") {
        const p = lv.split(/\s+/), [t, r = t, b = t, l = r] = p;
        for (const [prop, val] of [["top", t], ["right", r], ["bottom", b], ["left", l]]) d.cloneBefore({ prop, value: val });
      } else if (lv !== d.value) {
        d.cloneBefore({ value: lv });
        // width:min(330px, 88vw) -> width:88vw + max-width:330px (same result on old engines)
        const mm = /^min\((.*)\)$/i.exec(d.value.trim());
        if (mm && d.prop === "width" && !d.parent.some((x) => x.prop === "max-width")) {
          const abs = splitArgs(mm[1]).filter((a) => /^[\d.]+px$/.test(a));
          if (abs.length) d.cloneBefore({ prop: "max-width", value: abs[0] });
        }
      }
      if (d.prop === "overflow-wrap" && d.value === "anywhere") d.cloneBefore({ prop: "word-wrap", value: "break-word" });
    });
    root.append(postcss.parse(LEGACY_EXTRA));
  },
});
legacyCss.postcss = true;
const cssProcessor = postcss([legacyCss(), autoprefixer({ overrideBrowserslist: BROWSERS, grid: false })]);

// ---------------------------------------------------------------- build
function copyDir(a, b) {
  fs.mkdirSync(b, { recursive: true });
  for (const e of fs.readdirSync(a, { withFileTypes: true })) {
    const s = path.join(a, e.name), d = path.join(b, e.name);
    e.isDirectory() ? copyDir(s, d) : fs.copyFileSync(s, d);
  }
}
const kb = (n) => (n / 1024).toFixed(0) + " KB";

async function main() {
  const t0 = Date.now();
  fs.rmSync(OUT, { recursive: true, force: true });
  copyDir(SRC, OUT);
  fs.mkdirSync(path.join(OUT, "legacy"), { recursive: true });

  // 1. polyfills: core-js (only what Chrome 44 lacks) + our shims
  const core = await coreJsBuilder({ modules: ["es", "web"], exclude: ["web.dom-exception", "web.structured-clone"], targets: BROWSERS.join(", "), format: "bundle", summary: { console: { size: false, modules: false } } });
  const shims = fs.readFileSync(path.join(HERE, "legacy-shims.js"), "utf8");
  const poly = (await transformAsync(core + "\n" + shims, { cwd: HERE, babelrc: false, configFile: false, sourceType: "script", minified: true, comments: false, filename: "polyfills.js" })).code;
  fs.writeFileSync(path.join(OUT, "legacy", "polyfills.js"), poly);

  // 2. index.html: transpile inline <script>s and <style>s
  let html = fs.readFileSync(path.join(SRC, "index.html"), "utf8");
  let nScripts = 0;
  const parts = [];
  const re = /<script(\s[^>]*)?>([\s\S]*?)<\/script>|<style>([\s\S]*?)<\/style>/g;
  let last = 0, m;
  while ((m = re.exec(html))) {
    parts.push(html.slice(last, m.index)); last = re.lastIndex;
    if (m[3] !== undefined) {
      const css = (await cssProcessor.process(m[3], { from: undefined })).css;
      parts.push(`<style>${css}</style>`);
    } else if (/\bsrc=|type="(module|application\/json)"/.test(m[1] || "") || !m[2].trim()) {
      parts.push(m[0]);
    } else {
      const out = await transformAsync(m[2], { ...babelOpts(false), filename: `index-inline-${++nScripts}.js` });
      if (/regeneratorRuntime\b/.test(out.code) && !/function _regeneratorRuntime/.test(out.code)) throw new Error("regenerator runtime missing");
      parts.push(`<script${m[1] || ""}>${out.code}</script>`);
    }
  }
  parts.push(html.slice(last));
  html = parts.join("");
  // polyfills must run before anything else
  html = html.replace(/<head>/i, `<head>\n<script src="/static/legacy/polyfills.js"></script>`);
  fs.writeFileSync(path.join(OUT, "index.html"), html);

  // 3. vendor libraries loaded later by the interface
  const vendor = path.join(OUT, "vendor", "face-api.js");
  const fa = await transformAsync(fs.readFileSync(vendor, "utf8"), { ...babelOpts(true), filename: "face-api.js" });
  fs.writeFileSync(vendor, fa.code);

  console.log(`legacy build ok in ${((Date.now() - t0) / 1000).toFixed(1)} s: ${nScripts} scripts, polyfills ${kb(poly.length)},`,
    `index ${kb(html.length)}, face-api ${kb(fa.code.length)} -> ${OUT}`);
}
main().catch((e) => { console.error("legacy build FAILED:", e.message); process.exit(1); });
