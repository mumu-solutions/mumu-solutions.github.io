#!/usr/bin/env bash
# Run Lighthouse against a URL and enforce a budget, in a pinned container.
#
# The container exists so the numbers are comparable between a laptop and CI:
# Lighthouse's score weights and Chrome's behaviour both move between versions,
# and an unpinned run silently changes what "regression" means.
#
#     ./tools/check_lighthouse.sh                        # live site, both form factors
#     ./tools/check_lighthouse.sh https://host mobile    # one form factor
#
# Needs docker. Builds the image on first use (~1 min), reuses it after.
#
# Reading the output: an item is only actionable here if the URL it names is
# ours. Most of what Lighthouse reports on this site is the AdSense loader
# being blocked by our own CSP, or a Cloudflare edge injection — neither is
# fixable in this repository. See ADSENSE.md and SECURITY-HEADERS.md.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BASE="${1:-https://$(tr -d '[:space:]' < "$ROOT/CNAME")}"
ONLY="${2:-}"
IMAGE="mumu-lighthouse:pinned"
OUT="$(mktemp -d)"

# Budget. Google's thresholds for "good", with the categories this site holds.
MAX_LCP_MS=2500
MAX_CLS=0.1
MIN_PERF=90
MIN_A11Y=100
MIN_SEO=100

command -v docker >/dev/null || { echo "docker required"; exit 2; }
docker image inspect "$IMAGE" >/dev/null 2>&1 || {
  echo "Building $IMAGE (first run only)..."
  docker build -q -f "$ROOT/tools/lighthouse/Dockerfile" -t "$IMAGE" "$ROOT/tools/lighthouse" >/dev/null
}

fail=0
for form in mobile desktop; do
  [ -n "$ONLY" ] && [ "$ONLY" != "$form" ] && continue
  preset=""; [ "$form" = desktop ] && preset="--preset=desktop"
  printf '\n\033[1m%s — %s\033[0m\n' "$BASE" "$form"
  docker run --rm -v "$OUT:/out" "$IMAGE" "$BASE" $preset \
    --output=json --output-path="/out/$form.json" --quiet \
    --only-categories=performance,accessibility,best-practices,seo \
    --chrome-flags="--headless --no-sandbox" >/dev/null 2>&1

  [ -s "$OUT/$form.json" ] || { echo "  FAIL  lighthouse produced no report"; fail=1; continue; }

  python3 - "$OUT/$form.json" "$MAX_LCP_MS" "$MAX_CLS" "$MIN_PERF" "$MIN_A11Y" "$MIN_SEO" <<'PY' || fail=1
import json, sys
path, max_lcp, max_cls, min_perf, min_a11y, min_seo = sys.argv[1:7]
d = json.load(open(path)); d = d.get("lighthouseResult", d)
bad = 0

def show(label, value, ok, shown=None):
    global bad
    print(f"  {'PASS' if ok else 'FAIL'}  {label:34} {shown if shown is not None else value}")
    if not ok: bad = 1

cats = {k: round((v.get("score") or 0) * 100) for k, v in d["categories"].items()}
show("performance", cats.get("performance"), cats.get("performance", 0) >= int(min_perf))
show("accessibility", cats.get("accessibility"), cats.get("accessibility", 0) >= int(min_a11y))
show("seo", cats.get("seo"), cats.get("seo", 0) >= int(min_seo))
print(f"  NOTE  best-practices                  {cats.get('best-practices')}"
      "  (see the console errors below)")

a = d["audits"]
lcp = a["largest-contentful-paint"]["numericValue"]
cls = a["cumulative-layout-shift"]["numericValue"]
show("largest-contentful-paint", lcp, lcp < float(max_lcp), f"{lcp:.0f} ms")
show("cumulative-layout-shift", cls, cls < float(max_cls), f"{cls:.3f}")

# Attribute the console errors, because almost none of them are ours.
items = ((a.get("errors-in-console") or {}).get("details") or {}).get("items", [])
ours, third = [], []
for it in items:
    text = json.dumps(it)
    (ours if "mumu.solutions" in text and "google" not in text else third).append(it)
print(f"  NOTE  console errors                  {len(items)}"
      f"  ({len(third)} third-party, {len(ours)} ours)")
for it in ours:
    print(f"        ours: {(it.get('description') or '')[:88]}")
sys.exit(bad)
PY
done

echo
[ "$fail" -eq 0 ] && echo "Budget met." \
                  || echo "Budget missed. Check whether the named URLs are ours before changing code."
rm -rf "$OUT"
exit "$fail"
