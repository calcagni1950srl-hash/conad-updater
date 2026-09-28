#!/usr/bin/env python3
import gzip,json,sqlite3,sys,os
src=sys.argv[1] if len(sys.argv)>1 else "prezzi_confronto_fast.db"
out=sys.argv[2] if len(sys.argv)>2 else "resolver_index.json.gz"
db=sqlite3.connect(src); db.row_factory=sqlite3.Row
aliases=[dict(r) for r in db.execute("select alias_norm,canonical_category,product_type from canonical_aliases order by length(alias_norm) desc")]
products=[]
sql="""select id,market,name,brand,category,price_eur,unit_price_eur,unit_price_unit,
              variable_weight,quantity_text,quantity_unit,canonical_category,product_type,
              norm_name,norm_brand,norm_category
       from products"""
for r in db.execute(sql):
    d=dict(r)
    products.append(d)
payload={"schema":1,"aliases":aliases,"products":products}
raw=json.dumps(payload,ensure_ascii=False,separators=(",",":")).encode("utf-8")
with gzip.open(out,"wb",compresslevel=9) as f: f.write(raw)
print("products",len(products),"raw_mb",round(len(raw)/1048576,2),"gz_mb",round(os.path.getsize(out)/1048576,2))

# refresh_after_piccolo_2026_09_28
