#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Prove di non regressione per cm_collector.

Coprono i tre punti dove il conteggio si sbaglia facilmente e il confine di
riservatezza, che e' l'unica garanzia che si puo' esibire in sede di verifica.

    python test_collector.py

Nessuna dipendenza: si esegue com'e'.
"""

from __future__ import annotations

import os
import sys
import tempfile

import cm_collector as cc

esiti: list[tuple[bool, str]] = []


def verifica(nome: str, ottenuto, atteso) -> None:
    if isinstance(atteso, float) or isinstance(ottenuto, float):
        buono = abs(float(ottenuto) - float(atteso)) < 1e-9
    else:
        buono = ottenuto == atteso
    esiti.append((buono, nome))
    segno = "ok  " if buono else "FALLITO"
    print(f"  {segno}  {nome:<44} {ottenuto!r}"
          + ("" if buono else f"   atteso {atteso!r}"))


def store(livello: str = "nominativo") -> cc.Store:
    d = tempfile.mkdtemp()
    privacy = cc.make_privacy(livello, os.path.join(d, "k.key"))
    return cc.Store(os.path.join(d, "t.db"), privacy, livello)


def punto(metric, valore, temporalita, ts, utente, sessione, tipo=None):
    return {
        "ts": ts, "metric": metric, "value": valore, "kind": "sum",
        "temporality": temporalita, "user_key": utente, "session_id": sessione,
        "org_id": "org-1", "model": "claude-opus-5", "type_attr": tipo,
        "attrs": {"user.email": utente, "user.id": "hash-" + str(utente),
                  "session.id": sessione, "type": tipo, "model": "claude-opus-5"},
    }


# --------------------------------------------------------------------------- #

print("\nConteggio")
print("-" * 72)

# Gli esportatori OTLP ritentano quando il raccoglitore non risponde. Senza
# vincolo di unicita' lo stesso datapoint verrebbe contato due volte.
st = store()
st.add([punto("claude_code.cost.usage", 3.42, 1, 1000.0, "a@x.it", "s1")])
nuovi, dupl = st.add([punto("claude_code.cost.usage", 3.42, 1, 1000.0, "a@x.it", "s1")])
verifica("un rinvio identico non viene riscritto", (nuovi, dupl), (0, 1))
verifica("e non viene contato due volte",
         cc.aggregate(st, "claude_code.cost.usage")[0][1], 3.42)

st.add([punto("claude_code.cost.usage", 1.58, 1, 2000.0, "a@x.it", "s1")])
verifica("delta successivi si sommano",
         cc.aggregate(st, "claude_code.cost.usage")[0][1], 5.00)

# Con temporalita' cumulativa ogni invio porta il totale dall'avvio del
# processo, non l'incremento: sommarli gonfia il conto.
st2 = store()
for ts, v in ((1000.0, 10.0), (2000.0, 25.0), (3000.0, 40.0)):
    st2.add([punto("claude_code.cost.usage", v, 2, ts, "b@x.it", "s9")])
verifica("cumulative: si prende il picco, non la somma",
         cc.aggregate(st2, "claude_code.cost.usage")[0][1], 40.00)
for ts, v in ((1000.0, 5.0), (2000.0, 12.0)):
    st2.add([punto("claude_code.cost.usage", v, 2, ts, "b@x.it", "s10")])
verifica("cumulative: i picchi si sommano fra serie",
         cc.aggregate(st2, "claude_code.cost.usage")[0][1], 52.00)

# Ogni gruppo deve avere il proprio totale. Con una sola postazione un errore
# qui non si vede: si vede solo quando il team e' piu' di uno.
st.add([punto("claude_code.cost.usage", 2.00, 1, 3000.0, "c@x.it", "s2")])
per_utente = dict(cc.aggregate(st, "claude_code.cost.usage", by="user"))
verifica("ogni postazione ha il proprio totale (1)", per_utente["a@x.it"], 5.00)
verifica("ogni postazione ha il proprio totale (2)", per_utente["c@x.it"], 2.00)
verifica("il totale complessivo torna",
         cc.aggregate(st, "claude_code.cost.usage")[0][1], 7.00)

# --------------------------------------------------------------------------- #

print("\nRighe per il pannello")
print("-" * 72)

st3 = store()
st3.add([punto("claude_code.token.usage", 5000, 1, 1000.0, "a@x.it", "s1", "cacheRead")])
st3.add([punto("claude_code.token.usage", 300, 1, 1000.0, "a@x.it", "s1", "output")])
st3.add([punto("claude_code.token.usage", 42, 1, 1000.0, "a@x.it", "s1", "input")])
st3.add([punto("claude_code.cost.usage", 1.00, 1, 1000.0, "a@x.it", "s1")])
st3.add([punto("claude_code.session.count", 1, 1, 1000.0, "a@x.it", "s1")])
righe = {r["person"]: r for r in cc.team_rows(st3)}
verifica("token divisi per tipo (cache_read)",
         righe["a@x.it"]["tokens"]["cache_read"], 5000.0)
verifica("token divisi per tipo (output)", righe["a@x.it"]["tokens"]["output"], 300.0)
verifica("totale token", righe["a@x.it"]["total_tokens"], 5342.0)
verifica("sessioni contate", righe["a@x.it"]["sessions"], 1)
verifica("modelli elencati", righe["a@x.it"]["models"], ["claude-opus-5"])

# --------------------------------------------------------------------------- #

print("\nRiservatezza")
print("-" * 72)

def byte_su_disco(store_: cc.Store) -> bytes:
    """Tutti i byte che l'archivio occupa davvero.

    SQLite gira in modalita' WAL: finche' non viene fatto il checkpoint i dati
    stanno nel file -wal, non nel .db. Guardare solo il .db darebbe un falso
    negativo, cioe' esattamente il modo in cui una garanzia di riservatezza
    diventa finta senza che nessuno se ne accorga.
    """
    grezzo = b""
    for suffisso in ("", "-wal", "-shm"):
        p = store_.path + suffisso
        if os.path.exists(p):
            with open(p, "rb") as fh:
                grezzo += fh.read()
    return grezzo


# La telemetria nativa manda user.email in chiaro e non e' disattivabile sulla
# postazione: il livello si impone qui, prima della scrittura in archivio.
for livello, deve_esserci in (("nominativo", True), ("pseudonimo", False),
                              ("aggregato", False)):
    s = store(livello)
    s.add([punto("claude_code.cost.usage", 1.0, 1, 1000.0, "mario@azienda.it", "s1")])
    s.con.commit()
    trovato = b"mario@azienda.it" in byte_su_disco(s)
    verifica(f"{livello}: indirizzo nei byte dell'archivio", trovato, deve_esserci)

s = store("pseudonimo")
s.add([punto("claude_code.cost.usage", 1.0, 1, 1000.0, "mario@azienda.it", "s1")])
codice = cc.team_rows(s)[0]["person"]
verifica("pseudonimo: il codice ha la forma attesa",
         codice.startswith("p-") and len(codice) == 14, True)

d = tempfile.mkdtemp()
chiave = os.path.join(d, "stessa.key")
a = cc.make_privacy("pseudonimo", chiave)(punto(
    "m", 1, 1, 1.0, "mario@azienda.it", "s"))["user_key"]
b = cc.make_privacy("pseudonimo", chiave)(punto(
    "m", 1, 1, 1.0, "mario@azienda.it", "s"))["user_key"]
c = cc.make_privacy("pseudonimo", os.path.join(tempfile.mkdtemp(), "altra.key"))(
    punto("m", 1, 1, 1.0, "mario@azienda.it", "s"))["user_key"]
verifica("stessa chiave: stesso codice fra riavvii", a == b, True)
verifica("chiave diversa: codice diverso", a != c, True)

s = store("aggregato")
s.add([punto("claude_code.cost.usage", 1.0, 1, 1000.0, "mario@azienda.it", "s1")])
verifica("aggregato: nessuna identita conservata",
         cc.team_rows(s)[0]["person"], "(non identificato)")

# --------------------------------------------------------------------------- #

print("\nDecodifica OTLP")
print("-" * 72)

payload = {"resourceMetrics": [{
    "resource": {"attributes": [
        {"key": "user.email", "value": {"stringValue": "tizio@x.it"}}]},
    "scopeMetrics": [{"metrics": [
        {"name": "claude_code.token.usage", "sum": {
            "aggregationTemporality": 1,
            "dataPoints": [{"timeUnixNano": "1786500000000000000", "asInt": "128000",
                            "attributes": [{"key": "type",
                                            "value": {"stringValue": "cacheRead"}}]}]}},
        {"name": "claude_code.cost.usage", "sum": {
            "aggregationTemporality": 1,
            "dataPoints": [{"timeUnixNano": "1786500000000000000",
                            "asDouble": 3.42, "attributes": []}]}}]}]}]}
punti = list(cc.iter_points(payload))
verifica("datapoint estratti", len(punti), 2)
verifica("asInt letto come numero", punti[0]["value"], 128000.0)
verifica("asDouble letto come numero", punti[1]["value"], 3.42)
verifica("attributi di resource ereditati dal datapoint",
         punti[0]["user_key"], "tizio@x.it")
verifica("attributo di datapoint conservato", punti[0]["type_attr"], "cacheRead")

# --------------------------------------------------------------------------- #

falliti = [n for ok, n in esiti if not ok]
print("\n" + "=" * 72)
if falliti:
    print(f"{len(falliti)} PROVE FALLITE su {len(esiti)}:")
    for n in falliti:
        print(f"  - {n}")
    sys.exit(1)
print(f"tutte le {len(esiti)} prove superate")
