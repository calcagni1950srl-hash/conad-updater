import json, urllib.request, urllib.parse, re

API="https://www.lidl.it/q/api/search"
BASE={"locale":"it_IT","assortment":"IT","version":"2.1.0","offset":0,"fetchsize":20}
HEADERS={"User-Agent":"Mozilla/5.0","Accept":"application/mindshift.search+json;version=2, application/json"}
TERMS=["spaghetti","olio extravergine","passata di pomodoro","zucchine","uova","latte","petto di pollo"]
PARAMS=["query","q","search","term","keyword","searchTerm","text"]

def get(params):
    req=urllib.request.Request(API+"?"+urllib.parse.urlencode(params),headers=HEADERS)
    try:
        with urllib.request.urlopen(req,timeout=30) as r:
            return r.status,json.load(r)
    except Exception as e:
        return None,{"error":repr(e)}

def names(x):
    out=[]
    def walk(v):
        if isinstance(v,dict):
            n=v.get("title") or v.get("name") or v.get("productName")
            u=v.get("url") or v.get("canonicalUrl") or v.get("productUrl")
            if n and (u or v.get("id")) and "/p/" in json.dumps(v,ensure_ascii=False):
                s=str(n)
                if s not in out: out.append(s)
            for z in v.values(): walk(z)
        elif isinstance(v,list):
            for z in v: walk(z)
    walk(x)
    return out[:10]

rows=[]
st,base=get(BASE)
rows.append({"term":"__BASE__","param":"","status":st,"numFound":base.get("numFound"),"names":names(base)})
for term in TERMS:
    for p in PARAMS:
        params=dict(BASE); params[p]=term
        st,x=get(params)
        rows.append({"term":term,"param":p,"status":st,"numFound":x.get("numFound"),"names":names(x),"error":x.get("error")})
    # also test category-free query page HTML for embedded product hints
    url="https://www.lidl.it/q/search?"+urllib.parse.urlencode({"q":term})
    try:
        req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0"})
        with urllib.request.urlopen(req,timeout=30) as r:
            html=r.read().decode("utf-8","ignore")
        urls=sorted(set(re.findall(r'href=["\\\']([^"\\\']+/p/[^"\\\']+)["\\\']', html, re.I)))
        if not urls:
            urls=sorted(set(re.findall(r"/p/[^\\\"'<>\\s]+", html)))
        rows.append({"term":term,"param":"PAGE","status":200,"html_len":len(html),
                     "has_term":term.lower() in html.lower(),
                     "product_urls":len(urls),
                     "urls":urls[:20]})
    except Exception as e:
        rows.append({"term":term,"param":"PAGE","error":repr(e)})
with open("lidl_search_term_probe.json","w",encoding="utf-8") as f:
    json.dump(rows,f,ensure_ascii=False,indent=2)
for r in rows:
    print(json.dumps(r,ensure_ascii=False))
