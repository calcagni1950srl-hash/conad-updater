import json,re,requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

URL='https://spesaonline.conad.it/aree-coperte-dal-servizio/caserta'
UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/152 Safari/537.36'
s=requests.Session(); s.headers.update({'User-Agent':UA,'Accept-Language':'it-IT,it;q=0.9'})
r=s.get(URL,timeout=60)
r.raise_for_status()
html=r.text
report={'source':URL,'status':r.status_code,'html_len':len(html),'capodrise_matches':[],'candidate_ids':[],'scripts_scanned':0,'endpoint_hints':[]}
for m in re.finditer('CAPODRISE',html,re.I):
    report['capodrise_matches'].append(html[max(0,m.start()-500):m.end()+800])
# collect numeric ids close to Capodrise/Retella occurrences
for snip in report['capodrise_matches']:
    report['candidate_ids'] += re.findall(r'(?<!\d)(\d{5,10})(?!\d)',snip)
# inspect embedded json/script text and selected JS bundles for API/store hints
soup=BeautifulSoup(html,'html.parser')
for tag in soup.find_all('script'):
    txt=tag.string or tag.get_text() or ''
    if 'capodrise' in txt.lower() or 'retella' in txt.lower():
        report['capodrise_matches'].append(txt[:20000])
    src=tag.get('src')
    if not src: continue
    if report['scripts_scanned']>=35: break
    try:
        rr=s.get(urljoin(URL,src),timeout=30)
        if rr.status_code!=200: continue
        report['scripts_scanned']+=1
        body=rr.text
        for pat in [r'https?://[^"\']+', r'/api/[^"\']+', r'[^"\']*(?:store|negoz|pointOfService)[^"\']*']:
            for x in re.findall(pat,body,re.I)[:80]:
                if any(k in x.lower() for k in ['api','store','negoz','pointofservice']):
                    report['endpoint_hints'].append(x[:500])
    except Exception as e:
        pass
report['candidate_ids']=sorted(set(report['candidate_ids']))
report['endpoint_hints']=list(dict.fromkeys(report['endpoint_hints']))[:300]
with open('conad_capodrise_probe.json','w',encoding='utf-8') as f: json.dump(report,f,ensure_ascii=False,indent=2)
print(json.dumps({'status':report['status'],'html_len':report['html_len'],'capodrise_matches':len(report['capodrise_matches']),'candidate_ids':report['candidate_ids'],'scripts_scanned':report['scripts_scanned'],'endpoint_hints':len(report['endpoint_hints'])},ensure_ascii=False,indent=2))