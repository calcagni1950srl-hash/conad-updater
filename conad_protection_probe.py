import json,re,requests
from pathlib import Path
URL='https://spesaonline.conad.it/etc.clientlibs/conad-common/clientlibs/clientlib-global-protection.lc-ff3a62d61705cbee22934354428ab6f7-lc.min.js'
r=requests.get(URL,timeout=45,headers={'User-Agent':'Mozilla/5.0','Accept-Language':'it-IT'})
r.raise_for_status()
s=r.text
keys=['gpGetProtectionToken','grecaptcha','enterprise','recaptcha','protection','entryaccess','execute','sitekey','6LeX2lQa']
out={'url':URL,'status':r.status_code,'length':len(s),'matches':[]}
for k in keys:
    start=0
    while True:
        i=s.lower().find(k.lower(),start)
        if i<0: break
        out['matches'].append({'key':k,'offset':i,'context':s[max(0,i-2500):min(len(s),i+6500)]})
        start=i+len(k)
        if sum(1 for x in out['matches'] if x['key']==k)>=8: break
Path('conad_protection_probe.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
Path('conad_global_protection.js').write_text(s,encoding='utf-8')
print(json.dumps({'status':r.status_code,'length':len(s),'matches':len(out['matches'])},ensure_ascii=False))