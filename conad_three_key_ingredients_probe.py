import requests,re,json,html
from urllib.parse import quote_plus

TERMS=["aglio","cipolla","prezzemolo"]
RX=re.compile(r'data-product="([^"]+)"')
s=requests.Session()
s.headers.update({"User-Agent":"Mozilla/5.0","Accept-Language":"it-IT,it;q=0.9"})
out={}
for term in TERMS:
    url="https://spesaonline.conad.it/search?query="+quote_plus(term)
    r=s.get(url,timeout=45)
    cards=[]
    for raw in RX.findall(r.text):
        try:
            p=json.loads(html.unescape(raw))
        except Exception:
            continue
        try:
            price=float(p.get("basePrice") or 0)
        except Exception:
            price=0
        cards.append({
            "code":p.get("code"),
            "name":p.get("nome"),
            "price":price,
            "qty":p.get("netQuantity"),
            "unit":p.get("netQuantityUm"),
            "cat1":p.get("categoriaPrimoLivello"),
            "cat2":p.get("categoriaSecondoLivello"),
        })
    out[term]={
        "url":url,
        "status":r.status_code,
        "cards":len(cards),
        "positive":[x for x in cards if x["price"]>0],
        "all":cards[:80],
    }
print(json.dumps(out,ensure_ascii=False))
open("conad_three_key_ingredients_probe.json","w",encoding="utf-8").write(json.dumps(out,ensure_ascii=False,indent=2))
