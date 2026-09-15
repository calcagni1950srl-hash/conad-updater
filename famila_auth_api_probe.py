import json
import requests
from bs4 import BeautifulSoup

PAGE = "https://www.cosicomodo.it/familasud/teverola/reparti/prodotti-alimentari/c/10012"
API_BASE = "https://api.cosicomodo.it/occ/v2"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/152 Safari/537.36"


def compact(v, limit=1200):
    try:
        text = json.dumps(v, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        text = repr(v)
    return text[:limit]


s = requests.Session()
s.headers.update({
    "User-Agent": UA,
    "Accept-Language": "it-IT,it;q=0.9,en;q=0.7",
})

# Apri prima la vera pagina Famila: conserva i cookie di punto vendita/sessione.
r = s.get(PAGE, headers={
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}, timeout=60)
r.raise_for_status()

soup = BeautifulSoup(r.text, "html.parser")
node = soup.find("script", id="__NEXT_DATA__")
if not node or not node.string:
    raise SystemExit("NEXT_DATA_MISSING")
pp = ((json.loads(node.string).get("props") or {}).get("pageProps") or {})
sp = pp.get("searchPageData") or {}

site = str(pp.get("originSiteWithSubBrand") or pp.get("originSite") or "familasud")
store = str(pp.get("storeAliasId") or "teverola")
user = "anonymous"
point = pp.get("pointOfService")
current_query = sp.get("currentQuery") or {}
sorts = sp.get("sorts") or []

selected_sort = None
if isinstance(sorts, list):
    for item in sorts:
        if isinstance(item, dict) and item.get("selected"):
            selected_sort = item.get("code")
            break
    if not selected_sort:
        for item in sorts:
            if isinstance(item, dict) and item.get("code"):
                selected_sort = item.get("code")
                break

print(json.dumps({
    "site": site,
    "storeAliasId": store,
    "pointOfService": point,
    "cookies": s.cookies.get_dict(),
    "currentQuery": current_query,
    "selected_sort": selected_sort,
    "pagination_page0": sp.get("pagination"),
}, ensure_ascii=False, indent=2)[:15000])

endpoint = f"{API_BASE}/{site}/stores/{store}/users/{user}/products/search-by-category"
base_params = {
    "categoryCode": "10012",
    "currentPage": 1,
    "pageSize": 20,
    "fields": "FULL",
}

# Replica progressivamente i parametri che il frontend passa a P.JP.
variants = []
variants.append(("minimal", dict(base_params)))
if selected_sort:
    variants.append(("with-sort", {**base_params, "sort": selected_sort}))

# currentQuery può contenere facet/sort/filtri già canonicalizzati dal server.
if isinstance(current_query, dict):
    qparams = dict(base_params)
    for key, value in current_query.items():
        if value is None or value == "":
            continue
        # non sovrascrivere pagina/categoria del probe
        if key in {"currentPage", "pageSize", "categoryCode", "fields"}:
            continue
        if isinstance(value, (str, int, float, bool)):
            qparams[key] = value
    variants.append(("with-currentQuery", qparams))

# Cookie importanti anche come header espliciti, oltre a Session cookies.
headers = {
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Origin": "https://www.cosicomodo.it",
    "Referer": PAGE,
    "Sec-Fetch-Site": "same-site",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
}

seen = set()
for name, params in variants:
    key = tuple(sorted((str(k), str(v)) for k, v in params.items()))
    if key in seen:
        continue
    seen.add(key)
    rr = s.get(endpoint, params=params, headers=headers, timeout=60)
    row = {
        "variant": name,
        "status": rr.status_code,
        "url": rr.url,
        "content_type": rr.headers.get("content-type"),
        "body": rr.text[:2500],
    }
    if rr.status_code == 200:
        try:
            data = rr.json()
            pag = data.get("pagination") or {}
            products = data.get("products") or []
            row.update({
                "currentPage": pag.get("currentPage"),
                "totalPages": pag.get("totalPages"),
                "totalResults": pag.get("totalResults"),
                "product_count": len(products),
                "codes": [str(x.get("code")) for x in products[:8] if isinstance(x, dict)],
            })
        except Exception as e:
            row["json_error"] = repr(e)
    print(json.dumps(row, ensure_ascii=False, indent=2))
    if row.get("status") == 200 and int(row.get("currentPage", -1)) == 1 and row.get("product_count", 0) > 0:
        print("FAMILA_BROWSER_EQUIVALENT_API_OK", name)
        raise SystemExit(0)

# Come rete di sicurezza prova il proxy validate-search usato dal frontend per richieste autenticate.
proxy = "https://www.cosicomodo.it/api/auth/validate-search"
proxy_payload = {
    "endpoint": f"/{site}/stores/{store}/users/{user}/products/search-by-category",
    "method": "GET",
    "data": None,
    "params": variants[-1][1] if variants else base_params,
    "headers": {},
    "isBaseUrl": False,
    "isForwardedForNeeded": False,
    "isAgent": False,
    "isAgentUrl": True,
    "isFirstAxiosMessage": False,
}
pr = s.post(proxy, json=proxy_payload, headers={
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Origin": "https://www.cosicomodo.it",
    "Referer": PAGE,
}, timeout=60)
print(json.dumps({
    "variant": "validate-search-proxy",
    "status": pr.status_code,
    "content_type": pr.headers.get("content-type"),
    "body": pr.text[:4000],
}, ensure_ascii=False, indent=2))

raise SystemExit("FAMILA_BROWSER_EQUIVALENT_API_FAILED")
