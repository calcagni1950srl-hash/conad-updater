import json,re,html as htmlmod,requests
from pathlib import Path

BASE='https://spesaonline.conad.it'
S=requests.Session()
S.headers.update({'User-Agent':'Mozilla/5.0','Accept-Language':'it-IT,it;q=0.9','Referer':BASE+'/'})

PRODUCT_RE=re.compile(r'data-product="([^"]+)"')

def parse_products(body):
    out=[]
    for raw in PRODUCT_RE.findall(body):
        try:
            p=json.loads(htmlmod.unescape(raw))
        except Exception:
            continue
        if p.get('code') and p.get('nome'):
            out.append({
                'code':str(p.get('code')),
                'name':p.get('nome'),
                'price':p.get('basePrice'),
                'qty':p.get('netQuantity'),
                'unit':p.get('netQuantityUm')
            })
    return out

# Initialize ordinary anonymous session.
r=S.get(BASE+'/',timeout=30)

base_payload={
  'pointOfServiceId':'010548',
  'becommerce':'sap',
  'typeOfService':'ORDER_AND_COLLECT',
  'deliveryAddress':'Via Retella, 81020 Capodrise CE, Italia',
  'completeAddress':{
    'formatted_address':'Via Retella, 81020 Capodrise CE, Italia',
    'route':'Via Retella',
    'street_number':'',
    'locality':'Capodrise',
    'postal_code':'81020',
    'country':'Italia',
    'administrative_area_level_3':'Capodrise',
    'administrative_area_level_2':'Provincia di Caserta',
    'administrative_area_level_1':'Campania',
    'notCompleted':False
  },
  'latitudine':41.04177275840482,
  'longitudine':14.319815401607025,
  'nStoresFound':6
}

variants=[
 ('no_token',dict(base_payload)),
 ('null_token',{**base_payload,'protectionToken':None}),
 ('empty_token',{**base_payload,'protectionToken':''}),
]

out={'initial_status':r.status_code,'initial_cookies':S.cookies.get_dict(),'attempts':[]}
for name,payload in variants:
    s=requests.Session(); s.headers.update(S.headers); s.cookies.update(S.cookies)
    rr=s.post(BASE+'/api/ecommerce/it-it.set-ecaccess.json',json=payload,timeout=30)
    att={'variant':name,'status':rr.status_code,'body':rr.text[:5000],'cookies_after':s.cookies.get_dict()}
    try:
        q=s.get(BASE+'/search?query=latte',timeout=30)
        products=parse_products(q.text)
        att['search_status']=q.status_code
        att['product_count']=len(products)
        att['positive_prices']=sum(1 for x in products if isinstance(x.get('price'),(int,float)) and x.get('price',0)>0)
        att['sample']=products[:10]
        att['search_has_store_010548']='010548' in q.text
    except Exception as e:
        att['search_error']=repr(e)
    out['attempts'].append(att)

Path('conad_capodrise_direct_store_probe.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(out,ensure_ascii=False,indent=2))

# trigger workflow after workflow file exists
