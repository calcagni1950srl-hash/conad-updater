import asyncio, html, json, re
from pathlib import Path
import requests
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

BASE="https://spesaonline.conad.it"
PAGE=BASE+"/tutti-i-prodotti"
LOADER=BASE+"/tutti-i-prodotti/_jcr_content/root/search.loader.html?page=2"
UA="Mozilla/5.0 (SmartCampania Conad full catalog probe)"

def extract_products(body):
    out=[]
    for raw in re.findall(r'data-product="([^"]+)"', body):
        try:
            obj=json.loads(html.unescape(raw))
            out.append(obj)
        except Exception:
            pass
    return out

def scan_http():
    s=requests.Session()
    s.headers.update({"User-Agent":UA,"Accept-Language":"it-IT,it;q=0.9"})
    r=s.get(PAGE,timeout=60)
    r.raise_for_status()
    p1=extract_products(r.text)
    rr=s.get(LOADER,headers={"Referer":PAGE},timeout=60)
    loader_status=rr.status_code
    p2=extract_products(rr.text) if rr.ok else []
    text=r.text
    urls=sorted(set(re.findall(r'https?://[^"\'<> ]+', text)))
    interesting=[u for u in urls if any(k in u.lower() for k in ("store","shop","search","product","service","point"))]
    return {
        "page_status":r.status_code,
        "page_bytes":len(r.content),
        "page1_products":len(p1),
        "first_product":p1[0] if p1 else None,
        "loader_status":loader_status,
        "loader_bytes":len(rr.content),
        "page2_products":len(p2),
        "second_page_first_product":p2[0] if p2 else None,
        "interesting_urls":interesting[:200],
        "html_markers":{
            "anacan": "anacan" in text.lower(),
            "pointofservice": "pointofservice" in text.lower(),
            "storecode": "storecode" in text.lower(),
            "selectedstore": "selectedstore" in text.lower(),
            "retailstore": "retailstore" in text.lower(),
        }
    }

async def browser_probe():
    captured=[]
    requests_meta=[]

    def on_request(req):
        u=req.url
        if req.resource_type in ("xhr","fetch","document") or any(k in u.lower() for k in ("loader","store","pointofservice","product","search","service","address","location","delivery","pickup","ritiro","check","funnel","geo")):
            captured.append(u)
            requests_meta.append({
                "resource_type": req.resource_type,
                "method": req.method,
                "url": u,
                "post_data": (req.post_data or "")[:10000],
                "headers": {k:v for k,v in req.headers.items() if k.lower() in ("content-type","referer","x-requested-with")},
            })

    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True)
        ctx=await browser.new_context(locale="it-IT")
        page=await ctx.new_page()
        page.on("request", on_request)

        await page.goto(BASE+"/entry",wait_until="domcontentloaded",timeout=120000)
        for sel in ("#onetrust-reject-all-handler","#onetrust-accept-btn-handler"):
            try:
                loc=page.locator(sel)
                if await loc.count() and await loc.first.is_visible():
                    await loc.first.click(force=True,timeout=3000)
                    break
            except Exception:
                pass
        await page.wait_for_timeout(1200)

        initial_inputs=await page.locator("input").evaluate_all("""els => els.map(e => ({
          id:e.id,name:e.name,type:e.type,placeholder:e.placeholder,
          aria:e.getAttribute('aria-label'),value:e.value
        }))""")
        initial_buttons=await page.locator("button").evaluate_all("""els => els.map(e => ({
          text:(e.innerText||'').trim(), id:e.id, cls:e.className
        })).filter(x=>x.text)""")

        script_urls=await page.locator("script[src]").evaluate_all("""els=>els.map(e=>e.src).filter(Boolean)""")
        script_scan=[]
        for su in script_urls:
            try:
                rr=await ctx.request.get(su,timeout=30000)
                if not rr.ok:
                    continue
                txt=await rr.text()
                low=txt.lower()
                if any(k in low for k in ("googleinputentrypageline1","banner-address-form","pac-container-custom","scelta negozio","id negozio")):
                    hits=[]
                    for key in ("googleInputEntrypageLine1","banner-address-form","pac-container-custom","ID Negozio","pointOfService","storeCode","stores.json","ORDER_AND_COLLECT","pickup","ritiro"):
                        pos=txt.find(key)
                        if pos>=0:
                            hits.append({"key":key,"snippet":txt[max(0,pos-1200):pos+3500]})
                    script_scan.append({"url":su,"bytes":len(txt),"hits":hits})
            except Exception:
                pass

        js_endpoint_snippets=[]
        endpoint_patterns=[
            "stores.json","pointOfService","typeOfService","ORDER_AND_COLLECT",
            "selectStore","storeSelection","setPointOfService","setStore",
            "internalStoreCode","preparationCenters","newPlatform",
            "chosenStore","storeSelected","selectedDeliveryId","setPointOfService"
        ]
        for su in script_urls:
            if "conad-ecommerce" not in su:
                continue
            try:
                rr=await ctx.request.get(su,timeout=30000)
                if not rr.ok:
                    continue
                txt=await rr.text()
                for pat in endpoint_patterns:
                    start=0
                    seen=0
                    while seen<8:
                        pos=txt.find(pat,start)
                        if pos<0:
                            break
                        js_endpoint_snippets.append({
                            "url":su,
                            "pattern":pat,
                            "pos":pos,
                            "snippet":txt[max(0,pos-6000):pos+12000],
                        })
                        seen+=1
                        start=pos+len(pat)
            except Exception:
                pass

        interaction={"attempted":False,"address_candidates":[],"form_before":"","form_after_suggestion":"","after_verify_text":"","after_verify_url":"","visible_modals":[],"error":None}
        direct_store_probe=None
        ecaccess_probe=None
        try:
            form=page.locator("form.banner-address-form").first
            interaction["form_before"]=(await form.evaluate("(e)=>e.outerHTML"))[:20000] if await form.count() else ""
            addr=form.locator('input[placeholder*="Via Mario Rossi"]').first if await form.count() else page.locator('input[placeholder*="Via Mario Rossi"]').first
            if await addr.count():
                interaction["attempted"]=True
                await addr.fill("Via Retella, Capodrise")
                await page.wait_for_timeout(2500)
                candidates=await page.locator("body *").evaluate_all("""els => els
                  .filter(e => {
                    const s=getComputedStyle(e);
                    const t=(e.innerText||'').trim();
                    return t && t.length<300 && s.display!=='none' && s.visibility!=='hidden' &&
                      t.toLowerCase().includes('capodrise');
                  })
                  .slice(0,80)
                  .map(e => ({tag:e.tagName, cls:e.className, id:e.id, text:(e.innerText||'').trim()}))""")
                interaction["address_candidates"]=candidates
                clicked=False
                try:
                    await addr.press("ArrowDown")
                    await page.wait_for_timeout(300)
                    await addr.press("Enter")
                    await page.wait_for_timeout(1200)
                    interaction["keyboard_selection_attempted"]=True
                    interaction["keyboard_address_value"]=await addr.input_value()
                    interaction["keyboard_line2_class"]=await page.locator("#googleInputEntrypageLine2").get_attribute("class")
                    if "uk-hidden" not in (interaction["keyboard_line2_class"] or ""):
                        clicked=True
                        interaction["clicked_selector"]="keyboard:ArrowDown+Enter"
                except Exception as e:
                    interaction["keyboard_selection_error"]=repr(e)

                if not clicked:
                    selectors=[
                        '.pac-container-custom .pac-item[data-place-id]',
                        '.pac-item[data-place-id]',
                        '[data-place-id]',
                        '.pac-item',
                        '[role=option]',
                        'li'
                    ]
                    for selector in selectors:
                        locs=page.locator(selector)
                        n=await locs.count()
                        for i in range(min(n,30)):
                            try:
                                txt=(await locs.nth(i).inner_text()).strip()
                                desc=(await locs.nth(i).get_attribute("data-description")) or ""
                                if "capodrise" in (txt+" "+desc).lower():
                                    target=locs.nth(i).locator("a").first
                                    if await target.count():
                                        await target.evaluate("""e=>{
                                          e.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,cancelable:true,view:window}));
                                          e.dispatchEvent(new MouseEvent('mouseup',{bubbles:true,cancelable:true,view:window}));
                                          e.click();
                                        }""")
                                    else:
                                        await locs.nth(i).evaluate("""e=>{
                                          e.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,cancelable:true,view:window}));
                                          e.dispatchEvent(new MouseEvent('mouseup',{bubbles:true,cancelable:true,view:window}));
                                          e.click();
                                        }""")
                                    clicked=True
                                    interaction["clicked_selector"]=selector
                                    interaction["clicked_text"]=txt
                                    interaction["clicked_description"]=desc
                                    break
                            except Exception:
                                pass
                        if clicked:
                            break
                interaction["clicked"]=clicked
                await page.wait_for_timeout(1800)
                interaction["form_after_suggestion"]=(await form.evaluate("(e)=>e.outerHTML"))[:30000] if await form.count() else ""
                interaction["address_value_after_click"]=await addr.input_value()
                interaction["line2_class_after_click"]=await page.locator("#googleInputEntrypageLine2").get_attribute("class")
                interaction["line2_visible_after_click"]=await page.locator("#googleInputEntrypageLine2").is_visible()

                try:
                    rr=await ctx.request.post(
                        BASE+"/api/ecommerce/it-it.stores.json",
                        data={
                            "latitudine":41.0435591,
                            "longitudine":14.3170526,
                            "typeOfService":"ORDER_AND_COLLECT",
                            "partial":True,
                        },
                        headers={"referer":BASE+"/entry","content-type":"application/json"},
                        timeout=60000,
                    )
                    direct_store_probe={
                        "status":rr.status,
                        "ok":rr.ok,
                        "text":(await rr.text())[:200000],
                    }
                except Exception as e:
                    direct_store_probe={"error":repr(e)}

                try:
                    store_obj=None
                    if direct_store_probe and direct_store_probe.get("ok") and direct_store_probe.get("text"):
                        parsed=json.loads(direct_store_probe["text"])
                        stores=(parsed.get("data",{}).get("orderAndCollect",{}).get("pointOfServices",[]) or [])
                        store_obj=next((x for x in stores if x.get("name")=="010548"), None)
                    if store_obj:
                        payload={
                            "pointOfServiceId":store_obj["name"],
                            "becommerce":store_obj.get("becommerce","sap"),
                            "typeOfService":"ORDER_AND_COLLECT",
                            "deliveryAddress":"VIA RETELLA EX GIARD.DEL SOLE, SNC, 81020 CAPODRISE",
                            "completeAddress":{
                                "formatted_address":"VIA RETELLA EX GIARD.DEL SOLE, SNC, 81020 CAPODRISE",
                                "line1":"VIA RETELLA EX GIARD.DEL SOLE",
                                "line2":"SNC",
                                "postalCode":"81020",
                                "town":"CAPODRISE",
                                "country":{"isocode":"IT","name":"Italia"},
                                "notCompleted":False,
                            },
                            "latitudine":store_obj.get("geoPoint",{}).get("latitude"),
                            "longitudine":store_obj.get("geoPoint",{}).get("longitude"),
                            "nStoresFound":len(stores),
                        }
                        ecaccess_probe=await page.evaluate("""async (payload)=>{
                          const out={
                            hasManager:!!window.OnboardingManager,
                            hasProtection:typeof window.gpGetProtectionToken==='function',
                            before:{
                              localStorage:Object.fromEntries(Object.entries(localStorage)),
                              pointOfService:window.pointOfService||null,
                              typeOfService:window.typeOfService||null
                            }
                          };
                          try{
                            const mgr=window.OnboardingManager;
                            out.managerKeys=mgr?Object.keys(mgr).slice(0,100):[];
                            out.setChosenStoreSource=mgr?.storeService?.setChosenStore?.toString?.()||null;
                            out.postSource=mgr?.storeService?.post?.toString?.()||null;
                            if(!mgr?.storeService?.setChosenStore) throw new Error("setChosenStore unavailable");
                            const response=await new Promise((resolve,reject)=>{
                              mgr.storeService.setChosenStore(
                                (x)=>resolve({ok:true,data:x}),
                                (x)=>resolve({ok:false,error:x}),
                                {...payload}
                              );
                              setTimeout(()=>resolve({ok:false,timeout:true}),20000);
                            });
                            out.response=response;
                            out.after={
                              localStorage:Object.fromEntries(Object.entries(localStorage)),
                              pointOfService:window.pointOfService||null,
                              typeOfService:window.typeOfService||null
                            };
                          }catch(e){out.error=String(e?.stack||e)}
                          return out;
                        }""", payload)
                        if ecaccess_probe and ecaccess_probe.get("response",{}).get("ok"):
                            await page.goto(BASE+"/home",wait_until="domcontentloaded",timeout=120000)
                            await page.wait_for_timeout(5000)
                            ecaccess_probe["home_url"]=page.url
                            ecaccess_probe["home_text"]=(await page.locator("body").inner_text())[:25000]
                            ecaccess_probe["home_storage"]=await page.evaluate("Object.fromEntries(Object.entries(localStorage))")
                            ecaccess_probe["home_pointOfService"]=await page.evaluate("window.pointOfService||null")
                            ecaccess_probe["home_typeOfService"]=await page.evaluate("window.typeOfService||null")
                            ecaccess_probe["home_cookies"]=await ctx.cookies()
                            await page.goto(FULL_URL,wait_until="domcontentloaded",timeout=120000)
                            await page.wait_for_timeout(5000)
                            products_after=[]
                            for el in await page.locator("[data-product]").all():
                                raw=await el.get_attribute("data-product")
                                if raw:
                                    try: products_after.append(json.loads(raw))
                                    except Exception: pass
                            ecaccess_probe["catalog_products"]=len(products_after)
                            ecaccess_probe["catalog_positive"]=sum(1 for x in products_after if float(x.get("basePrice") or 0)>0)
                            ecaccess_probe["catalog_sample"]=[{
                                "code":x.get("code"),"title":x.get("title"),"basePrice":x.get("basePrice"),
                                "promo":x.get("promo"),"variante":x.get("variante"),"increment":x.get("increment")
                            } for x in products_after[:15]]
                except Exception as e:
                    ecaccess_probe={"error":repr(e)}

                civici=form.locator('#googleInputEntrypageLine2') if await form.count() else page.locator('#googleInputEntrypageLine2')
                if await civici.count():
                    for i in range(await civici.count()):
                        try:
                            if await civici.nth(i).is_visible():
                                await civici.nth(i).fill("1")
                                break
                        except Exception:
                            pass

                try:
                    btn=form.locator("button.submitButton").first if await form.count() else page.get_by_role("button",name="Verifica").first
                    if await btn.count() and await btn.is_visible():
                        await btn.click()
                except Exception:
                    pass
                await page.wait_for_timeout(5000)

                interaction["after_verify_url"]=page.url
                interaction["after_verify_text"]=(await page.locator("body").inner_text())[:30000]
                interaction["visible_modals"]=await page.locator(".uk-modal.uk-open, .uk-offcanvas.uk-open, [role=dialog]").evaluate_all("""els=>els.map(e=>({id:e.id,cls:e.className,text:(e.innerText||'').trim().slice(0,8000)}))""")
                interaction["buttons_after_verify"]=await page.locator("button,a").evaluate_all("""els=>els.map(e=>({
                    tag:e.tagName,text:(e.innerText||'').trim(),id:e.id,cls:e.className,href:e.getAttribute('href')
                })).filter(x=>x.text).slice(0,500)""")

                service_clicked=None
                for label in ["Ordina e Ritira","Ordina e ritira","Ritiro","Spesa a Casa"]:
                    try:
                        loc=page.get_by_text(label, exact=False)
                        n=await loc.count()
                        for j in range(min(n,20)):
                            if await loc.nth(j).is_visible():
                                await loc.nth(j).click(force=True)
                                service_clicked=label
                                break
                        if service_clicked:
                            break
                    except Exception:
                        pass
                interaction["service_clicked"]=service_clicked
                if service_clicked:
                    await page.wait_for_timeout(5000)
                    interaction["after_service_url"]=page.url
                    interaction["after_service_text"]=(await page.locator("body").inner_text())[:30000]
                    interaction["buttons_after_service"]=await page.locator("button,a").evaluate_all("""els=>els.map(e=>({
                        tag:e.tagName,text:(e.innerText||'').trim(),id:e.id,cls:e.className,href:e.getAttribute('href')
                    })).filter(x=>x.text).slice(0,500)""")
                    interaction["capodrise_nodes_after_service"]=await page.locator("body *").evaluate_all("""els=>els.filter(e=>{
                        const s=getComputedStyle(e),t=(e.innerText||'').trim().toLowerCase();
                        return t.includes('capodrise') && t.length<1500 && s.display!=='none' && s.visibility!=='hidden';
                    }).slice(0,100).map(e=>({tag:e.tagName,id:e.id,cls:e.className,text:(e.innerText||'').trim()}))""")
        except Exception as e:
            interaction["error"]=repr(e)

        await page.goto(PAGE,wait_until="networkidle",timeout=120000)
        cookies=await ctx.cookies()
        storage=await page.evaluate("""() => ({
          local: Object.fromEntries(Object.entries(localStorage)),
          session: Object.fromEntries(Object.entries(sessionStorage))
        })""")
        selected_products=extract_products(await page.content())
        await browser.close()

    return {
        "cookies":[{"name":c["name"],"domain":c["domain"],"path":c["path"],"value_preview":c["value"][:300]} for c in cookies],
        "storage":storage,
        "initial_inputs":initial_inputs,
        "initial_buttons":initial_buttons[:150],
        "script_urls":script_urls,
        "script_scan":script_scan,
        "js_endpoint_snippets":js_endpoint_snippets,
        "interaction":interaction,
        "direct_store_probe":direct_store_probe,
        "ecaccess_probe":ecaccess_probe,
        "selected_page_products":len(selected_products),
        "selected_positive_products":sum(1 for x in selected_products if float(x.get("basePrice") or 0)>0),
        "selected_product_sample":selected_products[:10],
        "captured_urls":sorted(set(captured))[:1000],
        "requests_meta":requests_meta[:1000],
    }

async def main():
    out={"http":scan_http(),"browser":await browser_probe()}
    Path("probe_full_catalog.json").write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(out,ensure_ascii=False,indent=2))

if __name__=="__main__":
    asyncio.run(main())

# trigger probe after workflow creation
