import sqlite3, sys, time
p=sys.argv[1] if len(sys.argv)>1 else 'prezzi_confronto_fast.db'
db=sqlite3.connect(p)
markets=[r[0] for r in db.execute('select distinct market from products order by market')]
key_tokens=['patata','zucchina','penne','barilla','coca','cola','cocacola']
print('KEY CANDIDATES')
for tok in key_tokens:
    rows=dict(db.execute('select p.market,count(distinct p.id) from product_tokens t join products p on p.id=t.product_id where t.token=? group by p.market',(tok,)))
    print(tok,{m:rows.get(m,0) for m in markets})

terms=['patata','zucchina','penne','barilla','coca','cola','latte','pane','riso','tonno','olio','pomodoro','mozzarella','pollo','uova','acqua','caffe','biscotti','farina','zucchero','sale','cipolla','carota','mela','banana','yogurt','parmigiano','prosciutto','salmone','merluzzo','fagioli','ceci','lenticchie','piselli','mais','burro','succo','arancia','limone','insalata','lattuga','peperone','melanzana','detersivo','shampoo','dentifricio','carta','gelato','pizza','pasta']
for n in (10,25,50):
    q=terms[:n]
    ph=','.join('?' for _ in q)
    sql=f'select distinct p.id,p.market from product_tokens t join products p on p.id=t.product_id where t.token in ({ph})'
    best=999.0
    count=0
    for _ in range(5):
        t=time.perf_counter(); rows=db.execute(sql,q).fetchall(); dt=(time.perf_counter()-t)*1000
        best=min(best,dt); count=len(rows)
    print(f'BENCH {n}: {best:.2f} ms, candidates={count}')


print("\nCASE DETAILS")
cases = {
    "prosciutto crudo": ["prosciutto","crudo"],
    "carta igienica": ["carta","igienica"],
    "acqua lete": ["acqua","lete"],
}
for label,toks in cases.items():
    print("\nCASE",label)
    ph=','.join('?' for _ in toks)
    sql=f"""
    WITH hits AS (
      SELECT p.id,COUNT(DISTINCT t.token) hit
      FROM product_tokens t JOIN products p ON p.id=t.product_id
      WHERE t.token IN ({ph})
      GROUP BY p.id
    )
    SELECT p.market,h.hit,p.name,p.brand,p.category,p.quantity_text,p.quantity_value,p.quantity_unit,p.price_eur,p.unit_price_eur,p.unit_price_unit,p.variable_weight
    FROM hits h JOIN products p ON p.id=h.id
    WHERE h.hit>=?
    ORDER BY p.market,h.hit DESC,p.price_eur ASC
    """
    rows=db.execute(sql, toks+[len(toks)]).fetchall()
    last=None; n=0
    for r in rows:
        if r[0]!=last:
            last=r[0]; n=0
            print(" ",last)
        if n<8:
            print("   ",r[1:])
            n+=1


print("\nTOP ALTRO SOURCE CATEGORIES")
for row in db.execute("""
SELECT market, norm_category, count(*) n
FROM products
WHERE canonical_category='ALTRO'
GROUP BY market,norm_category
ORDER BY n DESC
LIMIT 100
"""):
    print(row)
