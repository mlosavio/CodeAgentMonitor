#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Prova di carico: molte postazioni che scrivono insieme.

Il raccoglitore tiene una sola connessione SQLite sotto lock. Con una macchina
non si nota; con venti che spediscono nello stesso momento e' il punto dove le
cose si rompono — o silenziosamente perdono righe, che e' peggio.

Questa prova simula N postazioni concorrenti e verifica tre cose:
  1. nessun invio viene perso o rifiutato
  2. nessuna riga viene contata due volte
  3. il tempo di risposta resta utilizzabile

Archivio temporaneo, cancellato alla fine.

    python test_carico.py             # 20 postazioni, 10 invii ciascuna
    python test_carico.py 50 20       # 50 postazioni, 20 invii ciascuna
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer

import cam_collector as cc

try:  # console Windows: senza questo l'output rediretto muore sugli accenti
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover
    pass


POSTAZIONI = int(sys.argv[1]) if len(sys.argv) > 1 else 20
INVII = int(sys.argv[2]) if len(sys.argv) > 2 else 10
SESSIONI_PER_INVIO = 5

esiti: list[tuple[bool, str]] = []


def verifica(nome: str, ottenuto, atteso) -> None:
    buono = ottenuto == atteso
    esiti.append((buono, nome))
    print(f"  {'ok  ' if buono else 'FALLITO'}  {nome:<46} {ottenuto!r}"
          + ("" if buono else f"   atteso {atteso!r}"))


cartella = tempfile.mkdtemp(prefix="cam-carico-")
store = cc.Store(f"{cartella}/carico.db",
                 cc.make_privacy("pseudonimo", f"{cartella}/k.key"), "pseudonimo")
cc.Handler.store = store
cc.Handler.verbose = False
cc.Handler.token = None
httpd = ThreadingHTTPServer(("127.0.0.1", 0), cc.Handler)
httpd.daemon_threads = True
# La porta la sceglie il server: fra lo sceglierla e l'usarla non c'e'
# nessuna finestra in cui un altro processo possa infilarcisi.
base = f"http://127.0.0.1:{httpd.server_address[1]}"
threading.Thread(target=httpd.serve_forever, daemon=True).start()
time.sleep(0.3)

ORA = time.time()
tempi: list[float] = []
errori: list[str] = []
lock = threading.Lock()


def una_postazione(n: int) -> None:
    """Una macchina che spedisce piu' volte, come farebbe cam_agent."""
    utente = f"utente{n:03d}@azienda.it"
    for giro in range(INVII):
        sessioni = []
        for s in range(SESSIONI_PER_INVIO):
            sid = f"m{n:03d}-s{s:02d}"
            # ogni giro la sessione "cresce": stesso id, fine piu' avanti
            sessioni.append({
                "session_id": sid, "project": f"Progetto{s % 3}",
                "start": ORA - 86400, "end": ORA + giro,
                "duration": 3600.0, "active": 60.0 * (giro + 1),
                "user_prompts": giro + 1, "assistant_msgs": 10 * (giro + 1),
                "tool_calls": 20, "cost": 1.0 * (giro + 1), "real_cost": 0.0,
                "billing": "subscription",
                "tokens": {"input": 10, "output": 20, "cache_read": 1000},
                "per_month": {"2026-08": {"claude-opus-5": {"cost": 1.0}}},
            })
        corpo = json.dumps({
            "agent": "cam-agent/carico", "machine": f"m-{n:03d}", "user": utente,
            "attrs": {"user.email": utente}, "sessions": sessioni,
        }).encode()
        req = urllib.request.Request(f"{base}/v1/sessions", corpo,
                                     {"Content-Type": "application/json"})
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                r.read()
                if r.status != 200:
                    with lock:
                        errori.append(f"m-{n:03d} giro {giro}: HTTP {r.status}")
        except Exception as exc:
            with lock:
                errori.append(f"m-{n:03d} giro {giro}: {exc}")
        with lock:
            tempi.append(time.perf_counter() - t0)


try:
    print(f"\n{POSTAZIONI} postazioni, {INVII} invii ciascuna, "
          f"{SESSIONI_PER_INVIO} sessioni per invio")
    print(f"({POSTAZIONI * INVII} richieste, "
          f"{POSTAZIONI * INVII * SESSIONI_PER_INVIO} sessioni scritte)")
    print("-" * 72)

    inizio = time.perf_counter()
    with ThreadPoolExecutor(max_workers=POSTAZIONI) as pool:
        list(pool.map(una_postazione, range(POSTAZIONI)))
    durata = time.perf_counter() - inizio

    print()
    print("Tenuta")
    print("-" * 72)
    verifica("nessun errore di rete o rifiuto", errori[:3], [])
    verifica("tutte le richieste hanno risposto", len(tempi),
             POSTAZIONI * INVII)

    # Ogni postazione ha SESSIONI_PER_INVIO sessioni distinte, non una per giro:
    # i giri successivi devono aggiornare, non moltiplicare.
    n_righe = store.query("SELECT COUNT(*) AS n FROM sessions")[0]["n"]
    verifica("nessuna riga duplicata dai giri successivi", n_righe,
             POSTAZIONI * SESSIONI_PER_INVIO)
    n_persone = store.query(
        "SELECT COUNT(DISTINCT user_key) AS n FROM sessions")[0]["n"]
    verifica("una postazione per macchina, nessuna fusa", n_persone, POSTAZIONI)

    # L'ultimo giro e' quello con i numeri piu' alti: deve aver vinto.
    atteso_costo = float(POSTAZIONI * SESSIONI_PER_INVIO * INVII)
    letto = store.query("SELECT SUM(cost) AS c FROM sessions")[0]["c"]
    verifica("ha vinto l'invio piu' recente, non l'ultimo arrivato",
             round(letto, 6), atteso_costo)

    righe = cc.team_rows(store)
    verifica("il pannello vede tutte le postazioni", len(righe), POSTAZIONI)
    verifica("e il totale coincide",
             round(sum(r["cost"] for r in righe), 6), atteso_costo)

    print()
    print("Tempi")
    print("-" * 72)
    tempi.sort()
    medio = sum(tempi) / len(tempi) * 1000
    p50 = tempi[len(tempi) // 2] * 1000
    p95 = tempi[int(len(tempi) * 0.95)] * 1000
    peggiore = tempi[-1] * 1000
    print(f"  medio {medio:7.1f} ms   mediano {p50:7.1f} ms   "
          f"95° {p95:7.1f} ms   peggiore {peggiore:7.1f} ms")
    print(f"  {len(tempi) / durata:.0f} richieste al secondo, "
          f"{durata:.1f} s in tutto")
    # Soglia larga di proposito: qui interessa scoprire un blocco, non
    # inseguire millisecondi. Un agente spedisce ogni 15 minuti.
    verifica("il caso peggiore resta sotto i 5 secondi", peggiore < 5000, True)

    if errori:
        print()
        print(f"  primi errori: {errori[:5]}")

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
print(f"tutte le {len(esiti)} prove di carico superate")
