#!/usr/bin/env python3
"""Check this site against the MUMU brand system in mumu-solutions/mumu-branding.

The brand repo is the producer, this site is a consumer, and the handoff is a
deliberate copy of `dist/tokens.css` into the page's <style> block. So drift is
expected to happen and must be *reported*, never silently reconciled — that is
the brand spec's own position (see "Relationship to the live site" and
validator check 5).

Everything here is deterministic: colour maths, file formats, token diffs. No
brand values are hardcoded — they are read from the branding repo at the ref you
ask for, so this script stays correct as the brand moves and safe to keep in a
public repo. Judgment calls (is this layout on-brand? does this copy sound like
us?) are the `site-brand-guardian` agent's job, not this script's.

Usage:
    python3 tools/brand_drift.py                     # check against the latest tag
    python3 tools/brand_drift.py --ref main          # or any branch/tag/sha
    python3 tools/brand_drift.py --ref v0.2.2 --json
    python3 tools/brand_drift.py --site . --repo mumu-solutions/mumu-branding

Needs `gh` authenticated for a private brand repo. Exit 1 if any ERROR.
Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse, unquote

ERROR, WARN, INFO = "ERROR", "WARN", "INFO"

# Precision floor from the brand spec's invariant 2: lightness 2dp (as a
# percentage), chroma 4dp, hue 2dp. Below this, hex -> OKLCH -> hex stops
# round-tripping exactly, which is what makes "one canonical form" enforceable.
L_DP, C_DP, H_DP = 2, 4, 2

# Tolerances for calling a declared OKLCH "the same colour" as its hex. Tighter
# than human-visible on purpose: the point is that one of the two is a typo, not
# that someone will notice.
L_TOL, C_TOL, H_TOL = 0.5, 0.005, 0.5
ACHROMATIC = 0.002  # below this chroma, hue is meaningless — do not compare it


class Finding:
    def __init__(self, level, check, message, fix=None):
        self.level, self.check, self.message, self.fix = level, check, message, fix

    def as_dict(self):
        return {"level": self.level, "check": self.check, "message": self.message, "fix": self.fix}


# ---------------------------------------------------------------- colour maths
def hex_to_oklch(h):
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    lin = lambda c: c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = map(lin, (r, g, b))
    l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    cbrt = lambda x: x ** (1 / 3) if x >= 0 else -((-x) ** (1 / 3))
    l_, m_, s_ = cbrt(l), cbrt(m), cbrt(s)
    L = 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_
    A = 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_
    B = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_
    return L * 100, math.hypot(A, B), math.degrees(math.atan2(B, A)) % 360


def fmt_oklch(L, C, H, alpha=None):
    # An achromatic colour has no meaningful hue, so emit the conventional
    # "0 0" rather than whatever angle the maths happened to land on.
    if C < ACHROMATIC:
        core = f"oklch({round(L, L_DP):g}% 0 0"
    else:
        core = f"oklch({round(L, L_DP):g}% {round(C, C_DP):g} {round(H, H_DP):g}"
    return core + (f" / {alpha})" if alpha else ")")


def hue_gap(a, b):
    d = abs(a - b) % 360
    return min(d, 360 - d)


# ------------------------------------------------------------ brand repo access
def gh_json(path):
    out = subprocess.run(["gh", "api", path], capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or f"gh api {path} failed")
    return json.loads(out.stdout)


def gh_file(repo, path, ref):
    """Return file text at ref, or None if it does not exist there."""
    try:
        blob = gh_json(f"repos/{repo}/contents/{path}?ref={ref}")
    except RuntimeError:
        return None
    import base64
    if isinstance(blob, list) or "content" not in blob:
        return None
    return base64.b64decode(blob["content"]).decode("utf-8", "replace")


def is_external(url):
    return bool(urlparse(url).scheme) or url.startswith("//")


def gh_json_safe(path):
    try:
        return gh_json(path)
    except RuntimeError:
        return None


def brand_format(repo, ref, key):
    """One registered format from brand/tokens/formats.yaml, e.g. open_graph."""
    text = gh_file(repo, "brand/tokens/formats.yaml", ref)
    if not text:
        return None
    m = re.search(rf"^{key}:\s*\{{(.+?)\}}", text, re.M)
    if not m:
        return None
    out = {}
    for k, v in re.findall(r"(\w+):\s*([^,}\s]+)", m.group(1)):
        out[k] = int(v) if v.isdigit() else v
    return out


def latest_tag(repo):
    tags = gh_json(f"repos/{repo}/tags")
    return tags[0]["name"] if tags else None


def ref_head(repo, ref):
    try:
        return gh_json(f"repos/{repo}/commits/{ref}")["sha"][:7]
    except RuntimeError:
        return "?"


# ------------------------------------------------------------ brand token source
def load_brand(repo, ref):
    """Find the most authoritative token source present at this ref.

    The brand repo generates dist/ from brand/tokens/, so dist/tokens.css is the
    paste-ready artifact and the right thing to diff against. Early in the
    project neither exists yet and only the design spec does — so fall back, and
    say which source was used, because it changes how much this script can claim.
    """
    css = gh_file(repo, "dist/tokens.css", ref)
    if css:
        return {"source": "dist/tokens.css", "tokens": parse_css_tokens(css),
                "version": css_version(css), "raw": css}

    color_yaml = gh_file(repo, "brand/tokens/color.yaml", ref)
    if color_yaml:
        return {"source": "brand/tokens/color.yaml", "tokens": parse_yaml_colors(color_yaml),
                "version": None, "raw": color_yaml}

    spec = gh_file(repo, "docs/superpowers/specs/2026-09-10-marketing-brand-system-design.md", ref)
    if spec:
        return {"source": "design spec (tokens not implemented yet)",
                "tokens": {}, "version": None, "raw": spec}

    return {"source": None, "tokens": {}, "version": None, "raw": ""}


def iter_rules(css):
    """Yield (selector, declarations) for innermost rule blocks only.

    Written as a brace scanner rather than a regex because both the brand's
    tokens.css and this site nest their OKLCH branch inside `@supports (...){ }`.
    A regex over `sel{body}` reads the at-rule line as the selector and swallows
    the inner `:root{`, so the light OKLCH block lands in the same bucket as the
    dark one and silently overwrites it — which is exactly the false "drift"
    this function exists to avoid reporting.
    """
    depth, buf, stack = 0, [], []
    i = 0
    while i < len(css):
        ch = css[i]
        if ch == "{":
            stack.append("".join(buf).strip())
            buf = []
            depth += 1
        elif ch == "}":
            body = "".join(buf)
            sel = stack.pop() if stack else ""
            depth -= 1
            # a block containing declarations (not just nested blocks) is a rule
            if "--" in body or ":" in body:
                yield sel, body
            buf = []
        else:
            buf.append(ch)
        i += 1


def scope_of(selector):
    s = selector.lower()
    if 'data-theme="light"' in s or "[data-theme=light]" in s:
        return "light"
    if 'data-theme="dark"' in s or "[data-theme=dark]" in s:
        return "dark"
    if ":root" in s or s in ("html", "body"):
        return "root"
    return None


def collect_tokens(css):
    """{scope: {token: {'hex':…, 'oklch':…, 'raw':…}}} — every token, not just colours."""
    out = {}
    for sel, body in iter_rules(css):
        scope = scope_of(sel)
        if not scope:
            continue
        bucket = out.setdefault(scope, {})
        for name, val in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", body):
            val = val.strip()
            slot = bucket.setdefault(name, {})
            if val.startswith("#"):
                slot["hex"] = val
            elif val.startswith("oklch"):
                slot["oklch"] = val
            else:
                slot["raw"] = val
    return out


def parse_css_tokens(css):
    return collect_tokens(css)


def theme_identity(tokens):
    """Which scope means dark and which means light, decided by the palette
    rather than by selector names — a brand that goes light-first would put
    light in `:root`, and comparing `:root` to `:root` would then be wrong."""
    ident = {}
    for scope, toks in tokens.items():
        probe = toks.get("--bg-body") or toks.get("--bg-surface") or toks.get("--bg")
        L = None
        if probe and probe.get("hex"):
            L = hex_to_oklch(probe["hex"])[0]
        elif probe and probe.get("oklch"):
            m = re.search(r"([\d.]+)%", probe["oklch"])
            L = float(m.group(1)) if m else None
        ident[scope] = None if L is None else ("dark" if L < 50 else "light")
    return ident


def normalize_value(v):
    """`.5rem` and `0.5rem` are the same length; quoting and spacing in a font
    stack are noise. Compare meaning, not keystrokes."""
    v = v.lower().replace(" ", "").replace('"', "'").rstrip(";")
    return re.sub(r"\b0+(\.\d)", r"\1", v)


def parse_yaml_colors(text):
    """Minimal reader for `token: {hex: ..., oklch: ...}` shapes. Deliberately
    forgiving: it only needs the two values, not the whole schema."""
    out, scope, token = {}, "root", None
    for line in text.splitlines():
        if re.match(r"^\s*#", line) or not line.strip():
            continue
        m = re.match(r"^(\s*)([\w-]+):\s*(.*)$", line)
        if not m:
            continue
        indent, key, val = len(m.group(1)), m.group(2), m.group(3).strip().strip('"\'')
        if indent == 0:
            scope = key if key in ("dark", "light") else "root"
            continue
        if val.startswith("#"):
            out.setdefault(scope, {}).setdefault(token or key, {})["hex"] = val
        elif val.startswith("oklch"):
            out.setdefault(scope, {}).setdefault(token or key, {})["oklch"] = val
        elif not val:
            token = key
    return out


def css_version(css):
    m = re.search(r"version[\"']?\s*[:=]\s*[\"']?v?([\d]+\.[\d]+\.[\d]+)", css, re.I)
    return m.group(1) if m else None


# -------------------------------------------------------------------- site side
def parse_site(html):
    """Token blocks, declared fonts, and preloads as the page actually ships.
    Uses the same collector as the brand side so the two are symmetric — a
    difference in parsing would show up as a difference in the palette."""
    style = "\n".join(re.findall(r"<style>(.*?)</style>", html, re.S)) or html
    scopes = collect_tokens(style)
    fams = {}
    for name, val in re.findall(r"(--font-[\w-]+)\s*:\s*([^;]+);", html):
        fams[name] = val.strip()
    preloads = re.findall(r'<link rel="preload" href="([^"]+\.woff2)"', html)
    faces = re.findall(r"@font-face\{font-family:([^;]+);src:url\(([^)]+)\)", html)
    return {"scopes": scopes, "families": fams, "preloads": preloads, "faces": faces}


def png_is_png(p: Path):
    return p.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def png_size(p: Path):
    b = p.read_bytes()
    if not png_is_png(p):
        # JPEG: walk the segments for SOFn
        i, n = 2, len(b)
        while i < n - 9:
            if b[i] != 0xFF:
                i += 1
                continue
            marker = b[i + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3):
                return int.from_bytes(b[i + 7:i + 9], "big"), int.from_bytes(b[i + 5:i + 7], "big")
            i += 2 + int.from_bytes(b[i + 2:i + 4], "big")
        return None
    return int.from_bytes(b[16:20], "big"), int.from_bytes(b[20:24], "big")


# ----------------------------------------------------------------------- checks
def run(site_dir: Path, repo: str, ref: str):
    findings = []
    add = lambda lvl, chk, msg, fix=None: findings.append(Finding(lvl, chk, msg, fix))

    page = site_dir / "index.html"
    if not page.exists():
        add(ERROR, "site", f"no index.html in {site_dir}")
        return findings, {}

    html = page.read_text(encoding="utf-8", errors="replace")
    site = parse_site(html)
    brand = load_brand(repo, ref)

    meta = {"repo": repo, "ref": ref, "ref_head": ref_head(repo, ref),
            "brand_source": brand["source"], "brand_version": brand["version"],
            "site_tokens": sum(len(v) for v in site["scopes"].values())}

    if not brand["source"]:
        add(ERROR, "brand-source",
            f"found no token source in {repo}@{ref} — checked dist/tokens.css, "
            f"brand/tokens/color.yaml and the design spec")
        return findings, meta

    # -- check 1: is each declared OKLCH the same colour as its own hex? -------
    # Brand decision D2: the hex is canonical and OKLCH is derived from it, so a
    # mismatch is always the OKLCH being wrong, never the hex.
    for scope, tokens in sorted(site["scopes"].items()):
        for name, vals in sorted(tokens.items()):
            if "hex" not in vals or "oklch" not in vals:
                continue
            nums = re.findall(r"[\d.]+", vals["oklch"].split("/")[0])
            if len(nums) < 3:
                continue
            dL, dC, dH = (float(n) for n in nums[:3])
            alpha = vals["oklch"].split("/")[1].strip(" )") if "/" in vals["oklch"] else None
            L, C, H = hex_to_oklch(vals["hex"])
            bad = []
            if abs(L - dL) > L_TOL:
                bad.append(f"lightness {dL}% vs {L:.2f}%")
            if abs(C - dC) > C_TOL:
                bad.append(f"chroma {dC} vs {C:.4f}")
            if C > ACHROMATIC and dC > ACHROMATIC and hue_gap(H, dH) > H_TOL:
                bad.append(f"hue {dH}° vs {H:.2f}° ({hue_gap(H, dH):.1f}° apart)")
            if bad:
                add(ERROR, "oklch-derivation",
                    f"{scope} {name}: declared {vals['oklch']} is not {vals['hex']} — " + "; ".join(bad),
                    f"{name}:{fmt_oklch(L, C, H, alpha)};")

    # -- check 2: does the site's blue match the brand's canonical blue? -------
    brand_primary = None
    for scope in ("dark", "root", "light"):
        v = brand["tokens"].get(scope, {}).get("--primary") or brand["tokens"].get(scope, {}).get("primary")
        if v and v.get("hex"):
            brand_primary = v["hex"].lower()
            break
    if brand_primary:
        site_primary = (site["scopes"].get("dark", {}) or site["scopes"].get("root", {})).get("--primary", {}).get("hex", "").lower()
        if site_primary and site_primary != brand_primary:
            add(ERROR, "canonical-colour",
                f"site --primary is {site_primary}, brand says {brand_primary}",
                f"--primary:{brand_primary};")
    else:
        add(INFO, "canonical-colour",
            f"{brand['source']} declares no machine-readable --primary yet, so the "
            f"site's blue could not be compared to the brand's")

    # -- check 3: token-by-token drift against the generated CSS --------------
    # Align the two sides by which theme a scope *is*, never by what its
    # selector is called.
    if brand["tokens"]:
        b_ident, s_ident = theme_identity(brand["tokens"]), theme_identity(site["scopes"])
        site_by_theme = {}
        for scope, theme in s_ident.items():
            if theme:
                site_by_theme.setdefault(theme, {}).update(site["scopes"][scope])

        for scope, btokens in sorted(brand["tokens"].items()):
            theme = b_ident.get(scope)
            if not theme:
                add(INFO, "token-drift",
                    f"could not tell whether the brand's `{scope}` block is the dark or the light "
                    f"theme (no background token to measure), so it was not compared")
                continue
            stokens = site_by_theme.get(theme, {})
            if not stokens:
                add(WARN, "token-drift", f"the brand defines a {theme} theme; the site has no {theme} block")
                continue
            for name, bval in sorted(btokens.items()):
                key = name if name.startswith("--") else f"--{name}"
                if key not in stokens:
                    add(WARN, "token-drift", f"{theme} {key} exists in the brand but not on the site")
                    continue
                for kind in ("hex", "oklch", "raw"):
                    if bval.get(kind) and stokens[key].get(kind):
                        if normalize_value(bval[kind]) != normalize_value(stokens[key][kind]):
                            add(ERROR, "token-drift",
                                f"{theme} {key}: site has {stokens[key][kind]}, brand has {bval[kind]}",
                                f"{key}:{bval[kind]};")

    # -- check 4: typography families ----------------------------------------
    brand_type = gh_file(repo, "brand/tokens/typography.yaml", ref)
    if brand_type:
        for fam in re.findall(r'family:\s*["\']?([A-Za-z ]+)', brand_type):
            if fam.strip() and fam.strip().lower() not in html.lower():
                add(WARN, "typography", f"brand registers the family '{fam.strip()}', which the site never declares")
    else:
        add(INFO, "typography",
            f"no brand/tokens/typography.yaml at {ref}; site declares "
            + ", ".join(sorted(set(re.findall(r"'([A-Za-z ]+)'", " ".join(site['families'].values()))))))

    # -- check 5: assets the brand registers ----------------------------------
    # Resolve the share image from the page's own og:image rather than assuming
    # a filename — the site is free to name it whatever the brand named it.
    m = re.search(r'<meta\s+property="og:image"\s+content="([^"]+)"', html)
    og_url = m.group(1) if m else ""
    og_rel = (urlparse(og_url).path if is_external(og_url) else og_url).lstrip("/")
    og = site_dir / unquote(og_rel) if og_rel else None
    want = brand_format(repo, ref, "open_graph")
    if og and og.exists():
        if not png_is_png(og) and (not want or want.get("format", "png") == "png"):
            add(ERROR, "asset-format",
                f"{og_rel} is not PNG data despite the extension "
                f"(brand spec defect 4 — unacceptable in a canonical asset)")
        size = png_size(og)
        if size and want and (size[0], size[1]) != (want["width"], want["height"]):
            add(ERROR, "asset-format",
                f"{og_rel} is {size[0]}×{size[1]}; formats.yaml registers "
                f"open_graph at {want['width']}×{want['height']}")
    elif og_rel:
        add(ERROR, "asset-format", f"og:image points at {og_rel}, which is not in the repo")

    # Provenance beats file format. The brand's sanctioned handoff is
    # dist/assets, and logo.yaml assigns those raster exports to specific slots
    # — so "the site uses a PNG" is not a finding. "The site uses a PNG the
    # brand did not generate, or generated differently" is.
    # Index everything the brand publishes under dist/ — since v0.4.0 the
    # sanctioned handoff is dist/brand-kit/ as well as dist/assets, and
    # USAGE.md sends the site header to an SVG that only exists in the kit.
    tree = gh_json_safe(f"repos/{repo}/git/trees/{ref}?recursive=1")
    dist = [b for b in (tree or {}).get("tree", [])
            if b.get("type") == "blob" and b["path"].startswith("dist/")]
    if dist:
        by_name = {}
        for b in dist:
            by_name.setdefault(b["path"].rsplit("/", 1)[-1], []).append(b)
        local = sorted((site_dir / "images" / "brand").glob("*")) if (site_dir / "images" / "brand").is_dir() else []
        if not local:
            add(WARN, "asset-provenance",
                f"the brand publishes {len(dist)} files under dist/ at {ref}, but the site "
                f"copies none of them into images/brand/")
        for f in local:
            entries = by_name.get(f.name)
            size = f.stat().st_size
            if not entries:
                add(WARN, "asset-provenance",
                    f"images/brand/{f.name} is not published anywhere under dist/ at {ref} — "
                    f"either renamed here or dropped there")
            elif not any(e.get("size") == size for e in entries):
                where = entries[0]["path"]
                add(ERROR, "asset-provenance",
                    f"images/brand/{f.name} is {size} bytes, the brand's is "
                    f"{entries[0]['size']} ({where}) — the copy is stale or was edited by hand",
                    f"re-copy from {where} at {ref}")
        manifest = site_dir / "images" / "brand" / "MANIFEST.json"
        if manifest.exists() and brand["version"]:
            try:
                versions = {v.get("version") for v in json.loads(manifest.read_text())["files"].values()}
                if versions - {brand["version"]}:
                    add(ERROR, "asset-provenance",
                        f"images/brand/MANIFEST.json says {sorted(versions)}, brand at {ref} is "
                        f"{brand['version']} — the copied assets are from another release")
            except (json.JSONDecodeError, KeyError) as e:
                add(WARN, "asset-provenance", f"images/brand/MANIFEST.json is unreadable: {e}")

    # -- check 6: is the site quoting a released brand version? ---------------
    if brand["version"]:
        stamped = re.search(r"brand[- ]version[:\s]*v?([\d.]+)", html, re.I)
        if not stamped:
            add(WARN, "version-stamp",
                f"brand {brand['source']} is version {brand['version']}, but the site records no "
                f"brand version — nobody can tell whether this page is current",
                f"<!-- brand-version: {brand['version']} -->")
        elif stamped.group(1) != brand["version"]:
            add(ERROR, "version-stamp",
                f"site says brand v{stamped.group(1)}, brand repo at {ref} is v{brand['version']}")

    return findings, meta


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--site", default=".", help="site root (default: .)")
    ap.add_argument("--repo", default="mumu-solutions/mumu-branding")
    ap.add_argument("--ref", default=None, help="branch, tag or sha (default: latest tag)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    ref = args.ref or latest_tag(args.repo) or "main"
    findings, meta = run(Path(args.site).resolve(), args.repo, ref)

    if args.json:
        print(json.dumps({"meta": meta, "findings": [f.as_dict() for f in findings]}, indent=2, ensure_ascii=False))
    else:
        print(f"\n  brand drift: site {meta.get('site_tokens', 0)} tokens  vs  {args.repo}@{ref} ({meta.get('ref_head','?')})")
        print(f"  brand source: {meta.get('brand_source')}"
              + (f"  ·  brand version {meta['brand_version']}" if meta.get("brand_version") else ""))
        order = {ERROR: 0, WARN: 1, INFO: 2}
        for lvl in (ERROR, WARN, INFO):
            items = [f for f in findings if f.level == lvl]
            if not items:
                continue
            print(f"\n  {lvl} ({len(items)})")
            for f in sorted(items, key=lambda f: f.check):
                print(f"    [{f.check}] {f.message}")
                if f.fix:
                    print(f"       -> {f.fix}")
        counts = {lvl: len([f for f in findings if f.level == lvl]) for lvl in (ERROR, WARN, INFO)}
        print(f"\n  {counts[ERROR]} error(s), {counts[WARN]} warning(s), {counts[INFO]} note(s)\n")

    return 1 if any(f.level == ERROR for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
