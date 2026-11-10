#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Prove sul flusso: cosa parte verso il modello e cosa resta sulla macchina.

I numeri di questa vista non fanno crashare niente quando sono sbagliati:
restano plausibili. I modi di sbagliarli sono quattro, e sono tutti silenziosi:

  1. lo stesso contenuto sta nel transcript due volte — come riga della
     conversazione e dentro una richiesta registrata — e contarlo due volte
     raddoppia i risultati degli strumenti;
  2. contare solo le richieste registrate ne perde meta', perche' Claude Code
     scrive un blob solo per i messaggi che non sono gia' una riga;
  3. una modifica produce in locale il file intero e manda al modello una
     riga: confondere le due misure cancella proprio la differenza cercata;
  4. un transcript senza richieste registrate deve dire «non misurato», non
     zero.

    python test_flusso.py
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import sys
import tempfile

import cam
import cam_flusso as F

try:  # console Windows: senza questo l'output rediretto muore sugli accenti
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover
    pass


esiti: list[tuple[bool, str]] = []
SID = "11111111-2222-3333-4444-555555555555"
T0 = dt.datetime(2026, 3, 2, 10, 0, 0, tzinfo=dt.timezone.utc)

PRICING = {
    "models": {"claude-opus-5": {"in": 5.0, "out": 25.0}},
    "cache_multipliers": {"read": 0.10, "write_5m": 1.25, "write_1h": 2.00},
    "server_tools": {"web_search_request": 0.01, "web_fetch_request": 0.0},
    "aliases": {}, "free_models": [],
    "copilot": {"enabled": False},
}


def verifica(nome: str, ottenuto, atteso) -> None:
    ok = ottenuto == atteso
    esiti.append((ok, nome))
    segno = "ok  " if ok else "FALLITA"
    print(f"  {segno}  {nome:<56} {ottenuto!r}"
          + ("" if ok else f"   atteso {atteso!r}"))


def vero(nome: str, condizione) -> None:
    verifica(nome, bool(condizione), True)


# --------------------------------------------------------------------------- #
# Righe finte, nella forma che Claude Code scrive dalla 2.1.309
# --------------------------------------------------------------------------- #


def quando(secondi: float) -> str:
    return (T0 + dt.timedelta(seconds=secondi)).isoformat().replace("+00:00", "Z")


def shape(h="S1", n=1000) -> dict:
    return {"type": "api-request-shape", "shapeHash": h,
            "shape": {"system": [{"type": "text", "text": "x" * n}], "tools": []}}


def blob(h, ruolo, blocchi) -> dict:
    return {"type": "api-request-blob", "hash": h,
            "message": {"role": ruolo, "content": blocchi}}


def richiesta(sec, rid, tail, base=None, keep=0, sh="S1", fonte="sdk") -> dict:
    r = {"type": "api-request", "id": rid, "timestamp": quando(sec),
         "shapeHash": sh, "querySource": fonte, "keep": keep, "tail": list(tail)}
    if base:
        r["base"] = base
    return r


def testo(t) -> dict:
    return {"type": "text", "text": t}


def utente(sec, blocchi, meta=False, locale=None) -> dict:
    r = {"type": "user", "sessionId": SID, "timestamp": quando(sec),
         "cwd": "C:\\lavoro\\progetto", "message": {"role": "user", "content": blocchi}}
    if meta:
        r["isMeta"] = True
    if locale is not None:
        r["toolUseResult"] = locale
    return r


def assistente(sec, rid, blocchi, out=10) -> dict:
    return {"type": "assistant", "sessionId": SID, "timestamp": quando(sec),
            "requestRef": rid, "requestId": "req-" + rid,
            "message": {"id": "msg-" + rid, "role": "assistant",
                        "model": "claude-opus-5", "content": blocchi,
                        "usage": {"input_tokens": 100, "output_tokens": out}}}


def uso(tid, nome, args=None) -> dict:
    return {"type": "tool_use", "id": tid, "name": nome, "input": args or {}}


def reso(tid, contenuto, errore=False) -> dict:
    b = {"type": "tool_result", "tool_use_id": tid, "content": contenuto}
    if errore:
        b["is_error"] = True
    return b


def misura(righe, subagent=False) -> dict:
    fl = F.Flusso(subagent=subagent)
    for r in righe:
        fl.ingest(r)
    return F.somma([fl.snapshot()])


# --------------------------------------------------------------------------- #
# Prove
# --------------------------------------------------------------------------- #


def prova_origini():
    """Ogni blocco finisce sotto chi l'ha prodotto, e una volta sola."""
    print("\nDa dove viene quello che parte")
    righe = [
        shape("S1", 1000),
        # il primo messaggio viaggia coi promemoria: e' un blob
        blob("B1", "user", [testo("<system-reminder>" + "r" * 83 + "</system-reminder>"),
                            testo("ciao mondo")]),
        blob("B2", "system", [testo("e" * 50)]),
        # ...ed e' anche una riga della conversazione, senza i promemoria
        utente(0, [testo("ciao mondo")]),
        richiesta(1, "R1", ["B1", "B2"]),
        assistente(3, "R1", [testo("a" * 30), uso("T1", "Read", {"file_path": "abcdef"})]),
        utente(4, [reso("T1", "c" * 400)], locale={"file": {"content": "c" * 400}}),
        # gli hash H3 e H4 non hanno blob: rimandano alle due righe qui sopra
        richiesta(5, "R2", ["H3", "H4"], base="R1", keep=2),
        assistente(8, "R2", [testo("b" * 20)]),
    ]
    f = misura(righe)
    verifica("richieste contate", f["richieste"], 2)
    verifica("scritto da te: il prompt, una volta", f["nuovo"]["tu"], 10)
    # 1004: il testo delle istruzioni piu' i 4 caratteri di "text", che `peso`
    # conta come ogni altra stringa della forma della richiesta
    verifica("harness: istruzioni + promemoria + sistema",
             f["nuovo"]["harness"], 1004 + 118 + 50)
    verifica("strumenti: il risultato senza blob si conta", f["nuovo"]["strumenti"], 400)
    verifica("modello: testo e chiamata rispediti", f["nuovo"]["modello"], 30 + 6 + 20)
    verifica("istruzioni e strumenti, la prima volta", f["impianto"], 1004)
    verifica("messaggi partiti / unici", (f["msg_spediti"], f["msg_unici"]), (6, 4))
    verifica("ogni messaggio partito in media", f["ripetizione"], 1.5)
    verifica("attesa del modello: 2s + 3s", f["tempo_modello"], 5.0)
    vero("misurato", f["misurato"])


def prova_doppioni():
    """Un risultato scritto sia come riga sia come blob non raddoppia."""
    print("\nLo stesso contenuto, scritto due volte")
    base = [
        shape(),
        blob("B1", "user", [testo("leggi")]),
        utente(0, [testo("leggi")]),
        richiesta(1, "R1", ["B1"]),
        assistente(2, "R1", [uso("T1", "Read")]),
        utente(3, [reso("T1", "z" * 500)]),
    ]
    dopo = [
        # la stessa riga, in viaggio, in forma diversa: blob col risultato dentro
        blob("B2", "user", [reso("T1", "z" * 500), testo("<system-reminder>pro</system-reminder>")]),
        richiesta(4, "R2", ["H2", "B2"], base="R1", keep=1),
    ]
    f = misura(base + dopo)
    verifica("risultato in riga e in blob: una volta", f["nuovo"]["strumenti"], 500)
    verifica("prompt in riga e in blob: una volta", f["nuovo"]["tu"], 5)
    # blob prima, riga dopo: l'ordine non deve contare
    f2 = misura([shape(), blob("B2", "user", [reso("T1", "z" * 500)]),
                 assistente(2, "R1", [uso("T1", "Read")]),
                 utente(3, [reso("T1", "z" * 500)])])
    verifica("blob prima della riga: una volta", f2["nuovo"]["strumenti"], 500)
    # storia riemessa (fork, --resume): stesse righe una seconda volta
    f3 = misura(base + dopo + base + dopo)
    verifica("storia riemessa: richieste", f3["richieste"], 2)
    verifica("storia riemessa: strumenti", f3["nuovo"]["strumenti"], 500)
    verifica("storia riemessa: esecuzioni", f3["esecuzioni"], 1)
    # un promemoria uguale in due messaggi diversi e' partito due volte
    f4 = misura([blob("B1", "system", [testo("p" * 40)]),
                 blob("B9", "system", [testo("p" * 40)])])
    verifica("promemoria ripetuto in due blob: due volte", f4["nuovo"]["harness"], 80)


def prova_locale():
    """In locale passa il file intero; al modello torna una riga."""
    print("\nProdotto sulla macchina, girato al modello")
    righe = [
        shape(),
        richiesta(0, "R1", ["B1"]),
        assistente(1, "R1", [uso("E1", "Edit"), uso("B1", "Bash"), uso("M1", "mcp__x__y"),
                             uso("W1", "WebSearch"), uso("A1", "Agent")]),
        utente(2, [reso("E1", "ok")],
               locale={"originalFile": "f" * 9000, "oldString": "a", "newString": "bb"}),
        # output lungo: su disco per intero, al modello l'anteprima
        utente(5, [reso("B1", "o" * 100)],
               locale={"stdout": "o" * 100, "persistedOutputSize": 50_000}),
        utente(6, [reso("M1", "m" * 30)], locale={"content": "m" * 30}),
        utente(7, [reso("W1", "w" * 70, errore=True)]),          # senza toolUseResult
        utente(61, [reso("A1", "fatto")], locale={"content": "fatto"}),
    ]
    f = misura(righe)
    per = {s["nome"]: s for s in f["strumenti"]}
    verifica("Edit: prodotto in locale", per["Edit"]["prodotto"], 9003)
    verifica("Edit: girato al modello", per["Edit"]["inviato"], 2)
    verifica("Edit: resta qui", per["Edit"]["trattenuto"], 9001)
    verifica("Bash: vale il file salvato, non l'anteprima", per["Bash"]["prodotto"], 50_000)
    verifica("senza toolUseResult: prodotto = inviato", per["WebSearch"]["prodotto"], 70)
    verifica("errore contato", per["WebSearch"]["errori"], 1)
    verifica("dove gira", [per[n]["dove"] for n in ("Edit", "mcp__x__y", "WebSearch", "Agent")],
             ["locale", "mcp", "rete", "agenti"])
    verifica("eseguiti sulla macchina / in tutto",
             (f["esecuzioni_locali"], f["esecuzioni"]), (2, 5))
    verifica("resta qui: solo gli strumenti locali", f["trattenuto"], 9001 + 49_900)
    # Edit 1-2, Bash 1-5, mcp 1-6, WebSearch 1-7: insieme durano 6s, non 18.
    # L'agente (60s) e' lavoro del modello: non e' tempo della macchina.
    verifica("tempo negli strumenti: unione, agenti esclusi", f["tempo_strumenti"], 6.0)
    verifica("tempo del singolo strumento", per["Bash"]["tempo"], 4.0)


def prova_senza_richieste():
    """Transcript vecchio: il contenuto c'e', la misura delle richieste no."""
    print("\nPrima della 2.1.309")
    righe = [
        utente(0, [testo("fai una cosa")]),
        {"type": "assistant", "timestamp": quando(2),
         "message": {"content": [uso("T1", "Read")]}},
        utente(3, [reso("T1", "c" * 200)]),
    ]
    f = misura(righe)
    vero("non misurato", not f["misurato"])
    verifica("richieste", f["richieste"], 0)
    verifica("ripetizione: assente, non zero", f["ripetizione"], None)
    verifica("il contenuto delle righe resta", (f["nuovo"]["tu"], f["nuovo"]["strumenti"]),
             (12, 200))
    verifica("gli strumenti si contano lo stesso", f["esecuzioni_locali"], 1)


def prova_subagent():
    """Nel file di un subagent il compito l'ha scritto il modello, non tu."""
    print("\nSubagent")
    righe = [blob("B1", "user", [testo("cerca tutti i file")]),
             richiesta(0, "R1", ["B1"], fonte="agent:builtin:general-purpose")]
    f = misura(righe, subagent=True)
    verifica("compito del subagent: del modello", (f["nuovo"]["tu"], f["nuovo"]["modello"]),
             (0, 18))
    verifica("origine della richiesta", f["fonti"], {"agent:builtin:general-purpose": 1})
    verifica("involucro riconosciuto",
             F.origine_testo("  <system-reminder>x</system-reminder>"), "harness")
    verifica("testo libero", F.origine_testo("ciao"), "tu")


def prova_unione():
    print("\nIntervalli")
    verifica("sovrapposti", F.unione([(0, 4), (2, 6), (10, 11)]), 7.0)
    verifica("contenuti", F.unione([(0, 10), (2, 3)]), 10.0)
    verifica("nessuno", F.unione([]), 0.0)
    verifica("quota senza denominatore", F.quota(3, 0), None)


def prova_attive(base):
    """Quali sessioni sono aperte: i processi vivi, non i file rimasti."""
    print("\nSessioni aperte")
    cartella = os.path.join(base, "c--lavoro--progetto")
    os.makedirs(cartella)
    for sid in ("aaa", "bbb", "ccc"):
        with open(os.path.join(cartella, sid + ".jsonl"), "w", encoding="utf-8") as fh:
            fh.write("{}\n")
    sess = os.path.join(os.path.dirname(base), "sessions")
    os.makedirs(sess)
    for pid, sid, stato in ((1, "aaa", "busy"), (2, "bbb", "idle"), (3, "zzz", "idle")):
        with open(os.path.join(sess, f"{pid}.json"), "w", encoding="utf-8") as fh:
            json.dump({"pid": pid, "sessionId": sid, "status": stato}, fh)
    with open(os.path.join(sess, "rotto.json"), "w", encoding="utf-8") as fh:
        fh.write("{non json")
    attive = F.sessioni_attive(base, vivo=lambda pid: pid != 2)
    verifica("processo morto e sessione senza transcript: fuori",
             [(a["session_id"], a["stato"]) for a in attive], [("aaa", "busy")])
    # senza registri dei processi vale il transcript toccato da poco
    shutil.rmtree(sess)
    vecchio = os.path.join(cartella, "ccc.jsonl")
    os.utime(vecchio, (1000, 1000))
    attive = F.sessioni_attive(base, finestra=900)
    verifica("senza registri: i transcript recenti",
             sorted(a["session_id"] for a in attive), ["aaa", "bbb"])
    vero("il processo corrente risulta vivo", F.processo_vivo(os.getpid()))


def prova_osservatorio(base):
    """Lettura incrementale: le righe nuove si aggiungono, il resto non si rilegge."""
    print("\nOsservatorio")
    cartella = os.path.join(base, "c--lavoro--progetto")
    os.makedirs(cartella)
    path = os.path.join(cartella, SID + ".jsonl")

    def aggiungi(righe, coda=""):
        with open(path, "a", encoding="utf-8") as fh:
            for r in righe:
                fh.write(json.dumps(r) + "\n")
            fh.write(coda)

    aggiungi([shape("S1", 100), blob("B1", "user", [testo("ciao")]),
              utente(0, [testo("ciao")]), richiesta(1, "R1", ["B1"]),
              assistente(2, "R1", [uso("T1", "Write")], out=40)])
    sess = os.path.join(os.path.dirname(base), "sessions")
    os.makedirs(sess)
    with open(os.path.join(sess, "1.json"), "w", encoding="utf-8") as fh:
        json.dump({"pid": os.getpid(), "sessionId": SID, "status": "busy"}, fh)

    oss = cam.Osservatorio(base, PRICING, 300)
    righe = oss.aggiorna()
    verifica("una sessione aperta", [(s["session_id"], s["_status"]) for s in righe],
             [(SID, "busy")])
    s1 = righe[0]
    verifica("token usciti letti dallo stesso giro", s1["tokens"]["output"], 40)
    verifica("strumenti: ancora nessun risultato", s1["flusso"]["esecuzioni"], 0)
    pos = oss.files[path].pos

    # una riga intera e una a meta': la seconda aspetta il giro dopo
    aggiungi([utente(3, [reso("T1", "ok")], locale={"content": "k" * 300})],
             coda='{"type": "api-request", "id": "R2", "tail": ["H')
    s2 = oss.aggiorna()[0]
    verifica("riga nuova contata", s2["flusso"]["esecuzioni_locali"], 1)
    verifica("resta qui", s2["flusso"]["trattenuto"], 298)
    verifica("riga a meta' non contata", s2["flusso"]["richieste"], 1)
    vero("si e' letto solo il pezzo nuovo", oss.files[path].pos > pos)
    aggiungi([], coda='2"], "base": "R1", "keep": 1}\n')
    verifica("riga completata al giro dopo", oss.aggiorna()[0]["flusso"]["richieste"], 2)
    vero("niente di nuovo: stessa riga, non ricostruita",
         oss.sessione(SID, [path]) is oss.sessione(SID, [path]))

    # sessione chiusa: sparisce, e con lei lo stato tenuto in memoria
    shutil.rmtree(sess)
    os.utime(path, (1000, 1000))
    verifica("sessione chiusa: nessuna riga", oss.aggiorna(), [])
    verifica("e nessun file tenuto aperto", oss.files, {})


def main() -> int:
    print("Prove sul flusso")
    print("=" * 76)
    for prova in (prova_origini, prova_doppioni, prova_locale, prova_senza_richieste,
                  prova_subagent, prova_unione):
        prova()
    for prova in (prova_attive, prova_osservatorio):
        tmp = tempfile.mkdtemp(prefix="cam-flusso-")
        base = os.path.join(tmp, "projects")
        os.makedirs(base, exist_ok=True)
        try:
            prova(base)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    print()
    print("=" * 76)
    fallite = [n for ok, n in esiti if not ok]
    if fallite:
        print(f"{len(fallite)} prove fallite su {len(esiti)}:")
        for n in fallite:
            print("  -", n)
        return 1
    print(f"tutte le {len(esiti)} prove sul flusso superate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
