#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Andamenti e indicatori, calcolati sui turni.

Qui dentro non si disegna e non si stampa niente: si producono numeri, e chi li
mostra decide come. Serve a due cose che altrimenti divergono — la vista nel
pannello e quella da riga di comando — e a poterli provare senza aprire una
finestra.

Tre scelte che vale la pena spiegare, perche' cambiano quello che si legge.

**I periodi vuoti ci sono.** Una settimana senza lavoro e' uno zero, non
un'assenza: saltarla accosterebbe due punti lontani e farebbe sembrare continuo
un uso che continuo non e' stato. La serie copre tutto l'intervallo, buchi
compresi.

**Le mediane, non le medie.** Sulle durate bastano due sessioni lasciate aperte
tutta la notte per spostare una media di ore. La mediana dice cosa succede di
solito, che e' la domanda vera.

**Ogni indicatore dichiara da che parte sta il bene.** «Cache hit» che sale e'
buono, «turni interrotti» che sale e' brutto, «costo per turno» che sale non e'
ne' l'uno ne' l'altro — dipende da cosa si stava facendo. Senza questa
informazione una freccia verde e' una bugia, quindi chi non ha un verso lo
dichiara e resta grigio.
"""

from __future__ import annotations

import datetime as dt

# --------------------------------------------------------------------------- #
# Bucket temporali
# --------------------------------------------------------------------------- #

GRANULARITA = (
    ("giorno", "Giorno", "%d/%m"),
    ("settimana", "Settimana", "%d/%m"),
    ("mese", "Mese", "%m/%Y"),
)
_GRANI = {g[0] for g in GRANULARITA}


def inizio_bucket(ts: float, grana: str) -> dt.date:
    """La data a cui appartiene un istante, secondo la granularita' scelta."""
    d = dt.datetime.fromtimestamp(ts).date()
    if grana == "giorno":
        return d
    if grana == "settimana":
        return d - dt.timedelta(days=d.weekday())   # lunedi'
    if grana == "mese":
        return d.replace(day=1)
    raise ValueError(f"granularita' sconosciuta: {grana!r}")


def bucket_successivo(d: dt.date, grana: str) -> dt.date:
    if grana == "giorno":
        return d + dt.timedelta(days=1)
    if grana == "settimana":
        return d + dt.timedelta(days=7)
    if grana == "mese":
        return (d.replace(day=28) + dt.timedelta(days=7)).replace(day=1)
    raise ValueError(f"granularita' sconosciuta: {grana!r}")


def grana_consigliata(da: dt.date, a: dt.date) -> str:
    """Quanti punti servono per vedere una forma senza vedere il rumore.

    Sotto i due mesi il giorno racconta qualcosa; oltre l'anno e mezzo il giorno
    e' una selva di righe e la settimana pure.
    """
    giorni = max(1, (a - da).days)
    if giorni <= 60:
        return "giorno"
    if giorni <= 550:
        return "settimana"
    return "mese"


def etichetta(d: dt.date, grana: str) -> str:
    if grana == "settimana":
        return d.strftime("%d/%m")
    if grana == "mese":
        return d.strftime("%m/%Y")
    return d.strftime("%d/%m")


# --------------------------------------------------------------------------- #
# Utilita'
# --------------------------------------------------------------------------- #

_TOKEN_IN = ("input", "cache_read", "cache_w5m", "cache_w1h")


def mediana(valori) -> float | None:
    xs = sorted(v for v in valori if v is not None)
    if not xs:
        return None
    m = len(xs) // 2
    return float(xs[m]) if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2.0


def _cache_hit(tok: dict) -> float | None:
    letti = tok.get("cache_read", 0)
    totale = sum(tok.get(k, 0) for k in _TOKEN_IN)
    return (letti / totale) if totale else None


def _somma_token(dst: dict, src: dict) -> None:
    for k, v in (src or {}).items():
        if isinstance(v, (int, float)):
            dst[k] = dst.get(k, 0) + v


# --------------------------------------------------------------------------- #
# Serie temporale
# --------------------------------------------------------------------------- #

# Cosa si puo' mettere su un grafico. Una metrica per grafico: due scale sullo
# stesso disegno inventano una correlazione che nei dati non c'e'.
#
# `zero` dice se l'asse deve partire da zero, e non e' un dettaglio estetico.
# Una somma (costo, turni) e' una quantita': si disegna riempita a partire da
# zero, perche' l'area *e'* la quantita'. Un livello (cache hit, durata mediana)
# non parte da niente: riempirlo da zero direbbe una cosa falsa, e schiacciarlo
# su un asse 0-100% renderebbe piatta una riga che invece si muove. I livelli si
# disegnano come linea sola, con l'asse adattato ai valori.
METRICHE = (
    {"key": "costo", "label": "Valore consumato", "formato": "usd", "zero": True,
     "nota": "Quanto varrebbe a listino API il lavoro svolto nel periodo."},
    {"key": "turni", "label": "Turni", "formato": "num", "zero": True,
     "nota": "Quante volte hai chiesto qualcosa. È la misura d'uso più diretta."},
    {"key": "durata_totale", "label": "Tempo nei turni", "formato": "dur", "zero": True,
     "nota": "Somma delle durate dei turni. Non è il «tempo attivo» delle altre "
             "schede, che toglie le pause: qui un turno fermo ad aspettare un "
             "permesso conta per intero."},
    {"key": "sessioni", "label": "Sessioni", "formato": "num", "zero": True,
     "nota": "Conversazioni distinte toccate nel periodo."},
    {"key": "progetti", "label": "Progetti", "formato": "num", "zero": True,
     "nota": "Progetti distinti su cui si è lavorato: la misura di quanto lo "
             "strumento si è allargato."},
    {"key": "cache_hit", "label": "Cache hit", "formato": "pct", "zero": False,
     "nota": "Quota dei token in ingresso arrivati dalla cache. Scende quando il "
             "contesto viene riscritto da capo, ed è lì che si paga."},
    {"key": "costo_turno", "label": "Costo per turno", "formato": "usd", "zero": False,
     "nota": "Quanto costa in media una richiesta. Sale se i turni diventano più "
             "grossi, non necessariamente se si spreca."},
    {"key": "durata_mediana", "label": "Durata mediana del turno", "formato": "dur",
     "zero": False,
     "nota": "Quanto dura di solito un turno. La mediana, non la media: due "
             "sessioni lasciate aperte sposterebbero la media di ore."},
    {"key": "quota_interrotti", "label": "Turni interrotti", "formato": "pct",
     "zero": True,
     "nota": "Quota di turni fermati a metà. È l'unico giudizio negativo "
             "esplicito che i transcript contengono."},
)
METRICA = {m["key"]: m for m in METRICHE}


def serie(turni: list[dict], grana: str = "settimana",
          da: dt.date | None = None, a: dt.date | None = None) -> list[dict]:
    """Un punto per periodo, con tutte le metriche calcolate insieme.

    Si calcola tutto in un passaggio invece di una funzione per metrica: i dati
    sono gli stessi, e cosi' un grafico che cambia metrica non rilegge niente.
    """
    if grana not in _GRANI:
        raise ValueError(f"granularita' sconosciuta: {grana!r}")
    datati = [t for t in turni if t.get("ts")]
    if not datati and not (da and a):
        return []

    if datati:
        primo = inizio_bucket(min(t["ts"] for t in datati), grana)
        ultimo = inizio_bucket(max(t["ts"] for t in datati), grana)
    else:
        primo = ultimo = inizio_bucket(dt.datetime.now().timestamp(), grana)
    if da:
        primo = min(primo, inizio_bucket(
            dt.datetime.combine(da, dt.time.min).timestamp(), grana))
    if a:
        ultimo = max(ultimo, inizio_bucket(
            dt.datetime.combine(a, dt.time.min).timestamp(), grana))

    vuoto = {}
    d = primo
    while d <= ultimo:
        vuoto[d] = {
            "inizio": d, "etichetta": etichetta(d, grana),
            "costo": 0.0, "turni": 0, "durata_totale": 0.0,
            "richieste": 0, "strumenti": 0, "interrotti": 0,
            "_sessioni": set(), "_progetti": set(), "_durate": [], "_token": {},
        }
        d = bucket_successivo(d, grana)

    for t in datati:
        b = vuoto.get(inizio_bucket(t["ts"], grana))
        if b is None:                      # fuori dall'intervallo richiesto
            continue
        b["turni"] += 1
        b["costo"] += t.get("cost") or 0.0
        b["richieste"] += t.get("requests") or 0
        b["strumenti"] += t.get("tools") or 0
        if t.get("interrupted"):
            b["interrotti"] += 1
        if t.get("duration") is not None:
            b["durata_totale"] += t["duration"]
            b["_durate"].append(t["duration"])
        if t.get("session_id"):
            b["_sessioni"].add(t["session_id"])
        if t.get("project"):
            b["_progetti"].add(t["project"])
        _somma_token(b["_token"], t.get("tokens"))

    out = []
    for d in sorted(vuoto):
        b = vuoto[d]
        b["sessioni"] = len(b.pop("_sessioni"))
        b["progetti"] = len(b.pop("_progetti"))
        b["durata_mediana"] = mediana(b.pop("_durate"))
        b["cache_hit"] = _cache_hit(b.pop("_token"))
        b["costo_turno"] = (b["costo"] / b["turni"]) if b["turni"] else None
        b["quota_interrotti"] = (b["interrotti"] / b["turni"]) if b["turni"] else None
        out.append(b)
    return out


# --------------------------------------------------------------------------- #
# Indicatori
# --------------------------------------------------------------------------- #

# verso: da che parte sta il bene. 'su' = salire e' un miglioramento,
# 'giu' = salire e' un peggioramento, None = dipende, e allora niente colore.
INDICATORI = (
    {"key": "turni", "label": "Turni", "formato": "num", "verso": None,
     "nota": "Quanto è stato usato nel periodo."},
    {"key": "giorni_attivi", "label": "Giorni di lavoro", "formato": "num", "verso": "su",
     "nota": "Giorni in cui c'è stato almeno un turno: dice se l'uso è "
             "continuo o a ondate."},
    {"key": "turni_per_giorno", "label": "Turni per giorno",
     "formato": "num1", "verso": None,
     "nota": "Intensità nei giorni in cui si lavora davvero, senza che i giorni "
             "fermi abbassino la media."},
    {"key": "progetti", "label": "Progetti", "formato": "num", "verso": "su",
     "nota": "Su quanti progetti distinti è stato usato: la misura di adozione "
             "in larghezza."},
    {"key": "progetti_nuovi", "label": "Progetti nuovi", "formato": "num", "verso": None,
     "nota": "Progetti mai visti nei periodi precedenti. Alto vuol dire che si "
             "sta allargando; zero, che si è assestato su quelli di sempre."},
    {"key": "costo_turno", "label": "Costo per turno", "formato": "usd", "verso": None,
     "nota": "Sale se i turni diventano più grossi. Non è di per sé uno spreco: "
             "va letto insieme alla cache hit."},
    {"key": "durata_mediana", "label": "Durata mediana",
     "formato": "dur", "verso": None,
     "nota": "Quanto dura di solito un turno, comprese le attese per i permessi "
             "e i comandi lenti."},
    {"key": "cache_hit", "label": "Cache hit", "formato": "pct", "verso": "su",
     "nota": "Quando scende, vuol dire che il contesto viene riscritto più "
             "spesso — ed è la voce più grossa del conto."},
    {"key": "quota_interrotti", "label": "Turni interrotti", "formato": "pct",
     "verso": "giu",
     "nota": "Quota di risposte fermate a metà. Se sale, qualcosa non sta "
             "andando nella direzione giusta."},
    {"key": "strumenti_turno", "label": "Strumenti per turno", "formato": "num1",
     "verso": None,
     "nota": "Quante letture, comandi e modifiche servono in media per turno."},
)


def _aggregato(turni: list[dict], visti_prima: set | None = None) -> dict:
    """I numeri grezzi di un insieme di turni."""
    tok: dict = {}
    durate, giorni, sessioni, progetti = [], set(), set(), set()
    costo = interrotti = richieste = strumenti = 0
    costo = 0.0
    for t in turni:
        costo += t.get("cost") or 0.0
        richieste += t.get("requests") or 0
        strumenti += t.get("tools") or 0
        if t.get("interrupted"):
            interrotti += 1
        if t.get("duration") is not None:
            durate.append(t["duration"])
        if t.get("ts"):
            giorni.add(dt.datetime.fromtimestamp(t["ts"]).date())
        if t.get("session_id"):
            sessioni.add(t["session_id"])
        if t.get("project"):
            progetti.add(t["project"])
        _somma_token(tok, t.get("tokens"))
    n = len(turni)
    nuovi = len(progetti - visti_prima) if visti_prima is not None else None
    return {
        "turni": n,
        "costo": costo,
        "sessioni": len(sessioni),
        "progetti": len(progetti),
        "progetti_nuovi": nuovi,
        "giorni_attivi": len(giorni),
        "turni_per_giorno": (n / len(giorni)) if giorni else None,
        "costo_turno": (costo / n) if n else None,
        "durata_mediana": mediana(durate),
        "cache_hit": _cache_hit(tok),
        "quota_interrotti": (interrotti / n) if n else None,
        "strumenti_turno": (strumenti / n) if n else None,
        "_progetti": progetti,
    }


def _delta(ora, prima) -> float | None:
    """Variazione relativa. None quando il confronto non direbbe niente.

    Da zero a qualcosa non e' «piu' infinito per cento»: e' un inizio, e va
    mostrato come tale invece che come una percentuale assurda.
    """
    if ora is None or prima is None:
        return None
    if prima == 0:
        return None
    return (ora - prima) / abs(prima)


def indicatori(turni: list[dict], precedenti: list[dict] | None = None,
               tutti_i_precedenti: list[dict] | None = None) -> list[dict]:
    """Gli indicatori del periodo, col confronto sul periodo precedente.

    `precedenti` sono i turni della finestra immediatamente prima, della stessa
    lunghezza: e' l'unico confronto onesto, perche' due finestre di durata
    diversa non sono paragonabili.

    `tutti_i_precedenti` serve solo a sapere quali progetti erano gia' stati
    visti, per contare quelli nuovi.
    """
    gia_visti = set()
    for t in (tutti_i_precedenti or []):
        if t.get("project"):
            gia_visti.add(t["project"])
    ora = _aggregato(turni, gia_visti)
    prima = _aggregato(precedenti or []) if precedenti is not None else None

    out = []
    for spec in INDICATORI:
        k = spec["key"]
        valore = ora.get(k)
        precedente = prima.get(k) if prima else None
        out.append({
            **spec,
            "valore": valore,
            "precedente": precedente,
            "delta": _delta(valore, precedente),
        })
    return out


def finestra_precedente(turni: list[dict], da: dt.date, a: dt.date) -> list[dict]:
    """I turni della finestra di pari lunghezza subito prima di [da, a)."""
    giorni = max(1, (a - da).days)
    inizio = da - dt.timedelta(days=giorni)
    t0 = dt.datetime.combine(inizio, dt.time.min).timestamp()
    t1 = dt.datetime.combine(da, dt.time.min).timestamp()
    return [t for t in turni if t.get("ts") and t0 <= t["ts"] < t1]


def intervallo(turni: list[dict]) -> tuple[dt.date, dt.date] | None:
    """Primo e ultimo giorno coperti dai turni."""
    ts = [t["ts"] for t in turni if t.get("ts")]
    if not ts:
        return None
    return (dt.datetime.fromtimestamp(min(ts)).date(),
            dt.datetime.fromtimestamp(max(ts)).date())


# --------------------------------------------------------------------------- #
# Adozione di piu' postazioni (dall'archivio del raccoglitore)
# --------------------------------------------------------------------------- #


def adozione_team(con, grana: str = "mese") -> list[dict]:
    """Postazioni attive per periodo, dall'archivio del raccoglitore.

    Risponde alla domanda che in un gruppo conta piu' del consumo: quante delle
    postazioni pagate stanno davvero usando lo strumento, e se il numero cresce.
    Le postazioni ferme restano invisibili — chi non usa non manda niente — ed e'
    per questo che il totale pagato va dichiarato altrove.

    Ritorna [] se l'archivio non c'e' o non ha sessioni: e' la condizione
    normale di chi lavora da solo, non un errore.
    """
    if con is None:
        return []
    try:
        righe = con.execute(
            "SELECT started, user_key, session_id, cost FROM sessions"
            " WHERE started IS NOT NULL AND user_key IS NOT NULL").fetchall()
    except Exception:
        return []
    if not righe:
        return []

    per_bucket: dict[dt.date, dict] = {}
    for started, user_key, session_id, cost in righe:
        d = inizio_bucket(started, grana)
        b = per_bucket.setdefault(d, {"inizio": d, "etichetta": etichetta(d, grana),
                                      "_p": set(), "_s": set(), "costo": 0.0})
        b["_p"].add(user_key)
        b["_s"].add(session_id)
        b["costo"] += cost or 0.0

    if not per_bucket:
        return []
    d, ultimo = min(per_bucket), max(per_bucket)
    out = []
    while d <= ultimo:
        b = per_bucket.get(d) or {"inizio": d, "etichetta": etichetta(d, grana),
                                  "_p": set(), "_s": set(), "costo": 0.0}
        out.append({"inizio": b["inizio"], "etichetta": b["etichetta"],
                    "postazioni": len(b["_p"]), "sessioni": len(b["_s"]),
                    "costo": b["costo"]})
        d = bucket_successivo(d, grana)
    return out
