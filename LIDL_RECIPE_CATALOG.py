import csv, json, re, sqlite3, time, unicodedata, urllib.request
from pathlib import Path
from urllib.parse import urlencode

API="https://www.lidl.it/q/api/search"
BASE={"locale":"it_IT","assortment":"IT","version":"2.1.0"}
HEADERS={"User-Agent":"Mozilla/5.0","Accept":"application/mindshift.search+json;version=2, application/json"}
RECIPES=Path("qa_v81/recipes_v61_157.tsv")
BASE_PRODUCTS=Path("lidl_api_products.json")
EXCLUDED=("fiori e piante","prodotti per la cura della persona","bilancio","articoli per animali domestici",
          "pulizia","bucato","casa","giardino","utensili","elettronica","abbigliamento")
STOP={"di","del","della","dei","degli","delle","con","per","alla","allo","alle","al","ai","da","in","e","o",
      "fresco","fresca","freschi","fresche","cotto","cotta","cotti","cotte","pulito","pulita","pronto","pronta"}

def fetch_q(q, offset=0, fetchsize=60):
    p=dict(BASE,q=q,offset=offset,fetchsize=fetchsize)
    req=urllib.request.Request(API+"?"+urlencode(p),headers=HEADERS)
    with urllib.request.urlopen(req,timeout=45) as r:
        return json.load(r)

def walk(x):
    if isinstance(x,dict):
        yield x
        for v in x.values(): yield from walk(v)
    elif isinstance(x,list):
        for v in x: yield from walk(v)

def pick(d,*keys):
    for k in keys:
        v=d.get(k)
        if v not in (None,"",[]): return v

def money(v):
    if isinstance(v,(int,float)): return float(v)
    if isinstance(v,str):
        m=re.search(r"(\d+[\.,]\d{1,2}|\d+)",v.replace("\xa0"," "))
        if m: return float(m.group(1).replace(",","."))
    if isinstance(v,dict):
        for k in ("price","value","amount","current","salesPrice"):
            if k in v:
                z=money(v[k])
                if z is not None: return z

def extract(data, seen, query):
    for d in walk(data):
        pid=pick(d,"id","productId","product_id","sku")
        name=pick(d,"title","name","productName")
        link=pick(d,"url","canonicalUrl","canonical_url","productUrl")
        if not (name and (pid or link)): continue
        text=json.dumps(d,ensure_ascii=False)
        if "/p/" not in text: continue
        price=None
        for k in ("price","salesPrice","currentPrice","priceInfo","pricing"):
            if k in d:
                price=money(d[k])
                if price is not None: break
        raw=d
        cat=str((raw.get("keyfacts") or {}).get("wonCategoryPrimary") or "")
        if any(x in cat.lower() for x in EXCLUDED): continue
        if not price or price<=0: continue
        pr=raw.get("price") or {}
        rec={
            "id":str(pid or ""),
            "name":str(name),
            "url":str(link or ""),
            "price":price,
            "packaging":str((pr.get("packaging") or {}).get("text") or "").strip(),
            "base_price":str((pr.get("basePrice") or {}).get("text") or "").strip(),
            "category_path":cat,
            "query_hits":[query],
            "raw":raw,
        }
        key=str(pid or link)
        old=seen.get(key)
        if old:
            if query not in old["query_hits"]: old["query_hits"].append(query)
            if (not old.get("packaging")) and rec.get("packaging"): old["packaging"]=rec["packaging"]
            if (not old.get("base_price")) and rec.get("base_price"): old["base_price"]=rec["base_price"]
        else:
            seen[key]=rec

def norm(s):
    s=unicodedata.normalize("NFKD",s.lower()).encode("ascii","ignore").decode()
    s=re.sub(r"[^a-z0-9 ]+"," ",s)
    return re.sub(r"\s+"," ",s).strip()

def query_variants(ingredient):
    s=norm(ingredient)
    out=[s]
    for part in re.split(r"\s+o\s+|\s+oppure\s+",s):
        part=part.strip()
        if part and part not in out: out.append(part)
    toks=[t for t in s.split() if len(t)>=4 and t not in STOP]
    # individual meaningful tokens help Lidl search surface staples omitted by long phrases
    for t in toks:
        if t not in out: out.append(t)
    # short noun-like phrase
    if len(toks)>=2:
        p=" ".join(toks[:2])
        if p not in out: out.append(p)
    return out[:5]

ingredients=[]
with RECIPES.open(encoding="utf-8",newline="") as f:
    for row in csv.DictReader(f,delimiter="\t"):
        x=(row.get("ingredient") or "").strip()
        if x and x not in ingredients: ingredients.append(x)

queries=[]
for ing in ingredients:
    for q in query_variants(ing):
        if q and q not in queries: queries.append(q)

seen={}
if BASE_PRODUCTS.exists():
    for x in json.loads(BASE_PRODUCTS.read_text(encoding="utf-8")):
        key=str(x.get("id") or x.get("url"))
        x=dict(x)
        x.setdefault("query_hits",["__base_category__"])
        seen[key]=x

errors=[]
query_stats=[]
for i,q in enumerate(queries,1):
    try:
        first=fetch_q(q,0,60)
        n=int(first.get("numFound") or 0)
        before=len(seen)
        extract(first,seen,q)
        # only paginate when a term genuinely returns more than one page
        for off in range(60,min(n,180),60):
            page=fetch_q(q,off,60)
            extract(page,seen,q)
        query_stats.append({"q":q,"numFound":n,"added":len(seen)-before})
    except Exception as e:
        errors.append({"q":q,"error":repr(e)})
    if i%25==0:
        print("PROGRESS",i,"/",len(queries),"products",len(seen),"errors",len(errors))
    time.sleep(0.03)

products=list(seen.values())
products.sort(key=lambda x:(x.get("name","").lower(),x.get("price",0)))
Path("lidl_recipe_products.json").write_text(json.dumps(products,ensure_ascii=False,indent=2),encoding="utf-8")
summary={
    "ingredients":len(ingredients),
    "queries":len(queries),
    "products_positive":len(products),
    "with_packaging":sum(bool(x.get("packaging")) for x in products),
    "with_base_price":sum(bool(x.get("base_price")) for x in products),
    "errors":len(errors),
    "top_queries":sorted(query_stats,key=lambda x:(x["added"],x["numFound"]),reverse=True)[:30],
}
Path("lidl_recipe_catalog_summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
Path("lidl_recipe_query_errors.json").write_text(json.dumps(errors,ensure_ascii=False,indent=2),encoding="utf-8")
con=sqlite3.connect("prezzi_lidl_recipe.db")
con.execute("DROP TABLE IF EXISTS products")
con.execute("CREATE TABLE products(id TEXT PRIMARY KEY,name TEXT,url TEXT,price REAL,packaging TEXT,base_price TEXT,category_path TEXT)")
for p in products:
    con.execute("INSERT OR REPLACE INTO products VALUES(?,?,?,?,?,?,?)",
                (p.get("id") or p.get("url"),p.get("name"),p.get("url"),p.get("price"),
                 p.get("packaging",""),p.get("base_price",""),p.get("category_path","")))
con.commit(); con.close()
print(json.dumps(summary,ensure_ascii=False))
