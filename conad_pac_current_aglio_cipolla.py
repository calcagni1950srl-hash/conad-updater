import json, re, time
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import requests
import fitz

STORES = {
    "010548": "Capodrise Superstore",
    "009695": "Sant'Arpino Superstore",
    "005560": "Caserta Acquaviva Conad City",
    "002595": "Caserta Quercione Conad City",
    "008874": "Caserta Patturelli Conad",
    "002928": "Portico di Caserta Conad",
    "010487": "Maddaloni Via Napoli Conad City",
    "009919": "S. Maria C.V. Gran Bretagna Conad",
    "009516": "Maddaloni Via Appia Conad",
}
TERMS = ["aglio", "cipolla"]
WORD = {t: re.compile(r"(?<![A-Za-zÀ-ÿ])"+re.escape(t)+r"(?![A-Za-zÀ-ÿ])", re.I) for t in TERMS}
PRICE = re.compile(r"(?<!\d)(\d{1,3}[,.]\d{2})(?:\s*€|\s*euro)?(?!\d)", re.I)

s = requests.Session()
s.headers.update({"User-Agent":"Mozilla/5.0 SmartCampania/1.0","Accept-Language":"it-IT,it;q=0.9"})

def walk(o):
    if isinstance(o, dict):
        yield o
        for v in o.values(): yield from walk(v)
    elif isinstance(o, list):
        for v in o: yield from walk(v)

def active(node, now_ms):
    if not node.get("pdfUrl") or not (node.get("title") or node.get("feTitle")):
        return False
    if node.get("visible") is False or node.get("valid") is False: return False
    a,b=node.get("validFrom"),node.get("validTo")
    if isinstance(a,(int,float)) and now_ms<a: return False
    if isinstance(b,(int,float)) and now_ms>b: return False
    return True

def contexts(text, term, radius_lines=5):
    lines=[re.sub(r"\s+"," ",x).strip() for x in text.splitlines()]
    hits=[]
    for i,line in enumerate(lines):
        if not WORD[term].search(line): continue
        lo=max(0,i-radius_lines); hi=min(len(lines),i+radius_lines+1)
        ctx=" | ".join(x for x in lines[lo:hi] if x)
        hits.append({"line":i+1,"context":ctx[:1800],"prices":PRICE.findall(ctx)})
    return hits

now_ms=int(datetime.now(timezone.utc).timestamp()*1000)
out={"checked_at":datetime.now(timezone.utc).isoformat(),"stores":{}}
pdf_cache={}

for store_id, name in STORES.items():
    api=f"https://www.conad.it/api/corporate/it-it.getPointOfServiceByAnacanId.json?anacanId={store_id}"
    row={"name":name,"api":api,"flyers":[],"errors":[]}
    try:
        r=s.get(api,timeout=45); r.raise_for_status(); payload=r.json()
        seen=set()
        for node in walk(payload):
            if not active(node,now_ms): continue
            pdf=node.get("pdfUrl")
            key=(node.get("title") or node.get("feTitle"),pdf)
            if key in seen: continue
            seen.add(key)
            item={
                "title":key[0],"pdf":pdf,"validFrom":node.get("validFrom"),
                "validTo":node.get("validTo"),"hits":{}
            }
            try:
                if pdf not in pdf_cache:
                    pr=s.get(pdf,timeout=120); pr.raise_for_status()
                    if not pr.content.startswith(b"%PDF"): raise RuntimeError("not pdf")
                    doc=fitz.open(stream=pr.content,filetype="pdf")
                    pages=[p.get_text("text") for p in doc]
                    pdf_cache[pdf]=pages
                pages=pdf_cache[pdf]
                for term in TERMS:
                    th=[]
                    for pn,text in enumerate(pages,1):
                        for h in contexts(text,term):
                            h["page"]=pn; th.append(h)
                    if th: item["hits"][term]=th
                item["pages"]=len(pages)
            except Exception as e:
                item["error"]=repr(e)
            row["flyers"].append(item)
    except Exception as e:
        row["errors"].append(repr(e))
    out["stores"][store_id]=row
    time.sleep(.3)

Path("conad_pac_current_aglio_cipolla.json").write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
summary=[]
for sid,row in out["stores"].items():
    hits=[]
    for f in row["flyers"]:
        for term,hs in f.get("hits",{}).items():
            hits.append({"flyer":f["title"],"term":term,"hits":len(hs),"examples":hs[:3]})
    summary.append({"store":sid,"name":row["name"],"active_flyers":len(row["flyers"]),"hits":hits,"errors":row["errors"]})
print(json.dumps(summary,ensure_ascii=False))
