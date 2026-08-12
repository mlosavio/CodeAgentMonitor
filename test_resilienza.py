#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Prova di resilienza: l'agente e un raccoglitore che cade.

Il raccoglitore puo' essere spento, in aggiornamento o irraggiungibile. In quel
caso l'agente NON deve aggiornare il proprio segnalibro, altrimenti quello che
non e' arrivato lo considererebbe spedito e non lo rimanderebbe mai piu': una
perdita silenziosa, il tipo peggiore.

Nel codice e' gestita. Le cose gestite ma mai provate sono pero' proprio quelle
che poi non funzionano, quindi qui si verifica davvero, spegnendo e riaccendendo
un raccoglitore vero.

    python test_resilienza.py
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import types
from http.server import ThreadingHTTPServer

import claude_monitor as cm
import cm_agent as ca
import cm_collector as cc

try:  # console Windows: senza questo l'output rediretto muore sugli accenti
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover
    pass


esiti: list[tuple[bool, str]] = []


def verifica(nome: str, ottenuto, atteso) -> None:
    buono = ottenuto == atteso
    esiti.append((buono, nome))
    print(f"  {'ok  ' if buono else 'FALLITO'}  {nome:<52} {ottenuto!r}"
          + ("" if buono else f"   atteso {atteso!r}"))


def porta_libera() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


ORA = time.time()


def finta_sessione(sid: str, fine: float, costo: float) -> dict:
    """Una sessione come la produce il parser, ridotta al necessario."""
    return {
        "session_id": sid, "project": "Progetto", "cwd": r"C:\SEGRETO\percorso",
        "title": "SEGRETO-titolo", "start": ORA - 3600, "end": fine,
        "duration": 3600.0, "active": 1200.0, "user_prompts": 5,
        "assistant_msgs": 50, "tool_calls": 90, "cost": costo,
        "tokens": {"input": 10, "output": 20, "cache_read": 900,
                   "cache_w5m": 30, "cache_w1h": 10},
        "by_month": {}, "models": {},
    }


SESSIONI = [finta_sessione("s-1", ORA - 100, 10.0),
            finta_sessione("s-2", ORA - 50, 20.0)]


def avvia_raccoglitore(store: cc.Store, porta: int) -> ThreadingHTTPServer:
    cc.Handler.store = store
    cc.Handler.verbose = False
    cc.Handler.token = None
    httpd = ThreadingHTTPServer(("127.0.0.1", porta), cc.Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    time.sleep(0.2)
    return httpd


cartella = tempfile.mkdtemp(prefix="cm-resil-")
porta = porta_libera()
stato_path = os.path.join(cartella, "agent.json")
store = cc.Store(os.path.join(cartella, "r.db"),
                 cc.make_privacy("pseudonimo", os.path.join(cartella, "k.key")),
                 "pseudonimo")

# un_giro() chiama il parser vero, che qui non serve: si sostituisce con le
# sessioni finte, cosi' si prova la logica di invio e non la lettura dei file
sessioni_correnti = list(SESSIONI)
cm_originale = cm.collect
cm.collect = lambda *a, **k: [dict(s) for s in sessioni_correnti]
cm.allocate_real_cost = lambda *a, **k: None

args = types.SimpleNamespace(
    endpoint=f"http://127.0.0.1:{porta}", token=None, base=".", config=None,
    idle_gap=300.0, dry_run=False, show_payload=False, once=True, reset=False)
config = {"subscription": {"currency": "EUR"}, "billing": {"mode": "subscription"}}

httpd = None
try:
    print("\nRaccoglitore SPENTO")
    print("-" * 72)
    stato = ca.carica_stato(stato_path)
    ok = ca.un_giro(args, config, stato, stato_path)
    verifica("l'invio fallisce senza far esplodere l'agente", ok, False)
    verifica("il segnalibro resta vuoto", stato["inviate"], {})
    su_disco = ca.carica_stato(stato_path)
    verifica("e resta vuoto anche su disco", su_disco.get("inviate"), {})
    # L'identificativo di postazione invece si conserva subito, altrimenti ogni
    # tentativo fallito ne genererebbe uno nuovo.
    verifica("l'identificativo di postazione e' gia' salvato",
             bool(su_disco.get("machine")), True)
    macchina = su_disco["machine"]

    print("\nRaccoglitore ACCESO")
    print("-" * 72)
    httpd = avvia_raccoglitore(store, porta)
    ok = ca.un_giro(args, config, stato, stato_path)
    verifica("ora l'invio riesce", ok, True)
    verifica("niente e' andato perso: due sessioni in archivio",
             store.query("SELECT COUNT(*) AS n FROM sessions")[0]["n"], 2)
    verifica("il costo e' quello giusto",
             store.query("SELECT SUM(cost) AS c FROM sessions")[0]["c"], 30.0)
    verifica("il segnalibro ora e' aggiornato",
             sorted(ca.carica_stato(stato_path)["inviate"]), ["s-1", "s-2"])
    verifica("la postazione non e' cambiata",
             ca.carica_stato(stato_path)["machine"], macchina)

    print("\nSecondo giro a vuoto")
    print("-" * 72)
    ok = ca.un_giro(args, config, stato, stato_path)
    verifica("non rispedisce quello che e' gia' arrivato", ok, True)
    verifica("e l'archivio non raddoppia",
             store.query("SELECT COUNT(*) AS n FROM sessions")[0]["n"], 2)

    print("\nIl raccoglitore cade mentre una sessione cresce")
    print("-" * 72)
    httpd.shutdown()
    httpd.server_close()
    httpd = None
    sessioni_correnti = [finta_sessione("s-1", ORA - 100, 10.0),
                         finta_sessione("s-2", ORA + 500, 99.0),   # cresciuta
                         finta_sessione("s-3", ORA + 600, 7.0)]    # nuova
    ok = ca.un_giro(args, config, stato, stato_path)
    verifica("l'invio fallisce di nuovo", ok, False)
    verifica("il segnalibro non avanza sulla sessione cresciuta",
             ca.carica_stato(stato_path)["inviate"].get("s-2") < ORA + 500, True)
    verifica("e la nuova sessione non risulta spedita",
             "s-3" in ca.carica_stato(stato_path)["inviate"], False)

    print("\nIl raccoglitore torna")
    print("-" * 72)
    httpd = avvia_raccoglitore(store, porta)
    ok = ca.un_giro(args, config, stato, stato_path)
    verifica("recupera quello che era rimasto indietro", ok, True)
    verifica("tre sessioni in archivio",
             store.query("SELECT COUNT(*) AS n FROM sessions")[0]["n"], 3)
    # 10 + 99 (cresciuta, sostituita) + 7
    verifica("i valori sono quelli dell'ultimo invio, non sommati",
             store.query("SELECT SUM(cost) AS c FROM sessions")[0]["c"], 116.0)

    print("\nIl confine regge anche nel percorso di recupero")
    print("-" * 72)
    grezzo = b""
    for suff in ("", "-wal", "-shm"):
        p = store.path + suff
        if os.path.exists(p):
            with open(p, "rb") as fh:
                grezzo += fh.read()
    verifica("nessun titolo o percorso e' finito in archivio",
             b"SEGRETO" in grezzo, False)

finally:
    if httpd:
        httpd.shutdown()
        httpd.server_close()
    cm.collect = cm_originale
    store.con.close()
    shutil.rmtree(cartella, ignore_errors=True)

falliti = [n for ok_, n in esiti if not ok_]
print("\n" + "=" * 72)
if falliti:
    print(f"{len(falliti)} PROVE FALLITE su {len(esiti)}:")
    for n in falliti:
        print(f"  - {n}")
    sys.exit(1)
print(f"tutte le {len(esiti)} prove di resilienza superate")
