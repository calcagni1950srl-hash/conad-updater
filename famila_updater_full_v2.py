import time
import requests
import famila_updater_full_v1 as base

# V2 definitiva per GitHub Actions:
# - NON apre più la pagina HTML Famila, che può essere bloccata dal WAF;
# - usa direttamente l'API ufficiale OCC, già verificata su Teverola;
# - una sola sessione, richieste sequenziali e lente;
# - sui blocchi temporanei aspetta e riprova la stessa pagina.

MIN_DELAY_SECONDS = 1.05
WAF_CODES = {429, 474, 481, 482, 500, 502, 503, 504}


def api_only_session_for_store():
    session = requests.Session()
    session.headers.update({
        "User-Agent": base.UA,
        "Accept-Language": "it-IT,it;q=0.9,en;q=0.7",
        "Connection": "close",
    })
    point = {
        "name": "MEGAMARK_FAMILA_163711",
        "displayName": "Famila - Teverola",
        "selexCode": "163711",
        "site": base.SITE,
        "store": base.STORE_ALIAS,
    }
    return session, point


def cool_down(attempt, status):
    if status in {474, 481, 482}:
        return min(120.0, 25.0 * (attempt + 1))
    if status == 429:
        return min(90.0, 15.0 * (attempt + 1))
    return min(45.0, 5.0 * (attempt + 1))


def fetch_page_resilient(session, category_code, page, retries=8):
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
        "Origin": base.BASE_WEB,
        "Referer": (
            f"{base.BASE_WEB}/{base.SITE}/{base.STORE_ALIAS}/"
            f"reparti/prodotti-alimentari/c/10012"
        ),
        "Sec-Fetch-Site": "same-site",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
        "Connection": "close",
    }

    last_status = None
    last_body = ""
    for attempt in range(retries):
        time.sleep(MIN_DELAY_SECONDS)
        try:
            response = session.get(
                endpoint,
                params=params,
                headers=headers,
                timeout=60,
            )
            last_status = response.status_code
            last_body = response.text[:800]

            if response.status_code == 200:
                data = response.json()
                if not isinstance(data, dict):
                    raise RuntimeError("Risposta Famila JSON non valida")

                pagination = data.get("pagination") or {}
                returned_page = pagination.get("currentPage")
                if returned_page is None or int(returned_page) != int(page):
                    raise RuntimeError(
                        f"Pagina Famila inattesa: chiesta={page}, "
                        f"ricevuta={returned_page}"
                    )
                return data

            if response.status_code in WAF_CODES:
                wait = cool_down(attempt, response.status_code)
                print(
                    f"Famila temporaneamente limitato HTTP {response.status_code} "
                    f"categoria={category_code} pagina={page}; attesa {wait:.0f}s"
                )
                time.sleep(wait)
                # La API è stata verificata senza cookie: ripartire puliti evita
                # di trascinare eventuali identificatori di sessione bloccati.
                session.cookies.clear()
                continue

            raise RuntimeError(
                f"Famila HTTP {response.status_code}: {response.text[:500]}"
            )

        except (requests.RequestException, ValueError) as exc:
            if attempt == retries - 1:
                raise RuntimeError(
                    f"Errore Famila categoria={category_code} page={page}: {exc}"
                ) from exc
            wait = min(45.0, 5.0 * (attempt + 1))
            print(
                f"Errore rete Famila categoria={category_code} pagina={page}: "
                f"{exc}; attesa {wait:.0f}s"
            )
            time.sleep(wait)
            session.cookies.clear()

    raise RuntimeError(
        f"Famila fetch fallito categoria={category_code} page={page}; "
        f"status={last_status}; body={last_body}"
    )


# Sostituiamo solo trasporto/sessione. Parsing, deduplica, SQLite e audit
# restano quelli della V1.
base.session_for_store = api_only_session_for_store
base.fetch_page = fetch_page_resilient

if __name__ == "__main__":
    base.main()
