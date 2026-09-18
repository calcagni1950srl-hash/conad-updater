import requests,re,json,html
from urllib.parse import urlencode

TERMS=["aglio","cipolla","prezzemolo"]
VARIANTS=[
    ("plain",{}),
    ("anacanId",{"anacanId":"010548"}),
    ("storeId",{"storeId":"010548"}),
    ("pointOfServiceId",{"pointOfServiceId":"010548"}),
    ("pos",{"pos":"010548"}),
    ("anacan-store",{"anacanId":"010548","storeId":"010548"}),
]
RX=re.compile(r'data-product="([^"]+)"')
s=requests.Session()
s.headers.update({"User-Agent":"Mozilla/5.0","Accept-Language":"it-IT,it;q=0.9"})
out={}
for term in TERMS:
    out[term]={}
    for label,params in VARIANTS:
        q={"query":term,**params}
        url="https://spesaonline.conad.it/search?"+urlencode(q)
        r=s.get(url,timeout=45)
        cards=[]
        for raw in RX.findall(r.text):
            try:p=json.loads(html.unescape(raw))
            except Exception: continue
            try:price=float(p.get("basePrice") or 0)
            except Exception:price=0
            cards.append({"code":p.get("code"),"name":p.get("nome"),"price":price,"qty":p.get("netQuantity"),"unit":p.get("netQuantityUm")})
        out[term][label]={
            "url":url,"status":r.status_code,"cards":len(cards),
            "positive":[x for x in cards if x["price"]>0],
            "contains_010548":"010548" in r.text,
        }
print(json.dumps(out,ensure_ascii=False))
open("conad_public_store_param_probe.json","w",encoding="utf-8").write(json.dumps(out,ensure_ascii=False,indent=2))
