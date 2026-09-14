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

Scanning uses html.parser rather than a regular expression. That is not a style
preference — see _ScriptCollector for the three separate ways the regex version
was wrong, starting with index.html's own header comment, which contains the
literal text "<script>" and was hashed as if it were code.
"""

import base64
import hashlib
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

PAGE = Path(__file__).resolve().parent.parent / "index.html"

# Locating the CSP meta tag so it can be rewritten in place. This one stays a
# regex because it needs the exact character span to splice, and it is matched
# against a tag this repository writes and owns — it is not filtering untrusted
# markup.
CSP_RE = re.compile(
    r'(<meta\s+http-equiv="Content-Security-Policy"\s+content=")([^"]*)(">)', re.I
)


class _ScriptCollector(HTMLParser):
    """Collect (attributes, body) for every <script> element, in document order.

    This used to be a regex, and it was wrong three times in a row — each caught
    by CodeQL's py/bad-tag-filter, each a different edge of the same mistake:

      1. "<script>" appearing inside an HTML comment was matched as an element,
         swallowing everything up to the first real "</script>".
      2. "<SCRIPT>" was skipped, because HTML tag names are case-insensitive and
         the pattern was not.
      3. "</script >" did not terminate the element, so the match ran on into the
         next script.

    A fourth was waiting — "</script foo>" also closes the element, since end
    tags may carry (ignored) attributes. The lesson is not that the regex needed
    a fourth patch; it is that matching HTML with regular expressions cannot be
    made correct. The standard library already has a parser that handles every
    one of these cases, including treating script content as raw CDATA.
    """

    def __init__(self) -> None:
        # convert_charrefs must stay False: the hash has to cover the bytes the
        # browser hashes, and entity conversion would silently rewrite them.
        super().__init__(convert_charrefs=False)
        self.blocks: list[tuple[dict[str, str], str]] = []
        self._attrs: dict[str, str] | None = None
        self._buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self._attrs = {k.lower(): (v or "") for k, v in attrs}
            self._buf = []

    def handle_data(self, data):
        if self._attrs is not None:
            self._buf.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self._attrs is not None:
            self.blocks.append((self._attrs, "".join(self._buf)))
            self._attrs = None


def inline_script_hashes(html: str) -> list[str]:
    """sha256 of every inline <script> the browser will execute, in document order."""
    p = _ScriptCollector()
    p.feed(html)
    p.close()
    out = []
    for attrs, body in p.blocks:
        if "src" in attrs or "ld+json" in attrs.get("type", "").lower():
            continue
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
