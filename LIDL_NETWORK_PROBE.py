import json
import re
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

TARGETS = [
    "https://www.lidl.it/q/query/pasta",
    "https://www.lidl.it/q/query/pomodoro",
    "https://www.lidl.it/q/query/carne",
    "https://www.lidl.it/c/cibo-e-bevande/s10068374",
]

interesting = []

def looks_interesting(url, ctype):
    u = url.lower()
    c = (ctype or "").lower()
    return (
        "json" in c
        or "graphql" in u
        or "search" in u
        or "query" in u
        or "product" in u
        or "api" in u
        or "recommend" in u
        or "algolia" in u
        or "constructor" in u
        or "commerce" in u
    )

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(locale="it-IT")
    page = context.new_page()

    def on_response(resp):
        try:
            ctype = resp.headers.get("content-type", "")
            if not looks_interesting(resp.url, ctype):
                return
            rec = {
                "url": resp.url,
                "status": resp.status,
                "content_type": ctype,
                "request_method": resp.request.method,
                "resource_type": resp.request.resource_type,
            }
            if "json" in ctype.lower():
                try:
                    body = resp.text()
                    rec["body_preview"] = body[:1500]
                except Exception as e:
                    rec["body_error"] = repr(e)
            interesting.append(rec)
        except Exception:
            pass

    page.on("response", on_response)

    for url in TARGETS:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2500)

        for label in ["Accetta tutti", "Accetta", "Consenti tutti"]:
            try:
                btn = page.get_by_role("button", name=re.compile(label, re.I))
                if btn.count():
                    btn.first.click(timeout=1500)
                    page.wait_for_timeout(300)
                    break
            except Exception:
                pass

        for _ in range(8):
            page.mouse.wheel(0, 5000)
            page.wait_for_timeout(700)
            for text in ["Visualizza altri prodotti", "Mostra altri prodotti", "Carica altri"]:
                try:
                    btn = page.get_by_text(text, exact=False)
                    if btn.count():
                        btn.last.click(timeout=1500)
                        page.wait_for_timeout(900)
                        break
                except Exception:
                    pass

    browser.close()

# Deduplicate while retaining the richest preview.
by_key = {}
for rec in interesting:
    key = (rec["url"], rec["status"], rec["content_type"])
    old = by_key.get(key)
    if old is None or len(rec.get("body_preview", "")) > len(old.get("body_preview", "")):
        by_key[key] = rec

rows = sorted(by_key.values(), key=lambda x: x["url"])
with open("lidl_network_probe.json", "w", encoding="utf-8") as f:
    json.dump(rows, f, ensure_ascii=False, indent=2)

print("INTERESTING_RESPONSES", len(rows))
for r in rows:
    print("\nURL", r["url"])
    print("STATUS", r["status"], "TYPE", r["content_type"], "RESOURCE", r["resource_type"])
    if r.get("body_preview"):
        print("BODY", r["body_preview"][:600].replace("\n", " "))
