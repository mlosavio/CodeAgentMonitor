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
import datetime as dt
import glob
import json
import os
import re
import sys
import time

__version__ = "1.0.0"

CACHE_FORMAT = 7  # bump per invalidare la cache su disco quando cambia lo schema

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
                if row.get("isSidechain"):
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
                content = (row.get("message") or {}).get("content")
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_result":
                            self.result_ids.add(
                                block.get("tool_use_id") or f"anon-{len(self.result_ids)}"
                            )
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
                               "month": month_of(ts), "text": text}
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

    def snapshot(self) -> dict:
        """Record aggregato. Non consuma lo stato: richiamabile a ogni refresh."""
        models: dict[str, dict] = {}
        by_month: dict[str, dict] = {}
        messages = list(self.prompts) if self.keep_messages else []
        for entry in self.dedup.values():
            add_tok(models.setdefault(entry["model"], new_tok()), entry["tok"])
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
            "user_prompts": self.user_prompts,
            "subagent_prompts": self.subagent_prompts,
            "assistant_msgs": len(self.dedup),
            "tool_calls": len(self.tool_ids),
            "tool_results": len(self.result_ids),
            "api_errors": self.api_errors,
            "bad_lines": self.bad_lines,
            "agents": dict(self.agents),
            "ts": list(self.ts),
            "first_prompt": self.first_prompt,
            "version": self.version,
            "git_branch": self.git_branch,
            "entrypoint": self.entrypoint,
        }
        if self.keep_messages:
            rec["messages"] = messages
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
# Cache su disco
# --------------------------------------------------------------------------- #


def cache_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache.json")


def load_cache(use_cache: bool) -> dict:
    if not use_cache:
        return {}
    try:
        with open(cache_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        if data.get("format") != CACHE_FORMAT:
            return {}
        return data.get("files") or {}
    except Exception:
        return {}


def save_cache(files: dict, use_cache: bool) -> None:
    if not use_cache:
        return
    try:
        tmp = cache_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"format": CACHE_FORMAT, "files": files}, fh)
        os.replace(tmp, cache_path())
    except Exception:
        pass


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
        "ts": [],
        "files": [],
        "version": None,
        "git_branch": None,
        "entrypoint": None,
        "mtime": 0.0,
    }


def merge_record(sess: dict, rec: dict, mtime: float) -> None:
    for model, tok in (rec.get("models") or {}).items():
        add_tok(sess["models"].setdefault(model, new_tok()), tok)
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
    else:
        sess["project_dir"] = sess["project_dir"] or rec.get("project_dir")
        sess["cwd"] = sess["cwd"] or rec.get("cwd")
        sess["title"] = sess["title"] or rec.get("title")
        sess["first_prompt"] = sess["first_prompt"] or rec.get("first_prompt")
        sess["version"] = sess["version"] or rec.get("version")
        sess["git_branch"] = sess["git_branch"] or rec.get("git_branch")
        sess["entrypoint"] = sess["entrypoint"] or rec.get("entrypoint")
    sess["project"] = sess["project"] or project_label(rec)


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

    per_month = {}
    for month, models in (sess.get("by_month") or {}).items():
        per_month[month] = {
            m: {"tokens": tok, "cost": cost_of(m, tok, pricing)[0]}
            for m, tok in models.items()
        }
    sess["per_month"] = per_month
    sess["messages_total"] = sess["user_prompts"] + sess["assistant_msgs"]
    return sess


def collect(base: str, pricing: dict, use_cache: bool, idle_gap: float,
            project: str | None = None, quiet: bool = False,
            on_progress=None) -> list[dict]:
    """Aggrega tutte le sessioni sotto `base`.

    `on_progress(fatti, totali, path, da_cache)` viene richiamato per ogni file:
    serve a chi mostra una barra di avanzamento (la GUI). Default None = nessun effetto.
    """
    files = sorted(glob.glob(os.path.join(base, "**", "*.jsonl"), recursive=True))
    if not files:
        warn(f"Nessun transcript trovato in {base}")
        return []

    cache = load_cache(use_cache)
    fresh: dict[str, dict] = {}
    sessions: dict[str, dict] = {}
    parsed = 0
    total_files = len(files)
    if on_progress is not None:
        on_progress(0, total_files, None, True)

    for done, path in enumerate(files, 1):
        try:
            st = os.stat(path)
        except OSError:
            if on_progress is not None:
                on_progress(done, total_files, path, True)
            continue
        if project and not path_matches_project(path, project):
            if on_progress is not None:
                on_progress(done, total_files, path, True)
            continue
        key = path
        cached = cache.get(key)
        if cached and cached.get("size") == st.st_size and abs(cached.get("mtime", -1) - st.st_mtime) < 0.001:
            rec = cached["rec"]
        else:
            if not quiet and st.st_size > 20_000_000:
                info(f"analizzo {os.path.basename(path)} ({st.st_size / 1e6:.0f} MB)…")
            rec = scan_file(path, pricing)
            parsed += 1
        fresh[key] = {"size": st.st_size, "mtime": st.st_mtime, "rec": rec}

        sid = rec.get("session_id") or path
        sess = sessions.setdefault(sid, new_session(sid))
        merge_record(sess, rec, st.st_mtime)

        if on_progress is not None:
            on_progress(done, total_files, path, cached is not None)

    # riscrive la cache preservando le voci dei file esclusi dal filtro --project
    if use_cache:
        merged = dict(cache)
        merged.update(fresh)
        alive = {p for p in files}
        merged = {k: v for k, v in merged.items() if k in alive}
        save_cache(merged, use_cache)

    out = [finalize(s, pricing, idle_gap) for s in sessions.values()]
    out.sort(key=lambda s: s["end"] or 0, reverse=True)
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
            h_tokens(t["input"]),
            h_tokens(t["output"]),
            h_tokens(t["cache_w5m"] + t["cache_w1h"]),
            h_tokens(t["cache_read"]),
            h_cost(s["cost"]),
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
    print_cost_legend(pricing, shown)

    if not args.no_breakdown:
        print_model_breakdown(shown, pricing)
    report_unknown(shown)


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


def load_conversation(base: str, session_id: str, pricing: dict,
                      idle_gap: float = 300.0) -> tuple[dict, list[dict]]:
    """Sessione + messaggi in ordine, con il testo di quello che è stato detto."""
    sid, files = find_session_files(base, session_id)
    if not files:
        return {}, []
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


def export_conversations(sessions: list[dict], base: str, pricing: dict,
                         out_dir: str, idle_gap: float = 300.0,
                         include_subagents: bool = False,
                         on_progress=None) -> dict:
    """Scrive una cartella con un indice e una conversazione per file.

    Le sessioni vengono rilette una a una: il testo dei messaggi non sta nella
    cache, che tiene solo i numeri. Ritorna un riepilogo di quello che ha scritto.
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


def build_json_payload(sessions: list[dict], pricing: dict, top: int = 0) -> dict:
    """Payload machine-readable delle sessioni. Puro: non stampa nulla."""
    shown = sessions[:top] if top else sessions
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
        },
        "sessions": [],
    }
    for s in shown:
        payload["sessions"].append({
            "session_id": s["session_id"],
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
            "tokens": s["tokens"],
            "cost_usd": round(s["cost"], 6),
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
        })
    return payload


def view_json(sessions: list[dict], pricing: dict, args) -> None:
    print(json.dumps(build_json_payload(sessions, pricing, args.top),
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
    p.add_argument("--by-project", action="store_true", help="aggrega per progetto invece che per sessione")
    p.add_argument("--by-month", action="store_true",
                   help="consumo, costo ipotetico e costo reale dell'abbonamento, mese per mese")
    p.add_argument("--json", action="store_true", help="output JSON")
    p.add_argument("--idle-gap", type=float, default=None,
                   help="pausa oltre la quale il tempo non è 'attivo' (default da config.json)")
    p.add_argument("--no-breakdown", action="store_true", help="nascondi la tabella per modello")
    p.add_argument("--no-cache", action="store_true", help="non usare la cache su disco")
    p.add_argument("--clear-cache", action="store_true", help="svuota la cache ed esci")
    p.add_argument("--no-color", action="store_true", help="disabilita i colori")
    p.add_argument("--version", action="version", version=f"claude-monitor {__version__}")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    init_color(args.no_color or args.json)

    if args.clear_cache:
        try:
            os.remove(cache_path())
            print("cache svuotata.")
        except FileNotFoundError:
            print("nessuna cache da svuotare.")
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

    if args.json:
        view_json(sessions, pricing, args)
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
