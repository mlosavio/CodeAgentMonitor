#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cm-agent — porta nell'archivio del raccoglitore quello che la telemetria non sa.

La telemetria nativa di Claude Code copre il flusso vivo, ma parte dal giorno in
cui la si accende e non conosce l'abbonamento: il suo costo e' il valore a
listino API. Sul disco pero' ci sono gia' i transcript di mesi, e da quelli il
parser di claude_monitor.py ricava anche la spesa reale.

Questo agente li rilegge a intervalli, calcola la differenza rispetto a quanto
gia' spedito e manda solo quella. Non apre porte, non resta in ascolto: parla
solo lui, verso il raccoglitore. Funziona quindi identico in sede, in VPN e su
un portatile fuori rete.

    python cm_agent.py --once                    # un invio solo, poi esce
    python cm_agent.py                           # resta e rispedisce ogni 15 min
    python cm_agent.py --dry-run                 # mostra cosa spedirebbe
    python cm_agent.py --show-payload            # stampa il JSON esatto
    python cm_agent.py --reset                   # dimentica cosa ha gia' spedito

COSA ESCE DA QUESTA MACCHINA
    Solo i campi elencati in CAMPI_SPEDITI: numeri, identificativi e il nome del
    progetto. Titoli delle conversazioni, testo delle richieste, percorsi dei
    file e cartella di lavoro non vengono mai letti nel payload — non perche'
    siano esclusi, ma perche' non sono nell'elenco di quelli inclusi. La
    differenza conta: un elenco di esclusioni si dimentica di aggiornarlo quando
    il parser guadagna un campo nuovo, un elenco di inclusioni no.

Solo stdlib, come il resto del progetto.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request

import claude_monitor as cm

__version__ = "0.1.0"

try:  # console Windows: assicura UTF-8 in output
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover
    pass

# --------------------------------------------------------------------------- #
# Il confine: cosa esce da questa macchina
# --------------------------------------------------------------------------- #

# Elenco chiuso. Aggiungere una voce qui e' una decisione da prendere in modo
# consapevole; dimenticarsene lascia il campo a terra, che e' il verso giusto
# in cui sbagliare.
CAMPI_SPEDITI = (
    "session_id",       # identificativo opaco, gia' presente nella telemetria
    "project",          # etichetta del progetto: serve al costo per commessa
    "start", "end",     # quando
    "duration", "active",
    "user_prompts", "assistant_msgs", "tool_calls",
    "cost",             # valore a listino API, in USD
    "real_cost",        # quota dell'abbonamento attribuita
    "billing",          # 'subscription' oppure 'api'
    "tokens",           # dizionario di soli numeri
    "per_month",        # ripartizione mensile: mese -> modello -> numeri
)

# Campi che il parser produce e che restano a terra. Elencati solo per poterli
# verificare in una prova: non e' questa tupla a escluderli, e' CAMPI_SPEDITI a
# non includerli.
MAI_SPEDITI = (
    "title",            # titolo della conversazione: ne abbiamo trovati con
                        # dentro il nome di una persona terza
    "first_prompt",     # testo della prima richiesta
    "cwd", "project_dir",  # percorsi assoluti: struttura del disco e utente
    "files",            # percorsi dei transcript
    "git_branch",       # nomi di ramo: spesso sono numeri di ticket o clienti
    "agents", "ts",
)


def payload_sessione(sess: dict) -> dict:
    """Una sessione ridotta ai soli campi ammessi."""
    fuori = {}
    for campo in CAMPI_SPEDITI:
        valore = sess.get(campo)
        if campo == "tokens" and isinstance(valore, dict):
            # solo numeri, nient'altro, anche se il parser cambiasse forma
            valore = {k: v for k, v in valore.items() if isinstance(v, (int, float))}
        fuori[campo] = valore
    return fuori


def controlla_confine(payload: dict) -> list[str]:
    """Verifica che nel payload non sia finito nulla che non doveva.

    Chiamata prima di ogni invio, non solo nelle prove: costa niente e la
    garanzia va esercitata dove i dati passano davvero.
    """
    sospetti = []
    for s in payload.get("sessions", []):
        for chiave in s:
            if chiave not in CAMPI_SPEDITI:
                sospetti.append(chiave)
    return sorted(set(sospetti))


# --------------------------------------------------------------------------- #
# Identita'
# --------------------------------------------------------------------------- #


def identita_account() -> dict:
    """Indirizzo e identificativi dell'account, da ~/.claude.json.

    Si usa lo stesso indirizzo che manda la telemetria nativa: e' l'unico modo
    perche' le due fonti si uniscano sulla stessa persona invece di comparire
    come due postazioni distinte.

    Va detto chiaro: qui l'indirizzo esce in chiaro dalla macchina, esattamente
    come fa gia' la telemetria. A ridurlo e' il raccoglitore, secondo il livello
    con cui e' stato avviato, prima di scrivere in archivio.
    """
    path = os.path.expanduser("~/.claude.json")
    try:
        with open(path, encoding="utf-8") as fh:
            acc = (json.load(fh).get("oauthAccount") or {})
        return {
            "user": acc.get("emailAddress"),
            "account_uuid": acc.get("accountUuid"),
            "organization": acc.get("organizationUuid"),
        }
    except Exception:
        return {"user": None, "account_uuid": None, "organization": None}


def id_macchina(stato: dict, path_stato: str | None = None) -> str:
    """Identificativo stabile della postazione, generato una volta e conservato.

    Non e' il nome del computer di proposito: quelli spesso contengono il nome
    di chi ci lavora, e diventerebbero un identificatore di persona che nessun
    livello di riservatezza toglierebbe piu'.

    Va salvato subito, non al primo invio riuscito: altrimenti una prova a vuoto
    o un raccoglitore spento ne generano uno nuovo ogni volta, e la stessa
    macchina comparirebbe come tante postazioni diverse.
    """
    mid = stato.get("machine")
    if not mid:
        mid = "m-" + os.urandom(8).hex()
        stato["machine"] = mid
        if path_stato:
            salva_stato(path_stato, stato)
    return mid


# --------------------------------------------------------------------------- #
# Stato locale: cosa e' gia' stato spedito
# --------------------------------------------------------------------------- #


def percorso_stato() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.config")
    cartella = os.path.join(base, "claude-monitor")
    os.makedirs(cartella, exist_ok=True)
    return os.path.join(cartella, "agent.json")


def carica_stato(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            s = json.load(fh)
        s.setdefault("inviate", {})
        return s
    except Exception:
        return {"inviate": {}}


def salva_stato(path: str, stato: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(stato, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)   # sostituzione atomica: mai uno stato mezzo scritto


def da_spedire(sessioni: list[dict], inviate: dict) -> list[dict]:
    """Solo le sessioni nuove o cresciute dall'ultimo invio.

    Il confronto e' sull'ultimo evento della sessione: una conversazione chiusa
    non viene rispedita all'infinito, una ancora aperta si' finche' cresce.
    """
    fuori = []
    for s in sessioni:
        sid = s.get("session_id")
        if not sid:
            continue
        fine = float(s.get("end") or 0)
        if fine > float(inviate.get(sid) or 0):
            fuori.append(s)
    return fuori


# --------------------------------------------------------------------------- #
# Invio
# --------------------------------------------------------------------------- #


def spedisci(endpoint: str, payload: dict, token: str | None,
             timeout: float = 30.0) -> dict:
    corpo = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(endpoint.rstrip("/") + "/v1/sessions", corpo,
                                 {"Content-Type": "application/json"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8") or "{}")


def un_giro(args, config: dict, stato: dict, path_stato: str) -> bool:
    """Una raccolta e un invio. True se e' andata."""
    ident = identita_account()
    macchina = id_macchina(stato, path_stato)

    sessioni = cm.collect(args.base, config, True, args.idle_gap, None, True, None)
    cm.allocate_real_cost(sessioni, config)
    for s in sessioni:
        s["billing"] = cm.session_billing(s, config)

    nuove = da_spedire(sessioni, stato["inviate"])
    if not nuove:
        print(f"niente di nuovo ({len(sessioni)} sessioni gia' allineate)")
        return True

    payload = {
        "agent": f"cm-agent/{__version__}",
        "machine": macchina,
        "user": ident["user"],
        "attrs": {k: v for k, v in {
            "user.email": ident["user"],
            "user.account_uuid": ident["account_uuid"],
            "organization.id": ident["organization"],
        }.items() if v},
        "currency": cm.display_currency(config),
        "sessions": [payload_sessione(s) for s in nuove],
    }
    for s in payload["sessions"]:
        s["currency"] = payload["currency"]
        s["user"] = payload["user"]
        s["attrs"] = payload["attrs"]

    # La verifica del confine gira a ogni invio, non solo nelle prove.
    sospetti = controlla_confine({"sessions": [
        {k: v for k, v in s.items() if k not in ("currency", "user", "attrs")}
        for s in payload["sessions"]]})
    if sospetti:
        print(f"INVIO ANNULLATO: campi non ammessi nel payload: {sospetti}")
        return False

    if args.show_payload:
        print(json.dumps(payload, ensure_ascii=False, indent=2)[:4000])

    if args.dry_run:
        tot = sum(s["cost"] or 0 for s in payload["sessions"])
        print(f"[prova] {len(nuove)} sessioni pronte, ${tot:,.2f} a listino — "
              f"niente spedito")
        return True

    try:
        esito = spedisci(args.endpoint, payload, args.token)
    except urllib.error.HTTPError as exc:
        print(f"raccoglitore ha rifiutato ({exc.code}): {exc.read()[:200]!r}")
        return False
    except (urllib.error.URLError, socket.timeout, OSError) as exc:
        # Il raccoglitore puo' essere spento: non e' un errore dell'agente.
        # Lo stato non viene aggiornato, quindi al giro dopo si riprova tutto.
        print(f"raccoglitore non raggiungibile: {exc}")
        return False

    for s in nuove:
        stato["inviate"][s["session_id"]] = float(s.get("end") or 0)
    stato["ultimo_invio"] = time.time()
    salva_stato(path_stato, stato)
    print(f"spedite {len(nuove)} sessioni — "
          f"{esito.get('scritte', '?')} scritte, "
          f"{esito.get('ignorate', '?')} gia' aggiornate")
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Manda al raccoglitore le sessioni ricavate dai transcript.")
    ap.add_argument("--endpoint", default=os.environ.get(
        "CM_ENDPOINT", "http://127.0.0.1:4318"),
        help="indirizzo del raccoglitore (o variabile CM_ENDPOINT)")
    ap.add_argument("--token", default=os.environ.get("CM_TOKEN"),
                    help="token condiviso, se il raccoglitore lo richiede")
    ap.add_argument("--base", default=None, help="cartella dei transcript")
    ap.add_argument("--config", default=None, help="file di configurazione")
    ap.add_argument("--idle-gap", type=float, default=None,
                    help="pausa oltre la quale il tempo non conta come lavoro")
    ap.add_argument("--interval", type=float, default=15.0,
                    help="minuti fra un invio e il successivo (default 15)")
    ap.add_argument("--once", action="store_true", help="un invio solo, poi esce")
    ap.add_argument("--dry-run", action="store_true",
                    help="calcola e mostra, senza spedire niente")
    ap.add_argument("--show-payload", action="store_true",
                    help="stampa il JSON esatto che uscirebbe")
    ap.add_argument("--reset", action="store_true",
                    help="dimentica cosa e' gia' stato spedito e rimanda tutto")
    args = ap.parse_args(argv)

    config = cm.load_config(args.config)
    if args.base is None:
        args.base = cm.default_base()
    if args.idle_gap is None:
        args.idle_gap = float((cm.defaults_of(config) or {}).get("idle_gap", 300))

    path_stato = percorso_stato()
    stato = carica_stato(path_stato)
    if args.reset:
        stato["inviate"] = {}
        salva_stato(path_stato, stato)
        print("stato azzerato: al prossimo invio riparte da tutto lo storico")

    print(f"cm-agent {__version__} → {args.endpoint}")
    print(f"  postazione {id_macchina(stato, path_stato)}")
    print(f"  stato      {path_stato}")
    print()

    if args.once or args.dry_run:
        return 0 if un_giro(args, config, stato, path_stato) else 1

    attesa = max(60.0, args.interval * 60.0)
    try:
        while True:
            un_giro(args, config, stato, path_stato)
            time.sleep(attesa)
    except KeyboardInterrupt:
        print("\nfermato.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
