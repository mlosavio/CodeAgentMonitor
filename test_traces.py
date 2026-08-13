#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Prove sui turni: come le richieste vengono raggruppate, e cosa costano.

Il raggruppamento in turni ha tre regole che non si vedono guardando il codice
e che, sbagliate, non fanno crashare niente — cambiano solo i numeri:

  1. il criterio e' il timestamp, non la posizione: Claude Code riemette
     interi segmenti di storia migliaia di righe dopo, e per posizione
     finirebbero tutti nell'ultimo turno;
  2. un prompt di sidechain nel transcript principale NON apre un turno: e'
     l'orchestratore che parla a un subagent dentro un turno gia' aperto;
  3. i turni di un subagent non sono turni della conversazione: il loro
     consumo va sommato al turno del padre che li conteneva.

Qui si costruiscono transcript finti che mettono alla prova esattamente
quelle tre regole, piu' la contabilita' che ci sta sopra.

    python test_traces.py
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import sys
import tempfile

import claude_monitor as cm

try:  # console Windows: senza questo l'output rediretto muore sugli accenti
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover
    pass


esiti: list[tuple[bool, str]] = []
SID = "11111111-2222-3333-4444-555555555555"
T0 = dt.datetime(2026, 3, 2, 10, 0, 0, tzinfo=dt.timezone.utc)

PRICING = {
    "models": {"claude-opus-5": {"in": 5.0, "out": 25.0},
               "claude-haiku-4-5": {"in": 1.0, "out": 5.0}},
    "cache_multipliers": {"read": 0.10, "write_5m": 1.25, "write_1h": 2.00},
    "server_tools": {"web_search_request": 0.01, "web_fetch_request": 0.0},
    "aliases": {}, "free_models": [],
    # Queste prove riguardano i transcript di Claude Code. Copilot legge
    # dallo storage di VS Code della macchina vera, che non ha niente a che
    # fare con le cartelle temporanee costruite qui: acceso, ci farebbe
    # trovare sessioni che nessuna prova ha scritto.
    "copilot": {"enabled": False},
}


def verifica(nome: str, ottenuto, atteso) -> None:
    ok = ottenuto == atteso
    esiti.append((ok, nome))
    segno = "ok  " if ok else "FALLITA"
    print(f"  {segno}  {nome:<52} {ottenuto!r}"
          + ("" if ok else f"   atteso {atteso!r}"))


def vero(nome: str, condizione) -> None:
    verifica(nome, bool(condizione), True)


def quasi(nome: str, ottenuto: float, atteso: float, eps: float = 1e-9) -> None:
    ok = abs(ottenuto - atteso) <= eps
    esiti.append((ok, nome))
    print(f"  {'ok  ' if ok else 'FALLITA'}  {nome:<52} {ottenuto!r}"
          + ("" if ok else f"   atteso {atteso!r}"))


# --------------------------------------------------------------------------- #
# Costruzione di transcript finti
# --------------------------------------------------------------------------- #


def quando(secondi: float) -> str:
    return (T0 + dt.timedelta(seconds=secondi)).isoformat().replace("+00:00", "Z")


def prompt(sec, testo, sidechain=False) -> dict:
    riga = {"type": "user", "sessionId": SID, "timestamp": quando(sec),
            "cwd": "C:\\lavoro\\progetto",
            "message": {"content": [{"type": "text", "text": testo}]}}
    if sidechain:
        riga["isSidechain"] = True
    return riga


def risposta(sec, req, testo="", tools=(), model="claude-opus-5",
             inp=100, out=200, cr=0, cw1h=0) -> dict:
    blocchi = []
    if testo:
        blocchi.append({"type": "text", "text": testo})
    for tid, nome, args in tools:
        blocchi.append({"type": "tool_use", "id": tid, "name": nome, "input": args})
    return {"type": "assistant", "sessionId": SID, "timestamp": quando(sec),
            "requestId": req,
            "message": {"id": "msg-" + req, "model": model, "content": blocchi,
                        "usage": {"input_tokens": inp, "output_tokens": out,
                                  "cache_read_input_tokens": cr,
                                  "cache_creation": {"ephemeral_5m_input_tokens": 0,
                                                     "ephemeral_1h_input_tokens": cw1h}}}}


def risultato(sec, tid, testo="fatto", errore=False) -> dict:
    blocco = {"type": "tool_result", "tool_use_id": tid, "content": testo}
    if errore:
        blocco["is_error"] = True
    return {"type": "user", "sessionId": SID, "timestamp": quando(sec),
            "message": {"content": [blocco]}}


def interruzione(sec) -> dict:
    return {"type": "user", "sessionId": SID, "timestamp": quando(sec),
            "message": {"content": [{"type": "text",
                                     "text": "[Request interrupted by user]"}]}}


def scrivi(base: str, righe: list[dict], subagent: str | None = None) -> str:
    cartella = os.path.join(base, "c--lavoro--progetto")
    if subagent:
        cartella = os.path.join(cartella, SID, "subagents")
        nome = f"agent-{subagent}.jsonl"
    else:
        nome = f"{SID}.jsonl"
    os.makedirs(cartella, exist_ok=True)
    path = os.path.join(cartella, nome)
    with open(path, "w", encoding="utf-8") as fh:
        for r in righe:
            fh.write(json.dumps(r) + "\n")
    return path


def sessione_di(base: str) -> dict:
    sessioni = cm.collect(base, PRICING, use_cache=False, idle_gap=300, quiet=True)
    return sessioni[0] if sessioni else {}


# --------------------------------------------------------------------------- #
# Le prove
# --------------------------------------------------------------------------- #


def prova_confini(base):
    print("\nDue prompt, due turni — e ogni richiesta nel suo")
    scrivi(base, [
        prompt(0, "prima domanda"),
        risposta(5, "req-1", "ecco", [("t1", "Read", {"file": "a.py"})]),
        risultato(7, "t1"),
        risposta(9, "req-2", "finito"),
        prompt(100, "seconda domanda"),
        risposta(105, "req-3", "ok", out=400),
    ])
    s = sessione_di(base)
    tr = s["traces"]
    verifica("turni riconosciuti", len(tr), 2)
    verifica("prompt del primo turno", tr[0]["prompt"], "prima domanda")
    verifica("richieste nel primo turno", tr[0]["requests"], 2)
    verifica("strumenti nel primo turno", tr[0]["tools"], 1)
    verifica("richieste nel secondo turno", tr[1]["requests"], 1)
    verifica("nomi degli strumenti", tr[0]["tool_names"], ["Read"])
    quasi("durata del primo turno", tr[0]["duration"], 9.0)
    quasi("il costo dei turni fa quello della sessione",
          sum(t["cost"] for t in tr), s["cost"])
    verifica("span contati come ProxyAgent", tr[0]["spans"], 1 + 2 + 1)


def prova_riemissione(base):
    print("\nStoria riemessa in fondo: torna nel turno in cui e' nata")
    scrivi(base, [
        prompt(0, "prima domanda"),
        risposta(5, "req-1", "ecco", out=1000),
        prompt(100, "seconda domanda"),
        risposta(105, "req-2", "ok", out=10),
        # compattazione / --resume: la riga vecchia ricompare in coda, con il
        # suo timestamp originale. Per posizione finirebbe nel secondo turno.
        risposta(5, "req-1", "ecco", out=1000),
    ])
    s = sessione_di(base)
    tr = s["traces"]
    verifica("i turni restano due", len(tr), 2)
    verifica("la riga riemessa non si duplica", tr[0]["requests"], 1)
    verifica("e non finisce nell'ultimo turno", tr[1]["requests"], 1)
    verifica("output del primo turno", tr[0]["tokens"]["output"], 1000)
    verifica("output del secondo turno", tr[1]["tokens"]["output"], 10)


def prova_streaming(base):
    print("\nRighe di streaming dello stesso messaggio: una richiesta sola")
    scrivi(base, [
        prompt(0, "domanda"),
        risposta(5, "req-1", "parziale", out=50),
        risposta(6, "req-1", "parziale piu' lunga", out=120),
        risposta(7, "req-1", "completa", out=300),
    ])
    tr = sessione_di(base)["traces"]
    verifica("una sola richiesta", tr[0]["requests"], 1)
    verifica("si tiene il massimo per campo", tr[0]["tokens"]["output"], 300)


def prova_sidechain(base):
    print("\nPrompt di sidechain nel transcript principale: non apre un turno")
    scrivi(base, [
        prompt(0, "domanda vera"),
        risposta(5, "req-1", "lancio un agente", [("t1", "Agent", {})]),
        prompt(6, "istruzioni per il subagent", sidechain=True),
        risultato(60, "t1"),
        risposta(65, "req-2", "ho finito"),
    ])
    s = sessione_di(base)
    verifica("un turno solo", len(s["traces"]), 1)
    verifica("con dentro tutte e due le richieste", s["traces"][0]["requests"], 2)
    verifica("il prompt di sidechain resta contato a parte",
             s["subagent_prompts"], 1)


def prova_interruzione(base):
    print("\nInterruzione: segnata sul turno giusto")
    scrivi(base, [
        prompt(0, "prima domanda"),
        risposta(5, "req-1", "sto lavorando"),
        interruzione(8),
        prompt(100, "seconda domanda"),
        risposta(105, "req-2", "ok"),
    ])
    tr = sessione_di(base)["traces"]
    verifica("il primo turno risulta interrotto", tr[0]["interrupted"], True)
    verifica("il secondo no", tr[1]["interrupted"], False)
    verifica("l'interruzione non conta come prompt",
             [t["prompt"] for t in tr],
             ["prima domanda", "seconda domanda"])


def prova_prima_del_prompt(base):
    print("\nRichieste prima di qualsiasi prompt: non si perdono")
    scrivi(base, [
        risposta(1, "req-0", "avvio automatico", out=90),
        prompt(10, "domanda"),
        risposta(15, "req-1", "risposta", out=10),
    ])
    tr = sessione_di(base)["traces"]
    verifica("un turno in piu', senza prompt", len(tr), 2)
    verifica("il primo non ha prompt", tr[0]["prompt"], None)
    verifica("e si tiene la sua richiesta", tr[0]["tokens"]["output"], 90)
    verifica("numerazione progressiva", [t["n"] for t in tr], [1, 2])


def prova_subagent(base):
    print("\nTurni di un subagent: sommati al turno del padre")
    scrivi(base, [
        prompt(0, "prima domanda"),
        risposta(5, "req-1", "lancio un agente", [("t1", "Agent", {})]),
        risultato(80, "t1"),
        risposta(85, "req-2", "riporto"),
        prompt(200, "seconda domanda"),
        risposta(205, "req-3", "ok"),
    ])
    scrivi(base, [
        prompt(10, "fai questa cosa", sidechain=True),
        risposta(20, "req-s1", "sto cercando", [("s1", "Grep", {})],
                 model="claude-haiku-4-5", out=700),
        risultato(30, "s1"),
        risposta(40, "req-s2", "trovato", model="claude-haiku-4-5", out=300),
    ], subagent="uno")

    s = sessione_di(base)
    tr = s["traces"]
    verifica("i turni della conversazione restano due", len(tr), 2)
    verifica("il subagent e' finito nel primo turno", tr[0]["subagents"], 1)
    verifica("le sue richieste sono contate li'", tr[0]["requests"], 2 + 2)
    verifica("e i suoi strumenti", tr[0]["tools"], 1 + 1)
    verifica("con il suo modello", "claude-haiku-4-5" in tr[0]["per_model"], True)
    verifica("il secondo turno resta pulito", tr[1]["subagents"], 0)
    quasi("il costo dei turni fa ancora quello della sessione",
          sum(t["cost"] for t in tr), s["cost"])


def prova_cache_e_mediana(base):
    print("\nCache hit e durata mediana")
    scrivi(base, [
        prompt(0, "uno"),
        risposta(10, "req-1", "a", inp=100, out=10, cr=900, cw1h=0),
        prompt(100, "due"),
        risposta(160, "req-2", "b", inp=100, out=10, cr=0, cw1h=0),
        prompt(300, "tre"),
        risposta(320, "req-3", "c", inp=100, out=10, cr=900, cw1h=0),
    ])
    s = sessione_di(base)
    tr = s["traces"]
    quasi("cache hit del primo turno", tr[0]["cache_hit"], 0.9)
    quasi("cache hit del secondo", tr[1]["cache_hit"], 0.0)
    quasi("cache hit della sessione", s["cache_hit"], 1800 / 2100)
    quasi("durata mediana dei turni", s["turn_median"], 20.0)
    verifica("mediana di una lista vuota", cm.median([]), None)
    quasi("mediana di due valori", cm.median([10, 20]), 15.0)
    verifica("niente cache hit senza token", cm.cache_hit(cm.new_tok()), None)


def prova_span(base):
    print("\nSpan: la latenza di una richiesta e la durata di uno strumento")
    scrivi(base, [
        prompt(0, "domanda"),
        risposta(5, "req-1", "leggo", [("t1", "Read", {"file": "a.py"})]),
        risultato(12, "t1", "contenuto del file"),
        risposta(20, "req-2", "scrivo", [("t2", "Write", {"file": "b.py"})]),
        risultato(21, "t2", "errore di scrittura", errore=True),
        risposta(30, "req-3", "finito"),
    ])
    sess, trace, spans, msgs = cm.load_trace(base, SID, 1, PRICING)
    verifica("radice + 3 richieste + 2 strumenti", len(spans), 6)
    verifica("la radice e' l'interazione", spans[0]["name"], "interaction")
    quasi("la radice dura quanto il turno", spans[0]["duration"], 30.0)

    llm = [s for s in spans if s["type"] == "llm"]
    quasi("la prima richiesta parte dal prompt", llm[0]["duration"], 5.0)
    quasi("la seconda parte dal risultato che l'ha sbloccata",
          llm[1]["duration"], 20.0 - 12.0)

    tool = {s["name"]: s for s in spans if s["type"] == "tool"}
    quasi("lo strumento dura dall'invocazione al risultato",
          tool["tool:Read"]["duration"], 7.0)
    verifica("lo strumento fallito e' segnato", tool["tool:Write"]["ok"], False)
    verifica("gli argomenti dello strumento ci sono",
             "a.py" in tool["tool:Read"]["args"], True)
    verifica("e il risultato pure",
             tool["tool:Read"]["detail"], "contenuto del file")
    verifica("gli strumenti stanno sotto la loro richiesta",
             [s["depth"] for s in spans if s["type"] == "tool"], [2, 2])
    verifica("i messaggi del turno tornano in ordine",
             [m["kind"] for m in msgs][0], "prompt")


def prova_finestra_del_turno(base):
    print("\nGli span di un turno non pescano dal turno dopo")
    scrivi(base, [
        prompt(0, "uno"),
        risposta(5, "req-1", "a"),
        prompt(50, "due"),
        risposta(55, "req-2", "b"),
        risposta(60, "req-3", "c"),
    ])
    _, _, spans1, _ = cm.load_trace(base, SID, 1, PRICING)
    _, _, spans2, _ = cm.load_trace(base, SID, 2, PRICING)
    verifica("primo turno: radice + una richiesta", len(spans1), 2)
    verifica("secondo turno: radice + due richieste", len(spans2), 3)


def prova_idempotenza(base):
    print("\nRicalcolare non cambia il risultato")
    scrivi(base, [
        prompt(0, "uno"),
        risposta(5, "req-1", "a", [("t1", "Read", {})]),
        prompt(50, "due"),
        risposta(55, "req-2", "b"),
    ])
    s = sessione_di(base)
    prima = (s["traces_n"], s["spans_n"], round(s["cost"], 9),
             [t["requests"] for t in s["traces"]])
    cm.finalize(s, PRICING, 300)
    cm.finalize(s, PRICING, 300)
    dopo = (s["traces_n"], s["spans_n"], round(s["cost"], 9),
            [t["requests"] for t in s["traces"]])
    verifica("due finalize di fila danno lo stesso", dopo, prima)


def prova_ricerca(base):
    print("\nRicerca sui turni")
    scrivi(base, [
        prompt(0, "sistemare la riconciliazione"),
        risposta(5, "req-1", "ok", [("t1", "PowerShell", {})]),
        prompt(50, "tutt'altro argomento"),
        risposta(55, "req-2", "ok"),
    ])
    sessioni = cm.collect(base, PRICING, use_cache=False, idle_gap=300, quiet=True)
    verifica("tutti i turni", len(cm.flatten_traces(sessioni)), 2)
    verifica("per parola del prompt",
             len(cm.flatten_traces(sessioni, "riconcil")), 1)
    verifica("per nome di strumento",
             len(cm.flatten_traces(sessioni, "powershell")), 1)
    verifica("per progetto", len(cm.flatten_traces(sessioni, "progetto")), 2)
    verifica("senza riscontri", len(cm.flatten_traces(sessioni, "zzz")), 0)
    verifica("dal piu' recente",
             [t["prompt"] for t in cm.flatten_traces(sessioni)][0],
             "tutt'altro argomento")


def prova_clip():
    print("\nArgomenti e risultati accorciati")
    grosso = {"type": "image", "source": {"type": "base64", "data": "A" * 40000}}
    testo = cm.clip_blob(grosso, 4000)
    verifica("il base64 diventa una misura", "KB di dati" in testo, True)
    verifica("e sparisce dal testo", "AAAA" in testo, False)
    verifica("il testo normale passa intero",
             cm.clip_blob("due parole", 4000), "due parole")
    verifica("il testo lungo viene tagliato",
             len(cm.clip_blob("x" * 9000, 4000)) <= 4001, True)
    verifica("None diventa vuoto", cm.clip_blob(None, 100), "")


def prova_contesto_esteso(base):
    """PF08: i modelli a finestra estesa si riconoscono dai numeri, non dal nome."""
    print("\nRichieste che nella finestra standard non ci starebbero")
    scrivi(base, [
        prompt(0, "una domanda breve"),
        risposta(5, "req-1", inp=1000, out=200, cr=50_000),
        prompt(60, "e una su un contesto enorme"),
        risposta(65, "req-2", inp=1000, out=5000, cr=500_000),
        prompt(120, "e un'altra"),
        risposta(125, "req-3", inp=1000, out=1000, cr=300_000),
    ])
    s = sessione_di(base)
    ce = s["contesto_esteso"]
    verifica("due richieste oltre la finestra", ce["richieste"], 2)
    verifica("la prima, che ci sta dentro, non conta",
             ce["richieste"] < s["assistant_msgs"], True)
    verifica("senza rapporto dichiarato non si stima niente", ce["extra"], 0.0)
    verifica("e lo si dice", ce["dichiarato"], False)

    # Il rincaro dichiarato non entra nei totali: si affianca.
    caro = dict(PRICING, long_context={"in": 2.0, "out": 1.5})
    s2 = cm.collect(base, caro, use_cache=False, idle_gap=300, quiet=True)[0]
    verifica("col rapporto dichiarato c'e' un maggiorato",
             s2["contesto_esteso"]["extra"] > 0, True)
    verifica("ed e' dichiarato tale", s2["contesto_esteso"]["dichiarato"], True)
    quasi("ma il costo della sessione non cambia", s2["cost"], s["cost"])

    # Chi non vuole vederlo alza la soglia: nessuna richiesta la supera piu'.
    largo = dict(PRICING, finestra_standard=1_000_000)
    s3 = cm.collect(base, largo, use_cache=False, idle_gap=300, quiet=True)[0]
    verifica("con una finestra piu' grande non c'e' piu' niente da segnalare",
             s3["contesto_esteso"]["richieste"], 0)

    verifica("il contesto e' quello che entra, non quello che esce",
             cm.contesto_di({"input": 10, "cache_read": 5, "cache_w5m": 1,
                             "cache_w1h": 2, "output": 999}), 18)


def main() -> int:
    print("=" * 72)
    print("Prove sui turni")
    print("=" * 72)
    prove = [prova_confini, prova_riemissione, prova_streaming, prova_sidechain,
             prova_interruzione, prova_prima_del_prompt, prova_subagent,
             prova_cache_e_mediana, prova_span, prova_finestra_del_turno,
             prova_idempotenza, prova_ricerca, prova_contesto_esteso]
    for prova in prove:
        tmp = tempfile.mkdtemp(prefix="cm-turni-")
        base = os.path.join(tmp, "projects")
        os.makedirs(base, exist_ok=True)
        try:
            prova(base)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    prova_clip()

    print()
    print("=" * 72)
    fallite = [n for ok, n in esiti if not ok]
    if fallite:
        print(f"{len(fallite)} prove fallite su {len(esiti)}:")
        for n in fallite:
            print("  -", n)
        return 1
    print(f"tutte le {len(esiti)} prove sui turni superate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
