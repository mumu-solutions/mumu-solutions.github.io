#!/usr/bin/env python3
"""Keep VERSION and the version meta tag in index.html in agreement.

The version exists in two places on purpose: VERSION is the source of truth and
what a release is tagged from, while the meta tag is what you can read off the
live site without guessing from a commit hash. Two copies of anything drift, so
this is the guard — the same reason tools/check_csp_hashes.py exists.

    python3 tools/check_version.py           # verify, exit 1 on drift
    python3 tools/check_version.py --sync     # copy VERSION into index.html
    python3 tools/check_version.py --bump minor   # raise VERSION, then sync

What the parts mean for this site — the rule is the public contract, not the
size of the diff:

    MAJOR  a <section id> is renamed or removed. Those anchors are referenced by
           sitemap.xml, llms.txt, the nav, and by inbound links nobody controls.
           Breaking one breaks somebody else's bookmark.
    MINOR  a new section, a new product card, a new capability. Additive.
    PATCH  copy, styling, metadata, tooling. Nothing a visitor could have linked
           to changes shape.

A redesign that touches every rule but keeps all seven anchors is a MINOR. A
one-line edit renaming #about to #sobre is a MAJOR. That asymmetry is the point.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "VERSION"
PAGE = ROOT / "index.html"

SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
META = re.compile(r'(<meta\s+name="version"\s+content=")([^"]*)(">)', re.I)


def read_version() -> str:
    if not VERSION_FILE.exists():
        print("FAIL: no VERSION file")
        raise SystemExit(1)
    v = VERSION_FILE.read_text(encoding="utf-8").strip()
    if not SEMVER.match(v):
        print(f"FAIL: VERSION is {v!r}, which is not MAJOR.MINOR.PATCH")
        raise SystemExit(1)
    return v


def main() -> int:
    args = sys.argv[1:]
    version = read_version()

    if "--bump" in args:
        try:
            part = args[args.index("--bump") + 1]
        except IndexError:
            print("FAIL: --bump needs one of major, minor, patch")
            return 1
        if part not in ("major", "minor", "patch"):
            print(f"FAIL: --bump {part!r}; expected major, minor or patch")
            return 1
        major, minor, patch = (int(x) for x in SEMVER.match(version).groups())
        if part == "major":
            major, minor, patch = major + 1, 0, 0
        elif part == "minor":
            minor, patch = minor + 1, 0
        else:
            patch += 1
        new = f"{major}.{minor}.{patch}"
        VERSION_FILE.write_text(new + "\n", encoding="utf-8")
        print(f"VERSION {version} -> {new}")
        version = new
        args.append("--sync")

    html = PAGE.read_text(encoding="utf-8")
    m = META.search(html)
    if not m:
        print('FAIL: no <meta name="version"> in index.html')
        return 1
    in_page = m.group(2).strip()

    if in_page == version:
        print(f"OK: VERSION and index.html both say {version}")
        return 0

    if "--sync" not in args:
        print(f"Version drift: VERSION says {version}, index.html says {in_page}")
        print("\nRun:  python3 tools/check_version.py --sync")
        return 1

    PAGE.write_text(html[: m.start(2)] + version + html[m.end(2) :], encoding="utf-8")
    print(f"Synced index.html to {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
