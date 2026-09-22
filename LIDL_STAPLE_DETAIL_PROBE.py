import json, urllib.request
from urllib.parse import urlencode
API="https://www.lidl.it/q/api/search"
BASE={"locale":"it_IT","assortment":"IT","version":"2.1.0","offset":0,"fetchsize":20}
HEADERS={"User-Agent":"Mozilla/5.0","Accept":"application/mindshift.search+json;version=2, application/json"}
TERMS=["zucchine","uova","olio extravergine","spaghetti","passata di pomodoro","petto di pollo","mozzarella","burro"]

def fetch(q):
    p=dict(BASE,q=q)
    req=urllib.request.Request(API+"?"+urlencode(p),headers=HEADERS)
    with urllib.request.urlopen(req,timeout=30) as r:
        return json.load(r)

def walk(x):
    if isinstance(x,dict):
        yield x
        for v in x.values(): yield from walk(v)
    elif isinstance(x,list):
        for v in x: yield from walk(v)

for term in TERMS:
    data=fetch(term)
    print("TERM",term,"numFound",data.get("numFound"))
    seen=set()
    for d in walk(data):
        name=d.get("title") or d.get("name") or d.get("productName")
        url=d.get("url") or d.get("canonicalUrl") or d.get("productUrl")
        if not name or not url or "/p/" not in str(url): continue
        key=(name,url)
        if key in seen: continue
        seen.add(key)
        price=d.get("price")
        cat=((d.get("keyfacts") or {}).get("wonCategoryPrimary"))
        print(json.dumps({"name":name,"url":url,"price":price,"category":cat},ensure_ascii=False))
        if len(seen)>=8: break
