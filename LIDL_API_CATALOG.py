import json, sqlite3, re, urllib.request
from urllib.parse import urlencode

API='https://www.lidl.it/q/api/search'
BASE={'locale':'it_IT','assortment':'IT','version':'2.1.0','category.id':'10068374'}
HEADERS={'User-Agent':'Mozilla/5.0','Accept':'application/mindshift.search+json;version=2, application/json'}

def fetch(offset, fetchsize=60):
    params=dict(BASE,offset=offset,fetchsize=fetchsize)
    req=urllib.request.Request(API+'?'+urlencode(params),headers=HEADERS)
    with urllib.request.urlopen(req,timeout=60) as r: return json.load(r)

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

def money(v):
    if isinstance(v,(int,float)): return float(v)
    if isinstance(v,str):
        m=re.search(r'(\d+[\.,]\d{1,2}|\d+)',v.replace('\xa0',' '))
        if m: return float(m.group(1).replace(',','.'))
    if isinstance(v,dict):
        for k in ('price','value','amount','current','salesPrice'):
            if k in v:
                z=money(v[k])
                if z is not None: return z

def extract(data, seen):
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
                price=money(d[k])
                if price is not None: break
        key=str(pid or link)
        rec={'id':str(pid or ''),'name':str(name),'url':str(link or ''),'price':price,'raw':d}
        old=seen.get(key)
        if old is None or (not old['price'] and price): seen[key]=rec

first=fetch(0,60)
num=int(first.get('numFound') or 0)
pages=[first]
seen={}; extract(first,seen)
for offset in range(60,num,60):
    page=fetch(offset,60); pages.append(page); extract(page,seen)

products=list(seen.values())
positive=[p for p in products if p['price'] and p['price']>0]
NONFOOD=re.compile(r'cactus|kalanchoe|sanseveria|succulent|piant|vaso|bucato|detersiv|profumat|gel ultimate',re.I)
food=[p for p in positive if not NONFOOD.search(p['name'])]
with open('lidl_api_raw.json','w',encoding='utf-8') as f: json.dump(pages,f,ensure_ascii=False,indent=2)
with open('lidl_api_products.json','w',encoding='utf-8') as f: json.dump(food,f,ensure_ascii=False,indent=2)
con=sqlite3.connect('prezzi_lidl_api.db')
con.execute('DROP TABLE IF EXISTS products')
con.execute('CREATE TABLE products(id TEXT PRIMARY KEY,name TEXT,url TEXT,price REAL)')
for p in food: con.execute('INSERT OR REPLACE INTO products VALUES(?,?,?,?)',(p['id'] or p['url'],p['name'],p['url'],p['price']))
con.commit(); con.close()
print(json.dumps({'numFound':num,'pages':len(pages),'candidate_products':len(products),'positive_price_products':len(positive),'food_positive_products':len(food)},ensure_ascii=False))
for p in food[:30]: print(p['name'],p['price'],p['url'])
