#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Prove sulla sorgente Copilot.

Il rischio qui non e' sbagliare un conto: e' **inventarne uno**. Copilot non
dichiara i token, quindi ogni numero di costo che comparisse sarebbe finto, e
uno zero verrebbe letto come «non ha consumato niente» invece che come «non si
sa». Meta' delle prove sotto guardano proprio quello.

L'altra meta' guarda la fragilita' della fonte: e' storage interno di VS Code,
non documentato, che cambia con le versioni dell'estensione. Un file con una
forma inattesa deve far perdere quella sessione, non far cadere il programma.

    python test_copilot.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

import cam_copilot as cp

try:  # console Windows: senza questo l'output rediretto muore sugli accenti
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover
    pass

esiti: list[tuple[bool, str]] = []


def verifica(nome: str, ottenuto, atteso) -> None:
    ok = ottenuto == atteso
    esiti.append((ok, nome))
    print(f"  {'ok  ' if ok else 'FALLITA'}  {nome:<54} {ottenuto!r}"
          + ("" if ok else f"   atteso {atteso!r}"))


# --------------------------------------------------------------------------- #
# Finti file di VS Code
# --------------------------------------------------------------------------- #


def richiesta(testo, modello="copilot/claude-sonnet-4.5", ms=1_768_000_000_000,
              elapsed=2000, strumenti=(), risposta="ecco fatto") -> dict:
    blocchi = [{"value": risposta, "supportHtml": False}]
    blocchi.append({"kind": "thinking", "value": "ragionamento interno"})
    for t in strumenti:
        blocchi.append({"kind": "toolInvocationSerialized", "toolId": t})
    return {
        "requestId": "req-" + str(ms), "responseId": "resp",
        "message": {"text": testo, "parts": []},
        "modelId": modello,
        "timestamp": ms,
        "response": blocchi,
        "result": {"timings": {"firstProgress": 500, "totalElapsed": elapsed}},
    }


def scrivi_sessione(user_dir, hash_ws, nome, richieste, cartella=None,
                    sid="sess-1", titolo="una chat"):
    d = os.path.join(user_dir, "workspaceStorage", hash_ws, "chatSessions")
    os.makedirs(d, exist_ok=True)
    if cartella is not None:
        with open(os.path.join(user_dir, "workspaceStorage", hash_ws,
                               "workspace.json"), "w", encoding="utf-8") as fh:
            json.dump({"folder": cartella}, fh)
    path = os.path.join(d, nome)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"version": 3, "sessionId": sid, "customTitle": titolo,
                   "creationDate": richieste[0]["timestamp"] if richieste else 0,
                   "lastMessageDate": richieste[-1]["timestamp"] if richieste else 0,
                   "requests": richieste}, fh)
    return path


# --------------------------------------------------------------------------- #


def prova_lettura(tmp):
    print("\nUna sessione letta per intero")
    scrivi_sessione(tmp, "abc", "s.json", [
        richiesta("prima domanda", elapsed=3000, strumenti=["copilot_readFile"]),
        richiesta("seconda domanda", ms=1_768_000_060_000, elapsed=1000,
                  strumenti=["run_in_terminal", "copilot_readFile"]),
    ], cartella="file:///c%3A/lavoro/Mio%20Progetto")
    ss = cp.sessioni([tmp])
    verifica("una sessione", len(ss), 1)
    s = ss[0]
    verifica("il progetto viene da workspace.json", s["project"], "Mio Progetto")
    verifica("con il percorso decodificato", s["cwd"], "C:/lavoro/Mio Progetto")
    verifica("il titolo", s["title"], "una chat")
    verifica("due turni", s["traces_n"], 2)
    verifica("strumenti contati", s["tool_calls"], 3)
    verifica("nomi degli strumenti del secondo turno",
             s["traces"][1]["tool_names"], ["copilot_readFile", "run_in_terminal"])
    verifica("la fonte e' dichiarata", s["fonte"], "copilot")
    verifica("ed e' un dato acquisito", s["origine"], "acquisito")
    verifica("durata del turno dalla latenza misurata",
             s["traces"][0]["duration"], 3.0)
    verifica("tempo attivo = somma delle latenze", s["active"], 4.0)
    verifica("il modello", sorted(s["per_model"]), ["copilot/claude-sonnet-4.5"])


def prova_niente_costo(tmp):
    print("\nQuello che Copilot non dice, non viene inventato")
    scrivi_sessione(tmp, "abc", "s.json", [richiesta("domanda")],
                    cartella="file:///c%3A/x")
    s = cp.sessioni([tmp])[0]
    verifica("il costo non e' noto", s["costo_noto"], False)
    verifica("e nemmeno per il turno", s["traces"][0]["costo_noto"], False)
    verifica("niente cache hit", s["cache_hit"], None)
    verifica("nemmeno sul turno", s["traces"][0]["cache_hit"], None)
    verifica("i token sono zeri con le chiavi giuste, non un vuoto",
             sorted(s["tokens"]), sorted(cp.token_vuoti()))
    verifica("e valgono tutti zero", set(s["tokens"].values()), {0})
    verifica("il costo numerico e' zero, per poter sommare", s["cost"], 0.0)
    verifica("le voci per mese restano vuote: nessun mese da attribuire",
             s["per_month"], {})


def prova_file_strani(tmp):
    print("\nUna fonte non documentata: i file storti non fanno cadere niente")
    d = os.path.join(tmp, "workspaceStorage", "zzz", "chatSessions")
    os.makedirs(d, exist_ok=True)
    for nome, contenuto in (
        ("rotto.json", "{non json"),
        ("lista.json", "[1,2,3]"),
        ("vuoto.json", "{}"),
        ("senza-turni.json", '{"sessionId":"x","requests":[]}'),
        ("turni-strani.json", '{"sessionId":"y","requests":[null,"testo"]}'),
    ):
        with open(os.path.join(d, nome), "w", encoding="utf-8") as fh:
            fh.write(contenuto)
    verifica("nessuna sessione, nessuna eccezione", cp.sessioni([tmp]), [])
    scrivi_sessione(tmp, "buono", "ok.json", [richiesta("ciao")],
                    cartella="file:///c%3A/x")
    verifica("e quella buona si legge lo stesso", len(cp.sessioni([tmp])), 1)


def prova_senza_progetto(tmp):
    print("\nChat aperte senza cartella: ci sono lo stesso")
    d = os.path.join(tmp, "globalStorage", "emptyWindowChatSessions")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "vaga.json"), "w", encoding="utf-8") as fh:
        json.dump({"sessionId": "libera", "requests": [richiesta("una domanda")]}, fh)
    ss = cp.sessioni([tmp])
    verifica("la sessione c'e'", len(ss), 1)
    verifica("con un progetto dichiarato tale", ss[0]["project"], "(senza progetto)")
    verifica("e nessun percorso", ss[0]["cwd"], None)

    scrivi_sessione(tmp, "abc", "s.json", [richiesta("altra")], cartella=None)
    verifica("workspace.json assente: stesso trattamento",
             sorted(s["project"] for s in cp.sessioni([tmp])),
             ["(senza progetto)", "(senza progetto)"])


def prova_testo(tmp):
    print("\nIl testo, solo se richiesto")
    scrivi_sessione(tmp, "abc", "s.json",
                    [richiesta("che ore sono?", risposta="sono le tre")],
                    cartella="file:///c%3A/x")
    senza = cp.sessioni([tmp])[0]
    verifica("di default niente messaggi", "messaggi" in senza, False)
    con = cp.sessioni([tmp], keep_messages=True)[0]
    m = con["messaggi"]
    verifica("domanda e risposta", [x["kind"] for x in m], ["prompt", "assistant"])
    verifica("il testo della domanda", m[0]["text"], "che ore sono?")
    verifica("quello della risposta", m[1]["text"], "sono le tre")
    verifica("il ragionamento interno resta fuori",
             "ragionamento" in m[1]["text"], False)


def prova_ordine(tmp):
    print("\nDalla piu' recente")
    scrivi_sessione(tmp, "a", "s.json", [richiesta("vecchia", ms=1_700_000_000_000)],
                    cartella="file:///c%3A/uno", sid="vecchia")
    scrivi_sessione(tmp, "b", "s.json", [richiesta("nuova", ms=1_769_000_000_000)],
                    cartella="file:///c%3A/due", sid="nuova")
    verifica("ordine per fine, decrescente",
             [s["session_id"] for s in cp.sessioni([tmp])], ["nuova", "vecchia"])


def prova_integrazione(tmp):
    """L'unica prova che passa da `collect`, cioe' dal codice vero."""
    print("\nDentro il monitor, accanto a Claude Code")
    import cam
    import cam_archivio as ar
    scrivi_sessione(tmp, "abc", "s.json",
                    [richiesta("domanda", strumenti=["copilot_readFile"])],
                    cartella="file:///c%3A/lavoro/Progetto")

    vero_db, vere_radici = ar.db_path, cp.cartelle_utente
    ar.db_path = lambda _a=None: os.path.join(tmp, "cam-local.db")
    cp.cartelle_utente = lambda: [tmp]
    try:
        base = os.path.join(tmp, "projects")
        os.makedirs(base, exist_ok=True)
        cfg = {"models": {}, "cache_multipliers": {}, "server_tools": {},
               "aliases": {}, "free_models": []}
        ss = cam.collect(base, cfg, True, 300, quiet=True)
        verifica("la sessione Copilot entra nei conti", len(ss), 1)
        verifica("con la sua fonte", cam.fonte_di(ss[0]), "copilot")
        verifica("il costo si scrive col trattino", cam.costo_txt(ss[0]), "—")
        verifica("e i token pure", cam.tok_txt(ss[0], 0), "—")
        verifica("mentre per Claude Code resta un numero",
                 cam.costo_txt({"cost": 1.5}), cam.h_cost(1.5))

        # spenta in configurazione: non deve rientrare come «orfana»
        cfg_off = dict(cfg, copilot={"enabled": False})
        verifica("spenta, sparisce",
                 len(cam.collect(base, cfg_off, True, 300, quiet=True)), 0)
        verifica("ma resta in archivio",
                 ar.Archivio(ar.db_path(), cam.CACHE_FORMAT).conta()["sessioni"], 1)
        verifica("e riaccendendola torna",
                 len(cam.collect(base, cfg, True, 300, quiet=True)), 1)
    finally:
        ar.db_path, cp.cartelle_utente = vero_db, vere_radici


def main() -> int:
    print("=" * 72)
    print("Prove sulla sorgente Copilot")
    print("=" * 72)
    for prova in (prova_lettura, prova_niente_costo, prova_file_strani,
                  prova_senza_progetto, prova_testo, prova_ordine,
                  prova_integrazione):
        tmp = tempfile.mkdtemp(prefix="cam-copilot-")
        try:
            prova(tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    print()
    print("=" * 72)
    fallite = [n for ok, n in esiti if not ok]
    if fallite:
        print(f"{len(fallite)} prove fallite su {len(esiti)}:")
        for n in fallite:
            print("  -", n)
        return 1
    print(f"tutte le {len(esiti)} prove su Copilot superate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
