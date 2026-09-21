/*
 Smart Campania - Conad Capodrise stable catalog continuation
 Starts from page 251 to avoid downloading the first 7500 products again.
 Run on spesaonline.conad.it with Capodrise Via Retella already selected.
 Does not export cookies, passwords or session tokens.
*/
(async () => {
  const STORE_CODE = "010548";
  const BASE = "/tutti-i-prodotti";
  const START_PAGE = 251;
  const HARD_MAX_PAGE = 450;
  const PAUSE_MS = 4500;
  const sleep = ms => new Promise(r => setTimeout(r, ms));

  const bodyText = (document.body?.innerText || "").toUpperCase();
  if (!bodyText.includes("CAPODRISE") || !bodyText.includes("VIA RETELLA")) {
    throw new Error("Punto vendita Capodrise non rilevato. Seleziona prima Conad Superstore Via Retella.");
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

  const fetchText = async url => {
    for (let attempt = 1; attempt <= 8; attempt++) {
      const res = await fetch(url, {
        credentials: "include",
        cache: "no-store",
        headers: { "Accept": "text/html, */*;q=0.8" }
      });
      if (res.ok) return await res.text();
      if (res.status === 429) {
        const waitSeconds = Math.min(120, 20 * attempt);
        console.log(`[Conad] 429: attendo ${waitSeconds}s...`);
        await sleep(waitSeconds * 1000);
        continue;
      }
      throw new Error(`HTTP ${res.status} su ${url}`);
    }
    throw new Error("Conad continua a limitare le richieste.");
  };

  console.log("[Conad] Controllo totale catalogo...");
  const firstHtml = await fetchText(BASE);
  const firstDoc = new DOMParser().parseFromString(firstHtml, "text/html");
  const resultText = firstDoc.querySelector("b.results")?.textContent || "";
  const declaredTotal = Number((resultText.match(/[0-9.]+/)?.[0] || "").replaceAll(".", "")) || null;
  const expectedPages = declaredTotal ? Math.ceil(declaredTotal / 30) : null;
  const targetPage = Math.min(HARD_MAX_PAGE, expectedPages || HARD_MAX_PAGE);

  console.log("[Conad] totale dichiarato:", declaredTotal, "pagine previste:", expectedPages);
  if (expectedPages && expectedPages < START_PAGE) {
    alert(`Il catalogo dichiara ${declaredTotal} prodotti (${expectedPages} pagine): non servono pagine oltre 250.`);
    return;
  }

  const byCode = new Map();
  let lastPage = START_PAGE - 1;
  let noNewPages = 0;

  for (let page = START_PAGE; page <= targetPage; page++) {
    const html = await fetchText(`${BASE}/_jcr_content/root/search.loader.html?page=${page}`);
    const list = parseProducts(html);
    let added = 0;
    for (const p of list) {
      const code = String(p.code || "").trim();
      if (code && !byCode.has(code)) {
        byCode.set(code, p);
        added++;
      }
    }
    lastPage = page;
    console.log(`[Conad] pagina ${page}: letti ${list.length}, nuovi ${added}, continuazione ${byCode.size}`);

    if (list.length === 0 || added === 0) noNewPages++; else noNewPages = 0;
    if (!expectedPages && noNewPages >= 3) break;
    await sleep(PAUSE_MS);
  }

  const products = [...byCode.values()];
  const positive = products.filter(p => Number(p.basePrice || 0) > 0);
  const exported = {
    schema: "SMART_CAMPANIA_CONAD_STABLE_BROWSER_CONTINUATION_V1",
    capturedAt: new Date().toISOString(),
    source: "https://spesaonline.conad.it/tutti-i-prodotti",
    store: {
      code: STORE_CODE,
      name: "Conad Superstore Capodrise",
      address: "Via Retella ex Giard. del Sole - 81020 Capodrise",
      mode: "ORDER_AND_COLLECT"
    },
    policy: {
      recipeAvailabilitySource: "TUTTI_I_PRODOTTI_STABLE_CATALOG",
      flyerProductsUnlockRecipes: false,
      exportsCookiesOrTokens: false
    },
    stats: {
      startPage: START_PAGE,
      lastPage,
      declaredTotal,
      expectedPages,
      continuationProducts: products.length,
      positiveBasePrice: positive.length,
      zeroBasePrice: products.length - positive.length
    },
    products
  };

  const blob = new Blob([JSON.stringify(exported, null, 2)], {type:"application/json"});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "conad_capodrise_010548_continuazione_da_251.json";
  document.body.appendChild(a);
  a.click();
  a.remove();

  alert(
    `Continuazione Conad completata.\nPagine: ${START_PAGE}-${lastPage}\nProdotti aggiuntivi: ${products.length}\nTotale dichiarato: ${declaredTotal ?? "non rilevato"}\nCarica qui il JSON.`
  );
})().catch(err => {
  console.error("[Conad] ERRORE", err);
  alert("Errore continuazione Conad: " + (err?.message || err));
});
