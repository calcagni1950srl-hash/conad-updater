import time
import requests
import famila_updater_full_v1 as base

# V2: stessa estrazione/audit V1, ma con ritmo prudente e rinnovo sessione
# quando il WAF CosìComodo risponde 481 dopo molte pagine consecutive.

MIN_DELAY_SECONDS = 0.65
WAF_COOLDOWN_SECONDS = 12.0


def refresh_store_session(session: requests.Session):
    url = f"{base.BASE_WEB}/{base.SITE}/{base.STORE_ALIAS}/reparti/prodotti-alimentari/c/10012"
    response = session.get(
        url,
        headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": base.BASE_WEB + "/",
        },
        timeout=60,
    )
    response.raise_for_status()
    required = {"familasud_anonymous_preferred_base_store", "pointOfService"}
    if not required.issubset(set(session.cookies.keys())):
        raise RuntimeError(
            f"Rinnovo sessione Famila incompleto: cookie mancanti {required - set(session.cookies.keys())}"
        )


def fetch_page_resilient(session, category_code, page, retries=7):
    endpoint = (
        f"{base.API_BASE}/{base.SITE}/stores/{base.STORE_ALIAS}/users/"
        f"{base.USER_ID}/products/search-by-category"
    )
    params = {
        "categoryCode": category_code,
        "currentPage": page,
        "pageSize": 20,
        "fields": "FULL",
    }
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": base.BASE_WEB,
        "Referer": f"{base.BASE_WEB}/{base.SITE}/{base.STORE_ALIAS}/reparti/",
        "Sec-Fetch-Site": "same-site",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
    }

    last_status = None
    last_body = ""
    for attempt in range(retries):
        # Ritmo deliberatamente conservativo: il catalogo deve essere stabile,
        # non veloce a costo di pagine perse.
        time.sleep(MIN_DELAY_SECONDS)
        try:
            r = session.get(endpoint, params=params, headers=headers, timeout=60)
            last_status = r.status_code
            last_body = r.text[:800]

            if r.status_code == 200:
                data = r.json()
                if not isinstance(data, dict):
                    raise RuntimeError("Risposta Famila JSON non valida")
                pagination = data.get("pagination") or {}
                returned_page = pagination.get("currentPage")
                if returned_page is not None and int(returned_page) != int(page):
                    raise RuntimeError(
                        f"Pagina Famila inattesa: chiesta={page}, ricevuta={returned_page}"
                    )
                return data

            if r.status_code == 481:
                # WAF Link11: aspetta e rinnova i cookie del punto vendita.
                wait = WAF_COOLDOWN_SECONDS * (attempt + 1)
                print(
                    f"WAF 481 categoria={category_code} pagina={page}; "
                    f"cooldown {wait:.0f}s e rinnovo sessione"
                )
                time.sleep(wait)
                refresh_store_session(session)
                continue

            if r.status_code in {429, 500, 502, 503, 504}:
                time.sleep(3.0 * (attempt + 1))
                refresh_store_session(session)
                continue

            raise RuntimeError(
                f"Famila HTTP {r.status_code}: {r.text[:500]}"
            )

        except (requests.RequestException, ValueError) as exc:
            if attempt == retries - 1:
                raise RuntimeError(
                    f"Errore Famila categoria={category_code} page={page}: {exc}"
                ) from exc
            time.sleep(3.0 * (attempt + 1))
            refresh_store_session(session)

    raise RuntimeError(
        f"Famila fetch fallito categoria={category_code} page={page}; "
        f"status={last_status}; body={last_body}"
    )


# Monkey-patch intenzionale: base.main() usa questa funzione per TUTTE le pagine,
# lasciando immutati parsing, deduplica, SQLite e audit della V1.
base.fetch_page = fetch_page_resilient

if __name__ == "__main__":
    base.main()
