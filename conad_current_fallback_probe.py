import json,re,requests
from bs4 import BeautifulSoup

UA={"User-Agent":"Mozilla/5.0","Accept-Language":"it-IT,it;q=0.9"}

SOURCES={
  "aglio":{
    "url":"https://gresy.shop/products/5056-01",
    "names":["CONAD Aglio macinato 37 GR","Aglio macinato 37"],
    "expected_ean":"80458920",
  },
  "prezzemolo":{
    "url":"https://glovoapp.com/it/it/milano/stores/conad-mil?content=ortofrutta-sc.35059660%2Faromi-e-spezie-c.35059199",
    "names":["PREZZEMOLO VASCHETTA CONAD P.Q. 50G","Prezzemolo Vaschetta Conad"],
  },
  "cipolla":{
    "url":"https://glovoapp.com/it/it/milano/stores/conad-mil?content=ortofrutta-sc.35059660%2Faromi-e-spezie-c.35059199",
    "names":["CONAD Cipolla Fiocchi 18 g","Cipolla Fiocchi 18 g"],
  }
}

def euro(s):
    m=re.search(r'(?:€\s*|)(\d{1,3}[,.]\d{2})(?:\s*€)?',s)
    return float(m.group(1).replace(",",".")) if m else None

def fetch_text(url):
    r=requests.get(url,headers=UA,timeout=60)
    r.raise_for_status()
    soup=BeautifulSoup(r.text,"html.parser")
    return r,soup.get_text(" ",strip=True),r.text

def find_price_near(text,names,window=800):
    low=text.lower()
    for name in names:
        i=low.find(name.lower())
        if i>=0:
            chunk=text[i:i+window]
            vals=[]
            for m in re.finditer(r'(\d{1,3}[,.]\d{2})\s*€|€\s*(\d{1,3}[,.]\d{2})',chunk):
                raw=m.group(1) or m.group(2)
                try:v=float(raw.replace(",","."))
                except:continue
                if 0.2 <= v <= 20: vals.append(v)
            if vals:
                return vals[0],name,chunk[:500]
    return None,None,None

out={}
for key,src in SOURCES.items():
    r,text,html=fetch_text(src["url"])
    price,name,chunk=find_price_near(text,src["names"])
    row={"url":src["url"],"status":r.status_code,"price":price,"matched_name":name,"chunk":chunk}
    if src.get("expected_ean"):
        row["ean_present"]=src["expected_ean"] in html or src["expected_ean"] in text
    out[key]=row

print(json.dumps(out,ensure_ascii=False))
if not all(v.get("price") and v["price"]>0 for v in out.values()):
    raise SystemExit("MISSING_PRICE")
if out["aglio"].get("ean_present") is not True:
    raise SystemExit("GARLIC_EAN_NOT_CONFIRMED")
open("conad_current_fallback_probe.json","w",encoding="utf-8").write(json.dumps(out,ensure_ascii=False,indent=2))
