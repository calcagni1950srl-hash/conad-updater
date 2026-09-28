#!/usr/bin/env python3
import json,re,sqlite3,sys,time,unicodedata
from collections import defaultdict,Counter

DB=sys.argv[1] if len(sys.argv)>1 else "prezzi_confronto_fast.db"
STOP={"di","da","dal","dalla","dello","della","dei","degli","delle","il","lo","la","i","gli","le","un","uno","una","e","con","per","al","alla","allo","ai","alle","del","dell","in","x"}
SHAPES={"penne","spaghetti","rigatoni","fusilli","farfalle","linguine","bucatini","paccheri","ziti","tortiglioni"}
FRESH={"zucchine","patate","pomodori","melanzane","peperoni","cipolle","carote","mele","banane","insalata"}
DELI_WEIGHT={"prosciutto_crudo","prosciutto_cotto","mortadella","salame"}
GENERIC={
 "pasta":("PASTA_SECCA","pasta_secca"),"riso":("RISO","riso"),"latte":("LATTE","latte"),
 "uova":("UOVA","uova"),"burro":("BURRO","burro"),"olio":("OLIO","olio"),"farina":("FARINA","farina"),
 "zucchero":("ZUCCHERO","zucchero"),"sale":("SALE","sale"),"pane":("PANE","pane"),
 "yogurt":("YOGURT","yogurt"),"mozzarella":("MOZZARELLA","mozzarella"),"parmigiano":("FORMAGGIO","formaggio"),
 "salame":("SALAME","salame"),"caffe":("CAFFE","caffe"),"shampoo":("SHAMPOO","shampoo"),
 "dentifricio":("DENTIFRICIO","dentifricio"),"deodorante":("DEODORANTE","deodorante"),
 "carta cucina":("CARTA_CUCINA","carta_cucina"),"detersivo lavatrice":("DETERSIVO_LAVATRICE","detersivo_lavatrice"),
 "ammorbidente":("AMMORBIDENTE","ammorbidente"),"detersivo piatti":("DETERSIVO_PIATTI","detersivo_piatti"),
 "patate":("PATATE_FRESCHE","patate"),"zucchine":("ZUCCHINE_FRESCHE","zucchine"),"pomodori":("POMODORI_FRESCHI","pomodori"),
 "cipolle":("CIPOLLE_FRESCHE","cipolle"),"carote":("CAROTE_FRESCHE","carote"),"mele":("MELE_FRESCHE","mele"),"banane":("BANANE_FRESCHE","banane")
}

def norm(s):
    s=unicodedata.normalize("NFD",(s or "").lower())
    s="".join(ch for ch in s if unicodedata.category(ch)!="Mn")
    return re.sub(r"\s+"," ",re.sub(r"[^a-z0-9]+"," ",s)).strip()

def strip_qty(s):
    s=norm(s)
    s=re.sub(r"\b\d+(?:[.,]\d+)?\s*(kg|g|gr|grammi|l|lt|litri|ml|cl|pz|pezzi|bottiglie|bottiglia|rotoli|rotolo)\b"," ",s)
    s=re.sub(r"\b(confezione|conf|pacco|pack|da|di)\b"," ",s)
    return re.sub(r"\s+"," ",s).strip()

def parse_request(raw):
    n=norm(raw); g=ml=pieces=None
    m=re.search(r"\b(\d+(?:[.,]\d+)?)\s*(kg|g|gr|grammi)\b",n)
    if m:
        v=float(m.group(1).replace(",","."))
        g=v*1000 if m.group(2)=="kg" else v
    m=re.search(r"\b(\d+(?:[.,]\d+)?)\s*(l|lt|litri|ml|cl)\b",n)
    if m:
        v=float(m.group(1).replace(",","."))
        ml=v*1000 if m.group(2) in ("l","lt","litri") else v*10 if m.group(2)=="cl" else v
    m=re.search(r"\b(\d+)\s*(bottiglie|bottiglia|rotoli|rotolo|pz|pezzi)\b",n)
    if m: pieces=int(m.group(1))
    return {"raw":raw,"norm":n,"text":strip_qty(raw),"qty_g":g,"qty_ml":ml,"pieces":pieces}

def measure_text(name,qty_text,qty_unit):
    txt=norm(f"{name or ''} {qty_text or ''} {qty_unit or ''}")
    g=ml=pieces=None
    m=re.search(r"\b(\d+)\s*x\s*(\d+(?:[.,]\d+)?)\s*(l|lt|ml|cl|g|gr)\b",txt)
    if m:
        pieces=int(m.group(1)); v=float(m.group(2).replace(",","."))
        u=m.group(3)
        if u in ("g","gr"): g=v
        elif u in ("l","lt"): ml=v*1000
        elif u=="cl": ml=v*10
        else: ml=v
    m2=re.search(r"\b(\d+(?:[.,]\d+)?)\s*(kg|g|gr|grammi)\b",txt)
    if m2 and g is None:
        v=float(m2.group(1).replace(",","."))
        g=v*1000 if m2.group(2)=="kg" else v
    m3=re.search(r"\b(\d+(?:[.,]\d+)?)\s*(l|lt|litri|ml|cl)\b",txt)
    if m3 and ml is None:
        v=float(m3.group(1).replace(",","."))
        ml=v*1000 if m3.group(2) in ("l","lt","litri") else v*10 if m3.group(2)=="cl" else v
    if pieces is None:
        m4=re.search(r"\b(\d+)\s*(pz|pezzi|bottiglie|bottiglia|rotoli|rotolo)\b",txt)
        if m4: pieces=int(m4.group(1))
    return g,ml,pieces

class MemoryResolver:
    def __init__(self,path):
        t=time.perf_counter()
        db=sqlite3.connect(path); db.row_factory=sqlite3.Row
        self.aliases=[(r["alias_norm"],r["canonical_category"],r["product_type"]) for r in db.execute("select alias_norm,canonical_category,product_type from canonical_aliases order by length(alias_norm) desc")]
        self.markets=[r[0] for r in db.execute("select distinct market from products order by market")]
        self.by_type=defaultdict(lambda:defaultdict(list))
        self.by_cat=defaultdict(lambda:defaultdict(list))
        self.token_type=defaultdict(lambda:defaultdict(lambda:defaultdict(list)))
        self.brand_set=set()
        cols="id,market,name,norm_name,brand,norm_brand,category,norm_category,price_eur,unit_price_eur,unit_price_unit,variable_weight,quantity_text,quantity_unit,canonical_category,product_type"
        for r in db.execute("select "+cols+" from products"):
            d=dict(r)
            d["name_n"]=d["norm_name"] or norm(d["name"])
            d["brand_n"]=d["norm_brand"] or norm(d["brand"])
            d["cat_n"]=d["norm_category"] or norm(d["category"])
            d["tokens"]=set((d["name_n"]+" "+d["brand_n"]+" "+d["cat_n"]).split())
            g,ml,pieces=measure_text(d["name"],d["quantity_text"],d["quantity_unit"])
            d["g"]=g; d["ml"]=ml; d["pieces"]=pieces
            unit=(d["unit_price_unit"] or "").upper()
            d["variable"]=bool(d["variable_weight"] or (unit=="KG" and g is None))
            if d["brand_n"] and len(d["brand_n"])>=3: self.brand_set.add(d["brand_n"])
            if d["product_type"]:
                self.by_type[d["product_type"]][d["market"]].append(d)
                for tok in d["tokens"]:
                    if len(tok)>=2:
                        self.token_type[d["product_type"]][d["market"]][tok].append(d)
            self.by_cat[d["canonical_category"]][d["market"]].append(d)
        self.brands=sorted(self.brand_set,key=len,reverse=True)
        self.load_ms=round((time.perf_counter()-t)*1000,2)

    def brand(self,req):
        t=" "+req["norm"]+" "
        for b in self.brands:
            if " "+b+" " in t: return b
        return None

    def interpret(self,req):
        t=req["text"]
        # common grocery concepts must never go through statistical fallback
        for phrase,(c,ty) in sorted(GENERIC.items(),key=lambda x:len(x[0]),reverse=True):
            if re.search(r"(^| )"+re.escape(phrase)+r"( |$)",t):
                return c,ty
        for a,c,ty in self.aliases:
            if ty and re.search(r"(^| )"+re.escape(a)+r"( |$)",t): return c,ty
        toks=[x for x in t.split() if x not in STOP and len(x)>1]
        votes=Counter()
        for ty,markets in self.by_type.items():
            hit=0
            for arr in markets.values():
                for p in arr[:80]:
                    hit=max(hit,len(set(toks)&p["tokens"]))
                    if hit>=2: break
                if hit>=2: break
            if hit: votes[ty]+=hit
        if votes:
            ty=votes.most_common(1)[0][0]
            for c,markets in self.by_cat.items():
                for arr in markets.values():
                    if any(p["product_type"]==ty for p in arr[:80]): return c,ty
        return None,None

    def candidates_for(self,ty,market,req):
        arr=self.by_type.get(ty,{}).get(market,[])
        if not arr: return []
        toks=[x for x in req["text"].split() if x not in STOP and len(x)>1]
        seen={}
        idx=self.token_type.get(ty,{}).get(market,{})
        for tok in toks:
            for p in idx.get(tok,[]):
                seen[p["id"]]=p
        rb=self.brand(req)
        if rb:
            for p in arr:
                if rb==p["brand_n"] or rb in p["brand_n"] or rb in p["name_n"]:
                    seen[p["id"]]=p
        if seen:
            return list(seen.values())
        # generic request: only examine a small cheap shortlist + variable-weight items
        return sorted(arr,key=lambda p:p["price_eur"])[:60] + [p for p in arr if p["variable"]][:30]

    def score(self,p,req,mode):
        score=0
        toks=[x for x in req["text"].split() if x not in STOP and len(x)>1]
        for t in toks:
            if t in p["tokens"]: score+=20
            if t in p["brand_n"].split(): score+=12
        rb=self.brand(req)
        if rb:
            same=(rb==p["brand_n"] or rb in p["brand_n"] or rb in p["name_n"])
            if mode=="same_brand" and not same: return -9999
            if same: score+=80
        if req["pieces"]:
            if p["pieces"]!=req["pieces"]: return -9999
            score+=90
        if req["qty_g"]:
            if p["variable"]:
                score+=140
            elif p["g"]:
                # For deli/fresh products requested by weight, do not simulate multiple packs.
                # A fixed pack must be reasonably close to the requested weight.
                if p["product_type"] in DELI_WEIGHT:
                    ratio=p["g"]/max(req["qty_g"],1)
                    if ratio < 0.70 or ratio > 1.35:
                        return -9999
                diff=abs(p["g"]-req["qty_g"])/max(req["qty_g"],1)
                score+=70-140*diff
            else:
                if p["product_type"] in DELI_WEIGHT:
                    score-=55
                else:
                    score-=30
        if req["qty_ml"] and p["ml"]:
            total=p["ml"]*(p["pieces"] or 1)
            score+=50-90*abs(total-req["qty_ml"])/max(req["qty_ml"],1)
        q=req["text"]
        if ("prosciutto crudo" in q or q.startswith("crudo")) and any(x in p["name_n"] for x in ["tortell","raviol","cappellett","pizza","panino","sandwich"]): return -9999
        if any(x in q for x in ["carta igienica","carta wc","rotoli bagno"]) and "igien" not in p["name_n"] and "igien" not in p["cat_n"]: return -9999
        if "acqua" in q and any(x in p["name_n"] for x in ["micellare","profumo","tonno","pesce"]): return -9999
        if p["product_type"] in FRESH and any(x in p["name_n"] or x in p["cat_n"] for x in ["surgelat","grigliat","burger","crocchett","ripien","minestrone","vellutata","gnocchi","chips","patatin"]): return -9999
        wanted=SHAPES & set(req["text"].split())
        if wanted:
            if wanted & set(p["name_n"].split()): score+=60
            else: score-=50
        return score

    def resolve_one(self,raw,mode="cheapest"):
        req=parse_request(raw); cat,ty=self.interpret(req); rb=self.brand(req)
        out={"request":raw,"product_type":ty,"requested_brand":rb,"markets":{}}
        source=self.by_type.get(ty,{}) if ty else self.by_cat.get(cat,{})
        for m in self.markets:
            best=None; bs=-99999
            arr=self.candidates_for(ty,m,req) if ty else source.get(m,[])
            for p in arr:
                s=self.score(p,req,mode)
                if s>bs or (s==bs and best and p["price_eur"]<best["price_eur"]):
                    bs=s; best=p
            if best is None or bs<=-1000:
                out["markets"][m]=None
            else:
                out["markets"][m]={"name":best["name"],"brand":best["brand"],"price_eur":best["price_eur"],"score":round(bs,1),"pieces":best["pieces"],"g":best["g"],"variable":best["variable"]}
        return out

    def batch(self,items,mode="cheapest"):
        t=time.perf_counter(); rows=[self.resolve_one(x,mode) for x in items]
        return {"load_ms":self.load_ms,"elapsed_ms":round((time.perf_counter()-t)*1000,2),"items":rows}

if __name__=="__main__":
    r=MemoryResolver(DB)
    items=sys.argv[2:] or ["carta igienica","prosciutto crudo 200 g","zucchine 1 kg","acqua Lete 6 bottiglie","penne Barilla 500 g"]
    print(json.dumps({"cheapest":r.batch(items,"cheapest"),"same_brand":r.batch(items,"same_brand")},ensure_ascii=False,indent=2))
