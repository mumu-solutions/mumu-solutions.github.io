#!/usr/bin/env python3
"""Fail the deploy if index.html drops below the security posture it earned.

The site scores A+ on MDN Observatory. Most of that is easy to lose by accident
and hard to notice: one style="..." attribute, one CDN <script>, one 'unsafe-inline'
added to make something work. None of those break the page, so nothing else would
catch them — the grade would just quietly fall and stay fallen.

This checks only what lives in this repository. The response headers
(HSTS, nosniff, COOP/COEP/CORP) are set in Cloudflare and cannot be asserted from
here; .github/workflows/observatory.yml watches those by re-scanning the live site.

    python3 tools/check_security.py        # exit 1 on any violation

Run by .github/workflows/static.yml before the artifact is uploaded.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_csp_hashes import CSP_RE, inline_hashes, _declared  # noqa: E402

PAGE = Path(__file__).resolve().parent.parent / "index.html"

# Hosts index.html may reference for an executable or style resource. Anything
# else is a new third party and should be a deliberate, reviewed decision — not
# something that slips in with a copy-pasted snippet.
#
# The site's own canonical host counts as self and is read from CNAME rather than
# hardcoded, so this keeps working if the domain ever moves.
_CNAME = Path(__file__).resolve().parent.parent / "CNAME"
_SELF_HOST = _CNAME.read_text(encoding="utf-8").strip() if _CNAME.exists() else ""
ALLOWED_RESOURCE_HOSTS = {
    "static.cloudflareinsights.com",
    "cloudflareinsights.com",
    # AdSense loader. Reviewed and accepted: the host is named in the CSP and
    # described in the privacy dialog, and it cannot be self-hosted because
    # Google generates the file per request. Note this entry admits the loader
    # only — the hosts that actually draw ads (googleads.g.doubleclick.net,
    # tpc.googlesyndication.com) are deliberately absent, and so are frame-src
    # and a wider img-src. Adding them is what makes ads render, and it is the
    # decision this list exists to slow down.
    "pagead2.googlesyndication.com",
} | ({_SELF_HOST} if _SELF_HOST else set())

# Referrer values that are at least as private as what Observatory rewards.
ACCEPTABLE_REFERRER = {
    "no-referrer",
    "same-origin",
    "strict-origin",
    "strict-origin-when-cross-origin",
}

failures: list[str] = []
notes: list[str] = []


def require(ok: bool, label: str, detail: str = "") -> None:
    if ok:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}" + (f" — {detail}" if detail else ""))
        failures.append(label)


def main() -> int:
    html = PAGE.read_text(encoding="utf-8")

    m = CSP_RE.search(html)
    if not m:
        print("  FAIL  a Content-Security-Policy meta tag exists")
        return 1
    csp = m.group(2)
    directives = {
        d.strip().split()[0]: d.strip()
        for d in csp.split(";")
        if d.strip()
    }

    print("Content-Security-Policy")
    require("default-src" in directives and directives["default-src"] == "default-src 'none'",
            "default-src is 'none'", directives.get("default-src", "absent"))
    require("'unsafe-inline'" not in csp, "no 'unsafe-inline' anywhere")
    require("'unsafe-eval'" not in csp, "no 'unsafe-eval' anywhere")
    require("*" not in re.sub(r"'[^']*'", "", csp), "no wildcard source")
    for d in ("base-uri", "object-src", "form-action", "frame-ancestors",
              "script-src", "style-src", "img-src", "font-src", "connect-src"):
        require(d in directives, f"{d} is declared")
    require(directives.get("object-src") == "object-src 'none'", "object-src is 'none'")
    require(directives.get("frame-ancestors") == "frame-ancestors 'none'",
            "frame-ancestors is 'none'")

    print("\nInline hashes")
    for directive, found in inline_hashes(html).items():
        declared = _declared(csp, directive)
        require(declared == found, f"{directive} hashes match the inline blocks",
                f"{len(found)} block(s), {len(declared)} hash(es) — run check_csp_hashes.py --fix")

    print("\nMarkup")
    # A style attribute cannot be hashed; allowing it would mean re-opening
    # style-src with 'unsafe-inline', which costs the CSP its top score.
    style_attrs = re.findall(r"<[a-zA-Z][^>]*\sstyle\s*=", html)
    require(not style_attrs, "no style=\"...\" attributes",
            f"{len(style_attrs)} found — use a class instead")
    handlers = re.findall(r"\son[a-z]+\s*=\s*[\"']", html)
    require(not handlers, "no inline event handlers",
            f"{len(handlers)} found — addEventListener instead")
    require("javascript:" not in html, "no javascript: URLs")

    print("\nReferrer-Policy")
    r = re.search(r'<meta\s+name="referrer"\s+content="([^"]*)"', html, re.I)
    require(r is not None, "a referrer meta tag exists")
    if r:
        require(r.group(1).strip().lower() in ACCEPTABLE_REFERRER,
                "referrer value is private enough", r.group(1))

    print("\nThird-party resources")
    hosts = set(re.findall(r'(?:src|href)="https?://([^/"]+)', html))
    # Only script/style/font/img hosts matter for CSP; links to other sites are fine.
    res_hosts = set(re.findall(r'<(?:script|link)[^>]*(?:src|href)="https?://([^/"]+)', html))
    unexpected = res_hosts - ALLOWED_RESOURCE_HOSTS
    require(not unexpected, "no unreviewed third-party resource hosts",
            ", ".join(sorted(unexpected)))
    notes.append(f"hosts referenced anywhere (links included): {', '.join(sorted(hosts)) or 'none'}")

    print()
    for n in notes:
        print(f"  note: {n}")
    if failures:
        print(f"\n{len(failures)} requirement(s) failed:")
        for f in failures:
            print(f"  - {f}")
        print("\nSee SECURITY-HEADERS.md for why each of these is required.")
        return 1
    print("\nAll security requirements met.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
