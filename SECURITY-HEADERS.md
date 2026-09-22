# Security headers

Target: **A+** on [MDN HTTP Observatory](https://developer.mozilla.org/en-US/observatory/analyze?host=mumu.solutions).
A+ needs 100 points; the maximum is 105.

## Where things can live, and why

GitHub Pages serves static files and **cannot set response headers**. Observatory
reads almost everything from headers, with exactly two exceptions — confirmed in
its source, `src/retriever/utils.js`, `parseHttpEquivHeaders()`:

```js
if (content && httpEquiv === CONTENT_SECURITY_POLICY) { ... }        // <meta http-equiv="Content-Security-Policy">
else if (getAttribute(meta, "name")?.toLowerCase().trim() === "referrer") { ... }  // <meta name="referrer">
```

Those two are in `index.html`. Everything else has to come from Cloudflare,
which sits in front of Pages and is the only place that can add headers.

## Baseline, measured

Scan of `www.mumu.solutions`, algorithm version 6, before any of this. That was
the canonical host at the time; it is the apex now, and the weekly Observatory
run follows `CNAME`, so later scans are of `mumu.solutions`:

| mod | test | result |
|----:|------|--------|
| −25 | content-security-policy | csp-not-implemented |
| −20 | redirection | redirection-missing |
| −20 | strict-transport-security | hsts-not-implemented |
| −20 | x-frame-options | x-frame-options-not-implemented |
|  −5 | x-content-type-options | x-content-type-options-not-implemented |
|   0 | cookies, COOP, COEP, CORP, CORS, referrer-policy | not implemented |
|  +5 | subresource-integrity | implemented-and-external-scripts-loaded-securely |

**Grade F, score 10.**

## Resolved: plaintext HTTP and the redirect shape

This section used to open "fix this first": the site answered 200 on plaintext
HTTP, and the apex hopped to the other host while still unencrypted. Cloudflare's
**SSL/TLS → Edge Certificates → Always Use HTTPS** has since been switched on and
the shape is now the wanted one — same host to HTTPS first, then the final host:

```
http://mumu.solutions/       -> 301 -> https://mumu.solutions/       (same host, to TLS)
http://www.mumu.solutions/   -> 301 -> https://www.mumu.solutions/   (same host, to TLS)
https://www.mumu.solutions/  -> 301 -> https://mumu.solutions/       (to the canonical host)
```

That last hop is GitHub Pages, not Cloudflare: the apex is the custom domain
configured for the Pages deployment, so Pages redirects www to it. Nothing in
Cloudflare needs to know about the canonical host.

Re-verify after any DNS or Cloudflare change:

```bash
for u in http://mumu.solutions/ http://www.mumu.solutions/ https://www.mumu.solutions/; do
  echo -n "$u -> "; curl -sI "$u" | grep -iE '^HTTP|^location' | tr '\n' ' '; echo
done
```

One thing is still short of the claim it makes: the HSTS header carries
`preload` but `max-age=15552000` (180 days), and the preload list requires at
least a year. Either raise the max-age to `31536000` and submit the domain, or
drop the `preload` token so the header stops advertising something that has not
been done.

## Cloudflare: the header set

**Measured 2026-09-22 — most of this is still a plan, not a deployment.**
`tools/check_live.sh` asserts each one against the live site:

| Header | State |
|---|---|
| `X-Content-Type-Options` | applied |
| `Strict-Transport-Security` | applied, but `max-age=15552000` (180 d) while carrying `preload` — not submittable, see above |
| `Referrer-Policy` | **absent** — only the meta tag exists |
| `Cross-Origin-Opener-Policy` | **absent** |
| `Cross-Origin-Embedder-Policy` | **absent** |
| `Cross-Origin-Resource-Policy` | **absent** |
| `Content-Security-Policy` | applied as `frame-ancestors 'none'` since 1.8.0 — framing is now actually blocked |

The A+ is real but rests on what Observatory reads from `index.html`: the meta
CSP and the meta referrer. The header set below is what would make the policy
actually enforced rather than merely scored.

Rules → Transform Rules → **Modify Response Header**, applied to all requests.

| Header | Value |
|---|---|
| `Strict-Transport-Security` | `max-age=300` → `86400` → `31536000; includeSubDomains; preload` |
| `X-Content-Type-Options` | `nosniff` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `Cross-Origin-Opener-Policy` | `same-origin` |
| `Cross-Origin-Embedder-Policy` | `credentialless` |
| `Cross-Origin-Resource-Policy` | `same-origin` |
| `Content-Security-Policy` | `frame-ancestors 'none'` — **only** that directive |

### HSTS, rolled out in stages

Per [hstspreload.org](https://hstspreload.org/), ramp the max-age and confirm
nothing breaks at each step before going further. Preload is effectively
irreversible — browsers ship the list in their binaries, and removal takes
months.

1. `max-age=300` (5 min) — one day
2. `max-age=86400` (1 day) — one week
3. `max-age=31536000; includeSubDomains` — one week
4. add `; preload`, then submit the domain

`includeSubDomains` covers every subdomain of `mumu.solutions`. Confirm none of
them need plain HTTP before step 3.

### COEP is the one that can break things

`require-corp` demands that every cross-origin subresource opt in via CORP or
CORS. This page loads only same-origin assets today, so either value is safe —
but Cloudflare Web Analytics is cross-origin, and the privacy section says it
runs. **`credentialless` is the safer default**; it does not require the remote
end to opt in. Verify in the console after enabling.

### CSP at the edge, if you duplicate it

Two enforced CSPs **intersect** — a resource must satisfy both. That is why the
header carries `frame-ancestors` and nothing else: it covers the one directive a
meta tag cannot, while the meta keeps the rest. Duplicating the whole policy
would mean any future divergence between the two becomes a silent block, and
the error pages run a stricter policy of their own.

## Reachable score

| Change | Where | Points |
|---|---|---|
| CSP | repo (done) | −25 → −5 or better |
| Referrer-Policy | repo (done) | 0 → +5 |
| `frame-ancestors` in CSP | repo (done) | −20 → 0 |
| HTTP→HTTPS redirect | Cloudflare | −20 → 0 |
| HSTS | Cloudflare | −20 → 0 |
| `nosniff` | Cloudflare | −5 → 0 |
| COOP / COEP / CORP | Cloudflare | bonus |

**The repo changes alone cannot reach A+.** They lift the score meaningfully,
but HSTS, the redirect and `nosniff` are worth 45 points between them and none
can be expressed in HTML. A+ requires the Cloudflare work.

## Cache lifetimes

`Cache-Control` is a response header, so like everything else in this file it
is set in Cloudflare and no pull request can change or verify it. It is here
because getting it wrong is silent in both directions: too short wastes
bandwidth on every repeat visit, too long serves a stale site with no way to
recall it.

The rule that decides every row below: **if the filename does not change when
the content changes, the Browser TTL must be short.** Edge TTL is purgeable;
the visitor's browser is not.

Cache Rules, in this order — the first match wins:

| # | Matches | Browser TTL | Edge TTL |
|--:|---|---|---|
| 1 | `/`, `*.html` | no-store | 30 s – 5 min |
| 2 | `/robots.txt`, `/ads.txt`, `/sitemap.xml`, `/llms.txt` | 5 min | 5 min |
| 3 | `/fonts/*`, `/images/*` | 1 year | 1 year |
| 4 | `*.css`, `*.js` | 1 year | 1 year |
| 5 | everything else | 5 min | 5 min |

Row 4 is only safe because of `?v=<VERSION>`. `error.css` and `error.js` carry
no hash in their names, so `tools/check_version.py --sync` stamps the version
into every reference and the deploy fails if a stamp is stale. Remove the
stamping and row 4 becomes a trap: visitors keep the old stylesheet for a year.

Row 1 is the one that bites during development. HTML at a 2-hour TTL is why a
published change appeared to be missing — the origin had it and the edge did
not. If a publish looks like it did nothing, check `cf-cache-status` before
checking the deploy:

```bash
curl -sI https://mumu.solutions/ | grep -iE 'cf-cache-status|age|cache-control'
curl -s "https://mumu.solutions/?bust=$RANDOM" | grep -o 'name="version" content="[^"]*"'
```

The second command bypasses the edge copy, because the query string is part of
the cache key. If the two disagree, it is cache, not a failed deploy.

## The CSP's inline-script hashes

`script-src` pins a sha256 per inline `<script>`. Edit one and the browser
refuses to run it — silently; the page just loses its theme toggle and language
switcher. `tools/check_csp_hashes.py` recomputes them and the deploy workflow
fails on drift.

```bash
python3 tools/check_csp_hashes.py         # verify
python3 tools/check_csp_hashes.py --fix   # rewrite after editing a script
```

`style-src` uses `'unsafe-inline'` deliberately. The stylesheet is 23 KB edited
by hand months apart; a stale style hash unstyles the entire page, which is worse
and likelier than the injection it would prevent. A hash would also *disable*
`'unsafe-inline'` — CSP ignores it whenever a hash or nonce is present — and
break the two `style="margin-top:0"` attributes, which hashes cannot cover.

## Re-check

```bash
curl -sI https://mumu.solutions/ | grep -iE 'strict-transport|content-security|referrer|x-content-type|cross-origin'
curl -sS -X POST "https://observatory-api.mdn.mozilla.net/api/v2/scan?host=mumu.solutions"
curl -sS "https://observatory-api.mdn.mozilla.net/api/v2/analyze?host=mumu.solutions"
```
