import json, re, sys, time, hashlib
from pathlib import Path
from urllib.parse import urljoin, urlparse
from datetime import datetime, timezone
import requests

BASE='https://www.cosicomodo.it'
TARGETS=[
 'https://www.cosicomodo.it/familasud/teverola/reparti/frutta-e-verdura/c/10006',
 'https://www.cosicomodo.it/familasud/foggia-addedda/reparti/tutto-per-la-casa/c/10016',
]
OUT=Path('famila_frontend_v7_artifact'); OUT.mkdir(exist_ok=True)
CH=OUT/'chunks'; CH.mkdir(exist_ok=True)
S=requests.Session(); S.headers.update({'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/152 Safari/537.36','Accept-Language':'it-IT,it;q=0.9,en;q=0.7'})

def fetch(url):
    t=time.time()
    try:
        r=S.get(url,timeout=35,allow_redirects=True)
        return {'url':url,'status':r.status_code,'final_url':r.url,'bytes':len(r.content),'elapsed_s':round(time.time()-t,3),'content_type':r.headers.get('content-type',''),'text':r.text}
    except Exception as e:
        return {'url':url,'status':None,'final_url':None,'bytes':0,'elapsed_s':round(time.time()-t,3),'content_type':'','text':'','error':repr(e)}

def scripts_from_html(html, base):
    vals=re.findall(r'<script[^>]+src=["\']([^"\']+)["\']',html,re.I)
    out=[]
    for v in vals:
        u=urljoin(base,v)
        if urlparse(u).netloc.endswith('cosicomodo.it') and u not in out: out.append(u)
    return out

def contexts(text, terms, radius=260):
    low=text.lower(); found=[]
    for term in terms:
        start=0
        while True:
            i=low.find(term.lower(),start)
            if i<0: break
            a=max(0,i-radius); b=min(len(text),i+len(term)+radius)
            found.append({'term':term,'context':text[a:b].replace('\n',' ')})
            start=i+len(term)
            if len(found)>=180: return found
    return found

def url_candidates(text):
    c=set()
    patterns=[
      r'https?://[^"\'`\\\s]{5,300}',
      r'["\'](\/[^"\']*(?:api|search|product|catalog|category|pagination|page)[^"\']*)["\']',
      r'["\']([^"\']*(?:api\/|\/search|\/products?|\/catalog|\/categories|pagination)[^"\']*)["\']'
    ]
    for p in patterns:
        for m in re.findall(p,text,re.I):
            s=m if isinstance(m,str) else m[0]
            if 3<len(s)<350: c.add(s)
    return sorted(c)

def identifiers(text):
    keys=set()
    for pat in [r'["\']([A-Za-z_$][\w$]{1,50})["\']\s*:',r'\b([A-Za-z_$][\w$]{2,50})\s*[:=]']:
        for x in re.findall(pat,text):
            if any(k in x.lower() for k in ['page','offset','limit','size','sort','query','search','product','category','catalog']): keys.add(x)
    return sorted(keys)

result={'probe':'Famila Frontend Probe V7','generated_at_utc':datetime.now(timezone.utc).isoformat(),'browser_automation':False,'targets':[],'chunks':[],'summary':{}}
all_scripts=[]
for ti,u in enumerate(TARGETS,1):
    f=fetch(u); html=f.pop('text','')
    (OUT/f'target_{ti}.html').write_text(html,encoding='utf-8',errors='ignore')
    scr=scripts_from_html(html,f.get('final_url') or u)
    for x in scr:
        if x not in all_scripts: all_scripts.append(x)
    result['targets'].append({'fetch':f,'scripts':scr})

terms=['pagination','currentPage','totalPages','pageSize','pageNumber','offset','limit','loadMore','nextPage','setPage','searchResult','products','categoryCode','currentQuery','relevance','facets','sort']
for i,u in enumerate(all_scripts,1):
    f=fetch(u); text=f.pop('text','')
    name=f'{i:02d}_'+hashlib.sha1(u.encode()).hexdigest()[:8]+'.js'
    (CH/name).write_text(text,encoding='utf-8',errors='ignore')
    ctx=contexts(text,terms)
    urls=url_candidates(text)
    ids=identifiers(text)
    score=sum(1 for t in terms if t.lower() in text.lower())
    result['chunks'].append({'url':u,'file':f'chunks/{name}','fetch':f,'keyword_score':score,'identifiers':ids[:200],'url_candidates':urls[:250],'contexts':ctx[:180]})
    time.sleep(.35)

# Aggregate high-signal evidence
high=[]
for c in result['chunks']:
    if c['keyword_score']>=3 or c['url_candidates']:
        high.append({'url':c['url'],'file':c['file'],'keyword_score':c['keyword_score'],'identifiers':c['identifiers'],'url_candidates':c['url_candidates'],'contexts':c['contexts'][:35]})
result['summary']={'target_http_ok':all(t['fetch'].get('status')==200 for t in result['targets']), 'script_count':len(all_scripts),'chunks_http_200':sum(c['fetch'].get('status')==200 for c in result['chunks']),'high_signal_chunks':len(high),'high_signal':high}
# Fail-closed verdict: discovery, not pagination validation
result['verdict']='FRONTEND_CHUNKS_DISCOVERED' if high else 'FRONTEND_ENDPOINT_DISCOVERY_FAILED'
(OUT/'famila_frontend_probe_v7.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')

md=[]
md += ['# Famila Frontend Probe V7','',f"Verdetto: **{result['verdict']}**",'',f"Script scoperti: **{len(all_scripts)}**",f"Chunk HTTP 200: **{result['summary']['chunks_http_200']}**",f"Chunk ad alto segnale: **{len(high)}**",'']
md += ['## Obiettivo','Individuare nel frontend CosìComodo endpoint e parametri reali usati da catalogo/paginazione. Nessuna paginazione viene dichiarata valida in questo probe.','']
for h in high:
    md += [f"## {h['file']}",f"URL: `{h['url']}`",f"Keyword score: {h['keyword_score']}"]
    if h['identifiers']: md += ['','Identificatori: `'+ '`, `'.join(h['identifiers'][:60])+'`']
    if h['url_candidates']:
        md += ['','Endpoint/stringhe candidate:']+[f"- `{x[:300]}`" for x in h['url_candidates'][:80]]
    if h['contexts']:
        md += ['','Contesti ad alto segnale:']
        for x in h['contexts'][:18]: md += [f"- **{x['term']}**: `{x['context'][:700]}`"]
    md += ['']
(OUT/'RISULTATO.md').write_text('\n'.join(md),encoding='utf-8')
print(result['verdict'])
print('artifact:',OUT)
