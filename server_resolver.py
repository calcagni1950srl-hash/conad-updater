#!/usr/bin/env python3
import json, re, sqlite3, sys, time, unicodedata
from collections import defaultdict

DB=sys.argv[1] if len(sys.argv)>1 else "prezzi_confronto_fast.db"

STOP={"di","da","dal","dalla","dello","della","dei","degli","delle","il","lo","la","i","gli","le","un","uno","una","e","con","per","al","alla","allo","ai","alle","del","dell","in","x"}

def norm(s):
    s=unicodedata.normalize("NFD",(s or "").lower())
    s="".join(ch for ch in s if unicodedata.category(ch)!="Mn")
    s=re.sub(r"[^a-z0-9]+"," ",s).strip()
    return re.sub(r"\s+"," ",s)

def strip_qty(s):
    s=norm(s)
    s=re.sub(r"\b\d+(?:[.,]\d+)?\s*(kg|g|gr|grammi|l|lt|litri|ml|cl|pz|pezzi|bottiglie|bottiglia|rotoli|rotolo)\b"," ",s)
    s=re.sub(r"\b(confezione|conf|pacco|pack|da|di)\b"," ",s)
    return re.sub(r"\s+"," ",s).strip()

def parse_request(raw):
    n=norm(raw)
    qty_g=None; qty_ml=None; pieces=None
    m=re.search(r"\b(\d+(?:[.,]\d+)?)\s*(kg|g|gr|grammi)\b",n)
    if m:
        v=float(m.group(1).replace(",","."))
        qty_g=v*1000 if m.group(2)=="kg" else v
    m=re.search(r"\b(\d+(?:[.,]\d+)?)\s*(l|lt|litri|ml|cl)\b",n)
    if m:
        v=float(m.group(1).replace(",","."))
        u=m.group(2); qty_ml=v*1000 if u in ("l","lt","litri") else v*10 if u=="cl" else v
    m=re.search(r"\b(\d+)\s*(bottiglie|bottiglia|rotoli|rotolo|pz|pezzi)\b",n)
    if m: pieces=int(m.group(1))
    text=strip_qty(raw)
    return {"raw":raw,"norm":n,"text":text,"qty_g":qty_g,"qty_ml":qty_ml,"pieces":pieces}

class Resolver:
    def __init__(self,path):
        self.db=sqlite3.connect(path)
        self.db.row_factory=sqlite3.Row
        self.aliases=[]
        for r in self.db.execute("select alias_norm,canonical_category,product_type from canonical_aliases order by length(alias_norm) desc"):
            self.aliases.append((r["alias_norm"],r["canonical_category"],r["product_type"]))
        self.markets=[r[0] for r in self.db.execute("select distinct market from products order by market")]
        self.cache={}

    def interpret(self, req):
        t=req["text"]
        # longest phrase wins
        for alias,cat,typ in self.aliases:
            if typ and re.search(r"(^| )"+re.escape(alias)+r"( |$)",t):
                return cat,typ
        # fallback: use exact known product_type names / canonical categories
        toks=[x for x in t.split() if x not in STOP and len(x)>1]
        if toks:
            ph=",".join("?"*len(toks))
            sql=f"""select product_type,canonical_category,count(*) n
                    from products
                    where product_type is not null
                      and id in (
                        select product_id from product_tokens where token in ({ph})
                      )
                    group by product_type,canonical_category
                    order by n desc limit 3"""
            rows=self.db.execute(sql,toks).fetchall()
            if rows and rows[0]["n"]>=2:
                return rows[0]["canonical_category"],rows[0]["product_type"]
        return None,None

    def candidates(self,market,req,cat,typ,limit=24):
        key=(market,req["text"],cat,typ,limit)
        if key in self.cache: return self.cache[key]
        params=[]
        where=["market=?"]; params.append(market)
        if typ:
            where.append("product_type=?"); params.append(typ)
        elif cat:
            where.append("canonical_category=?"); params.append(cat)
        toks=[x for x in req["text"].split() if x not in STOP and len(x)>1]
        token_join=""
        if toks:
            ph=",".join("?"*len(toks))
            token_join=f"""left join (
              select product_id,count(distinct token) hits from product_tokens
              where token in ({ph}) group by product_id
            ) h on h.product_id=p.id"""
            params=toks+params
        sql=f"""select p.*,coalesce(h.hits,0) hits from products p {token_join}
                where {' and '.join(where)}
                order by hits desc, p.price_eur asc limit {int(limit)}"""
        rows=self.db.execute(sql,params).fetchall()
        self.cache[key]=rows
        return rows

    def score(self,r,req):
        name=norm(r["name"]); brand=norm(r["brand"] or ""); cat=norm(r["category"] or "")
        score=float(r["hits"] or 0)*20
        toks=[x for x in req["text"].split() if x not in STOP and len(x)>1]
        for t in toks:
            if re.search(r"(^| )"+re.escape(t)+r"( |$)",name): score+=12
            if re.search(r"(^| )"+re.escape(t)+r"( |$)",brand): score+=15
            if re.search(r"(^| )"+re.escape(t)+r"( |$)",cat): score+=5
        # hard exclusions for food-vs-derived ambiguity
        q=req["text"]
        if "prosciutto crudo" in q and any(x in name for x in ["tortell","raviol","cappellett","pizza","panino","sandwich"]): return -9999
        if "carta igienica" in q or "carta wc" in q:
            if "igien" not in name and "igien" not in cat: return -9999
        if "acqua" in q and any(x in name for x in ["micellare","profumo","tonno","pesce"]): return -9999
        return score

    def resolve_one(self,raw):
        req=parse_request(raw)
        cat,typ=self.interpret(req)
        out={"request":raw,"canonical_category":cat,"product_type":typ,"markets":{}}
        for m in self.markets:
            rows=self.candidates(m,req,cat,typ)
            ranked=sorted(((self.score(r,req),r) for r in rows),key=lambda x:x[0],reverse=True)
            ranked=[x for x in ranked if x[0]>-1000]
            if not ranked:
                out["markets"][m]=None
            else:
                s,r=ranked[0]
                out["markets"][m]={
                    "name":r["name"],"brand":r["brand"],"category":r["category"],
                    "price_eur":r["price_eur"],"score":round(s,1),
                    "product_type":r["product_type"]
                }
        return out

    def resolve_batch(self,items):
        t=time.perf_counter()
        rows=[self.resolve_one(x) for x in items]
        return {"elapsed_ms":round((time.perf_counter()-t)*1000,2),"items":rows}

if __name__=="__main__":
    r=Resolver(DB)
    items=sys.argv[2:] or ["carta igienica","prosciutto crudo 200 g","zucchine 1 kg","acqua Lete 6 bottiglie","penne Barilla 500 g"]
    print(json.dumps(r.resolve_batch(items),ensure_ascii=False,indent=2))
