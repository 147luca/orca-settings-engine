#!/usr/bin/env python3
"""
fetch_community.py — pull a model's page + comments past Cloudflare, save the text.

Run with the 3d-pipeline venv (has playwright).

HONEST LIMITATION (tested 2026-05-21): Cloudflare reliably blocks standalone
Playwright on Printables — headless, non-headless, bundled chromium, AND real
Chrome channel all get stuck on the "Just a moment..." challenge. So this
returns BLOCKED for Printables/MMF. It still works on non-Cloudflare sites.
Reliable acquisition for CF-protected pages: (a) the Playwright *MCP* driven
interactively (it cleared CF in testing), or (b) a manual page save. The value
is community_intel.extract_intel() — the parser — which runs on whatever text
you hand it.

  ~/3d-pipeline/.venv/bin/python3 fetch_community.py <model_url> [out.txt]

Prints the saved path on success, or "BLOCKED" if Cloudflare won't clear.
"""

import sys

from playwright.sync_api import sync_playwright

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def fetch(url, out_path):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=UA, viewport={"width": 1280, "height": 900})
        blocked = True
        for _ in range(4):
            page.goto(url, wait_until="domcontentloaded", timeout=35000)
            page.wait_for_timeout(4000)  # let the CF JS challenge run
            if "just a moment" not in page.title().lower():
                blocked = False
                break
        text = page.inner_text("body") if not blocked else ""
        if not blocked:
            try:  # comments live on a sub-page on Printables
                page.goto(
                    url.rstrip("/") + "/comments",
                    wait_until="domcontentloaded",
                    timeout=35000,
                )
                page.wait_for_timeout(3000)
                text += "\n\n=== COMMENTS ===\n" + page.inner_text("body")
            except Exception:
                pass
        browser.close()
    if blocked or len(text) < 500:
        return None
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)
    return out_path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: fetch_community.py <url> [out.txt]")
    url = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else "/tmp/community_page.txt"
    result = fetch(url, out)
    print(result if result else "BLOCKED")
    sys.exit(0 if result else 3)
