import json, re
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

URL = "https://www.cosicomodo.it/familasud/teverola/reparti/frutta-e-verdura/frutta-fresca/fragole-e-frutti-di-bosco/c/62"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/152 Safari/537.36"


def clean(s):
    return " ".join((s or "").split())


def product_context(anchor):
    node = anchor
    best = None
    for depth in range(9):
        node = getattr(node, "parent", None)
        if node is None:
            break
        text = clean(node.get_text(" ", strip=True))
        if "€" in text and ("Aggiungi" in text or "Aggiunto" in text):
            best = {
                "depth": depth + 1,
                "tag": node.name,
                "class": node.get("class") or [],
                "text": text[:1200],
            }
            break
    return best


def main():
    s = requests.Session()
    r = s.get(URL, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "it-IT,it;q=0.9,en;q=0.7",
    }, timeout=60)
    print("STATUS", r.status_code, "LEN", len(r.text), "URL", r.url)
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")
    products = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = str(a.get("href") or "")
        if "/p/" not in href:
            continue
        full = urljoin(r.url, href)
        if full in seen:
            continue
        seen.add(full)
        code = href.rsplit("/p/", 1)[-1].split("?", 1)[0].split("#", 1)[0]
        item = {
            "href": href,
            "code": code,
            "anchor_text": clean(a.get_text(" ", strip=True)),
            "context": product_context(a),
        }
        products.append(item)

    categories = []
    cseen = set()
    for a in soup.find_all("a", href=True):
        href = str(a.get("href") or "")
        if re.search(r"/c/\d+/?(?:\?|$)", href) and href not in cseen:
            cseen.add(href)
            categories.append({"href": href, "text": clean(a.get_text(" ", strip=True))})

    result = {
        "url": r.url,
        "status": r.status_code,
        "html_length": len(r.text),
        "product_links": len(products),
        "category_links": len(categories),
        "products_sample": products[:20],
        "categories_sample": categories[:40],
    }
    Path("famila_html_probe.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if r.status_code != 200 or len(products) < 8:
        raise SystemExit("PROBE_FAIL: pagina raggiunta ma prodotti insufficienti")
    if not any(p.get("context") for p in products):
        raise SystemExit("PROBE_FAIL: nessun contesto prezzo trovato")
    print("FAMILA_HTML_PROBE_OK")


if __name__ == "__main__":
    main()
