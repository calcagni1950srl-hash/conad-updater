import json, sqlite3, re, urllib.request
from urllib.parse import urlencode

API='https://www.lidl.it/q/api/search'
PARAMS={'offset':0,'fetchsize':1000,'locale':'it_IT','assortment':'IT','version':'2.1.0','category.id':'10068374'}
url=API+'?'+urlencode(PARAMS)
req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0','Accept':'application/mindshift.search+json;version=2, application/json'})
with urllib.request.urlopen(req,timeout=60) as r:
    data=json.load(r)

def walk(x):
    if isinstance(x,dict):
        yield x
        for v in x.values(): yield from walk(v)
    elif isinstance(x,list):
        for v in x: yield from walk(v)

def pick(d,*keys):
    for k in keys:
        v=d.get(k)
        if v not in (None,'',[]): return v
    return None

def money(v):
    if isinstance(v,(int,float)): return float(v)
    if isinstance(v,str):
        m=re.search(r'(\d+[\.,]\d{1,2}|\d+)',v.replace('\xa0',' '))
        if m: return float(m.group(1).replace('.','').replace(',','.'))
    if isinstance(v,dict):
        for k in ('price','value','amount','current','salesPrice'):
            if k in v:
                z=money(v[k])
                if z: return z
    return None

# Product records in the search response contain canonical product urls/ids.
seen={}
for d in walk(data):
    pid=pick(d,'id','productId','product_id','sku')
    name=pick(d,'title','name','productName')
    link=pick(d,'url','canonicalUrl','canonical_url','productUrl')
    if not (name and (pid or link)): continue
    text=json.dumps(d,ensure_ascii=False)
    if '/p/' not in text and not str(pid).lower().startswith(('product','p')): continue
    price=None
    for k in ('price','salesPrice','currentPrice','priceInfo','pricing'):
        if k in d:
            price=money(d[k]);
            if price: break
    key=str(pid or link)
    old=seen.get(key)
    rec={'id':str(pid or ''),'name':str(name),'url':str(link or ''),'price':price,'raw':d}
    if old is None or (not old['price'] and price): seen[key]=rec

products=list(seen.values())
positive=[p for p in products if p['price'] and p['price']>0]
with open('lidl_api_raw.json','w',encoding='utf-8') as f: json.dump(data,f,ensure_ascii=False,indent=2)
with open('lidl_api_products.json','w',encoding='utf-8') as f: json.dump(products,f,ensure_ascii=False,indent=2)
con=sqlite3.connect('prezzi_lidl_api.db')
con.execute('DROP TABLE IF EXISTS products')
con.execute('CREATE TABLE products(id TEXT PRIMARY KEY,name TEXT,url TEXT,price REAL)')
for p in positive:
    con.execute('INSERT OR REPLACE INTO products VALUES(?,?,?,?)',(p['id'] or p['url'],p['name'],p['url'],p['price']))
con.commit(); con.close()
print(json.dumps({'numFound':data.get('numFound'),'fetchsize':data.get('fetchsize'),'maxfetchsize':data.get('maxfetchsize'),'candidate_products':len(products),'positive_price_products':len(positive)},ensure_ascii=False))
for p in positive[:20]: print(p['name'],p['price'],p['url'])
