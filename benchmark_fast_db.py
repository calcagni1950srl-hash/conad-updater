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
