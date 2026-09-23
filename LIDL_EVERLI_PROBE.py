import json, urllib.request
from urllib.parse import urlencode

BASE="https://lidl.everli.com/api/search"
STORE_ID="9240"
HEADERS={"User-Agent":"Mozilla/5.0","Accept":"application/json"}

def fetch(keyword, skip=0, take=40):
    qs=urlencode({"keyword":keyword,"skip":skip,"take":take,"brands":"","tags":"","store_ids":STORE_ID})
    req=urllib.request.Request(BASE+"?"+qs,headers=HEADERS)
    with urllib.request.urlopen(req,timeout=45) as r:
        return json.load(r)

queries=["spaghetti","pasta","olio","passata","aglio","zucchine"]
out={}
for q in queries:
    try:
        data=fetch(q)
        out[q]=data
        print("EVERLI_QUERY",q,"TYPE",type(data).__name__,"LEN",len(data) if hasattr(data,"__len__") else "?")
        print(json.dumps(data,ensure_ascii=False)[:2500])
    except Exception as e:
        out[q]={"error":repr(e)}
        print("EVERLI_ERROR",q,repr(e))

with open("lidl_everli_probe.json","w",encoding="utf-8") as f:
    json.dump(out,f,ensure_ascii=False,indent=2)
