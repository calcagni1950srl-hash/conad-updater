import json, urllib.request, urllib.parse, re

BASE="https://www.lidl.it"
HEADERS={"User-Agent":"Mozilla/5.0","Accept":"*/*"}

def get_text(url):
    req=urllib.request.Request(url,headers=HEADERS)
    with urllib.request.urlopen(req,timeout=30) as r:
        return r.status,r.read().decode("utf-8","ignore"),dict(r.headers)

def get_json(url):
    req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0","Accept":"application/json, application/mindshift.search+json;version=2, */*"})
    with urllib.request.urlopen(req,timeout=30) as r:
        raw=r.read().decode("utf-8","ignore")
        try: data=json.loads(raw)
        except Exception: data={"_raw":raw[:5000]}
        return r.status,data,dict(r.headers)

# 1) Exact browser-visible search term
params={"locale":"it_IT","assortment":"IT","version":"2.1.0","offset":0,"fetchsize":48,"q":"pomodoro"}
url=BASE+"/q/api/search?"+urllib.parse.urlencode(params)
st,data,h=get_json(url)
print("SEARCH_POMODORO_STATUS",st)
print("SEARCH_POMODORO_NUMFOUND",data.get("numFound"))
print("SEARCH_POMODORO_TOPLEVEL",sorted(data.keys()) if isinstance(data,dict) else type(data).__name__)

# recursively collect product-like dicts
products=[]
def walk(v):
    if isinstance(v,dict):
        name=v.get("name") or v.get("title") or v.get("productName")
        u=v.get("url") or v.get("productUrl") or v.get("canonicalUrl")
        if name and (u or v.get("id")) and "/p/" in json.dumps(v,ensure_ascii=False):
            products.append(v)
        for z in v.values(): walk(z)
    elif isinstance(v,list):
        for z in v: walk(z)
walk(data)

seen=set()
for p in products:
    key=(str(p.get("id")),str(p.get("url") or p.get("productUrl") or p.get("canonicalUrl")))
    if key in seen: continue
    seen.add(key)
    print("SEARCH_PRODUCT",json.dumps({
      "id":p.get("id"),"name":p.get("name") or p.get("title") or p.get("productName"),
      "url":p.get("url") or p.get("productUrl") or p.get("canonicalUrl"),
      "price":p.get("price"),"category":p.get("category")
    },ensure_ascii=False))

# 2) inspect JS around gridboxes usage
st,js,_=get_text(BASE+"/p/fragment/fragment.0b0d5b2b.mjs")
print("FRAGMENT_JS_STATUS",st,"LEN",len(js))
for token in ["gridboxes","gridboxesPath","/q/api/gridboxes"]:
    pos=0
    while True:
        i=js.find(token,pos)
        if i<0: break
        print("JS_CONTEXT",token,re.sub(r"\s+"," ",js[max(0,i-1200):i+2200])[:3500])
        pos=i+len(token)

# 3) Probe plausible gridbox call forms using IDs/URLs discovered by search
ids=[]
for p in products:
    x=p.get("id")
    if x is not None and str(x) not in ids: ids.append(str(x))
print("DISCOVERED_IDS",ids[:20])

candidates=[]
if ids:
    first=ids[0]
    joined=",".join(ids[:10])
    candidates += [
      {"ids":joined},
      {"id":first},
      {"productIds":joined},
      {"productId":first},
      {"ids[]":joined},
    ]
for qp in candidates:
    base={"locale":"it_IT","assortment":"IT","version":"2.1.0"}
    base.update(qp)
    u=BASE+"/q/api/gridboxes?"+urllib.parse.urlencode(base)
    try:
        st,x,_=get_json(u)
        print("GRIDBOX_TRY",json.dumps({"params":qp,"status":st,"type":type(x).__name__,"preview":x if isinstance(x,dict) else str(x)[:2000]},ensure_ascii=False)[:5000])
    except Exception as e:
        print("GRIDBOX_ERROR",json.dumps({"params":qp,"error":repr(e)},ensure_ascii=False))
