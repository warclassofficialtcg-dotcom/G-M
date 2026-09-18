# G & M – gestione appuntamenti palestra & massaggi

Web-app per la prenotazione di allenamenti e massaggi con conferma del titolare via WhatsApp.

## Avvio rapido

1. Doppio click su `avvia.bat` (oppure `python app.py`).
2. Apri <http://localhost:5000>.
3. Al primo avvio viene creato `config.json`: **modificalo** e riavvia.

```json
{
  "OWNER_WHATSAPP": "393933379797",   // il TUO numero WhatsApp: 39 + numero, senza + e senza spazi
  "APP_NAME": "G & M",            // nome mostrato nell'app
  "OWNER_NAME": "Andrea",
  "ADMIN_EMAIL": "admin@palestra.it", // email con cui accedi come titolare
  "ADMIN_PASSWORD": "admin123",       // CAMBIALA prima di darla ai clienti
  "BASE_URL": "http://localhost:5000",// indirizzo pubblico del sito (vedi "Pubblicazione")
  "MAX_GYM_PER_SLOT": 2,              // persone massime nella stessa ora di palestra (classe completa)
  "PORT": 5000
}
```

> L'utente titolare viene creato automaticamente al primo avvio con `ADMIN_EMAIL` / `ADMIN_PASSWORD`.
> Se cambi la password nel config dopo la creazione, non viene aggiornata: cancella `gym.db` per ripartire da zero.

## Come funziona

**Cliente**
- Si registra con nome, email, telefono e password.
- Sezione **Allenamento**: pacchetto attivo, scheda, dieta, listino (70€ = 2/settimana, 80€ = 3/settimana, 30€ scheda o dieta).
- Sezione **Servizi** (Allenamenti + Massaggi nella stessa schermata, con due pulsanti in alto per saltare alla parte voluta): abbonamenti palestra, listino massaggi dal volantino (olistici/estetici e tecnici/sportivi, con benefici e prezzi), percorsi mensili Standard 150€ (4 massaggi) e Benessere 200€ (6 massaggi) acquistabili online, i propri massaggi.
- **Calendario** settimanale unico con orari diversi per tipo — palestra Lun–Ven 11–12 e 15–19, Sab 11–12 e 15–16; massaggi Lun–Ven 10–12 e 15–19, Sab 10–12 (13–15 pausa tutti i giorni; lunedì mattina chiuso, si prenota dalle 15) — clicca il giorno → l'ora → Palestra o Massaggio (con scelta del trattamento) → **Prenota**.
  Si apre WhatsApp con il messaggio già pronto da inviare al titolare (contiene il link di conferma).
- Il calendario è visibile a tutti: mostra chi si allena e quando, e permette di **unirsi a una lezione** già confermata.

**Regole**
- Massaggio: l'orario è esclusivo. Se c'è già qualcuno (palestra o massaggio) non è prenotabile.
- Palestra: se nell'orario c'è già un massaggio non è prenotabile. Se c'è già una lezione confermata, ci si unisce direttamente (fino a `MAX_GYM_PER_SLOT` persone).
- **Palestra solo con abbonamento attivo** (70€ = 2/settimana, 80€ = 3/settimana): senza abbonamento valido nel giorno scelto la prenotazione viene rifiutata e l'app rimanda alla sezione Allenamento. L'abbonamento vale **un mese esatto dal giorno del pagamento** (se ne paghi uno mentre l'attuale è ancora valido, il nuovo parte dalla scadenza). Alla scadenza si ripaga il mese dalla sezione Allenamento (nessun addebito automatico).
- I massaggi non richiedono abbonamento: con un percorso attivo vale il limite di 4/6 massaggi nel periodo.
- Prima di confermare, la finestra di prenotazione mostra l'**anteprima del messaggio WhatsApp** già compilato con giorno, ora e trattamento.
- Da "I miei" ogni appuntamento ha **Sposta**: si apre il calendario con gli orari disponibili evidenziati, si tocca il nuovo orario e l'appuntamento viene spostato (torna "in attesa" e il titolare riceve su WhatsApp il messaggio con vecchio e nuovo orario).
- Il singolo massaggio senza percorso si paga in studio (il prezzo compare nel messaggio WhatsApp).
- Non si possono prenotare orari già passati.

**Titolare**
- Riceve il messaggio WhatsApp, clicca il link → pagina con **Conferma / Rifiuta** (non serve login).
- Dopo la conferma l'appuntamento appare nel calendario generale.
- Sezione **Allenamento** (per il titolare): elenco **iscritti in palestra** con abbonamento, data di iscrizione, scadenza e stato; toccando un iscritto si apre il profilo dove caricare **PDF di scheda e dieta** (li vede solo quel cliente, nella sua sezione Allenamento → I tuoi file).
- Sezione **Gestione**: richieste in attesa, appuntamenti confermati, elenco clienti (con pacchetti, scheda/dieta testuali, PDF), pagamenti.

Listino pacchetti (`PACKAGES`), catalogo massaggi (`MASSAGES`) e orari (`GYM_HOURS`, `MASSAGE_HOURS`) si modificano in cima ad `app.py`.

## Pagamenti online (Stripe)

Stesso schema di SoundUp: Stripe Checkout, nessuna libreria, webhook firmato, attivazione idempotente.

1. Copia `.env.example` in `.env` e compila:
   - `PAYMENTS_PROVIDER=stripe` (lascia `simulated` per provare senza addebiti: il pulsante "Acquista" attiva subito il pacchetto)
   - `STRIPE_SECRET_KEY` e `STRIPE_PUBLISHABLE_KEY` da dashboard.stripe.com → Developers → API keys (copia la chiave **intera** col pulsante di copia)
   - `STRIPE_WEBHOOK_SECRET`: Developers → Webhooks → Add endpoint → URL `<PUBLIC_URL>/api/payments/stripe-webhook`, evento `checkout.session.completed` → Signing secret
   - `PUBLIC_URL` (se vuoto usa `BASE_URL`)
2. Il cliente, in **Allenamento → Listino**, clicca *Acquista* → paga su Stripe → torna sull'app con il pacchetto attivo (30 giorni; un rinnovo anticipato parte alla scadenza di quello in corso).
3. Il titolare vede tutto in **Gestione → Pagamenti** (totale incassato, imponibile/IVA per riga). Aprendo la scheda vengono anche recuperati eventuali pagamenti rimasti in sospeso.

I prezzi sono IVA inclusa (`VAT_RATE=0.22`); i pacchetti inseriti a mano dal titolare continuano a funzionare come prima.

## Pubblicazione online (necessaria per il link WhatsApp, Stripe e l'app installabile)

**Attenzione: Netlify non può ospitare questa app.** Netlify pubblica solo siti statici (HTML/JS) e funzioni Node: qui c'è un backend Python con database, quindi serve un hosting Python. Il più simile e gratuito è **Render** (già usato per SoundUp), in alternativa Railway o PythonAnywhere.

### Render (consigliato)
1. Crea un repository su GitHub e carica il contenuto dello zip (oppure `git init` nella cartella e push).
2. Su render.com: **New + → Blueprint** → collega il repository. Render legge `render.yaml` e configura tutto.
3. In **Environment** compila: `BASE_URL` (l'indirizzo che Render ti dà, es. `https://gm-prenotazioni.onrender.com`), `ADMIN_EMAIL`, `ADMIN_PASSWORD`; per gli incassi `PAYMENTS_PROVIDER=stripe` e le chiavi Stripe.
4. Apri l'indirizzo: la prima apertura sul piano gratuito può richiedere 30-60 secondi.

Piano gratuito: nessun disco, quindi **database e PDF caricati si azzerano a ogni deploy**. Per tenere clienti e prenotazioni: `plan: starter` e togli i commenti a `DATA_DIR` e al blocco `disk` in `render.yaml`.

Tutta la configurazione può arrivare da variabili d'ambiente (`APP_NAME`, `OWNER_WHATSAPP`, `BASE_URL`, `ADMIN_EMAIL`, `ADMIN_PASSWORD`, `SECRET_KEY`, `MAX_GYM_PER_SLOT`, `DATA_DIR`), che hanno la precedenza su `config.json`.

### In locale / rete Wi‑Fi
`avvia.bat`, poi `BASE_URL: "http://<IP del PC>:5000"` nel config: funziona solo per telefoni sulla stessa rete. Per un test da fuori: `ngrok http 5000`.

## App installabile (PWA)
Il sito è installabile come app sul telefono: aperto da **https**, Chrome/Android propone "Installa app" e su iPhone si usa *Condividi → Aggiungi a Home*. Icona e nome sono in `static/manifest.webmanifest` e `static/icons/` (logo rigenerabile con `python scripts/make_icons.py`).

## File

- `app.py` – backend Flask + SQLite (`gym.db` creato automaticamente)
- `templates/index.html` – applicazione (single page)
- `templates/conferma.html` – pagina di conferma per il titolare
- `static/app.js`, `static/style.css` – frontend
- `render.yaml`, `Procfile`, `requirements.txt` – pubblicazione su Render/Railway (gunicorn)
- `static/manifest.webmanifest`, `static/sw.js`, `static/icons/` – app installabile e logo
- `payments.py` – incasso Stripe (checkout, ritorno, webhook, riconciliazione)
- `config.json` – configurazione (generato al primo avvio)
- `.env` – chiavi Stripe (mai nel codice, vedi `.env.example`)
