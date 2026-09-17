import sqlite3,json,statistics
from pathlib import Path
DB='prezzi_conad.db'
con=sqlite3.connect(DB)
con.row_factory=sqlite3.Row
cur=con.cursor()
tables=[r[0] for r in cur.execute("select name from sqlite_master where type='table'")]
out={'db':DB,'tables':tables}
if 'products_current' not in tables:
    raise SystemExit('products_current missing')
cols=[r[1] for r in cur.execute('pragma table_info(products_current)')]
out['columns']=cols
rows=cur.execute('select * from products_current').fetchall()
out['rows_total']=len(rows)
prices=[]; zero=0; qty_ok=0; cats={}; stores={}; names=[]
for r in rows:
    d=dict(r); p=float(d.get('price_eur') or 0)
    if p>0: prices.append(p)
    else: zero+=1
    q=d.get('quantity_value')
    try:
        if q is not None and float(q)>0 and (d.get('quantity_unit') or '').strip(): qty_ok+=1
    except: pass
    c=(d.get('category1') or '').strip() or '(vuota)'; cats[c]=cats.get(c,0)+1
    s=(d.get('store_code') or '').strip() or '(vuoto)'; stores[s]=stores.get(s,0)+1
    names.append((d.get('product_name') or '').lower())
out['positive_prices']=len(prices); out['zero_prices']=zero
out['qty_with_value_unit']=qty_ok
out['stores']=stores
out['categories']=dict(sorted(cats.items(),key=lambda x:(-x[1],x[0])))
if prices:
    out['price_min']=min(prices); out['price_max']=max(prices); out['price_median']=statistics.median(prices)
# basic key-food presence, only positive price
checks=['pasta','riso','latte','uova','pomodoro','patate','zucchine','melanzane','pollo','salsiccia','manzo','tonno','ceci','fagioli','mozzarella','provola','olio','farina','pane']
pos_rows=[dict(r) for r in rows if float(dict(r).get('price_eur') or 0)>0]
out['checks']={}
for k in checks:
    hits=[{'name':r.get('product_name'),'price':r.get('price_eur'),'qty':r.get('quantity_value'),'unit':r.get('quantity_unit'),'category':r.get('category1')} for r in pos_rows if k in (r.get('product_name') or '').lower()][:5]
    out['checks'][k]={'count':sum(1 for r in pos_rows if k in (r.get('product_name') or '').lower()),'sample':hits}
# detect suspicious low positive prices
out['under_0_20']=sum(1 for p in prices if 0<p<0.20)
out['under_0_50']=sum(1 for p in prices if 0<p<0.50)
Path('conad_db_audit.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:out[k] for k in ['rows_total','positive_prices','zero_prices','qty_with_value_unit','stores','price_min','price_median','under_0_20','under_0_50'] if k in out},ensure_ascii=False,indent=2))
con.close()