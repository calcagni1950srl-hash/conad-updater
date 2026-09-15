import json, re
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

PAGE = "https://www.cosicomodo.it/familasud/teverola/reparti/prodotti-alimentari/c/10012"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/152 Safari/537.36"
NEEDLES = ["search-by-category", "currentPage", "categoryCode", "searchPageData", "pageSize", "products/search"]


def clean(s): return " ".join((s or "").split())

def snippets(text, needle, radius=350, maxn=8):
    out=[]; low=text.lower(); nlow=needle.lower(); start=0
    while len(out)<maxn:
        p=low.find(nlow,start)
        if p<0: break
        out.append(clean(text[max(0,p-radius):min(len(text),p+len(needle)+radius)]))
        start=p+len(needle)
    return out

s=requests.Session()
r=s.get(PAGE,headers={"User-Agent":UA},timeout=60); r.raise_for_status()
soup=BeautifulSoup(r.text,"html.parser")
results=[]
for tag in soup.find_all("script",src=True):
    src=urljoin(PAGE,str(tag.get("src")))
    try:
        rr=s.get(src,headers={"User-Agent":UA},timeout=60)
        if rr.status_code!=200: continue
        text=rr.text
        hits={n:snippets(text,n) for n in NEEDLES}
        if any(hits.values()):
            results.append({"src":src,"length":len(text),"hits":hits})
    except Exception as e:
        results.append({"src":src,"error":repr(e)})
print(json.dumps(results,ensure_ascii=False,indent=2))
if not any(any(v for v in x.get("hits",{}).values()) for x in results):
    raise SystemExit("NO_JS_ENDPOINT_HINTS")
