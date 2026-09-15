import json, re
from pathlib import Path

import requests
from bs4 import BeautifulSoup

URL = "https://www.cosicomodo.it/familasud/teverola/reparti/frutta-e-verdura/frutta-fresca/fragole-e-frutti-di-bosco/c/62"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/152 Safari/537.36"


def clean(s):
    return " ".join((s or "").split())


def snippet(text, needle, radius=500):
    pos = text.lower().find(needle.lower())
    if pos < 0:
        return None
    lo = max(0, pos - radius)
    hi = min(len(text), pos + len(needle) + radius)
    return clean(text[lo:hi])


def main():
    r = requests.get(URL, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "it-IT,it;q=0.9,en;q=0.7",
    }, timeout=60)
    print("STATUS", r.status_code, "LEN", len(r.text), "URL", r.url)
    r.raise_for_status()

    html = r.text
    soup = BeautifulSoup(html, "html.parser")
    scripts = []
    for i, s in enumerate(soup.find_all("script")):
        body = s.string if s.string is not None else s.get_text("", strip=False)
        body = body or ""
        scripts.append({
            "index": i,
            "id": s.get("id"),
            "type": s.get("type"),
            "src": s.get("src"),
            "length": len(body),
            "head": clean(body[:300]),
            "has_fragole": "fragole" in body.lower(),
            "has_price": "price" in body.lower(),
            "has_products": "products" in body.lower(),
        })

    needles = [
        "Fragole Italia",
        "Fragole \"sorriso\"",
        "MIRTILLI PREMIUM",
        "MEGAMARK_FAMILA",
        '"price"',
        'products',
        '/p/',
        'productCode',
    ]
    snippets = {n: snippet(html, n) for n in needles}

    patterns = {
        "p_paths": re.findall(r"[^\"'<>\\s]{0,140}/p/[^\"'<>\\s]{1,120}", html)[:50],
        "product_codes": re.findall(r"MEGAMARK_FAMILA_[A-Za-z0-9_-]+", html)[:50],
        "ean_like": re.findall(r"(?<!\d)\d{13}(?!\d)", html)[:50],
    }

    result = {
        "url": r.url,
        "status": r.status_code,
        "html_length": len(html),
        "scripts_count": len(scripts),
        "scripts": scripts,
        "snippets": snippets,
        "patterns": patterns,
    }
    Path("famila_html_probe.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))

    interesting = [s for s in scripts if s["has_fragole"] or s["has_price"] or s["has_products"]]
    if not interesting and not any(snippets.values()):
        raise SystemExit("PROBE_FAIL: nessun payload prodotto individuato nell'HTML")
    print("FAMILA_HTML_PAYLOAD_FOUND", len(interesting))


if __name__ == "__main__":
    main()
