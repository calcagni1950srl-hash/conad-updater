import sqlite3,json,os
p='prezzi_conad.db'
con=sqlite3.connect(p)
tables=[r[0] for r in con.execute("select name from sqlite_master where type='table'")]
out={'file':p,'size':os.path.getsize(p),'tables':tables}
if 'products_current' in tables:
    out['rows']=con.execute('select count(*) from products_current').fetchone()[0]
    out['positive']=con.execute('select count(*) from products_current where price_eur>0').fetchone()[0]
    out['zero']=con.execute('select count(*) from products_current where price_eur<=0').fetchone()[0]
    out['store_codes']=con.execute('select store_code,count(*) from products_current group by store_code').fetchall()
    out['checked_minmax']=con.execute('select min(checked_at),max(checked_at) from products_current').fetchone()
    out['categories']=con.execute('select category1,count(*) from products_current group by category1 order by count(*) desc limit 30').fetchall()
    out['samples']=con.execute('select product_name,quantity_value,quantity_unit,price_eur,unit_price,unit_price_unit,store_code from products_current where price_eur>0 order by product_name limit 25').fetchall()
if 'update_log' in tables:
    out['updates']=con.execute('select checked_at,store_code,query,declared_total,saved_count,status,message from update_log order by id desc limit 20').fetchall()
con.close()
open('conad_db_audit.json','w',encoding='utf-8').write(json.dumps(out,ensure_ascii=False,indent=2))
print(json.dumps(out,ensure_ascii=False,indent=2))