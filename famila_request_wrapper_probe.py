import json
import requests

URL="https://www.cosicomodo.it/_next/static/chunks/662-3b2fe6b3d225f1c7.js"
NEEDLES=["validate-search","baseURL","api.cosicomodo","static.cosicomodo","Authorization","axios","occ/v2","customValidate","isAnonymous"]
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/152 Safari/537.36"
text=requests.get(URL,headers={"User-Agent":UA},timeout=60).text

def snips(needle,r=900,maxn=12):
    out=[]; lo=text.lower(); nd=needle.lower(); pos=0
    while len(out)<maxn:
        p=lo.find(nd,pos)
        if p<0: break
        out.append(text[max(0,p-r):min(len(text),p+len(needle)+r)])
        pos=p+len(needle)
    return out

res={n:snips(n) for n in NEEDLES}
print(json.dumps(res,ensure_ascii=False,indent=2))
