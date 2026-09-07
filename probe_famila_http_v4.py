#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Famila HTTP Probe V4 - Smart Campania

Scopo:
- verificare se il catalogo pubblico Famila Sud / CosiComodo e' interrogabile
  in modo affidabile tramite HTTP diretto;
- NON usa Playwright/Selenium/browser automation;
- raccoglie diagnostica riproducibile in JSON + HTML campione;
- emette DIRECT_HTTP_CATALOG_VALIDATED soltanto se i controlli minimi
  su HTTP, presenza catalogo e segnali di prodotto/prezzo risultano coerenti.

Nota importante:
Questo e' un PROBE diagnostico, non un updater definitivo.
Non inventa prezzi, quantita', disponibilita' o store-id.
"""

from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


BASE = "https://www.cosicomodo.it"
BRAND_PREFIX = "/familasud/"
OUT = Path("famila_http_probe_v4_output")
OUT.mkdir(parents=True, exist_ok=True)

# URL pubbliche ufficiali gia' note/validate come punto di ingresso tecnico.
# Non sono usate per assumere che il negozio target Caserta sia uno di questi.
SEED_URLS = [
    f"{BASE}/familasud/teverola/ricerca",
    f"{BASE}/familasud/foggia-addedda/reparti/tutto-per-la-casa/c/10016",
]

SELECTOR_URL = (
    f"{BASE}/famila/global/homedeliverystoreselector"
    "?redirectUrl=%2Ffamila%2Fglobal%2Fstoreselector"
)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/152.0.0.0 Safari/537.36"
)

PRICE_RE = re.compile(r"(?:€\s*|EUR\s*)?(\d{1,4}[,.]\d{2})(?:\s*€)?", re.I)
UNIT_PRICE_RE = re.compile(
    r"(?:€\s*)?\d{1,4}[,.]\d{2}\s*(?:/|al\s+)(?:kg|kilo|l|lt|litro|pz|pezzo)",
    re.I,
)
PRODUCT_HINTS = [
    "product",
    "prodotto",
    "product-item",
    "product-card",
    "product-tile",
    "add-to-cart",
    "aggiungi",
]


@dataclass
class FetchResult:
    url: str
    status: Optional[int]
    final_url: Optional[str]
    elapsed_s: Optional[float]
    content_type: Optional[str]
    bytes: int
    error: Optional[str]


@dataclass
class PageAnalysis:
    url: str
    status: Optional[int]
    final_url: Optional[str]
    title: Optional[str]
    html_bytes: int
    product_nodes: int
    price_matches: int
    positive_price_matches: int
    unit_price_matches: int
    category_links: int
    product_links: int
    pagination_links: int
    has_store_context: bool
    has_search_box: bool
    has_reparti: bool
    has_block_page_signals: bool
    sample_prices: List[str]
    sample_product_links: List[str]
    sample_category_links: List[str]


def session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "it-IT,it;q=0.9,en;q=0.7",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Connection": "keep-alive",
        }
    )
    return s


def fetch(s: requests.Session, url: str, timeout: int = 45) -> Tuple[FetchResult, str]:
    start = time.perf_counter()
    try:
        r = s.get(url, timeout=timeout, allow_redirects=True)
        elapsed = round(time.perf_counter() - start, 3)
        text = r.text if r.content else ""
        return (
            FetchResult(
                url=url,
                status=r.status_code,
                final_url=r.url,
                elapsed_s=elapsed,
                content_type=r.headers.get("content-type"),
                bytes=len(r.content or b""),
                error=None,
            ),
            text,
        )
    except Exception as exc:
        elapsed = round(time.perf_counter() - start, 3)
        return (
            FetchResult(
                url=url,
                status=None,
                final_url=None,
                elapsed_s=elapsed,
                content_type=None,
                bytes=0,
                error=f"{type(exc).__name__}: {exc}",
            ),
            "",
        )


def normalize_price(raw: str) -> Optional[float]:
    try:
        return float(raw.replace(".", "").replace(",", "."))
    except Exception:
        return None


def unique_keep_order(values: List[str]) -> List[str]:
    seen = set()
    out = []
    for v in values:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def analyze_page(url: str, fr: FetchResult, html: str) -> PageAnalysis:
    soup = BeautifulSoup(html, "html.parser") if html else BeautifulSoup("", "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else None
    text = soup.get_text(" ", strip=True)
    low = (html + " " + text).lower()

    product_nodes = 0
    for tag in soup.find_all(True):
        attrs = " ".join(
            [str(tag.get("id", "")), " ".join(tag.get("class", []) if isinstance(tag.get("class"), list) else [str(tag.get("class", ""))])]
        ).lower()
        if any(h in attrs for h in PRODUCT_HINTS):
            product_nodes += 1

    prices_raw = PRICE_RE.findall(text)
    positive = []
    for p in prices_raw:
        val = normalize_price(p)
        if val is not None and val > 0:
            positive.append(p)

    category_links: List[str] = []
    product_links: List[str] = []
    pagination_links: List[str] = []

    base_host = urlparse(BASE).netloc
    for a in soup.find_all("a", href=True):
        href = a.get("href", "").strip()
        if not href or href.startswith("#") or href.startswith("javascript:"):
            continue
        abs_url = urljoin(fr.final_url or url, href)
        parsed = urlparse(abs_url)
        if parsed.netloc and parsed.netloc != base_host:
            continue
        path_q = parsed.path + ("?" + parsed.query if parsed.query else "")
        low_href = path_q.lower()
        label = a.get_text(" ", strip=True).lower()

        if BRAND_PREFIX in low_href and (
            "/reparti/" in low_href or "/c/" in low_href or "categorypath" in low_href
        ):
            category_links.append(abs_url)

        if BRAND_PREFIX in low_href and any(
            token in low_href for token in ["/p/", "/product/", "/prodotto/"]
        ):
            product_links.append(abs_url)

        if any(token in low_href for token in ["page=", "?q="]) and any(
            token in label for token in ["success", "avanti", "pagina", "carica"]
        ):
            pagination_links.append(abs_url)

    # Molti storefront e-commerce non espongono URL prodotto classici nel markup.
    # Aggiungiamo solo link interni con segnali espliciti di prodotto, senza
    # considerarli automaticamente veri prodotti ai fini del verdetto.
    if not product_links:
        for a in soup.find_all("a", href=True):
            href = a.get("href", "").strip()
            label = a.get_text(" ", strip=True).lower()
            classes = " ".join(a.get("class", []) if isinstance(a.get("class"), list) else []).lower()
            if "product" in classes or "prodot" in classes or "prodot" in label:
                abs_url = urljoin(fr.final_url or url, href)
                if urlparse(abs_url).netloc == base_host:
                    product_links.append(abs_url)

    block_signals = any(
        x in low
        for x in [
            "access denied",
            "forbidden",
            "captcha",
            "cf-chl-",
            "cloudflare ray id",
            "enable cookies",
            "temporarily blocked",
        ]
    )

    has_store_context = any(
        x in low
        for x in [
            "ritiro a:",
            "ritiro a",
            "famila -",
            "vedi consegna & orario",
            "verifica le disponibilità di date per il ritiro",
        ]
    )
    has_search_box = "cerca un prodotto" in low or "search" in low
    has_reparti = "reparti" in low and any(
        x in low
        for x in ["frutta e verdura", "latte, burro, uova", "tutto per la casa"]
    )

    sample_prices = unique_keep_order(prices_raw)[:20]
    return PageAnalysis(
        url=url,
        status=fr.status,
        final_url=fr.final_url,
        title=title,
        html_bytes=len(html.encode("utf-8", errors="ignore")),
        product_nodes=product_nodes,
        price_matches=len(prices_raw),
        positive_price_matches=len(positive),
        unit_price_matches=len(UNIT_PRICE_RE.findall(text)),
        category_links=len(unique_keep_order(category_links)),
        product_links=len(unique_keep_order(product_links)),
        pagination_links=len(unique_keep_order(pagination_links)),
        has_store_context=has_store_context,
        has_search_box=has_search_box,
        has_reparti=has_reparti,
        has_block_page_signals=block_signals,
        sample_prices=sample_prices,
        sample_product_links=unique_keep_order(product_links)[:20],
        sample_category_links=unique_keep_order(category_links)[:30],
    )


def save_html(name: str, html: str) -> None:
    (OUT / name).write_text(html, encoding="utf-8", errors="ignore")


def main() -> int:
    s = session()
    report: Dict[str, object] = {
        "probe": "Famila HTTP Probe V4",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "base": BASE,
        "brand": "Famila Sud / CosiComodo",
        "browser_automation": False,
        "target_area": "Caserta/Maddaloni",
        "notes": [
            "Probe diagnostico: non costruisce ancora il database prodotti.",
            "I seed store servono a validare la meccanica HTTP pubblica e non vengono assunti come negozio target Caserta.",
            "Nessun prezzo, quantita, disponibilita o store-id viene inventato.",
        ],
        "selector": {},
        "pages": [],
        "followups": [],
    }

    # 1) Selector ufficiale: ci serve per capire se HTTP puro viene bloccato.
    selector_fetch, selector_html = fetch(s, SELECTOR_URL)
    selector_low = selector_html.lower()
    selector_ok = (
        selector_fetch.status == 200
        and "scrivi il tuo cap o comune" in selector_low
        and "capcomuneinput" in selector_low
    )
    report["selector"] = {
        **asdict(selector_fetch),
        "contains_capComuneInput": "capcomuneinput" in selector_low,
        "contains_placeholder": "scrivi il tuo cap o comune" in selector_low,
        "validated": selector_ok,
    }
    save_html("01_selector.html", selector_html)

    analyses: List[PageAnalysis] = []

    # 2) Seed catalog pages ufficiali.
    for idx, url in enumerate(SEED_URLS, start=1):
        fr, html = fetch(s, url)
        pa = analyze_page(url, fr, html)
        analyses.append(pa)
        report["pages"].append(asdict(pa))  # type: ignore[index]
        save_html(f"1{idx}_seed_{idx}.html", html)
        time.sleep(1.5)

    # 3) Follow-up prudente: prendiamo max 3 category link reali emersi
    #    dal markup e li richiamiamo via HTTP. Nessun URL viene inventato.
    follow_urls: List[str] = []
    for pa in analyses:
        follow_urls.extend(pa.sample_category_links)
    follow_urls = unique_keep_order(follow_urls)[:3]

    follow_analyses: List[PageAnalysis] = []
    for idx, url in enumerate(follow_urls, start=1):
        fr, html = fetch(s, url)
        pa = analyze_page(url, fr, html)
        follow_analyses.append(pa)
        report["followups"].append(asdict(pa))  # type: ignore[index]
        save_html(f"2{idx}_follow_category_{idx}.html", html)
        time.sleep(1.5)

    all_pages = analyses + follow_analyses

    http_200_pages = sum(1 for p in all_pages if p.status == 200)
    blocked_pages = sum(1 for p in all_pages if p.has_block_page_signals or p.status in {403, 429, 492})
    catalog_context_pages = sum(1 for p in all_pages if p.has_store_context and p.has_reparti)
    pages_with_prices = sum(1 for p in all_pages if p.positive_price_matches > 0)
    pages_with_product_signals = sum(
        1 for p in all_pages if p.product_nodes > 0 or p.product_links > 0
    )
    unit_price_pages = sum(1 for p in all_pages if p.unit_price_matches > 0)

    # Fail-closed: il verdetto forte richiede TUTTO quanto segue.
    direct_http_validated = bool(
        selector_ok
        and len(analyses) >= 2
        and all(p.status == 200 for p in analyses)
        and blocked_pages == 0
        and catalog_context_pages >= 1
        and pages_with_product_signals >= 1
        and pages_with_prices >= 1
    )

    # Un secondo livello distingue il caso in cui HTTP funziona ma il markup
    # non espone abbastanza dati per costruire subito un updater robusto.
    updater_candidate = bool(
        direct_http_validated
        and (
            len(follow_analyses) >= 1
            or sum(p.category_links for p in analyses) > 0
        )
        and pages_with_prices >= 1
    )

    if updater_candidate:
        verdict = "DIRECT_HTTP_CATALOG_VALIDATED"
        next_action = "BUILD_FAMILA_UPDATER_HTTP_V1"
    elif direct_http_validated:
        verdict = "DIRECT_HTTP_ACCESS_VALIDATED_BUT_CATALOG_TRAVERSAL_INCOMPLETE"
        next_action = "STOP_AND_REVIEW_ARTIFACT_BEFORE_ANY_NEW_PROBE"
    elif blocked_pages > 0:
        verdict = "DIRECT_HTTP_BLOCKED_OR_UNSTABLE"
        next_action = "STOP_FAMILA_AND_MOVE_TO_NEXT_SUPERMARKET"
    else:
        verdict = "DIRECT_HTTP_NOT_SUFFICIENTLY_VALIDATED"
        next_action = "STOP_AND_REVIEW_ARTIFACT_BEFORE_ANY_NEW_PROBE"

    report["summary"] = {
        "selector_validated": selector_ok,
        "tested_catalog_pages": len(all_pages),
        "http_200_pages": http_200_pages,
        "blocked_or_error_pages": blocked_pages,
        "catalog_context_pages": catalog_context_pages,
        "pages_with_product_signals": pages_with_product_signals,
        "pages_with_positive_prices": pages_with_prices,
        "pages_with_unit_price_signals": unit_price_pages,
        "discovered_category_links": sum(p.category_links for p in all_pages),
        "discovered_product_links": sum(p.product_links for p in all_pages),
        "verdict": verdict,
        "next_action": next_action,
    }

    (OUT / "famila_http_probe_v4.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines = [
        "# Famila HTTP Probe V4 - Risultato",
        "",
        f"- Verdict: **{verdict}**",
        f"- Next action: **{next_action}**",
        f"- Selector HTTP validato: **{selector_ok}**",
        f"- Pagine catalogo testate: **{len(all_pages)}**",
        f"- HTTP 200: **{http_200_pages}**",
        f"- Bloccate/errori: **{blocked_pages}**",
        f"- Pagine con segnali prodotto: **{pages_with_product_signals}**",
        f"- Pagine con prezzi positivi rilevati: **{pages_with_prices}**",
        f"- Pagine con segnali €/kg-L-PZ: **{unit_price_pages}**",
        "",
        "Il JSON completo e gli HTML campione sono inclusi nello stesso artifact.",
        "Il workflow verde da solo NON equivale a validazione: usare il campo verdict.",
    ]
    (OUT / "RISULTATO.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"\nVERDICT={verdict}")
    print(f"NEXT_ACTION={next_action}")

    # Il probe deve comunque lasciare l'artifact anche se il catalogo non e'
    # validato. Falliamo il job solo per crash tecnici del programma, non per
    # esito diagnostico negativo.
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Interrotto.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        OUT.mkdir(parents=True, exist_ok=True)
        crash = {
            "probe": "Famila HTTP Probe V4",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "verdict": "PROBE_CRASHED",
            "error": f"{type(exc).__name__}: {exc}",
        }
        (OUT / "CRASH.json").write_text(
            json.dumps(crash, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(crash, ensure_ascii=False, indent=2), file=sys.stderr)
        raise
