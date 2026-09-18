import json, requests, os, re
from pathlib import Path
STORE_ID="010548"
API=f"https://www.conad.it/api/corporate/it-it.getPointOfServiceByAnacanId.json?anacanId={STORE_ID}"
TARGET={"Campioni del Risparmio","Convenienza Più","Autunno dai sapori che scaldano","Spunta il Risparmio"}
def walk(o):
    if isinstance(o,dict):
        yield o
        for v in o.values(): yield from walk(v)
    elif isinstance(o,list):
        for v in o: yield from walk(v)
data=requests.get(API,headers={"User-Agent":"Mozilla/5.0"},timeout=45).json()
Path("conad_flyers").mkdir(exist_ok=True)
saved=[]
seen=set()
for n in walk(data):
    if not isinstance(n,dict): continue
    title=n.get("title") or n.get("feTitle")
    url=n.get("pdfUrl")
    if title not in TARGET or not url or (title,url) in seen: continue
    seen.add((title,url))
    fn=re.sub(r'[^A-Za-z0-9._-]+','_',title).strip('_')+".pdf"
    r=requests.get(url,headers={"User-Agent":"Mozilla/5.0"},timeout=90)
    r.raise_for_status()
    if not r.content.startswith(b"%PDF"): raise RuntimeError("not pdf "+title)
    Path("conad_flyers",fn).write_bytes(r.content)
    saved.append({"title":title,"file":fn,"bytes":len(r.content),"url":url})
Path("conad_flyers/manifest.json").write_text(json.dumps(saved,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(saved,ensure_ascii=False,indent=2))
