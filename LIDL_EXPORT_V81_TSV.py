import json, re
from pathlib import Path

SRC=Path("lidl_recipe_products.json") if Path("lidl_recipe_products.json").exists() else Path("lidl_api_products.json")
OUT=Path("qa_v81/products_lidl_api.tsv")

def clean(v):
    if v is None: return ""
    return str(v).replace("\t"," ").replace("\r"," ").replace("\n"," ").strip()

def parse_packaging(text):
    if not text: return None, None
    s=text.lower().replace(",", ".").replace("×","x")
    m=re.search(r"(\d+)\s*x\s*(\d+(?:\.\d+)?)\s*(kg|g|gr|ml|cl|l|lt)\b",s)
    if m:
        n=float(m.group(1)); v=float(m.group(2)); u=m.group(3)
        return n*v, canonical(u)
    # avoid ambiguous ranges such as 50/65 g
    if re.search(r"\d+\s*/\s*\d+\s*(kg|g|gr|ml|cl|l|lt)",s):
        return None,None
    m=re.search(r"(\d+(?:\.\d+)?)\s*(kg|g|gr|ml|cl|l|lt|pz|pezzi)\b",s)
    if m:
        return float(m.group(1)), canonical(m.group(2))
    return None,None

def canonical(u):
    u=u.lower()
    if u=="gr": return "g"
    if u=="lt": return "l"
    if u=="pezzi": return "pz"
    return u

def parse_base_price(text):
    if not text: return None,None
    s=text.lower().replace(",", ".")
    m=re.search(r"(\d+(?:\.\d+)?)\s*€?\s*(?:/|al|per)?\s*(kg|l|lt|litro|pz|pezzo)\b",s)
    if not m: return None,None
    unit=m.group(2)
    if unit in ("l","lt","litro"): unit="litro"
    elif unit in ("pz","pezzo"): unit="pezzo"
    else: unit="kg"
    return float(m.group(1)),unit

products=json.loads(SRC.read_text(encoding="utf-8"))
OUT.parent.mkdir(parents=True,exist_ok=True)
rows=[]
with_qty=0
for x in products:
    qv,qu=parse_packaging(x.get("packaging",""))
    if qv and qu: with_qty+=1
    up,uu=parse_base_price(x.get("base_price",""))
    rows.append([
        clean(x.get("id") or x.get("url")),
        clean(x.get("name")),
        "",
        clean(x.get("category_path")),
        clean(qv),
        clean(qu),
        clean(x.get("price")),
        clean(up),
        clean(uu),
        "0",
        clean(x.get("url")),
        "",
    ])
with OUT.open("w",encoding="utf-8") as f:
    f.write("key\tname\tbrand\tcategory\tquantityValue\tquantityUnit\tpriceEur\tunitPriceEur\tunitPriceUnit\tvariableWeight\tsourceUrl\tcheckedAt\n")
    for r in rows: f.write("\t".join(r)+"\n")
print(json.dumps({"products":len(rows),"with_parseable_quantity":with_qty,"out":str(OUT)},ensure_ascii=False))
