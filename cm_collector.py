#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cm-collector — raccoglitore OTLP per la telemetria nativa di Claude Code.

Claude Code sa gia' esportare da solo 16 metriche (costo, token, tempo attivo,
sessioni, subagent, strumenti, commit, PR, righe modificate...) via OpenTelemetry.
Questo modulo ne raccoglie il flusso e lo conserva, senza che sulle macchine
debba girare niente di scritto da noi.

Riceve OTLP su HTTP in codifica JSON (protocollo "http/json"), quindi non serve
protobuf e non serve alcuna libreria esterna: solo stdlib, come claude_monitor.py.

    POST /v1/metrics    datapoint delle metriche
    POST /v1/logs       eventi (accettati e scartati se non richiesti)
    GET  /              cruscotto
    GET  /api/summary   riepilogo in JSON
    GET  /healthz       stato del servizio

Uso rapido:
    python cm_collector.py                       # avvia su 127.0.0.1:4318
    python cm_collector.py --port 4318 --db team.db
    python cm_collector.py --report              # riepilogo da terminale
    python cm_collector.py --report --by user    # per persona
    python cm_collector.py --setup               # stampa la configurazione da applicare

Livello di dettaglio sulle persone: --privacy {aggregato,pseudonimo,nominativo}.
La telemetria nativa manda l'indirizzo di posta in chiaro comunque, quindi il
livello si impone qui, prima della scrittura in archivio. Default: pseudonimo.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import sqlite3
import sys
import threading
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

__version__ = "0.1.0"

SCHEMA_VERSION = 1

try:  # console Windows: assicura UTF-8 in output
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover
    pass

# --------------------------------------------------------------------------- #
# Metriche che ci interessano
# --------------------------------------------------------------------------- #

# Nome metrica -> (etichetta breve, unita' di misura per la formattazione)
KNOWN_METRICS = {
    "claude_code.cost.usage":        ("costo",        "usd"),
    "claude_code.token.usage":       ("token",        "int"),
    "claude_code.active_time.total": ("tempo attivo", "sec"),
    "claude_code.session.count":     ("sessioni",     "int"),
    "claude_code.lines_of_code.count": ("righe",      "int"),
    "claude_code.commit.count":      ("commit",       "int"),
    "claude_code.pull_request.count": ("PR",          "int"),
    "claude_code.subagent.spawn":    ("subagent",     "int"),
    "claude_code.tool.execution":    ("strumenti",    "int"),
    "claude_code.mcp.rpc":           ("MCP",          "int"),
    "claude_code.compaction":        ("compattazioni", "int"),
}

# Attributi promossi a colonna, perche' sono gli assi su cui si raggruppa.
# I nomi possibili variano fra versioni: si prende il primo presente.
USER_KEYS    = ("user.email", "user.id", "user.account_uuid", "user.account_id")
SESSION_KEYS = ("session.id",)
ORG_KEYS     = ("organization.id",)
MODEL_KEYS   = ("model",)
TYPE_KEYS    = ("type", "token.type", "decision", "tool", "tool_name", "name")

# --------------------------------------------------------------------------- #
# OTLP/JSON: decodifica
# --------------------------------------------------------------------------- #


def any_value(v):
    """Converte un AnyValue OTLP nel corrispondente valore Python."""
    if not isinstance(v, dict):
        return v
    if "stringValue" in v:
        return v["stringValue"]
    if "intValue" in v:
        try:
            return int(v["intValue"])
        except (TypeError, ValueError):
            return v["intValue"]
    if "doubleValue" in v:
        return v["doubleValue"]
    if "boolValue" in v:
        return bool(v["boolValue"])
    if "arrayValue" in v:
        return [any_value(x) for x in v["arrayValue"].get("values", [])]
    if "kvlistValue" in v:
        return flatten_attrs(v["kvlistValue"].get("values", []))
    if "bytesValue" in v:
        return v["bytesValue"]
    return None


def flatten_attrs(attrs) -> dict:
    """[{key, value}] -> {key: valore}."""
    out = {}
    for a in attrs or []:
        k = a.get("key")
        if k is not None:
            out[k] = any_value(a.get("value"))
    return out


def first_of(d: dict, keys) -> str | None:
    """Primo valore presente fra piu' nomi possibili di attributo."""
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return str(v)
    return None


def point_value(dp: dict) -> float | None:
    """Valore numerico di un NumberDataPoint (asInt arriva come stringa)."""
    if "asDouble" in dp and dp["asDouble"] is not None:
        try:
            return float(dp["asDouble"])
        except (TypeError, ValueError):
            return None
    if "asInt" in dp and dp["asInt"] is not None:
        try:
            return float(int(dp["asInt"]))
        except (TypeError, ValueError):
            return None
    return None


def nano_to_epoch(value) -> float:
    """timeUnixNano (stringa) -> epoch in secondi. 0 se assente."""
    try:
        return int(value) / 1e9
    except (TypeError, ValueError):
        return 0.0


def iter_points(payload: dict):
    """Attraversa un ExportMetricsServiceRequest e produce un dict per datapoint.

    Gestisce sum, gauge e histogram. Degli histogram si tiene la somma, che e'
    l'unica grandezza che ci serve (durate e dimensioni aggregate).
    """
    for rm in payload.get("resourceMetrics", []) or []:
        res = flatten_attrs((rm.get("resource") or {}).get("attributes", []))
        for sm in rm.get("scopeMetrics", []) or []:
            for m in sm.get("metrics", []) or []:
                name = m.get("name")
                if not name:
                    continue
                for kind in ("sum", "gauge", "histogram", "exponentialHistogram"):
                    body = m.get(kind)
                    if not body:
                        continue
                    temporality = body.get("aggregationTemporality")
                    for dp in body.get("dataPoints", []) or []:
                        if kind in ("histogram", "exponentialHistogram"):
                            val = dp.get("sum")
                            val = float(val) if val is not None else None
                        else:
                            val = point_value(dp)
                        if val is None:
                            continue
                        attrs = flatten_attrs(dp.get("attributes", []))
                        merged = dict(res)
                        merged.update(attrs)
                        yield {
                            "ts": nano_to_epoch(dp.get("timeUnixNano")) or time.time(),
                            "metric": name,
                            "value": val,
                            "kind": "histogram" if "istogram" in kind.lower() else kind,
                            "temporality": temporality,
                            "user_key": first_of(merged, USER_KEYS),
                            "session_id": first_of(merged, SESSION_KEYS),
                            "org_id": first_of(merged, ORG_KEYS),
                            "model": first_of(merged, MODEL_KEYS),
                            "type_attr": first_of(merged, TYPE_KEYS),
                            "attrs": merged,
                        }


# --------------------------------------------------------------------------- #
# Riservatezza
# --------------------------------------------------------------------------- #
#
# La telemetria nativa manda "user.email" in chiaro, sempre: non e' configurabile
# sulla postazione. Il livello di dettaglio va quindi imposto QUI, nel punto in
# cui il dato entra nell'archivio, prima di essere scritto. E' l'unico punto in
# cui la scelta e' effettivamente esigibile e verificabile.
#
#   aggregato    nessun identificativo di persona          (L0)
#   pseudonimo   codice stabile a chiave, non reversibile   (L1)
#   nominativo   indirizzo di posta in chiaro               (L2)

PRIVACY_LEVELS = ("aggregato", "pseudonimo", "nominativo")

# Attributi che identificano la persona: rimossi o sostituiti sotto L2.
IDENTITY_ATTRS = ("user.email", "user.id", "user.account_uuid", "user.account_id")


def load_or_make_key(path: str) -> bytes:
    """Chiave di pseudonimizzazione. Custodita a parte dall'archivio.

    Chi ha l'archivio non puo' risalire alle persone; chi ha anche la chiave
    puo' ricalcolare i codici a partire da un indirizzo noto. La separazione
    fra i due e' l'intero senso del livello intermedio: va tenuta.
    """
    if os.path.exists(path):
        with open(path, "rb") as fh:
            return fh.read().strip()
    key = os.urandom(32).hex().encode("ascii")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(key)
    try:
        os.chmod(path, 0o600)
    except OSError:  # pragma: no cover - Windows senza POSIX ACL
        pass
    return key


def make_privacy(level: str, keyfile: str):
    """Restituisce fn(point) -> point che applica il livello scelto."""
    if level not in PRIVACY_LEVELS:
        raise ValueError(f"livello sconosciuto: {level}")

    if level == "nominativo":
        return lambda p: p

    if level == "aggregato":
        def scrub(p):
            p["user_key"] = None
            p["attrs"] = {k: v for k, v in p["attrs"].items()
                          if k not in IDENTITY_ATTRS}
            return p
        return scrub

    import hmac
    import hashlib
    key = load_or_make_key(keyfile)
    cache: dict[str, str] = {}

    def pseudo(p):
        ident = p.get("user_key")
        if ident:
            code = cache.get(ident)
            if code is None:
                digest = hmac.new(key, ident.encode("utf-8"), hashlib.sha256)
                code = "p-" + digest.hexdigest()[:12]
                cache[ident] = code
            p["user_key"] = code
        else:
            code = None
        attrs = {k: v for k, v in p["attrs"].items() if k not in IDENTITY_ATTRS}
        if code:
            attrs["user.pseudonym"] = code
        p["attrs"] = attrs
        return p

    return pseudo


# --------------------------------------------------------------------------- #
# Archivio
# --------------------------------------------------------------------------- #

DDL = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS points (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL NOT NULL,
    received    REAL NOT NULL,
    metric      TEXT NOT NULL,
    value       REAL NOT NULL,
    kind        TEXT NOT NULL,
    temporality INTEGER,
    user_key    TEXT,
    session_id  TEXT,
    org_id      TEXT,
    model       TEXT,
    type_attr   TEXT,
    series      TEXT NOT NULL,
    attrs       TEXT NOT NULL
);

-- Gli esportatori OTLP ritentano l'invio quando il raccoglitore non risponde:
-- senza questo vincolo lo stesso datapoint verrebbe contato due volte. E' lo
-- stesso errore di conteggio delle righe dei transcript, in un altro punto.
CREATE UNIQUE INDEX IF NOT EXISTS points_dedup
    ON points(metric, ts, series);

CREATE INDEX IF NOT EXISTS points_metric_ts ON points(metric, ts);
CREATE INDEX IF NOT EXISTS points_user      ON points(user_key);
CREATE INDEX IF NOT EXISTS points_session   ON points(session_id);
"""


def open_db(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path, check_same_thread=False, timeout=10)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.executescript(DDL)
    con.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES('schema', ?)",
        (str(SCHEMA_VERSION),),
    )
    con.commit()
    return con


def series_key(p: dict) -> str:
    """Identita' della serie temporale: metrica + attributi, senza il tempo.

    Serve sia per la deduplica sia per trattare correttamente le metriche
    cumulative, dove i valori non vanno sommati ma presi al massimo per serie.
    """
    return json.dumps(p["attrs"], sort_keys=True, ensure_ascii=False)


class Store:
    def __init__(self, path: str, privacy=None, level: str = "nominativo"):
        self.path = path
        self.con = open_db(path)
        self.lock = threading.Lock()
        self.written = 0
        self.duplicates = 0
        # Applicata prima della scrittura: l'archivio non vede mai il dato grezzo.
        self.privacy = privacy or (lambda p: p)
        if privacy is not None:  # in sola lettura non si riscrive il livello
            self.con.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES('privacy', ?)",
                (level,))
            self.con.commit()
        row = self.con.execute(
            "SELECT value FROM meta WHERE key='privacy'").fetchone()
        self.level = row[0] if row else level

    def add(self, points) -> tuple[int, int]:
        now = time.time()
        rows = []
        for p in points:
            p = self.privacy(p)
            rows.append((
                p["ts"], now, p["metric"], p["value"], p["kind"], p["temporality"],
                p["user_key"], p["session_id"], p["org_id"], p["model"],
                p["type_attr"], series_key(p),
                json.dumps(p["attrs"], sort_keys=True, ensure_ascii=False),
            ))
        if not rows:
            return (0, 0)
        with self.lock:
            before = self.con.total_changes
            self.con.executemany(
                "INSERT OR IGNORE INTO points"
                " (ts, received, metric, value, kind, temporality, user_key,"
                "  session_id, org_id, model, type_attr, series, attrs)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                rows,
            )
            self.con.commit()
            new = self.con.total_changes - before
        dup = len(rows) - new
        self.written += new
        self.duplicates += dup
        return (new, dup)

    def query(self, sql: str, args=()) -> list[sqlite3.Row]:
        with self.lock:
            cur = self.con.execute(sql, args)
            cur.row_factory = sqlite3.Row
            return cur.fetchall()


# --------------------------------------------------------------------------- #
# Aggregazione
# --------------------------------------------------------------------------- #
#
# OpenTelemetry ha due temporalita'. Con "delta" (1) ogni datapoint porta
# l'incremento dall'ultimo invio e i valori si sommano. Con "cumulative" (2)
# ogni datapoint porta il totale dall'avvio del processo: sommarli gonfia il
# conto, esattamente come sommare le righe di streaming di un transcript.
# Per le cumulative si prende il massimo per serie e poi si somma fra serie.

AGG_SQL = """
WITH filtrati AS (
    SELECT * FROM points
    WHERE metric = ? {extra}
),
parziali AS (
    -- delta: gli incrementi si sommano
    SELECT {group_col} AS gruppo, SUM(value) AS v
    FROM filtrati
    WHERE temporality = 1
    GROUP BY 1
    UNION ALL
    -- cumulative: per ogni serie il picco, poi si somma fra serie
    SELECT gruppo, SUM(picco) AS v FROM (
        SELECT {group_col} AS gruppo, series, MAX(value) AS picco
        FROM filtrati
        WHERE temporality != 1 OR temporality IS NULL
        GROUP BY 1, 2
    ) GROUP BY 1
)
SELECT gruppo, SUM(v) AS totale
FROM parziali
GROUP BY gruppo
ORDER BY totale DESC
"""

GROUP_COLS = {
    "user":    "COALESCE(user_key, '(non identificato)')",
    "session": "COALESCE(session_id, '(ignota)')",
    "model":   "COALESCE(model, '(ignoto)')",
    "type":    "COALESCE(type_attr, '(nessuno)')",
    "all":     "'totale'",
}


def aggregate(store: Store, metric: str, by: str = "all", since: float | None = None):
    """Totale di una metrica, raggruppato, con la temporalita' gestita giusta."""
    col = GROUP_COLS.get(by, GROUP_COLS["all"])
    extra, args = "", [metric]
    if since:
        extra = " AND ts >= ?"
        args.append(since)
    sql = AGG_SQL.format(group_col=col, extra=extra)
    return [(r["gruppo"], r["totale"]) for r in store.query(sql, tuple(args))]


def totals(store: Store, by: str = "all", since: float | None = None) -> dict:
    """Riepilogo di tutte le metriche note, nella forma {gruppo: {metrica: val}}."""
    out: dict[str, dict] = {}
    for metric in KNOWN_METRICS:
        for gruppo, val in aggregate(store, metric, by, since):
            out.setdefault(gruppo, {})[metric] = val
    return out


SEP = "\x1f"  # separatore fra chiave e sottochiave, non stampabile


def aggregate_pair(store: Store, metric: str, by: str, sub: str,
                   since: float | None = None) -> dict[str, dict[str, float]]:
    """Totale incrociato su due assi, es. postazione x tipo di token."""
    col = f"{GROUP_COLS[by]} || '{SEP}' || {GROUP_COLS[sub]}"
    extra, args = "", [metric]
    if since:
        extra, args = " AND ts >= ?", [metric, since]
    sql = AGG_SQL.format(group_col=col, extra=extra)
    out: dict[str, dict[str, float]] = {}
    for r in store.query(sql, tuple(args)):
        chiave, _, sottochiave = str(r["gruppo"]).partition(SEP)
        out.setdefault(chiave, {})[sottochiave] = r["totale"]
    return out


# Nomi dei tipi di token nella telemetria nativa -> campi del monitor.
# Sono gli stessi valori che il parser dei transcript ricava da usage.
TOKEN_TYPES = {
    "input": "input",
    "output": "output",
    "cacheRead": "cache_read",
    "cacheCreation": "cache_creation",
}


def team_rows(store: Store, since: float | None = None) -> list[dict]:
    """Una riga per postazione, gia' nella forma che il pannello si aspetta.

    Vive qui e non nella GUI perche' sia verificabile da riga di comando:
        python cm_collector.py --report --by user
    """
    tot = totals(store, "user", since)
    per_tipo = aggregate_pair(store, "claude_code.token.usage", "user", "type", since)
    per_modello = aggregate_pair(store, "claude_code.cost.usage", "user", "model", since)

    ultimi = {}
    sql = ("SELECT COALESCE(user_key,'(non identificato)') AS g, MAX(ts) AS ultimo"
           " FROM points" + (" WHERE ts >= ?" if since else "") + " GROUP BY g")
    for r in store.query(sql, (since,) if since else ()):
        ultimi[r["g"]] = r["ultimo"]

    righe = []
    for persona, vals in tot.items():
        tok = {v: 0.0 for v in TOKEN_TYPES.values()}
        for grezzo, val in (per_tipo.get(persona) or {}).items():
            campo = TOKEN_TYPES.get(grezzo)
            if campo:
                tok[campo] += val
        modelli = sorted(
            (m for m in (per_modello.get(persona) or {}) if m != "(ignoto)"),
            key=lambda m: per_modello[persona][m], reverse=True)
        righe.append({
            "person":   persona,
            "cost":     vals.get("claude_code.cost.usage", 0.0),
            "sessions": int(vals.get("claude_code.session.count", 0)),
            "active":   vals.get("claude_code.active_time.total", 0.0),
            "tokens":   tok,
            "total_tokens": sum(tok.values()),
            "models":   modelli,
            "last":     ultimi.get(persona, 0.0),
        })
    righe.sort(key=lambda r: r["cost"], reverse=True)
    return righe


def observed_months(store: Store, since: float | None = None) -> tuple[int, float, float]:
    """Mesi di calendario toccati dai dati, e gli estremi della finestra."""
    sql = ("SELECT MIN(ts) AS a, MAX(ts) AS b FROM points"
           + (" WHERE ts >= ?" if since else ""))
    r = store.query(sql, (since,) if since else ())[0]
    a, b = r["a"], r["b"]
    if not a or not b:
        return (0, 0.0, 0.0)
    da = time.localtime(a)
    db_ = time.localtime(b)
    mesi = (db_.tm_year - da.tm_year) * 12 + (db_.tm_mon - da.tm_mon) + 1
    return (max(1, mesi), a, b)


def team_costs(righe: list[dict], team: dict, mesi: int,
               usd_per_unit: float | None = None) -> tuple[list[dict], dict]:
    """Quanto e' costata ogni postazione, e il riepilogo per la direzione.

    Con l'abbonamento Team ogni postazione costa uguale, che venga usata o no.
    La domanda utile non e' quindi "quanto ha speso Tizio" — la risposta e'
    sempre la stessa cifra — ma quanto ha reso la postazione rispetto a quello
    che si paga comunque.

    Le postazioni dormienti sono l'altro numero, e sono invisibili alla
    telemetria: chi non usa lo strumento non manda nulla, quindi non compare.
    Vanno dedotte dal numero di postazioni pagate, che va dichiarato.
    """
    seats = int(team.get("seats") or 0)
    fee = float(team.get("fee_per_seat") or 0.0)
    rate = usd_per_unit if usd_per_unit else (
        1.0 if (team.get("currency") or "USD").upper() == "USD" else None)

    attive = sum(1 for r in righe if r["cost"] > 0)
    dormienti = max(0, seats - attive) if seats else 0
    per_postazione = fee * mesi

    for r in righe:
        r["paid"] = per_postazione
        # Quante volte una postazione ha reso quello che costa. Senza cambio
        # noto fra le due valute il rapporto non e' calcolabile: meglio niente
        # che un numero che confronta unita' diverse.
        r["ratio"] = (r["cost"] / (per_postazione * rate)) if (
            per_postazione and rate) else 0.0

    api_totale = sum(r["cost"] for r in righe)
    pagato_totale = per_postazione * seats if seats else 0.0
    riepilogo = {
        "seats": seats,
        "attive": attive,
        "dormienti": dormienti,
        "mesi": mesi,
        "fee_per_seat": fee,
        "currency": team.get("currency") or "USD",
        "per_postazione": per_postazione,
        "pagato_totale": pagato_totale,
        "pagato_a_vuoto": per_postazione * dormienti,
        "api_totale": api_totale,
        "ratio": (api_totale / (pagato_totale * rate)) if (
            pagato_totale and rate) else 0.0,
    }
    return righe, riepilogo


# --------------------------------------------------------------------------- #
# Formattazione
# --------------------------------------------------------------------------- #


def h_val(metric: str, value: float) -> str:
    _, unit = KNOWN_METRICS.get(metric, ("", "int"))
    if value is None:
        return "-"
    if unit == "usd":
        return f"${value:,.2f}"
    if unit == "sec":
        s = int(value)
        if s >= 3600:
            return f"{s // 3600}h {(s % 3600) // 60:02d}m"
        if s >= 60:
            return f"{s // 60}m {s % 60:02d}s"
        return f"{s}s"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}k"
    return f"{value:,.0f}".replace(",", ".")


# --------------------------------------------------------------------------- #
# Servizio HTTP
# --------------------------------------------------------------------------- #


def read_body(handler: BaseHTTPRequestHandler) -> bytes:
    """Corpo della richiesta, decompresso se necessario."""
    length = int(handler.headers.get("Content-Length") or 0)
    raw = handler.rfile.read(length) if length else b""
    enc = (handler.headers.get("Content-Encoding") or "").lower()
    if "gzip" in enc:
        raw = gzip.decompress(raw)
    elif "deflate" in enc:
        raw = zlib.decompress(raw)
    return raw


class Handler(BaseHTTPRequestHandler):
    server_version = f"cm-collector/{__version__}"
    store: Store = None       # iniettati da serve()
    verbose: bool = False

    def log_message(self, fmt, *args):  # silenzia il log per riga di default
        if self.verbose:
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # -- risposte ---------------------------------------------------------- #

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json")

    # -- rotte ------------------------------------------------------------- #

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0].rstrip("/")
        try:
            raw = read_body(self)
        except Exception as exc:
            self._json(400, {"error": f"corpo illeggibile: {exc}"})
            return

        if path == "/v1/metrics":
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except Exception as exc:
                self._json(400, {"error": f"JSON non valido: {exc}"})
                return
            try:
                new, dup = self.store.add(iter_points(payload))
            except Exception as exc:
                # Mai rispondere 5xx per un errore nostro di parsing: l'esportatore
                # ritenterebbe all'infinito lo stesso payload.
                sys.stderr.write(f"[cm-collector] errore in scrittura: {exc}\n")
                self._json(200, {"partialSuccess": {}})
                return
            if self.verbose:
                print(f"  + {new} datapoint" + (f" ({dup} duplicati)" if dup else ""))
            self._json(200, {"partialSuccess": {}})
            return

        if path in ("/v1/logs", "/v1/traces"):
            # Accettati e scartati: gli eventi non servono al pannello di consumo
            # e sono la categoria che puo' contenere testo. Rispondere 200 evita
            # che l'esportatore accumuli code di ritentativi.
            self._json(200, {"partialSuccess": {}})
            return

        self._json(404, {"error": "rotta sconosciuta"})

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0].rstrip("/") or "/"

        if path == "/healthz":
            self._json(200, {
                "stato": "attivo",
                "versione": __version__,
                "datapoint_scritti": self.store.written,
                "duplicati_scartati": self.store.duplicates,
                "archivio": os.path.abspath(self.store.path),
            })
            return

        if path == "/api/summary":
            self._json(200, {
                "totale": totals(self.store, "all"),
                "per_utente": totals(self.store, "user"),
                "per_modello": totals(self.store, "model"),
                "aggiornato": time.time(),
            })
            return

        if path == "/api/team":
            # Consumato dal pannello: righe gia' pronte, una per postazione.
            self._json(200, {
                "riservatezza": self.store.level,
                "postazioni": team_rows(self.store),
                "aggiornato": time.time(),
            })
            return

        if path == "/":
            self._send(200, dashboard_html(self.store).encode("utf-8"),
                       "text/html; charset=utf-8")
            return

        self._json(404, {"error": "rotta sconosciuta"})


# --------------------------------------------------------------------------- #
# Cruscotto
# --------------------------------------------------------------------------- #

CSS = """
:root{--paper:#f5f7f8;--surface:#fff;--ink:#141a1f;--ink2:#3d4952;--ink3:#6b7780;
--rule:#d6dde1;--data:#0f6e7e;--money:#a8620a}
@media(prefers-color-scheme:dark){:root{--paper:#0e1317;--surface:#161c21;
--ink:#e8edef;--ink2:#b4c0c7;--ink3:#849098;--rule:#2b353c;--data:#4fb6c7;--money:#e0a34a}}
*{box-sizing:border-box}
body{margin:0;padding:32px 24px 64px;background:var(--paper);color:var(--ink);
font:15px/1.55 "Segoe UI",system-ui,sans-serif}
.wrap{max-width:1000px;margin:0 auto}
h1{font-size:24px;letter-spacing:-.02em;margin:0 0 4px}
.sub{color:var(--ink3);font-size:14px;margin:0 0 28px}
h2{font-size:11.5px;text-transform:uppercase;letter-spacing:.11em;color:var(--ink3);
margin:32px 0 10px;padding-bottom:8px;border-bottom:1px solid var(--rule)}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px}
.tile{background:var(--surface);border:1px solid var(--rule);border-radius:6px;padding:16px 18px}
.tile .k{font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:var(--ink3)}
.tile .v{font-size:26px;font-weight:650;letter-spacing:-.02em;margin-top:4px;
font-variant-numeric:tabular-nums}
.tile.money .v{color:var(--money)}
.tile.data .v{color:var(--data)}
table{width:100%;border-collapse:collapse;background:var(--surface);
border:1px solid var(--rule);border-radius:6px;overflow:hidden;font-size:14px}
th,td{padding:11px 14px;text-align:left;border-bottom:1px solid var(--rule)}
th{font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:var(--ink3)}
td.n{text-align:right;font-variant-numeric:tabular-nums}
tr:last-child td{border-bottom:none}
.empty{background:var(--surface);border:1px solid var(--rule);border-radius:6px;
padding:28px;color:var(--ink2)}
code{font-family:Consolas,monospace;font-size:13px;background:var(--paper);
border:1px solid var(--rule);border-radius:3px;padding:1px 5px}
footer{margin-top:40px;color:var(--ink3);font-size:13px}
"""

TILE_METRICS = [
    ("claude_code.cost.usage", "Valore a listino API", "money"),
    ("claude_code.token.usage", "Token", "data"),
    ("claude_code.active_time.total", "Tempo attivo", "data"),
    ("claude_code.session.count", "Sessioni", ""),
]


def dashboard_html(store: Store) -> str:
    tot = totals(store, "all").get("totale", {})
    per_user = totals(store, "user")
    per_model = totals(store, "model")

    if not tot:
        body = (
            '<div class="empty"><p><b>Nessun dato ancora.</b></p>'
            "<p>Il raccoglitore &egrave; attivo e in ascolto, ma Claude Code non ha "
            "ancora esportato niente. Verifica che la telemetria sia configurata "
            "(<code>python cm_collector.py --setup</code>) e ricorda che va "
            "<b>riavviata la sessione</b> di Claude Code perch&eacute; le variabili "
            "d&rsquo;ambiente vengano lette.</p>"
            "<p>Il primo invio arriva entro l&rsquo;intervallo di esportazione "
            "configurato.</p></div>"
        )
    else:
        tiles = "".join(
            f'<div class="tile {cls}"><div class="k">{label}</div>'
            f'<div class="v">{h_val(m, tot.get(m, 0))}</div></div>'
            for m, label, cls in TILE_METRICS
        )
        body = f'<div class="tiles">{tiles}</div>'

        def tabella(titolo, dati, colonne):
            if not dati:
                return ""
            head = "".join(f"<th>{KNOWN_METRICS[c][0]}</th>" for c in colonne)
            righe = ""
            for nome, vals in sorted(
                dati.items(),
                key=lambda kv: kv[1].get("claude_code.cost.usage", 0),
                reverse=True,
            ):
                celle = "".join(
                    f'<td class="n">{h_val(c, vals[c])}</td>' if c in vals
                    else '<td class="n">&mdash;</td>'
                    for c in colonne
                )
                righe += f"<tr><td>{nome}</td>{celle}</tr>"
            return (f"<h2>{titolo}</h2><table><tr><th>&nbsp;</th>{head}</tr>"
                    f"{righe}</table>")

        cols = ["claude_code.cost.usage", "claude_code.token.usage",
                "claude_code.active_time.total", "claude_code.session.count"]
        body += tabella("Per postazione", per_user, cols)
        body += tabella("Per modello", per_model, cols[:2])

    return f"""<!doctype html>
<html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>cm-collector</title><style>{CSS}</style>
<meta http-equiv="refresh" content="30"></head>
<body><div class="wrap">
<h1>Telemetria Claude Code</h1>
<p class="sub">Fase 1 &middot; raccoglitore locale &middot; riservatezza
<b>{store.level}</b> &middot; {store.written} datapoint conservati,
{store.duplicates} duplicati scartati</p>
{body}
<footer>Valore a listino API: quanto costerebbe lo stesso consumo pagato a
consumo. Non &egrave; la spesa reale, che dipende dall&rsquo;abbonamento.
La pagina si aggiorna da sola ogni 30 secondi.</footer>
</div></body></html>"""


# --------------------------------------------------------------------------- #
# Configurazione da applicare a Claude Code
# --------------------------------------------------------------------------- #


def setup_env(endpoint: str) -> dict:
    """Variabili d'ambiente che attivano l'esportazione verso questo raccoglitore."""
    return {
        # Interruttore generale.
        "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
        # Solo metriche: gli eventi (logs) possono contenere testo e non servono.
        "OTEL_METRICS_EXPORTER": "otlp",
        "OTEL_LOGS_EXPORTER": "none",
        "OTEL_TRACES_EXPORTER": "none",
        # JSON su HTTP: nessuna dipendenza protobuf lato raccoglitore.
        "OTEL_EXPORTER_OTLP_PROTOCOL": "http/json",
        "OTEL_EXPORTER_OTLP_ENDPOINT": endpoint,
        # Delta invece di cumulative: i valori diventano sommabili senza
        # ricostruire i picchi per serie.
        "OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE": "delta",
        # Un invio al minuto: abbastanza reattivo, poco rumore.
        "OTEL_METRIC_EXPORT_INTERVAL": "60000",
        # Il testo delle richieste non esce mai dalla postazione. E' gia' il
        # valore predefinito, ma va scritto: e' la riga che si mostra in sede
        # di verifica, ed e' l'unica cosa che qui si puo' davvero decidere.
        # Il livello di dettaglio sulle persone NON si sceglie qui: la
        # telemetria manda "user.email" comunque. Lo impone il raccoglitore,
        # con --privacy, prima di scrivere in archivio.
        "OTEL_LOG_USER_PROMPTS": "0",
        # Utile in fase 1 per legare i datapoint alle sessioni del monitor.
        "OTEL_METRICS_INCLUDE_SESSION_ID": "true",
        "OTEL_METRICS_INCLUDE_VERSION": "true",
    }


def print_service(host: str, port: int, db: str, privacy: str) -> None:
    """Come far sopravvivere il raccoglitore alla sessione che l'ha avviato.

    Avviato a mano da un terminale, il raccoglitore muore con quel terminale —
    e i dati persi in quell'intervallo non si recuperano, perche' l'esportatore
    ritenta per poco e poi lascia perdere. Va installato come servizio.
    """
    qui = os.path.dirname(os.path.abspath(__file__))
    script = os.path.join(qui, os.path.basename(__file__))
    args = (f'"{script}" --db "{db}" --port {port} --host {host} '
            f'--privacy {privacy}')

    print("Far partire il raccoglitore da solo, e tenerlo su")
    print("=" * 64)
    print()
    if os.name == "nt":
        pyw = sys.executable.replace("python.exe", "pythonw.exe")
        print("Windows — attivita' pianificata all'accesso (nessuna finestra):")
        print()
        print(f'  schtasks /Create /TN "cm-collector" /SC ONLOGON /RL LIMITED \\')
        print(f'      /TR "\'{pyw}\' {args}"')
        print()
        print("  avvio immediato senza aspettare il prossimo accesso:")
        print('      schtasks /Run /TN "cm-collector"')
        print("  per rimuoverla:")
        print('      schtasks /Delete /TN "cm-collector" /F')
    else:
        print("Linux — unita' utente systemd in ~/.config/systemd/user/cm-collector.service:")
        print()
        print("  [Unit]")
        print("  Description=Raccoglitore telemetria Claude Code")
        print("  [Service]")
        print(f"  ExecStart={sys.executable} {args}")
        print("  Restart=always")
        print("  [Install]")
        print("  WantedBy=default.target")
        print()
        print("  systemctl --user enable --now cm-collector")
    print()
    print("Per il team il raccoglitore sta su una macchina sola, in sede, e le")
    print("postazioni gli mandano i dati: li' va installato come servizio di")
    print("sistema, non dell'utente, e --host va aperto oltre 127.0.0.1.")


def print_setup(endpoint: str) -> None:
    env = setup_env(endpoint)
    print("Configurazione da applicare a Claude Code")
    print("=" * 60)
    print()
    print("In ~/.claude/settings.json, blocco \"env\":")
    print()
    print(json.dumps({"env": env}, indent=2, ensure_ascii=False))
    print()
    print("Poi RIAVVIA Claude Code: le variabili si leggono all'avvio.")
    print()
    print("Per il team, le stesse chiavi vanno nel file di configurazione")
    print("centralizzato non modificabile dall'utente (managed-settings.json),")
    print("cosi' la configurazione non dipende dalla buona volonta' di ciascuno.")


# --------------------------------------------------------------------------- #
# Riepilogo da terminale
# --------------------------------------------------------------------------- #


def print_report(store: Store, by: str, since: float | None) -> None:
    dati = totals(store, by, since)
    if not dati:
        print("Nessun datapoint raccolto finora.")
        print()
        print("Se il raccoglitore e' appena partito e' normale: Claude Code")
        print("esporta a intervalli, e va riavviato dopo aver messo le variabili.")
        print("Verifica la configurazione con:  python cm_collector.py --setup")
        return

    etichetta = {"all": "Totale", "user": "Postazione", "model": "Modello",
                 "session": "Sessione", "type": "Tipo"}.get(by, by)
    metriche = [m for m in KNOWN_METRICS
                if any(m in v for v in dati.values())]

    larg = max(len(etichetta), max((len(str(k)) for k in dati), default=0))
    head = f"{etichetta:<{larg}}  " + "  ".join(
        f"{KNOWN_METRICS[m][0]:>13}" for m in metriche)
    print(head)
    print("-" * len(head))
    for nome, vals in sorted(
        dati.items(),
        key=lambda kv: kv[1].get("claude_code.cost.usage", 0),
        reverse=True,
    ):
        riga = f"{str(nome):<{larg}}  " + "  ".join(
            f"{h_val(m, vals[m]) if m in vals else '-':>13}" for m in metriche)
        print(riga)
    print()
    n = store.query("SELECT COUNT(*) AS n FROM points")[0]["n"]
    print(f"{n} datapoint in archivio ({store.level}) — "
          f"{os.path.abspath(store.path)}")


# --------------------------------------------------------------------------- #
# Avvio
# --------------------------------------------------------------------------- #


def serve(store: Store, host: str, port: int, verbose: bool) -> None:
    Handler.store = store
    Handler.verbose = verbose
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    nota = {
        "aggregato":  "nessun identificativo di persona conservato",
        "pseudonimo": "codice a chiave: l'indirizzo di posta non entra in archivio",
        "nominativo": "ATTENZIONE: indirizzi di posta conservati in chiaro",
    }[store.level]
    print(f"cm-collector {__version__} in ascolto su http://{host}:{port}")
    print(f"  archivio     {os.path.abspath(store.path)}")
    print(f"  riservatezza {store.level} — {nota}")
    print(f"  cruscotto    http://{host}:{port}/")
    print(f"  ingresso     http://{host}:{port}/v1/metrics")
    print()
    print("Ctrl+C per fermare.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nfermato.")
    finally:
        httpd.server_close()


def parse_since(value: str | None) -> float | None:
    if not value:
        return None
    unita = {"m": 60, "h": 3600, "d": 86400, "w": 604800}
    try:
        if value[-1] in unita:
            return time.time() - int(value[:-1]) * unita[value[-1]]
        return time.time() - int(value) * 86400
    except (ValueError, IndexError):
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Raccoglitore OTLP per la telemetria di Claude Code.")
    ap.add_argument("--host", default="127.0.0.1",
                    help="indirizzo di ascolto (default: solo locale)")
    ap.add_argument("--port", type=int, default=4318,
                    help="porta di ascolto (default: 4318, standard OTLP/HTTP)")
    ap.add_argument("--db", default="cm-team.db",
                    help="percorso dell'archivio SQLite")
    ap.add_argument("--report", action="store_true",
                    help="stampa il riepilogo ed esce")
    ap.add_argument("--by", default="all",
                    choices=sorted(GROUP_COLS), help="raggruppamento del riepilogo")
    ap.add_argument("--since", help="finestra temporale, es. 7d, 24h, 30m")
    ap.add_argument("--setup", action="store_true",
                    help="stampa la configurazione da applicare a Claude Code")
    ap.add_argument("--setup-service", action="store_true",
                    help="stampa come installare il raccoglitore come servizio")
    ap.add_argument("--privacy", default="pseudonimo", choices=PRIVACY_LEVELS,
                    help="livello di dettaglio sulle persone (default: pseudonimo)")
    ap.add_argument("--key", default="cm-pseudonimi.key",
                    help="file con la chiave di pseudonimizzazione, da custodire a parte")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="registra ogni invio ricevuto")
    args = ap.parse_args(argv)

    if args.setup:
        print_setup(f"http://{args.host}:{args.port}")
        return 0

    if args.setup_service:
        print_service(args.host, args.port, args.db, args.privacy)
        return 0

    privacy = make_privacy(args.privacy, args.key)
    store = Store(args.db, privacy, args.privacy)

    if args.report:
        print_report(store, args.by, parse_since(args.since))
        return 0

    serve(store, args.host, args.port, args.verbose)
    return 0


if __name__ == "__main__":
    sys.exit(main())
