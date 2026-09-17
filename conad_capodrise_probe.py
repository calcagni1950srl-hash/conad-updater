import json,re,requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

URL='https://spesaonline.conad.it/aree-coperte-dal-servizio/caserta'
UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/152 Safari/537.36'
s=requests.Session(); s.headers.update({'User-Agent':UA,'Accept-Language':'it-IT,it;q=0.9'})
r=s.get(URL,timeout=60); r.raise_for_status(); html=r.text
report={'source':URL,'status':r.status_code,'html_len':len(html),'capodrise_matches':[],'candidate_ids':[],'scripts_scanned':0,'endpoint_hints':[],'api_contexts':[],'direct_api_tests':[]}
for m in re.finditer('CAPODRISE|RETELLA',html,re.I):
    report['capodrise_matches'].append(html[max(0,m.start()-1000):m.end()+1800])
for snip in report['capodrise_matches']:
    report['candidate_ids'] += re.findall(r'(?<!\d)(\d{5,10})(?!\d)',snip)

soup=BeautifulSoup(html,'html.parser')
needles=['/api/ecommerce/it-it.stores.json','setChosenStore','pointOfServiceId','first-timeslot-by-stores','gpGetProtectionToken']
for tag in soup.find_all('script'):
    src=tag.get('src')
    if not src: continue
    if report['scripts_scanned']>=40: break
    try:
        rr=s.get(urljoin(URL,src),timeout=30)
        if rr.status_code!=200: continue
        report['scripts_scanned']+=1
        body=rr.text
        for needle in needles:
            start=0
            while True:
                i=body.find(needle,start)
                if i<0: break
                report['api_contexts'].append({'needle':needle,'script':urljoin(URL,src),'context':body[max(0,i-2500):i+4500]})
                start=i+len(needle)
                if sum(1 for x in report['api_contexts'] if x['needle']==needle)>=8: break
        for pat in [r'/api/[^"\']+', r'[^"\']*(?:store|negoz|pointOfService)[^"\']*']:
            for x in re.findall(pat,body,re.I)[:120]:
                if any(k in x.lower() for k in ['api','store','negoz','pointofservice']): report['endpoint_hints'].append(x[:600])
    except Exception: pass

# harmless GET probes against public store endpoint to learn required params
api=urljoin(URL,'/api/ecommerce/it-it.stores.json')
for params in [{},{'province':'CE'},{'city':'Capodrise'},{'query':'Capodrise'},{'address':'Capodrise'}]:
    try:
        rr=s.get(api,params=params,headers={'Referer':URL,'X-Requested-With':'XMLHttpRequest'},timeout=30)
        report['direct_api_tests'].append({'params':params,'status':rr.status_code,'url':rr.url,'body':rr.text[:5000]})
    except Exception as e:
        report['direct_api_tests'].append({'params':params,'error':repr(e)})

report['candidate_ids']=sorted(set(report['candidate_ids']))
report['endpoint_hints']=list(dict.fromkeys(report['endpoint_hints']))[:400]
with open('conad_capodrise_probe.json','w',encoding='utf-8') as f: json.dump(report,f,ensure_ascii=False,indent=2)
print(json.dumps({'status':report['status'],'html_len':report['html_len'],'capodrise_matches':len(report['capodrise_matches']),'candidate_ids':report['candidate_ids'],'scripts_scanned':report['scripts_scanned'],'api_contexts':len(report['api_contexts']),'direct_api_tests':[(x.get('params'),x.get('status')) for x in report['direct_api_tests']]},ensure_ascii=False,indent=2))