import json, sqlite3, urllib.request, time
from urllib.parse import urlencode

BASE="https://lidl.everli.com/api/search"
STORE_ID="9240"
HEADERS={"User-Agent":"Mozilla/5.0","Accept":"application/json"}

def fetch(keyword, skip=0, take=40):
    qs=urlencode({"keyword":keyword,"skip":skip,"take":take,"brands":"","tags":"","store_ids":STORE_ID})
    req=urllib.request.Request(BASE+"?"+qs,headers=HEADERS)
    with urllib.request.urlopen(req,timeout=45) as r:
        return json.load(r)

# Ingredient/search terms used by the exact Smart Campania V81 recipe set.
# Add common aliases so generic recipe ingredients can resolve to real Everli products.
queries=[
"spaghetti","pasta","pasta corta","pasta mista","paccheri","linguine","scialatielli","riso","gnocchi","lasagne",
"olio extravergine","aglio","sale","pepe","prezzemolo","cipolla","passata","pomodoro","pomodori pelati","basilico",
"limone","peperoncino","capperi","pangrattato","origano","rosmarino","farina","olive nere","vino bianco",
"zucchine","melanzane","patate","peperoni","scarola","friarielli","broccoli","cavolfiore","carciofi","fagiolini",
"sedano","carote","piselli","fagioli","ceci","lenticchie","zucca","lattuga","insalata",
"parmigiano","formaggio grattugiato","pecorino","provola","mozzarella","ricotta","burro","latte","uova","panna",
"pollo","petto di pollo","manzo","vitello","maiale","salsiccia","guanciale","pancetta","coniglio","agnello",
"cozze","vongole","gamberi","calamari","seppie","polpo","baccala","merluzzo","tonno","sgombro","acciughe","alici",
"pesce spada","spigola","orata","salmone","frutti di mare","scampi",
"pane","zucchero","cioccolato","cacao","mascarpone","yogurt","frutta","mele","pere","arance"
]

raw={}
products={}
errors={}
for i,q in enumerate(queries):
    try:
        data=fetch(q)
        raw[q]=data
        stores=data.get("stores") or []
        ps=(stores[0].get("products") or []) if stores else []
        print("EVERLI_QUERY",q,"PRODUCTS",len(ps))
        for p in ps:
            if str(p.get("store_id")) != STORE_ID: continue
            price=int(p.get("price") or 0)
            if price <= 0: continue
            pid=str(p.get("id") or p.get("ref_id") or "")
            if not pid: continue
            products[pid]=p
    except Exception as e:
        errors[q]=repr(e)
        print("EVERLI_ERROR",q,repr(e))
    time.sleep(0.03)

rows=[]
for p in products.values():
    cats=" / ".join(x.get("name","") for x in (p.get("categories") or []) if isinstance(x,dict))
    rows.append({
        "id":str(p.get("id") or ""),
        "name":str(p.get("name") or ""),
        "brand":str(p.get("brand") or ""),
        "price":round(int(p.get("price") or 0)/100,2),
        "short_description":str(p.get("short_description") or ""),
        "type":str(p.get("type") or ""),
        "value":p.get("value"),
        "category":str(p.get("category_name") or ""),
        "categories":cats,
        "main_category":str(p.get("main_category_name") or ""),
        "store_id":str(p.get("store_id") or ""),
        "image":str(p.get("image") or p.get("thumbnail") or "")
    })
rows.sort(key=lambda x:(x["name"].lower(),x["price"]))

with open("lidl_everli_raw.json","w",encoding="utf-8") as f:
    json.dump(raw,f,ensure_ascii=False)
with open("lidl_everli_products.json","w",encoding="utf-8") as f:
    json.dump(rows,f,ensure_ascii=False,indent=2)
with open("lidl_everli_errors.json","w",encoding="utf-8") as f:
    json.dump(errors,f,ensure_ascii=False,indent=2)

db=sqlite3.connect("prezzi_lidl_everli.db")
db.execute("DROP TABLE IF EXISTS products")
db.execute("""CREATE TABLE products(
 id TEXT PRIMARY KEY,name TEXT,brand TEXT,price REAL,short_description TEXT,
 type TEXT,value REAL,category TEXT,categories TEXT,main_category TEXT,
 store_id TEXT,image TEXT)""")
db.executemany("""INSERT INTO products VALUES(
 :id,:name,:brand,:price,:short_description,:type,:value,:category,:categories,:main_category,:store_id,:image)""",rows)
db.commit()
ok=db.execute("PRAGMA integrity_check").fetchone()[0]
db.close()
print("EVERLI_SUMMARY",json.dumps({"store_id":STORE_ID,"queries":len(queries),"products_positive":len(rows),"errors":len(errors),"sqlite_integrity":ok},ensure_ascii=False))
