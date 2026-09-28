import json, urllib.request
from urllib.parse import urlencode
BASE="https://lidl.everli.com/api/search"
STORE_ID="9240"
HEADERS={"User-Agent":"Mozilla/5.0","Accept":"application/json"}
def fetch(skip=0,take=100):
    qs=urlencode({"keyword":"","skip":skip,"take":take,"brands":"","tags":"","store_ids":STORE_ID})
    req=urllib.request.Request(BASE+"?"+qs,headers=HEADERS)
    with urllib.request.urlopen(req,timeout=45) as r:
        return json.load(r)
allp={}
for skip in range(0,5000,100):
    d=fetch(skip,100)
    stores=d.get("stores") or []
    ps=(stores[0].get("products") or []) if stores else []
    print("PAGE",skip,"COUNT",len(ps))
    for p in ps:
        if str(p.get("store_id"))==STORE_ID and int(p.get("price") or 0)>0:
            pid=str(p.get("id") or p.get("ref_id") or "")
            if pid: allp[pid]=p
    if len(ps)<100: break
print("TOTAL",len(allp))
for p in list(allp.values())[:30]:
    print(p.get("name"),p.get("brand"),p.get("category_name"),p.get("main_category_name"))
