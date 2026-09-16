# -*- coding: utf-8 -*-
"""G & M - incasso dei pacchetti (Stripe Checkout).

COSA SI VENDE
    I pacchetti del listino (70€ 2 allenamenti/sett., 80€ 3 allenamenti/sett.,
    30€ scheda, 30€ dieta). Pagato il pacchetto, viene inserita una riga nella
    tabella `packages` dell'utente con validità 30 giorni: la stessa riga che il
    titolare può inserire a mano dalla sezione Gestione.

COME FUNZIONA L'INCASSO (stesso schema di SoundUp)
    1. il cliente sceglie un pacchetto -> creiamo una Checkout Session Stripe e
       registriamo una riga 'pending' nella tabella payments;
    2. il cliente paga sulla pagina di Stripe (noi non vediamo mai la carta);
    3. Stripe conferma: sia al ritorno del cliente (success_url) sia via
       webhook server-to-server. Solo allora attiviamo il pacchetto.

    L'attivazione passa da grant_once(), idempotente: la conferma può arrivare
    due volte (ritorno + webhook) ma il pacchetto si crea una volta sola. Non ci
    si fida MAI del browser: prima di attivare si richiede a Stripe lo stato
    della sessione.

CONFIGURAZIONE (file .env accanto ad app.py, oppure variabili d'ambiente)
    PAYMENTS_PROVIDER        'stripe' oppure 'simulated' (default: sviluppo, nessun incasso reale)
    STRIPE_SECRET_KEY        sk_live_... / sk_test_...   (segreto: mai nel codice)
    STRIPE_PUBLISHABLE_KEY   pk_live_... / pk_test_...
    STRIPE_WEBHOOK_SECRET    whsec_...  (Developers -> Webhooks -> endpoint <PUBLIC_URL>/api/payments/stripe-webhook)
    PUBLIC_URL               es. https://gm.onrender.com (se manca si usa BASE_URL di config.json)
    VAT_RATE                 0.22 (IVA Italia; i prezzi del listino sono IVA inclusa)
"""
import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PACKAGE_DAYS = 30
# pacchetti "della stessa famiglia": il rinnovo parte alla scadenza di quello attivo
FAMILIES = {
    "70": ["70", "80"], "80": ["70", "80"],
    "standard": ["standard", "benessere"], "benessere": ["standard", "benessere"],
}


# --------------------------------------------------------------------------- #
#  Configurazione
# --------------------------------------------------------------------------- #
def _load_env_file():
    """Il .env riempie solo i buchi: chi lancia il processo ha l'ultima parola."""
    env_file = os.path.join(BASE_DIR, ".env")
    if not os.path.exists(env_file):
        return
    try:
        with open(env_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip("'\""))
    except OSError:
        pass


_load_env_file()


def _env(key, default=""):
    return (os.environ.get(key) or default).strip()


def provider():
    p = _env("PAYMENTS_PROVIDER", "simulated").lower()
    return p if p in ("stripe", "simulated") else "simulated"


def stripe_secret_key():
    return _env("STRIPE_SECRET_KEY")


def stripe_publishable_key():
    return _env("STRIPE_PUBLISHABLE_KEY")


def stripe_webhook_secret():
    return _env("STRIPE_WEBHOOK_SECRET")


def public_url(fallback=""):
    return (_env("PUBLIC_URL") or fallback).rstrip("/")


try:
    VAT_RATE = float(_env("VAT_RATE", "0.22"))
except ValueError:
    VAT_RATE = 0.22


def vat_breakdown(gross, rate=None):
    """Scorpora l'IVA da un prezzo IVA inclusa."""
    rate = VAT_RATE if rate is None else rate
    net = round(gross / (1 + rate), 2)
    return {"gross": round(gross, 2), "net": net, "vat": round(gross - net, 2), "rate": rate}


def is_stripe_live():
    return provider() == "stripe" and bool(stripe_secret_key())


def configuration_problem():
    """Testo del problema di configurazione, o None se Stripe è pronto."""
    if provider() != "stripe":
        return None
    try:
        _check_key(stripe_secret_key())
    except ValueError as e:
        return str(e)
    if not stripe_webhook_secret():
        return ("Manca STRIPE_WEBHOOK_SECRET: i pagamenti si attivano solo quando il cliente "
                "torna sull'app. Configura il webhook per non perdere gli incassi.")
    return None


def status():
    return {
        "provider": provider(),
        "live": is_stripe_live(),
        "simulated": provider() == "simulated",
        "stripe_pk": stripe_publishable_key() if is_stripe_live() else "",
        "problem": configuration_problem(),
        "vat_rate": VAT_RATE,
    }


# --------------------------------------------------------------------------- #
#  Schema
# --------------------------------------------------------------------------- #
SCHEMA = """
CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    provider TEXT NOT NULL,             -- 'stripe' | 'simulated'
    order_id TEXT NOT NULL UNIQUE,      -- id della Checkout Session (cs_...) o ordine simulato
    package_type TEXT NOT NULL,         -- '70' | '80' | 'scheda' | 'dieta'
    label TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,      -- IVA inclusa
    vat_rate REAL NOT NULL,
    status TEXT NOT NULL,               -- pending | paid | cancelled
    package_id INTEGER,                 -- riga creata in packages dopo l'incasso
    created_at TEXT NOT NULL,
    paid_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_pay_user ON payments(user_id);
CREATE TABLE IF NOT EXISTS stripe_events (
    id TEXT PRIMARY KEY,
    type TEXT,
    received_at TEXT NOT NULL
);
"""


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
#  Stripe: HTTP puro, nessuna libreria
# --------------------------------------------------------------------------- #
def _urlencode_params(params, prefix=""):
    """Encoder per i parametri form-urlencoded nidificati richiesti da Stripe."""
    items = []
    if isinstance(params, dict):
        for k, v in params.items():
            key = f"{prefix}[{k}]" if prefix else str(k)
            items.extend(_urlencode_params(v, key))
    elif isinstance(params, (list, tuple)):
        for i, v in enumerate(params):
            items.extend(_urlencode_params(v, f"{prefix}[{i}]"))
    else:
        items.append((prefix, str(params)))
    return items


def _check_key(key):
    """La dashboard mostra la chiave abbreviata ("sk_live_…"): chi la copia col mouse
    incolla il troncone. Meglio accorgersene qui e dirlo in italiano."""
    if not key:
        raise ValueError("Stripe non è configurato: manca STRIPE_SECRET_KEY nel file .env")
    if "…" in key or "..." in key:
        raise ValueError("La chiave Stripe è incompleta (contiene i puntini della versione abbreviata): "
                         "copiala per intero con il pulsante di copia della dashboard.")
    if not key.isascii():
        raise ValueError("La chiave Stripe contiene caratteri non validi: ricopiala senza spazi né virgolette.")
    if not key.startswith(("sk_", "rk_")):
        raise ValueError("La chiave Stripe non sembra una chiave segreta: deve iniziare con sk_ oppure rk_.")
    if len(key) < 30:
        raise ValueError("La chiave Stripe è troppo corta per essere completa: ricopiala dalla dashboard.")


def _stripe_api(path, payload=None, method="POST"):
    key = stripe_secret_key()
    _check_key(key)
    data = urllib.parse.urlencode(_urlencode_params(payload)).encode() if payload is not None else None
    req = urllib.request.Request(f"https://api.stripe.com/v1{path}", data=data, method=method)
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise ValueError(f"Stripe ha rifiutato la richiesta ({e.code}): {detail}")
    except OSError as e:
        raise ValueError(f"Stripe non raggiungibile: {e}")


# --------------------------------------------------------------------------- #
#  Flusso
# --------------------------------------------------------------------------- #
def _record(db, user_id, prov, order_id, ptype, info):
    db.execute(
        "INSERT INTO payments(user_id,provider,order_id,package_type,label,amount_cents,vat_rate,status,created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (user_id, prov, order_id, ptype, info["label"], int(round(info["price"] * 100)), VAT_RATE, "pending", now_iso()),
    )
    db.commit()


def start_purchase(db, user, ptype, packages, base_url, app_name):
    """Crea l'ordine e restituisce l'URL a cui mandare il cliente."""
    info = packages.get(ptype)
    if not info:
        raise ValueError("Pacchetto non valido")

    if provider() == "simulated":
        order_id = "sim_" + secrets.token_urlsafe(12)
        _record(db, user["id"], "simulated", order_id, ptype, info)
        return {"ok": True, "simulated": True, "order_id": order_id,
                "url": f"{base_url}/api/payments/simulated?order={order_id}"}

    pub = public_url(base_url)
    if not pub:
        raise ValueError("Manca PUBLIC_URL / BASE_URL: serve per riportare il cliente sull'app dopo il pagamento")
    session = _stripe_api("/checkout/sessions", {
        "mode": "payment",
        "payment_method_types": ["card"],
        "client_reference_id": str(user["id"]),
        "customer_email": user["email"],
        "success_url": f"{pub}/api/payments/return?session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url": f"{pub}/?pagamento=annullato",
        "line_items": [{
            "price_data": {
                "currency": "eur",
                "unit_amount": int(round(info["price"] * 100)),
                "product_data": {"name": f"{app_name} — {info['label']}",
                                 "description": f"Validità {PACKAGE_DAYS} giorni · IVA inclusa"},
            },
            "quantity": 1,
        }],
        "metadata": {"user_id": str(user["id"]), "package_type": ptype},
    })
    order_id, url = session.get("id"), session.get("url")
    if not order_id or not url:
        raise ValueError("Stripe non ha restituito una sessione di checkout valida")
    _record(db, user["id"], "stripe", order_id, ptype, info)
    return {"ok": True, "simulated": False, "order_id": order_id, "url": url}


def grant_once(db, order_id):
    """Attiva il pacchetto pagato. Idempotente: la seconda chiamata non fa nulla."""
    pay = db.execute("SELECT * FROM payments WHERE order_id=?", (order_id,)).fetchone()
    if not pay:
        raise ValueError("Pagamento non trovato")
    if pay["status"] == "paid":
        return {"already": True, "payment": dict(pay)}

    # il pacchetto mensile parte alla scadenza di quello ancora attivo, così chi
    # rinnova in anticipo non perde giorni; scheda/dieta partono subito
    today = date.today()
    start = today
    family = FAMILIES.get(pay["package_type"], [pay["package_type"]])
    if len(family) > 1:
        qs = ",".join("?" * len(family))
        cur = db.execute(
            f"SELECT MAX(end_date) FROM packages WHERE user_id=? AND type IN ({qs}) AND end_date>=?",
            (pay["user_id"], *family, today.isoformat()),
        ).fetchone()[0]
        if cur:
            start = date.fromisoformat(cur) + timedelta(days=1)
    end = start + timedelta(days=PACKAGE_DAYS - 1)
    cur = db.execute(
        "INSERT INTO packages(user_id,type,start_date,end_date,note,created_at) VALUES (?,?,?,?,?,?)",
        (pay["user_id"], pay["package_type"], start.isoformat(), end.isoformat(),
         f"Pagato online ({pay['provider']}) · {order_id}", now_iso()),
    )
    db.execute("UPDATE payments SET status='paid', paid_at=?, package_id=? WHERE id=?",
               (now_iso(), cur.lastrowid, pay["id"]))
    db.commit()
    return {"already": False, "payment": dict(db.execute("SELECT * FROM payments WHERE id=?", (pay["id"],)).fetchone())}


def confirm_stripe_session(db, session_id):
    """Al ritorno del cliente: si chiede a Stripe, non si crede al browser."""
    if not session_id:
        raise ValueError("Sessione mancante")
    session = _stripe_api(f"/checkout/sessions/{session_id}", method="GET")
    if session.get("payment_status") != "paid":
        raise ValueError(f"Pagamento non completato (stato Stripe: {session.get('payment_status')})")
    return grant_once(db, session_id)


def confirm_simulated(db, order_id):
    if provider() != "simulated":
        raise ValueError("Il pagamento simulato è disattivato")
    pay = db.execute("SELECT * FROM payments WHERE order_id=? AND provider='simulated'", (order_id,)).fetchone()
    if not pay:
        raise ValueError("Ordine simulato non trovato")
    return grant_once(db, order_id)


def verify_stripe_webhook(headers, raw_body):
    secret = stripe_webhook_secret()
    sig = headers.get("Stripe-Signature") or headers.get("stripe-signature") or ""
    if not secret or not sig:
        return False
    parts = dict(p.split("=", 1) for p in sig.split(",") if "=" in p)
    ts, v1 = parts.get("t"), parts.get("v1")
    if not ts or not v1:
        return False
    try:
        if abs(time.time() - int(ts)) > 300:
            return False
    except ValueError:
        return False
    expected = hmac.new(secret.encode(), f"{ts}.{raw_body.decode('utf-8', 'ignore')}".encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, v1)


def handle_stripe_event(db, event):
    """Stripe ripete la consegna finché non riceve 200: ogni evento si elabora una volta."""
    eid, etype = event.get("id"), event.get("type")
    if eid:
        if db.execute("SELECT 1 FROM stripe_events WHERE id=?", (eid,)).fetchone():
            return {"duplicate": True}
        db.execute("INSERT INTO stripe_events(id,type,received_at) VALUES (?,?,?)", (eid, etype, now_iso()))
        db.commit()
    if etype == "checkout.session.completed":
        obj = event.get("data", {}).get("object", {})
        if obj.get("payment_status") == "paid" and obj.get("id"):
            if db.execute("SELECT 1 FROM payments WHERE order_id=?", (obj["id"],)).fetchone():
                return grant_once(db, obj["id"])
    return {"ignored": etype}


def reconcile_pending(db, limit=25):
    """Recupero: sessioni Stripe rimaste 'pending' (cliente che ha chiuso il browser, webhook perso)."""
    if not is_stripe_live():
        return 0
    rows = db.execute(
        "SELECT order_id FROM payments WHERE provider='stripe' AND status='pending' ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    done = 0
    for r in rows:
        try:
            s = _stripe_api(f"/checkout/sessions/{r['order_id']}", method="GET")
            if s.get("payment_status") == "paid":
                grant_once(db, r["order_id"])
                done += 1
            elif s.get("status") == "expired":
                db.execute("UPDATE payments SET status='cancelled' WHERE order_id=?", (r["order_id"],))
                db.commit()
        except ValueError:
            continue
    return done


def history(db, user_id=None):
    if user_id is None:
        rows = db.execute(
            "SELECT p.*, u.name AS user_name, u.email AS user_email FROM payments p JOIN users u ON u.id=p.user_id "
            "ORDER BY p.id DESC LIMIT 200").fetchall()
    else:
        rows = db.execute("SELECT * FROM payments WHERE user_id=? ORDER BY id DESC LIMIT 50", (user_id,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["amount"] = d["amount_cents"] / 100
        d.update({k: v for k, v in vat_breakdown(d["amount"], d["vat_rate"]).items() if k != "gross"})
        out.append(d)
    return out
