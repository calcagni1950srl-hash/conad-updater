import json
from pathlib import Path
import requests
from bs4 import BeautifulSoup

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/152 Safari/537.36"
URLS = [
    "https://www.cosicomodo.it/familasud/teverola/reparti/prodotti-alimentari/c/10012",
    "https://www.cosicomodo.it/familasud/teverola/reparti/frutta-e-verdura/c/10006",
]


def walk(v, path="root"):
    yield path, v
    if isinstance(v, dict):
        for k, x in v.items():
            yield from walk(x, f"{path}.{k}")
    elif isinstance(v, list):
        for i, x in enumerate(v):
            yield from walk(x, f"{path}[{i}]")


def looks_product(d):
    if not isinstance(d, dict): return False
    code = str(d.get("code") or "")
    name = str(d.get("name") or d.get("description") or "")
    price = d.get("price") or d.get("bestPrice") or {}
    return bool(code and name and isinstance(price, dict) and price.get("value"))


def main():
    s = requests.Session()
    out = []
    for url in URLS:
        r = s.get(url, headers={"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9"}, timeout=60)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        node = soup.find("script", id="__NEXT_DATA__")
        data = json.loads(node.string)
        pp = ((data.get("props") or {}).get("pageProps") or {})
        products = {}
        paginations = []
        interesting = []
        for path, v in walk(pp):
            if looks_product(v):
                products[str(v.get("code"))] = v
            if isinstance(v, dict):
                keys = set(v.keys())
                if {"currentPage", "totalPages"}.issubset(keys) or {"currentPage", "totalResults"}.issubset(keys):
                    paginations.append({"path": path, "value": {k: v.get(k) for k in ("currentPage","pageSize","totalPages","totalResults","sort") if k in v}})
        for key in ("categoryData","searchPageData","subcategories","firstLevelCategories","categoryOptions"):
            value = pp.get(key)
            if value is not None:
                if isinstance(value, dict):
                    summary = {"type":"dict","keys":list(value.keys())[:80]}
                elif isinstance(value, list):
                    summary = {"type":"list","len":len(value),"sample":value[:3]}
                else:
                    summary = {"type":type(value).__name__,"value":str(value)[:500]}
                interesting.append({"key":key,"summary":summary})
        out.append({
            "url": url,
            "status": r.status_code,
            "html_length": len(r.text),
            "products_in_nextdata": len(products),
            "paginations": paginations[:30],
            "interesting": interesting,
        })
    Path("famila_pagination_probe.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))

if __name__ == "__main__": main()
