import json
from urllib.parse import urlencode
import requests
from bs4 import BeautifulSoup

BASE = "https://www.cosicomodo.it/familasud/teverola/reparti/prodotti-alimentari/c/10012"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/152 Safari/537.36"
CASES = [
    ("base", {}),
    ("page", {"page": 1}),
    ("page2", {"page": 2}),
    ("currentPage", {"currentPage": 1}),
    ("currentPage2", {"currentPage": 2}),
]


def parse(url, params):
    r = requests.get(url, params=params, headers={"User-Agent": UA, "Accept-Language":"it-IT,it;q=0.9"}, timeout=60)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    node = soup.find("script", id="__NEXT_DATA__")
    data = json.loads(node.string)
    pp = ((data.get("props") or {}).get("pageProps") or {})
    sp = pp.get("searchPageData") or {}
    pag = sp.get("pagination") or {}
    products = sp.get("products") or []
    return {
        "requested": r.url,
        "currentPage": pag.get("currentPage"),
        "pageSize": pag.get("pageSize"),
        "totalPages": pag.get("totalPages"),
        "totalResults": pag.get("totalResults"),
        "codes": [str(p.get("code")) for p in products[:8] if isinstance(p, dict)],
    }

out=[]
for name, params in CASES:
    try:
        row=parse(BASE,params); row["case"]=name; out.append(row)
    except Exception as e:
        out.append({"case":name,"error":repr(e)})
print(json.dumps(out,ensure_ascii=False,indent=2))

base_codes = next((x.get("codes") for x in out if x.get("case")=="base"), [])
valid=[x for x in out if x.get("currentPage") == 1 and x.get("codes") and x.get("codes") != base_codes]
if not valid:
    raise SystemExit("NO_VALID_PAGE_PARAM")
print("VALID_PAGE_PARAM", valid[0]["case"])
