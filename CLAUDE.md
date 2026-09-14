# mumu.solutions

Institutional single-page site. Static HTML/CSS/JS, no build step, no
dependencies. GitHub Pages serves the repository as-is, behind Cloudflare.

Everything visitors see is `index.html`: `<style>` in the head, markup, `<script>`
before `</body>`. That is deliberate — the page never breaks because a toolchain
moved on. Do not introduce npm, a bundler or a framework.

## Before you edit

```bash
python3 ~/.claude/skills/static-site-audit/scripts/audit_site.py .   # site invariants
python3 tools/check_security.py                                      # security requirements
```

Both must pass before and after. The second one also runs on every deploy and
**will block it**.

## Security requirements

The site scores **A+** on MDN Observatory. Most of that is easy to lose by
accident and impossible to notice by eye — none of these mistakes break the page,
they just silently cost the grade. `tools/check_security.py` enforces them.

**Never add any of these to `index.html`:**

| Don't | Why | Instead |
|---|---|---|
| `style="..."` attributes | Cannot be covered by a CSP hash. Allowing them means re-opening `style-src` with `'unsafe-inline'`, which drops CSP from +10 to 0 | Add a class |
| `onclick=` and friends | Same reason, for `script-src` | `addEventListener` |
| `javascript:` URLs | Blocked by the CSP; the link silently does nothing | A real handler |
| A `<script src>` from a CDN | New third party, and the gate rejects unknown hosts | Self-host it under `/` |
| `'unsafe-inline'` / `'unsafe-eval'` | Costs the CSP its top score | Hash the block (below) |

**After editing any `<script>` or the `<style>` block**, their hashes change and
the browser will refuse them — for `style-src` that means the page renders
completely unstyled. Regenerate:

```bash
python3 tools/check_csp_hashes.py --fix
```

Never hand-edit a `sha256-` value.

`frame-ancestors 'none'` is in the CSP but **browsers ignore it in a `<meta>`
tag** — this was tested, and the live site is currently framable. It is kept
because Observatory reads it. Real clickjacking protection needs a response
header; see `SECURITY-HEADERS.md`.

## Site invariants

Four pairs that can silently desync. The auditor checks all four.

| Invariant | Breaks as |
|---|---|
| Every visible string exists as both `data-lang="pt"` and `data-lang="en"` siblings | Text vanishes when the visitor switches language |
| Every CSS token is declared in both `:root` and `html[data-theme="light"]` | One theme renders with the other's colours |
| Every nav `href="#x"` has a matching `<section id="x">` | Nav link scrolls nowhere |
| Every major section appears in `sitemap.xml` | Section is invisible to search |

Change both language siblings in the same edit. "I'll come back for the
translation" is how the site ends up half-translated in public.

## What is not in this repository

Response headers — HSTS, `X-Content-Type-Options`, COOP/COEP/CORP, the
HTTP→HTTPS redirect — are Cloudflare settings. No pull request can change or
verify them, so `.github/workflows/observatory.yml` re-scans the live site weekly
and opens a rolling issue if the grade falls below `.github/observatory-floor`.

Cloudflare also injects into responses: a managed `robots.txt` block (AI-crawler
rules and content signals) and an inline bot-detection script that the CSP blocks
and **cannot** allow, because its body carries a per-request token. Both are
dashboard settings, documented in `SECURITY-HEADERS.md`.

## Layout

```
index.html          the site
llms.txt            summary for AI crawlers; keep in step with #products
sitemap.xml         canonical URL + section fragments; bump lastmod on change
robots.txt          crawl rules (Cloudflare prepends its own block at the edge)
CNAME               custom domain, one line
SECURITY-HEADERS.md the Cloudflare side and why each header is there
tools/              check_security.py, check_csp_hashes.py, observatory_report.py,
                    brand_drift.py, brand_watch.sh
*.local.html        scratch pages — gitignored, never published
```

Everything committed here is served: `upload-pages-artifact` uploads the whole
repository. So a scratch page built to look at rather than to publish gets the
`.local.html` suffix and stays untracked. Name it that way and it cannot reach
the site by accident.

## Products

Cards in `#products` carry a kicker (category), a status chip and a bilingual
description. Status is shown on the page only — `llms.txt` and the JSON-LD
deliberately describe what each product *does*, never whether it is running, so
they cannot drift when a chip changes.

Product descriptions in the JSON-LD `ItemList` are **verbatim** copies of the
visible `pt` card text. Structured data that contradicts the page is a
manual-action risk; keeping them identical makes drift detectable by comparison
rather than judgement.
