import json,re,requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

BASE='https://spesaonline.conad.it/'
KEYS=[
 'setChosenStore','set-ecaccess.json','pointOfServiceId','protectionToken','typeOfService',
 'selectedAddress_SAP_Format','completeAddress','notCompleted','formatted_address_no_street_number',
 'postal_code','street_number','administrative_area_level_3'
]
OUT={'scripts':[],'matches':[],'errors':[]}

s=requests.Session()
s.headers.update({'User-Agent':'Mozilla/5.0','Accept-Language':'it-IT,it;q=0.9'})
try:
    r=s.get(BASE,timeout=30)
    r.raise_for_status()
    soup=BeautifulSoup(r.text,'html.parser')
    scripts=[]
    for tag in soup.find_all('script',src=True):
        u=urljoin(BASE,tag.get('src'))
        if u not in scripts: scripts.append(u)
    OUT['scripts']=scripts
    for u in scripts:
        try:
            rr=s.get(u,timeout=30)
            txt=rr.text
            for key in KEYS:
                start=0; n=0
                while True:
                    i=txt.find(key,start)
                    if i<0: break
                    a=max(0,i-5000); b=min(len(txt),i+7000)
                    OUT['matches'].append({'script':u,'key':key,'offset':i,'context':txt[a:b]})
                    start=i+len(key); n+=1
                    if n>=12: break
        except Exception as e:
            OUT['errors'].append({'script':u,'error':repr(e)})
except Exception as e:
    OUT['errors'].append({'root':repr(e)})

open('conad_store_api_contract.json','w',encoding='utf-8').write(json.dumps(OUT,ensure_ascii=False,indent=2))
print(json.dumps({'scripts':len(OUT['scripts']),'matches':len(OUT['matches']),'errors':OUT['errors']},ensure_ascii=False))
