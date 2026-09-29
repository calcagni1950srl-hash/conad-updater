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


print("\nPRODUCT TYPE COUNTS BY MARKET")
for typ in ["carta_igienica","prosciutto_crudo","pasta_secca","acqua","zucchine","patate","cola"]:
    rows=dict(db.execute("select market,count(*) from products where product_type=? group by market",(typ,)))
    print(typ,{m:rows.get(m,0) for m in markets})

print("\nLIDL TYPE SAMPLES")
for typ in ["carta_igienica","prosciutto_crudo","pasta_secca","acqua","zucchine","patate","cola"]:
    print("TYPE",typ)
    for r in db.execute("select name,brand,category,quantity_text,price_eur from products where market='Lidl' and product_type=? limit 8",(typ,)):
        print(" ",r)


print("\nSOURCE CATEGORY DIAGNOSTICS")
for market in markets:
    print("\nMARKET",market)
    for key in ["acqua","carta","igien","cola","bevande"]:
        print(" KEY",key)
        rows=db.execute("""
          select distinct name,brand,category,canonical_category,product_type,price_eur
          from products
          where market=? and (norm_name like ? or norm_category like ?)
          limit 20
        """,(market,f"%{key}%",f"%{key}%")).fetchall()
        for r in rows[:10]: print("  ",r)

# rebuild_after_lidl_expanded_2026_09_28

# rebuild_after_piccolo_water_paper_2026_09_28


print("\nDECO PROSCIUTTO CRUDO CANDIDATES")
for r in db.execute("""
  select name,brand,category,quantity_text,quantity_value,quantity_unit,
         price_eur,unit_price_eur,unit_price_unit,variable_weight,product_type
  from products
  where market='Decò' and product_type='prosciutto_crudo'
  order by price_eur
  limit 50
"""):
    print(r)


print("\nLIDL FRESH CANDIDATES USER")
for typ in ("zucchine","patate"):
    print("TYPE",typ)
    for r in db.execute("""
      select name,brand,category,quantity_text,quantity_value,quantity_unit,
             price_eur,unit_price_eur,unit_price_unit,variable_weight,product_type
      from products
      where market='Lidl' and product_type=?
      order by price_eur
      limit 50
    """,(typ,)):
        print(r)


print("\nCONAD PENNE CANDIDATES USER")
for r in db.execute("""
  select name,brand,category,quantity_text,quantity_value,quantity_unit,
         price_eur,unit_price_eur,unit_price_unit,variable_weight,product_type
  from products
  where market='Conad' and lower(name) like '%penne%'
  order by price_eur
  limit 100
"""):
    print(r)


print("\nMEAT USER CASES V1")
markets=["Piccolo","Decò","Famila","Sole365","Conad","Lidl"]
queries=[
 ("petto pollo", "%petto%pollo%"),
 ("macinato manzo", "%macin%manzo%"),
 ("salsiccia", "%salsic%"),
 ("bistecca manzo", "%bistecc%manzo%")
]
for market in markets:
    print("\nMARKET",market)
    for label,pat in queries:
        print("QUERY",label)
        rows=db.execute("""
          select name,brand,category,quantity_text,quantity_value,quantity_unit,
                 price_eur,unit_price_eur,unit_price_unit,variable_weight,product_type
          from products
          where market=? and lower(name) like ?
          order by price_eur
          limit 40
        """,(market,pat)).fetchall()
        for r in rows:
            print(r)


print("\nMEAT SYNONYM CANDIDATES V2")
for market in ["Piccolo","Decò","Famila","Sole365","Conad","Lidl"]:
    print("\nMARKET",market)
    for label,terms in [
      ("macinato",["macin","macinat","trit","carne trita","bovino adulto"]),
      ("bistecca",["bistecc","fettin","scottona","entrecote","controfilet","bovino adulto"])
    ]:
        print("QUERY",label)
        seen=set()
        for term in terms:
            rows=db.execute("""
              select name,brand,category,quantity_text,quantity_value,quantity_unit,
                     price_eur,unit_price_eur,unit_price_unit,variable_weight,product_type
              from products
              where market=? and lower(name) like ?
              order by price_eur
              limit 30
            """,(market,"%"+term+"%")).fetchall()
            for r in rows:
                if r[0] in seen: continue
                seen.add(r[0]); print(r)


print("\nMEAT REMAINING GAPS V3")
checks={
 "Conad":["petto","pollo","macin","bovino","salsicc"],
 "Decò":["macin","bovino","scottona"],
 "Lidl":["bistecc","bovino","scottona","fettin","costata","tagliata"]
}
for market,terms in checks.items():
    print("\nMARKET",market)
    seen=set()
    for term in terms:
        rows=db.execute("""
          select name,brand,category,quantity_text,quantity_value,quantity_unit,
                 price_eur,unit_price_eur,unit_price_unit,variable_weight,product_type
          from products
          where market=? and lower(name) like ?
          order by price_eur
          limit 60
        """,(market,"%"+term+"%")).fetchall()
        for r in rows:
            if r[0] in seen: continue
            seen.add(r[0]); print(r)


print("\nPHONE_MEAT_EXACT_V4")
for market in ["Piccolo","Decò","Famila","Sole365","Conad","Lidl"]:
    print("\nMARKET",market)
    for label,terms in [
      ("bistecca_maiale",["bistecc","suino","maiale","lonza","braciola"]),
      ("carne_macinata",["macin","macinat","trit"])
    ]:
        print("QUERY",label)
        seen=set()
        for term in terms:
            rows=db.execute("""
              select name,brand,category,quantity_text,quantity_value,quantity_unit,
                     price_eur,unit_price_eur,unit_price_unit,variable_weight,product_type
              from products
              where market=? and lower(name) like ?
              order by price_eur
              limit 40
            """,(market,"%"+term+"%")).fetchall()
            for r in rows:
                if r[0] in seen: continue
                seen.add(r[0]); print(r)


print("\nLIDL_MEAT_META_V5")
for term in ["Petto di Pollo","Fettine Sottili di Petto di Pollo","Filetto di Petto di Pollo","Salsiccia","Braciole di Suino","Macinato di Bovino"]:
    print("\nTERM",term)
    rows=db.execute("""
      select name,brand,category,quantity_text,quantity_value,quantity_unit,
             price_eur,unit_price_eur,unit_price_unit,variable_weight,product_type
      from products
      where market='Lidl' and lower(name) like ?
      order by price_eur
      limit 40
    """,("%"+term.lower()+"%",)).fetchall()
    for r in rows: print(r)
