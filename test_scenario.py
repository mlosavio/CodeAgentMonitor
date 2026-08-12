#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Prova di scenario: la catena intera, con piu' postazioni, sopra HTTP vero.

Le prove di test_collector.py chiamano le funzioni direttamente. Questa invece
avvia un raccoglitore vero su una porta libera e ci parla via rete, come fanno
Claude Code e cm_agent: serializzazione, rotte, token, unione delle fonti e
pannello. E' l'unico modo per accorgersi degli errori che stanno fra i pezzi
invece che dentro un pezzo.

Usa un archivio temporaneo: non tocca cm-team.db.

    python test_scenario.py
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
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import cm_collector as cc

esiti: list[tuple[bool, str]] = []
TOKEN = "prova-token-condiviso"


def verifica(nome: str, ottenuto, atteso) -> None:
    if ottenuto is None or atteso is None:
        buono = ottenuto == atteso     # un valore mancante e' un esito, non un errore
    elif isinstance(atteso, float) or isinstance(ottenuto, float):
        buono = abs(float(ottenuto) - float(atteso)) < 1e-6
    else:
        buono = ottenuto == atteso
    esiti.append((buono, nome))
    print(f"  {'ok  ' if buono else 'FALLITO'}  {nome:<50} {ottenuto!r}"
          + ("" if buono else f"   atteso {atteso!r}"))


def porta_libera() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def posta(url: str, corpo: dict, token: str | None = TOKEN) -> tuple[int, dict]:
    dati = json.dumps(corpo).encode("utf-8")
    req = urllib.request.Request(url, dati, {"Content-Type": "application/json"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        return exc.code, {}


def prendi(url: str):
    with urllib.request.urlopen(url, timeout=10) as r:
        grezzo = r.read().decode()
    try:
        return json.loads(grezzo)
    except ValueError:
        return grezzo


# --------------------------------------------------------------------------- #
# Dati delle postazioni simulate
# --------------------------------------------------------------------------- #

ORA = time.time()
GIORNO = 86400.0

# anna: usa molto, ha l'agente -> storico completo
# bruno: usa poco, ha l'agente
# carla: ha solo la telemetria, l'agente non gira sulla sua macchina
# (le altre postazioni pagate non compaiono: non usano lo strumento)
POSTAZIONI = [
    ("m-anna",  "anna@azienda.it",  [
        ("s-a1", "Commessa Alfa", ORA - 90 * GIORNO, 1400.0, 40000.0, 62),
        ("s-a2", "Commessa Beta", ORA - 20 * GIORNO,  900.0, 26000.0, 41),
        ("s-a3", "Interno",       ORA -  2 * GIORNO,  180.0,  5000.0, 12),
    ]),
    ("m-bruno", "bruno@azienda.it", [
        ("s-b1", "Commessa Alfa", ORA - 30 * GIORNO,  260.0,  9000.0, 18),
    ]),
]
SOLO_TELEMETRIA = ("carla@azienda.it", 41.5)


def sessione(sid, progetto, quando, costo, token, msg):
    return {
        "session_id": sid, "project": progetto,
        "start": quando, "end": quando + 3600.0,
        "duration": 3600.0, "active": 2400.0,
        "user_prompts": msg // 3, "assistant_msgs": msg, "tool_calls": msg * 2,
        "cost": costo, "real_cost": 0.0, "billing": "subscription",
        "tokens": {"input": token * 0.001, "output": token * 0.01,
                   "cache_read": token * 0.95, "cache_w5m": token * 0.03,
                   "cache_w1h": token * 0.009},
        "per_month": {time.strftime("%Y-%m", time.localtime(quando)):
                      {"claude-opus-5": {"cost": costo}}},
    }


def datapoint(metrica, valore, utente, sid, tipo=None):
    attrs = [{"key": "user.email", "value": {"stringValue": utente}},
             {"key": "session.id", "value": {"stringValue": sid}}]
    if tipo:
        attrs.append({"key": "type", "value": {"stringValue": tipo}})
    return {"resourceMetrics": [{
        "resource": {"attributes": [
            {"key": "service.name", "value": {"stringValue": "claude-code"}}]},
        "scopeMetrics": [{"metrics": [{"name": metrica, "sum": {
            "aggregationTemporality": 1,
            "dataPoints": [{"timeUnixNano": str(int(ORA * 1e9)),
                            "asDouble": valore, "attributes": attrs}]}}]}]}]}


# --------------------------------------------------------------------------- #

cartella = tempfile.mkdtemp(prefix="cm-scenario-")
db = os.path.join(cartella, "scenario.db")
porta = porta_libera()
base = f"http://127.0.0.1:{porta}"

store = cc.Store(db, cc.make_privacy("pseudonimo", os.path.join(cartella, "k.key")),
                 "pseudonimo")
cc.Handler.store = store
cc.Handler.verbose = False
cc.Handler.token = TOKEN
httpd = ThreadingHTTPServer(("127.0.0.1", porta), cc.Handler)
httpd.daemon_threads = True
threading.Thread(target=httpd.serve_forever, daemon=True).start()
time.sleep(0.3)

try:
    print(f"\nRaccoglitore di prova su {base}  (archivio temporaneo)")

    print("\nAccesso")
    print("-" * 72)
    codice, _ = posta(f"{base}/v1/sessions", {"machine": "x", "sessions": []},
                      token=None)
    verifica("senza token viene rifiutato", codice, 401)
    codice, _ = posta(f"{base}/v1/sessions", {"machine": "x", "sessions": []},
                      token="sbagliato")
    verifica("con token sbagliato viene rifiutato", codice, 401)

    print("\nInvii dalle postazioni")
    print("-" * 72)
    for macchina, utente, sessioni in POSTAZIONI:
        corpo = {
            "agent": "cm-agent/prova", "machine": macchina, "user": utente,
            "attrs": {"user.email": utente},
            "sessions": [sessione(*s) for s in sessioni],
        }
        codice, esito = posta(f"{base}/v1/sessions", corpo)
        verifica(f"{macchina}: sessioni accettate",
                 (codice, esito.get("scritte")), (200, len(sessioni)))

    # una postazione senza agente: manda solo telemetria
    utente_tel, costo_tel = SOLO_TELEMETRIA
    for metrica, val, tipo in (("claude_code.cost.usage", costo_tel, None),
                               ("claude_code.session.count", 4, None),
                               ("claude_code.token.usage", 900000, "cacheRead")):
        codice, _ = posta(f"{base}/v1/metrics",
                          datapoint(metrica, val, utente_tel, "s-c1", tipo))
        verifica(f"{utente_tel}: {metrica.split('.')[-2]} accettata", codice, 200)

    # telemetria anche per anna, che ha gia' i transcript: non deve sommarsi
    posta(f"{base}/v1/metrics",
          datapoint("claude_code.cost.usage", 3.0, "anna@azienda.it", "s-a3"))
    posta(f"{base}/v1/metrics",
          datapoint("claude_code.lines_of_code.count", 1250,
                    "anna@azienda.it", "s-a3"))

    print("\nUnione delle fonti")
    print("-" * 72)
    righe = {r["person"]: r for r in cc.team_rows(store)}
    verifica("tre postazioni distinte", len(righe), 3)
    verifica("tutte pseudonimizzate",
             all(p.startswith("p-") for p in righe), True)
    verifica("nessun indirizzo fra le chiavi",
             any("@" in p for p in righe), False)

    # chi ha i transcript: il costo e' il loro, non la somma con la telemetria
    per_fonte = {}
    for r in righe.values():
        per_fonte.setdefault(r["source"], []).append(r)
    verifica("due postazioni con storico", len(per_fonte.get("transcript", [])), 2)
    verifica("una con la sola telemetria", len(per_fonte.get("telemetria", [])), 1)

    anna = max(righe.values(), key=lambda r: r["cost"])
    verifica("anna: costo dai transcript, senza sommare la telemetria",
             anna["cost"], 1400.0 + 900.0 + 180.0)
    verifica("anna: tre progetti distinti", anna["projects"], 3)
    verifica("anna: le righe di codice arrivano dalla telemetria",
             anna["extra"].get("claude_code.lines_of_code.count"), 1250.0)

    carla = per_fonte["telemetria"][0]
    verifica("carla: costo dalla telemetria", carla["cost"], costo_tel)
    verifica("carla: nessun progetto, i transcript non ci sono",
             carla["projects"], 0)

    print("\nLe tre viste dicono la stessa cifra")
    print("-" * 72)
    atteso = sum(r["cost"] for r in righe.values())
    api = prendi(f"{base}/api/summary")
    verifica("totale via API",
             api["totale"]["totale"]["claude_code.cost.usage"], atteso)
    uniti = cc.totals_uniti(store, "user")
    verifica("per postazione, terminale e pannello concordano",
             all(abs(uniti[p]["claude_code.cost.usage"] - r["cost"]) < 1e-6
                 for p, r in righe.items()), True)
    pagina = prendi(f"{base}/")
    verifica("il cruscotto mostra tutte le postazioni",
             all(p in pagina for p in righe), True)
    verifica("il cruscotto non mostra indirizzi",
             "azienda.it" in pagina, False)

    print("\nSpesa e postazioni ferme")
    print("-" * 72)
    mesi = cc.observed_months(store)[0]
    verifica("la finestra copre i mesi dei transcript", mesi >= 4, True)
    _, riep = cc.team_costs(list(righe.values()),
                            {"seats": 8, "fee_per_seat": 30.0, "currency": "EUR"},
                            mesi, usd_per_unit=1.08)
    verifica("postazioni usate", riep["attive"], 3)
    verifica("ferme dedotte dal dichiarato", riep["dormienti"], 5)
    verifica("spesa totale", riep["pagato_totale"], 8 * 30.0 * mesi)
    verifica("pagata a vuoto", riep["pagato_a_vuoto"], 5 * 30.0 * mesi)

    print("\nRiepilogo leggibile")
    print("-" * 72)
    ordinate = sorted(righe.values(), key=lambda r: r["cost"], reverse=True)
    print(f"  {'postazione':16s} {'fonte':11s} {'listino':>11s} "
          f"{'sess':>5s} {'prog':>5s} {'resa':>7s}")
    _, riep2 = cc.team_costs(ordinate,
                             {"seats": 8, "fee_per_seat": 30.0, "currency": "EUR"},
                             mesi, usd_per_unit=1.08)
    for r in ordinate:
        print(f"  {r['person']:16s} {r['source']:11s} ${r['cost']:>10,.2f} "
              f"{r['sessions']:>5d} {r['projects']:>5d} {r['ratio']:>6.1f}x")
    print(f"  {'':16s} {'':11s} {'':>11s} "
          f"{riep2['attive']}/{riep2['seats']} usate, "
          f"{riep2['dormienti']} ferme = {riep2['pagato_a_vuoto']:.0f} EUR")

finally:
    httpd.shutdown()
    httpd.server_close()
    store.con.close()
    shutil.rmtree(cartella, ignore_errors=True)

falliti = [n for ok, n in esiti if not ok]
print("\n" + "=" * 72)
if falliti:
    print(f"{len(falliti)} PROVE FALLITE su {len(esiti)}:")
    for n in falliti:
        print(f"  - {n}")
    sys.exit(1)
print(f"tutte le {len(esiti)} prove di scenario superate")
