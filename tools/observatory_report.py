#!/usr/bin/env python3
"""Turn an MDN Observatory scan into a Markdown report.

Used by .github/workflows/observatory.yml for both the run summary and the body
of the rolling issue, so the issue always shows the current state rather than
accumulating comments.

    python3 tools/observatory_report.py scan.json analyze.json > report.md
"""

import json
import sys
from pathlib import Path

FLOOR_FILE = Path(__file__).resolve().parent.parent / ".github" / "observatory-floor"

# Which side of the fence each test lives on. The deploy gate can only enforce
# the first group; everything else is a Cloudflare setting, which is the entire
# reason this workflow exists.
IN_REPO = {
    "content-security-policy",
    "x-frame-options",
    "referrer-policy",
    "subresource-integrity",
}


def main() -> int:
    scan = json.loads(Path(sys.argv[1]).read_text())
    analyze = json.loads(Path(sys.argv[2]).read_text())
    floor = int(FLOOR_FILE.read_text().strip()) if FLOOR_FILE.exists() else 100

    grade = scan.get("grade", "?")
    score = scan.get("score", 0)
    ok = isinstance(score, int) and score >= floor

    print(f"## {grade} — score {score}\n")
    print(
        f"Floor is **{floor}**. "
        + ("At or above it; nothing to do.\n" if ok else "**Below the floor.**\n")
    )

    tests = analyze.get("tests") or {}
    if not tests:
        print("_No per-test data returned._")
        return 0

    losing = [(k, v) for k, v in tests.items() if v.get("score_modifier", 0) < 0]
    if losing:
        print("### Losing points\n")
        print("| points | test | result | fix where |")
        print("|---:|---|---|---|")
        for k, v in sorted(losing, key=lambda kv: kv[1].get("score_modifier", 0)):
            where = "this repo" if k in IN_REPO else "**Cloudflare**"
            print(
                f"| {v.get('score_modifier')} | `{k}` | {v.get('result','')} | {where} |"
            )
        print()

    print("### All tests\n")
    print("| points | test | result |")
    print("|---:|---|---|")
    for k, v in sorted(tests.items(), key=lambda kv: kv[1].get("score_modifier", 0)):
        print(f"| {v.get('score_modifier', 0)} | `{k}` | {v.get('result','')} |")

    print(
        "\n---\n"
        "Repo-side requirements are enforced by `tools/check_security.py` on every "
        "deploy. Header-side settings live in Cloudflare — see `SECURITY-HEADERS.md`."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
