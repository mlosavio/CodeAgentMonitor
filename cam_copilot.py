#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GitHub Copilot come seconda sorgente.

Copilot **non scrive un transcript** come Claude Code. Le sue chat stanno nello
storage di VS Code, in un formato non pubblico che puo' cambiare a ogni
versione dell'estensione:

    %APPDATA%\\Code\\User\\workspaceStorage\\<hash>\\chatSessions\\*.json
    %APPDATA%\\Code\\User\\globalStorage\\emptyWindowChatSessions\\*.json

Da qui discende tutto il resto del disegno. Su una fonte non documentata non
si puo' dire «se il parser sbaglia si rilegge»: il file di oggi domani puo'
non esserci o avere un'altra forma. Quindi quello che si legge si **conserva**
in archivio con `origine = 'acquisito'`, e non si prova a ricostruirlo.

**Cosa c'e' e cosa no.** Ci sono i turni, gli orari, il modello, la latenza
*misurata* (piu' precisa di quella che deduciamo per Claude Code), le domande,
le risposte e le chiamate agli strumenti con i loro nomi. **Non ci sono i token
e non c'e' il costo**: le uniche chiavi che li nominano sono `maxInputTokens` e
`maxOutputTokens`, che sono i limiti del modello, non il consumo.

Quei due numeri restano **non noti**, e si mostrano come «—». Inventare un
«se fosse API» per un prodotto a quota fissa vorrebbe dire scrivere una cifra
che verrebbe letta come una spesa. Un trattino dice la verita': qui non si sa.

Solo stdlib.
"""

from __future__ import annotations

import glob
import json
import os
import urllib.parse

FONTE = "copilot"

# Le stesse voci che usa cam. Sono ripetute qui invece di importarle
# perche' e' cam a importare questo modulo, non il contrario — e
# soprattutto perche' i consumatori indicizzano direttamente `tokens["input"]`:
# un dizionario vuoto li farebbe saltare. Qui i token valgono zero e sono
# `costo_noto: False` a dire che quello zero non e' una misura.
_TOKEN = ("input", "output", "cache_read", "cache_w5m", "cache_w1h",
          "web_search", "web_fetch")


def token_vuoti() -> dict:
    return {k: 0 for k in _TOKEN}

# Blocchi della risposta che sono testo dell'assistente. Gli altri sono
# ragionamento interno, modifiche ai file, avvii di server: utili al
# funzionamento, non a chi rilegge la conversazione.
_KIND_TESTO = (None, "markdownContent")


def cartelle_utente() -> list[str]:
    """Le cartelle `User` di VS Code presenti sulla macchina.

    Stable e Insiders sono installazioni separate con storage separati: chi ha
    entrambe usa entrambe, e sommarle e' quello che ci si aspetta.
    """
    radici = []
    for base in (os.environ.get("APPDATA"),
                 os.path.join(os.path.expanduser("~"), ".config"),          # Linux
                 os.path.join(os.path.expanduser("~"), "Library", "Application Support")):
        if not base:
            continue
        for nome in ("Code", "Code - Insiders", "VSCodium"):
            p = os.path.join(base, nome, "User")
            if os.path.isdir(p):
                radici.append(p)
    return radici


def _progetto_di(cartella_hash: str) -> tuple[str | None, str | None]:
    """(nome del progetto, percorso) leggendo `workspace.json` accanto alle chat."""
    path = os.path.join(cartella_hash, "workspace.json")
    try:
        with open(path, encoding="utf-8") as fh:
            uri = (json.load(fh) or {}).get("folder")
    except (OSError, ValueError):
        return None, None
    if not uri:
        return None, None
    p = urllib.parse.unquote(urllib.parse.urlparse(uri).path or "")
    # file:///c%3A/MLO/D/Projects/Tizio -> C:/MLO/D/Projects/Tizio
    # La barra iniziale si toglie solo se davanti a una lettera di unita': su
    # macOS e Linux e' parte del percorso, e toglierla lo renderebbe relativo —
    # cioe' riferito alla cartella da cui e' partito il programma, che non
    # c'entra niente.
    if len(p) > 2 and p[0] == "/" and p[1].isalpha() and p[2] == ":":
        p = p[1].upper() + p[2:]
    return (os.path.basename(p.rstrip("/\\")) or p or None), (p or None)


def _testo_risposta(blocchi, limite: int = 200_000) -> str:
    out = []
    for b in blocchi or ():
        if not isinstance(b, dict):
            continue
        if b.get("kind") in _KIND_TESTO:
            v = b.get("value")
            if isinstance(v, str) and v.strip():
                out.append(v)
            elif isinstance(v, dict) and isinstance(v.get("value"), str):
                out.append(v["value"])
    testo = "\n".join(out).strip()
    return testo[:limite]


def _strumenti(blocchi) -> list[str]:
    nomi = []
    for b in blocchi or ():
        if isinstance(b, dict) and b.get("kind") == "toolInvocationSerialized":
            nomi.append(b.get("toolId")
                        or (b.get("toolSpecificData") or {}).get("kind")
                        or "?")
    return nomi


def _ms(v) -> float | None:
    """Millisecondi di VS Code in secondi epoch. None se non e' un numero."""
    if isinstance(v, (int, float)) and v > 0:
        return v / 1000.0
    return None


def leggi_file(path: str, progetto: str | None, cwd: str | None,
               keep_messages: bool = False) -> dict | None:
    """Una sessione di chat. None se il file non e' leggibile o non ha turni.

    Un file illeggibile non e' un errore da propagare: e' una versione
    dell'estensione che ha cambiato formato, e il resto del programma deve
    continuare a funzionare senza di lei.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict):
        return None
    richieste = d.get("requests")
    if not isinstance(richieste, list) or not richieste:
        return None

    sid = str(d.get("sessionId") or os.path.splitext(os.path.basename(path))[0])
    traces, messaggi = [], []
    strumenti_totali = 0
    attivo = 0.0
    modelli: dict[str, int] = {}
    primo = None

    for n, r in enumerate(richieste, 1):
        if not isinstance(r, dict):
            continue
        ts = _ms(r.get("timestamp"))
        timings = ((r.get("result") or {}).get("timings") or {})
        durata = None
        for chiave in ("totalElapsed", "firstProgress"):
            v = timings.get(chiave)
            if isinstance(v, (int, float)) and v >= 0:
                durata = v / 1000.0
                break
        if durata is None:
            v = r.get("timeSpentWaiting")
            durata = v / 1000.0 if isinstance(v, (int, float)) and v >= 0 else None
        if durata:
            attivo += durata

        prompt = ""
        msg = r.get("message")
        if isinstance(msg, dict) and isinstance(msg.get("text"), str):
            prompt = " ".join(msg["text"].split())
        modello = r.get("modelId") or None
        if modello:
            modelli[modello] = modelli.get(modello, 0) + 1
        nomi = _strumenti(r.get("response"))
        strumenti_totali += len(nomi)
        if primo is None and prompt:
            primo = prompt[:200]

        traces.append({
            "n": n,
            "ts": ts,
            "end": (ts + durata) if (ts is not None and durata) else ts,
            "duration": durata,
            "prompt": prompt[:200] or None,
            # Il file registra una richiesta per turno. I giri interni fra
            # modello e strumenti non compaiono, quindi non si contano.
            "requests": 1,
            "tools": len(nomi),
            "tool_names": sorted(set(nomi))[:24],
            "subagents": 0,
            "interrupted": False,
            "cost": 0.0,
            "costo_noto": False,
            "cache_hit": None,
            "tokens": token_vuoti(),
            "per_model": ({modello: {"tokens": token_vuoti(), "cost": 0.0}}
                          if modello else {}),
            "models": {},
            "spans": 1 + 1 + len(nomi),
        })

        if keep_messages:
            if prompt:
                messaggi.append({"kind": "prompt", "ts": ts, "text": prompt,
                                 "model": None, "tools": [], "subagent": False})
            risposta = _testo_risposta(r.get("response"))
            if risposta:
                messaggi.append({"kind": "assistant", "ts": ts, "text": risposta,
                                 "model": modello, "tools": nomi, "subagent": False})

    if not traces:
        return None

    inizio = _ms(d.get("creationDate")) or min(
        (t["ts"] for t in traces if t["ts"] is not None), default=None)
    fine = _ms(d.get("lastMessageDate")) or max(
        (t["end"] or t["ts"] for t in traces if t["ts"] is not None), default=None)

    sess = {
        "session_id": sid,
        "fonte": FONTE,
        "origine": "acquisito",
        "costo_noto": False,
        "project": progetto or "(senza progetto)",
        "project_dir": None,
        "cwd": cwd,
        "title": d.get("customTitle") or None,
        "first_prompt": primo,
        "git_branch": None,
        "version": None,
        "entrypoint": "vscode",
        "models": {},
        "by_month": {},
        "user_prompts": len(traces),
        "subagent_prompts": 0,
        "assistant_msgs": len(traces),
        "tool_calls": strumenti_totali,
        "tool_results": strumenti_totali,
        "api_errors": 0,
        "bad_lines": 0,
        "agents": {},
        "subagent_files": 0,
        "files": [],
        "percorso": path,
        "mtime": os.path.getmtime(path) if os.path.exists(path) else 0.0,
        "start": inizio,
        "end": fine,
        "duration": (fine - inizio) if (inizio and fine) else 0.0,
        "active": attivo,
        "tokens": token_vuoti(),
        "cost": 0.0,
        "per_model": {m: {"tokens": token_vuoti(), "cost": 0.0} for m in modelli},
        "per_month": {},
        "unknown_models": [],
        "messages_total": len(traces) * 2,
        "traces": traces,
        "traces_n": len(traces),
        "spans_n": sum(t["spans"] for t in traces),
        "cache_hit": None,
        "turn_median": None,
    }
    if keep_messages:
        sess["messaggi"] = messaggi
    return sess


def sessioni(radici: list[str] | None = None,
             keep_messages: bool = False) -> list[dict]:
    """Tutte le chat di Copilot trovate sulla macchina, dalla piu' recente.

    Le sessioni senza progetto (finestre aperte senza cartella) ci sono lo
    stesso: sono lavoro fatto, e scartarle perche' non si sa dove collocarlo
    vorrebbe dire perderlo.
    """
    out = []
    for user in (radici if radici is not None else cartelle_utente()):
        for path in sorted(glob.glob(os.path.join(
                user, "workspaceStorage", "*", "chatSessions", "*.json"))):
            cartella = os.path.dirname(os.path.dirname(path))
            progetto, cwd = _progetto_di(cartella)
            s = leggi_file(path, progetto, cwd, keep_messages)
            if s:
                out.append(s)
        for path in sorted(glob.glob(os.path.join(
                user, "globalStorage", "emptyWindowChatSessions", "*.json"))):
            s = leggi_file(path, None, None, keep_messages)
            if s:
                out.append(s)
    out.sort(key=lambda s: s["end"] or 0, reverse=True)
    return out


def mediana_turni(sess: dict) -> float | None:
    durate = sorted(t["duration"] for t in sess["traces"] if t.get("duration"))
    if not durate:
        return None
    m = len(durate) // 2
    return float(durate[m]) if len(durate) % 2 else (durate[m - 1] + durate[m]) / 2.0
