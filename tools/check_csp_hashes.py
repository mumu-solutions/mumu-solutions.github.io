#!/usr/bin/env python3
"""Keep the CSP hashes in sync with the inline code they cover.

A stale hash does not fail loudly. The browser refuses the block and the page
quietly loses its theme toggle, its language switcher, or — for style-src — its
entire appearance. On a site edited by hand months apart with no build step,
nothing else would catch it, so the deploy runs this and stops on drift.

    python3 tools/check_csp_hashes.py           # verify, exit 1 on drift
    python3 tools/check_csp_hashes.py --fix     # rewrite the CSP with real hashes

Covers both directives:

  * script-src — every inline <script> that executes.
  * style-src  — the inline <style> block.

Deliberately skipped:

  * <script src=...> and <link rel=stylesheet> are fetched, not inline; 'self'
    covers them.
  * <script type="application/ld+json"> is a data block. It is never executed,
    so CSP has nothing to block, and hashing it would force a hash update on
    every edit to the structured data for no security gain.

Scanning uses html.parser rather than a regular expression. That is not a style
preference — see _InlineCollector for the three separate ways the regex version
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

HASHED = ("script", "style")


class _InlineCollector(HTMLParser):
    """Collect (tag, attributes, body) for every inline <script> and <style>.

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
    one of these cases, including treating script and style content as raw CDATA.
    """

    def __init__(self) -> None:
        # convert_charrefs must stay False: the hash has to cover the bytes the
        # browser hashes, and entity conversion would silently rewrite them.
        super().__init__(convert_charrefs=False)
        self.blocks: list[tuple[str, dict[str, str], str]] = []
        self._tag: str | None = None
        self._attrs: dict[str, str] = {}
        self._buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in HASHED:
            self._tag = tag
            self._attrs = {k.lower(): (v or "") for k, v in attrs}
            self._buf = []

    def handle_data(self, data):
        if self._tag is not None:
            self._buf.append(data)

    def handle_endtag(self, tag):
        if tag == self._tag:
            self.blocks.append((tag, self._attrs, "".join(self._buf)))
            self._tag = None


def _sha256(body: str) -> str:
    return base64.b64encode(hashlib.sha256(body.encode()).digest()).decode()


def inline_hashes(html: str) -> dict[str, list[str]]:
    """{'script-src': [...], 'style-src': [...]} in document order."""
    p = _InlineCollector()
    p.feed(html)
    p.close()
    out: dict[str, list[str]] = {"script-src": [], "style-src": []}
    for tag, attrs, body in p.blocks:
        if "src" in attrs:
            continue
        if tag == "script":
            if "ld+json" in attrs.get("type", "").lower():
                continue
            out["script-src"].append(_sha256(body))
        else:
            out["style-src"].append(_sha256(body))
    return out


def _declared(csp: str, directive: str) -> list[str]:
    m = re.search(rf"(?:^|;)\s*{directive}\s([^;]*)", csp)
    return re.findall(r"'sha256-([^']*)'", m.group(1)) if m else []


def _rewrite(csp: str, directive: str, hashes: list[str]) -> str:
    """Replace the hash tokens inside one directive, leaving the rest intact."""

    def repl(m):
        body = re.sub(r"\s*'sha256-[^']*'", "", m.group(1))
        joined = "".join(f" 'sha256-{h}'" for h in hashes)
        # keep hashes next to 'self' so the directive reads predictably
        if "'self'" in body:
            return f"{directive} " + body.replace("'self'", "'self'" + joined, 1)
        return f"{directive} " + body.rstrip() + joined

    return re.sub(rf"(?<=[;^])?\b{directive}\s([^;]*)", repl, csp, count=1)


def main() -> int:
    fix = "--fix" in sys.argv
    html = PAGE.read_text(encoding="utf-8")

    m = CSP_RE.search(html)
    if not m:
        print("FAIL: no Content-Security-Policy meta tag found in index.html")
        return 1

    csp = m.group(2)
    actual = inline_hashes(html)
    drift = False

    for directive, found in actual.items():
        declared = _declared(csp, directive)
        if declared == found:
            print(f"OK: {directive} — {len(found)} hash(es) match")
            continue
        drift = True
        print(f"\n{directive} hashes are out of date.")
        print(f"  inline blocks found : {len(found)}")
        print(f"  hashes in the CSP   : {len(declared)}")
        for h in found:
            if h not in declared:
                print(f"  missing from CSP    : sha256-{h}")
        for h in declared:
            if h not in found:
                print(f"  stale, no such block: sha256-{h}")

    if not drift:
        return 0
    if not fix:
        print("\nRun:  python3 tools/check_csp_hashes.py --fix")
        return 1

    new = csp
    for directive, found in actual.items():
        new = _rewrite(new, directive, found)
    PAGE.write_text(html[: m.start(2)] + new + html[m.end(2) :], encoding="utf-8")
    print("\nRewrote the CSP.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
