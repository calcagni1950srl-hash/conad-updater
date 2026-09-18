import sqlite3, json
terms=["aglio","prezzemolo","cipolla","peperoncino","gamber","pangratt","origano","paccher","melanz","vino bianco","scarola","cozze","burro","sedano","pepe nero","vongol","calamar","provola","friarielli","broccol","cavolfior","finocch","ziti","scialatielli","rosmarino","basilico","sale"]
con=sqlite3.connect("prezzi_conad.db")
out={}
for term in terms:
    out[term]=con.execute("select product_name,price_eur,category1,category2 from products_current where price_eur>0 and lower(product_name) like ? limit 8",("%"+term+"%",)).fetchall()
print(json.dumps(out,ensure_ascii=False))
con.close()
