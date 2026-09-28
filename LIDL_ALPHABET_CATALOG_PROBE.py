import json, urllib.request, time
from urllib.parse import urlencode
BASE="https://lidl.everli.com/api/search"
STORE_ID="9240"
HEADERS={"User-Agent":"Mozilla/5.0","Accept":"application/json"}
TERMS=list("abcdefghijklmnopqrstuvwxyz")+["0","1","2","3","4","5","6","7","8","9"]
def fetch(keyword,skip=0,take=100):
    qs=urlencode({"keyword":keyword,"skip":skip,"take":take,"brands":"","tags":"","store_ids":STORE_ID})
    req=urllib.request.Request(BASE+"?"+qs,headers=HEADERS)
    with urllib.request.urlopen(req,timeout=45) as r: return json.load(r)
allp={}
for q in TERMS:
    qcount=0
    for skip in range(0,5000,100):
        try:
            d=fetch(q,skip,100)
        except Exception as e:
            print("ERR",q,skip,repr(e)); break
        stores=d.get("stores") or []
        ps=(stores[0].get("products") or []) if stores else []
        if not ps: break
        for p in ps:
            if str(p.get("store_id"))!=STORE_ID: continue
            if int(p.get("price") or 0)<=0: continue
            pid=str(p.get("id") or p.get("ref_id") or "")
            if pid:
                before=len(allp); allp[pid]=p; qcount += (len(allp)>before)
        if len(ps)<100: break
        time.sleep(.02)
    print("TERM",q,"NEW",qcount,"TOTAL",len(allp))
report={"final_total":len(allp),"checks":{}}
print("FINAL_TOTAL",len(allp))
for key in ["carta igienica","acqua","detersivo","shampoo","prosciutto crudo","pasta","zucchine"]:
    hits=[]
    kl=key.lower()
    for p in allp.values():
        txt=" ".join(str(p.get(k) or "") for k in ["name","brand","category_name","main_category_name"]).lower()
        if all(w in txt for w in kl.split()): hits.append(p)
    report["checks"][key]=len(hits)
    print("CHECK",key,"COUNT",len(hits))
    for p in hits[:8]:
        print(" ",p.get("name"),p.get("brand"),p.get("category_name"),p.get("main_category_name"),p.get("price"))


with open("alphabet_probe_report.json","w",encoding="utf-8") as fh:
    json.dump(report,fh,ensure_ascii=False,indent=2)
