# Conad Updater V5 AUTO — store 010548 Capodrise

Obiettivo: eliminare il cURL manuale e i token salvati.

La V5 apre Conad con Chromium/Playwright, passa dal normale onboarding del sito,
seleziona il servizio di ritiro e il punto vendita di Via Retella a Capodrise,
poi verifica nel codice pagina che `pointOfService.name == 010548`.

Solo dopo la verifica raccoglie i prodotti. Per ogni ricerca usa i pulsanti di
paginazione reali `data-page` del sito e blocca l'aggiornamento se il numero
raccolto non coincide con il totale dichiarato.

Non contiene cookie, JWT, password, credenziali o token.

IMPORTANTE: il flusso live può cambiare lato Conad. In caso di modifica della UI
l'azione fallisce senza sovrascrivere il DB e carica `conad-diagnostics` con HTML
e screenshot per correggere i selettori. Non tenta di aggirare CAPTCHA o protezioni.

Installazione GitHub:
- copiare i file nel repository Conad;
- copiare `update.yml` in `.github/workflows/update.yml`;
- eseguire manualmente `Aggiorna prezzi Conad Capodrise` la prima volta.
