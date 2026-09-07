import json, requests, time
from datetime import datetime, timezone
from pathlib import Path

OUT=Path("artifact")
OUT.mkdir(exist_ok=True)

TARGETS=[
    {"name":"Teverola Frutta e verdura","baseSiteId":"familasud","storeAliasId":"teverola","categoryCode":"10006"},
    {"name":"Foggia Addedda Tutto per la casa","baseSiteId":"familasud","storeAliasId":"foggia-addedda","categoryCode":"10016"},
]
BASE="https://api.cosicomodo.it/occ/v2"
HEADERS={"User-Agent":"Mozilla/5.0 (SmartCampania technical validation; +https://github.com/)","Accept":"application/json"}

def fetch(t,page):
    url=f"{BASE}/{t['baseSiteId']}/stores/{t['storeAliasId']}/users/anonymous/products/search-by-category"
    params={"categoryCode":t["categoryCode"],"currentPage":page,"pageSize":20,"fields":"FULL"}
    r=requests.get(url,params=params,headers=HEADERS,timeout=45)
    info={"requested_page":page,"url":r.url,"status":r.status_code,"content_type":r.headers.get("content-type",""),"bytes":len(r.content)}
    try:
        data=r.json()
        (OUT/f"{t['storeAliasId']}_page_{page}.json").write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
    except Exception:
        data=None
        (OUT/f"{t['storeAliasId']}_page_{page}.txt").write_text(r.text,encoding="utf-8",errors="ignore")
    if isinstance(data,dict):
        pag=data.get("pagination") or {}
        products=data.get("products") or []
        info.update({
            "pagination":pag,
            "product_count":len(products),
            "codes":[str(x.get("code") or x.get("ean") or "") for x in products if isinstance(x,dict)],
            "positive_prices":sum(1 for x in products if isinstance(x,dict) and isinstance(x.get("price"),dict) and (x["price"].get("value") or 0)>0),
        })
    return info

report={"probe":"Famila API Pagination Probe V8","generated_at_utc":datetime.now(timezone.utc).isoformat(),"targets":[]}
validated=0
for t in TARGETS:
    rec={"target":t,"pages":[]}
    for p in (0,1,2):
        try: rec["pages"].append(fetch(t,p))
        except Exception as e: rec["pages"].append({"requested_page":p,"error":repr(e)})
        time.sleep(2)
    pages=rec["pages"]
    ok=False
    if len(pages)==3 and all(x.get("status")==200 and x.get("product_count",0)>0 for x in pages):
        sets=[set(x.get("codes",[])) for x in pages]
        cps=[(x.get("pagination") or {}).get("currentPage") for x in pages]
        ok=(len(set(cps))==3 and all(sets[i]!=sets[j] for i in range(3) for j in range(i+1,3)))
    rec["pagination_validated"]=ok
    validated+=int(ok)
    report["targets"].append(rec)

report["verdict"]="DIRECT_OCC_API_PAGINATION_VALIDATED" if validated==len(TARGETS) else ("PARTIAL_OCC_API_PAGINATION_VALIDATION" if validated else "OCC_API_PAGINATION_NOT_VALIDATED")
(OUT/"famila_api_pagination_probe_v8.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")

lines=["# Famila API Pagination Probe V8","",f"Verdetto: **{report['verdict']}**","",
"Endpoint scoperto staticamente nei chunk ufficiali CosìComodo:",
"`https://api.cosicomodo.it/occ/v2/{baseSiteId}/stores/{storeAliasId}/users/anonymous/products/search-by-category`",""]
for r in report["targets"]:
    lines += [f"## {r['target']['name']}",f"Validata: **{r['pagination_validated']}**"]
    for p in r["pages"]:
        lines.append(f"- richiesta {p.get('requested_page')}: HTTP {p.get('status')} | currentPage {(p.get('pagination') or {}).get('currentPage')} | prodotti {p.get('product_count')} | prezzi positivi {p.get('positive_prices')}")
    lines.append("")
(OUT/"RISULTATO.md").write_text("\n".join(lines),encoding="utf-8")
print("\n".join(lines))
