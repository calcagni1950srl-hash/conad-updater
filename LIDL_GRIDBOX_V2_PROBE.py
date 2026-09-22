import json, urllib.request, urllib.parse, re

BASE="https://www.lidl.it"
HEADERS={"User-Agent":"Mozilla/5.0","Accept":"application/json,*/*"}
TERMS=["pomodoro","zucchine","spaghetti","olio extravergine","passata di pomodoro","uova","latte","petto di pollo"]

def get_json(url):
    req=urllib.request.Request(url,headers=HEADERS)
    with urllib.request.urlopen(req,timeout=30) as r:
        raw=r.read().decode("utf-8","ignore")
        try: return r.status,json.loads(raw)
        except Exception: return r.status,{"_raw":raw[:5000]}

def products(v):
    out=[]
    def walk(x):
        if isinstance(x,dict):
            n=x.get("name") or x.get("title") or x.get("productName")
            u=x.get("url") or x.get("canonicalUrl") or x.get("productUrl")
            if n and u and "/p/" in u: out.append(x)
            for z in x.values(): walk(z)
        elif isinstance(x,list):
            for z in x: walk(z)
    walk(v)
    seen=set(); uniq=[]
    for p in out:
        u=p.get("url") or p.get("canonicalUrl") or p.get("productUrl")
        if u not in seen:
            seen.add(u); uniq.append(p)
    return uniq

erp=[]
for term in TERMS:
    qs={"locale":"it_IT","assortment":"IT","version":"2.1.0","offset":0,"fetchsize":48,"q":term}
    st,data=get_json(BASE+"/q/api/search?"+urllib.parse.urlencode(qs))
    ps=products(data)
    print("TERM",term,"STATUS",st,"NUMFOUND",data.get("numFound"),"PRODUCTS",len(ps))
    for p in ps:
        u=p.get("url") or p.get("canonicalUrl") or p.get("productUrl")
        m=re.search(r"/p(\d+)(?:$|[/?#])",u or "")
        pid=m.group(1) if m else None
        print("SEARCH",json.dumps({"name":p.get("name") or p.get("title"),"url":u,"pid":pid,"price":p.get("price"),"category":p.get("category")},ensure_ascii=False))
        if pid: erp.append(pid)

erp=list(dict.fromkeys(erp))
print("ERP_FROM_URLS",erp[:100])

for batch_start in range(0,min(len(erp),60),20):
    batch=erp[batch_start:batch_start+20]
    for country,lang in [("IT","it"),("it","IT"),("it","it"),("IT","IT")]:
        url=f"{BASE}/p/api/gridboxes/v2/{country}/{lang}?"+urllib.parse.urlencode({"erpNumbers":",".join(batch)})
        try:
            st,data=get_json(url)
            print("GRIDV2",country,lang,"STATUS",st,"BATCH",batch)
            print(json.dumps(data,ensure_ascii=False)[:12000])
        except Exception as e:
            print("GRIDV2_ERROR",country,lang,repr(e))
