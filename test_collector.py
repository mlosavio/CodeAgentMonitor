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

import json
import os
import sys
import tempfile

import cm_collector as cc

try:  # console Windows: senza questo l'output rediretto muore sugli accenti
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover
    pass

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

print("\nUnione delle due fonti senza doppi conteggi")
print("-" * 72)

# Una postazione che ha sia i transcript sia la telemetria: la stessa sessione
# esiste in entrambe le fonti. Sommarle la conterebbe due volte.
st5 = store()
st5.add_sessions("m-1", [{
    "session_id": "s1", "user": "a@x.it", "project": "Alfa",
    "start": 1000.0, "end": 2000.0, "duration": 1000.0, "active": 900.0,
    "cost": 500.0, "real_cost": 30.0, "billing": "subscription",
    "tokens": {"input": 100, "output": 200, "cache_read": 5000,
               "cache_w5m": 300, "cache_w1h": 100},
    "per_month": {"2026-08": {"claude-opus-5": {"cost": 500.0}}},
}])
# la telemetria vede solo una parte di quella stessa attivita'
st5.add([punto("claude_code.cost.usage", 12.0, 1, 1900.0, "a@x.it", "s1")])
st5.add([punto("claude_code.session.count", 1, 1, 1900.0, "a@x.it", "s1")])
st5.add([punto("claude_code.lines_of_code.count", 420, 1, 1900.0, "a@x.it", "s1")])

righe5 = {r["person"]: r for r in cc.team_rows(st5)}
r5 = righe5["a@x.it"]
verifica("con entrambe le fonti vince il transcript", r5["source"], "transcript")
verifica("il costo NON e' la somma delle due fonti", r5["cost"], 500.0)
verifica("le sessioni non si sommano", r5["sessions"], 1)
verifica("il tempo attivo viene dai transcript", r5["active"], 900.0)
# La scrittura di cache e' divisa per durata nei transcript e no nella
# telemetria: qui si riunisce, altrimenti i due totali non sarebbero comparabili
verifica("cache write 5m e 1h riunite", r5["tokens"]["cache_creation"], 400.0)
verifica("totale token dai transcript", r5["total_tokens"], 5700.0)
# Le righe di codice esistono solo nella telemetria: affiancarle non duplica
verifica("le metriche di sola telemetria si affiancano",
         r5["extra"].get("claude_code.lines_of_code.count"), 420.0)

# Una postazione che ha solo la telemetria: l'agente non gira, ma deve comparire
st5.add([punto("claude_code.cost.usage", 7.0, 1, 1900.0, "b@x.it", "s9")])
righe5 = {r["person"]: r for r in cc.team_rows(st5)}
verifica("chi ha solo la telemetria compare comunque",
         righe5["b@x.it"]["source"], "telemetria")
verifica("e il suo costo viene da li'", righe5["b@x.it"]["cost"], 7.0)
verifica("chi ha solo i transcript resta segnato come tale",
         righe5["a@x.it"]["source"], "transcript")

# Una sessione cresciuta si aggiorna, non si somma
st5.add_sessions("m-1", [{
    "session_id": "s1", "user": "a@x.it", "project": "Alfa",
    "start": 1000.0, "end": 3000.0, "duration": 2000.0, "active": 1500.0,
    "cost": 800.0, "tokens": {}, "per_month": {},
}])
righe5 = {r["person"]: r for r in cc.team_rows(st5)}
verifica("una sessione cresciuta sostituisce, non somma",
         righe5["a@x.it"]["cost"], 800.0)

# Un invio arrivato fuori ordine non riporta indietro il conto
scritte, ignorate = st5.add_sessions("m-1", [{
    "session_id": "s1", "user": "a@x.it", "end": 2500.0, "cost": 111.0,
    "tokens": {}, "per_month": {},
}])
verifica("un invio piu' vecchio viene ignorato", (scritte, ignorate), (0, 1))
verifica("e il conto resta quello buono",
         {r["person"]: r for r in cc.team_rows(st5)}["a@x.it"]["cost"], 800.0)

# Il riepilogo da terminale e la scheda Persone devono dire la stessa cifra.
# Prima non era cosi': il terminale leggeva la sola telemetria e mostrava 0,10
# dollari dove il pannello ne mostrava 2.958. Un pannello e un comando che si
# contraddicono valgono meno di uno solo dei due.
uniti = cc.totals_uniti(st5, "user")
for r in cc.team_rows(st5):
    verifica(f"terminale e pannello concordano sul costo ({r['person']})",
             uniti[r["person"]]["claude_code.cost.usage"], r["cost"])
    verifica(f"...e sulle sessioni ({r['person']})",
             uniti[r["person"]]["claude_code.session.count"], float(r["sessions"]))
verifica("il totale e' la somma delle postazioni",
         cc.totals_uniti(st5, "all")["totale"]["claude_code.cost.usage"],
         sum(r["cost"] for r in cc.team_rows(st5)))
# Gli assi che i transcript non hanno restano quelli della sola telemetria,
# senza fingere di essere altro.
verifica("l'asse modello resta telemetria",
         cc.totals_uniti(st5, "model") == cc.totals(st5, "model"), True)

# --------------------------------------------------------------------------- #

print("\nModello di costo del team")
print("-" * 72)

righe_team = [
    {"person": "anna@x.it",  "cost": 420.0, "sessions": 88, "active": 0.0,
     "tokens": {}, "total_tokens": 0, "models": [], "last": 0.0},
    {"person": "bruno@x.it", "cost": 180.0, "sessions": 41, "active": 0.0,
     "tokens": {}, "total_tokens": 0, "models": [], "last": 0.0},
    {"person": "carla@x.it", "cost": 12.0, "sessions": 3, "active": 0.0,
     "tokens": {}, "total_tokens": 0, "models": [], "last": 0.0},
]
team = {"seats": 8, "fee_per_seat": 30.0, "currency": "EUR"}
arricchite, riep = cc.team_costs(list(righe_team), team, 3, usd_per_unit=1.08)

verifica("ogni postazione costa uguale", arricchite[0]["paid"], 90.0)
verifica("postazioni attive contate", riep["attive"], 3)
# Chi non usa lo strumento non manda telemetria: le dormienti non si osservano,
# si deducono dal numero dichiarato. Senza dichiararlo restano invisibili.
verifica("dormienti dedotte dal dichiarato", riep["dormienti"], 5)
verifica("spesa totale su tutte le postazioni", riep["pagato_totale"], 720.0)
verifica("quota pagata a vuoto", riep["pagato_a_vuoto"], 450.0)
verifica("resa di chi lo usa davvero", round(arricchite[0]["ratio"], 2), 4.32)
verifica("resa complessiva tiene conto delle dormienti",
         round(riep["ratio"], 2), 0.79)

# Senza postazioni dichiarate le colonne di spesa restano spente, invece di
# mostrare uno zero che sembrerebbe "non costa niente".
_, vuoto = cc.team_costs(list(righe_team), {}, 3, usd_per_unit=1.08)
verifica("senza postazioni dichiarate: nessuna spesa", vuoto["pagato_totale"], 0.0)
verifica("senza postazioni dichiarate: nessuna resa", vuoto["ratio"], 0.0)

# Con valute diverse e nessun cambio noto il rapporto non e' calcolabile:
# meglio non mostrarlo che confrontare unita' diverse.
_, senza_cambio = cc.team_costs(list(righe_team), team, 3, usd_per_unit=None)
verifica("valute diverse senza cambio: nessun rapporto", senza_cambio["ratio"], 0.0)

st4 = store()
import time as _t
adesso = _t.time()
st4.add([punto("claude_code.cost.usage", 1.0, 1, adesso - 60 * 86400, "a@x.it", "s1")])
st4.add([punto("claude_code.cost.usage", 1.0, 1, adesso, "a@x.it", "s2")])
verifica("mesi di calendario coperti dai dati",
         cc.observed_months(st4)[0] >= 2, True)

# La finestra va presa da entrambe le tabelle. Con la telemetria accesa oggi e
# transcript di mesi, contare solo la telemetria darebbe un mese di quota
# contro mesi di consumo, e una resa gonfiata nella stessa proporzione.
st6 = store()
st6.add([punto("claude_code.cost.usage", 1.0, 1, adesso, "a@x.it", "s1")])
solo_telemetria = cc.observed_months(st6)[0]
st6.add_sessions("m-1", [{
    "session_id": "vecchia", "user": "a@x.it",
    "start": adesso - 100 * 86400, "end": adesso - 95 * 86400,
    "cost": 10.0, "tokens": {}, "per_month": {},
}])
con_transcript = cc.observed_months(st6)[0]
verifica("con la sola telemetria la finestra e' corta", solo_telemetria, 1)
verifica("i transcript allargano la finestra", con_transcript >= 4, True)

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

# Cancellazione di una persona: si chiede per indirizzo, ma in archivio c'e' il
# codice. Se la riduzione non avvenisse, non si troverebbe niente e sembrerebbe
# fatta — il modo peggiore in cui una cancellazione puo' fallire.
d = tempfile.mkdtemp()
chiave = os.path.join(d, "k.key")
s = cc.Store(os.path.join(d, "t.db"), cc.make_privacy("pseudonimo", chiave),
             "pseudonimo")
s.add([punto("claude_code.cost.usage", 1.0, 1, 1000.0, "mario@azienda.it", "s1"),
       punto("claude_code.cost.usage", 2.0, 1, 1001.0, "lucia@azienda.it", "s2")])
s.add_sessions("m-1", [{"session_id": "s1", "project": "P", "start": 1.0,
                        "end": 2.0, "cost": 5.0}], "mario@azienda.it")
esito = cc.dimentica(s, "mario@azienda.it", chiave)
verifica("cancellazione: indirizzo ridotto al codice",
         esito["chiave"].startswith("p-"), True)
verifica("cancellazione: datapoint tolti", esito["points"], 1)
verifica("cancellazione: sessioni tolte", esito["sessions"], 1)
rimasti = [r["person"] for r in cc.team_rows(s)]
verifica("cancellazione: gli altri restano", len(rimasti), 1)
verifica("cancellazione: non resta traccia del cancellato",
         esito["chiave"] in rimasti, False)

# Senza la chiave giusta make_privacy ne creerebbe una nuova, calcolerebbe un
# codice diverso e cancellerebbe zero righe senza dirlo: deve fermarsi prima.
try:
    cc.dimentica(s, "lucia@azienda.it", os.path.join(d, "inesistente.key"))
    fermato = False
except FileNotFoundError:
    fermato = True
verifica("cancellazione senza chiave: si ferma invece di fingere", fermato, True)
verifica("cancellazione senza chiave: non ha tolto niente",
         len(cc.team_rows(s)), 1)

# --------------------------------------------------------------------------- #

print("\nConfine di cio' che esce dalla macchina (cm_agent)")
print("-" * 72)

import cm_agent as ca



# Una sessione con dentro tutto quello che il parser sa produrre, con valori
# riconoscibili al posto dei campi che non devono uscire. Se uno di questi
# ricompare nel payload serializzato, la prova cade.
SENTINELLE = {
    "title": "SEGRETO-titolo-con-nome-di-persona",
    "first_prompt": "SEGRETO-testo-della-richiesta",
    "cwd": r"C:\SEGRETO\Clienti\AcmeSpa",
    "project_dir": r"C:\SEGRETO\percorso",
    "files": [r"C:\SEGRETO\transcript.jsonl"],
    "git_branch": "SEGRETO-TICKET-4471",
    "agents": {"SEGRETO-agente": 1},
    "ts": [1.0, 2.0],
}
sessione = {
    "session_id": "abc-123", "project": "Progetto", "start": 1.0, "end": 2.0,
    "duration": 1.0, "active": 1.0, "user_prompts": 3, "assistant_msgs": 40,
    "tool_calls": 12, "cost": 1.23, "real_cost": 0.5, "billing": "subscription",
    "tokens": {"input": 10, "output": 20, "nota": "SEGRETO-non-numerico"},
    "per_month": {"2026-08": {"claude-opus-5": {"cost": 1.23}}},
    **SENTINELLE,
}

fuori = ca.payload_sessione(sessione)
serializzato = json.dumps(fuori, ensure_ascii=False)

verifica("nessuna sentinella nel payload", "SEGRETO" in serializzato, False)
verifica("i campi ammessi ci sono tutti",
         sorted(fuori) == sorted(ca.CAMPI_SPEDITI), True)
verifica("i token restano solo numeri", "nota" in fuori["tokens"], False)
verifica("il costo passa", fuori["cost"], 1.23)
verifica("la ripartizione mensile passa", "2026-08" in fuori["per_month"], True)

# Il campo nuovo che nessuno ha aggiunto all'elenco resta a terra: e' il verso
# giusto in cui sbagliare, e il motivo per cui l'elenco e' di inclusioni.
sessione["campo_inventato_domani"] = "SEGRETO-nuovo"
verifica("un campo nuovo non incluso non esce",
         "SEGRETO" in json.dumps(ca.payload_sessione(sessione)), False)

# Il controllo che gira prima di ogni invio, non solo qui.
verifica("il controllo del confine non segnala falsi positivi",
         ca.controlla_confine({"sessions": [fuori]}), [])
verifica("il controllo del confine intercetta un campo di troppo",
         ca.controlla_confine({"sessions": [dict(fuori, cwd="x")]}), ["cwd"])

# Il filtro incrementale: rispedire tutto ogni volta e' sbagliato quanto non
# rispedire una sessione cresciuta.
sessioni = [{"session_id": "a", "end": 100.0}, {"session_id": "b", "end": 200.0}]
verifica("senza stato si spedisce tutto",
         len(ca.da_spedire(sessioni, {})), 2)
verifica("gia' spedite e non cresciute: niente",
         len(ca.da_spedire(sessioni, {"a": 100.0, "b": 200.0})), 0)
verifica("una sessione cresciuta si rispedisce",
         [s["session_id"] for s in ca.da_spedire(sessioni, {"a": 50.0, "b": 200.0})],
         ["a"])

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
