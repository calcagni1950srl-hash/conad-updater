import json, requests
from pathlib import Path
STORE_ID="010548"
URL=f"https://www.conad.it/api/corporate/it-it.getPointOfServiceByAnacanId.json?anacanId={STORE_ID}"

def walk(o):
    if isinstance(o,dict):
        yield o
        for v in o.values(): yield from walk(v)
    elif isinstance(o,list):
        for v in o: yield from walk(v)

r=requests.get(URL,headers={"User-Agent":"Mozilla/5.0","Accept-Language":"it-IT,it;q=0.9"},timeout=45)
r.raise_for_status()
data=r.json()
out=[]
seen=set()
for n in walk(data):
    if not isinstance(n,dict): continue
    title=n.get("title") or n.get("feTitle")
    pdf=n.get("pdfUrl")
    if not title or not pdf: continue
    k=(title,pdf)
    if k in seen: continue
    seen.add(k)
    out.append({"title":title,"keys":sorted(n.keys()),"node":n})
Path("conad_capodrise_flyer_metadata.json").write_text(json.dumps({"store_id":STORE_ID,"flyers":out},ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps({"store_id":STORE_ID,"flyer_count":len(out),"flyers":[{"title":x["title"],"keys":x["keys"]} for x in out]},ensure_ascii=False,indent=2))
