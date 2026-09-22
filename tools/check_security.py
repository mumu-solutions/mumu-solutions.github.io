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
    # No ad host. pagead2.googlesyndication.com was here while the AdSense
    # loader shipped; it was removed in 1.7.0 and the allowance went with it,
    # so the gate rejects it again. Putting the loader back means adding the
    # host here in the same commit — see ADSENSE.md.
} | ({_SELF_HOST} if _SELF_HOST else set())

# Sources that may contain a "*", and nothing else may.
#
# A bare "*" or a scheme-only source ("https:", "data:") lets in anything at
# all, and refusing those is the entire point of the check below. A wildcard in
# the leftmost label of a named host is a different animal: it cannot widen
# past that one registrable domain.
#
# This set is EMPTY, and that is the desired state. It briefly held
# https://*.google-analytics.com, because GA4 builds its collect host per
# visitor from a regional subdomain that cannot be enumerated. Google Analytics
# was removed in 1.5.0 and the entry went with it — an allowance kept after the
# thing it allowed is gone is how a policy quietly rots.
#
# Adding to this set widens the policy. Do it only with the same kind of
# evidence GA had: the host genuinely cannot be enumerated.
ALLOWED_WILDCARD_SOURCES: set[str] = set()

# Only "https://*.label.tld" qualifies. "*", "*.com", "https://*" and
# "https://example.*" all fail this and are rejected regardless of the set.
WILDCARD_FORM = re.compile(r"^https://\*\.[a-z0-9-]+(?:\.[a-z0-9-]+)+$")

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
    # Every source carrying a "*" must be both well-formed as a subdomain
    # wildcard and explicitly reviewed. "script-src *" and "script-src https:"
    # still fail here: the first is not in the set and does not match the form,
    # the second is caught by the scheme-only check that follows.
    wild = [src
            for directive in directives.values()
            for src in directive.split()[1:]
            if "*" in src
            and not (WILDCARD_FORM.match(src) and src in ALLOWED_WILDCARD_SOURCES)]
    require(not wild, "no unreviewed wildcard source", ", ".join(wild))

    # A scheme-only source ("https:", "data:", "blob:") is as permissive as a
    # bare wildcard and the old check never looked for one.
    scheme_only = [src
                   for directive in directives.values()
                   for src in directive.split()[1:]
                   if re.fullmatch(r"[a-z][a-z0-9+.-]*:", src)]
    require(not scheme_only, "no scheme-only source", ", ".join(scheme_only))
    # frame-ancestors is deliberately NOT in this list. Browsers ignore it in a
    # meta CSP, so asserting it here only ever proved the string was present.
    # It moved to the Cloudflare response header in 1.8.0, where it actually
    # blocks framing — and the assertion moved with it, to
    # tools/check_live.sh, which reads the live header. A rule is only worth
    # keeping next to the thing it can actually observe.
    for d in ("base-uri", "object-src", "form-action",
              "script-src", "style-src", "img-src", "font-src", "connect-src"):
        require(d in directives, f"{d} is declared")
    require(directives.get("object-src") == "object-src 'none'", "object-src is 'none'")
    require("frame-ancestors" not in csp,
            "no frame-ancestors in the meta CSP (it belongs in the header)",
            "browsers ignore it here; check_live.sh asserts the real one")

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
