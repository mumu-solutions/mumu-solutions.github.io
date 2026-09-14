#!/usr/bin/env python3
"""Keep the CSP script-src hashes in sync with the inline scripts they cover.

A stale hash does not fail loudly. The browser refuses to run the script and the
page quietly loses its theme toggle, its language switcher, or both — on a site
that is edited by hand months apart, with no build step to catch it. So the
deploy runs this and stops if they have drifted.

    python3 tools/check_csp_hashes.py           # verify, exit 1 on drift
    python3 tools/check_csp_hashes.py --fix     # rewrite the CSP with real hashes

Only inline <script> blocks that actually execute are hashed. Two exclusions,
both deliberate:

  * <script src=...> is fetched, not inline; 'self' covers it.
  * <script type="application/ld+json"> is a data block. It is never executed,
    so CSP has nothing to block, and hashing it would mean every edit to the
    structured data also needed a hash update for no security gain.

HTML comments are stripped before scanning. This is not hypothetical tidiness:
index.html documents its own structure with the literal text "<script>" inside a
comment, and a regex that misses that will hash the comment plus everything up
to the first real </script> — producing a hash that matches nothing and a CSP
that blocks the real script.
"""

import base64
import hashlib
import re
import sys
from pathlib import Path

PAGE = Path(__file__).resolve().parent.parent / "index.html"

# Every pattern here is case-insensitive, because HTML tag and attribute names
# are. <SCRIPT> and <Script> are the same element to a browser, so a
# case-sensitive scan would skip one, leave it unhashed, and the CSP would then
# block a script this tool just told you was fine. CodeQL flags exactly this as
# "bad HTML filtering regexp" and it is right to.
CSP_RE = re.compile(
    r'(<meta\s+http-equiv="Content-Security-Policy"\s+content=")([^"]*)(">)', re.I
)
SCRIPT_RE = re.compile(r"<script([^>]*)>(.*?)</script>", re.S | re.I)
COMMENT_RE = re.compile(r"<!--.*?-->", re.S)


def inline_script_hashes(html: str) -> list[str]:
    """sha256 of every inline <script> the browser will execute, in document order."""
    without_comments = COMMENT_RE.sub("", html)
    out = []
    for m in SCRIPT_RE.finditer(without_comments):
        attrs, body = m.group(1).lower(), m.group(2)
        if "src=" in attrs or "ld+json" in attrs:
            continue
        # body is hashed as written — only the attributes are case-folded
        out.append(base64.b64encode(hashlib.sha256(body.encode()).digest()).decode())
    return out


def main() -> int:
    fix = "--fix" in sys.argv
    html = PAGE.read_text(encoding="utf-8")

    m = CSP_RE.search(html)
    if not m:
        print("FAIL: no Content-Security-Policy meta tag found in index.html")
        return 1

    csp = m.group(2)
    actual = inline_script_hashes(html)
    declared = re.findall(r"'sha256-([^']*)'", csp)

    if declared == actual:
        print(f"OK: {len(actual)} inline script hash(es) match the CSP")
        return 0

    print("CSP script-src hashes are out of date.")
    print(f"  inline scripts found : {len(actual)}")
    print(f"  hashes in the CSP    : {len(declared)}")
    for h in actual:
        if h not in declared:
            print(f"  missing from CSP     : sha256-{h}")
    for h in declared:
        if h not in actual:
            print(f"  stale, no such script: sha256-{h}")

    if not fix:
        print("\nRun:  python3 tools/check_csp_hashes.py --fix")
        return 1

    # Replace the whole hash list in place, preserving the order of the rest of
    # script-src so 'self' and the cloudflareinsights host survive untouched.
    new_src = re.sub(r"\s*'sha256-[^']*'", "", csp)
    joined = " ".join(f"'sha256-{h}'" for h in actual)
    new_src = re.sub(r"(script-src 'self')", rf"\1 {joined}", new_src)
    PAGE.write_text(html[: m.start(2)] + new_src + html[m.end(2) :], encoding="utf-8")
    print(f"\nRewrote the CSP with {len(actual)} hash(es).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
