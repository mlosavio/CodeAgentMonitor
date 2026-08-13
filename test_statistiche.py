#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Prove sugli andamenti e sugli indicatori.

Un grafico sbagliato non si schianta: disegna una riga plausibile e convince.
Le prove qui sotto guardano i punti in cui una statistica dice una bugia senza
accorgersene:

  - **i periodi vuoti**: una settimana senza lavoro deve valere zero ed esserci,
    non sparire accostando due punti lontani;
  - **i rapporti aggregati**: la cache hit di un periodo si calcola sui token di
    tutto il periodo, non facendo la media delle percentuali dei singoli turni —
    che darebbe lo stesso peso a un turno da mille token e a uno da un milione;
  - **la mediana**, che sulle durate e' l'unica cosa che regge due sessioni
    lasciate aperte tutta la notte;
  - **il confronto con zero**, che non e' «piu' infinito per cento» ma un inizio;
  - **il verso**, cioe' quali indicatori possono permettersi una freccia verde.

    python test_statistiche.py
"""

from __future__ import annotations

import datetime as dt
import os
import sqlite3
import sys
import tempfile

import cm_statistiche as st

try:  # console Windows: senza questo l'output rediretto muore sugli accenti
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover
    pass

esiti: list[tuple[bool, str]] = []


def verifica(nome: str, ottenuto, atteso) -> None:
    ok = ottenuto == atteso
    esiti.append((ok, nome))
    print(f"  {'ok  ' if ok else 'FALLITA'}  {nome:<52} {ottenuto!r}"
          + ("" if ok else f"   atteso {atteso!r}"))


def quasi(nome: str, ottenuto, atteso, eps=1e-9) -> None:
    ok = ottenuto is not None and abs(ottenuto - atteso) <= eps
    esiti.append((ok, nome))
    print(f"  {'ok  ' if ok else 'FALLITA'}  {nome:<52} {ottenuto!r}"
          + ("" if ok else f"   atteso {atteso!r}"))


def ts(giorno: str, ora: str = "10:00") -> float:
    return dt.datetime.strptime(f"{giorno} {ora}", "%Y-%m-%d %H:%M").timestamp()


def turno(giorno, costo=1.0, durata=60.0, req=1, tool=0, interrotto=False,
          sessione="s1", progetto="p1", cache_read=90, input_=10, ora="10:00") -> dict:
    return {
        "ts": ts(giorno, ora), "cost": costo, "duration": durata,
        "requests": req, "tools": tool, "interrupted": interrotto,
        "session_id": sessione, "project": progetto,
        "tokens": {"input": input_, "cache_read": cache_read,
                   "cache_w5m": 0, "cache_w1h": 0, "output": 500},
    }


# --------------------------------------------------------------------------- #


def prova_bucket():
    print("\nDove cade un istante")
    # 2026-03-04 e' un mercoledi'
    verifica("il giorno e' se stesso",
             st.inizio_bucket(ts("2026-03-04"), "giorno"), dt.date(2026, 3, 4))
    verifica("la settimana comincia di lunedi'",
             st.inizio_bucket(ts("2026-03-04"), "settimana"), dt.date(2026, 3, 2))
    verifica("un lunedi' resta se stesso",
             st.inizio_bucket(ts("2026-03-02"), "settimana"), dt.date(2026, 3, 2))
    verifica("una domenica torna al lunedi' prima",
             st.inizio_bucket(ts("2026-03-08"), "settimana"), dt.date(2026, 3, 2))
    verifica("il mese e' il primo",
             st.inizio_bucket(ts("2026-03-04"), "mese"), dt.date(2026, 3, 1))
    verifica("dopo dicembre viene gennaio",
             st.bucket_successivo(dt.date(2026, 12, 1), "mese"), dt.date(2027, 1, 1))
    verifica("febbraio non inciampa",
             st.bucket_successivo(dt.date(2026, 2, 1), "mese"), dt.date(2026, 3, 1))
    verifica("granularita' inventata: errore chiaro",
             _errore(lambda: st.inizio_bucket(0, "trimestre")), True)


def _errore(f) -> bool:
    try:
        f()
    except ValueError:
        return True
    except Exception:
        return False
    return False


def prova_periodi_vuoti():
    print("\nI periodi senza lavoro ci sono, e valgono zero")
    turni = [turno("2026-03-02"), turno("2026-03-23")]   # tre settimane di distanza
    s = st.serie(turni, "settimana")
    verifica("quattro settimane, buchi compresi", len(s), 4)
    verifica("le etichette sono consecutive", [b["etichetta"] for b in s],
             ["02/03", "09/03", "16/03", "23/03"])
    verifica("le settimane in mezzo valgono zero",
             [b["turni"] for b in s], [1, 0, 0, 1])
    verifica("e non hanno un costo inventato",
             [b["costo"] for b in s], [1.0, 0.0, 0.0, 1.0])
    verifica("un rapporto senza dati e' None, non zero",
             [b["cache_hit"] for b in s][1], None)
    verifica("e nemmeno una durata mediana",
             [b["durata_mediana"] for b in s][1], None)
    verifica("nessun turno: nessuna serie", st.serie([], "settimana"), [])


def prova_metriche():
    print("\nLe metriche del periodo")
    turni = [
        turno("2026-03-02", costo=2.0, durata=10, req=3, tool=5, sessione="a", progetto="x"),
        turno("2026-03-03", costo=4.0, durata=30, req=1, tool=1, sessione="b", progetto="y"),
        turno("2026-03-04", costo=6.0, durata=110, req=2, tool=0, sessione="a",
              progetto="x", interrotto=True),
    ]
    b = st.serie(turni, "settimana")[0]
    quasi("costo sommato", b["costo"], 12.0)
    verifica("turni contati", b["turni"], 3)
    quasi("tempo nei turni", b["durata_totale"], 150.0)
    verifica("sessioni distinte", b["sessioni"], 2)
    verifica("progetti distinti", b["progetti"], 2)
    quasi("costo per turno", b["costo_turno"], 4.0)
    quasi("durata mediana, non media", b["durata_mediana"], 30.0)
    verifica("interrotti contati", b["interrotti"], 1)
    quasi("quota interrotti", b["quota_interrotti"], 1 / 3)


def prova_cache_pesata():
    print("\nLa cache hit si calcola sui token, non sulle percentuali")
    # un turno piccolissimo con cache pessima, uno enorme con cache ottima:
    # la media delle due percentuali direbbe ~50%, la verita' e' ~99%
    turni = [
        turno("2026-03-02", cache_read=0, input_=100),          # 0%
        turno("2026-03-03", cache_read=999_900, input_=100),    # 99.99%
    ]
    b = st.serie(turni, "settimana")[0]
    media_percentuali = (0 + 999_900 / 1_000_000) / 2
    quasi("aggregata sui token", b["cache_hit"], 999_900 / 1_000_100, eps=1e-6)
    verifica("e non e' la media delle percentuali",
             abs(b["cache_hit"] - media_percentuali) > 0.4, True)


def prova_mediana():
    print("\nMediana")
    verifica("lista vuota", st.mediana([]), None)
    verifica("i None si saltano", st.mediana([None, None]), None)
    quasi("dispari", st.mediana([3, 1, 2]), 2.0)
    quasi("pari", st.mediana([1, 2, 3, 4]), 2.5)
    quasi("un valore anomalo non la sposta",
          st.mediana([10, 11, 12, 100_000]), 11.5)


def prova_indicatori():
    print("\nIndicatori e confronto col periodo prima")
    ora = [turno("2026-03-10", costo=3.0), turno("2026-03-11", costo=3.0)]
    prima = [turno("2026-03-03", costo=2.0)]
    ind = {k["key"]: k for k in st.indicatori(ora, prima)}
    verifica("turni ora", ind["turni"]["valore"], 2)
    verifica("turni prima", ind["turni"]["precedente"], 1)
    quasi("variazione", ind["turni"]["delta"], 1.0)
    verifica("giorni di lavoro", ind["giorni_attivi"]["valore"], 2)
    quasi("costo per turno", ind["costo_turno"]["valore"], 3.0)
    quasi("in calo rispetto a prima", ind["costo_turno"]["delta"], 0.5)
    verifica("ogni indicatore ha la sua spiegazione",
             all(k.get("nota") for k in st.indicatori(ora, prima)), True)


def prova_verso():
    print("\nIl verso: chi puo' permettersi una freccia colorata")
    versi = {k["key"]: k["verso"] for k in st.indicatori([], [])}
    verifica("cache hit: salire e' meglio", versi["cache_hit"], "su")
    verifica("turni interrotti: salire e' peggio", versi["quota_interrotti"], "giu")
    verifica("giorni di lavoro: salire e' meglio", versi["giorni_attivi"], "su")
    verifica("costo per turno: dipende, quindi niente colore",
             versi["costo_turno"], None)
    verifica("durata mediana: dipende", versi["durata_mediana"], None)
    verifica("turni: dipende", versi["turni"], None)


def prova_delta_da_zero():
    print("\nDa zero a qualcosa non e' una percentuale")
    ora = [turno("2026-03-10", interrotto=True)]
    prima = [turno("2026-03-03", interrotto=False)]
    ind = {k["key"]: k for k in st.indicatori(ora, prima)}
    verifica("prima era zero: nessun confronto",
             ind["quota_interrotti"]["delta"], None)
    verifica("ma il valore c'e'", ind["quota_interrotti"]["valore"], 1.0)
    vuoto = {k["key"]: k for k in st.indicatori([], [])}
    verifica("nessun turno da nessuna parte", vuoto["costo_turno"]["valore"], None)
    verifica("e nessuna variazione", vuoto["costo_turno"]["delta"], None)
    solo_ora = {k["key"]: k for k in st.indicatori(ora, None)}
    verifica("senza periodo precedente non si confronta",
             solo_ora["turni"]["delta"], None)


def prova_progetti_nuovi():
    print("\nProgetti nuovi")
    storia = [turno("2026-01-05", progetto="vecchio")]
    ora = [turno("2026-03-10", progetto="vecchio"),
           turno("2026-03-11", progetto="nuovo")]
    ind = {k["key"]: k for k in st.indicatori(ora, [], storia)}
    verifica("uno solo e' nuovo", ind["progetti_nuovi"]["valore"], 1)
    verifica("i progetti toccati sono due", ind["progetti"]["valore"], 2)
    tutti_gia_visti = {k["key"]: k for k in st.indicatori(ora, [], ora)}
    verifica("se erano gia' tutti visti, zero",
             tutti_gia_visti["progetti_nuovi"]["valore"], 0)


def prova_finestra():
    print("\nLa finestra precedente ha la stessa lunghezza, e non si sovrappone")
    turni = [turno("2026-03-01"), turno("2026-03-07"), turno("2026-03-08"),
             turno("2026-03-14")]
    prec = st.finestra_precedente(turni, dt.date(2026, 3, 8), dt.date(2026, 3, 15))
    giorni = sorted(dt.datetime.fromtimestamp(t["ts"]).date().isoformat() for t in prec)
    verifica("prende i sette giorni prima", giorni, ["2026-03-01", "2026-03-07"])
    verifica("e non il primo giorno della finestra",
             "2026-03-08" in giorni, False)


def prova_grana():
    print("\nQuale ampiezza conviene")
    d = dt.date(2026, 1, 1)
    verifica("due settimane: a giorni",
             st.grana_consigliata(d, d + dt.timedelta(days=14)), "giorno")
    verifica("sei mesi: a settimane",
             st.grana_consigliata(d, d + dt.timedelta(days=180)), "settimana")
    verifica("tre anni: a mesi",
             st.grana_consigliata(d, d + dt.timedelta(days=1100)), "mese")
    verifica("un giorno solo non fa saltare niente",
             st.grana_consigliata(d, d), "giorno")


def prova_intervallo():
    print("\nIntervallo coperto")
    verifica("senza turni non c'e' intervallo", st.intervallo([]), None)
    verifica("senza timestamp nemmeno", st.intervallo([{"ts": None}]), None)
    verifica("primo e ultimo giorno",
             st.intervallo([turno("2026-03-04"), turno("2026-03-01")]),
             (dt.date(2026, 3, 1), dt.date(2026, 3, 4)))


def prova_adozione_team():
    print("\nPostazioni attive, dall'archivio del raccoglitore")
    verifica("senza archivio: niente, e non e' un errore",
             st.adozione_team(None), [])
    tmp = tempfile.mkdtemp(prefix="cm-stat-")
    try:
        con = sqlite3.connect(os.path.join(tmp, "t.db"))
        con.execute("CREATE TABLE sessions (machine TEXT, session_id TEXT,"
                    " user_key TEXT, project TEXT, started REAL, cost REAL)")
        con.executemany("INSERT INTO sessions VALUES (?,?,?,?,?,?)", [
            ("m1", "s1", "tizio", "p", ts("2026-01-10"), 10.0),
            ("m1", "s2", "tizio", "p", ts("2026-01-20"), 5.0),
            ("m2", "s3", "caio", "p", ts("2026-01-21"), 7.0),
            ("m3", "s4", "sempronio", "p", ts("2026-03-02"), 3.0),
        ])
        con.commit()
        righe = st.adozione_team(con, "mese")
        verifica("tre mesi, febbraio compreso", len(righe), 3)
        verifica("gennaio: due postazioni", righe[0]["postazioni"], 2)
        verifica("febbraio: nessuna, ma il mese c'e'", righe[1]["postazioni"], 0)
        verifica("marzo: una", righe[2]["postazioni"], 1)
        quasi("il costo di gennaio", righe[0]["costo"], 22.0)
        verifica("sessioni di gennaio", righe[0]["sessioni"], 3)
        con.execute("DROP TABLE sessions")
        verifica("archivio senza la tabella: niente, senza schiantarsi",
                 st.adozione_team(con), [])
        con.close()
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    print("=" * 72)
    print("Prove sugli andamenti")
    print("=" * 72)
    for prova in (prova_bucket, prova_periodi_vuoti, prova_metriche,
                  prova_cache_pesata, prova_mediana, prova_indicatori,
                  prova_verso, prova_delta_da_zero, prova_progetti_nuovi,
                  prova_finestra, prova_grana, prova_intervallo,
                  prova_adozione_team):
        prova()

    print()
    print("=" * 72)
    fallite = [n for ok, n in esiti if not ok]
    if fallite:
        print(f"{len(fallite)} prove fallite su {len(esiti)}:")
        for n in fallite:
            print("  -", n)
        return 1
    print(f"tutte le {len(esiti)} prove sugli andamenti superate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
