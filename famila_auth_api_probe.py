import json
import requests
from bs4 import BeautifulSoup

PAGE = "https://www.cosicomodo.it/familasud/teverola/reparti/prodotti-alimentari/c/10012"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/152 Safari/537.36"

s = requests.Session()
s.headers.update({
    "User-Agent": UA,
    "Accept-Language": "it-IT,it;q=0.9,en;q=0.7",
    "Origin": "https://www.cosicomodo.it",
    "Referer": PAGE,
})
r = s.get(PAGE, timeout=60); r.raise_for_status()
soup = BeautifulSoup(r.text, "html.parser")
node = soup.find("script", id="__NEXT_DATA__")
pp = ((json.loads(node.string).get("props") or {}).get("pageProps") or {})

api_base = str(pp.get("apiBase") or "https://api.cosicomodo.it/occ/v2").rstrip("/")
site = str(pp.get("originSiteWithSubBrand") or pp.get("originSite") or "familasud")
store = str(pp.get("storeAliasId") or "teverola")
user = str(pp.get("appUserId") or "anonymous")
token = str(pp.get("appAuthToken") or "")
sp = pp.get("searchPageData") or {}
query = sp.get("currentQuery")

print(json.dumps({
    "apiBase": api_base,
    "site": site,
    "store": store,
    "appUserId": user,
    "token_present": bool(token),
    "token_length": len(token),
    "cookies": sorted(s.cookies.keys()),
    "currentQuery_type": type(query).__name__,
}, ensure_ascii=False, indent=2))

endpoint = f"{api_base}/{site}/stores/{store}/users/{user}/products/search-by-category"
params = {"categoryCode":"10012","currentPage":1,"pageSize":20,"fields":"FULL"}

variants = [
    ("session-only", {}),
    ("auth-raw", {"Authorization": token} if token else {}),
    ("auth-bearer", {"Authorization": f"Bearer {token}"} if token and not token.lower().startswith("bearer ") else ({"Authorization": token} if token else {})),
]

for name, extra in variants:
    headers={"Accept":"application/json, text/plain, */*", **extra}
    rr=s.get(endpoint, params=params, headers=headers, timeout=60)
    row={"variant":name,"status":rr.status_code,"url":rr.url,"content_type":rr.headers.get("content-type"),"body_head":rr.text[:300]}
    if rr.status_code==200:
        try:
            data=rr.json(); pag=data.get("pagination") or {}; products=data.get("products") or []
            row.update({"currentPage":pag.get("currentPage"),"totalPages":pag.get("totalPages"),"totalResults":pag.get("totalResults"),"product_count":len(products),"codes":[str(x.get("code")) for x in products[:5] if isinstance(x,dict)]})
        except Exception as e: row["json_error"]=repr(e)
    print(json.dumps(row,ensure_ascii=False,indent=2))
    if row.get("status")==200 and row.get("currentPage")==1 and row.get("product_count",0)>0:
        print("FAMILA_API_AUTH_OK", name)
        raise SystemExit(0)

raise SystemExit("FAMILA_API_AUTH_FAILED")
