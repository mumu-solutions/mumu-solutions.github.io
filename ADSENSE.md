# AdSense: what is live, what is deliberately not

Companion to `SECURITY-HEADERS.md`. That file covers the surfaces this
repository cannot assert because they live in Cloudflare; this one covers the
surfaces it cannot assert because they live in the AdSense dashboard — plus the
one decision in `index.html` that looks like a mistake until you read why.

Publisher ID: `ca-pub-1351604242843112`. Canonical host: the apex,
`mumu.solutions` (see `CNAME`).

## In the repository

| Thing | Where | What it answers |
|---|---|---|
| Loader `<script>` | `index.html` `<head>` | — |
| `google-adsense-account` meta | `index.html` `<head>` | does this domain belong to the publisher account |
| `ads.txt` | repo root | who is allowed to sell this domain's inventory |
| `Allow: /ads.txt` | `robots.txt` | lets a crawler actually read the above |

The meta tag and `ads.txt` are not duplicates. The meta proves ownership to
Google; `ads.txt` proves authorisation to ad buyers, who have no other way to
tell a genuine seller from one claiming inventory that is not theirs. Both must
stay: removing the meta can un-verify the domain, and an unreachable `ads.txt`
has AdSense report "Earnings at risk".

`robots.txt` needed that explicit `Allow` because its `User-agent: *` group is
an allowlist ending in `Disallow: /`. Any new root file is hidden by default —
`/ads.txt` would have returned 200 to a browser and been invisible to the
crawler that matters. The same is true of the next root file somebody adds.

## No ads render, on purpose

The CSP permits `pagead2.googlesyndication.com` on `script-src` and nothing
more. That is enough for the loader to download and not enough to draw an ad:

- ads are drawn in **iframes** from `googleads.g.doubleclick.net` and
  `tpc.googlesyndication.com`. `frame-src` is absent, so `default-src 'none'`
  denies them.
- creatives are **images** from Google hosts. `img-src` is `'self'`.
- Google's ad code also wants `'unsafe-inline'` and `'unsafe-eval'`, which
  `tools/check_security.py` asserts against outright.

Opening `frame-src` and `img-src` is what makes ads appear, and it costs the
site its A+ on MDN Observatory. Whoever takes that decision must also relax
`tools/check_security.py` — which is the entire point of that gate — and should
expect `.github/workflows/observatory.yml` to open a rolling issue when the live
grade falls below `.github/observatory-floor`.

Two constraints for that edit, both easy to trip:

- `check_security.py` rejects any wildcard not in `ALLOWED_WILDCARD_SOURCES`,
  which is now empty. So `*.googlesyndication.com` fails the gate: enumerate
  the ad hosts literally, or argue the case for an entry.
- every new host also needs an entry in `ALLOWED_RESOURCE_HOSTS`, or the gate
  rejects it as an unreviewed third party.

Take the host list from Google's published CSP guidance rather than
reconstructing it. A missing host blocks ads with no error visible locally.

## sellers.json is not ours to host

A recurring confusion, so it is written down. `ads.txt` and `sellers.json` are
two halves of one chain:

- **`ads.txt`** is published by the *publisher* and names the exchanges allowed
  to sell its inventory. That is the file in this repository.
- **`sellers.json`** is published by the *exchange* and names the publishers it
  sells for. Google's lives at `realtimebidding.google.com/sellers.json`.

Only a seller of type `INTERMEDIARY` hosts its own. MUMU Solutions sells only
its own inventory — seller type `PUBLISHER`, which is also what the `DIRECT`
relationship in `ads.txt` declares — so **there is no `sellers.json` to create
here**. A file at `mumu.solutions/sellers.json` would be inert; no buyer reads
that path.

Measured against the live file on 2026-09-21: 971,604 seller entries, of which
688,634 are confidential. Confidential sellers still appear, as
`{"seller_id": ..., "is_confidential": 1, "seller_type": "PUBLISHER"}` — so an
absent `seller_id` means *not listed*, not *hidden*. `pub-1351604242843112` was
absent, consistent with an account not yet approved and serving.

Once approved, with **Seller information visibility → Transparent**, the entry
should read:

```json
{ "seller_id": "pub-1351604242843112", "seller_type": "PUBLISHER",
  "name": "MUMU SOLUTIONS", "domain": "mumu.solutions" }
```

`name` is Google's copy of the account profile name, not something this repo
sets — the trade name is the wanted value, not the razão social, because it is
what matches the wordmark, `og:site_name` and the JSON-LD `Organization`. The
setting publishes name and domain only: never address, CNPJ or phone.

```bash
curl -sL https://realtimebidding.google.com/sellers.json \
| python3 -c 'import json,sys; s=json.load(sys.stdin)["sellers"]; \
h=[x for x in s if x["seller_id"]=="pub-1351604242843112"]; print(h or "not listed yet")'
```

## The error pages carry the meta, not the loader

`404.html`, `error-401.html`, `error-403.html` and `error-500.html` each carry
`<meta name="google-adsense-account">` — it proves the domain and serves no ad,
so there is no reason for it to be absent. None of them carries the loader
`<script>`, and that is deliberate. Google Publisher Policies, under Inventory
Value:

> We do not allow Google-served ads on screens: without publisher-content or
> with low-value content, that are under construction, that are used for
> alerts, navigation or other behavioral purposes

An error page is a screen used for alerts and navigation, with no publisher
content. There is a second, independent reason: the ad placement policies note
that Google "may disable ad serving on content that cannot be evaluated",
naming pages blocked by `robots.txt` — and every error page is blocked by this
site's blanket `Disallow: /`.

Today the CSP would block ad rendering on those pages anyway, so nothing is
visibly wrong. The risk is later: whoever opens `frame-src` to make ads render
on `index.html` would silently switch them on here too, if the loader were
present. Keeping it off the error pages means that decision cannot leak into a
policy breach by accident.

The error pages' `script-src` is a bare `'self'` and they have no `connect-src`
at all, so nothing on them reaches off-origin. Adding the loader means widening
the CSP on four more files first, which is the speed bump this note is meant to
be.

## Still open

1. **Site approval** in AdSense. Precedes everything; ad units serve blanks
   until it lands.
2. **Seller information visibility → Transparent**, business name
   `MUMU SOLUTIONS`, business domain `mumu.solutions`. If verification asks for
   proof, the ownership meta and `/ads.txt` are both already live on the apex.
3. **A certified consent platform** — `todo.txt`, "Politica de cookies". Google
   requires one before ads may be served to EEA/UK visitors.
4. **The CSP decision** above, then **ad units or Auto ads**. There are
   currently zero `<ins class="adsbygoogle">` slots.

The privacy dialog in `index.html` states that no ads appear yet and why. When
that stops being true, both the `pt` and `en` siblings have to change in the
same edit, or the notice becomes a false claim about tracking — which is the
one kind of error on this page that is worse than a broken layout.

## Verify

```bash
curl -sIL https://mumu.solutions/ads.txt | grep -iE '^HTTP|^content-type'
curl -sL  https://mumu.solutions/ | grep -o 'name="version" content="[^"]*"'
curl -sL  https://mumu.solutions/robots.txt | grep -i 'ads.txt'
```

`ads.txt` must return 200 as `text/plain`. AdSense re-crawls it on its own
schedule, typically a day or more — a warning that persists overnight is
propagation, not a broken file.
