import json, time, urllib.parse
import requests

API = "https://api.cosicomodo.it/occ/v2/familasud/stores/teverola/users/anonymous/products/search-by-category"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/152 Safari/537.36"

point_min = {
    "name":"MEGAMARK_FAMILA_163711",
    "socio":"MEGAMARK",
    "tipoDiServizio":"CC",
    "displayName":"Teverola",
    "insegna":"familasud",
    "url":"teverola",
    "description":"FAMILA - TEVEROLA",
    "address":{
        "postalCode":"81030",
        "town":"Teverola",
        "district":"CE",
        "line1":"Località Zona Asi Aversa Nord sn"
    }
}

variants = [
    ("no-cookie", {}),
    ("preferred-store", {
        "familasud_anonymous_preferred_base_store":"MEGAMARK_FAMILA_163711",
    }),
    ("preferred-store-cap", {
        "familasud_anonymous_preferred_base_store":"MEGAMARK_FAMILA_163711",
        "familasud_provisionalcap":"81030 - Teverola",
    }),
    ("preferred-store-cap-pos", {
        "familasud_anonymous_preferred_base_store":"MEGAMARK_FAMILA_163711",
        "familasud_provisionalcap":"81030 - Teverola",
        "pointOfService": urllib.parse.quote(json.dumps(point_min, ensure_ascii=False, separators=(",",":")), safe=""),
    }),
]

# Lascia raffreddare il WAF rispetto ai workflow precedenti.
time.sleep(20)

for name,cookies in variants:
    s=requests.Session()
    s.headers.update({"User-Agent":UA,"Accept-Language":"it-IT,it;q=0.9,en;q=0.7"})
    for k,v in cookies.items():
        s.cookies.set(k,v,domain=".cosicomodo.it",path="/")
    r=s.get(API,params={"categoryCode":"10012","currentPage":1,"pageSize":20,"fields":"FULL"},headers={
        "Accept":"application/json, text/plain, */*",
        "Origin":"https://www.cosicomodo.it",
        "Referer":"https://www.cosicomodo.it/familasud/teverola/reparti/prodotti-alimentari/c/10012",
        "Sec-Fetch-Site":"same-site","Sec-Fetch-Mode":"cors","Sec-Fetch-Dest":"empty"
    },timeout=60)
    row={"variant":name,"status":r.status_code,"body":r.text[:800],"sent_cookies":list(cookies.keys())}
    if r.status_code==200:
        try:
            d=r.json(); p=d.get("pagination") or {}; products=d.get("products") or []
            row.update({"currentPage":p.get("currentPage"),"totalPages":p.get("totalPages"),"totalResults":p.get("totalResults"),"product_count":len(products),"codes":[str(x.get("code")) for x in products[:5] if isinstance(x,dict)]})
        except Exception as e: row["json_error"]=repr(e)
    print(json.dumps(row,ensure_ascii=False,indent=2))
    if row.get("status")==200 and row.get("currentPage")==1 and row.get("product_count",0)>0:
        print("FAMILA_MANUAL_COOKIE_OK",name)
        raise SystemExit(0)
    time.sleep(4)

raise SystemExit("FAMILA_MANUAL_COOKIE_FAILED")
