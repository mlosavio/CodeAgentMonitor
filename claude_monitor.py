#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
claude-monitor — tempo, costo e messaggi delle conversazioni Claude Code.

Legge i transcript JSONL scritti da Claude Code in
    ~/.claude/projects/<project-encoded>/<session-uuid>.jsonl
    ~/.claude/projects/<project-encoded>/<session-uuid>/subagents/agent-*.jsonl

e calcola per ogni sessione: durata (wall-clock e "attiva"), numero di messaggi,
token per tipo (input / output / cache write 5m+1h / cache read) e costo stimato
riga per riga, usando il modello di quella riga e il listino di pricing.json.

Solo stdlib. Vedi README.md per i dettagli.

Uso rapido:
    python claude_monitor.py                     # ultime sessioni
    python claude_monitor.py --since 7d --top 30
    python claude_monitor.py --project MioProgetto
    python claude_monitor.py --by-project
    python claude_monitor.py --session a1b2c3d4  # dettaglio turno per turno
    python claude_monitor.py --watch             # live sulla sessione attiva
    python claude_monitor.py --json              # output machine-readable
"""

from __future__ import annotations

import argparse
import bisect
import datetime as dt
import glob
import json
import os
import re
import sys
import time

import cm_archivio
import cm_copilot
import cm_statistiche as cm_stat

__version__ = "1.0.0"

CACHE_FORMAT = 9  # bump per invalidare la cache su disco quando cambia lo schema

# --------------------------------------------------------------------------- #
# Console
# --------------------------------------------------------------------------- #

try:  # console Windows: assicura UTF-8 in output
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover
    pass


def _unicode_ok() -> bool:
    try:
        "─│▏█».".encode(sys.stdout.encoding or "utf-8")
        return True
    except Exception:
        return False


UNI = _unicode_ok()
HR = "─" if UNI else "-"
ARROW = "»" if UNI else ">"
BULLET = "·" if UNI else "."


class C:
    """Codici ANSI (disattivabili con --no-color / NO_COLOR)."""

    enabled = False
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"

    @classmethod
    def w(cls, text: str, *codes: str) -> str:
        if not cls.enabled or not codes:
            return text
        return "".join(codes) + text + cls.RESET


def init_color(no_color: bool) -> None:
    if no_color or os.environ.get("NO_COLOR"):
        C.enabled = False
        return
    if not sys.stdout.isatty():
        C.enabled = False
        return
    if os.name == "nt":  # abilita le sequenze VT su console Windows
        try:
            import ctypes

            k = ctypes.windll.kernel32
            h = k.GetStdHandle(-11)
            mode = ctypes.c_uint32()
            if k.GetConsoleMode(h, ctypes.byref(mode)):
                k.SetConsoleMode(h, mode.value | 0x0004)
        except Exception:
            pass
    C.enabled = True


# --------------------------------------------------------------------------- #
# Pricing
# --------------------------------------------------------------------------- #

DEFAULT_PRICING = {
    "updated": "built-in",
    "currency": "USD",
    "plan": "subscription",
    "cache_multipliers": {"read": 0.10, "write_5m": 1.25, "write_1h": 2.00},
    "server_tools": {"web_search_request": 0.01, "web_fetch_request": 0.0},
    "models": {
        "claude-fable-5": {"in": 10.00, "out": 50.00},
        "claude-opus-5": {"in": 5.00, "out": 25.00},
        "claude-opus-4-8": {"in": 5.00, "out": 25.00},
        "claude-opus-4-7": {"in": 5.00, "out": 25.00},
        "claude-opus-4-6": {"in": 5.00, "out": 25.00},
        "claude-sonnet-5": {"in": 3.00, "out": 15.00},
        "claude-sonnet-4-6": {"in": 3.00, "out": 15.00},
        "claude-haiku-4-5": {"in": 1.00, "out": 5.00},
    },
    "aliases": {},
    "free_models": ["<synthetic>"],
    "billing": {"mode": "subscription", "api_projects": [], "api_sessions": []},
    "subscription": None,
    "fx": {"usd_per_unit": None},
    "defaults": {"idle_gap": 300, "top": 20, "locale": "us", "theme": "auto",
                 "live_interval": 2.0, "watch_interval": 3.0,
                 "auto_refresh_minutes": 5},
    "statusline": {"enabled": True, "show_cost": True, "show_active_time": True,
                   "show_messages": True, "show_limits": True, "separator": " · ",
                   "limit_warn_pct": 75, "limit_critical_pct": 90,
                   "budget_ms": 150, "max_bytes_per_render": 8388608,
                   "gsd_timeout_ms": 1500},
}

_DATE_SUFFIX = re.compile(r"-\d{8}$")


def config_candidates() -> list[str]:
    here = os.path.dirname(os.path.abspath(__file__))
    return [os.path.join(here, "config.json"), os.path.join(here, "pricing.json")]


def load_config(path: str | None) -> dict:
    """Carica la configurazione: prezzi, abbonamento, modalità di fatturazione, default.

    Cerca `config.json` e, per compatibilità, `pricing.json` accanto allo script.
    Qualunque problema ⇒ default integrati, senza fermarsi.
    """
    paths = [path] if path else config_candidates()
    for p in paths:
        if not p or not os.path.isfile(p):
            continue
        try:
            with open(p, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as exc:
            warn(f"{os.path.basename(p)} illeggibile ({exc}); uso i valori integrati.")
            continue
        for key, default in DEFAULT_PRICING.items():
            data.setdefault(key, default)
        data["_path"] = p
        return data
    warn("nessun config.json trovato; uso i valori integrati.")
    return dict(DEFAULT_PRICING)


# Nome storico, mantenuto perché il resto del codice (e la GUI) lo usa già.
load_pricing = load_config


def defaults_of(config: dict) -> dict:
    d = dict(DEFAULT_PRICING.get("defaults") or {})
    d.update(config.get("defaults") or {})
    return d


def normalize_model(model: str, pricing: dict) -> str:
    """Riporta l'id del modello alla chiave usata nel listino."""
    if not model:
        return ""
    m = model.strip()
    alias = pricing.get("aliases") or {}
    if m in alias:
        return alias[m]
    m = m.replace("[1m]", "")
    models = pricing.get("models") or {}
    if m in models:
        return m
    stripped = _DATE_SUFFIX.sub("", m)
    if stripped in alias:
        return alias[stripped]
    return stripped


def cost_of(model: str, tok: dict, pricing: dict) -> tuple[float, bool]:
    """Costo in USD di un blocco di token. Ritorna (costo, modello_sconosciuto)."""
    if model in (pricing.get("free_models") or []):
        return 0.0, False
    price = (pricing.get("models") or {}).get(model)
    st = pricing.get("server_tools") or {}
    web = (
        tok.get("web_search", 0) * st.get("web_search_request", 0.0)
        + tok.get("web_fetch", 0) * st.get("web_fetch_request", 0.0)
    )
    if not price:
        return web, True
    cm = pricing.get("cache_multipliers") or {}
    pin = price["in"] / 1e6
    pout = price["out"] / 1e6
    total = (
        tok.get("input", 0) * pin
        + tok.get("output", 0) * pout
        + tok.get("cache_read", 0) * pin * cm.get("read", 0.10)
        + tok.get("cache_w5m", 0) * pin * cm.get("write_5m", 1.25)
        + tok.get("cache_w1h", 0) * pin * cm.get("write_1h", 2.00)
        + web
    )
    return total, False


TOKEN_FIELDS = ("input", "output", "cache_read", "cache_w5m", "cache_w1h", "web_search", "web_fetch")

# Finestra di contesto standard dei modelli Claude. Serve a riconoscere le
# richieste che NON possono esserci passate dentro.
FINESTRA_STANDARD = 200_000


def contesto_di(tok: dict) -> int:
    """Quanti token sono entrati nel modello per questa richiesta.

    Tutto quello che ha attraversato la finestra: il prompt nuovo, quello
    riletto dalla cache e quello appena scritto in cache. L'output non c'entra,
    perche' esce.
    """
    return (tok.get("input", 0) + tok.get("cache_read", 0)
            + tok.get("cache_w5m", 0) + tok.get("cache_w1h", 0))


def finestra_standard(pricing: dict) -> int:
    return int((pricing or {}).get("finestra_standard") or FINESTRA_STANDARD)


def sovrapprezzo_contesto(sess: dict, pricing: dict) -> dict:
    """Cosa costerebbe in piu' il lavoro fatto oltre la finestra standard.

    Il transcript registra l'id del modello **senza** il suffisso della finestra
    estesa (`claude-opus-5`, non `claude-opus-5[1m]`), quindi a posteriori i due
    non si distinguono per nome. Si distinguono pero' per i numeri: una richiesta
    che ha fatto entrare piu' token di quanti la finestra standard ne contenga
    non puo' esserci passata dentro, e su quella non c'e' niente da dedurre.

    Il maggiorato NON viene sommato ai costi: si mostra a parte. Il rapporto e'
    configurazione (`long_context`) perche' i listini cambiano, e un numero
    inventato in una colonna di costi diventa vero appena qualcuno lo legge.
    """
    dati = sess.get("oltre_finestra") or {}
    n = dati.get("richieste") or 0
    if not n:
        return {"richieste": 0, "token": 0, "extra": 0.0, "dichiarato": False}
    conf = (pricing or {}).get("long_context") or {}
    m_in = float(conf.get("in") or 0) or None
    m_out = float(conf.get("out") or 0) or None
    token = extra = 0.0
    for model, tok in (dati.get("models") or {}).items():
        token += contesto_di(tok) + tok.get("output", 0)
        if not (m_in and m_out):
            continue
        # I due rincari sono voci diverse del listino: quello che entra e
        # quello che esce. Le chiamate agli strumenti non c'entrano — si pagano
        # a richiesta, non a token — e restano fuori da entrambi i conti.
        senza_web = {k: v for k, v in tok.items()
                     if k not in ("web_search", "web_fetch")}
        entra = dict(senza_web, output=0)
        esce = {k: (v if k == "output" else 0) for k, v in senza_web.items()}
        base = cost_of(model, entra, pricing)[0] + cost_of(model, esce, pricing)[0]
        maggiorato = (cost_of(model, entra, pricing)[0] * m_in
                      + cost_of(model, esce, pricing)[0] * m_out)
        extra += max(0.0, maggiorato - base)
    return {"richieste": n, "token": int(token), "extra": extra,
            "dichiarato": bool(m_in and m_out)}


# --------------------------------------------------------------------------- #
# Abbonamento — costo REALE, contrapposto al costo ipotetico a listino API
# --------------------------------------------------------------------------- #


def subscription_of(pricing: dict) -> dict | None:
    sub = pricing.get("subscription")
    if not isinstance(sub, dict) or not sub.get("monthly_cost"):
        return None
    return sub


def billing_of(config: dict) -> dict:
    b = dict(DEFAULT_PRICING["billing"])
    b.update(config.get("billing") or {})
    return b


def session_billing(sess: dict, config: dict) -> str:
    """'api' oppure 'subscription' per una singola sessione.

    In abbonamento il costo per token è solo ipotetico; a consumo è quello reale.
    Si può marcare un progetto o una sessione come API pur restando in abbonamento
    (es. lanciati con ANTHROPIC_API_KEY).
    """
    b = billing_of(config)
    if (b.get("mode") or "subscription").lower() == "api":
        return "api"
    sid = (sess.get("session_id") or "").lower()
    project = (sess.get("project") or "").lower()
    cwd = (sess.get("cwd") or "").lower()
    for needle in (b.get("api_projects") or []):
        n = str(needle).lower()
        if n and (n == project or n in cwd):
            return "api"
    for needle in (b.get("api_sessions") or []):
        n = str(needle).lower()
        if n and sid.startswith(n):
            return "api"
    return "subscription"


def display_currency(config: dict) -> str:
    """Valuta in cui esprimere il costo reale.

    A consumo puro si paga in dollari, quindi resta il listino; in abbonamento è
    la valuta con cui viene addebitata la quota (e le eventuali sessioni API
    vengono convertite lì dentro, per avere un totale solo).
    """
    if (billing_of(config).get("mode") or "subscription").lower() == "api":
        return config.get("currency") or "USD"
    sub = subscription_of(config)
    return (sub.get("currency") if sub else None) or config.get("currency") or "USD"


def to_display_currency(usd: float, config: dict) -> float | None:
    if (display_currency(config) or "USD").upper() == "USD":
        return usd
    rate = fx_usd_per_unit(config)
    if not rate:
        return None
    return usd / rate


def fx_usd_per_unit(pricing: dict) -> float | None:
    """Quanti USD vale un'unità della valuta dell'abbonamento (per il rapporto)."""
    fx = pricing.get("fx")
    if isinstance(fx, dict) and isinstance(fx.get("usd_per_unit"), (int, float)):
        return float(fx["usd_per_unit"])
    return None


def money(value: float, currency: str) -> str:
    symbol = {"EUR": "€", "USD": "$", "GBP": "£"}.get((currency or "").upper())
    if symbol:
        return f"{symbol}{value:,.2f}"
    return f"{value:,.2f} {currency}"


def monthly_costs(sessions: list[dict], pricing: dict) -> dict[str, dict]:
    """Costo ipotetico per mese di fatturazione, sommato su tutte le sessioni.

    Il bucket è per messaggio: una sessione lunga contribuisce a più mesi.
    """
    months: dict[str, dict] = {}
    for s in sessions:
        for month, models in (s.get("per_month") or {}).items():
            slot = months.get(month)
            if slot is None:
                slot = months[month] = {"month": month, "cost": 0.0, "tokens": new_tok(),
                                        "sessions": set(), "projects": set()}
            for model, data in models.items():
                slot["cost"] += data["cost"]
                add_tok(slot["tokens"], data["tokens"])
            slot["sessions"].add(s["session_id"])
            slot["projects"].add(s["project"] or "?")
    return months


def real_cost_table(sessions: list[dict], config: dict) -> dict[str, float]:
    """Quota fissa dovuta per ogni mese in cui c'è stata attività in abbonamento.

    L'abbonamento si paga uguale comunque lo si usi: il costo reale di un mese è
    la quota, non una funzione del consumo. Quello che varia è come la si ripartisce.
    Un mese in cui hai usato solo l'API non genera quota.
    """
    sub = subscription_of(config)
    if not sub:
        return {}
    fee = float(sub["monthly_cost"])
    since = (sub.get("since") or "")[:7]
    subscribed = [s for s in sessions if s.get("billing") == "subscription"]
    out = {}
    for month in monthly_costs(subscribed, config):
        if month == "?" or (since and month < since):
            continue
        out[month] = fee
    return out


def allocate_real_cost(sessions: list[dict], config: dict) -> None:
    """Calcola il costo REALE di ogni sessione, nella valuta di visualizzazione.

    - sessione in abbonamento → la sua fetta della quota del mese, proporzionale
      al costo ipotetico (se in agosto vale il 40% del consumo, prende il 40% della quota);
    - sessione a consumo (API) → il costo per token è già quello reale.

    Scrive `billing`, `real_cost` e `per_month_real` su ogni sessione.
    """
    for s in sessions:
        s["billing"] = session_billing(s, config)
        s["real_cost"] = 0.0
        s["per_month_real"] = {}

    # sessioni a consumo: il costo a listino È l'addebito
    for s in sessions:
        if s["billing"] != "api":
            continue
        for month, models in (s.get("per_month") or {}).items():
            usd = sum(d["cost"] for d in models.values())
            converted = to_display_currency(usd, config)
            if converted is None:
                continue
            s["real_cost"] += converted
            s["per_month_real"][month] = converted

    # sessioni in abbonamento: ripartizione della quota mensile
    subscribed = [s for s in sessions if s["billing"] == "subscription"]
    months = monthly_costs(subscribed, config)
    for month, fee in real_cost_table(sessions, config).items():
        total = months[month]["cost"]
        if total <= 0:
            continue
        for s in subscribed:
            own = sum(d["cost"] for d in (s.get("per_month") or {}).get(month, {}).values())
            if own <= 0:
                continue
            share = fee * own / total
            s["real_cost"] += share
            s["per_month_real"][month] = share


def new_tok() -> dict:
    return {k: 0 for k in TOKEN_FIELDS}


def add_tok(dst: dict, src: dict) -> None:
    for k in TOKEN_FIELDS:
        dst[k] = dst.get(k, 0) + src.get(k, 0)


def cache_hit(tok: dict) -> float | None:
    """Quota dei token di ingresso arrivati dalla cache invece che a prezzo pieno.

    Al denominatore c'e' tutto quello che e' entrato nel modello: la rilettura
    della cache, la sua scrittura e l'input non memorizzato. L'output resta
    fuori — e' quello che il modello produce, non quello che gli si da' da
    leggere, e metterlo dentro farebbe scendere il numero al crescere delle
    risposte, che non e' quello che la parola "cache" descrive.

    None quando non e' entrato niente: un turno senza richieste non ha una
    percentuale sbagliata, non ne ha nessuna.
    """
    served = tok.get("cache_read", 0)
    fresh = tok.get("input", 0) + tok.get("cache_w5m", 0) + tok.get("cache_w1h", 0)
    total = served + fresh
    if total <= 0:
        return None
    return served / total


def median(values) -> float | None:
    """Mediana, o None su una sequenza vuota.

    Sulle durate dice piu' della media: bastano due sessioni lasciate aperte
    tutta la notte per spostare la media di ore e far sembrare lungo un lavoro
    fatto di turni da un minuto.
    """
    xs = sorted(v for v in values if v is not None)
    if not xs:
        return None
    mid = len(xs) // 2
    if len(xs) % 2:
        return float(xs[mid])
    return (xs[mid - 1] + xs[mid]) / 2.0


# --------------------------------------------------------------------------- #
# Parsing dei transcript
# --------------------------------------------------------------------------- #


def session_id_from_path(path: str) -> str:
    """Ricava l'uuid di sessione dal percorso, qualunque sia l'annidamento.

        projects/<progetto>/<uuid>.jsonl                             sessione principale
        projects/<progetto>/<uuid>/subagents/agent-*.jsonl           subagent
        projects/<progetto>/<uuid>/subagents/workflows/wf_*/*.jsonl  agent di un workflow
    """
    parts = path.replace("\\", "/").split("/")
    if "projects" in parts:
        idx = parts.index("projects")
        rest = parts[idx + 1:]
        if len(rest) >= 2:
            head = rest[1]
            return head[:-6] if head.endswith(".jsonl") else head
    return os.path.splitext(os.path.basename(path))[0]


def parse_ts(value) -> float | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def month_of(ts: float | None) -> str:
    """Mese di fatturazione (ora locale) di un timestamp: 'YYYY-MM'."""
    if ts is None:
        return "?"
    return dt.datetime.fromtimestamp(ts).strftime("%Y-%m")


def extract_usage(usage: dict) -> dict:
    """usage grezza -> dizionario token normalizzato."""
    tok = new_tok()
    tok["input"] = usage.get("input_tokens") or 0
    tok["output"] = usage.get("output_tokens") or 0
    tok["cache_read"] = usage.get("cache_read_input_tokens") or 0

    cc = usage.get("cache_creation") or {}
    w5 = cc.get("ephemeral_5m_input_tokens") or 0
    w1 = cc.get("ephemeral_1h_input_tokens") or 0
    total_w = usage.get("cache_creation_input_tokens") or 0
    if w5 + w1 == 0 and total_w:
        # dettaglio TTL assente: attribuisco al TTL 5m (moltiplicatore minore)
        w5 = total_w
    tok["cache_w5m"] = w5
    tok["cache_w1h"] = w1

    stu = usage.get("server_tool_use") or {}
    tok["web_search"] = stu.get("web_search_requests") or 0
    tok["web_fetch"] = stu.get("web_fetch_requests") or 0
    return tok


# Messaggi che Claude Code scrive come "user" ma che l'utente non ha digitato.
SYNTHETIC_PROMPT_PREFIXES = (
    "[request interrupted",
    "<local-command-stdout>",
    "<local-command-stderr>",
    "api error:",
    "caveat: the messages below were generated by the user while running local commands",
)


def is_human_prompt(row: dict) -> bool:
    """True per un turno realmente scritto dall'utente (o dall'orchestratore, per i subagent).

    Esclude i tool_result — che Claude Code scrive come messaggi di tipo "user" —,
    i messaggi iniettati dal sistema (isMeta) e i segnaposto tipo
    "[Request interrupted by user]".
    """
    if row.get("type") != "user" or row.get("isMeta"):
        return False
    content = (row.get("message") or {}).get("content")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        kinds = {b.get("type") for b in content if isinstance(b, dict)}
        if not (kinds & {"text", "image"}) or "tool_result" in kinds:
            return False
        if "image" in kinds:
            return True
        text = " ".join(
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
        )
    else:
        return False
    stripped = text.strip()
    if not stripped:
        return False
    low = stripped.lower()
    return not any(low.startswith(p) for p in SYNTHETIC_PROMPT_PREFIXES)


# Un blocco di base64: e' cosi' che arrivano immagini e allegati dentro un
# tool_result. Va riconosciuto prima di tagliare, altrimenti il taglio produce
# comunque migliaia di caratteri illeggibili al posto del contenuto vero.
_BASE64_RUN = re.compile(r"[A-Za-z0-9+/\\n]{200,}={0,2}")


def clip_blob(value, limit: int) -> str:
    """Argomenti o risultato di uno strumento, in una riga sola e accorciati.

    Serve a farli leggere, non a conservarli: il transcript resta la fonte, e
    un risultato di `Read` su un file grosso non ha motivo di stare in memoria
    per intero solo perche' qualcuno ha aperto il turno che lo conteneva.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            text = str(value)

    def shrink(m):
        n = len(m.group()) * 3 // 4          # base64 -> byte
        return f"«{n // 1024} KB di dati»" if n >= 1024 else f"«{n} byte di dati»"

    text = _BASE64_RUN.sub(shrink, text)
    if len(text) > limit:
        return text[:limit] + ("…" if UNI else "...")
    return text


INTERRUPT_PREFIX = "[request interrupted"


def is_interrupt(row: dict) -> bool:
    """True se la riga e' il segnaposto che Claude Code scrive quando l'utente
    interrompe la risposta a meta'.

    Non e' un prompt — `is_human_prompt` giustamente lo scarta — ma non e'
    nemmeno rumore: dice che la risposta in corso non andava bene. E' l'unico
    giudizio negativo esplicito che il transcript contiene, e vale la pena
    portarlo sul turno a cui appartiene.
    """
    if row.get("type") != "user":
        return False
    content = (row.get("message") or {}).get("content")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = " ".join(
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
        )
    else:
        return False
    return text.strip().lower().startswith(INTERRUPT_PREFIX)


_DROP_TAGS = re.compile(
    r"<(system-reminder|ide_opened_file|ide_selection|command-message|local-command-stdout)>"
    r".*?</\1>",
    re.S,
)
_CMD_NAME = re.compile(r"<command-name>(.*?)</command-name>", re.S)
_CMD_ARGS = re.compile(r"<command-args>(.*?)</command-args>", re.S)
_ANY_TAG = re.compile(r"</?[a-zA-Z][\w:-]*>")


def prompt_text(row: dict, limit: int = 400) -> str:
    """Testo leggibile del turno utente, ripulito dai wrapper XML di Claude Code."""
    content = (row.get("message") or {}).get("content")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = " ".join(
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
        )
    else:
        text = ""

    name = _CMD_NAME.search(text)
    if name:  # slash command: mostra "/comando argomenti"
        args = _CMD_ARGS.search(text)
        text = name.group(1).strip() + (" " + args.group(1).strip() if args else "")
    else:
        text = _DROP_TAGS.sub(" ", text)
        text = _ANY_TAG.sub(" ", text)

    text = " ".join(text.split())
    return text[:limit]


class TranscriptParser:
    """Accumula gli eventi di un .jsonl.

    Lo stato resta vivo dopo `snapshot()`, così in modalità --watch si possono
    ingerire solo le righe nuove invece di rileggere l'intero file a ogni refresh
    (un transcript lungo può pesare centinaia di MB).
    """

    def __init__(self, path: str, pricing: dict, keep_messages: bool = False):
        self.path = path
        self.pricing = pricing
        self.keep_messages = keep_messages
        self.parts = path.replace("\\", "/").split("/")
        self.is_subagent = "subagents" in self.parts

        self.session_id = None
        self.cwd = None
        self.title = None
        self.version = None
        self.git_branch = None
        self.entrypoint = None
        self.first_prompt = None

        self.user_prompts = 0
        self.subagent_prompts = 0
        self.api_errors = 0
        self.bad_lines = 0
        self.agents: dict[str, int] = {}
        self.ts: list[float] = []
        self.prompts: list[dict] = []

        # Confini dei turni: (timestamp, testo del prompt). Da qui nascono i
        # trace. Sono pochi — decine per sessione — quindi stanno in memoria e
        # nella cache su disco senza pesare, al contrario delle richieste.
        self.marks: list[tuple[float, str]] = []
        self.interrupts: list[float] = []

        # Solo con keep_messages: l'anagrafica delle chiamate agli strumenti,
        # che serve a disegnare gli span di un singolo turno. Tenerla sempre
        # vorrebbe dire portarsi dietro argomenti e risultati di ogni comando
        # di ogni sessione — megabyte per un dettaglio che si guarda una volta.
        self.tool_uses: list[dict] = []
        self.tool_res: dict[str, dict] = {}

        # chiave di dedup -> {model, tok, ts, tools}
        self.dedup: dict[tuple, dict] = {}
        self.tool_ids: set[str] = set()
        self.result_ids: set[str] = set()

    # -- ingestione --------------------------------------------------------- #

    def ingest_line(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            self.bad_lines += 1  # riga parziale (streaming in corso) o corrotta
            return
        if isinstance(row, dict):
            self.ingest(row)

    def ingest(self, row: dict) -> None:
        rtype = row.get("type")

        if self.session_id is None and row.get("sessionId"):
            self.session_id = row["sessionId"]
        if self.cwd is None and row.get("cwd"):
            self.cwd = row["cwd"]
        if self.version is None and row.get("version"):
            self.version = row["version"]
        if self.git_branch is None and row.get("gitBranch"):
            self.git_branch = row["gitBranch"]
        if self.entrypoint is None and row.get("entrypoint"):
            self.entrypoint = row["entrypoint"]

        if rtype == "ai-title":
            self.title = row.get("aiTitle") or self.title
            return

        ts = parse_ts(row.get("timestamp"))
        if ts is not None:
            self.ts.append(ts)

        if rtype == "system":
            if row.get("subtype") == "api_error" or row.get("level") == "error":
                self.api_errors += 1
            return

        if rtype == "user":
            if is_human_prompt(row):
                sidechain = bool(row.get("isSidechain"))
                # Un turno comincia quando parla un umano. Dentro il transcript
                # principale i prompt di sidechain sono l'orchestratore che
                # istruisce un subagent *dentro* un turno gia' aperto: aprirne
                # uno nuovo spezzerebbe in due il turno del padre. Nel file di
                # un subagent, invece, quel prompt e' l'unico inizio che c'e'.
                if ts is not None and (not sidechain or self.is_subagent):
                    self.marks.append((ts, prompt_text(row, 200)))
                if sidechain:
                    self.subagent_prompts += 1
                else:
                    self.user_prompts += 1
                    if self.first_prompt is None:
                        self.first_prompt = prompt_text(row, 200)
                    if self.keep_messages:
                        # per rileggere la conversazione serve il testo intero
                        self.prompts.append(
                            {"kind": "prompt", "ts": ts,
                             "text": prompt_text(row, limit=200_000)}
                        )
            else:
                if ts is not None and is_interrupt(row):
                    self.interrupts.append(ts)
                content = (row.get("message") or {}).get("content")
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_result":
                            rid = block.get("tool_use_id") or f"anon-{len(self.result_ids)}"
                            self.result_ids.add(rid)
                            if self.keep_messages and rid not in self.tool_res:
                                # Il timestamp del risultato chiude lo strumento:
                                # e' da qui che si ricava quanto e' durato.
                                self.tool_res[rid] = {
                                    "ts": ts,
                                    "error": block.get("is_error") is True,
                                    "text": clip_blob(block.get("content"), 4000),
                                }
            return

        if rtype != "assistant":
            return

        msg = row.get("message") or {}
        model = normalize_model(msg.get("model") or "", self.pricing)

        names = []
        said = []
        for block in msg.get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                bid = block.get("id") or f"anon-{len(self.tool_ids)}"
                if bid not in self.tool_ids:
                    self.tool_ids.add(bid)
                    names.append(block.get("name") or "?")
                    if self.keep_messages:
                        self.tool_uses.append({
                            "id": bid,
                            "name": block.get("name") or "?",
                            "ts": ts,
                            "args": clip_blob(block.get("input"), 4000),
                            "req": row.get("requestId") or msg.get("id") or "",
                        })
            elif block.get("type") == "text" and self.keep_messages:
                # il testo cresce durante lo streaming: più avanti tengo il più lungo
                said.append(block.get("text") or "")
        text = "\n".join(t for t in said if t)

        agent = row.get("attributionAgent")
        if agent:
            self.agents[agent] = self.agents.get(agent, 0) + 1

        usage = msg.get("usage")
        if not isinstance(usage, dict):
            return

        # Durante lo streaming Claude Code scrive una riga per blocco di contenuto:
        # stessa message.id / requestId e usage ripetuta (output_tokens crescente).
        # Sommarle tutte gonfierebbe il costo — tengo il massimo per campo.
        key = (row.get("requestId") or "", msg.get("id") or row.get("uuid") or "")
        tok = extract_usage(usage)
        entry = self.dedup.get(key)
        if entry is None:
            self.dedup[key] = {"model": model, "tok": tok, "ts": ts, "tools": names,
                               "month": month_of(ts), "text": text,
                               "req": key[0] or key[1]}
        else:
            for k in TOKEN_FIELDS:
                if tok[k] > entry["tok"][k]:
                    entry["tok"][k] = tok[k]
            entry["tools"].extend(names)
            # righe successive dello stesso messaggio: la versione completa è la più lunga
            if len(text) > len(entry.get("text") or ""):
                entry["text"] = text
            if ts is not None:
                entry["ts"] = ts
                entry["month"] = month_of(ts)

    # -- risultato ---------------------------------------------------------- #

    def _build_turns(self) -> list[dict]:
        """Raggruppa le richieste in turni: da un prompt umano al successivo.

        Il criterio e' il **timestamp**, non la posizione nel file. Sembra un
        dettaglio e non lo e': Claude Code riemette interi segmenti di storia —
        stesso uuid, stesso timestamp — anche migliaia di righe piu' avanti
        (fork, `--resume`, compattazione). Raggruppando per posizione quelle
        righe finirebbero nell'ultimo turno, che si prenderebbe il costo di
        tutta la conversazione. Per timestamp tornano dove sono nate, e
        rileggere lo stesso file due volte da' lo stesso risultato.

        Il turno prodotto e' un aggregato leggero: quanto e' costato, quante
        richieste e quanti strumenti. Gli span veri — uno per richiesta e uno
        per strumento — si ricostruiscono solo quando si apre un trace, dal
        transcript, perche' tenerli per ogni sessione vorrebbe dire moltiplicare
        per cento la cache su disco per un dettaglio che si guarda una volta.
        """
        def blank(ts, text):
            return {"ts": ts, "end": ts, "prompt": text, "models": {},
                    "requests": 0, "tools": 0, "_names": set(), "interrupted": False}

        marks = sorted(self.marks)
        starts = [ts for ts, _ in marks]
        turns = [blank(ts, text) for ts, text in marks]
        # Richieste precedenti a qualsiasi prompt: sessione ripresa da un altro
        # file, oppure avvio non chiesto da nessuno. Non hanno un prompt a cui
        # appartenere, ma il loro costo e' reale e non va perso.
        head = None

        def bucket(ts):
            nonlocal head
            if ts is not None and starts:
                i = bisect.bisect_right(starts, ts) - 1
                if i >= 0:
                    return turns[i]
            if ts is None and turns:
                # Senza timestamp non si sa dove metterla: l'ultimo turno e' la
                # scelta meno sbagliata, perche' e' quello ancora in corso.
                return turns[-1]
            if head is None:
                head = blank(None, None)
            return head

        def stretch(t, ts):
            if ts is None:
                return
            if t["ts"] is None or ts < t["ts"]:
                t["ts"] = ts
            if t["end"] is None or ts > t["end"]:
                t["end"] = ts

        for entry in self.dedup.values():
            t = bucket(entry["ts"])
            add_tok(t["models"].setdefault(entry["model"], new_tok()), entry["tok"])
            t["requests"] += 1
            tools = entry.get("tools") or []
            t["tools"] += len(tools)
            t["_names"].update(tools)
            stretch(t, entry["ts"])
        for ts in self.interrupts:
            t = bucket(ts)
            t["interrupted"] = True
            stretch(t, ts)

        out = ([head] if head is not None else []) + turns
        for i, t in enumerate(out, 1):
            t["n"] = i
            # I nomi servono alla ricerca, non alla contabilita': ne bastano
            # pochi, ordinati, senza ripetizioni.
            t["tool_names"] = sorted(t.pop("_names"))[:24]
        return out

    def snapshot(self) -> dict:
        """Record aggregato. Non consuma lo stato: richiamabile a ogni refresh."""
        models: dict[str, dict] = {}
        by_month: dict[str, dict] = {}
        messages = list(self.prompts) if self.keep_messages else []
        # Richieste che nella finestra standard non ci sarebbero entrate: sono
        # la sola traccia rimasta dei modelli a contesto esteso, che nel
        # transcript si chiamano come gli altri.
        soglia = finestra_standard(self.pricing)
        oltre: dict[str, dict] = {}
        n_oltre = 0
        for entry in self.dedup.values():
            add_tok(models.setdefault(entry["model"], new_tok()), entry["tok"])
            if soglia and contesto_di(entry["tok"]) > soglia:
                n_oltre += 1
                add_tok(oltre.setdefault(entry["model"], new_tok()), entry["tok"])
            # una sessione può attraversare più mesi: il costo va attribuito al mese
            # del singolo messaggio, non a quello di inizio sessione
            month = entry.get("month") or "?"
            add_tok(by_month.setdefault(month, {}).setdefault(entry["model"], new_tok()),
                    entry["tok"])
            if self.keep_messages:
                messages.append({
                    "kind": "assistant",
                    "ts": entry["ts"],
                    "model": entry["model"],
                    "tok": entry["tok"],
                    "tools": entry["tools"],
                    "text": entry.get("text") or "",
                    "req": entry.get("req") or "",
                })
        if self.keep_messages:
            messages.sort(key=lambda m: (m["ts"] is None, m["ts"] or 0))

        session_id = self.session_id or session_id_from_path(self.path)

        project_dir = None
        if "projects" in self.parts:
            idx = self.parts.index("projects")
            if idx + 1 < len(self.parts):
                project_dir = self.parts[idx + 1]

        rec = {
            "path": self.path,
            "session_id": session_id,
            "project_dir": project_dir,
            "cwd": self.cwd,
            "title": self.title,
            "is_subagent": self.is_subagent,
            "models": models,
            "by_month": by_month,
            "oltre_finestra": {"richieste": n_oltre, "models": oltre},
            "user_prompts": self.user_prompts,
            "subagent_prompts": self.subagent_prompts,
            "assistant_msgs": len(self.dedup),
            "tool_calls": len(self.tool_ids),
            "tool_results": len(self.result_ids),
            "api_errors": self.api_errors,
            "bad_lines": self.bad_lines,
            "agents": dict(self.agents),
            "ts": list(self.ts),
            "turns": self._build_turns(),
            "first_prompt": self.first_prompt,
            "version": self.version,
            "git_branch": self.git_branch,
            "entrypoint": self.entrypoint,
        }
        if self.keep_messages:
            rec["messages"] = messages
            rec["tool_uses"] = self.tool_uses
            rec["tool_res"] = self.tool_res
        return rec


class LiveFile:
    """Parser incrementale: a ogni update() legge solo i byte aggiunti al file."""

    def __init__(self, path: str, pricing: dict):
        self.path = path
        self.pricing = pricing
        self.parser = TranscriptParser(path, pricing)
        self.pos = 0

    def update(self) -> None:
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return
        if size < self.pos:  # file troncato o ricreato: riparto da capo
            self.parser = TranscriptParser(self.path, self.pricing)
            self.pos = 0
        if size == self.pos:
            return
        try:
            with open(self.path, "rb") as fh:
                fh.seek(self.pos)
                data = fh.read()
        except OSError:
            return
        cut = data.rfind(b"\n")
        if cut == -1:
            return  # nessuna riga completa: la risposta è ancora in streaming
        self.pos += cut + 1
        for raw in data[: cut + 1].split(b"\n"):
            if raw:
                self.parser.ingest_line(raw.decode("utf-8", errors="replace"))

    def snapshot(self) -> dict:
        return self.parser.snapshot()


def scan_file(path: str, pricing: dict, keep_messages: bool = False) -> dict:
    """Aggrega un singolo .jsonl leggendolo per intero."""
    parser = TranscriptParser(path, pricing, keep_messages)
    try:
        fh = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return parser.snapshot()
    with fh:
        for line in fh:
            parser.ingest_line(line)
    return parser.snapshot()


# --------------------------------------------------------------------------- #
# Archivio su disco
# --------------------------------------------------------------------------- #


def cache_path() -> str:
    """La vecchia cache JSON. Resta solo per il trasloco e per cancellarla."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache.json")


def archivio_of(config: dict) -> dict:
    """Opzioni dell'archivio. Il testo e' spento finche' non lo si accende."""
    a = config.get("archivio") or {}
    return {"testo": bool(a.get("testo", False))}


def copilot_of(config: dict) -> dict:
    """Opzioni della sorgente Copilot.

    Acceso di default: leggere le chat di Copilot e' lo stesso gesto che
    leggere i transcript di Claude Code — file gia' sul disco, della stessa
    persona, sulla stessa macchina. Chi non vuole vederle lo spegne.
    """
    c = config.get("copilot") or {}
    return {"enabled": bool(c.get("enabled", True))}


def costo_txt(riga: dict) -> str:
    """Il costo, oppure un trattino dove non e' misurabile.

    Copilot si paga a quota fissa e non dichiara i token: un «se fosse API»
    calcolato li' sarebbe un numero inventato, e verrebbe letto come una spesa.
    Il trattino dice la sola cosa vera, cioe' che non si sa.
    """
    if riga.get("costo_noto") is False:
        return "—" if UNI else "-"
    return h_cost(riga.get("cost") or 0.0)


def tok_txt(riga: dict, n) -> str:
    """Un conteggio di token, o «—» dove la sorgente non li dichiara.

    Zero sarebbe un'altra affermazione: vorrebbe dire che quel turno non ha
    consumato niente, che e' falso.
    """
    if riga.get("costo_noto") is False:
        return "—" if UNI else "-"
    return h_tokens(n)


def fonte_di(riga: dict) -> str:
    return riga.get("fonte") or "claude-code"


ETICHETTA_FONTE = {"claude-code": "Claude Code", "copilot": "Copilot"}


def fonti_presenti(sessions: list[dict]) -> list[str]:
    return sorted({fonte_di(s) for s in sessions})


def apri_archivio_lettura():
    """L'archivio in sola lettura, per chi vuole solo guardarci dentro.

    Non allinea niente e non svuota niente: aprire l'archivio per leggere una
    conversazione non deve poter far ripartire una riscansione.
    """
    try:
        return cm_archivio.Archivio(cm_archivio.db_path(), CACHE_FORMAT,
                                    sola_lettura=True)
    except Exception:
        return None


def apri_archivio(use_cache: bool, quiet: bool = False, testo: bool = False):
    """Apre `cm-local.db`, traslocando la vecchia cache JSON la prima volta.

    Con `--no-cache` non si apre niente: quel flag vuol dire "non toccare il
    disco", e vale anche per l'archivio.

    Se l'archivio non si apre — disco pieno, file di un altro utente, unita' di
    rete che non regge il lock — non e' un motivo per non dare i numeri: si
    rilegge tutto e si va avanti senza. Un monitor che si rifiuta di misurare
    perche' non riesce a ricordare e' peggio di uno lento.
    """
    if not use_cache:
        return None
    try:
        arch = cm_archivio.Archivio(cm_archivio.db_path(), CACHE_FORMAT, testo=testo)
    except Exception as exc:
        if not quiet:
            warn(f"archivio non disponibile ({exc}): rileggo tutto")
        return None
    try:
        vecchia = cache_path()
        if os.path.exists(vecchia):
            if not arch.conta()["file"]:
                n = cm_archivio.importa_cache_json(arch, vecchia, CACHE_FORMAT)
                if n and not quiet:
                    info(f"archivio: {n} transcript ripresi dalla vecchia cache")
            os.remove(vecchia)
    except OSError:
        pass  # la cache vecchia e' derivata: non riuscire a toglierla non e' grave
    if arch.svuotata and not quiet:
        info("formato del parser cambiato: rileggo i transcript "
             "(l'archivio delle sessioni resta)")
    return arch


# --------------------------------------------------------------------------- #
# Aggregazione per sessione
# --------------------------------------------------------------------------- #


def project_label(rec: dict) -> str:
    cwd = rec.get("cwd")
    if cwd:
        return os.path.basename(cwd.rstrip("\\/")) or cwd
    pdir = rec.get("project_dir") or "?"
    return pdir.split("-")[-1] or pdir


def new_session(session_id: str) -> dict:
    return {
        "session_id": session_id,
        "project": None,
        "project_dir": None,
        "cwd": None,
        "title": None,
        "first_prompt": None,
        "models": {},
        "by_month": {},
        "user_prompts": 0,
        "subagent_prompts": 0,
        "assistant_msgs": 0,
        "tool_calls": 0,
        "tool_results": 0,
        "api_errors": 0,
        "bad_lines": 0,
        "agents": {},
        "subagent_files": 0,
        "oltre_finestra": {"richieste": 0, "models": {}},
        "ts": [],
        "turns": [],
        "sub_turns": [],
        "files": [],
        "version": None,
        "git_branch": None,
        "entrypoint": None,
        "mtime": 0.0,
    }


def merge_record(sess: dict, rec: dict, mtime: float) -> None:
    for model, tok in (rec.get("models") or {}).items():
        add_tok(sess["models"].setdefault(model, new_tok()), tok)
    oltre = rec.get("oltre_finestra") or {}
    if oltre.get("richieste"):
        dove = sess.setdefault("oltre_finestra", {"richieste": 0, "models": {}})
        dove["richieste"] += oltre["richieste"]
        for model, tok in (oltre.get("models") or {}).items():
            add_tok(dove["models"].setdefault(model, new_tok()), tok)
    for month, models in (rec.get("by_month") or {}).items():
        slot = sess["by_month"].setdefault(month, {})
        for model, tok in models.items():
            add_tok(slot.setdefault(model, new_tok()), tok)
    for key in ("user_prompts", "subagent_prompts", "assistant_msgs", "tool_calls",
                "tool_results", "api_errors", "bad_lines"):
        sess[key] += rec.get(key, 0)
    for agent, n in (rec.get("agents") or {}).items():
        sess["agents"][agent] = sess["agents"].get(agent, 0) + n
    sess["ts"].extend(rec.get("ts") or [])
    sess["files"].append(rec.get("path"))
    sess["mtime"] = max(sess["mtime"], mtime)
    if rec.get("is_subagent"):
        sess["subagent_files"] += 1
        # I turni di un subagent non sono turni della conversazione: sono lavoro
        # svolto *dentro* un turno del padre. Restano da parte fino a finalize,
        # che li riversa nel turno che li conteneva — anche perche' l'ordine in
        # cui i file arrivano qui non e' garantito.
        sess["sub_turns"].extend(rec.get("turns") or [])
    else:
        sess["turns"].extend(rec.get("turns") or [])
        sess["project_dir"] = sess["project_dir"] or rec.get("project_dir")
        sess["cwd"] = sess["cwd"] or rec.get("cwd")
        sess["title"] = sess["title"] or rec.get("title")
        sess["first_prompt"] = sess["first_prompt"] or rec.get("first_prompt")
        sess["version"] = sess["version"] or rec.get("version")
        sess["git_branch"] = sess["git_branch"] or rec.get("git_branch")
        sess["entrypoint"] = sess["entrypoint"] or rec.get("entrypoint")
    sess["project"] = sess["project"] or project_label(rec)


def build_traces(sess: dict, pricing: dict) -> list[dict]:
    """I turni della sessione con costo, token e cache, subagent compresi.

    Non tocca `turns` e `sub_turns`: `finalize` viene richiamato anche solo per
    ricalcolare i costi quando cambia il listino, e deve poter ripartire dagli
    stessi dati grezzi senza trovarli gia' consumati.
    """
    def in_order(key):
        return sorted((sess.get(key) or []),
                      key=lambda t: (t.get("ts") is None, t.get("ts") or 0))

    base = in_order("turns")
    subs = in_order("sub_turns")
    if not base:
        # Nessun transcript principale: restano solo i file dei subagent, e i
        # loro turni sono l'unica traccia di quel lavoro. Meglio mostrarli come
        # trace a se' che perderli.
        base, subs = subs, []

    traces = []
    for i, t in enumerate(base, 1):
        traces.append({
            "n": i,
            "ts": t.get("ts"),
            "end": t.get("end"),
            "prompt": t.get("prompt"),
            "models": {m: dict(tok) for m, tok in (t.get("models") or {}).items()},
            "requests": t.get("requests", 0),
            "tools": t.get("tools", 0),
            "tool_names": list(t.get("tool_names") or []),
            "interrupted": bool(t.get("interrupted")),
            "subagents": 0,
        })

    # I trace con un timestamp stanno in testa (l'ordinamento sopra manda in
    # fondo quelli senza): la ricerca binaria lavora solo su quel tratto.
    starts = [tr["ts"] for tr in traces if tr["ts"] is not None]
    for s in subs:
        ts = s.get("ts")
        i = bisect.bisect_right(starts, ts) - 1 if (ts is not None and starts) else -1
        if i < 0:
            if not traces:
                continue
            i = 0  # un subagent partito prima del primo prompt: al primo turno
        tr = traces[i]
        for model, tok in (s.get("models") or {}).items():
            add_tok(tr["models"].setdefault(model, new_tok()), tok)
        tr["requests"] += s.get("requests", 0)
        tr["tools"] += s.get("tools", 0)
        tr["subagents"] += 1
        if len(tr["tool_names"]) < 24:
            merged = sorted(set(tr["tool_names"]) | set(s.get("tool_names") or []))
            tr["tool_names"] = merged[:24]
        end = s.get("end")
        if end is not None and (tr["end"] is None or end > tr["end"]):
            tr["end"] = end

    for tr in traces:
        total = new_tok()
        cost = 0.0
        per_model = {}
        for model, tok in tr["models"].items():
            add_tok(total, tok)
            c, _ = cost_of(model, tok, pricing)
            cost += c
            per_model[model] = {"tokens": tok, "cost": c}
        tr["tokens"] = total
        tr["cost"] = cost
        tr["per_model"] = per_model
        tr["cache_hit"] = cache_hit(total)
        tr["duration"] = (
            (tr["end"] - tr["ts"]) if (tr["ts"] is not None and tr["end"] is not None) else None
        )
        # Come li conta ProxyAgent: la radice del turno, una richiesta al
        # modello, e uno span per ogni strumento usato.
        tr["spans"] = 1 + tr["requests"] + tr["tools"]
    return traces


def finalize(sess: dict, pricing: dict, idle_gap: float) -> dict:
    # Idempotente: "ts" viene consumato al primo giro; una seconda chiamata conserva i
    # tempi già calcolati e si limita a ricalcolare i costi (utile se cambia il listino).
    ts = sorted(sess.pop("ts", []))
    if ts or "start" not in sess:
        sess["start"] = ts[0] if ts else None
        sess["end"] = ts[-1] if ts else None
        sess["duration"] = (ts[-1] - ts[0]) if len(ts) > 1 else 0.0
        active = 0.0
        for a, b in zip(ts, ts[1:]):
            gap = b - a
            if gap <= idle_gap:
                active += gap
        sess["active"] = active

    total = new_tok()
    cost = 0.0
    unknown = set()
    per_model = {}
    for model, tok in sess["models"].items():
        add_tok(total, tok)
        c, miss = cost_of(model, tok, pricing)
        if miss:
            unknown.add(model)
        cost += c
        per_model[model] = {"tokens": tok, "cost": c}
    sess["tokens"] = total
    sess["cost"] = cost
    sess["per_model"] = per_model
    sess["unknown_models"] = sorted(unknown)
    sess["contesto_esteso"] = sovrapprezzo_contesto(sess, pricing)

    per_month = {}
    for month, models in (sess.get("by_month") or {}).items():
        per_month[month] = {
            m: {"tokens": tok, "cost": cost_of(m, tok, pricing)[0]}
            for m, tok in models.items()
        }
    sess["per_month"] = per_month
    sess["messages_total"] = sess["user_prompts"] + sess["assistant_msgs"]

    sess["traces"] = build_traces(sess, pricing)
    sess["traces_n"] = len(sess["traces"])
    sess["spans_n"] = sum(tr["spans"] for tr in sess["traces"])
    sess["cache_hit"] = cache_hit(total)
    sess["turn_median"] = median(tr["duration"] for tr in sess["traces"])
    return sess


def raccogli_testo(dove: list[dict], rec: dict) -> None:
    """Mette da parte i messaggi di un file, marcati se vengono da un subagent."""
    for m in rec.get("messages") or ():
        m["subagent"] = rec.get("is_subagent", False)
        dove.append(m)


def sessione_nel_progetto(sess: dict, project: str | None) -> bool:
    """Il filtro `--project` applicato a una sessione senza piu' file.

    Non si puo' usare `path_matches_project`, che ragiona sul percorso del
    transcript: qui il transcript non c'e' piu'. Restano il progetto e la
    cartella di lavoro, che sono poi le due cose che si scrivono davvero.
    """
    if not project or project.lower() == "all":
        return True
    ago = project.lower()
    return (ago in (sess.get("project") or "").lower()
            or ago in (sess.get("cwd") or "").lower())


def collect(base: str, pricing: dict, use_cache: bool, idle_gap: float,
            project: str | None = None, quiet: bool = False,
            on_progress=None) -> list[dict]:
    """Aggrega tutte le sessioni sotto `base`.

    `on_progress(fatti, totali, path, da_cache)` viene richiamato per ogni file:
    serve a chi mostra una barra di avanzamento (la GUI). Default None = nessun effetto.
    """
    # Nessun transcript non vuol dire nessuna sessione: possono essere scaduti
    # tutti, e allora l'unica copia rimasta e' quella in archivio. L'avviso
    # arriva in fondo, quando si sa se e' venuto fuori qualcosa lo stesso.
    files = sorted(glob.glob(os.path.join(base, "**", "*.jsonl"), recursive=True))

    arch = apri_archivio(use_cache, quiet, testo=archivio_of(pricing)["testo"])
    tieni_testo = bool(arch and arch.testo)
    indice = arch.indice() if arch else {}
    vivi: set[str] = set()
    sessions: dict[str, dict] = {}
    # Solo quando si archivia il testo: quali sessioni sono state toccate in
    # questo giro, e quali dei loro file sono invece arrivati dalla cache.
    testi: dict[str, list[dict]] = {}
    toccate: set[str] = set()
    dalla_cache: dict[str, list[str]] = {}
    parsed = 0
    total_files = len(files)
    if on_progress is not None:
        on_progress(0, total_files, None, True)

    try:
        for done, path in enumerate(files, 1):
            try:
                st = os.stat(path)
            except OSError:
                if on_progress is not None:
                    on_progress(done, total_files, path, True)
                continue
            # I file esclusi dal filtro restano comunque "vivi": esistono, e la
            # potatura toglie solo quelli spariti davvero.
            vivi.add(cm_archivio.chiave(path))
            if project and not path_matches_project(path, project):
                if on_progress is not None:
                    on_progress(done, total_files, path, True)
                continue

            voce = indice.get(cm_archivio.chiave(path))
            rec = None
            if (voce and voce[0] == st.st_size
                    and abs(voce[1] - st.st_mtime) < 0.001):
                rec = arch.record(path)
            da_cache = rec is not None
            if rec is None:
                if not quiet and st.st_size > 20_000_000:
                    info(f"analizzo {os.path.basename(path)} ({st.st_size / 1e6:.0f} MB)…")
                rec = scan_file(path, pricing, keep_messages=tieni_testo)
                parsed += 1
                if arch:
                    arch.scrivi_file(path, st.st_size, st.st_mtime, rec)

            sid = rec.get("session_id") or path
            sess = sessions.setdefault(sid, new_session(sid))
            merge_record(sess, rec, st.st_mtime)
            if tieni_testo:
                if da_cache:
                    dalla_cache.setdefault(sid, []).append(path)
                else:
                    toccate.add(sid)
                    raccogli_testo(testi.setdefault(sid, []), rec)

            if on_progress is not None:
                on_progress(done, total_files, path, da_cache)

        out = [finalize(s, pricing, idle_gap) for s in sessions.values()]
        out.sort(key=lambda s: s["end"] or 0, reverse=True)

        if arch:
            if tieni_testo:
                # Il testo di una sessione si riscrive per intero: se anche solo
                # un suo file e' cambiato, gli altri vanno riletti, altrimenti
                # riscrivendo si perderebbe la meta' arrivata dalla cache. Sono
                # i file dei subagent, piccoli; quello grosso e' proprio quello
                # che e' cambiato e che si stava rileggendo comunque.
                for sid in toccate:
                    for path in dalla_cache.get(sid, ()):
                        raccogli_testo(testi.setdefault(sid, []),
                                       scan_file(path, pricing, keep_messages=True))
                for s in out:
                    msgs = testi.get(s["session_id"])
                    if msgs is None:
                        continue
                    msgs.sort(key=lambda m: (m["ts"] is None, m["ts"] or 0))
                    arch.scrivi_messaggi(
                        s["session_id"], msgs,
                        [t["ts"] for t in s["traces"] if t["ts"] is not None])
            arch.pota(base, vivi)
            arch.scrivi_sessioni(out)

        # Copilot: fonte separata, letta dallo storage di VS Code. Non passa da
        # `pota`, che ragiona sui transcript, e le sue righe sono acquisite per
        # definizione — quel formato non e' documentato e domani puo' cambiare.
        if copilot_of(pricing)["enabled"]:
            copilot = [s for s in cm_copilot.sessioni(keep_messages=tieni_testo)
                       if sessione_nel_progetto(s, project)]
            if copilot:
                if arch:
                    arch.scrivi_sessioni(copilot, fonte=cm_copilot.FONTE)
                    if tieni_testo:
                        for s in copilot:
                            arch.scrivi_messaggi(
                                s["session_id"], s.get("messaggi") or [],
                                [t["ts"] for t in s["traces"] if t["ts"] is not None])
                for s in copilot:
                    s.pop("messaggi", None)
                out.extend(copilot)

        if arch:
            # Sessioni di cui non resta piu' la fonte: senza questo passaggio
            # sparirebbero dai conti il giorno in cui Claude Code fa le pulizie,
            # o VS Code le sue.
            attive = {"claude-code"}
            if copilot_of(pricing)["enabled"]:
                attive.add(cm_copilot.FONTE)
            orfane = [o for o in arch.sessioni_orfane(
                          {s["session_id"] for s in out}, fonti=attive)
                      if sessione_nel_progetto(o, project)]
            out.extend(orfane)
        out.sort(key=lambda s: s["end"] or 0, reverse=True)
    finally:
        if arch:
            try:
                arch.chiudi()
            except Exception:
                pass
    if not out and not quiet:
        warn(f"Nessun transcript trovato in {base}")
    return out


def session_files_from_transcript(transcript_path: str) -> list[str]:
    """File di una sessione partendo dal suo transcript principale, senza glob globale.

        <dir>/<uuid>.jsonl  +  <dir>/<uuid>/subagents/**/*.jsonl

    Il ramo dei subagent è ricorsivo perché i workflow annidano ulteriormente
    (`subagents/workflows/wf_*/`).
    """
    out = [transcript_path] if os.path.isfile(transcript_path) else []
    sub = os.path.join(os.path.splitext(transcript_path)[0], "subagents")
    if os.path.isdir(sub):
        out.extend(sorted(glob.glob(os.path.join(sub, "**", "*.jsonl"), recursive=True)))
    return out


def path_matches_project(path: str, needle: str) -> bool:
    if needle.lower() in ("all", "*"):
        return True
    return needle.lower() in path.lower()


# --------------------------------------------------------------------------- #
# Formattazione
# --------------------------------------------------------------------------- #


# Chi usa questo modulo come libreria (es. la GUI) può dirottare i messaggi qui invece
# che su stderr: callable(level, msg) -> True se il messaggio è stato gestito.
LOG_HOOK = None


def warn(msg: str) -> None:
    if LOG_HOOK is not None and LOG_HOOK("warn", msg):
        return
    print(C.w("! " + msg, C.YELLOW), file=sys.stderr)


def info(msg: str) -> None:
    if LOG_HOOK is not None and LOG_HOOK("info", msg):
        return
    print(C.w(BULLET + " " + msg, C.DIM), file=sys.stderr)


def h_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 10_000:
        return f"{n / 1000:.0f}k"
    if n >= 1_000:
        return f"{n / 1000:.1f}k"
    return str(int(n))


def h_dur(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    s = int(seconds)
    if s >= 3600:
        return f"{s // 3600}h{(s % 3600) // 60:02d}m"
    if s >= 60:
        return f"{s // 60}m{s % 60:02d}s"
    return f"{s}s"


def h_cost(value: float) -> str:
    if value >= 100:
        return f"${value:,.1f}"
    if value >= 1:
        return f"${value:.2f}"
    if value > 0:
        return f"${value:.4f}"
    return "$0"


def h_time(epoch: float | None, fmt: str = "%d/%m %H:%M") -> str:
    if not epoch:
        return "-"
    return dt.datetime.fromtimestamp(epoch).strftime(fmt)


def h_ago(epoch: float | None) -> str:
    if not epoch:
        return "-"
    delta = time.time() - epoch
    if delta < 60:
        return f"{int(delta)}s fa"
    if delta < 3600:
        return f"{int(delta // 60)}m fa"
    if delta < 86400:
        return f"{int(delta // 3600)}h fa"
    return f"{int(delta // 86400)}g fa"


def trunc(text: str, width: int) -> str:
    text = text or ""
    if len(text) <= width:
        return text
    return text[: max(0, width - 1)] + ("…" if UNI else "~")


def print_table(headers, rows, aligns=None, styles=None) -> None:
    if not rows:
        return
    ncol = len(headers)
    aligns = aligns or ["<"] * ncol
    widths = [len(str(hd)) for hd in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    head = "  ".join(f"{str(hd):{aligns[i]}{widths[i]}}" for i, hd in enumerate(headers))
    print(C.w(head, C.BOLD))
    print(C.w(HR * len(head), C.DIM))
    for r, row in enumerate(rows):
        line = "  ".join(f"{str(cell):{aligns[i]}{widths[i]}}" for i, cell in enumerate(row))
        style = (styles or {}).get(r)
        print(C.w(line, *style) if style else line)


def cost_columns(config: dict) -> tuple[str, str | None]:
    """Intestazioni delle colonne di costo.

    A consumo il costo per token È l'addebito: una colonna sola, senza doppioni.
    In abbonamento la seconda colonna è una PERCENTUALE, non un importo: quegli
    euro non sono una spesa in più ma una fetta di soldi già usciti, e mostrarli
    come importo fa credere di aver speso quella cifra.
    """
    if (billing_of(config).get("mode") or "subscription").lower() == "api":
        return "SPESO", None
    return "SE FOSSE API", "% CONS"


def cost_legend(config: dict, total_real: float = 0.0) -> list[str]:
    """Righe che spiegano cosa sono davvero le colonne di costo."""
    mode = (billing_of(config).get("mode") or "subscription").lower()
    if mode == "api":
        return ["SPESO = addebito reale a consumo: questi soldi li hai spesi."]
    cur = display_currency(config)
    sub = subscription_of(config)
    quota = money(float(sub["monthly_cost"]), cur) if sub else "la quota"
    pagato = money(total_real, cur)
    return [
        f"Hai pagato {pagato} in tutto ({quota}/mese), comunque tu li usi.",
        "Qui si misura il CONSUMO; i soldi veri sono in --by-month.",
        "",
        "SE FOSSE API = quanto varrebbe a listino API. NON l'hai pagato.",
        "% CONS       = quanto pesa questa riga sul consumo totale.",
        "               Non è una fetta di euro: in un mese usato poco la quota",
        "               resta in gran parte inutilizzata, non 'consumata' da qui.",
    ]


def print_cost_legend(config: dict, sessions: list[dict] | None = None) -> None:
    total_real = sum(s.get("real_cost", 0.0) for s in (sessions or []))
    print()
    for line in cost_legend(config, total_real):
        print(C.w("  " + line, C.DIM) if line else "")


def share_pct(value: float, total: float) -> str:
    """Peso di una riga sul consumo totale mostrato."""
    if total <= 0 or value <= 0:
        return "-"
    p = 100 * value / total
    return f"{p:.0f}%" if p >= 1 else "<1%"


def plan_note(config: dict) -> str:
    mode = (billing_of(config).get("mode") or "subscription").lower()
    listino = f"{BULLET} listino {config.get('updated', '?')}"
    if mode == "api":
        return f"uso a consumo: il costo per token è quello reale {listino}"
    sub = subscription_of(config)
    plan = (sub or {}).get("plan", "abbonamento")
    return (f"{plan}: il costo per token è IPOTETICO, quello reale è la quota "
            f"{listino}")


# --------------------------------------------------------------------------- #
# Viste
# --------------------------------------------------------------------------- #


def view_summary(sessions: list[dict], pricing: dict, args) -> None:
    shown = sessions[: args.top] if args.top else sessions
    if not shown:
        print("Nessuna sessione corrispondente ai filtri.")
        return

    cur = display_currency(pricing)
    h_hyp, h_real = cost_columns(pricing)
    headers = ["PROGETTO", "SESSIONE", "INIZIO", "DURATA", "ATTIVO", "TU/CLAUDE", "TOOL",
               "IN", "OUT", "CACHE W", "CACHE R", h_hyp]
    aligns = ["<", "<", "<", ">", ">", ">", ">", ">", ">", ">", ">", ">"]
    sub = h_real is not None and subscription_of(pricing)
    if sub:
        headers.append(h_real)
        aligns.append(">")
    tot_cost = sum(s["cost"] for s in shown)
    rows = []
    for s in shown:
        t = s["tokens"]
        row = [
            trunc(s["project"] or "?", 20),
            s["session_id"][:8],
            h_time(s["start"]),
            h_dur(s["duration"]),
            h_dur(s["active"]),
            f"{s['user_prompts']}/{s['assistant_msgs']}",
            s["tool_calls"],
            tok_txt(s, t["input"]),
            tok_txt(s, t["output"]),
            tok_txt(s, t["cache_w5m"] + t["cache_w1h"]),
            tok_txt(s, t["cache_read"]),
            costo_txt(s),
        ]
        if sub:
            row.append(share_pct(s["cost"], tot_cost))
        rows.append(row)

    total = new_tok()
    for s in shown:
        add_tok(total, s["tokens"])
    total_row = [
        f"TOTALE ({len(shown)})", "", "",
        h_dur(sum(s["duration"] for s in shown)),
        h_dur(sum(s["active"] for s in shown)),
        f"{sum(s['user_prompts'] for s in shown)}/{sum(s['assistant_msgs'] for s in shown)}",
        sum(s["tool_calls"] for s in shown),
        h_tokens(total["input"]), h_tokens(total["output"]),
        h_tokens(total["cache_w5m"] + total["cache_w1h"]), h_tokens(total["cache_read"]),
        h_cost(sum(s["cost"] for s in shown)),
    ]
    if sub:
        total_row.append("100%" if tot_cost else "-")
    rows.append(total_row)

    print()
    print_table(headers, rows, aligns, styles={len(rows) - 1: (C.BOLD,)})
    print_trace_summary(shown)
    print_fonti_note(shown)
    print_contesto_esteso(shown, pricing)
    print_cost_legend(pricing, shown)

    if not args.no_breakdown:
        print_model_breakdown(shown, pricing)
    report_unknown(shown)


def h_pct(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{100 * value:.1f}%"


def stats_of_traces(traces: list[dict]) -> dict:
    """Numeri che si leggono per turno, non per sessione.

    Si parte dai turni e non dalle sessioni perche' cosi' i numeri seguono il
    filtro: cercando "riconciliazione", la cache hit che si legge e' quella dei
    turni trovati, non quella delle sessioni che li contengono. Le due cose
    coincidono solo quando non si filtra niente — ogni richiesta finisce in uno
    e un solo turno.
    """
    total = new_tok()
    for tr in traces:
        add_tok(total, tr["tokens"])
    return {
        "traces": len(traces),
        "spans": sum(tr["spans"] for tr in traces),
        "median": median(tr["duration"] for tr in traces),
        "cache_hit": cache_hit(total),
        "interrupted": sum(1 for tr in traces if tr["interrupted"]),
    }


def trace_stats(sessions: list[dict]) -> dict:
    return stats_of_traces([tr for s in sessions for tr in (s.get("traces") or [])])


def print_trace_summary(sessions: list[dict]) -> None:
    st = trace_stats(sessions)
    if not st["traces"]:
        return
    parts = [
        f"{C.w('turni', C.DIM)} {st['traces']}",
        f"{C.w('span', C.DIM)} {st['spans']}",
        f"{C.w('durata mediana', C.DIM)} {h_dur(st['median'])}",
        f"{C.w('cache hit', C.DIM)} {h_pct(st['cache_hit'])}",
    ]
    if st["interrupted"]:
        parts.append(f"{C.w('interrotti', C.DIM)} {st['interrupted']}")
    print()
    print("  " + f"   {BULLET}   ".join(parts))


def print_fonti_note(sessions: list[dict]) -> None:
    """Spiega i trattini, quando ce ne sono.

    Un «—» in una colonna di costi si legge come uno zero se nessuno dice il
    contrario. Qui il contrario e' che quella sorgente i token non li dichiara.
    """
    muti = [s for s in sessions if s.get("costo_noto") is False]
    if not muti:
        return
    fonti = sorted({fonte_di(s) for s in muti})
    nomi = ", ".join(ETICHETTA_FONTE.get(f, f) for f in fonti)
    turni = sum(s.get("traces_n", 0) for s in muti)
    print()
    print(C.w(f"  {len(muti)} sessioni da {nomi} ({turni} turni): ci sono turni, "
              f"tempi e strumenti.", C.DIM))
    print(C.w("  Token e costo no — si paga a quota fissa e non vengono "
              "dichiarati. «—» vuol dire non misurabile, non zero.", C.DIM))


def print_contesto_esteso(sessions: list[dict], pricing: dict) -> None:
    """Il consumo che la finestra standard non avrebbe potuto contenere.

    E' un limite noto messo in chiaro invece che una stima messa nei totali:
    quel lavoro e' stato pagato di piu' di quanto dica la colonna del costo, e
    fin qui non lo diceva niente.
    """
    n = sum((s.get("contesto_esteso") or {}).get("richieste", 0) for s in sessions)
    if not n:
        return
    token = sum((s.get("contesto_esteso") or {}).get("token", 0) for s in sessions)
    extra = sum((s.get("contesto_esteso") or {}).get("extra", 0.0) for s in sessions)
    soglia = finestra_standard(pricing)
    print()
    print(C.w(f"  {n} richieste hanno superato i {soglia // 1000}k token di "
              f"contesto ({h_tokens(token)} in tutto):", C.DIM))
    print(C.w("  sono girate su un modello a finestra estesa, che il transcript "
              "non distingue per nome", C.DIM))
    print(C.w("  ma che si paga a listino maggiorato.", C.DIM))
    if extra:
        print(C.w(f"  Col rapporto dichiarato in long_context sarebbero "
                  f"{h_cost(extra)} in piu', non compresi nei totali.", C.DIM))
    else:
        print(C.w("  Quanto in piu' non e' calcolato: dichiara il rapporto in "
                  "long_context (config.json).", C.DIM))


def print_model_breakdown(sessions: list[dict], pricing: dict) -> None:
    agg: dict[str, dict] = {}
    msgs: dict[str, int] = {}
    for s in sessions:
        for model, data in s["per_model"].items():
            add_tok(agg.setdefault(model, new_tok()), data["tokens"])
            msgs[model] = msgs.get(model, 0) + 0
    if not agg:
        return
    rows = []
    grand = sum(cost_of(m, t, pricing)[0] for m, t in agg.items()) or 1.0
    for model, tok in sorted(agg.items(), key=lambda kv: -cost_of(kv[0], kv[1], pricing)[0]):
        cost, unknown = cost_of(model, tok, pricing)
        rows.append([
            model + (" (?)" if unknown else ""),
            h_tokens(tok["input"]),
            h_tokens(tok["output"]),
            h_tokens(tok["cache_w5m"]),
            h_tokens(tok["cache_w1h"]),
            h_tokens(tok["cache_read"]),
            h_cost(cost),
            f"{100 * cost / grand:.0f}%",
        ])
    print()
    print(C.w("  Per modello", C.BOLD))
    print_table(["MODELLO", "IN", "OUT", "CACHE W5m", "CACHE W1h", "CACHE R", "COSTO", "%"],
                rows, ["<", ">", ">", ">", ">", ">", ">", ">"])


def report_unknown(sessions: list[dict]) -> None:
    unknown = sorted({m for s in sessions for m in s["unknown_models"]})
    if unknown:
        print()
        warn("modelli non presenti in pricing.json (costo contato 0): " + ", ".join(unknown))
        warn("aggiungili a pricing.json per avere il costo corretto.")


def view_by_project(sessions: list[dict], pricing: dict, args) -> None:
    agg: dict[str, dict] = {}
    for s in sessions:
        key = s["project"] or "?"
        p = agg.setdefault(key, {
            "sessions": 0, "user": 0, "assistant": 0, "tools": 0,
            "duration": 0.0, "active": 0.0, "cost": 0.0, "tokens": new_tok(), "last": 0.0,
        })
        p["sessions"] += 1
        p["user"] += s["user_prompts"]
        p["assistant"] += s["assistant_msgs"]
        p["tools"] += s["tool_calls"]
        p["duration"] += s["duration"]
        p["active"] += s["active"]
        p["cost"] += s["cost"]
        p["real"] = p.get("real", 0.0) + s.get("real_cost", 0.0)
        add_tok(p["tokens"], s["tokens"])
        p["last"] = max(p["last"], s["end"] or 0)

    rows = []
    cur = display_currency(pricing)
    h_hyp, h_real = cost_columns(pricing)
    sub = h_real is not None and subscription_of(pricing)
    tot_cost = sum(p["cost"] for p in agg.values())
    for name, p in sorted(agg.items(), key=lambda kv: -kv[1]["cost"]):
        t = p["tokens"]
        row = [
            trunc(name, 28), p["sessions"], h_dur(p["duration"]), h_dur(p["active"]),
            f"{p['user']}/{p['assistant']}", p["tools"],
            h_tokens(t["input"]), h_tokens(t["output"]),
            h_tokens(t["cache_w5m"] + t["cache_w1h"]), h_tokens(t["cache_read"]),
            h_cost(p["cost"]),
        ]
        if sub:
            row.append(share_pct(p["cost"], tot_cost))
        row.append(h_ago(p["last"]))
        rows.append(row)
    total_cost = sum(p["cost"] for p in agg.values())
    total_row = ["TOTALE", sum(p["sessions"] for p in agg.values()),
                 h_dur(sum(p["duration"] for p in agg.values())),
                 h_dur(sum(p["active"] for p in agg.values())),
                 f"{sum(p['user'] for p in agg.values())}/{sum(p['assistant'] for p in agg.values())}",
                 sum(p["tools"] for p in agg.values()), "", "", "", "",
                 h_cost(total_cost)]
    if sub:
        total_row.append("100%" if tot_cost else "-")
    total_row.append("")
    rows.append(total_row)
    headers = ["PROGETTO", "SESS", "DURATA", "ATTIVO", "TU/CLAUDE", "TOOL",
               "IN", "OUT", "CACHE W", "CACHE R", h_hyp]
    aligns = ["<", ">", ">", ">", ">", ">", ">", ">", ">", ">", ">"]
    if sub:
        headers.append(h_real)
        aligns.append(">")
    headers.append("ULTIMA")
    aligns.append(">")
    print()
    print_table(headers, rows, aligns, styles={len(rows) - 1: (C.BOLD,)})
    print_cost_legend(pricing, sessions)
    if not args.no_breakdown:
        print_model_breakdown(sessions, pricing)
    report_unknown(sessions)


def view_by_month(sessions: list[dict], pricing: dict, args) -> None:
    """Consumo, costo ipotetico e costo reale, mese per mese."""
    sub = subscription_of(pricing)
    months = monthly_costs(sessions, pricing)
    fees = real_cost_table(sessions, pricing)
    if not months:
        print("Nessun dato mensile.")
        return

    mode = (billing_of(pricing).get("mode") or "subscription").lower()
    rate = fx_usd_per_unit(pricing)
    cur = display_currency(pricing)
    now_month = dt.datetime.now().strftime("%Y-%m")
    # a consumo puro il rapporto sarebbe sempre 1: la colonna non dice nulla
    show_ratio = mode != "api"

    rows = []
    tot_hyp = tot_real = 0.0
    tot_tok = new_tok()
    for month in sorted(months, reverse=True):
        slot = months[month]
        tok = slot["tokens"]
        consumed = tok["input"] + tok["output"] + tok["cache_read"] + \
            tok["cache_w5m"] + tok["cache_w1h"]
        hyp = slot["cost"]
        tot_hyp += hyp
        add_tok(tot_tok, tok)
        # reale = quota del mese (abbonamento) + costo a consumo delle sessioni API
        real = sum(s.get("per_month_real", {}).get(month, 0.0) for s in sessions)
        tot_real += real
        api_only = mode != "api" and month not in fees
        row = [
            month + (" *" if month == now_month else "") + (" API" if api_only else ""),
            len(slot["sessions"]), len(slot["projects"]),
            h_tokens(consumed), h_tokens(tok["output"]),
            h_cost(hyp),
            money(real, cur) if real else "-",
        ]
        if show_ratio:
            row.append(f"{hyp / (real * rate):.1f}×" if (real and rate) else "-")
        rows.append(row)

    consumed_tot = sum(tot_tok[k] for k in
                       ("input", "output", "cache_read", "cache_w5m", "cache_w1h"))
    total_row = ["TOTALE", "", "", h_tokens(consumed_tot), h_tokens(tot_tok["output"]),
                 h_cost(tot_hyp), money(tot_real, cur) if tot_real else "-"]
    if show_ratio:
        total_row.append(f"{tot_hyp / (tot_real * rate):.1f}×" if (tot_real and rate) else "-")
    rows.append(total_row)

    print()
    mode = (billing_of(pricing).get("mode") or "subscription").lower()
    n_api = sum(1 for s in sessions if s.get("billing") == "api")
    if mode == "api":
        print(C.w(f"  Uso a consumo (API)  {BULLET}  il costo per token è quello reale", C.BOLD))
        print()
    elif sub:
        line = (f"  {sub.get('plan', 'abbonamento')}"
                f"  {BULLET}  {money(float(sub['monthly_cost']), cur)}/mese"
                + (f"  ({sub['note']})" if sub.get("note") else ""))
        if n_api:
            line += f"  {BULLET}  {n_api} sessioni a consumo"
        print(C.w(line, C.BOLD))
        if pricing.get("_path"):
            print(C.w(f"  da {pricing['_path']}  {BULLET}  modificalo lì se cambia il piano",
                      C.DIM))
        print()
    # qui il secondo numero è una cifra davvero uscita dal conto, non una ripartizione
    headers = ["MESE", "SESS", "PROG", "TOKEN", "OUTPUT",
               "SE FOSSE API" if show_ratio else "SPESO",
               "PAGATO" if show_ratio else ""]
    aligns = ["<", ">", ">", ">", ">", ">", ">"]
    if not show_ratio:  # a consumo le due colonne sarebbero identiche
        headers = headers[:-1]
        aligns = aligns[:-1]
        rows = [r[:-1] for r in rows]
    if show_ratio:
        headers.append("RESA")
        aligns.append(">")
    print_table(headers, rows, aligns, styles={len(rows) - 1: (C.BOLD,)})
    print()
    if mode == "api":
        print(C.w("  SPESO = addebito reale a consumo: questi soldi li hai spesi.", C.DIM))
    else:
        print(C.w("  PAGATO       = soldi davvero usciti dal conto."
                  + ("  (+ sessioni a consumo)" if n_api else ""), C.DIM))
        print(C.w("  SE FOSSE API = quanto sarebbe costato a listino. NON l'hai pagato.", C.DIM))
        if rate:
            print(C.w(f"  RESA         = quanto valore hai tirato fuori dalla quota "
                      f"(cambio {rate} USD per {cur}).", C.DIM))
        else:
            print(C.w("  RESA         = non calcolabile: manca fx.usd_per_unit in config.json",
                      C.DIM))
    if any("*" in str(r[0]) for r in rows):
        print(C.w("  * mese in corso" + ("" if mode == "api"
                                         else ": la quota è già dovuta per intero"), C.DIM))
    if mode != "api" and any(" API" in str(r[0]) for r in rows):
        print(C.w("  API = mese senza attività in abbonamento, solo sessioni a consumo", C.DIM))


def flatten_traces(sessions: list[dict], search: str = "",
                   anche: set | None = None) -> list[dict]:
    """Tutti i turni di tutte le sessioni, dal piu' recente.

    Ogni turno porta con se' la sessione da cui viene: senza, una tabella di
    trace e' un elenco di frasi senza contesto. `search` filtra sul testo del
    prompt, sui nomi degli strumenti, sul progetto e sull'identificativo di
    sessione — quello che si ha sempre sottomano, senza rileggere niente.

    `anche` sono i turni trovati cercando nel testo archiviato: turni che il
    filtro qui sopra scarterebbe perche' la parola non sta nel prompt ma in una
    risposta. E' una mappa (sessione, turno) -> frammento, ma va bene anche un
    insieme di sole coppie: quello che serve qui e' l'appartenenza, il frammento
    e' un di piu' che si mostra se c'e'.
    """
    needle = (search or "").strip().lower()
    anche = anche or {}
    trovati = anche if isinstance(anche, dict) else {}
    out = []
    for s in sessions:
        for tr in s.get("traces") or []:
            chiave = (s.get("session_id"), tr.get("n"))
            if needle:
                if chiave not in anche:
                    blob = " ".join(filter(None, [
                        tr.get("prompt") or "", " ".join(tr.get("tool_names") or []),
                        s.get("project") or "", s.get("session_id") or "",
                    ])).lower()
                    if needle not in blob:
                        continue
            row = dict(tr)
            row["session_id"] = s.get("session_id")
            row["project"] = s.get("project")
            row["title"] = s.get("title")
            row["archiviata"] = bool(s.get("archiviata"))
            hit = trovati.get(chiave) or {}
            row["frammento"] = hit.get("frammento") or None
            row["frammento_ruolo"] = hit.get("ruolo") or None
            out.append(row)
    out.sort(key=lambda r: (r["ts"] is None, -(r["ts"] or 0)))
    return out


def cerca_nel_testo(needle: str) -> dict:
    """Turni in cui il testo archiviato contiene `needle`, con il frammento.

    Chiave (sessione, turno), valore il pezzo di testo attorno alla parola: e'
    la differenza fra una ricerca che restringe un elenco e una che dice dove ha
    trovato. Dizionario vuoto se il testo non e' archiviato: non e' un errore,
    e' la configurazione predefinita.
    """
    needle = (needle or "").strip()
    # Sotto le tre lettere non si cerca: l'indice risponderebbe mezza storia, e
    # ogni tasto premuto costerebbe un'apertura dell'archivio per niente.
    if len(needle) < 3:
        return {}
    arch = apri_archivio_lettura()
    if arch is None:
        return {}
    try:
        trovati: dict = {}
        for r in arch.cerca(needle):
            if r.get("turno") is None:
                continue
            chiave = (r["session_id"], r["turno"])
            # Le righe arrivano ordinate per pertinenza: di un turno con piu'
            # messaggi che contengono la parola, il primo e' quello da mostrare.
            if chiave not in trovati:
                trovati[chiave] = {"frammento": r.get("frammento") or "",
                                   "ruolo": r.get("ruolo")}
        return trovati
    finally:
        arch.chiudi()


def h_byte(n: float) -> str:
    """Byte come si leggono, senza decimali inutili sui numeri piccoli."""
    for unita, soglia in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if n >= soglia:
            return f"{n / soglia:.1f} {unita}".replace(".0 ", " ")
    return f"{int(n)} byte"


def view_archivio() -> int:
    """Com'e' messo l'archivio: quanto pesa, da cosa, e cosa si puo' fare.

    E' la scheda di manutenzione: senza sapere cos'e' che occupa spazio, le
    uniche mosse possibili sono cancellare tutto o non toccare niente.
    """
    path = cm_archivio.db_path()
    if not os.path.isfile(path):
        info("nessun archivio: verra' creato alla prima scansione.")
        return 0
    try:
        with cm_archivio.Archivio(path, CACHE_FORMAT, sola_lettura=True) as arch:
            c, p = arch.conta(), arch.peso()
    except Exception as exc:
        warn(f"archivio non apribile: {exc}")
        return 2

    print()
    print(C.w(f"  Archivio  {path}", C.BOLD))
    print()
    print(f"  {h_byte(p['file']):>10}  in tutto")
    for nome, byte in sorted(p["parti"].items(), key=lambda kv: -kv[1]):
        quota = 100 * byte / p["file"] if p["file"] else 0
        print(f"  {h_byte(byte):>10}  {nome:<20} {quota:4.0f}%")
    print(C.w("  Le voci si contano sui dati e non sulle pagine: la loro somma "
              "sta sotto il totale,\n  che comprende indici e spazio libero.", C.DIM))
    if p.get("chiaro"):
        print(C.w(f"  La cache di analisi e' compressa: in chiaro sarebbe "
                  f"{h_byte(p['chiaro'])}, di cui {h_byte(p['timestamp'])} di soli\n"
                  "  timestamp — che restano tutti, ed e' quello che permette di "
                  "cambiare --idle-gap\n  senza rileggere i transcript.", C.DIM))
    print()
    print(f"  {c['sessioni']} sessioni, {c['turni']} turni, "
          f"{c['file']} transcript in cache"
          + (f", {c['messaggi']} messaggi di testo" if c["messaggi"] else "")
          + ".")
    if c["acquisite"]:
        print(C.w(f"  {c['acquisite']} sessioni non hanno piu' il transcript: "
                  "sono l'unica copia rimasta di quel lavoro.", C.DIM))
    print()
    print(C.w("  --clear-cache      svuota la cache di analisi: i transcript "
              "vengono riletti, le sessioni restano", C.DIM))
    print(C.w("  --dimentica-testo  cancella il testo delle conversazioni e "
              "compatta il file; i numeri restano", C.DIM))
    return 0


def print_frammenti(righe: list[dict], needle: str, quanti: int = 12) -> None:
    """Dove compare la parola cercata, quando non e' nel prompt.

    Il prompt sta gia' nella tabella: qui finisce solo quello che la tabella non
    fa vedere, cioe' il testo delle risposte. Senza, cercare una parola dice
    quanti turni la contengono ma non dove — che e' meta' risposta.
    """
    con_testo = [r for r in righe if r.get("frammento")]
    if not (needle and con_testo):
        return
    ap, ch = ("«", "»") if UNI else ('"', '"')
    print()
    print(C.w(f"  Dove compare {ap}{needle}{ch}"
              f"  ({len(con_testo)} nel testo archiviato)", C.BOLD))
    for r in con_testo[:quanti]:
        # «tu» / «risposta» e non «claude»: il testo archiviato arriva anche da
        # sorgenti che Claude non l'hanno mai visto.
        chi = "tu" if r.get("frammento_ruolo") == "tu" else "risposta"
        frammento = r["frammento"]
        if not UNI:
            frammento = frammento.replace("«", "[").replace("»", "]").replace("…", "...")
        turno = f"#{r.get('n')}"
        print("    " + C.w(h_time(r["ts"]), C.DIM)
              + f"  {(r['session_id'] or '')[:8]} {turno:<5}"
              + C.w(f"{chi:<9}", C.DIM) + trunc(frammento, 92))
    if len(con_testo) > quanti:
        print(C.w(f"    {'…' if UNI else '...'} e altri {len(con_testo) - quanti}", C.DIM))


def view_traces(sessions: list[dict], pricing: dict, args) -> None:
    needle = getattr(args, "search", "") or ""
    rows_all = flatten_traces(sessions, needle, anche=cerca_nel_testo(needle))
    if not rows_all:
        print()
        info("nessun turno da mostrare" + (" con questo filtro" if getattr(args, "search", "") else ""))
        return
    shown = rows_all[: args.top] if args.top else rows_all

    print()
    print(C.w(f"  Turni  ({len(shown)} di {len(rows_all)})", C.BOLD))
    rows = []
    styles = {}
    for tr in shown:
        models = sorted(tr["per_model"], key=lambda m: -tr["per_model"][m]["cost"])
        label = models[0] if len(models) == 1 else (
            (models[0] + f" +{len(models) - 1}") if models else "-")
        prompt = tr["prompt"] or "(prima del primo prompt)"
        if tr["interrupted"]:
            prompt = ("⨯ " if UNI else "! ") + prompt
        if tr.get("archiviata"):
            prompt = ("▪ " if UNI else "= ") + prompt
        rows.append([
            h_time(tr["ts"]),
            trunc(tr["project"] or "?", 18),
            (tr["session_id"] or "")[:8],
            h_dur(tr["duration"]),
            tr["requests"], tr["tools"],
            h_pct(tr["cache_hit"]),
            # Una sorgente che non dichiara i token lascia il dizionario vuoto:
            # zero e' la somma giusta, e la colonna del costo dice gia' «—».
            tok_txt(tr, sum(tr["tokens"].get(k, 0) for k in
                            ("input", "output", "cache_read", "cache_w5m", "cache_w1h"))),
            costo_txt(tr),
            trunc(label, 20),
            trunc(prompt, 52),
        ])
        if tr["interrupted"]:
            styles[len(rows) - 1] = (C.DIM,)
    print_table(
        ["INIZIO", "PROGETTO", "SESSIONE", "DURATA", "REQ", "TOOL", "CACHE",
         "TOKEN", "COSTO", "MODELLO", "PROMPT"],
        rows, ["<", "<", "<", ">", ">", ">", ">", ">", ">", "<", "<"], styles)
    print_frammenti(shown, needle)
    print()
    st = trace_stats(sessions)
    print(C.w(f"  {st['traces']} turni in {len(sessions)} sessioni  {BULLET}  "
              f"{st['spans']} span  {BULLET}  durata mediana {h_dur(st['median'])}"
              f"  {BULLET}  cache hit {h_pct(st['cache_hit'])}", C.DIM))
    if st["interrupted"]:
        print(C.w(f"  {'⨯' if UNI else '!'} = turno interrotto dall'utente "
                  f"({st['interrupted']} in tutto): la risposta in corso non andava bene.",
                  C.DIM))
    print_fonti_note(sessions)
    if any(t.get("archiviata") for t in shown):
        print(C.w(f"  {'▪' if UNI else '='} = dall'archivio: il transcript non c'e' piu'.",
                  C.DIM))
    print_cost_legend(pricing, sessions)


def sessione_archiviata(session_id: str) -> dict:
    """La sessione come l'ha lasciata l'archivio, o {} se non c'e'.

    Accetta anche un prefisso, come tutto il resto del tool: chi legge un
    identificativo a schermo ne copia i primi otto caratteri.
    """
    arch = apri_archivio_lettura()
    if arch is None:
        return {}
    try:
        riga = arch.con.execute(
            "SELECT session_id FROM sessione WHERE session_id=? OR session_id LIKE ?"
            " ORDER BY LENGTH(session_id) LIMIT 1",
            (session_id, session_id + "%")).fetchone()
        if not riga:
            return {}
        cur = arch.con.execute("SELECT * FROM sessione WHERE session_id=?", (riga[0],))
        nomi = [d[0] for d in cur.description]
        return arch._a_sessione(dict(zip(nomi, cur.fetchone())))
    except Exception:
        return {}
    finally:
        arch.chiudi()


def conversazione_archiviata(session_id: str) -> tuple[dict, list[dict]]:
    """Sessione e messaggi presi dall'archivio, quando il transcript non c'e' piu'.

    E' il motivo per cui si archivia il testo: una conversazione cancellata da
    `cleanupPeriodDays` resta leggibile. Quello che non torna sono i risultati
    degli strumenti, che non vengono archiviati apposta — restano le domande e
    le risposte, che e' la parte che si rilegge.
    """
    sess = sessione_archiviata(session_id)
    if not sess:
        return {}, []
    arch = apri_archivio_lettura()
    if arch is None:
        return sess, []
    try:
        return sess, normalizza_messaggi(arch.messaggi_di(sess["session_id"]))
    finally:
        arch.chiudi()


def normalizza_messaggi(messaggi: list[dict]) -> list[dict]:
    """Dai messaggi dell'archivio a quelli che si aspetta chi li mostra.

    Del conteggio dei token, per un messaggio riletto dall'archivio, non resta
    niente: sta nei totali del turno, non riga per riga. Zeri espliciti invece
    di campi assenti, cosi' chi disegna una tabella non deve sapere da dove
    arriva quello che sta disegnando.
    """
    for m in messaggi:
        if not m.get("tok"):
            m["tok"] = new_tok()
    return messaggi


def fmt_stat(valore, formato: str) -> str:
    """Un numero degli andamenti, scritto come si legge."""
    if valore is None:
        return "—" if UNI else "-"
    if formato == "usd":
        return h_cost(valore)
    if formato == "dur":
        return h_dur(valore)
    if formato == "pct":
        return f"{100 * valore:.1f}%"
    if formato == "num1":
        return f"{valore:.1f}"
    return f"{valore:,.0f}".replace(",", ".")


_SPARK = "▁▂▃▄▅▆▇█"


def sparkline(valori, larghezza: int = 0) -> str:
    """Una riga di blocchi proporzionali. Vuota se non c'e' niente da mostrare."""
    xs = [v or 0 for v in valori]
    if not UNI or not xs or max(xs) <= 0:
        return ""
    if larghezza and len(xs) > larghezza:   # tiene la coda: il recente conta
        xs = xs[-larghezza:]
    top = max(xs)
    return "".join(_SPARK[min(len(_SPARK) - 1, int(v / top * (len(_SPARK) - 1)))]
                   for v in xs)


def view_trend(sessions: list[dict], pricing: dict, args) -> None:
    turni = flatten_traces(sessions)
    if not turni:
        print()
        info("nessun turno da cui ricavare un andamento")
        return
    iv = cm_stat.intervallo(turni)
    grana = getattr(args, "grana", None) or cm_stat.grana_consigliata(*iv)
    punti = cm_stat.serie(turni, grana)

    nome = dict((g[0], g[1]) for g in cm_stat.GRANULARITA)[grana]
    print()
    print(C.w(f"  Andamento {BULLET} per {nome.lower()} {BULLET} "
              f"dal {iv[0].strftime('%d/%m/%Y')} al {iv[1].strftime('%d/%m/%Y')}",
              C.BOLD))

    mostrati = punti[-args.top:] if args.top else punti
    rows = []
    for b in mostrati:
        rows.append([
            b["etichetta"],
            h_cost(b["costo"]),
            b["turni"],
            h_dur(b["durata_totale"]),
            fmt_stat(b["costo_turno"], "usd"),
            fmt_stat(b["durata_mediana"], "dur"),
            fmt_stat(b["cache_hit"], "pct"),
            b["sessioni"],
            b["progetti"],
            b["interrotti"] or "",
        ])
    print_table(["PERIODO", "VALORE", "TURNI", "TEMPO", "PER TURNO", "MEDIANA",
                 "CACHE", "SESS", "PROG", "INTER"],
                rows, ["<", ">", ">", ">", ">", ">", ">", ">", ">", ">"])

    linea = sparkline([b["costo"] for b in mostrati])
    if linea:
        print()
        print(C.w(f"  valore consumato  {linea}", C.DIM))
        print(C.w(f"  turni             {sparkline([b['turni'] for b in mostrati])}",
                  C.DIM))

    # Confronto: la finestra piu' recente contro quella di pari lunghezza prima.
    giorni = int(getattr(args, "finestra", 0) or 0)
    if not giorni:
        giorni = {"giorno": 7, "settimana": 28, "mese": 90}[grana]
    fine = iv[1] + dt.timedelta(days=1)
    inizio = fine - dt.timedelta(days=giorni)
    t0 = dt.datetime.combine(inizio, dt.time.min).timestamp()
    recenti = [t for t in turni if t.get("ts") and t["ts"] >= t0]
    prec = cm_stat.finestra_precedente(turni, inizio, fine)

    print()
    print(C.w(f"  Indicatori {BULLET} ultimi {giorni} giorni "
              f"contro i {giorni} precedenti", C.BOLD))
    righe = []
    stili = {}
    for k in cm_stat.indicatori(recenti, prec, turni):
        d = k["delta"]
        if d is None:
            var = "—" if UNI else "-"
        else:
            var = f"{100 * d:+.0f}%"
        righe.append([k["label"], fmt_stat(k["valore"], k["formato"]),
                      fmt_stat(k["precedente"], k["formato"]), var])
        if d and k["verso"]:
            bene = (d > 0) == (k["verso"] == "su")
            stili[len(righe) - 1] = (C.GREEN,) if bene else (C.YELLOW,)
    print_table(["INDICATORE", "ORA", "PRIMA", "VAR"],
                righe, ["<", ">", ">", ">"], stili)
    print()
    print(C.w("  Il colore c'è solo dove salire vuol dire qualcosa: "
              "«costo per turno» che sale non è né buono né cattivo.", C.DIM))
    print_cost_legend(pricing, sessions)


def load_conversation(base: str, session_id: str, pricing: dict,
                      idle_gap: float = 300.0) -> tuple[dict, list[dict]]:
    """Sessione + messaggi in ordine, con il testo di quello che è stato detto."""
    sid, files = find_session_files(base, session_id)
    if not files:
        return conversazione_archiviata(session_id)
    sess = new_session(sid)
    messages = []
    for path in files:
        rec = scan_file(path, pricing, keep_messages=True)
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = 0.0
        merge_record(sess, rec, mtime)
        for m in rec.get("messages", []):
            m["subagent"] = rec["is_subagent"]
            messages.append(m)
    finalize(sess, pricing, idle_gap)
    messages.sort(key=lambda m: (m["ts"] is None, m["ts"] or 0))
    return sess, messages


def build_spans(trace: dict, window: tuple[float | None, float | None],
                messages: list[dict], tool_uses: list[dict],
                tool_res: dict[str, dict], pricing: dict) -> list[dict]:
    """L'albero degli span di un turno: la radice, le richieste, gli strumenti.

    La durata di una richiesta non e' scritta da nessuna parte nel transcript:
    c'e' solo l'istante in cui la risposta e' stata registrata. Ma l'istante in
    cui e' partita e' l'ultimo evento precedente — il prompt, oppure il
    risultato dello strumento che l'ha fatta ripartire. La differenza fra i due
    e' l'attesa vera, ed e' la stessa lettura che fa ProxyAgent.

    Gli strumenti invece hanno un inizio e una fine espliciti: la riga che li
    invoca e quella che ne riporta il risultato.
    """
    lo, hi = window

    def inside(ts):
        if ts is None:
            return False
        if lo is not None and ts < lo:
            return False
        return hi is None or ts < hi

    msgs = sorted((m for m in messages
                   if m.get("kind") == "assistant" and inside(m.get("ts"))),
                  key=lambda m: m["ts"])
    uses = sorted((u for u in tool_uses if inside(u.get("ts"))),
                  key=lambda u: u["ts"])

    # Confini da cui una richiesta puo' essere partita: il prompt del turno e
    # ogni risultato di strumento tornato prima di lei.
    edges = sorted(
        [t for t in [trace.get("ts")] if t is not None]
        + [r["ts"] for r in tool_res.values() if inside(r.get("ts"))]
    )

    spans = [{
        "name": "interaction", "type": "interaction", "depth": 0,
        "start": trace.get("ts"), "end": trace.get("end"),
        "model": None, "cost": trace.get("cost", 0.0), "ok": not trace.get("interrupted"),
        "detail": trace.get("prompt") or "(prima del primo prompt)",
    }]

    by_req: dict[str, int] = {}
    for m in msgs:
        i = bisect.bisect_left(edges, m["ts"]) - 1
        start = edges[i] if i >= 0 else m["ts"]
        cost, _ = cost_of(m["model"], m["tok"], pricing)
        spans.append({
            "name": "llm_request", "type": "llm", "depth": 1,
            "start": start, "end": m["ts"],
            "model": m["model"], "cost": cost, "ok": True,
            "subagent": bool(m.get("subagent")),
            "tokens": m["tok"],
            "detail": (m.get("text") or "").strip(),
        })
        if m.get("req"):
            by_req.setdefault(m["req"], len(spans) - 1)

    for u in uses:
        res = tool_res.get(u["id"]) or {}
        end = res.get("ts") or u["ts"]
        spans.append({
            "name": "tool:" + u["name"], "type": "tool",
            "depth": 2 if u.get("req") in by_req else 1,
            "start": u["ts"], "end": end,
            "model": None, "cost": 0.0, "ok": not res.get("error"),
            "parent": by_req.get(u.get("req")),
            "args": u.get("args") or "",
            "detail": (res.get("text") or "").strip(),
        })

    # In ordine di inizio, ma ogni strumento resta attaccato alla richiesta che
    # l'ha invocato: e' quello che rende leggibile la cascata.
    root = spans[0]
    rest = spans[1:]
    llm = [s for s in rest if s["type"] == "llm"]
    tools_by_parent: dict[int | None, list] = {}
    for s in rest:
        if s["type"] == "tool":
            tools_by_parent.setdefault(s.get("parent"), []).append(s)
    out = [root]
    out.extend(tools_by_parent.get(None, []))
    for idx, s in enumerate(llm, 1):
        out.append(s)
        out.extend(tools_by_parent.get(idx, []))
    for s in out:
        s["duration"] = ((s["end"] - s["start"])
                         if (s.get("start") is not None and s.get("end") is not None)
                         else None)
    return out


def load_trace(base: str, session_id: str, n: int, pricing: dict,
               idle_gap: float = 300.0) -> tuple[dict, dict, list[dict], list[dict]]:
    """Un singolo turno letto a fondo: sessione, trace, span e messaggi.

    Rilegge i transcript della sessione — e solo quelli — perche' gli span
    vivono in un dettaglio che la scansione normale butta via apposta.
    """
    sid, files = find_session_files(base, session_id)
    if not files:
        # Senza transcript gli span non si ricostruiscono — vivono nel
        # dettaglio che non si archivia. Restano i numeri del turno e, se il
        # testo e' archiviato, quello che ci si e' detti.
        sess = sessione_archiviata(session_id)
        if not sess:
            return {}, {}, [], []
        trace = next((t for t in sess.get("traces") or [] if t["n"] == n), {})
        arch = apri_archivio_lettura()
        msgs = []
        if arch is not None:
            try:
                msgs = normalizza_messaggi(arch.messaggi_di(sess["session_id"], turno=n))
            finally:
                arch.chiudi()
        return sess, trace, [], msgs
    sess = new_session(sid)
    messages, tool_uses, tool_res = [], [], {}
    for path in files:
        rec = scan_file(path, pricing, keep_messages=True)
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = 0.0
        merge_record(sess, rec, mtime)
        for m in rec.get("messages", []):
            m["subagent"] = rec["is_subagent"]
            messages.append(m)
        tool_uses.extend(rec.get("tool_uses") or [])
        tool_res.update(rec.get("tool_res") or {})
    finalize(sess, pricing, idle_gap)

    traces = sess.get("traces") or []
    trace = next((t for t in traces if t["n"] == n), None)
    if trace is None:
        return sess, {}, [], []
    # La finestra arriva fino al turno successivo, non alla fine dell'ultima
    # risposta: quello che succede in mezzo appartiene comunque a questo turno.
    later = [t["ts"] for t in traces if t["n"] > n and t["ts"] is not None]
    window = (trace["ts"], min(later) if later else None)
    spans = build_spans(trace, window, messages, tool_uses, tool_res, pricing)

    lo, hi = window
    turn_msgs = [
        m for m in messages
        if m.get("ts") is not None
        and (lo is None or m["ts"] >= lo)
        and (hi is None or m["ts"] < hi)
    ]
    turn_msgs.sort(key=lambda m: m["ts"])
    return sess, trace, spans, turn_msgs


def conversation_markdown(sess: dict, messages: list[dict], pricing: dict,
                          include_subagents: bool = False) -> str:
    """La conversazione in Markdown: titolo, quando, e il percorso fatto.

    I messaggi dei subagent sono esclusi per default: sono lavoro interno e
    renderebbero illeggibile il filo del discorso.
    """
    title = sess.get("title") or sess.get("first_prompt") or "(senza titolo)"
    out = [f"# {title}", ""]
    meta = [
        f"- **Progetto**: {sess.get('project') or '?'}  ",
        f"- **Cartella**: `{sess.get('cwd') or '?'}`  ",
        f"- **Quando**: {h_time(sess.get('start'), '%d/%m/%Y %H:%M')} → "
        f"{h_time(sess.get('end'), '%d/%m/%Y %H:%M')}  ",
        f"- **Durata**: {h_dur(sess.get('duration'))} "
        f"({h_dur(sess.get('active'))} di lavoro effettivo)  ",
        f"- **Messaggi**: {sess.get('user_prompts')} tuoi / "
        f"{sess.get('assistant_msgs')} di Claude, {sess.get('tool_calls')} tool  ",
        f"- **Sessione**: `{sess.get('session_id')}`  ",
    ]
    if sess.get("git_branch"):
        meta.append(f"- **Branch**: {sess['git_branch']}  ")
    out += meta + ["", "---", ""]

    pending_tools: list[str] = []

    def flush_tools():
        if pending_tools:
            uniq = list(dict.fromkeys(pending_tools))
            out.append(f"<sub>⚙ {', '.join(uniq)}</sub>")
            out.append("")
            pending_tools.clear()

    for m in messages:
        if m.get("subagent") and not include_subagents:
            continue
        when = h_time(m.get("ts"), "%d/%m %H:%M")
        if m["kind"] == "prompt":
            flush_tools()
            out.append(f"### {ARROW} Tu · {when}")
            out.append("")
            out.append(m.get("text") or "")
            out.append("")
            continue
        text = (m.get("text") or "").strip()
        if text:
            flush_tools()
            out.append(f"**Claude** · {when}")
            out.append("")
            out.append(text)
            out.append("")
        else:
            pending_tools.extend(m.get("tools") or [])
    flush_tools()
    return "\n".join(out).rstrip() + "\n"


_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def conversation_filename(sess: dict) -> str:
    """Nome file leggibile e ordinabile: data, titolo, prefisso della sessione."""
    when = h_time(sess.get("start"), "%Y-%m-%d") if sess.get("start") else "senza-data"
    title = (sess.get("title") or sess.get("first_prompt") or "senza titolo").strip()
    title = _UNSAFE.sub("-", title)
    title = " ".join(title.split())[:70].rstrip(" .")
    return f"{when} {title} [{(sess.get('session_id') or '')[:8]}].md"


def export_turni_jsonl(righe: list[dict], path: str) -> dict:
    """I turni selezionati, uno per riga, in JSONL.

    **A cosa serve** — la domanda che questo export si portava dietro da mesi.
    Un turno e' gia' una coppia domanda/risposta con accanto il costo, la
    durata, gli strumenti usati e se e' stato interrotto: e' il formato in cui
    si valuta un prompt o si confronta un modello. Il criterio di selezione non
    e' una nuova invenzione, e' **il filtro che si ha davanti** — cercare
    «riconciliazione», o restringere a una sessione, e poi esportare quello.

    Le risposte vengono dall'archivio del testo. Senza (`archivio.testo`
    spento) escono `null`, e il conto dei turni senza risposta lo dice: un
    dataset di valutazione con meta' delle risposte vuote e' peggio di nessun
    dataset, e non deve sembrare completo.
    """
    arch = apri_archivio_lettura()
    testi: dict[tuple, list] = {}
    if arch is not None:
        try:
            for sid in {r.get("session_id") for r in righe if r.get("session_id")}:
                for m in arch.messaggi_di(sid):
                    testi.setdefault((sid, m.get("turno")), []).append(m)
        finally:
            arch.chiudi()

    scritti = senza_risposta = 0
    with open(path, "w", encoding="utf-8") as fh:
        for r in righe:
            chiave = (r.get("session_id"), r.get("n"))
            msg = sorted(testi.get(chiave) or [],
                         key=lambda m: (m.get("ts") is None, m.get("ts") or 0))
            domande = [m["text"] for m in msg if m.get("kind") == "prompt"]
            risposte = [m["text"] for m in msg if m.get("kind") == "assistant"]
            if not risposte:
                senza_risposta += 1
            modelli = sorted(r.get("per_model") or {},
                             key=lambda m: -(r["per_model"][m].get("cost") or 0))
            fh.write(json.dumps({
                "sessione": r.get("session_id"),
                "turno": r.get("n"),
                "quando": (dt.datetime.fromtimestamp(r["ts"], dt.timezone.utc)
                           .isoformat() if r.get("ts") else None),
                "progetto": r.get("project"),
                "fonte": fonte_di(r),
                "modelli": modelli,
                "durata_s": r.get("duration"),
                "richieste": r.get("requests"),
                "strumenti": list(r.get("tool_names") or []),
                "n_strumenti": r.get("tools"),
                "interrotto": bool(r.get("interrupted")),
                # Le sorgenti che i token non li dichiarano scrivono null, non
                # zero: un dataset con degli zeri finti si allena sugli zeri.
                "costo_usd": None if r.get("costo_noto") is False else r.get("cost"),
                "token": None if r.get("costo_noto") is False else r.get("tokens"),
                "cache_hit": r.get("cache_hit"),
                "domanda": "\n\n".join(domande) or r.get("prompt"),
                "risposta": "\n\n".join(risposte) or None,
            }, ensure_ascii=False) + "\n")
            scritti += 1
    return {"turni": scritti, "senza_risposta": senza_risposta,
            "testo_archiviato": bool(testi)}


def export_conversations(sessions: list[dict], base: str, pricing: dict,
                         out_dir: str, idle_gap: float = 300.0,
                         include_subagents: bool = False,
                         on_progress=None) -> dict:
    """Scrive una cartella con un indice e una conversazione per file.

    Le sessioni vengono rilette una a una dai transcript, e da `cm-local.db`
    quelle il cui transcript non c'e' piu' — sempre che il testo fosse
    archiviato, altrimenti finiscono fra quelle non leggibili. Ritorna un
    riepilogo di quello che ha scritto.
    """
    os.makedirs(out_dir, exist_ok=True)
    by_project: dict[str, list[tuple[dict, str]]] = {}
    written, failed = 0, []
    total = len(sessions)

    for i, s in enumerate(sessions, 1):
        if on_progress is not None:
            on_progress(i, total, s.get("project") or "?")
        try:
            full, messages = load_conversation(base, s["session_id"], pricing, idle_gap)
            if not full:
                failed.append(s["session_id"])
                continue
            project = full.get("project") or "senza-progetto"
            folder = os.path.join(out_dir, _UNSAFE.sub("-", project))
            os.makedirs(folder, exist_ok=True)
            name = conversation_filename(full)
            with open(os.path.join(folder, name), "w", encoding="utf-8") as fh:
                fh.write(conversation_markdown(full, messages, pricing, include_subagents))
            by_project.setdefault(project, []).append((full, name))
            written += 1
        except Exception:
            failed.append(s.get("session_id", "?"))

    index = os.path.join(out_dir, "indice.md")
    with open(index, "w", encoding="utf-8") as fh:
        fh.write(_index_markdown(by_project, pricing))
    return {"written": written, "failed": failed, "index": index,
            "projects": len(by_project)}


def _index_markdown(by_project: dict, pricing: dict) -> str:
    """Indice delle conversazioni esportate, un blocco per progetto."""
    sub = subscription_of(pricing)
    out = ["# Conversazioni Claude Code", "",
           f"Esportate il {dt.datetime.now():%d/%m/%Y %H:%M}.", ""]
    tot_cost = sum(s["cost"] for sess in by_project.values() for s, _ in sess)
    tot_n = sum(len(v) for v in by_project.values())
    out.append(f"{tot_n} conversazioni in {len(by_project)} progetti"
               f"  {BULLET}  {h_cost(tot_cost)} a listino API"
               + (f", su un abbonamento da {money(float(sub['monthly_cost']), display_currency(pricing))}/mese"
                  if sub else ""))
    out += ["", "---", ""]

    for project in sorted(by_project, key=lambda p: -sum(s["cost"] for s, _ in by_project[p])):
        rows = sorted(by_project[project], key=lambda r: r[0].get("start") or 0, reverse=True)
        costo = sum(s["cost"] for s, _ in rows)
        attivo = sum(s["active"] for s, _ in rows)
        out.append(f"## {project}")
        out.append("")
        out.append(f"{len(rows)} conversazioni  {BULLET}  {h_cost(costo)}  "
                   f"{BULLET}  {h_dur(attivo)} di lavoro effettivo")
        out.append("")
        out.append("| Quando | Conversazione | Messaggi | Attivo | Se fosse API |")
        out.append("|---|---|---:|---:|---:|")
        for s, name in rows:
            titolo = (s.get("title") or s.get("first_prompt") or "(senza titolo)")
            titolo = " ".join(titolo.split())[:80].replace("|", "\\|")
            link = f"{_UNSAFE.sub('-', project)}/{name}".replace(" ", "%20")
            out.append(f"| {h_time(s.get('start'), '%d/%m/%Y %H:%M')} "
                       f"| [{titolo}]({link}) "
                       f"| {s.get('user_prompts')}/{s.get('assistant_msgs')} "
                       f"| {h_dur(s.get('active'))} "
                       f"| {h_cost(s.get('cost', 0))} |")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def find_session_files(base: str, needle: str) -> tuple[str | None, list[str]]:
    """Trova i file (principale + subagent) di una sessione, per uuid o prefisso."""
    hits: dict[str, list[str]] = {}
    for path in glob.glob(os.path.join(base, "**", "*.jsonl"), recursive=True):
        sid = session_id_from_path(path)
        if sid.lower().startswith(needle.lower()):
            hits.setdefault(sid, []).append(path)
    if not hits:
        return None, []
    if len(hits) > 1:
        warn(f"prefisso ambiguo: {', '.join(sorted(hits))} — uso il più recente")
    sid = max(hits, key=lambda k: max(os.path.getmtime(p) for p in hits[k]))
    return sid, sorted(hits[sid])


def view_detail(base: str, pricing: dict, args) -> None:
    sid, files = find_session_files(base, args.session)
    if not files:
        warn(f"Sessione '{args.session}' non trovata in {base}")
        return

    sess = new_session(sid)
    messages = []
    for path in files:
        rec = scan_file(path, pricing, keep_messages=True)
        merge_record(sess, rec, os.path.getmtime(path))
        for m in rec.get("messages", []):
            m["subagent"] = rec["is_subagent"]
            messages.append(m)
    finalize(sess, pricing, args.idle_gap)
    messages.sort(key=lambda m: (m["ts"] is None, m["ts"] or 0))

    print()
    print(C.w(f"  Sessione {sid}", C.BOLD))
    if sess["title"]:
        print(f"  {C.w('titolo', C.DIM)}    {sess['title']}")
    print(f"  {C.w('progetto', C.DIM)}  {sess['project']}  {C.w(sess['cwd'] or '', C.DIM)}")
    print(f"  {C.w('inizio', C.DIM)}    {h_time(sess['start'], '%d/%m/%Y %H:%M:%S')}"
          f"   {C.w('fine', C.DIM)} {h_time(sess['end'], '%d/%m/%Y %H:%M:%S')} ({h_ago(sess['end'])})")
    print(f"  {C.w('durata', C.DIM)}    {h_dur(sess['duration'])}"
          f"   {C.w('attivo', C.DIM)} {h_dur(sess['active'])}"
          f"   {C.w('branch', C.DIM)} {sess['git_branch'] or '-'}"
          f"   {C.w('cc', C.DIM)} v{sess['version'] or '?'}")
    print(f"  {C.w('messaggi', C.DIM)}  {sess['user_prompts']} utente / {sess['assistant_msgs']} assistant"
          f"   {C.w('tool', C.DIM)} {sess['tool_calls']}"
          f"   {C.w('subagent', C.DIM)} {sess['subagent_files']} file, {sess['subagent_prompts']} task"
          f"   {C.w('errori API', C.DIM)} {sess['api_errors']}")
    print(f"  {C.w('COSTO', C.BOLD)}     {C.w(h_cost(sess['cost']), C.GREEN, C.BOLD)}"
          f"   {C.w(plan_note(pricing), C.DIM)}")
    print()

    rows = []
    styles = {}
    running = 0.0
    for m in messages:
        if m["kind"] == "prompt":
            rows.append([h_time(m["ts"], "%H:%M:%S"), ARROW + " utente", "", "", "", "", "", "",
                         trunc(m["text"], 46)])
            styles[len(rows) - 1] = (C.CYAN,)
            continue
        cost, _ = cost_of(m["model"], m["tok"], pricing)
        running += cost
        t = m["tok"]
        tools = ",".join(dict.fromkeys(m["tools"]))
        rows.append([
            h_time(m["ts"], "%H:%M:%S"),
            trunc(m["model"], 20) + ("*" if m["subagent"] else ""),
            h_tokens(t["input"]), h_tokens(t["output"]),
            h_tokens(t["cache_w5m"] + t["cache_w1h"]), h_tokens(t["cache_read"]),
            h_cost(cost), h_cost(running),
            trunc(tools, 46),
        ])
    if args.top and len(rows) > args.top:
        cut = len(rows) - args.top
        print(C.w(f"  (mostro gli ultimi {args.top} di {len(rows)} turni "
                  f"{BULLET} usa --top 0 per vederli tutti)", C.DIM))
        print()
        rows = rows[cut:]
        styles = {k - cut: v for k, v in styles.items() if k >= cut}
    print_table(["ORA", "MODELLO", "IN", "OUT", "CACHE W", "CACHE R", "COSTO", "CUMUL", "TOOL / TESTO"],
                rows, ["<", "<", ">", ">", ">", ">", ">", ">", "<"], styles)
    if sess["subagent_files"]:
        print()
        print(C.w(f"  * = messaggio di un subagent  {BULLET}  "
                  + ", ".join(f"{k}×{v}" for k, v in sorted(sess["agents"].items())), C.DIM))
    print()
    print_model_breakdown([sess], pricing)
    report_unknown([sess])


def view_watch(base: str, pricing: dict, args) -> None:
    print(C.w(f"  claude-monitor {BULLET} avvio, analisi del transcript…", C.DIM))
    prev_cost = None
    prev_sid = None
    live: dict[str, LiveFile] = {}
    try:
        while True:
            target = None
            newest = -1.0
            for path in glob.glob(os.path.join(base, "**", "*.jsonl"), recursive=True):
                if args.project and not path_matches_project(path, args.project):
                    continue
                try:
                    mt = os.path.getmtime(path)
                except OSError:
                    continue
                if mt > newest:
                    newest, target = mt, path
            if target is None:
                warn("nessun transcript trovato")
                return

            sid = session_id_from_path(target)
            _, files = find_session_files(base, sid)

            if sid != prev_sid:
                prev_cost = None
                prev_sid = sid
                live = {}

            sess = new_session(sid)
            for path in files:
                lf = live.get(path)
                if lf is None:
                    lf = live[path] = LiveFile(path, pricing)
                lf.update()  # legge solo i byte nuovi
                try:
                    mtime = os.path.getmtime(path)
                except OSError:
                    mtime = 0.0
                merge_record(sess, lf.snapshot(), mtime)
            finalize(sess, pricing, args.idle_gap)

            os.system("cls" if os.name == "nt" else "clear")
            t = sess["tokens"]
            delta = "" if prev_cost is None else f"  (+{h_cost(max(0.0, sess['cost'] - prev_cost))})"
            elapsed = time.time() - (sess["start"] or time.time())

            print(C.w(f"  claude-monitor {BULLET} LIVE   {dt.datetime.now():%H:%M:%S}   "
                      f"agg. ogni {args.interval}s   Ctrl-C per uscire", C.BOLD))
            print(C.w(f"  {HR * 78}", C.DIM))
            print(f"  {C.w('progetto', C.DIM)}  {C.w(sess['project'] or '?', C.BOLD)}"
                  f"   {C.w('sessione', C.DIM)} {sid[:8]}"
                  f"   {C.w('branch', C.DIM)} {sess['git_branch'] or '-'}")
            if sess["title"]:
                print(f"  {C.w('titolo', C.DIM)}    {trunc(sess['title'], 70)}")
            elif sess["first_prompt"]:
                print(f"  {C.w('primo msg', C.DIM)} {trunc(sess['first_prompt'], 70)}")
            print()
            print(f"  {C.w('trascorso', C.DIM)}   {C.w(h_dur(elapsed), C.BOLD):>12}"
                  f"      {C.w('attivo', C.DIM)} {h_dur(sess['active'])}"
                  f"      {C.w('ultima attività', C.DIM)} {h_ago(sess['end'])}")
            print(f"  {C.w('messaggi', C.DIM)}    {C.w(str(sess['user_prompts']) + ' utente / ' + str(sess['assistant_msgs']) + ' assistant', C.BOLD)}"
                  f"      {C.w('tool', C.DIM)} {sess['tool_calls']}"
                  f"      {C.w('subagent', C.DIM)} {sess['subagent_files']}")
            print(f"  {C.w('token', C.DIM)}       in {h_tokens(t['input'])}"
                  f"  out {h_tokens(t['output'])}"
                  f"  cache-w {h_tokens(t['cache_w5m'] + t['cache_w1h'])}"
                  f"  cache-r {h_tokens(t['cache_read'])}")
            print()
            print(f"  {C.w('COSTO', C.BOLD)}       {C.w(h_cost(sess['cost']), C.GREEN, C.BOLD)}"
                  f"{C.w(delta, C.GREEN)}")
            print(C.w(f"  {plan_note(pricing)}", C.DIM))
            if sess["per_model"]:
                print()
                for model, data in sorted(sess["per_model"].items(), key=lambda kv: -kv[1]["cost"]):
                    bar_units = int(30 * data["cost"] / sess["cost"]) if sess["cost"] else 0
                    bar = ("█" if UNI else "#") * bar_units
                    print(f"   {trunc(model, 26):<26} {h_cost(data['cost']):>10}  {C.w(bar, C.BLUE)}")
            if sess["bad_lines"]:
                print()
                print(C.w(f"   {sess['bad_lines']} riga/e non parsabili (risposta in streaming in corso)", C.DIM))

            prev_cost = sess["cost"]
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n" + C.w("  interrotto.", C.DIM))


def build_json_payload(sessions: list[dict], pricing: dict, top: int = 0,
                       with_traces: bool = False) -> dict:
    """Payload machine-readable delle sessioni. Puro: non stampa nulla.

    I turni si includono solo su richiesta: sono una decina di volte piu'
    numerosi delle sessioni e chi vuole i totali non vuole pagarli.
    """
    shown = sessions[:top] if top else sessions
    st = trace_stats(shown)
    payload = {
        "generated_at": dt.datetime.now().astimezone().isoformat(),
        "tool_version": __version__,
        "pricing": {
            "updated": pricing.get("updated"),
            "billing_mode": (billing_of(pricing).get("mode") or "subscription"),
            "notional": (billing_of(pricing).get("mode") or "subscription").lower()
                        != "api",
            "subscription": subscription_of(pricing),
            "display_currency": display_currency(pricing),
        },
        "totals": {
            "sessions": len(shown),
            "cost_usd": round(sum(s["cost"] for s in shown), 6),
            "real_cost": round(sum(s.get("real_cost", 0.0) for s in shown), 6),
            "duration_s": round(sum(s["duration"] for s in shown), 1),
            "active_s": round(sum(s["active"] for s in shown), 1),
            "user_prompts": sum(s["user_prompts"] for s in shown),
            "assistant_messages": sum(s["assistant_msgs"] for s in shown),
            "tool_calls": sum(s["tool_calls"] for s in shown),
            "traces": st["traces"],
            "spans": st["spans"],
            "trace_median_s": round(st["median"], 1) if st["median"] is not None else None,
            "cache_hit": round(st["cache_hit"], 6) if st["cache_hit"] is not None else None,
            "traces_interrupted": st["interrupted"],
        },
        "sessions": [],
    }
    def trace_json(tr: dict) -> dict:
        return {
            "n": tr["n"],
            "start": dt.datetime.fromtimestamp(tr["ts"], dt.timezone.utc).isoformat()
                     if tr["ts"] else None,
            "duration_s": round(tr["duration"], 1) if tr["duration"] is not None else None,
            "prompt": tr["prompt"],
            "requests": tr["requests"],
            "tools": tr["tools"],
            "tool_names": tr["tool_names"],
            "spans": tr["spans"],
            "subagents": tr["subagents"],
            "interrupted": tr["interrupted"],
            "tokens": tr["tokens"],
            "cache_hit": round(tr["cache_hit"], 6) if tr["cache_hit"] is not None else None,
            "cost_usd": round(tr["cost"], 6),
            "models": sorted(tr["per_model"]),
        }

    for s in shown:
        noto = s.get("costo_noto") is not False
        payload["sessions"].append({
            "session_id": s["session_id"],
            "fonte": fonte_di(s),
            # null, non zero: Copilot non dichiara i token, e chi legge questo
            # JSON deve poter distinguere «non misurato» da «non consumato».
            "costo_misurabile": noto,
            "project": s["project"],
            "project_dir": s["project_dir"],
            "cwd": s["cwd"],
            "title": s["title"],
            "first_prompt": s["first_prompt"],
            "git_branch": s["git_branch"],
            "claude_code_version": s["version"],
            "entrypoint": s["entrypoint"],
            "start": dt.datetime.fromtimestamp(s["start"], dt.timezone.utc).isoformat() if s["start"] else None,
            "end": dt.datetime.fromtimestamp(s["end"], dt.timezone.utc).isoformat() if s["end"] else None,
            "duration_s": round(s["duration"], 1),
            "active_s": round(s["active"], 1),
            "messages": {
                "user_prompts": s["user_prompts"],
                "assistant": s["assistant_msgs"],
                "subagent_tasks": s["subagent_prompts"],
                "tool_calls": s["tool_calls"],
                "tool_results": s["tool_results"],
                "api_errors": s["api_errors"],
            },
            "tokens": s["tokens"] if noto else None,
            "cost_usd": round(s["cost"], 6) if noto else None,
            "billing": s.get("billing", "subscription"),
            "real_cost": round(s.get("real_cost", 0.0), 6),
            "real_cost_by_month": {m: round(v, 6)
                                   for m, v in (s.get("per_month_real") or {}).items()},
            "cost_usd_by_month": {
                m: round(sum(d["cost"] for d in models.values()), 6)
                for m, models in (s.get("per_month") or {}).items()
            },
            "per_model": {
                m: {"tokens": d["tokens"], "cost_usd": round(d["cost"], 6)}
                for m, d in s["per_model"].items()
            },
            "subagents": {"files": s["subagent_files"], "types": s["agents"]},
            "unknown_models": s["unknown_models"],
            "traces_n": s.get("traces_n", 0),
            "spans_n": s.get("spans_n", 0),
            "cache_hit": round(s["cache_hit"], 6) if s.get("cache_hit") is not None else None,
            "trace_median_s": round(s["turn_median"], 1)
                              if s.get("turn_median") is not None else None,
            **({"traces": [trace_json(tr) for tr in (s.get("traces") or [])]}
               if with_traces else {}),
        })
    return payload


def view_json(sessions: list[dict], pricing: dict, args) -> None:
    print(json.dumps(build_json_payload(sessions, pricing, args.top,
                                        with_traces=getattr(args, "traces", False)),
                     indent=2, ensure_ascii=False))


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def parse_since(value: str | None) -> float | None:
    if not value:
        return None
    v = value.strip().lower()
    m = re.fullmatch(r"(\d+)\s*([smhdw])", v)
    if m:
        n = int(m.group(1))
        mult = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[m.group(2)]
        return time.time() - n * mult
    if v in ("today", "oggi"):
        return dt.datetime.combine(dt.date.today(), dt.time.min).timestamp()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"):
        try:
            return dt.datetime.strptime(value, fmt).timestamp()
        except ValueError:
            continue
    raise SystemExit(f"--since non riconosciuto: {value!r} (usa 7d, 24h, 90m, oggi, 2026-08-01)")


def default_base() -> str:
    home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    return os.path.join(home, ".claude", "projects")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="claude-monitor",
        description="Tempo, costo e messaggi delle conversazioni Claude Code (dai transcript JSONL).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""esempi:
  claude_monitor.py                        riepilogo delle ultime 20 sessioni
  claude_monitor.py --since 7d --top 50    ultima settimana
  claude_monitor.py --project MioProgetto   filtra per progetto
  claude_monitor.py --by-project           totali aggregati per progetto
  claude_monitor.py --session a1b2c3d4     dettaglio turno per turno
  claude_monitor.py --traces               un turno per riga, di tutte le sessioni
  claude_monitor.py --traces --search bug  cerca nei prompt e negli strumenti
  claude_monitor.py --watch                cruscotto live sulla sessione attiva
  claude_monitor.py --by-month             consumo, quota pagata e resa per mese
  claude_monitor.py --json > report.json   output machine-readable

configurazione (abbonamento, listino, tema, default):
  """ + (config_candidates()[0]) + """
  claude_monitor.py --config <file>        per usarne un altro
""",
    )
    p.add_argument("--base", default=default_base(),
                   help="cartella dei transcript (default: %(default)s)")
    p.add_argument("--config", "--pricing", dest="config",
                   help="percorso di config.json (default: accanto allo script)")
    p.add_argument("--billing", choices=["subscription", "api"],
                   help="come viene pagato l'uso; sovrascrive config.json "
                        "(subscription = quota fissa, il costo per token è ipotetico; "
                        "api = a consumo, il costo per token è reale)")
    p.add_argument("--project", help="filtra per progetto (sottostringa di path/cartella), o 'all'")
    p.add_argument("--since", help="considera solo le sessioni concluse dopo: 7d, 24h, 90m, oggi, 2026-08-01")
    p.add_argument("--top", type=int, default=None,
                   help="numero massimo di sessioni (0 = tutte; default da config.json)")
    p.add_argument("--session", help="dettaglio di una sessione (uuid o prefisso)")
    p.add_argument("--chat", action="store_true",
                   help="con --session: stampa la conversazione in Markdown "
                        "(titolo, data e percorso fatto) invece del dettaglio dei costi")
    p.add_argument("--with-subagents", action="store_true",
                   help="con --chat o --export-md: include i messaggi dei subagent")
    p.add_argument("--export-md", metavar="CARTELLA",
                   help="esporta in Markdown le conversazioni selezionate (rispetta "
                        "--project e --since): una cartella per progetto più un indice")
    p.add_argument("--watch", action="store_true", help="cruscotto live sulla sessione più recente")
    p.add_argument("--interval", type=float, default=None,
                   help="secondi tra i refresh in --watch (default da config.json)")
    p.add_argument("--traces", action="store_true",
                   help="un turno per riga invece di una sessione: dal prompt alla "
                        "fine della risposta, con durata, richieste, strumenti e costo")
    p.add_argument("--search", metavar="TESTO",
                   help="con --traces: filtra sul testo del prompt, sui nomi degli "
                        "strumenti, sul progetto e sull'id di sessione")
    p.add_argument("--trend", action="store_true",
                   help="andamento nel tempo e indicatori: uso, adozione, "
                        "cache, turni interrotti")
    p.add_argument("--grana", choices=[g[0] for g in cm_stat.GRANULARITA],
                   help="con --trend: ampiezza del periodo (default: scelta "
                        "in base all'intervallo coperto)")
    p.add_argument("--finestra", type=int, metavar="GIORNI",
                   help="con --trend: giorni della finestra confrontata con la "
                        "precedente di pari lunghezza")
    p.add_argument("--by-project", action="store_true", help="aggrega per progetto invece che per sessione")
    p.add_argument("--by-month", action="store_true",
                   help="consumo, costo ipotetico e costo reale dell'abbonamento, mese per mese")
    p.add_argument("--json", action="store_true", help="output JSON")
    p.add_argument("--idle-gap", type=float, default=None,
                   help="pausa oltre la quale il tempo non è 'attivo' (default da config.json)")
    p.add_argument("--no-breakdown", action="store_true", help="nascondi la tabella per modello")
    p.add_argument("--no-cache", action="store_true",
                   help="non leggere né scrivere l'archivio su disco")
    p.add_argument("--clear-cache", action="store_true",
                   help="svuota la cache di analisi (i transcript verranno riletti) "
                        "ed esci; l'archivio delle sessioni resta")
    p.add_argument("--dimentica-testo", action="store_true",
                   help="cancella dall'archivio il testo delle conversazioni ed esci; "
                        "i numeri restano")
    p.add_argument("--archivio", action="store_true",
                   help="quanto pesa l'archivio e da cosa, ed esci")
    p.add_argument("--export-turni", metavar="FILE.jsonl",
                   help="scrive in JSONL i turni selezionati (rispetta --search, "
                        "--project e --since): un turno per riga, con domanda, "
                        "risposta, costo ed esito")
    p.add_argument("--no-color", action="store_true", help="disabilita i colori")
    p.add_argument("--version", action="version", version=f"claude-monitor {__version__}")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    init_color(args.no_color or args.json)

    if args.clear_cache:
        try:
            os.remove(cache_path())
        except FileNotFoundError:
            pass
        try:
            with cm_archivio.Archivio(cm_archivio.db_path(), CACHE_FORMAT) as arch:
                n = arch.svuota_cache()
                c = arch.conta()
        except Exception as exc:
            warn(f"archivio non apribile: {exc}")
            return 2
        print(f"cache di analisi svuotata ({n} transcript): al prossimo giro "
              "vengono riletti.")
        print(f"l'archivio resta: {c['sessioni']} sessioni, {c['turni']} turni"
              + (f", di cui {c['acquisite']} senza piu' il transcript"
                 if c["acquisite"] else "") + ".")
        return 0

    if args.archivio:
        return view_archivio()

    if args.dimentica_testo:
        try:
            with cm_archivio.Archivio(cm_archivio.db_path(), CACHE_FORMAT) as arch:
                n = arch.dimentica_testo()
                # Senza VACUUM le pagine restano dentro il file: a chi ha appena
                # chiesto di dimenticare qualcosa, un file grande uguale sembra
                # — a ragione — che non sia successo niente.
                recuperati = arch.compatta()
                c = arch.conta()
        except Exception as exc:
            warn(f"archivio non apribile: {exc}")
            return 2
        print(f"{n} messaggi cancellati dall'archivio"
              + (f", {h_byte(recuperati)} restituiti al disco." if recuperati else "."))
        print(f"restano i numeri: {c['sessioni']} sessioni, {c['turni']} turni.")
        if c["acquisite"]:
            warn(f"{c['acquisite']} sessioni non hanno piu' il transcript: "
                 "di quelle conversazioni non resta piu' il testo da nessuna parte.")
        return 0

    if not os.path.isdir(args.base):
        warn(f"cartella non trovata: {args.base}")
        return 2

    pricing = load_config(args.config)
    if args.billing:  # lo switch da riga di comando vince sul file
        pricing.setdefault("billing", {})["mode"] = args.billing
    d = defaults_of(pricing)
    if args.idle_gap is None:
        args.idle_gap = float(d.get("idle_gap", 300))
    if args.top is None:
        args.top = int(d.get("top", 20))
    if args.interval is None:
        args.interval = float(d.get("watch_interval", 3.0))
    use_cache = not args.no_cache

    if args.watch:
        view_watch(args.base, pricing, args)
        return 0

    if args.session:
        if args.chat:
            sess, messages = cm_chat = load_conversation(
                args.base, args.session, pricing, args.idle_gap)
            if not sess:
                warn(f"Sessione '{args.session}' non trovata")
                return 2
            print(conversation_markdown(sess, messages, pricing, args.with_subagents))
            return 0
        view_detail(args.base, pricing, args)
        return 0

    sessions = collect(args.base, pricing, use_cache, args.idle_gap,
                       project=args.project, quiet=args.json)
    since = parse_since(args.since)
    if since:
        sessions = [s for s in sessions if (s["end"] or 0) >= since]
    sessions = [s for s in sessions if s["assistant_msgs"] or s["user_prompts"]]
    allocate_real_cost(sessions, pricing)

    if args.export_md:
        def progress(done, total, project):
            info(f"[{done}/{total}] {project}")
        result = export_conversations(sessions, args.base, pricing, args.export_md,
                                      args.idle_gap, args.with_subagents, progress)
        print()
        print(C.w(f"  {result['written']} conversazioni in {result['projects']} progetti "
                  f"{BULLET} indice: {result['index']}", C.BOLD))
        if result["failed"]:
            warn(f"{len(result['failed'])} sessioni non leggibili: "
                 + ", ".join(s[:8] for s in result["failed"][:5]))
        return 0

    if args.export_turni:
        needle = getattr(args, "search", "") or ""
        righe = flatten_traces(sessions, needle, anche=cerca_nel_testo(needle))
        if args.top:
            righe = righe[: args.top]
        esito = export_turni_jsonl(righe, args.export_turni)
        print()
        print(C.w(f"  {esito['turni']} turni in {args.export_turni}", C.BOLD))
        if esito["senza_risposta"]:
            warn(f"{esito['senza_risposta']} turni senza risposta"
                 + ("" if esito["testo_archiviato"] else
                    ": il testo non e' archiviato (archivio.testo in config.json)"))
        return 0

    if args.json:
        view_json(sessions, pricing, args)
    elif args.trend:
        view_trend(sessions, pricing, args)
    elif args.traces:
        view_traces(sessions, pricing, args)
    elif args.by_month:
        view_by_month(sessions, pricing, args)
    elif args.by_project:
        view_by_project(sessions, pricing, args)
    else:
        view_summary(sessions, pricing, args)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
