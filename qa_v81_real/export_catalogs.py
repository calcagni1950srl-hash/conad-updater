import base64, gzip, json, sqlite3
from pathlib import Path

OUT=Path("qa_v81_real")
OUT.mkdir(exist_ok=True)

def clean(v):
    if v is None: return ""
    return str(v).replace("\t"," ").replace("\r"," ").replace("\n"," ")

HEADER=["kind","key","supermarket","store","name","brand","category","qv","qu","price","up","upu","qtext","uptext","variable","source","checked"]

def write_rows(name, rows):
    tsv=OUT/f"{name}.tsv"
    with tsv.open("w",encoding="utf-8",newline="") as f:
        f.write("\t".join(HEADER)+"\n")
        for row in rows:
            f.write("\t".join(clean(x) for x in row)+"\n")
    raw=tsv.read_bytes()
    packed=base64.b64encode(gzip.compress(raw,9)).decode("ascii")
    (OUT/f"{name}.tsv.gz.b64").write_text(packed,encoding="ascii")
    tsv.unlink()
    return {"rows":len(rows),"b64_chars":len(packed)}

def piccolo(path):
    con=sqlite3.connect(path); con.row_factory=sqlite3.Row
    rows=[]
    sql="""SELECT supermarket,store_code,category,name,quantity_value,quantity_unit,
                  price_eur,unit_price_eur,unit_price_unit,variable_weight,source_url,checked_at
           FROM products_certified WHERE price_eur>0"""
    for r in con.execute(sql):
        key="|".join(clean(r[k]) for k in ("supermarket","store_code","category","name"))
        rows.append(["PICCOLO",key,r["supermarket"] or "Piccolo",r["store_code"],r["name"],"",r["category"],
                     r["quantity_value"],r["quantity_unit"],r["price_eur"],r["unit_price_eur"],r["unit_price_unit"],
                     "","",r["variable_weight"] or 0,r["source_url"],r["checked_at"]])
    con.close(); return rows

def deco(path):
    con=sqlite3.connect(path); con.row_factory=sqlite3.Row
    rows=[]
    sql="""SELECT product_key,supermarket,store,product_name,brand,category,quantity_text,
                  price_eur,unit_price_text,product_url,checked_at
           FROM products WHERE price_eur>0"""
    for r in con.execute(sql):
        rows.append(["DECO",r["product_key"],r["supermarket"] or "Decò",r["store"],r["product_name"],r["brand"],r["category"],
                     "","",r["price_eur"],"","","%s"% (r["quantity_text"] or "")," %s"% (r["unit_price_text"] or ""),
                     0,r["product_url"],r["checked_at"]])
    con.close(); return rows

def cosi(path,kind,market,default_store):
    con=sqlite3.connect(path); con.row_factory=sqlite3.Row
    rows=[]
    sql="""SELECT product_id,product_name,brand,category_name,price_eur,unit_price,unit_price_unit,
                  product_url,source_url,store_alias_id,detected_at
           FROM products WHERE price_eur>0"""
    for r in con.execute(sql):
        rows.append([kind,r["product_id"],market,r["store_alias_id"] or default_store,r["product_name"],r["brand"],r["category_name"],
                     "","",r["price_eur"],r["unit_price"],r["unit_price_unit"],"","",0,r["source_url"] or r["product_url"],r["detected_at"]])
    con.close(); return rows

def conad(path):
    con=sqlite3.connect(path); con.row_factory=sqlite3.Row
    cols={r[1] for r in con.execute("PRAGMA table_info(products_current)")}
    has_var="variable_weight" in cols
    q="""SELECT supermarket,store_code,store_name,product_code,product_name,brand,category1,category2,category3,
                quantity_value,quantity_unit,price_eur,unit_price,unit_price_unit,checked_at%s
         FROM products_current WHERE price_eur>0""" % (",variable_weight" if has_var else "")
    rows=[]
    for r in con.execute(q):
        cat=" > ".join(x for x in [r["category1"],r["category2"],r["category3"]] if x)
        var=r["variable_weight"] if has_var else 0
        rows.append(["CONAD",f'{r["store_code"]}|{r["product_code"]}',r["supermarket"] or "Conad",r["store_name"] or r["store_code"],
                     r["product_name"],r["brand"],cat,r["quantity_value"],r["quantity_unit"],r["price_eur"],r["unit_price"],r["unit_price_unit"],
                     "","",var,"",r["checked_at"]])
    con.close(); return rows

summary={}
summary["piccolo"]=write_rows("catalog_piccolo",piccolo("qa_v81_real/prezzi_piccolo.db"))
summary["deco"]=write_rows("catalog_deco",deco("prezzi_deco.db"))
summary["famila"]=write_rows("catalog_famila",cosi("prezzi_famila_app.db","FAMILA","Famila","Teverola"))
summary["sole365"]=write_rows("catalog_sole365",cosi("prezzi_sole365_app.db","SOLE365","Sole365","Marcianise Aurno"))
summary["conad"]=write_rows("catalog_conad",conad("prezzi_conad_capodrise_app.db"))
(OUT/"catalog_summary.json").write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding="utf-8")
print(json.dumps(summary,indent=2))
