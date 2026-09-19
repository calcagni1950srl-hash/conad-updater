import sqlite3, json, re, unicodedata, os, urllib.request

TERMS=["peperoncino","origano","rosmarino"]
DBS=[
  ("Decò","prezzi_deco.db"),
  ("Famila","prezzi_famila_app.db"),
  ("Sole365","prezzi_sole365_app.db"),
]

def norm(s):
    s=(s or "").lower()
    s=unicodedata.normalize("NFD",s)
    s="".join(ch for ch in s if unicodedata.category(ch)!="Mn")
    s=re.sub(r"[^a-z0-9]+"," ",s)
    return re.sub(r"\s+"," ",s).strip()

def standalone(term,name):
    n=norm(name)
    if not re.search(r"(^| )"+re.escape(term)+r"( |$)",n):
        return False
    bad={
      "peperoncino":["patatine","salsa","sugo","pesto","pizza","mix","con olive","tonno","cracker"],
      "origano":["patatine","cracker","pizza","sugo","pesto","crostini"],
      "rosmarino":["patatine","snack","cracker","mais","crostini"]
    }
    return not any(x in n for x in bad.get(term,[]))

out={}
for market,path in DBS:
    if not os.path.exists(path):
        continue
    con=sqlite3.connect(path)
    table=con.execute("select name from sqlite_master where type='table' and name='products_current'").fetchone()
    if not table:
        con.close(); continue
    cols=[r[1] for r in con.execute("pragma table_info(products_current)")]
    namecol="product_name" if "product_name" in cols else ("name" if "name" in cols else None)
    pricecol="price_eur" if "price_eur" in cols else ("price" if "price" in cols else None)
    qv="quantity_value" if "quantity_value" in cols else None
    qu="quantity_unit" if "quantity_unit" in cols else None
    up="unit_price" if "unit_price" in cols else ("unit_price_eur" if "unit_price_eur" in cols else None)
    upu="unit_price_unit" if "unit_price_unit" in cols else None
    if not namecol or not pricecol:
        con.close(); continue
    sel=[namecol,pricecol]
    for col in [qv,qu,up,upu]:
        sel.append(col if col else "NULL")
    rows=con.execute("select "+",".join(sel)+" from products_current where "+pricecol+">0").fetchall()
    for term in TERMS:
        vals=[]
        for name,price,qval,qunit,uprice,uunit in rows:
            if not standalone(term,name): continue
            unit=None
            if uprice and float(uprice)>0:
                uu=norm(uunit)
                if "kg" in uu:
                    unit=float(uprice)
            if unit is None and qval and float(qval)>0 and (qunit or "").upper() in ("KG","G","GR"):
                kg=float(qval) if (qunit or "").upper()=="KG" else float(qval)/1000.0
                if kg>0: unit=float(price)/kg
            vals.append({"name":name,"price":float(price),"qv":qval,"qu":qunit,"unit_eur_kg":unit})
        out.setdefault(term,{})[market]=vals[:20]
    con.close()

print(json.dumps(out,ensure_ascii=False,indent=2))
open("market_average_spices_probe.json","w",encoding="utf-8").write(json.dumps(out,ensure_ascii=False,indent=2))
