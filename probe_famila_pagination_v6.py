#!/usr/bin/env python3
import json, re, sys, time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urlparse

import requests
from bs4 import BeautifulSoup

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
BASE = "https://www.cosicomodo.it"
TARGETS = [
    "https://www.cosicomodo.it/familasud/teverola/reparti/frutta-e-verdura/c/10006",
    "https://www.cosicomodo.it/familasud/foggia-addedda/reparti/tutto-per-la-casa/c/10016",
]
OUT = Path("artifact")
OUT.mkdir(exist_ok=True)

session = requests.Session()
session.headers.update({"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9,en;q=0.7"})

def fetch(url, label):
    t=time.time()
    try:
        r=session.get(url, timeout=45, allow_redirects=True)
        meta={"url":url,"status":r.status_code,"final_url":r.url,"bytes":len(r.content),"elapsed_s":round(time.time()-t,3),"content_type":r.headers.get("content-type"),"error":None}
        suffix = ".json" if "application/json" in (r.headers.get("content-type") or "") or url.endswith(".json") else ".html"
        (OUT/f"{label}{suffix}").write_bytes(r.content)
        return r,meta
    except Exception as e:
        return None,{"url":url,"status":None,"final_url":None,"bytes":0,"elapsed_s":round(time.time()-t,3),"content_type":None,"error":repr(e)}

def walk_find(obj, key):
    if isinstance(obj, dict):
        if key in obj: return obj[key]
        for v in obj.values():
            x=walk_find(v,key)
            if x is not None:return x
    elif isinstance(obj,list):
        for v in obj:
            x=walk_find(v,key)
            if x is not None:return x
    return None

def parse_next_html(text):
    soup=BeautifulSoup(text,"html.parser")
    node=soup.find("script", id="__NEXT_DATA__")
    if not node or not node.string: return None
    try:return json.loads(node.string)
    except Exception:return None

def parse_payload(r):
    if r is None:return None
    ctype=(r.headers.get("content-type") or "").lower()
    if "json" in ctype or r.url.endswith(".json"):
        try:
            d=r.json()
            # _next/data normally returns {pageProps: ...}
            return {"pageProps": d.get("pageProps", d), "buildId": None, "query": d.get("query") if isinstance(d,dict) else None}
        except Exception:return None
    return parse_next_html(r.text)

def extract(parsed):
    if not parsed:return {"valid":False}
    root=parsed.get("props",{}).get("pageProps",{}) if "props" in parsed else parsed.get("pageProps",{})
    sp=walk_find(root,"searchPageData")
    if not isinstance(sp,dict):return {"valid":False,"query":parsed.get("query"),"buildId":parsed.get("buildId")}
    pag=sp.get("pagination") or {}
    products=sp.get("products") or []
    codes=[]
    prices=[]
    for p in products:
        if not isinstance(p,dict):continue
        code=p.get("code") or p.get("ean") or p.get("id")
        if code is not None:codes.append(str(code))
        pr=p.get("price") or {}
        val=pr.get("value") if isinstance(pr,dict) else None
        if isinstance(val,(int,float)):prices.append(float(val))
    return {
        "valid":True,
        "categoryCode":sp.get("categoryCode"),
        "currentQuery":((sp.get("currentQuery") or {}).get("query") or {}).get("value"),
        "currentQueryUrl":((sp.get("currentQuery") or {}).get("url")),
        "pagination":pag,
        "product_count":len(products),
        "product_codes":codes,
        "positive_price_count":sum(1 for x in prices if x>0),
        "query":parsed.get("query"),
        "buildId":parsed.get("buildId")
    }

def next_json_url(base_url, build_id, query):
    path=urlparse(base_url).path.strip("/")
    return f"{BASE}/_next/data/{build_id}/{path}.json?{urlencode(query)}"

def candidate_urls(base_url, page_index, build_id=None):
    rawq=":relevance"
    qs=[
        ("q_relevance_page", {"q":rawq,"page":str(page_index)}),
        ("page_q_relevance", {"page":str(page_index),"q":rawq}),
        ("q_relevance_currentPage", {"q":rawq,"currentPage":str(page_index)}),
        ("q_relevance_pageNumber", {"q":rawq,"pageNumber":str(page_index)}),
        ("q_relevance_page_size", {"q":rawq,"page":str(page_index),"pageSize":"20"}),
        ("q_relevance_p", {"q":rawq,"p":str(page_index)}),
    ]
    out=[]
    for name,q in qs:
        out.append((name, base_url+"?"+urlencode(q)))
        if build_id:
            out.append(("nextdata_"+name, next_json_url(base_url,build_id,q)))
    return out

def main():
    report={
        "probe":"Famila Pagination Probe V6",
        "generated_at_utc":datetime.now(timezone.utc).isoformat(),
        "browser_automation":False,
        "method":"HTTP direct + __NEXT_DATA__ / Next.js data endpoint",
        "targets":[],
        "summary":{}
    }
    global_valid=False
    for ti,base in enumerate(TARGETS,1):
        r,m=fetch(base,f"target_{ti}_base")
        parsed=parse_payload(r)
        baseinfo=extract(parsed)
        entry={"base_fetch":m,"base":baseinfo,"tests":[]}
        base_codes=baseinfo.get("product_codes",[])
        build=baseinfo.get("buildId")
        # test second page (index 1), then third page only if needed
        for page_index in [1,2]:
            for ci,(name,url) in enumerate(candidate_urls(base,page_index,build),1):
                rr,mm=fetch(url,f"target_{ti}_p{page_index}_{ci}_{name}")
                info=extract(parse_payload(rr))
                pag=info.get("pagination") or {}
                cp=pag.get("currentPage")
                codes=info.get("product_codes",[])
                changed=bool(codes and base_codes and codes!=base_codes)
                validated=(cp==page_index and changed and info.get("product_count",0)>0 and info.get("positive_price_count",0)>0)
                entry["tests"].append({"candidate":name,"requested_page_index":page_index,"fetch":mm,"parsed":info,"codes_changed_vs_base":changed,"pagination_validated":validated})
                if validated:
                    global_valid=True
                    entry["winning_candidate"]={"candidate":name,"requested_page_index":page_index,"url":url,"currentPage":cp}
                    break
            if entry.get("winning_candidate"):break
        report["targets"].append(entry)
    if global_valid:
        verdict="DIRECT_HTTP_PAGINATION_VALIDATED"
        next_action="BUILD_FAMILA_HTTP_UPDATER_V1"
    else:
        verdict="DIRECT_HTTP_PAGINATION_STILL_UNRESOLVED"
        next_action="INSPECT_FRONTEND_NETWORK_OR_CHUNK_LOGIC"
    report["summary"]={"pagination_validated":global_valid,"verdict":verdict,"next_action":next_action}
    (OUT/"famila_pagination_probe_v6.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    md=["# Famila Pagination Probe V6","",f"**Verdetto:** `{verdict}`","",f"**Prossima azione:** `{next_action}`","", "Il probe considera valida la paginazione solo se:","- la risposta contiene dati strutturati di catalogo;","- `currentPage` coincide con la pagina richiesta;","- i codici prodotto cambiano rispetto alla pagina base;","- sono presenti prodotti e prezzi positivi.",""]
    for i,t in enumerate(report["targets"],1):
        md += [f"## Target {i}",f"Base: `{t['base_fetch']['url']}`",f"Paginazione base: `{t['base'].get('pagination')}`"]
        if t.get("winning_candidate"):
            md += [f"**Metodo valido:** `{t['winning_candidate']['candidate']}`",f"URL: `{t['winning_candidate']['url']}`"]
        else:
            md += ["Nessun candidato ha superato i criteri fail-closed."]
        md += [""]
    (OUT/"RISULTATO.md").write_text("\n".join(md),encoding="utf-8")
    print(json.dumps(report["summary"],ensure_ascii=False,indent=2))
    return 0

if __name__=="__main__":
    sys.exit(main())
