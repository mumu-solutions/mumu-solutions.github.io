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


def parse_css_tokens(css):
    """{scope: {token: {'hex':..., 'oklch':...}}} from a generated tokens.css."""
    out = {}
    for sel, body in re.findall(r"([^{}]+)\{([^}]*)\}", css):
        scope = "dark" if "dark" in sel else ("light" if "light" in sel else "root")
        bucket = out.setdefault(scope, {})
        for name, val in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", body):
            val = val.strip()
            key = "hex" if val.startswith("#") else ("oklch" if val.startswith("oklch") else "other")
            bucket.setdefault(name, {})[key] = val
    return out


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
    """Token blocks, declared fonts, and preloads as the page actually ships."""
    scopes = {}
    for sel, body in re.findall(r"(:root|html\[data-theme=\"[a-z]+\"\])\s*\{([^}]*)\}", html):
        scope = "light" if "light" in sel else ("dark" if "dark" in sel else "root")
        bucket = scopes.setdefault(scope, {})
        for name, val in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", body):
            val = val.strip()
            if val.startswith("#"):
                bucket.setdefault(name, {})["hex"] = val
            elif val.startswith("oklch"):
                bucket.setdefault(name, {})["oklch"] = val
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
    if brand["tokens"]:
        for scope, btokens in sorted(brand["tokens"].items()):
            stokens = site["scopes"].get(scope, {})
            for name, bval in sorted(btokens.items()):
                key = name if name.startswith("--") else f"--{name}"
                if key not in stokens:
                    add(WARN, "token-drift", f"{scope} {key} exists in the brand but not on the site")
                    continue
                for kind in ("hex", "oklch"):
                    if bval.get(kind) and stokens[key].get(kind):
                        b, s = bval[kind].lower().replace(" ", ""), stokens[key][kind].lower().replace(" ", "")
                        if b != s:
                            add(ERROR, "token-drift",
                                f"{scope} {key} {kind}: site has {stokens[key][kind]}, brand has {bval[kind]}",
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
    og = site_dir / "images" / "og-image.png"
    if og.exists():
        if not png_is_png(og):
            add(ERROR, "asset-format",
                "images/og-image.png is not PNG data despite the extension "
                "(brand spec defect 4 — unacceptable in a canonical asset)",
                "re-encode as real PNG, e.g. sips -s format png og-image.png --out og-image.png")
        size = png_size(og)
        if size and size != (1200, 630):
            add(ERROR, "asset-format", f"og-image is {size[0]}×{size[1]}, brand format is 1200×630")
    else:
        add(WARN, "asset-format", "images/og-image.png is missing")

    logo_svgs = gh_json(f"repos/{repo}/contents/brand/source/logo?ref={ref}") if brand["tokens"] else None
    if isinstance(logo_svgs, list) and any(f["name"].endswith(".svg") for f in logo_svgs):
        if not list((site_dir / "images").glob("*.svg")):
            add(WARN, "logo", "the brand now ships vector logo source, but the site still uses only raster")

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
