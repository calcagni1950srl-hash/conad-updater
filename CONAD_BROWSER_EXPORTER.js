/*
 Smart Campania - Conad Capodrise stable catalog browser exporter
 Run ONLY on https://spesaonline.conad.it after selecting:
 Conad Superstore - Via Retella ex Giard. del Sole - 81020 Capodrise
 No cookies, passwords or session tokens are written to the export.
*/
(async () => {
  const STORE_CODE = "010548";
  const STORE_LABEL = "CONAD SUPERSTORE - VIA RETELLA EX GIARD.DEL SOLE - 81020 CAPODRISE";
  const BASE = "/tutti-i-prodotti";
  const MAX_PAGES = 250;
  const PAUSE_MS = 500;
  const sleep = ms => new Promise(r => setTimeout(r, ms));

  const bodyText = (document.body?.innerText || "").toUpperCase();
  if (!bodyText.includes("CAPODRISE") || !bodyText.includes("VIA RETELLA")) {
    throw new Error("Punto vendita Capodrise non rilevato nella pagina. Seleziona prima Conad Superstore Via Retella.");
  }

  const parseProducts = html => {
    const doc = new DOMParser().parseFromString(html, "text/html");
    const out = [];
    for (const el of doc.querySelectorAll("[data-product]")) {
      const raw = el.getAttribute("data-product");
      if (!raw) continue;
      try {
        const p = JSON.parse(raw);
        if (p && p.code) out.push(p);
      } catch (_) {}
    }
    return out;
  };

  const byCode = new Map();
  const addProducts = list => {
    let added = 0;
    for (const p of list) {
      const code = String(p.code || "").trim();
      if (!code) continue;
      const old = byCode.get(code);
      if (!old) {
        byCode.set(code, p);
        added++;
      } else {
        const op = Number(old.basePrice || 0);
        const np = Number(p.basePrice || 0);
        if (op <= 0 && np > 0) byCode.set(code, p);
      }
    }
    return added;
  };

  const fetchText = async url => {
    const res = await fetch(url, {
      method: "GET",
      credentials: "include",
      cache: "no-store",
      headers: { "Accept": "text/html, */*;q=0.8" }
    });
    if (!res.ok) throw new Error(`HTTP ${res.status} su ${url}`);
    return await res.text();
  };

  console.log("[Conad] Verifica Capodrise OK. Lettura catalogo stabile...");
  const firstHtml = await fetchText(BASE);
  const first = parseProducts(firstHtml);
  addProducts(first);
  console.log(`[Conad] pagina iniziale: ${first.length} prodotti, unici ${byCode.size}`);

  let noNewPages = 0;
  let lastPage = 1;
  for (let page = 2; page <= MAX_PAGES; page++) {
    const url = `${BASE}/_jcr_content/root/search.loader.html?page=${page}`;
    const html = await fetchText(url);
    const list = parseProducts(html);
    const added = addProducts(list);
    lastPage = page;

    if (page % 10 === 0 || added === 0) {
      console.log(`[Conad] pagina ${page}: letti ${list.length}, nuovi ${added}, totale ${byCode.size}`);
    }

    if (list.length === 0 || added === 0) noNewPages++;
    else noNewPages = 0;

    if (noNewPages >= 3) break;
    await sleep(PAUSE_MS);
  }

  const products = [...byCode.values()];
  const priceOf = p => Number(p.basePrice || 0);
  const positive = products.filter(p => priceOf(p) > 0);
  const zero = products.filter(p => priceOf(p) <= 0);
  const bassiFissi = products.filter(p => p.bassiFissi === true || p.bassiFissi === 1);
  const promo = products.filter(p => Array.isArray(p.promo) && p.promo.length > 0);
  const categories = {};
  for (const p of products) {
    const k = p.categoriaPrimoLivello || "Senza categoria";
    categories[k] = (categories[k] || 0) + 1;
  }

  const exported = {
    schema: "SMART_CAMPANIA_CONAD_STABLE_BROWSER_EXPORT_V1",
    capturedAt: new Date().toISOString(),
    source: "https://spesaonline.conad.it/tutti-i-prodotti",
    store: {
      code: STORE_CODE,
      label: STORE_LABEL,
      mode: "ORDER_AND_COLLECT"
    },
    policy: {
      recipeAvailabilitySource: "TUTTI_I_PRODOTTI_STABLE_CATALOG",
      flyerProductsUnlockRecipes: false,
      exportsCookiesOrTokens: false
    },
    stats: {
      pagesScanned: lastPage,
      products: products.length,
      positiveBasePrice: positive.length,
      zeroBasePrice: zero.length,
      bassiFissi: bassiFissi.length,
      productsWithPromoMetadata: promo.length,
      categories
    },
    products
  };

  const stamp = new Date().toISOString().slice(0,10);
  const filename = `conad_capodrise_010548_catalogo_stabile_${stamp}.json`;
  const blob = new Blob([JSON.stringify(exported, null, 2)], {type:"application/json"});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);

  console.log("[Conad] ESPORTAZIONE COMPLETATA", exported.stats);
  alert(`Catalogo Conad Capodrise esportato. Prodotti: ${products.length} - con prezzo positivo: ${positive.length}. File: ${filename}`);
})().catch(err => {
  console.error("[Conad] ERRORE", err);
  alert("Errore esportazione Conad: " + (err?.message || err));
});