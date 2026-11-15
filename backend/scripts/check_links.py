#!/usr/bin/env python3
"""Open every source URL in equipment.json and report which ones fail.

Run on a machine with internet access:
    python scripts/check_links.py
After a link opens and shows the facts it is cited for, set "checked": true
on that source in equipment.json.
"""
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_FILE = Path(__file__).resolve().parents[1] / "equipment.json"


def check(url, timeout=20):
    """Return (ok, detail) for one URL."""
    request = urllib.request.Request(url, headers={"User-Agent": "SpaceHeritageLinkCheck/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return 200 <= response.status < 400, f"HTTP {response.status}"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except Exception as exc:  # DNS failure, timeout, TLS problem...
        return False, type(exc).__name__


def collect_urls(data):
    urls = {}
    for item in data["equipment"]:
        for source in item.get("sources", []):
            urls.setdefault(source["url"], []).append((item["id"], source.get("checked")))
        coords = item.get("coordinates", {})
        if coords.get("source_url"):
            urls.setdefault(coords["source_url"], []).append((item["id"], None))
    return urls


def main(argv):
    path = Path(argv[1]) if len(argv) > 1 else DEFAULT_FILE
    urls = collect_urls(json.loads(path.read_text(encoding="utf-8")))
    failures = 0
    for url, users in sorted(urls.items()):
        ok, detail = check(url)
        failures += not ok
        print(f"{'OK  ' if ok else 'FAIL'} {detail:<14} {url}  <- {', '.join(sorted({u[0] for u in users}))}")
    print(f"\n{len(urls) - failures}/{len(urls)} links opened.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
