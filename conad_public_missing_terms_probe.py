import requests,re,json,html
from urllib.parse import quote_plus

terms=["aglio","prezzemolo","cipolla","peperoncino","pangrattato","origano","paccheri","melanzane","burro","sedano","pepe nero","vongole","cozze","calamari","provola","friarielli","broccoli","cavolfiore","finocchi","ziti","scialatielli"]
rx=re.compile(r'data-product="([^"]+)"')
s=requests.Session()
s.headers.update({"User-Agent":"Mozilla/5.0","Accept-Language":"it-IT,it;q=0.9"})
out={}
for term in terms:
    url="https://spesaonline.conad.it/search?query="+quote_plus(term)
    rr=s.get(url,timeout=45)
    cards=[]
    for raw in rx.findall(rr.text):
        try:
            p=json.loads(html.unescape(raw))
        except Exception:
            continue
        try: price=float(p.get("basePrice") or 0)
        except Exception: price=0
        if price>0:
            cards.append({"name":p.get("nome"),"price":price,"code":p.get("code"),"cat1":p.get("categoriaPrimoLivello"),"cat2":p.get("categoriaSecondoLivello")})
    out[term]={"http":rr.status_code,"positive":len(cards),"samples":cards[:8]}
print(json.dumps(out,ensure_ascii=False))
