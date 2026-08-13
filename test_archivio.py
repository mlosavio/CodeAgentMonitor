#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Prove sull'archivio locale (`cm-local.db`).

Il punto delicato non e' salvare: e' **cosa sopravvive a cosa**. L'archivio ha
due meta' con regole opposte, e le prove qui sotto guardano quasi solo il
confine fra le due:

  - la cache di analisi si puo' buttare quando si vuole, e si butta da sola
    quando cambia il formato del parser;
  - l'archivio delle sessioni no, perche' un giorno conterra' righe che nessuna
    rilettura potrebbe rifare (i transcript scaduti, e Copilot).

Se questo confine si rompe non se ne accorge nessuno finche' non serve, cioe'
quando i dati non ci sono piu'.

    python test_archivio.py
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time

import claude_monitor as cm
import cm_archivio as ar

try:  # console Windows: senza questo l'output rediretto muore sugli accenti
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover
    pass


esiti: list[tuple[bool, str]] = []
SID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
T0 = dt.datetime(2026, 4, 1, 9, 0, 0, tzinfo=dt.timezone.utc)

PRICING = {
    "models": {"claude-opus-5": {"in": 5.0, "out": 25.0}},
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
    print(f"  {'ok  ' if ok else 'FALLITA'}  {nome:<54} {ottenuto!r}"
          + ("" if ok else f"   atteso {atteso!r}"))


# --------------------------------------------------------------------------- #
# Transcript finti
# --------------------------------------------------------------------------- #


def quando(sec: float) -> str:
    return (T0 + dt.timedelta(seconds=sec)).isoformat().replace("+00:00", "Z")


def prompt(sec, testo, sidechain=False) -> dict:
    riga = {"type": "user", "sessionId": SID, "timestamp": quando(sec),
            "cwd": "C:\\lavoro\\progetto",
            "message": {"content": [{"type": "text", "text": testo}]}}
    if sidechain:
        riga["isSidechain"] = True
    return riga


def risposta(sec, req, out=100, testo="ok") -> dict:
    return {"type": "assistant", "sessionId": SID, "timestamp": quando(sec),
            "requestId": req,
            "message": {"id": "msg-" + req, "model": "claude-opus-5",
                        "content": [{"type": "text", "text": testo}],
                        "usage": {"input_tokens": 10, "output_tokens": out,
                                  "cache_read_input_tokens": 90}}}


def scrivi(base, righe, subagent=None) -> str:
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


def apri(tmp, formato=cm.CACHE_FORMAT, testo=False,
         sola_lettura=False) -> ar.Archivio:
    return ar.Archivio(os.path.join(tmp, "cm-local.db"), formato, testo=testo,
                       sola_lettura=sola_lettura)


def conta(tmp, tabella) -> int:
    con = sqlite3.connect(os.path.join(tmp, "cm-local.db"))
    try:
        return con.execute(f"SELECT COUNT(*) FROM {tabella}").fetchone()[0]
    finally:
        con.close()


# --------------------------------------------------------------------------- #
# Le prove
# --------------------------------------------------------------------------- #


def prova_cache(tmp, base):
    print("\nCache di analisi: si riusa solo se il file non e' cambiato")
    path = scrivi(base, [prompt(0, "uno"), risposta(5, "req-1")])
    st = os.stat(path)
    rec = cm.scan_file(path, PRICING)
    with apri(tmp) as a:
        a.scrivi_file(path, st.st_size, st.st_mtime, rec)
    with apri(tmp) as a:
        idx = a.indice()
        k = ar.chiave(path)
        verifica("il file e' in cache", k in idx, True)
        verifica("con la sua dimensione", idx[k][0], st.st_size)
        verifica("e il record si rilegge", a.record(path)["session_id"], SID)
        verifica("un percorso scritto diversamente e' lo stesso file",
                 ar.chiave(path.replace("\\", "/").upper()) == k, True)
        verifica("un file mai visto non c'e'", a.record(path + ".altro"), None)


def prova_formato(tmp, base):
    print("\nCambia il formato del parser: si svuota la cache, non l'archivio")
    path = scrivi(base, [prompt(0, "uno"), risposta(5, "req-1")])
    sessioni = cm.collect(base, PRICING, True, 300, quiet=True)
    # collect ha usato l'archivio accanto allo script: qui ne uso uno mio
    with apri(tmp) as a:
        st = os.stat(path)
        a.scrivi_file(path, st.st_size, st.st_mtime,
                      cm.scan_file(path, PRICING))
        a.scrivi_sessioni(sessioni)
    verifica("prima: cache piena", conta(tmp, "file"), 1)
    verifica("prima: archivio pieno", conta(tmp, "sessione"), 1)
    turni_prima = conta(tmp, "turno")

    with apri(tmp, formato=cm.CACHE_FORMAT + 1) as a:
        verifica("l'archivio segnala di aver svuotato", a.svuotata, True)
    verifica("dopo: cache svuotata", conta(tmp, "file"), 0)
    verifica("dopo: sessioni intatte", conta(tmp, "sessione"), 1)
    verifica("dopo: turni intatti", conta(tmp, "turno"), turni_prima)

    with apri(tmp, formato=cm.CACHE_FORMAT + 1) as a:
        verifica("il secondo giro non risvuota niente", a.svuotata, False)


def prova_potatura(tmp, base):
    print("\nSparisce un transcript: la sessione resta, marcata acquisita")
    scrivi(base, [prompt(0, "uno"), risposta(5, "req-1")])
    sub = scrivi(base, [prompt(2, "task", sidechain=True), risposta(3, "req-s")],
                 subagent="uno")
    sessioni = cm.collect(base, PRICING, True, 300, quiet=True)
    verifica("la sessione ha due file", len(sessioni[0]["files"]), 2)

    vivi = set()
    with apri(tmp) as a:
        for p in sessioni[0]["files"]:
            st = os.stat(p)
            a.scrivi_file(p, st.st_size, st.st_mtime, cm.scan_file(p, PRICING))
            vivi.add(ar.chiave(p))
        a.scrivi_sessioni(sessioni)
        a.pota(base, vivi)

    con = sqlite3.connect(os.path.join(tmp, "cm-local.db"))
    riga = con.execute("SELECT origine, file_totali, file_mancanti "
                       "FROM sessione").fetchone()
    verifica("finche' i file ci sono e' derivata", riga, ("derivato", 2, 0))
    con.close()

    # Claude Code fa le pulizie: sparisce il file del subagent.
    os.remove(sub)
    with apri(tmp) as a:
        tolti = a.pota(base, vivi - {ar.chiave(sub)})
        verifica("una riga di cache tolta", tolti, 1)
    con = sqlite3.connect(os.path.join(tmp, "cm-local.db"))
    riga = con.execute("SELECT origine, file_totali, file_mancanti "
                       "FROM sessione").fetchone()
    verifica("adesso e' acquisita", riga, ("acquisito", 2, 1))
    verifica("ma la sessione c'e' ancora", conta(tmp, "sessione"), 1)
    verifica("e i suoi turni pure", conta(tmp, "turno") > 0, True)
    con.close()


def prova_potatura_altra_base(tmp, base):
    print("\nLanciato su un'altra cartella: non pota quello che non ha guardato")
    path = scrivi(base, [prompt(0, "uno"), risposta(5, "req-1")])
    altrove = os.path.join(tmp, "altra", "sessione.jsonl")
    os.makedirs(os.path.dirname(altrove), exist_ok=True)
    shutil.copyfile(path, altrove)
    with apri(tmp) as a:
        for p in (path, altrove):
            st = os.stat(p)
            a.scrivi_file(p, st.st_size, st.st_mtime, cm.scan_file(p, PRICING))
    # il conteggio va fatto a connessione chiusa: finche' la scansione e' in
    # corso le scritture stanno in una transazione, e da fuori non si vedono —
    # che e' esattamente il comportamento voluto
    verifica("due file in cache", conta(tmp, "file"), 2)
    with apri(tmp) as a:
        # si pota solo sotto `base`, e li' non manca niente
        verifica("niente da potare sotto base", a.pota(base, {ar.chiave(path)}), 0)
    verifica("l'altra cartella e' rimasta", conta(tmp, "file"), 2)


def prova_turni_riscritti(tmp, base):
    print("\nUna sessione che perde turni non lascia in giro i vecchi")
    sessioni = [{
        "session_id": SID, "files": ["x"], "project": "p", "cost": 1.0,
        "traces": [{"n": i, "ts": i, "end": i, "duration": 0.0, "prompt": f"t{i}",
                    "requests": 1, "tools": 0, "spans": 2, "subagents": 0,
                    "interrupted": False, "cost": 0.5, "cache_hit": 0.5,
                    "tokens": {}, "per_model": {}, "tool_names": []}
                   for i in (1, 2, 3)],
    }]
    with apri(tmp) as a:
        a.scrivi_sessioni(sessioni)
    verifica("tre turni", conta(tmp, "turno"), 3)
    sessioni[0]["traces"] = sessioni[0]["traces"][:1]
    with apri(tmp) as a:
        a.scrivi_sessioni(sessioni)
    verifica("riscrivendo ne resta uno", conta(tmp, "turno"), 1)


def prova_import_json(tmp, base):
    print("\nTrasloco dalla vecchia cache JSON")
    path = scrivi(base, [prompt(0, "uno"), risposta(5, "req-1")])
    st = os.stat(path)
    rec = cm.scan_file(path, PRICING)
    vecchia = os.path.join(tmp, ".cache.json")

    with open(vecchia, "w", encoding="utf-8") as fh:
        json.dump({"format": cm.CACHE_FORMAT,
                   "files": {path: {"size": st.st_size, "mtime": st.st_mtime,
                                    "rec": rec}}}, fh)
    with apri(tmp) as a:
        verifica("importa col formato giusto",
                 ar.importa_cache_json(a, vecchia, cm.CACHE_FORMAT), 1)
        verifica("e il record si rilegge", a.record(path)["session_id"], SID)

    with open(vecchia, "w", encoding="utf-8") as fh:
        json.dump({"format": cm.CACHE_FORMAT - 1, "files": {path: {}}}, fh)
    with apri(tmp) as a:
        verifica("col formato sbagliato non importa niente",
                 ar.importa_cache_json(a, vecchia, cm.CACHE_FORMAT), 0)
    verifica("un file illeggibile non fa saltare niente",
             ar.importa_cache_json(apri(tmp), os.path.join(tmp, "manca"),
                                   cm.CACHE_FORMAT), 0)


def prova_svuota(tmp, base):
    print("\n--clear-cache svuota l'analisi e lascia l'archivio")
    scrivi(base, [prompt(0, "uno"), risposta(5, "req-1")])
    sessioni = cm.collect(base, PRICING, True, 300, quiet=True)
    with apri(tmp) as a:
        for p in sessioni[0]["files"]:
            st = os.stat(p)
            a.scrivi_file(p, st.st_size, st.st_mtime, cm.scan_file(p, PRICING))
        a.scrivi_sessioni(sessioni)
        prima = a.conta()
        verifica("prima c'e' tutto", (prima["file"], prima["sessioni"]), (1, 1))
        verifica("svuotando torna quanti erano", a.svuota_cache(), 1)
        dopo = a.conta()
    verifica("cache vuota", dopo["file"], 0)
    verifica("sessioni intatte", dopo["sessioni"], 1)
    verifica("ma ora sono acquisite", dopo["acquisite"], 1)


def prova_collect(tmp, base):
    """L'unica prova che passa da `collect`, cioe' dal codice vero."""
    print("\nDa capo a fondo: la seconda scansione non rilegge i file")
    scrivi(base, [prompt(0, "uno"), risposta(5, "req-1", out=200),
                  prompt(60, "due"), risposta(65, "req-2", out=300)])

    letti: list[bool] = []

    def spia(fatti, totali, path, da_cache):
        if path:
            letti.append(da_cache)

    uno = cm.collect(base, PRICING, True, 300, quiet=True, on_progress=spia)
    verifica("primo giro: letto dal disco", any(letti), False)
    letti.clear()
    due = cm.collect(base, PRICING, True, 300, quiet=True, on_progress=spia)
    verifica("secondo giro: preso dall'archivio", all(letti), True)
    verifica("stessi turni", (uno[0]["traces_n"], due[0]["traces_n"]), (2, 2))
    verifica("stesso costo", round(uno[0]["cost"], 9), round(due[0]["cost"], 9))

    # cambia il file: la cache non deve piu' valere
    scrivi(base, [prompt(0, "uno"), risposta(5, "req-1", out=200),
                  prompt(60, "due"), risposta(65, "req-2", out=300),
                  prompt(120, "tre"), risposta(125, "req-3", out=400)])
    letti.clear()
    tre = cm.collect(base, PRICING, True, 300, quiet=True, on_progress=spia)
    verifica("file cambiato: riletto", any(letti), False)
    verifica("e il turno nuovo si vede", tre[0]["traces_n"], 3)

    letti.clear()
    cm.collect(base, PRICING, False, 300, quiet=True, on_progress=spia)
    verifica("con --no-cache si rilegge sempre", any(letti), False)


def con_testo(base_config: dict | None = None) -> dict:
    c = dict(PRICING)
    c["archivio"] = {"testo": True}
    return c


def prova_testo(tmp, base):
    print("\nTesto archiviato: solo se acceso, e attaccato al suo turno")
    scrivi(base, [prompt(0, "prima domanda"), risposta(5, "req-1", testo="prima risposta"),
                  prompt(60, "seconda domanda"), risposta(65, "req-2", testo="seconda risposta")])

    cm.collect(base, PRICING, True, 300, quiet=True)
    with apri(tmp) as a:
        verifica("spento: niente testo", a.conta()["messaggi"], 0)

    sessioni = cm.collect(base, con_testo(), True, 300, quiet=True)
    with apri(tmp, testo=True) as a:
        c = a.conta()
        verifica("acceso: il testo c'e'", c["messaggi"], 4)
        msg = a.messaggi_di(SID)
        verifica("in ordine di tempo", [m["kind"] for m in msg],
                 ["prompt", "assistant", "prompt", "assistant"])
        verifica("il primo turno ha due messaggi",
                 len(a.messaggi_di(SID, turno=1)), 2)
        verifica("e il secondo pure", len(a.messaggi_di(SID, turno=2)), 2)
        verifica("la risposta sta nel turno giusto",
                 a.messaggi_di(SID, turno=2)[1]["text"], "seconda risposta")
        verifica("i turni sono due", sessioni[0]["traces_n"], 2)

    verifica("accenderlo ha fatto rileggere i transcript",
             conta(tmp, "file"), 1)


def prova_ricerca_testo(tmp, base):
    print("\nRicerca a testo pieno dentro le risposte")
    scrivi(base, [
        prompt(0, "domanda generica"),
        risposta(5, "req-1", testo="la riconciliazione della fattura è sbagliata"),
        prompt(60, "altra domanda"),
        risposta(65, "req-2", testo="qui non c'entra niente"),
    ])
    cm.collect(base, con_testo(), True, 300, quiet=True)
    with apri(tmp, testo=True) as a:
        verifica("FTS5 disponibile", a.fts, True)
        r = a.cerca("riconciliazione")
        verifica("una risposta trovata", len(r), 1)
        verifica("nel turno giusto", r[0]["turno"], 1)
        verifica("con il frammento", "riconciliazione" in r[0]["frammento"].lower(), True)
        verifica("gli accenti non contano", len(a.cerca("e sbagliata")), 1)
        verifica("parole che non ci sono", a.cerca("zzz"), [])
        verifica("caratteri speciali non fanno saltare la query",
                 isinstance(a.cerca('fattura - "x" *'), list), True)
        verifica("stringa vuota", a.cerca("   "), [])

    trovati = cm.cerca_nel_testo("riconciliazione")
    verifica("e la ricerca arriva fino ai turni", len(trovati), 1)
    # PF05: il frammento arriva fino alla riga della tabella. Senza, la ricerca
    # dice quanti turni contengono la parola e non dove.
    chiave = next(iter(trovati))
    verifica("il turno porta con se' il frammento",
             "riconciliazione" in trovati[chiave]["frammento"].lower(), True)
    verifica("evidenziata", "«" in trovati[chiave]["frammento"], True)
    verifica("e chi l'ha detta", trovati[chiave]["ruolo"], "claude")
    verifica("sotto le tre lettere non si cerca", cm.cerca_nel_testo("ri"), {})

    sessioni = cm.collect(base, con_testo(), True, 300, quiet=True)
    righe = cm.flatten_traces(sessioni, "riconciliazione", anche=trovati)
    verifica("un turno solo passa il filtro", len(righe), 1)
    verifica("con il frammento addosso",
             "riconciliazione" in (righe[0]["frammento"] or "").lower(), True)
    # Anche le domande sono testo archiviato: se la parola sta li', il frammento
    # dice «tu». La colonna mostra il prompt tagliato, il frammento mostra il
    # punto — non e' un doppione, e' il pezzo che il taglio nasconderebbe.
    altri = cm.flatten_traces(sessioni, "altra", anche=cm.cerca_nel_testo("altra"))
    verifica("una parola nella domanda e' attribuita a te",
             [r["frammento_ruolo"] for r in altri], ["tu"])
    verifica("senza ricerca nessuna riga ha frammenti",
             {r["frammento"] for r in cm.flatten_traces(sessioni)}, {None})
    verifica("un insieme al posto della mappa non fa saltare niente",
             len(cm.flatten_traces(sessioni, "riconciliazione", anche={chiave})), 1)

    # PF06: il dataset esce dalla stessa selezione che si ha davanti.
    fuori = os.path.join(tmp, "turni.jsonl")
    esito = cm.export_turni_jsonl(righe, fuori)
    verifica("un turno esportato", esito["turni"], 1)
    verifica("con la risposta presa dall'archivio", esito["senza_risposta"], 0)
    riga = json.loads(open(fuori, encoding="utf-8").read().strip())
    verifica("la domanda c'e'", riga["domanda"], "domanda generica")
    verifica("e la risposta per intero, non il frammento",
             riga["risposta"], "la riconciliazione della fattura è sbagliata")
    verifica("col costo accanto", riga["costo_usd"] > 0, True)
    verifica("e l'esito del turno", riga["interrotto"], False)

    with apri(tmp, testo=True) as a:
        verifica("dimenticare il testo lo cancella", a.dimentica_testo(), 4)
        verifica("e i numeri restano", a.conta()["turni"] > 0, True)
        verifica("la ricerca non trova piu' niente", a.cerca("riconciliazione"), [])


def prova_manutenzione(tmp, base):
    print("\nQuanto pesa l'archivio, e restituire lo spazio")
    scrivi(base, [prompt(i * 60, f"domanda numero {i} " + "x" * 400)
                  for i in range(30)]
           + [risposta(1900, "req-1", testo="una risposta " + "y" * 4000)])
    cm.collect(base, con_testo(), True, 300, quiet=True)
    with apri(tmp, testo=True) as a:
        p = a.peso()
        verifica("il file ha un peso", p["file"] > 0, True)
        verifica("le voci stanno sotto il totale",
                 sum(p["parti"].values()) <= p["file"], True)
        verifica("la cache di analisi c'e'", p["parti"]["cache di analisi"] > 0, True)
        verifica("e il testo pure", p["parti"]["testo"] > 0, True)
        # PF09 vive di questo numero: se non si misura, non si decide.
        verifica("i timestamp si contano dentro il record",
                 p["timestamp"] > 0, True)
    # ...e la risposta e' stata comprimere, non buttare via: i timestamp ci sono
    # ancora tutti, ma su disco occupano molto meno.
    con = sqlite3.connect(os.path.join(tmp, "cm-local.db"))
    grezzo, in_chiaro = con.execute(
        "SELECT rec, LENGTH(rec) FROM file LIMIT 1").fetchone()
    con.close()
    verifica("il record in cache e' un blob compresso",
             isinstance(grezzo, bytes), True)
    verifica("e sta in meno spazio di quanto ne occupi in chiaro",
             in_chiaro < len(json.dumps(ar.spacchetta(grezzo))), True)

    with apri(tmp) as a:
        rec = a.record(next(iter(a.indice())))
        verifica("e si rilegge per intero", isinstance(rec.get("ts"), list), True)
        verifica("con tutti i suoi istanti", len(rec["ts"]) > 1, True)
    # Le righe scritte prima, in chiaro, non devono far rileggere niente.
    con = sqlite3.connect(os.path.join(tmp, "cm-local.db"))
    path_vecchio = con.execute("SELECT path FROM file LIMIT 1").fetchone()[0]
    con.execute("UPDATE file SET rec=? WHERE path=?",
                (json.dumps({"session_id": "vecchia", "ts": [1.0, 2.0]}),
                 path_vecchio))
    con.commit()
    con.close()
    with apri(tmp) as a:
        verifica("il vecchio formato in chiaro si legge ancora",
                 a.record(path_vecchio)["session_id"], "vecchia")
        verifica("e uno illeggibile non fa saltare niente",
                 ar.spacchetta(b"non compresso"), None)

    with apri(tmp, testo=True) as a:
        a.dimentica_testo()
        prima = a.peso()["file"]
        recuperati = a.compatta()
        dopo = a.peso()["file"]
        verifica("compattare non fa crescere il file", dopo <= prima, True)
        verifica("e dichiara quanto ha restituito", recuperati, max(0, prima - dopo))
        verifica("i numeri sopravvivono al VACUUM", a.conta()["turni"] > 0, True)

    with apri(tmp, testo=True, sola_lettura=True) as a:
        verifica("in sola lettura non si compatta niente", a.compatta(), 0)
        verifica("ma il peso si legge lo stesso", a.peso()["file"] > 0, True)


def prova_sessione_sopravvissuta(tmp, base):
    print("\nIl transcript sparisce: la sessione resta nei conti")
    path = scrivi(base, [
        prompt(0, "una domanda che vale la pena ricordare"),
        risposta(5, "req-1", out=500, testo="una risposta memorabile"),
        prompt(60, "e una seconda"),
        risposta(65, "req-2", out=200, testo="e la sua risposta"),
    ])
    prima = cm.collect(base, con_testo(), True, 300, quiet=True)
    costo = prima[0]["cost"]
    verifica("prima la sessione c'e'", len(prima), 1)
    verifica("e non e' archiviata", prima[0].get("archiviata"), None)

    os.remove(path)
    dopo = cm.collect(base, con_testo(), True, 300, quiet=True)
    verifica("dopo la sessione c'e' ancora", len(dopo), 1)
    verifica("marcata archiviata", dopo[0]["archiviata"], True)
    verifica("con lo stesso costo", round(dopo[0]["cost"], 9), round(costo, 9))
    verifica("e gli stessi turni", dopo[0]["traces_n"], 2)
    verifica("i turni si rileggono", len(dopo[0]["traces"]), 2)
    verifica("con il loro prompt", dopo[0]["traces"][0]["prompt"],
             "una domanda che vale la pena ricordare")
    verifica("e il per_mese per la vista mensile",
             bool(dopo[0]["per_month"]), True)
    verifica("i mesi si sommano ancora",
             round(sum(d["cost"] for mm in dopo[0]["per_month"].values()
                       for d in mm.values()), 9), round(costo, 9))

    sess, msgs = cm.conversazione_archiviata(SID)
    verifica("e la conversazione si rilegge", len(msgs), 4)
    verifica("con il testo giusto", msgs[1]["text"], "una risposta memorabile")
    verifica("i token dei messaggi sono azzerati, non assenti",
             msgs[1]["tok"]["output"], 0)

    _, trace, spans, turno_msgs = cm.load_trace(base, SID, 2, PRICING)
    verifica("il turno si apre lo stesso", trace.get("n"), 2)
    verifica("ma senza span", spans, [])
    verifica("con i messaggi di quel turno", len(turno_msgs), 2)


def prova_sessione_dimezzata(tmp, base):
    print("\nSparisce solo un file: la sessione resta scansionata, ma dice cosa manca")
    scrivi(base, [prompt(0, "uno"), risposta(5, "req-1", out=100)])
    sub = scrivi(base, [prompt(2, "task", sidechain=True),
                        risposta(3, "req-s", out=900)], subagent="uno")
    prima = cm.collect(base, PRICING, True, 300, quiet=True)
    costo_pieno = prima[0]["cost"]

    os.remove(sub)
    dopo = cm.collect(base, PRICING, True, 300, quiet=True)
    verifica("resta una sessione sola", len(dopo), 1)
    verifica("non e' archiviata: un file ce l'ha ancora",
             dopo[0].get("archiviata"), None)
    verifica("il costo cala, perche' quel lavoro non si legge piu'",
             dopo[0]["cost"] < costo_pieno, True)
    con = sqlite3.connect(os.path.join(tmp, "cm-local.db"))
    riga = con.execute("SELECT origine, file_totali, file_mancanti "
                       "FROM sessione").fetchone()
    con.close()
    verifica("ma l'archivio dice di quanti file manca", riga, ("acquisito", 2, 1))


def prova_filtro_progetto(tmp, base):
    print("\nIl filtro --project vale anche per le sessioni senza piu' file")
    path = scrivi(base, [prompt(0, "uno"), risposta(5, "req-1")])
    cm.collect(base, PRICING, True, 300, quiet=True)
    os.remove(path)
    verifica("col progetto giusto si vede",
             len(cm.collect(base, PRICING, True, 300, project="progetto", quiet=True)), 1)
    verifica("con un altro no",
             len(cm.collect(base, PRICING, True, 300, project="altro", quiet=True)), 0)
    verifica("senza filtro si vede", len(cm.collect(base, PRICING, True, 300, quiet=True)), 1)


def main() -> int:
    print("=" * 72)
    print("Prove sull'archivio locale")
    print("=" * 72)
    prove = [prova_cache, prova_formato, prova_potatura,
             prova_potatura_altra_base, prova_turni_riscritti,
             prova_import_json, prova_svuota, prova_collect,
             prova_testo, prova_ricerca_testo, prova_manutenzione,
             prova_sessione_sopravvissuta,
             prova_sessione_dimezzata, prova_filtro_progetto]
    # `collect` apre l'archivio accanto allo script: durante le prove va
    # spostato altrove, se no si scrive sul cm-local.db vero.
    vero_db_path = ar.db_path
    for prova in prove:
        tmp = tempfile.mkdtemp(prefix="cm-arch-")
        base = os.path.join(tmp, "projects")
        os.makedirs(base, exist_ok=True)
        ar.db_path = lambda _a=None, _t=tmp: os.path.join(_t, "cm-local.db")
        try:
            prova(tmp, base)
        finally:
            ar.db_path = vero_db_path
            shutil.rmtree(tmp, ignore_errors=True)

    print()
    print("=" * 72)
    fallite = [n for ok, n in esiti if not ok]
    if fallite:
        print(f"{len(fallite)} prove fallite su {len(esiti)}:")
        for n in fallite:
            print("  -", n)
        return 1
    print(f"tutte le {len(esiti)} prove sull'archivio superate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
