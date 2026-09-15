import json, re
from pathlib import Path

import requests
from bs4 import BeautifulSoup

URL = "https://www.cosicomodo.it/familasud/teverola/reparti/frutta-e-verdura/frutta-fresca/fragole-e-frutti-di-bosco/c/62"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/152 Safari/537.36"
CAT_RE = re.compile(r"^/familasud/teverola/reparti/.+/c/\d+/?$")


def walk(value):
    yield value
    if isinstance(value, dict):
        for v in value.values():
            yield from walk(v)
    elif isinstance(value, list):
        for v in value:
            yield from walk(v)


def fnum(v):
    try:
        return float(v)
    except Exception:
        return None


def product_from_dict(d):
    if not isinstance(d, dict):
        return None
    code = str(d.get("code") or "").strip()
    name = str(d.get("name") or d.get("description") or "").strip()
    price = d.get("price") or d.get("bestPrice") or {}
    if not isinstance(price, dict):
        return None
    value = fnum(price.get("value"))
    url = str(d.get("url") or "").strip()
    if not code or not name or not value or value <= 0:
        return None
    if "/p/" not in url and not re.fullmatch(r"\d{8,14}", code):
        return None
    return {
        "code": code,
        "name": name,
        "price": value,
        "price_formatted": price.get("formattedValue"),
        "unit_price": fnum(price.get("priceReferenceUnit")),
        "unit": price.get("referenceUnitMeasure"),
        "url": url,
        "leafCategoryName": d.get("leafCategoryName"),
        "brand": d.get("marca") or d.get("brand"),
        "saleable": d.get("saleable"),
        "stock": d.get("stock"),
    }


def main():
    r = requests.get(URL, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "it-IT,it;q=0.9,en;q=0.7",
    }, timeout=60)
    print("STATUS", r.status_code, "LEN", len(r.text), "URL", r.url)
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")
    node = soup.find("script", id="__NEXT_DATA__")
    if not node or not node.string:
        raise SystemExit("PROBE_FAIL: __NEXT_DATA__ non trovato")
    data = json.loads(node.string)

    products = {}
    category_urls = set()
    for x in walk(data):
        if isinstance(x, dict):
            p = product_from_dict(x)
            if p:
                products[p["code"]] = p
        elif isinstance(x, str):
            s = x.split("?", 1)[0].rstrip("/")
            if CAT_RE.match(s):
                category_urls.add(s)

    pp = ((data.get("props") or {}).get("pageProps") or {})
    result = {
        "status": r.status_code,
        "html_length": len(r.text),
        "pageProps_keys": sorted(pp.keys()),
        "product_count": len(products),
        "products_sample": list(products.values())[:20],
        "category_url_count": len(category_urls),
        "category_urls_sample": sorted(category_urls)[:100],
    }
    Path("famila_html_probe.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if len(products) < 8:
        raise SystemExit(f"PROBE_FAIL: estratti solo {len(products)} prodotti")
    if not category_urls:
        raise SystemExit("PROBE_FAIL: nessuna URL categoria trovata")
    print("FAMILA_STRUCTURED_PROBE_OK", len(products), len(category_urls))


if __name__ == "__main__":
    main()
