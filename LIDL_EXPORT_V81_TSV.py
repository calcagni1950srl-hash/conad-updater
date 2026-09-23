import json, re
from pathlib import Path

EVERLI=Path("lidl_everli_products.json")
SRC=EVERLI if EVERLI.exists() else (Path("lidl_recipe_products.json") if Path("lidl_recipe_products.json").exists() else Path("lidl_api_products.json"))
OUT=Path("qa_v81/products_lidl_api.tsv")

def clean(v):
    if v is None: return ""
    return str(v).replace("\t"," ").replace("\r"," ").replace("\n"," ").strip()

def canonical(u):
    u=u.lower()
    if u in ("gr","grammi"): return "g"
    if u in ("lt","litri","litro"): return "l"
    if u in ("pezzi","pezzo"): return "pz"
    return u

def parse_packaging(text):
    if not text: return None,None
    s=str(text).lower().replace(",", ".").replace("×","x")
    m=re.search(r"(\d+)\s*x\s*(\d+(?:\.\d+)?)\s*(kg|g|gr|ml|cl|l|lt)\b",s)
    if m: return float(m.group(1))*float(m.group(2)),canonical(m.group(3))
    if re.search(r"\d+\s*/\s*\d+\s*(kg|g|gr|ml|cl|l|lt)",s): return None,None
    m=re.search(r"(\d+(?:\.\d+)?)\s*(kg|g|gr|ml|cl|l|lt|pz|pezzi)\b",s)
    if m: return float(m.group(1)),canonical(m.group(2))
    return None,None

def parse_base_price(text):
    if not text: return None,None
    s=str(text).lower().replace(",", ".")
    m=re.search(r"(\d+(?:\.\d+)?)\s*€?\s*(?:/|al|per)?\s*(kg|l|lt|litro|pz|pezzo)\b",s)
    if not m: return None,None
    unit=m.group(2)
    return float(m.group(1)),("litro" if unit in ("l","lt","litro") else "pezzo" if unit in ("pz","pezzo") else "kg")

products=json.loads(SRC.read_text(encoding="utf-8"))
OUT.parent.mkdir(parents=True,exist_ok=True)
rows=[]; with_qty=0
for x in products:
    if SRC==EVERLI:
        # Everli exposes quantity primarily as type/value and often also in short_description/name.
        typ=str(x.get("type") or "").lower(); val=x.get("value")
        qu=None; qv=None
        if val not in (None,"") and typ:
            try: qv=float(val)
            except: qv=None
            unitmap={"grams":"g","gram":"g","g":"g","kilograms":"kg","kilogram":"kg","kg":"kg","milliliters":"ml","milliliter":"ml","ml":"ml","liters":"l","liter":"l","l":"l","pieces":"pz","piece":"pz","pz":"pz"}
            qu=unitmap.get(typ)
        if not (qv and qu): qv,qu=parse_packaging(" ".join([str(x.get("short_description") or ""),str(x.get("name") or "")]))
        if qv and qu: with_qty+=1
        category=" / ".join(filter(None,[clean(x.get("main_category")),clean(x.get("category")),clean(x.get("categories"))]))
        rows.append([clean(x.get("id")),clean(x.get("name")),clean(x.get("brand")),category,clean(qv),clean(qu),clean(x.get("price")),"","","0","","Everli store 9240"])
    else:
        qv,qu=parse_packaging(x.get("packaging","")); with_qty += 1 if qv and qu else 0
        up,uu=parse_base_price(x.get("base_price",""))
        rows.append([clean(x.get("id") or x.get("url")),clean(x.get("name")),"",clean(x.get("category_path")),clean(qv),clean(qu),clean(x.get("price")),clean(up),clean(uu),"0",clean(x.get("url")),""])
with OUT.open("w",encoding="utf-8") as f:
    f.write("key\tname\tbrand\tcategory\tquantityValue\tquantityUnit\tpriceEur\tunitPriceEur\tunitPriceUnit\tvariableWeight\tsourceUrl\tcheckedAt\n")
    for r in rows: f.write("\t".join(r)+"\n")
print(json.dumps({"source":str(SRC),"products":len(rows),"with_parseable_quantity":with_qty,"quantity_coverage_pct":round(100*with_qty/len(rows),1) if rows else 0,"out":str(OUT)},ensure_ascii=False))
