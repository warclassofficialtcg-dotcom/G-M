# -*- coding: utf-8 -*-
"""
Gestione appuntamenti palestra / massaggi.
Backend Flask + SQLite (nessuna dipendenza oltre a Flask).
"""
import json
import os
import secrets
import sqlite3
from datetime import date, datetime, timedelta
from functools import wraps
from urllib.parse import quote

from flask import Flask, g, jsonify, redirect, render_template, request, session
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

import payments

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# DATA_DIR: cartella persistente sull'hosting (es. /var/data su Render); in locale la cartella del progetto
DATA_DIR = os.environ.get("DATA_DIR") or BASE_DIR
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "gym.db")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")

# ---------------------------------------------------------------------------
# Configurazione
# ---------------------------------------------------------------------------
DEFAULT_CONFIG = {
    "SECRET_KEY": None,                    # generata al primo avvio
    "OWNER_WHATSAPP": "393933379797",      # numero del titolare con prefisso internazionale (39), senza +
    "OWNER_NAME": "Titolare",
    "APP_NAME": "G & M",
    "ADMIN_EMAIL": "admin@palestra.it",
    "ADMIN_PASSWORD": "admin123",          # CAMBIALA!
    "BASE_URL": "http://localhost:5000",   # indirizzo pubblico dell'app (usato nel link di conferma)
    "MAX_GYM_PER_SLOT": 4,                 # persone massime nella stessa ora di palestra
    "PORT": 5000,
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg.update(json.load(f))
    # le variabili d'ambiente (pannello dell'hosting) hanno la precedenza sul file
    for k in DEFAULT_CONFIG:
        if os.environ.get(k):
            cfg[k] = os.environ[k]
    if os.environ.get("PORT"):
        cfg["PORT"] = int(os.environ["PORT"])
    if os.environ.get("MAX_GYM_PER_SLOT"):
        cfg["MAX_GYM_PER_SLOT"] = int(os.environ["MAX_GYM_PER_SLOT"])
    changed = False
    if not cfg.get("SECRET_KEY"):
        cfg["SECRET_KEY"] = secrets.token_hex(32)
        changed = True
    if changed or not os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    return cfg


CONFIG = load_config()
STATIC_VERSION = str(int(os.path.getmtime(os.path.join(BASE_DIR, "static", "app.js"))))  # cache-busting

app = Flask(__name__, static_folder="static", template_folder="templates")
app.secret_key = CONFIG["SECRET_KEY"]
app.json.sort_keys = False  # mantiene l'ordine del listino
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)  # dietro il proxy dell'hosting: https e host corretti
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)

# ---------------------------------------------------------------------------
# Costanti di business
# ---------------------------------------------------------------------------
# family: 'palestra' (limite settimanale) | 'massaggio' (limite mensile) | 'extra'
PACKAGES = {
    "70": {"label": "Abbonamento 70€ – 2 allenamenti a settimana", "price": 70, "per_week": 2, "family": "palestra", "monthly": True},
    "80": {"label": "Abbonamento 80€ – 3 allenamenti a settimana", "price": 80, "per_week": 3, "family": "palestra", "monthly": True},
    "scheda": {"label": "Scheda allenamento – 30€", "price": 30, "per_week": None, "family": "extra"},
    "dieta": {"label": "Dieta – 30€", "price": 30, "per_week": None, "family": "extra"},
    "standard": {"label": "Percorso Standard – 4 massaggi al mese", "price": 150, "per_month": 4, "family": "massaggio"},
    "benessere": {"label": "Percorso Benessere – 6 massaggi al mese", "price": 200, "per_month": 6, "family": "massaggio"},
}

# Listino massaggi (dal volantino "Andrea Marrone – Massaggi")
MASSAGES = {
    "svedese":        {"name": "Massaggio Svedese", "price": 50, "cat": "olistici", "tags": "Rilassante · Rivitalizzante · Tonificante"},
    "viso":           {"name": "Massaggio Viso Lifting", "price": 40, "cat": "olistici", "tags": "Rassodante · Ringiovanente"},
    "anticellulite":  {"name": "Massaggio Anticellulite", "price": 50, "cat": "olistici", "tags": "Drenante · Levigante · Riducente"},
    "antistress":     {"name": "Massaggio Antistress / Relax", "price": 50, "cat": "olistici", "tags": "Rilassante · De-stressante"},
    "ayurvedico":     {"name": "Massaggio Ayurvedico", "price": 50, "cat": "olistici", "tags": "Equilibrante · Purificante · Rinvigorente"},
    "candle":         {"name": "Candle Massage", "price": 50, "cat": "olistici", "tags": "Avvolgente · Nutriente · Caldo"},
    "cervicale":      {"name": "Massaggio Cervicale", "price": 40, "cat": "tecnici", "tags": "Antidolorifico · Rilassante · Lenitivo"},
    "sportivo":       {"name": "Massaggio Sportivo / Taping", "price": 50, "cat": "tecnici", "tags": "Stimolante · Rigenerante · Terapeutico"},
    "decontratturante": {"name": "Massaggio Decontratturante", "price": 50, "cat": "tecnici", "tags": "Intenso · Lenitivo · Rigenerativo"},
    "posturale":      {"name": "Massaggio Posturale", "price": 50, "cat": "tecnici", "tags": "Alleviante · Terapeutico"},
    "linfodrenaggio": {"name": "Linfodrenaggio Vodder", "price": 70, "cat": "tecnici", "tags": "Drenante · Terapeutico · Rilassante"},
}
MASSAGE_CATEGORIES = {
    "olistici": {
        "title": "Massaggi olistici ed estetici",
        "text": "Un'arte del benessere che abbraccia l'individuo nella sua totalità, nel corpo e nella mente. "
                "Attraverso l'uso di oli essenziali favoriscono il rilassamento profondo, stimolando e riequilibrando il benessere.",
        "for": ["Ansia e stress", "Ritenzione idrica", "Cellulite", "Rilassamento cutaneo", "Stanchezza mentale"],
    },
    "tecnici": {
        "title": "Massaggi tecnici e sportivi",
        "text": "Strumenti per liberare il corpo dalle tensioni della vita quotidiana: sciolgono i nodi muscolari, "
                "migliorano la postura e alleviano i dolori cronici.",
        "for": ["Dolore cervico-dorsale", "Dolore lombo-sacrale", "Contratture muscolari", "Posture incongrue", "Lavori usuranti"],
    },
}

# Orari (ultimo orario = ultimo slot prenotabile, durata 1 ora)
GYM_HOURS = {"weekday": [11, 12, 15, 16, 17, 18, 19], "saturday": [11, 12, 13, 14, 15, 16]}   # 13-15 pausa pranzo
MASSAGE_HOURS = {"weekday": list(range(10, 20)), "saturday": [10, 11, 12, 13]}                 # lun-ven 10-19, sab 10-13
DAY_NAMES = ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"]
ACTIVE_STATUSES = ("pending", "confirmed")


def hours_for(d: date, typ: str):
    wd = d.weekday()
    table = GYM_HOURS if typ == "palestra" else MASSAGE_HOURS
    if wd <= 4:
        return table["weekday"]
    if wd == 5:
        return table["saturday"]
    return []


def slots_for(d: date):
    """Tutti gli orari del giorno con i tipi prenotabili in ciascuno."""
    g_h, m_h = set(hours_for(d, "palestra")), set(hours_for(d, "massaggio"))
    return [(h, [t for t, ok in (("palestra", h in g_h), ("massaggio", h in m_h)) if ok]) for h in sorted(g_h | m_h)]


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    phone TEXT,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS packages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    type TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sheets (
    user_id INTEGER PRIMARY KEY REFERENCES users(id),
    workout TEXT DEFAULT '',
    diet TEXT DEFAULT '',
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS appointments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    type TEXT NOT NULL,            -- 'palestra' | 'massaggio'
    date TEXT NOT NULL,            -- YYYY-MM-DD
    hour INTEGER NOT NULL,         -- 11..19
    status TEXT NOT NULL,          -- pending | confirmed | rejected | cancelled
    joined INTEGER NOT NULL DEFAULT 0,
    token TEXT UNIQUE,
    created_at TEXT NOT NULL,
    decided_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_app_date ON appointments(date, hour);
"""
MIGRATIONS = [
    "ALTER TABLE appointments ADD COLUMN massage_type TEXT",
]


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.executescript(SCHEMA)
    db.executescript(payments.SCHEMA)
    for m in MIGRATIONS + payments.MIGRATIONS:
        try:
            db.execute(m)
        except sqlite3.OperationalError:
            pass  # colonna già presente
    payments.PACKAGES_REF = PACKAGES
    # crea l'admin (titolare) se non esiste
    row = db.execute("SELECT id FROM users WHERE email = ?", (CONFIG["ADMIN_EMAIL"].lower(),)).fetchone()
    if not row:
        cur = db.execute(
            "INSERT INTO users(name,email,phone,password_hash,role,created_at) VALUES (?,?,?,?,?,?)",
            (CONFIG["OWNER_NAME"], CONFIG["ADMIN_EMAIL"].lower(), CONFIG["OWNER_WHATSAPP"],
             generate_password_hash(CONFIG["ADMIN_PASSWORD"]), "admin", now_iso()),
        )
        db.execute("INSERT INTO sheets(user_id) VALUES (?)", (cur.lastrowid,))
    db.commit()
    db.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    return get_db().execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()


def login_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        u = current_user()
        if not u:
            return jsonify(error="Devi effettuare l'accesso"), 401
        g.user = u
        return f(*a, **kw)
    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        u = current_user()
        if not u:
            return jsonify(error="Devi effettuare l'accesso"), 401
        if u["role"] != "admin":
            return jsonify(error="Accesso riservato al titolare"), 403
        g.user = u
        return f(*a, **kw)
    return wrapper


def user_public(u, full=False):
    return {
        "id": u["id"],
        "name": u["name"] if full else u["name"].split(" ")[0],
        "email": u["email"] if full else None,
        "phone": u["phone"] if full else None,
        "role": u["role"],
    }


def active_packages(db, user_id, on: date = None):
    on = on or date.today()
    rows = db.execute(
        "SELECT * FROM packages WHERE user_id=? AND start_date<=? AND end_date>=? ORDER BY created_at DESC",
        (user_id, on.isoformat(), on.isoformat()),
    ).fetchall()
    return [dict(r) for r in rows]


def training_limit(db, user_id, on: date):
    """Allenamenti/settimana consentiti dal pacchetto attivo; None se nessun pacchetto mensile."""
    for p in active_packages(db, user_id, on):
        info = PACKAGES.get(p["type"])
        if info and info.get("per_week"):
            return info["per_week"], p
    return None, None


def massage_limit(db, user_id, on: date):
    """Massaggi consentiti dal percorso attivo nel suo periodo di validità; None se nessun abbonamento."""
    for p in active_packages(db, user_id, on):
        info = PACKAGES.get(p["type"])
        if info and info.get("per_month"):
            return info["per_month"], p
    return None, None


def gym_package_status(db, user_id):
    """Abbonamento palestra: attivo/scaduto, scadenza e giorni rimanenti (serve per prenotare)."""
    limit, pkg = training_limit(db, user_id, date.today())
    if pkg:
        end = date.fromisoformat(pkg["end_date"])
        return {"active": True, "type": pkg["type"], "per_week": limit, "start_date": pkg["start_date"],
                "end_date": pkg["end_date"], "days_left": (end - date.today()).days + 1}
    last = db.execute(
        "SELECT * FROM packages WHERE user_id=? AND type IN ('70','80') ORDER BY end_date DESC LIMIT 1", (user_id,)
    ).fetchone()
    return {"active": False, "expired": dict(last) if last else None}


def fmt_date_it(d: date):
    return f"{DAY_NAMES[d.weekday()]} {d.strftime('%d/%m/%Y')}"


def appt_dict(row, viewer=None):
    d = dict(row)
    d["date_label"] = fmt_date_it(date.fromisoformat(d["date"]))
    d["time_label"] = f"{d['hour']:02d}:00"
    m = MASSAGES.get(d.get("massage_type") or "")
    d["massage_name"] = m["name"] if m else None
    if viewer is not None:
        d["mine"] = d["user_id"] == viewer["id"]
    return d


def whatsapp_message(appt, user, moved_from=None):
    d = date.fromisoformat(appt["date"])
    tipo = "PALESTRA" if appt["type"] == "palestra" else "MASSAGGIO"
    m = MASSAGES.get(appt["massage_type"] or "") if appt["type"] == "massaggio" else None
    if m:
        tipo = f"MASSAGGIO – {m['name']} ({m['price']}€)"
    link = f"{base_url()}/conferma/{appt['token']}"
    if moved_from:
        od = date.fromisoformat(moved_from["date"])
        return (f"Ciao! Sono {user['name']}.\n"
                f"Ho spostato il mio appuntamento *{tipo}*\n"
                f"❌ da {fmt_date_it(od)} alle {moved_from['hour']:02d}:00\n"
                f"✅ a {fmt_date_it(d)} alle {appt['hour']:02d}:00\n"
                + (f"Conferma o rifiuta qui: {link}" if appt["status"] == "pending" else f"Dettagli: {link}"))
    if appt["joined"]:
        return (f"Ciao! Sono {user['name']}.\n"
                f"Mi unisco alla lezione di *{tipo}* di {fmt_date_it(d)} alle {appt['hour']:02d}:00.\n"
                f"Dettagli: {link}")
    return (f"Ciao! Sono {user['name']}.\n"
            f"Richiesta appuntamento *{tipo}*\n"
            f"📅 {fmt_date_it(d)} alle {appt['hour']:02d}:00\n"
            f"Conferma o rifiuta qui: {link}")


def base_url():
    """BASE_URL dal config; se non impostato (o localhost) usa l'indirizzo con cui è stata chiamata l'app."""
    cfg = (CONFIG.get("BASE_URL") or "").rstrip("/")
    if not cfg or "localhost" in cfg or "127.0.0.1" in cfg:
        return request.url_root.rstrip("/")
    return cfg


def whatsapp_url(msg):
    return f"https://wa.me/{CONFIG['OWNER_WHATSAPP']}?text={quote(msg)}"


# ---------------------------------------------------------------------------
# Pagine
# ---------------------------------------------------------------------------
@app.context_processor
def inject_globals():
    return {"APP_NAME": CONFIG.get("APP_NAME", "G & M"), "STATIC_V": STATIC_VERSION}


@app.route("/")
def index():
    return render_template("index.html")


@app.after_request
def sw_scope(resp):
    # permette al service worker in /static/ di controllare tutto il sito
    if request.path == "/static/sw.js":
        resp.headers["Service-Worker-Allowed"] = "/"
        resp.headers["Cache-Control"] = "no-cache"
    return resp


def _load_appt_by_token(db, token):
    return db.execute(
        "SELECT a.*, u.name AS user_name, u.phone AS user_phone, u.email AS user_email "
        "FROM appointments a JOIN users u ON u.id=a.user_id WHERE token=?", (token,)
    ).fetchone()


@app.route("/conferma/<token>", methods=["GET", "POST"])
def conferma(token):
    """Pagina aperta dal titolare dal link ricevuto su WhatsApp."""
    db = get_db()
    appt = _load_appt_by_token(db, token)
    if not appt:
        return render_template("conferma.html", appt=None, others=[], message="Link non valido."), 404

    message = None
    if request.method == "POST":
        action = request.form.get("action")
        if appt["status"] not in ACTIVE_STATUSES:
            message = "Questo appuntamento è già stato chiuso."
        elif action == "confirm":
            db.execute("UPDATE appointments SET status='confirmed', decided_at=? WHERE id=?", (now_iso(), appt["id"]))
            db.commit()
            message = "✅ Appuntamento confermato e inserito nel calendario generale."
        elif action == "reject":
            db.execute("UPDATE appointments SET status='rejected', decided_at=? WHERE id=?", (now_iso(), appt["id"]))
            db.commit()
            message = "❌ Appuntamento rifiutato."
        appt = _load_appt_by_token(db, token)

    others = db.execute(
        "SELECT u.name FROM appointments a JOIN users u ON u.id=a.user_id "
        "WHERE a.date=? AND a.hour=? AND a.id<>? AND a.status IN ('pending','confirmed')",
        (appt["date"], appt["hour"], appt["id"]),
    ).fetchall()
    return render_template("conferma.html", appt=appt_dict(appt), others=[o["name"] for o in others], message=message)


# ---------------------------------------------------------------------------
# API: autenticazione
# ---------------------------------------------------------------------------
@app.post("/api/register")
def api_register():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    phone = (data.get("phone") or "").strip()
    password = data.get("password") or ""
    if len(name) < 2:
        return jsonify(error="Inserisci il tuo nome"), 400
    if "@" not in email or "." not in email:
        return jsonify(error="Email non valida"), 400
    if len(password) < 6:
        return jsonify(error="La password deve avere almeno 6 caratteri"), 400
    db = get_db()
    try:
        cur = db.execute(
            "INSERT INTO users(name,email,phone,password_hash,role,created_at) VALUES (?,?,?,?,?,?)",
            (name, email, phone, generate_password_hash(password), "user", now_iso()),
        )
        db.execute("INSERT INTO sheets(user_id) VALUES (?)", (cur.lastrowid,))
        db.commit()
    except sqlite3.IntegrityError:
        return jsonify(error="Esiste già un account con questa email"), 409
    session.permanent = True
    session["user_id"] = cur.lastrowid
    return jsonify(ok=True)


@app.post("/api/login")
def api_login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    u = get_db().execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    if not u or not check_password_hash(u["password_hash"], password):
        return jsonify(error="Email o password errati"), 401
    session.permanent = True
    session["user_id"] = u["id"]
    return jsonify(ok=True)


@app.post("/api/logout")
def api_logout():
    session.clear()
    return jsonify(ok=True)


@app.get("/api/me")
@login_required
def api_me():
    db = get_db()
    u = g.user
    sheets = db.execute("SELECT * FROM sheets WHERE user_id=?", (u["id"],)).fetchone()
    pkgs = active_packages(db, u["id"])
    for p in pkgs:
        p["info"] = PACKAGES.get(p["type"])
    history = db.execute(
        "SELECT * FROM packages WHERE user_id=? ORDER BY start_date DESC LIMIT 12", (u["id"],)
    ).fetchall()
    appts = db.execute(
        "SELECT * FROM appointments WHERE user_id=? AND date>=? AND status IN ('pending','confirmed') "
        "ORDER BY date, hour", (u["id"], date.today().isoformat())
    ).fetchall()
    return jsonify(
        user=user_public(u, full=True),
        sheets={"workout": sheets["workout"] if sheets else "", "diet": sheets["diet"] if sheets else "",
                "updated_at": sheets["updated_at"] if sheets else None},
        packages=pkgs,
        package_history=[{**dict(h), "info": PACKAGES.get(h["type"])} for h in history],
        appointments=[appt_dict(a, u) for a in appts],
        catalog=PACKAGES,
        massages=MASSAGES,
        massage_categories=MASSAGE_CATEGORIES,
        hours={"palestra": GYM_HOURS, "massaggio": MASSAGE_HOURS},
        owner_whatsapp=CONFIG["OWNER_WHATSAPP"],
        max_gym=CONFIG["MAX_GYM_PER_SLOT"],
        app_name=CONFIG.get("APP_NAME", "G & M"),
        payments=payments.status(),
        payment_history=payments.history(db, u["id"]),
        subscription=payments.active_subscription(db, u["id"]),
        gym_package=gym_package_status(db, u["id"]),
    )


# ---------------------------------------------------------------------------
# API: calendario e prenotazioni
# ---------------------------------------------------------------------------
@app.get("/api/calendar")
@login_required
def api_calendar():
    """Calendario settimanale generale, visibile a tutti gli utenti loggati."""
    db = get_db()
    start_param = request.args.get("week")
    try:
        ref = date.fromisoformat(start_param) if start_param else date.today()
    except ValueError:
        ref = date.today()
    monday = ref - timedelta(days=ref.weekday())
    sunday = monday + timedelta(days=6)
    rows = db.execute(
        "SELECT a.*, u.name AS user_name FROM appointments a JOIN users u ON u.id=a.user_id "
        "WHERE a.date BETWEEN ? AND ? AND a.status IN ('pending','confirmed') ORDER BY a.date, a.hour",
        (monday.isoformat(), sunday.isoformat()),
    ).fetchall()
    by_slot = {}
    for r in rows:
        by_slot.setdefault((r["date"], r["hour"]), []).append(r)

    now = datetime.now()
    is_admin = g.user["role"] == "admin"
    days = []
    for i in range(7):
        d = monday + timedelta(days=i)
        slots = []
        for h, types in slots_for(d):
            appts = by_slot.get((d.isoformat(), h), [])
            slots.append({
                "hour": h,
                "types": types,
                "label": f"{h:02d}:00",
                "past": datetime(d.year, d.month, d.day, h) <= now,
                "appointments": [{
                    "id": a["id"],
                    "type": a["type"],
                    "massage_name": (MASSAGES.get(a["massage_type"] or "") or {}).get("name"),
                    "status": a["status"],
                    "name": a["user_name"] if is_admin else a["user_name"].split(" ")[0],
                    "mine": a["user_id"] == g.user["id"],
                } for a in appts],
            })
        days.append({
            "date": d.isoformat(),
            "weekday": DAY_NAMES[d.weekday()],
            "day": d.day,
            "month": d.strftime("%m"),
            "closed": not slots,
            "is_today": d == date.today(),
            "slots": slots,
        })
    return jsonify(
        week_start=monday.isoformat(),
        week_end=sunday.isoformat(),
        prev_week=(monday - timedelta(days=7)).isoformat(),
        next_week=(monday + timedelta(days=7)).isoformat(),
        days=days,
        max_gym=CONFIG["MAX_GYM_PER_SLOT"],
    )


def check_slot(db, u, typ, d, hour, massage_type=None, exclude_id=None):
    """Regole di prenotazione. Ritorna (errore, codice_http, extra) oppure (None, esistenti, None)."""
    if hour not in hours_for(d, typ):
        return "Orario non prenotabile per questo tipo di appuntamento", 400, None
    if typ == "massaggio" and massage_type not in MASSAGES:
        return "Scegli il tipo di massaggio", 400, None
    if datetime(d.year, d.month, d.day, hour) <= datetime.now():
        return "Non puoi prenotare un orario già passato", 400, None

    existing = [e for e in db.execute(
        "SELECT * FROM appointments WHERE date=? AND hour=? AND status IN ('pending','confirmed')",
        (d.isoformat(), hour)).fetchall() if e["id"] != exclude_id]
    if any(e["user_id"] == u["id"] for e in existing):
        return "Hai già un appuntamento in questo orario", 409, None

    def count_mine(t, start, end):
        return db.execute(
            "SELECT COUNT(*) FROM appointments WHERE user_id=? AND type=? AND status IN ('pending','confirmed') "
            "AND date BETWEEN ? AND ? AND id<>?", (u["id"], t, start, end, exclude_id or 0)).fetchone()[0]

    if typ == "massaggio":
        if existing:
            return "Orario non disponibile: c'è già un appuntamento. Scegli un altro orario.", 409, None
        limit, pkg = massage_limit(db, u["id"], d)
        if limit and count_mine("massaggio", pkg["start_date"], pkg["end_date"]) >= limit:
            return f"Hai già usato i {limit} massaggi del tuo percorso in questo periodo.", 409, None
    else:
        if any(e["type"] == "massaggio" for e in existing):
            return "In questo orario c'è un massaggio: non è possibile allenarsi. Scegli un altro orario.", 409, None
        if len(existing) >= CONFIG["MAX_GYM_PER_SLOT"]:
            return "Lezione al completo. Scegli un altro orario.", 409, None
        # per allenarsi serve l'abbonamento mensile valido nel giorno scelto
        limit, pkg = training_limit(db, u["id"], d)
        if not limit:
            return ("Per prenotare gli allenamenti serve un abbonamento mensile attivo "
                    f"(valido anche il {fmt_date_it(d)}). Vai nella sezione Allenamento per attivarlo."), 402, {"code": "no_package"}
        monday = d - timedelta(days=d.weekday())
        if count_mine("palestra", monday.isoformat(), (monday + timedelta(days=6)).isoformat()) >= limit:
            return f"Hai raggiunto il limite del tuo abbonamento: {limit} allenamenti a settimana.", 409, None
    return None, existing, None


def _parse_booking(data):
    typ = data.get("type")
    if typ not in ("palestra", "massaggio"):
        raise ValueError("Tipo di appuntamento non valido")
    try:
        d = date.fromisoformat(data.get("date", ""))
        hour = int(data.get("hour"))
    except (ValueError, TypeError):
        raise ValueError("Data o orario non validi")
    return typ, d, hour


def _booking_response(db, u, appt, joined, moved_from=None):
    msg = whatsapp_message(appt, u, moved_from)
    d = date.fromisoformat(appt["date"])
    return jsonify(
        ok=True,
        appointment=appt_dict(appt, u),
        joined=joined,
        whatsapp_url=whatsapp_url(msg),
        whatsapp_text=msg,
        no_package=(appt["type"] == "massaggio" and massage_limit(db, u["id"], d)[0] is None),
    )


@app.post("/api/appointments")
@login_required
def api_book():
    db = get_db()
    u = g.user
    data = request.get_json(silent=True) or {}
    try:
        typ, d, hour = _parse_booking(data)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    massage_type = data.get("massage_type") if typ == "massaggio" else None
    err, existing, extra = check_slot(db, u, typ, d, hour, massage_type)
    if err:
        return jsonify(error=err, **(extra or {})), existing

    # se c'è già una lezione di palestra confermata, ci si unisce direttamente
    joined = typ == "palestra" and any(e["type"] == "palestra" and e["status"] == "confirmed" for e in existing)
    status = "confirmed" if joined else "pending"
    cur = db.execute(
        "INSERT INTO appointments(user_id,type,massage_type,date,hour,status,joined,token,created_at,decided_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (u["id"], typ, massage_type, d.isoformat(), hour, status, int(joined), secrets.token_urlsafe(24), now_iso(),
         now_iso() if joined else None),
    )
    db.commit()
    appt = db.execute("SELECT * FROM appointments WHERE id=?", (cur.lastrowid,)).fetchone()
    return _booking_response(db, u, appt, joined)


@app.post("/api/appointments/<int:aid>/move")
@login_required
def api_move(aid):
    """Sposta un proprio appuntamento a un altro giorno/ora: torna 'in attesa' e va riconfermato dal titolare."""
    db = get_db()
    u = g.user
    old = db.execute("SELECT * FROM appointments WHERE id=? AND user_id=?", (aid, u["id"])).fetchone()
    if not old or old["status"] not in ACTIVE_STATUSES:
        return jsonify(error="Appuntamento non trovato o già chiuso"), 404
    data = request.get_json(silent=True) or {}
    try:
        d = date.fromisoformat(data.get("date", ""))
        hour = int(data.get("hour"))
    except (ValueError, TypeError):
        return jsonify(error="Data o orario non validi"), 400
    massage_type = data.get("massage_type") or old["massage_type"]
    if d.isoformat() == old["date"] and hour == old["hour"] and massage_type == old["massage_type"]:
        return jsonify(error="È già questo l'orario del tuo appuntamento"), 400
    err, existing, extra = check_slot(db, u, old["type"], d, hour, massage_type, exclude_id=aid)
    if err:
        return jsonify(error=err, **(extra or {})), existing
    joined = old["type"] == "palestra" and any(e["type"] == "palestra" and e["status"] == "confirmed" for e in existing)
    status = "confirmed" if joined else "pending"
    db.execute(
        "UPDATE appointments SET date=?, hour=?, massage_type=?, status=?, joined=?, token=?, decided_at=?, "
        "created_at=? WHERE id=?",
        (d.isoformat(), hour, massage_type if old["type"] == "massaggio" else None, status, int(joined),
         secrets.token_urlsafe(24), now_iso() if joined else None, now_iso(), aid),
    )
    db.commit()
    appt = db.execute("SELECT * FROM appointments WHERE id=?", (aid,)).fetchone()
    return _booking_response(db, u, appt, joined, moved_from=dict(old))


@app.get("/api/appointments/<int:aid>/whatsapp")
@login_required
def api_whatsapp_link(aid):
    db = get_db()
    appt = db.execute("SELECT * FROM appointments WHERE id=? AND user_id=?", (aid, g.user["id"])).fetchone()
    if not appt:
        return jsonify(error="Appuntamento non trovato"), 404
    msg = whatsapp_message(appt, g.user)
    return jsonify(whatsapp_url=whatsapp_url(msg), whatsapp_text=msg)


@app.delete("/api/appointments/<int:aid>")
@login_required
def api_cancel(aid):
    db = get_db()
    appt = db.execute("SELECT * FROM appointments WHERE id=?", (aid,)).fetchone()
    if not appt:
        return jsonify(error="Appuntamento non trovato"), 404
    if appt["user_id"] != g.user["id"] and g.user["role"] != "admin":
        return jsonify(error="Non autorizzato"), 403
    if appt["status"] not in ACTIVE_STATUSES:
        return jsonify(error="Appuntamento già chiuso"), 400
    db.execute("UPDATE appointments SET status='cancelled', decided_at=? WHERE id=?", (now_iso(), aid))
    db.commit()
    return jsonify(ok=True)


# ---------------------------------------------------------------------------
# API: pagamenti (Stripe Checkout, vedi payments.py)
# ---------------------------------------------------------------------------
@app.get("/api/payments/status")
@login_required
def api_payments_status():
    return jsonify(payments.status())


@app.post("/api/payments/start")
@login_required
def api_payments_start():
    data = request.get_json(silent=True) or {}
    try:
        r = payments.start_purchase(get_db(), g.user, data.get("type"), PACKAGES,
                                    base_url(), CONFIG.get("APP_NAME", "G & M"),
                                    recurring=bool(data.get("recurring")))
    except ValueError as e:
        return jsonify(error=str(e)), 400
    return jsonify(r)


@app.post("/api/payments/subscription/cancel")
@login_required
def api_subscription_cancel():
    try:
        return jsonify(payments.cancel_subscription(get_db(), g.user["id"]))
    except ValueError as e:
        return jsonify(error=str(e)), 400


@app.get("/api/payments/return")
def api_payments_return():
    """success_url di Stripe: si verifica la sessione e si attiva il pacchetto."""
    try:
        payments.confirm_stripe_session(get_db(), request.args.get("session_id"))
        return redirect("/?pagamento=ok")
    except ValueError as e:
        return redirect("/?pagamento=errore&msg=" + quote(str(e)))


@app.get("/api/payments/simulated")
def api_payments_simulated():
    """Solo in sviluppo (PAYMENTS_PROVIDER=simulated): finge l'incasso e attiva il pacchetto."""
    try:
        payments.confirm_simulated(get_db(), request.args.get("order", ""))
        return redirect("/?pagamento=ok")
    except ValueError as e:
        return redirect("/?pagamento=errore&msg=" + quote(str(e)))


@app.post("/api/payments/stripe-webhook")
def api_stripe_webhook():
    raw = request.get_data() or b""
    if not payments.verify_stripe_webhook(request.headers, raw):
        return jsonify(error="Firma webhook non valida"), 400
    try:
        event = json.loads(raw.decode("utf-8") or "{}")
    except ValueError:
        return jsonify(error="JSON non valido"), 400
    try:
        payments.handle_stripe_event(get_db(), event)
    except ValueError as e:
        return jsonify(error=str(e)), 500
    return jsonify(ok=True)


# ---------------------------------------------------------------------------
# API: amministrazione (titolare)
# ---------------------------------------------------------------------------
@app.get("/api/admin/payments")
@admin_required
def admin_payments():
    db = get_db()
    recovered = payments.reconcile_pending(db)
    return jsonify(payments=payments.history(db), recovered=recovered, status=payments.status())


@app.get("/api/admin/users")
@admin_required
def admin_users():
    db = get_db()
    users = db.execute("SELECT * FROM users ORDER BY name").fetchall()
    out = []
    for u in users:
        pk = active_packages(db, u["id"])
        out.append({**user_public(u, full=True), "created_at": u["created_at"],
                    "packages": [{**p, "info": PACKAGES.get(p["type"])} for p in pk]})
    return jsonify(users=out)


@app.get("/api/admin/users/<int:uid>")
@admin_required
def admin_user_detail(uid):
    db = get_db()
    u = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not u:
        return jsonify(error="Utente non trovato"), 404
    sheets = db.execute("SELECT * FROM sheets WHERE user_id=?", (uid,)).fetchone()
    pkgs = db.execute("SELECT * FROM packages WHERE user_id=? ORDER BY start_date DESC", (uid,)).fetchall()
    appts = db.execute(
        "SELECT * FROM appointments WHERE user_id=? ORDER BY date DESC, hour DESC LIMIT 50", (uid,)
    ).fetchall()
    return jsonify(
        user={**user_public(u, full=True), "created_at": u["created_at"]},
        sheets={"workout": sheets["workout"] if sheets else "", "diet": sheets["diet"] if sheets else ""},
        packages=[{**dict(p), "info": PACKAGES.get(p["type"])} for p in pkgs],
        appointments=[appt_dict(a) for a in appts],
    )


@app.post("/api/admin/users/<int:uid>/packages")
@admin_required
def admin_add_package(uid):
    db = get_db()
    data = request.get_json(silent=True) or {}
    typ = data.get("type")
    if typ not in PACKAGES:
        return jsonify(error="Pacchetto non valido"), 400
    try:
        start = date.fromisoformat(data.get("start_date"))
        end = date.fromisoformat(data.get("end_date")) if data.get("end_date") else start + timedelta(days=30)
    except (ValueError, TypeError):
        return jsonify(error="Date non valide"), 400
    if end < start:
        return jsonify(error="La data di fine precede quella di inizio"), 400
    db.execute(
        "INSERT INTO packages(user_id,type,start_date,end_date,note,created_at) VALUES (?,?,?,?,?,?)",
        (uid, typ, start.isoformat(), end.isoformat(), (data.get("note") or "").strip(), now_iso()),
    )
    db.commit()
    return jsonify(ok=True)


@app.delete("/api/admin/packages/<int:pid>")
@admin_required
def admin_delete_package(pid):
    db = get_db()
    db.execute("DELETE FROM packages WHERE id=?", (pid,))
    db.commit()
    return jsonify(ok=True)


@app.put("/api/admin/users/<int:uid>/sheets")
@admin_required
def admin_sheets(uid):
    db = get_db()
    data = request.get_json(silent=True) or {}
    db.execute(
        "INSERT INTO sheets(user_id,workout,diet,updated_at) VALUES (?,?,?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET workout=excluded.workout, diet=excluded.diet, updated_at=excluded.updated_at",
        (uid, data.get("workout") or "", data.get("diet") or "", now_iso()),
    )
    db.commit()
    return jsonify(ok=True)


@app.get("/api/admin/appointments")
@admin_required
def admin_appointments():
    db = get_db()
    status = request.args.get("status", "pending")
    base = ("SELECT a.*, u.name AS user_name, u.phone AS user_phone FROM appointments a "
            "JOIN users u ON u.id=a.user_id ")
    if status == "upcoming":
        rows = db.execute(base + "WHERE a.status='confirmed' AND a.date>=? ORDER BY a.date, a.hour",
                          (date.today().isoformat(),)).fetchall()
    else:
        rows = db.execute(base + "WHERE a.status=? ORDER BY a.date, a.hour", (status,)).fetchall()
    return jsonify(appointments=[appt_dict(r) for r in rows])


@app.post("/api/admin/appointments/<int:aid>/status")
@admin_required
def admin_set_status(aid):
    db = get_db()
    status = (request.get_json(silent=True) or {}).get("status")
    if status not in ("confirmed", "rejected", "cancelled"):
        return jsonify(error="Stato non valido"), 400
    db.execute("UPDATE appointments SET status=?, decided_at=? WHERE id=?", (status, now_iso(), aid))
    db.commit()
    return jsonify(ok=True)


# ---------------------------------------------------------------------------
init_db()

if __name__ == "__main__":
    print(f"\n  App avviata su http://localhost:{CONFIG['PORT']}")
    print(f"  Login titolare: {CONFIG['ADMIN_EMAIL']}  (vedi config.json)\n")
    app.run(host="0.0.0.0", port=int(CONFIG["PORT"]), debug=False)
