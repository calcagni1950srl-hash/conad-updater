import csv, json, re, sqlite3, time, unicodedata, urllib.request
from pathlib import Path
from urllib.parse import urlencode
from datetime import datetime, timezone

API="https://www.lidl.it/q/api/search"
GRID="https://www.lidl.it/p/api/gridboxes/v2/IT/it"
BASE={"locale":"it_IT","assortment":"IT","version":"2.1.0"}
HEADERS={"User-Agent":"Mozilla/5.0","Accept":"application/mindshift.search+json;version=2, application/json, */*"}
RECIPES=Path("qa_v81/recipes_v61_157.tsv")
BASE_PRODUCTS=Path("lidl_api_products.json")
EXCLUDED=("fiori e piante","prodotti per la cura della persona","bilancio","articoli per animali domestici",
          "pulizia","bucato","casa","giardino","utensili","elettronica","abbigliamento")
STOP={"di","del","della","dei","degli","delle","con","per","alla","allo","alle","al","ai","da","in","e","o",
      "fresco","fresca","freschi","fresche","cotto","cotta","cotti","cotte","pulito","pulita","pronto","pronta"}

def get_json(url, accept=None):
    h=dict(HEADERS)
    if accept: h["Accept"]=accept
    req=urllib.request.Request(url,headers=h)
    with urllib.request.urlopen(req,timeout=45) as r:
        return json.load(r)

def fetch_q(q, offset=0, fetchsize=60):
    p=dict(BASE,q=q,offset=offset,fetchsize=fetchsize)
    return get_json(API+"?"+urlencode(p))

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

def pid_from_url(url):
    m=re.search(r"/p(\d+)(?:$|[/?#])",str(url or ""))
    return m.group(1) if m else ""

def dt(v):
    if not v: return None
    try:
        return datetime.fromisoformat(str(v).replace("Z","+00:00"))
    except Exception:
        return None

def active_between(start,end,now):
    s,e=dt(start),dt(end)
    if s and now < s: return False
    if e and now >= e: return False
    return True

def price_fields(pr):
    pr=pr or {}
    return {
        "price": money(pr.get("price")),
        "packaging": str((pr.get("packaging") or {}).get("text") or "").strip(),
        "base_price": str((pr.get("basePrice") or {}).get("text") or "").strip(),
        "start": pr.get("startDate"),
        "end": pr.get("endDateExclusive") or pr.get("endDate"),
        "old_price": money(pr.get("oldPrice")),
    }

def extract_candidates(data, seen, query):
    for d in walk(data):
        name=pick(d,"title","name","productName")
        link=pick(d,"url","canonicalUrl","canonical_url","productUrl")
        pid=pid_from_url(link) or pick(d,"erpNumber","productId","product_id","sku","id")
        if not (name and (pid or link)): continue
        if "/p/" not in json.dumps(d,ensure_ascii=False): continue
        cat=str((d.get("keyfacts") or {}).get("wonCategoryPrimary") or d.get("categoryPath") or "")
        if any(x in cat.lower() for x in EXCLUDED): continue
        pr=price_fields(d.get("price") or {})
        key=str(pid or link)
        rec=seen.get(key)
        if not rec:
            rec={
                "id":str(pid or ""),"name":str(name),"url":str(link or ""),
                "price":pr["price"],"packaging":pr["packaging"],"base_price":pr["base_price"],
                "category_path":cat,"query_hits":[query],"raw":d,
                "price_source":"search" if pr["price"] else "",
                "lidl_plus_price":None,"lidl_plus_packaging":"","lidl_plus_active":False,
            }
            seen[key]=rec
        else:
            if query not in rec["query_hits"]: rec["query_hits"].append(query)
            if not rec.get("packaging") and pr["packaging"]: rec["packaging"]=pr["packaging"]
            if not rec.get("base_price") and pr["base_price"]: rec["base_price"]=pr["base_price"]
            if not rec.get("price") and pr["price"]:
                rec["price"]=pr["price"]; rec["price_source"]="search"
            if not rec.get("category_path") and cat: rec["category_path"]=cat

def enrich_gridboxes(seen):
    ids=[k for k,v in seen.items() if str(v.get("id") or "").isdigit()]
    now=datetime.now(timezone.utc)
    stats={"requested":0,"returned":0,"standard_added":0,"packaging_added":0,"lidl_plus_seen":0,"future_only":0}
    for pos in range(0,len(ids),20):
        batch=ids[pos:pos+20]
        if not batch: continue
        stats["requested"]+=len(batch)
        try:
            data=get_json(GRID+"?"+urlencode({"erpNumbers":",".join(batch)}),"application/json,*/*")
        except Exception:
            continue
        if not isinstance(data,list): continue
        stats["returned"]+=len(data)
        for row in data:
            gb=(row or {}).get("gridBox") or {}
            pid=str(gb.get("erpNumber") or gb.get("productId") or gb.get("itemId") or "")
            rec=seen.get(pid)
            if not rec: continue
            cat=str(((gb.get("keyfacts") or {}).get("wonCategoryPrimary")) or rec.get("category_path") or "")
            if any(x in cat.lower() for x in EXCLUDED): continue
            rec["category_path"]=cat
            rec["gridbox_raw"]=gb
            # Standard/current price: prefer top-level gridBox price, then region currentPrice.
            std=price_fields(gb.get("price") or {})
            chosen=None
            if std["price"] and active_between(std["start"],std["end"],now):
                chosen=std
            if not chosen:
                rp=gb.get("regionsPrices") or {}
                for bucket in rp.values():
                    cur=price_fields((bucket or {}).get("currentPrice") or {})
                    if cur["price"] and active_between(cur["start"],cur["end"],now):
                        chosen=cur; break
            if chosen:
                if not rec.get("price"):
                    rec["price"]=chosen["price"]; rec["price_source"]="gridbox_current"; stats["standard_added"]+=1
                if not rec.get("packaging") and chosen["packaging"]:
                    rec["packaging"]=chosen["packaging"]; stats["packaging_added"]+=1
                if not rec.get("base_price") and chosen["base_price"]: rec["base_price"]=chosen["base_price"]
            # Lidl Plus is stored separately and never substituted for standard price.
            plus_candidates=[]
            for x in gb.get("lidlPlus") or []:
                pf=price_fields((x or {}).get("price") or {})
                if pf["price"]: plus_candidates.append(pf)
            for bucket in (gb.get("regionsPrices") or {}).values():
                pf=price_fields((bucket or {}).get("currentLidlPlusPrice") or {})
                if pf["price"]: plus_candidates.append(pf)
            active_plus=[x for x in plus_candidates if active_between(x["start"],x["end"],now)]
            if active_plus:
                p=min(active_plus,key=lambda x:x["price"])
                rec["lidl_plus_price"]=p["price"]; rec["lidl_plus_packaging"]=p["packaging"]; rec["lidl_plus_active"]=True
                stats["lidl_plus_seen"]+=1
                if not rec.get("packaging") and p["packaging"]:
                    rec["packaging"]=p["packaging"]; stats["packaging_added"]+=1
            # Packaging from a future offer is structurally safe to use, price is not.
            if not rec.get("packaging"):
                candidates=[]
                for bucket in (gb.get("regionsPrices") or {}).values():
                    for key in ("currentPrice","currentLidlPlusPrice"):
                        pf=price_fields((bucket or {}).get(key) or {})
                        if pf["packaging"]: candidates.append(pf["packaging"])
                    for key in ("futurePrices","futureLidlPlusPrices"):
                        for z in (bucket or {}).get(key) or []:
                            pf=price_fields((z or {}).get("price") if isinstance(z,dict) and "price" in z else z)
                            if pf["packaging"]: candidates.append(pf["packaging"])
                for x in gb.get("lidlPlus") or []:
                    pf=price_fields((x or {}).get("price") or {})
                    if pf["packaging"]: candidates.append(pf["packaging"])
                if candidates:
                    rec["packaging"]=candidates[0]; stats["packaging_added"]+=1
            if not rec.get("price") and (plus_candidates or gb.get("storeStartDate")):
                stats["future_only"]+=1
        time.sleep(0.03)
    return stats

def norm(s):
    s=unicodedata.normalize("NFKD",s.lower()).encode("ascii","ignore").decode()
    s=re.sub(r"[^a-z0-9 ]+"," ",s)
    return re.sub(r"\s+"," ",s).strip()

def query_variants(ingredient):
    s=norm(ingredient); out=[s]
    for part in re.split(r"\s+o\s+|\s+oppure\s+",s):
        part=part.strip()
        if part and part not in out: out.append(part)
    toks=[t for t in s.split() if len(t)>=4 and t not in STOP]
    for t in toks:
        if t not in out: out.append(t)
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
        x=dict(x)
        pid=str(x.get("id") or pid_from_url(x.get("url")) or "")
        x["id"]=pid
        x.setdefault("query_hits",["__base_category__"])
        x.setdefault("price_source","base_search")
        x.setdefault("lidl_plus_price",None); x.setdefault("lidl_plus_packaging",""); x.setdefault("lidl_plus_active",False)
        seen[pid or str(x.get("url"))]=x

errors=[]; query_stats=[]
for i,q in enumerate(queries,1):
    try:
        first=fetch_q(q,0,60); n=int(first.get("numFound") or 0); before=len(seen)
        extract_candidates(first,seen,q)
        for off in range(60,min(n,180),60):
            extract_candidates(fetch_q(q,off,60),seen,q)
        query_stats.append({"q":q,"numFound":n,"added":len(seen)-before})
    except Exception as e:
        errors.append({"q":q,"error":repr(e)})
    if i%25==0: print("PROGRESS",i,"/",len(queries),"candidates",len(seen),"errors",len(errors))
    time.sleep(0.03)

grid_stats=enrich_gridboxes(seen)
products=[x for x in seen.values() if x.get("price") and float(x["price"])>0]
products.sort(key=lambda x:(x.get("name","").lower(),x.get("price",0)))
Path("lidl_recipe_products.json").write_text(json.dumps(products,ensure_ascii=False,indent=2),encoding="utf-8")
Path("lidl_recipe_candidates_enriched.json").write_text(json.dumps(list(seen.values()),ensure_ascii=False,indent=2),encoding="utf-8")
summary={
    "ingredients":len(ingredients),"queries":len(queries),"candidates":len(seen),
    "products_positive_current_standard":len(products),
    "with_packaging":sum(bool(x.get("packaging")) for x in products),
    "with_base_price":sum(bool(x.get("base_price")) for x in products),
    "active_lidl_plus_separate":sum(bool(x.get("lidl_plus_active")) for x in seen.values()),
    "errors":len(errors),"gridboxes":grid_stats,
    "top_queries":sorted(query_stats,key=lambda x:(x["added"],x["numFound"]),reverse=True)[:30],
}
Path("lidl_recipe_catalog_summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
Path("lidl_recipe_query_errors.json").write_text(json.dumps(errors,ensure_ascii=False,indent=2),encoding="utf-8")
con=sqlite3.connect("prezzi_lidl_recipe.db")
con.execute("DROP TABLE IF EXISTS products")
con.execute("CREATE TABLE products(id TEXT PRIMARY KEY,name TEXT,url TEXT,price REAL,packaging TEXT,base_price TEXT,category_path TEXT,price_source TEXT,lidl_plus_price REAL)")
for p in products:
    con.execute("INSERT OR REPLACE INTO products VALUES(?,?,?,?,?,?,?,?,?)",
                (p.get("id") or p.get("url"),p.get("name"),p.get("url"),p.get("price"),
                 p.get("packaging",""),p.get("base_price",""),p.get("category_path",""),
                 p.get("price_source",""),p.get("lidl_plus_price")))
con.commit(); con.close()
print(json.dumps(summary,ensure_ascii=False))
