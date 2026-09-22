#!/usr/bin/env bash
# Assert the things that live at the edge, against the deployed URL.
#
# check_security.py, check_csp_hashes.py and check_version.py all read files in
# this repository. None of them can see a response header or a cache rule,
# because those are set in Cloudflare — so nothing here was verified by any
# pull request until this script existed.
#
#     ./tools/check_live.sh                      # defaults to the CNAME host
#     ./tools/check_live.sh https://staging.host  # a preview deployment
#
# Exits non-zero on the first failing assertion, so CI can depend on it.
set -uo pipefail

BASE="${1:-https://$(tr -d '[:space:]' < "$(dirname "$0")/../CNAME")}"
fail=0

hdr() { printf '\n\033[1m%s\033[0m\n' "$1"; }
chk() { # chk <label> <expected-regex> <url> [curl args...]
  local label=$1 want=$2 url=$3; shift 3
  local got; got=$(curl -sI "$@" "$url" | tr -d '\r')
  if grep -qiE "$want" <<<"$got"; then
    printf '  PASS  %s\n' "$label"
  else
    printf '  FAIL  %s\n        want /%s/\n        got  %s\n' "$label" "$want" \
      "$(grep -iE "${want%%:*}" <<<"$got" | head -1 || echo '(header absent)')"
    fail=1
  fi
}
no() { # no <label> <forbidden-regex> <url>
  local label=$1 bad=$2 url=$3
  if curl -sI "$url" | tr -d '\r' | grep -qiE "$bad"; then
    printf '  FAIL  %s (found /%s/)\n' "$label" "$bad"; fail=1
  else
    printf '  PASS  %s\n' "$label"
  fi
}

echo "Live checks against $BASE"

hdr "Response headers  (Cloudflare; see SECURITY-HEADERS.md)"
chk "HSTS is a year or more"      'strict-transport-security: .*max-age=(3153[0-9]{4}|[4-9][0-9]{7,})' "$BASE/"
chk "nosniff"                     'x-content-type-options: *nosniff'                "$BASE/"
chk "referrer policy is private"  'referrer-policy: *(no-referrer|same-origin|strict-origin)' "$BASE/"
chk "COOP"                        'cross-origin-opener-policy: *same-origin'        "$BASE/"
chk "CORP"                        'cross-origin-resource-policy: *same-origin'      "$BASE/"
# This is now the ONLY place framing is blocked: the meta copy was removed in
# 1.8.0 because browsers ignore it there. If this fails, the site is framable.
chk "CSP header carries frame-ancestors" "content-security-policy:.*frame-ancestors +'none'" "$BASE/"
chk "error pages are covered too"        "content-security-policy:.*frame-ancestors" "$BASE/404.html"

hdr "Redirects  (same host to TLS first, then canonical)"
for u in "http://${BASE#https://}/" "https://www.${BASE#https://}/"; do
  loc=$(curl -sI "$u" | tr -d '\r' | grep -i '^location:' | head -1)
  printf '  %-42s %s\n' "$u" "${loc:-(no redirect)}"
done

hdr "Cache lifetimes  (see the table in SECURITY-HEADERS.md)"
chk "HTML is short-lived"  'cache-control:.*(no-store|max-age=([0-9]|[1-9][0-9]{1,2})\b)' "$BASE/"
chk "fonts are long-lived" 'cache-control:.*max-age=(2[0-9]{6,}|[3-9][0-9]{6,})' \
    "$BASE/fonts/manrope-latin.woff2"
chk "images are long-lived" 'cache-control:.*max-age=(2[0-9]{6,}|[3-9][0-9]{6,})' \
    "$BASE/images/brand/favicon-32.png"

hdr "Published surface"
for p in 404.html error-401.html error-403.html error-500.html \
         robots.txt sitemap.xml ads.txt llms.txt; do
  code=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/$p")
  [ "$code" = 200 ] && printf '  PASS  /%s\n' "$p" \
                    || { printf '  FAIL  /%s -> %s\n' "$p" "$code"; fail=1; }
done
code=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/definitely-not-a-page")
[ "$code" = 404 ] && printf '  PASS  unknown path returns 404 (not a soft 404)\n' \
                  || { printf '  FAIL  unknown path returned %s, expected 404\n' "$code"; fail=1; }
no "no source map published" '^HTTP.*200' "$BASE/index.js.map"

hdr "Version mirror"
live=$(curl -s "$BASE/?bust=$$" | grep -o 'name="version" content="[^"]*"' | grep -o '[0-9][0-9.]*')
repo=$(tr -d '[:space:]' < "$(dirname "$0")/../VERSION")
if [ "$live" = "$repo" ]; then
  printf '  PASS  live and VERSION both say %s\n' "$repo"
else
  printf '  NOTE  live says %s, VERSION says %s\n' "${live:-?}" "$repo"
  printf '        cache-busted, so this is a pending deploy, not the edge:\n'
  curl -sI "$BASE/" | tr -d '\r' | grep -iE 'cf-cache-status|age' | sed 's/^/        /'
  fail=1
fi

echo
if [ "$fail" -eq 0 ]; then
  echo "All live checks passed."
else
  echo "Some live checks failed. Anything under Response headers or Cache"
  echo "lifetimes is a Cloudflare setting, not a repository change."
fi
exit "$fail"
