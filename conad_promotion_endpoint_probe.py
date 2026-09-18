import json,re,requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

BASE="https://www.conad.it/ricerca-negozi/conad-superstore-via-retella-ex-giarddel-sole-snc-81020-capodrise--010548"
KEYS=[
 "promozioni-nazionali","promozioni-locali","promoDetails","promodetails",
 "nationalPromo","localPromo","disaggregatedId","hasDisaggregated",
 "disaggregato","PPN19","getProm","promotion","promotions","offerte",
 "anacanId","getPointOfServiceByAnacanId"
]
s=requests.Session()
s.headers.update({"User-Agent":"Mozilla/5.0","Accept-Language":"it-IT,it;q=0.9"})
r=s.get(BASE,timeout=45); r.raise_for_status()
soup=BeautifulSoup(r.text,"html.parser")
scripts=[]
for tag in soup.find_all("script",src=True):
    u=urljoin(BASE,tag["src"])
    if "conad.it" in u and u not in scripts: scripts.append(u)
out={"scripts":scripts,"matches":[],"urls":[],"errors":[]}
url_rx=re.compile(r'["\']([^"\']*(?:promo|offert|volantin|catalog)[^"\']*)["\']',re.I)
for u in scripts:
    try:
        rr=s.get(u,timeout=45); txt=rr.text
        found=[]
        low=txt.lower()
        for key in KEYS:
            start=0
            while True:
                i=low.find(key.lower(),start)
                if i<0: break
                found.append({"key":key,"context":txt[max(0,i-2500):min(len(txt),i+5000)]})
                start=i+len(key)
                if sum(1 for x in found if x["key"]==key)>=12: break
        if found: out["matches"].append({"script":u,"found":found})
        for x in url_rx.findall(txt):
            x=x.replace("\\/","/")
            if len(x)<500 and x not in out["urls"]: out["urls"].append(x)
    except Exception as e:
        out["errors"].append({"script":u,"error":repr(e)})
open("conad_promotion_endpoint_probe.json","w",encoding="utf-8").write(json.dumps(out,ensure_ascii=False,indent=2))
print(json.dumps({"scripts":len(scripts),"matched_scripts":len(out["matches"]),"urls":out["urls"][:100],"errors":out["errors"]},ensure_ascii=False))
