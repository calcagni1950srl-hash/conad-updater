import argparse
import asyncio
import html as htmlmod
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from urllib.parse import quote_plus

from playwright.async_api import async_playwright

BASE = "https://spesaonline.conad.it"
SEARCH = BASE + "/search?query={query}"
LOADER = BASE + "/search/_jcr_content/root/search.loader.html?query={query}&page={page}"
STORE_CODE = "010548"
STORE_NAME = "Conad Superstore Capodrise"
STORE_ADDRESS = "VIA RETELLA EX GIARD.DEL SOLE, SNC, CAPODRISE 81020"

PRODUCT_RE = re.compile(r'data-product="([^"]+)"')
TOTAL_RE = re.compile(r'<b class="results">\s*([\d.]+)\s+risultat(?:o|i)')

DEFAULT_QUERIES = [
    "pasta", "riso", "pane", "farina", "latte", "uova", "formaggio", "mozzarella", "provola",
    "pomodoro", "passata", "pelati", "olio", "sale", "pepe", "aglio", "cipolla", "patate",
    "zucchine", "melanzane", "peperoni", "scarola", "friarielli", "piselli", "fagioli",
    "ceci", "carne", "manzo", "pollo", "salsiccia", "prosciutto", "salame", "tonno",
    "alici", "baccalà", "cozze", "polpo", "pangrattato", "capperi", "olive", "basilico",
    "prezzemolo", "origano", "lievito", "burro"
]


def profile_dir() -> Path:
    root = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(root) / "SmartCampania" / "ConadChromeProfile"


def find_chrome() -> str:
    candidates = [
        shutil.which("chrome"),
        shutil.which("chrome.exe"),
        os.path.join(os.environ.get("PROGRAMFILES", ""), "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("PROGRAMFILES(X86)", ""), "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "Application", "chrome.exe"),
    ]
    for p in candidates:
        if p and os.path.exists(p):
            return p
    raise RuntimeError("Google Chrome non trovato. Installa Chrome e riprova.")


def bootstrap(profile: Path):
    """Open a normal Chrome profile. Store selection is intentionally manual."""
    chrome = find_chrome()
    profile.mkdir(parents=True, exist_ok=True)
    print("\nINIZIALIZZAZIONE CONAD CAPODRISE")
    print("1) Si apre Chrome normale con un profilo dedicato a Smart Campania.")
    print("2) Sul sito Conad inserisci: Via Retella, 81020 Capodrise CE.")
    print("3) Scegli 'Ordina e Ritira'.")
    print("4) Seleziona il Conad Superstore di VIA RETELLA EX GIARD.DEL SOLE, SNC.")
    print("5) Premi 'Conferma il negozio'.")
    print("6) Verifica che il sito mostri prodotti/prezzi, poi CHIUDI TUTTE le finestre di quel Chrome.")
    print("\nLo script non automatizza questa selezione e non tenta di superare la protezione del sito.\n")
    args = [
        chrome,
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        BASE + "/",
    ]
    proc = subprocess.Popen(args)
    rc = proc.wait()
    print(f"Chrome chiuso (codice {rc}). Ora esegui CONAD_CAPODRISE_AGGIORNA.bat.")


def parse_products(body: str):
    out = {}
    for raw in PRODUCT_RE.findall(body):
        try:
            p = json.loads(htmlmod.unescape(raw))
        except Exception:
            continue
        code = p.get("code")
        name = p.get("nome")
        price = p.get("basePrice")
        if code and name and price is not None:
            try:
                price_f = float(price)
            except Exception:
                continue
            if price_f > 0:
                p["basePrice"] = price_f
                out[str(code)] = p
    return out


def parse_total(body: str):
    m = TOTAL_RE.search(body)
    return int(m.group(1).replace(".", "")) if m else None


def unit_price(p):
    try:
        q = float(p.get("netQuantity"))
        price = float(p.get("basePrice"))
    except Exception:
        return None, None
    u = (p.get("netQuantityUm") or "").upper()
    if q <= 0:
        return None, None
    if u == "KG":
        return round(price / q, 4), "EUR/KG"
    if u in ("L", "LT"):
        return round(price / q, 4), "EUR/L"
    return None, None


async def accept_cookie_if_present(page):
    try:
        b = page.locator("#onetrust-accept-btn-handler")
        if await b.count() and await b.first.is_visible():
            await b.first.click(timeout=3000)
            await page.wait_for_timeout(600)
    except Exception:
        pass


async def store_evidence(page):
    """Fail-closed evidence that the active shopping session is Capodrise.

    We accept only explicit evidence rendered/stored by the website after the manual selection.
    We do not infer the store from the public nearby-store list.
    """
    evidence = []
    try:
        body_text = (await page.locator("body").inner_text(timeout=5000)).lower()
    except Exception:
        body_text = ""
    try:
        html = (await page.content()).lower()
    except Exception:
        html = ""

    if "via retella" in body_text and "capodrise" in body_text:
        evidence.append("ui:via-retella+capodrise")

    try:
        storage = await page.evaluate("""() => {
          const out = {local:{}, session:{}};
          for (let i=0;i<localStorage.length;i++) { const k=localStorage.key(i); out.local[k]=localStorage.getItem(k); }
          for (let i=0;i<sessionStorage.length;i++) { const k=sessionStorage.key(i); out.session[k]=sessionStorage.getItem(k); }
          return out;
        }""")
        s = json.dumps(storage, ensure_ascii=False).lower()
        if STORE_CODE in s:
            evidence.append("storage:010548")
        if "via retella" in s and "capodrise" in s:
            evidence.append("storage:via-retella+capodrise")
    except Exception:
        pass

    if STORE_CODE in html and "scegli il negozio dove ritirare la spesa" not in body_text:
        evidence.append("html:010548")

    return sorted(set(evidence))


async def fetch_text(request, url, referer, retries=4):
    headers = {
        "accept": "application/json, text/plain, */*",
        "accept-language": "it-IT,it;q=0.9",
        "referer": referer,
    }
    last = None
    for attempt in range(1, retries + 1):
        try:
            r = await request.get(url, headers=headers, timeout=30000)
            body = await r.text()
            if r.ok:
                return body
            last = f"HTTP {r.status}"
            if r.status == 429:
                wait = min(90, 12 * (2 ** (attempt - 1)))
                print(f"HTTP 429: pausa {wait}s ({attempt}/{retries})", flush=True)
                await asyncio.sleep(wait)
                continue
        except Exception as e:
            last = repr(e)
        await asyncio.sleep(min(8, attempt * 2))
    raise RuntimeError(f"Richiesta fallita: {url} — {last}")


async def harvest_query(page, request, query):
    q = quote_plus(query)
    search_url = SEARCH.format(query=q)
    await page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
    await accept_cookie_if_present(page)
    await page.wait_for_timeout(1000)

    body = await page.content()
    total = parse_total(body)
    first = parse_products(body)
    if total is None:
        raise RuntimeError(f"Totale risultati non trovato per '{query}'. Sessione Conad forse scaduta.")
    if total and not first:
        raise RuntimeError(f"Nessun prodotto con prezzo positivo per '{query}'.")

    allp = dict(first)
    pages = max(1, math.ceil(total / 40))

    for pageno in range(2, pages + 1):
        await asyncio.sleep(2.0)
        url = LOADER.format(query=q, page=pageno)
        referer = search_url + f"&page={pageno}"
        body2 = await fetch_text(request, url, referer)
        pp = parse_products(body2)
        if not pp:
            raise RuntimeError(f"Pagina {pageno}/{pages} vuota per '{query}'.")
        allp.update(pp)

    if total > 0 and len(allp) < min(total, 3):
        raise RuntimeError(f"Copertura insufficiente per '{query}': {len(allp)}/{total}.")

    return total, allp


def save_db(results, path: Path):
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript("""
    CREATE TABLE IF NOT EXISTS products_current(
      supermarket TEXT NOT NULL, store_code TEXT NOT NULL, store_name TEXT,
      store_address TEXT, product_code TEXT NOT NULL, product_name TEXT NOT NULL,
      brand TEXT, category1 TEXT, category2 TEXT, category3 TEXT,
      quantity_value REAL, quantity_unit TEXT, price_eur REAL NOT NULL,
      unit_price REAL, unit_price_unit TEXT, bassi_fissi INTEGER NOT NULL DEFAULT 0,
      image_url TEXT, source_queries TEXT, checked_at TEXT NOT NULL,
      PRIMARY KEY(store_code,product_code)
    );
    CREATE TABLE IF NOT EXISTS price_history(
      id INTEGER PRIMARY KEY AUTOINCREMENT, store_code TEXT NOT NULL,
      product_code TEXT NOT NULL, price_eur REAL NOT NULL, unit_price REAL,
      checked_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS update_log(
      id INTEGER PRIMARY KEY AUTOINCREMENT, checked_at TEXT NOT NULL,
      store_code TEXT, query TEXT, declared_total INTEGER, saved_count INTEGER,
      status TEXT, message TEXT
    );
    """)

    merged = {}
    sources = {}
    for query, (total, pp) in results.items():
        for code, p in pp.items():
            merged[code] = p
            sources.setdefault(code, set()).add(query)
        con.execute(
            """INSERT INTO update_log
               (checked_at,store_code,query,declared_total,saved_count,status,message)
               VALUES(?,?,?,?,?,?,?)""",
            (now, STORE_CODE, query, total, len(pp), "OK",
             "Sessione Capodrise creata manualmente in Chrome normale; raccolta dal catalogo ufficiale Conad")
        )

    if len(merged) < 3:
        con.close()
        raise RuntimeError(f"Solo {len(merged)} prodotti: DB non sostituito.")

    con.execute("DELETE FROM products_current WHERE store_code=?", (STORE_CODE,))
    for code, p in merged.items():
        up, upu = unit_price(p)
        row = (
            "Conad", STORE_CODE, STORE_NAME, STORE_ADDRESS,
            code, p["nome"], p.get("marchio"), p.get("categoriaPrimoLivello"),
            p.get("categoriaSecondoLivello"), p.get("categoriaTerzoLivello"),
            p.get("netQuantity"), p.get("netQuantityUm"), float(p["basePrice"]),
            up, upu, int(bool(p.get("bassiFissi"))), p.get("defaultImgSrc"),
            ",".join(sorted(sources[code])), now
        )
        con.execute("INSERT OR REPLACE INTO products_current VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
        con.execute(
            "INSERT INTO price_history(store_code,product_code,price_eur,unit_price,checked_at) VALUES(?,?,?,?,?)",
            (STORE_CODE, code, float(p["basePrice"]), up, now)
        )
    con.commit()
    con.close()
    return len(merged)


async def update(profile: Path, db: Path, queries, headful: bool, diagnostics: Path):
    if not profile.exists():
        raise RuntimeError("Profilo Conad non inizializzato. Esegui prima CONAD_CAPODRISE_INIZIALIZZA.bat.")
    diagnostics.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            channel="chrome",
            headless=not headful,
            locale="it-IT",
            viewport={"width": 1440, "height": 1000},
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        try:
            await page.goto(SEARCH.format(query="latte"), wait_until="domcontentloaded", timeout=60000)
            await accept_cookie_if_present(page)
            await page.wait_for_timeout(1200)
            evidence = await store_evidence(page)
            if not evidence:
                await page.screenshot(path=str(diagnostics / "sessione_non_verificata.png"), full_page=False)
                (diagnostics / "sessione_non_verificata.html").write_text(await page.content(), encoding="utf-8")
                raise RuntimeError(
                    "Sessione Capodrise non verificata. Apri CONAD_CAPODRISE_INIZIALIZZA.bat, "
                    "seleziona di nuovo il negozio 010548 e chiudi Chrome."
                )

            print("Sessione Capodrise verificata:", ", ".join(evidence), flush=True)
            selected = queries or DEFAULT_QUERIES
            results = {}
            for idx, q in enumerate(selected, 1):
                total, pp = await harvest_query(page, ctx.request, q)
                results[q] = (total, pp)
                print(f"[{idx}/{len(selected)}] {q}: {len(pp)} prodotti con prezzo positivo / {total} dichiarati", flush=True)
                if idx < len(selected):
                    await asyncio.sleep(1.0)

            saved = save_db(results, db)
            audit = {
                "status": "OK",
                "supermarket": "Conad",
                "store_code": STORE_CODE,
                "store_name": STORE_NAME,
                "store_address": STORE_ADDRESS,
                "session_evidence": evidence,
                "queries": len(results),
                "unique_positive_price_products": saved,
                "db": str(db),
                "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            (diagnostics / "conad_capodrise_audit.json").write_text(
                json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(json.dumps(audit, ensure_ascii=False, indent=2))
        finally:
            await ctx.close()


def main():
    ap = argparse.ArgumentParser(description="Smart Campania - Conad Superstore Capodrise 010548")
    ap.add_argument("--bootstrap", action="store_true", help="Apre Chrome normale per la selezione manuale iniziale del negozio")
    ap.add_argument("--profile", default=str(profile_dir()))
    ap.add_argument("--db", default="prezzi_conad_capodrise.db")
    ap.add_argument("--query", action="append", help="Query catalogo; ripetibile. Se assente usa il set completo")
    ap.add_argument("--smoke", action="store_true", help="Test rapido: latte, pasta, uova")
    ap.add_argument("--headful", action="store_true", help="Mostra Chrome durante l'aggiornamento")
    ap.add_argument("--diagnostics", default="diagnostics_conad_capodrise")
    args = ap.parse_args()

    profile = Path(args.profile).expanduser().resolve()
    if args.bootstrap:
        bootstrap(profile)
        return

    qs = ["latte", "pasta", "uova"] if args.smoke else args.query
    try:
        asyncio.run(update(
            profile=profile,
            db=Path(args.db).resolve(),
            queries=qs,
            headful=args.headful,
            diagnostics=Path(args.diagnostics).resolve(),
        ))
    except Exception as e:
        print(f"ERRORE CONAD CAPODRISE: {e}", file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
