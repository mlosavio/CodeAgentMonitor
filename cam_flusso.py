#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Modello o macchina: cosa parte verso Anthropic e cosa resta sul posto.

Una sessione di Claude Code e' fatta di due lavori che si alternano e che nei
totali si confondono:

  * le **richieste al modello** — ogni volta parte la conversazione intera,
    piu' le istruzioni e l'elenco degli strumenti;
  * il lavoro dell'**harness**, cioe' di Claude Code sulla macchina: leggere
    file, eseguire comandi, applicare modifiche. Il modello chiede, la
    macchina esegue, e al modello torna solo il risultato.

Dalla versione 2.1.309 il transcript registra le richieste per quello che
sono, in tre tipi di riga:

    api-request-shape   istruzioni di sistema + definizioni degli strumenti
    api-request-blob    un messaggio, identificato dal suo hash
    api-request         una richiesta: `base` (quella da cui parte), `keep`
                        (quanti suoi messaggi tiene) e `tail` (gli hash dei
                        messaggi aggiunti)

Quindi quante richieste, quando, e con quanti messaggi non si stima: si legge.
Per ogni richiesta, messaggi = messaggi della base[:keep] + tail. Il contenuto
dei messaggi sta in parte nei blob e in parte nelle righe normali della
conversazione (vedi `Flusso`).

Il lavoro locale sta invece nelle righe dei risultati: `toolUseResult` e'
quello che lo strumento ha prodotto sulla macchina, `message.content` e' la
parte che ne e' stata girata al modello. Per una lettura coincidono; per una
modifica no — in locale passa il file intero, al modello torna una riga.

Le grandezze sono **caratteri** di testo, non token: i token esatti li da'
gia' `usage`, ma solo come totale per richiesta, senza dire di chi sono.
I caratteri dicono la composizione; i token, il conto.

Questo modulo non importa `cam`: prende righe gia' decodificate e restituisce
numeri, cosi' si prova senza transcript veri.
"""

from __future__ import annotations

import collections
import datetime as dt
import glob
import json
import os

# Da dove viene un contenuto spedito al modello.
ORIGINI = ("tu", "harness", "strumenti", "modello")
ETICHETTA_ORIGINE = {
    "tu":        "scritto da te",
    "harness":   "aggiunto da Claude Code",
    "strumenti": "risultati degli strumenti",
    "modello":   "risposte del modello, rispedite",
}

# Dove gira uno strumento. E' la distinzione che il nome nasconde: «Read» e
# «WebSearch» sono entrambi "tool", ma uno legge il disco e l'altro no.
DOVE = ("locale", "mcp", "rete", "agenti")
ETICHETTA_DOVE = {
    "locale": "sulla macchina",
    "mcp":    "server MCP",
    "rete":   "in rete",
    "agenti": "altri agenti",
}
_RETE = {"WebSearch", "WebFetch"}
_AGENTI = {"Agent", "Task", "Workflow", "SendMessage"}

# Testo che Claude Code scrive in un messaggio "user" senza che l'utente
# l'abbia digitato: promemoria, contesto dell'editor, esito di comandi locali.
_INVOLUCRI = ("<system-reminder>", "<ide_opened_file>", "<ide_selection>",
              "<browser_instruction>", "<command-message>", "<command-name>",
              "<local-command-stdout>", "<local-command-stderr>",
              "<local-command-caveat>", "<task-notification>",
              "[request interrupted")


def peso(v) -> int:
    """Caratteri di testo dentro un valore JSON, qualunque forma abbia.

    Conta le stringhe e non la punteggiatura del JSON: un blocco annidato su
    quattro livelli non spedisce al modello piu' contenuto di uno piatto.
    """
    if isinstance(v, str):
        return len(v)
    if isinstance(v, dict):
        return sum(peso(x) for x in v.values())
    if isinstance(v, list):
        return sum(peso(x) for x in v)
    return 0


def dove_gira(nome: str) -> str:
    if nome.startswith("mcp__"):
        return "mcp"
    if nome in _RETE:
        return "rete"
    if nome in _AGENTI:
        return "agenti"
    return "locale"


def origine_testo(testo: str, subagent: bool = False) -> str:
    """Di chi e' un blocco di testo in un messaggio "user".

    Nel file di un subagent il testo libero non l'ha scritto una persona: e'
    il compito che gli ha dato il modello che lo ha lanciato.
    """
    inizio = (testo or "").lstrip()[:40].lower()
    if any(inizio.startswith(p) for p in _INVOLUCRI):
        return "harness"
    return "modello" if subagent else "tu"


def _parse_ts(value) -> float | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _vuoto() -> dict:
    return {k: 0 for k in ORIGINI}


def blocchi_di(message: dict, subagent: bool = False, meta: bool = False):
    """Un messaggio -> i suoi blocchi come (chiave, origine, caratteri).

    La chiave dice se due blocchi sono lo stesso contenuto: l'id per le
    chiamate agli strumenti e i loro risultati, il testo stesso per il resto.
    Serve perche' lo stesso contenuto compare due volte nel transcript — come
    riga della conversazione e dentro una richiesta registrata — e va contato
    una volta.
    """
    ruolo = message.get("role")
    content = message.get("content")
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    for block in content or []:
        if not isinstance(block, dict):
            continue
        tipo = block.get("type")
        if tipo == "tool_result":
            yield (("r", block.get("tool_use_id")), "strumenti",
                   peso(block.get("content")))
            continue
        if tipo == "tool_use":
            yield ("u", block.get("id")), "modello", peso(block.get("input"))
            continue
        if tipo == "text":
            testo = block.get("text") or ""
        elif tipo == "thinking":
            testo = block.get("thinking") or block.get("signature") or ""
        else:                              # immagini, allegati, aggiunte di strumenti
            testo = None
        n = len(testo) if testo is not None else peso(block)
        chiave = ("t", hash(testo)) if testo is not None else ("x", tipo, n)
        if ruolo == "assistant":
            origine = "modello"
        elif ruolo != "user" or meta:      # "system", o scritto dall'harness
            origine = "harness"
        elif testo is not None:
            origine = origine_testo(testo, subagent)
        else:
            origine = "modello" if subagent else "tu"
        yield chiave, origine, n


class Flusso:
    """Accumula, riga per riga, cosa un transcript ha spedito e cosa ha fatto.

    Come `TranscriptParser`, lo stato resta vivo dopo `snapshot()`: sulle
    sessioni in corso si ingeriscono solo le righe nuove.

    Il contenuto spedito arriva da **due** posti, e questo e' il punto delicato.
    Claude Code scrive un `api-request-blob` solo per i messaggi che non sono
    gia' una riga del transcript (il primo, coi promemoria; i messaggi di
    sistema; quelli che in viaggio hanno una forma diversa). Gli altri hash di
    `tail` rimandano alle righe "user" e "assistant" normali. Contare solo i
    blob perderebbe meta' dei risultati degli strumenti; contare tutto, li
    raddoppierebbe. Si contano entrambi, un blocco alla volta, una volta sola.
    """

    def __init__(self, subagent: bool = False):
        self.subagent = subagent
        self.blob: set[str] = set()               # hash dei blob gia' pesati
        self.shape: dict[str, int] = {}           # hash -> caratteri
        self.shape_viste: set[str] = set()
        self.per_id: set = set()                  # tool_use / tool_result contati
        self.t_righe: set = set()                 # testi contati dalle righe
        self.t_blob: set = set()                  # testi contati dai blob
        self.richieste = 0
        self.fonti: collections.Counter = collections.Counter()
        self.nuovo = _vuoto()
        self.impianto = 0                         # istruzioni + strumenti, la prima volta
        self.msg_spediti = 0                      # messaggi partiti, ripetizioni comprese
        self.msg_unici: set[str] = set()
        self.tempi: dict[str, list] = {}          # id richiesta -> [partenza, arrivo]
        self.usi: dict[str, tuple] = {}           # tool_use id -> (nome, ts)
        self.resi: set[str] = set()
        self.strumenti: dict[str, dict] = {}
        self.intervalli: list[tuple[float, float]] = []

    # -- ingestione --------------------------------------------------------- #

    def ingest(self, row: dict) -> None:
        tipo = row.get("type")
        if tipo == "api-request-blob":
            h = row.get("hash")
            if h and h not in self.blob:
                self.blob.add(h)
                self._conta(row.get("message") or {}, da_blob=True)
        elif tipo == "api-request-shape":
            h = row.get("shapeHash")
            if h and h not in self.shape:
                self.shape[h] = peso(row.get("shape"))
        elif tipo == "api-request":
            self._richiesta(row)
        elif tipo == "assistant":
            self._conta(dict(row.get("message") or {}, role="assistant"))
            self._risposta(row)
        elif tipo == "user":
            self._conta(dict(row.get("message") or {}, role="user"),
                        meta=bool(row.get("isMeta")))
            self._risultati(row)

    def _conta(self, message: dict, da_blob: bool = False, meta: bool = False) -> None:
        """Aggiunge al contenuto nuovo i blocchi che non si sono ancora visti.

        Dentro i blob lo stesso testo conta ogni volta: un promemoria ripetuto
        in due messaggi e' partito due volte. Fra le righe no — Claude Code
        riemette interi segmenti di storia (fork, --resume) — e fra una riga e
        un blob nemmeno, perche' sono lo stesso messaggio scritto due volte.
        """
        for chiave, origine, n in blocchi_di(message, self.subagent, meta):
            if chiave[0] in ("r", "u"):
                if chiave[1] is not None:
                    if chiave in self.per_id:
                        continue
                    self.per_id.add(chiave)
            elif da_blob:
                if chiave in self.t_righe:
                    continue
                self.t_blob.add(chiave)
            else:
                if chiave in self.t_righe or chiave in self.t_blob:
                    continue
                self.t_righe.add(chiave)
            self.nuovo[origine] += n

    def _richiesta(self, row: dict) -> None:
        rid = row.get("id")
        if not rid or rid in self.tempi:
            return                         # riemessa: gia' contata
        self.richieste += 1
        self.fonti[row.get("querySource") or "?"] += 1
        self.tempi[rid] = [_parse_ts(row.get("timestamp")), None]
        tail = row.get("tail") or []
        self.msg_spediti += max(0, int(row.get("keep") or 0)) + len(tail)
        self.msg_unici.update(tail)

        sh = row.get("shapeHash")
        if sh and sh not in self.shape_viste and sh in self.shape:
            self.shape_viste.add(sh)
            self.impianto += self.shape[sh]
            self.nuovo["harness"] += self.shape[sh]

    def _risposta(self, row: dict) -> None:
        ts = _parse_ts(row.get("timestamp"))
        tempo = self.tempi.get(row.get("requestRef"))
        if tempo is not None and ts is not None and (tempo[1] is None or ts > tempo[1]):
            tempo[1] = ts
        for block in (row.get("message") or {}).get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                bid = block.get("id")
                if bid and bid not in self.usi:
                    self.usi[bid] = (block.get("name") or "?", ts)

    def _risultati(self, row: dict) -> None:
        content = (row.get("message") or {}).get("content")
        if not isinstance(content, list):
            return
        blocchi = [b for b in content
                   if isinstance(b, dict) and b.get("type") == "tool_result"]
        if not blocchi:
            return
        ts = _parse_ts(row.get("timestamp"))
        locale = row.get("toolUseResult")
        for block in blocchi:
            bid = block.get("tool_use_id")
            if not bid or bid in self.resi:
                continue                   # storia riemessa: gia' contato
            self.resi.add(bid)
            nome, partenza = self.usi.get(bid, ("?", None))
            inviato = peso(block.get("content"))
            # Senza toolUseResult (versioni vecchie, permesso negato) in locale
            # non risulta niente di piu' di quello che e' partito.
            prodotto = inviato
            if locale is not None and len(blocchi) == 1:
                prodotto = peso(locale)
                if isinstance(locale, dict):
                    # Un output troppo lungo viene salvato su disco e al modello
                    # ne va un'anteprima: la misura vera e' quella del file.
                    prodotto = max(prodotto, int(locale.get("persistedOutputSize") or 0))
            s = self.strumenti.setdefault(nome, {
                "n": 0, "prodotto": 0, "inviato": 0, "tempo": 0.0, "errori": 0})
            s["n"] += 1
            s["prodotto"] += max(prodotto, inviato)
            s["inviato"] += inviato
            if block.get("is_error") is True:
                s["errori"] += 1
            if ts is not None and partenza is not None and ts >= partenza:
                s["tempo"] += ts - partenza
                if dove_gira(nome) != "agenti":
                    self.intervalli.append((partenza, ts))

    # -- risultato ---------------------------------------------------------- #

    def snapshot(self) -> dict:
        """Parziale di un file. Sommabile con quelli degli altri file della sessione."""
        return {
            "richieste": self.richieste,
            "fonti": dict(self.fonti),
            "nuovo": dict(self.nuovo),
            "impianto": self.impianto,
            "msg_spediti": self.msg_spediti,
            "msg_unici": len(self.msg_unici),
            "tempo_modello": sum(b - a for a, b in self.tempi.values()
                                 if a is not None and b is not None and b >= a),
            "strumenti": {n: dict(s) for n, s in self.strumenti.items()},
            "intervalli": list(self.intervalli),
        }


def unione(intervalli) -> float:
    """Durata coperta da un insieme di intervalli, contando una volta le sovrapposizioni.

    Tre letture lanciate insieme durano quanto la piu' lenta, non la somma.
    """
    totale, fine = 0.0, None
    for a, b in sorted(intervalli):
        if fine is None or a > fine:
            totale += b - a
            fine = b
        elif b > fine:
            totale += b - fine
            fine = b
    return totale


def somma(parziali: list[dict]) -> dict:
    """I parziali dei file di una sessione -> il quadro della sessione.

    `misurato` e' False quando nessun file contiene richieste registrate
    (Claude Code precedente alla 2.1.309). Il contenuto si ricava lo stesso
    dalle righe della conversazione, ma manca tutto quello che l'harness
    aggiunge per conto suo — istruzioni, strumenti, promemoria — e il tempo
    passato ad aspettare il modello: chi mostra deve dire «—», non zero.
    """
    nuovo = _vuoto()
    fonti: collections.Counter = collections.Counter()
    strumenti: dict[str, dict] = {}
    intervalli: list = []
    out = {"richieste": 0, "impianto": 0, "msg_spediti": 0, "msg_unici": 0,
           "tempo_modello": 0.0}
    for p in parziali:
        for k in out:
            out[k] += p.get(k, 0)
        for k in ORIGINI:
            nuovo[k] += (p.get("nuovo") or {}).get(k, 0)
        fonti.update(p.get("fonti") or {})
        intervalli.extend(p.get("intervalli") or [])
        for nome, s in (p.get("strumenti") or {}).items():
            d = strumenti.setdefault(nome, {
                "n": 0, "prodotto": 0, "inviato": 0, "tempo": 0.0, "errori": 0})
            for k in d:
                d[k] += s.get(k, 0)

    per_dove = {d: {"n": 0, "prodotto": 0, "inviato": 0} for d in DOVE}
    righe = []
    for nome, s in strumenti.items():
        dove = dove_gira(nome)
        for k in ("n", "prodotto", "inviato"):
            per_dove[dove][k] += s[k]
        righe.append(dict(s, nome=nome, dove=dove,
                          trattenuto=max(0, s["prodotto"] - s["inviato"])))
    righe.sort(key=lambda r: (-r["prodotto"], r["nome"]))

    totale_nuovo = sum(nuovo.values())
    loc = per_dove["locale"]
    out.update({
        "misurato": out["richieste"] > 0,
        "nuovo": nuovo,
        "nuovo_totale": totale_nuovo,
        # Quante volte, in media, lo stesso messaggio e' partito: a ogni
        # richiesta riparte la conversazione intera.
        "ripetizione": quota(out["msg_spediti"], out["msg_unici"]),
        "fonti": dict(fonti),
        "strumenti": righe,
        "per_dove": per_dove,
        "esecuzioni": sum(d["n"] for d in per_dove.values()),
        "esecuzioni_locali": loc["n"],
        "prodotto_locale": loc["prodotto"],
        "inviato_locale": loc["inviato"],
        "trattenuto": max(0, loc["prodotto"] - loc["inviato"]),
        "tempo_strumenti": unione(intervalli),
    })
    return out


def quota(parte: float, tutto: float) -> float | None:
    """Rapporto, o None quando il denominatore non c'e': non e' uno zero."""
    return (parte / tutto) if tutto else None


# --------------------------------------------------------------------------- #
# Quali sessioni sono in corso
# --------------------------------------------------------------------------- #


def processo_vivo(pid) -> bool:
    """True se il processo esiste. Nel dubbio True: meglio una riga in piu'.

    Su Windows `os.kill(pid, 0)` non interroga, termina: si passa da OpenProcess.
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return True
    if pid <= 0:
        return True
    if os.name == "nt":
        try:
            import ctypes
            k = ctypes.windll.kernel32
            k.OpenProcess.restype = ctypes.c_void_p
            h = k.OpenProcess(0x1000, False, pid)      # QUERY_LIMITED_INFORMATION
            if not h:
                return k.GetLastError() == 5           # accesso negato: esiste
            code = ctypes.c_ulong()
            ok = k.GetExitCodeProcess(ctypes.c_void_p(h), ctypes.byref(code))
            k.CloseHandle(ctypes.c_void_p(h))
            return (not ok) or code.value == 259       # STILL_ACTIVE
        except Exception:
            return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except Exception:
        return True
    return True


def sessioni_attive(base: str, finestra: float = 900.0, adesso: float | None = None,
                    vivo=processo_vivo) -> list[dict]:
    """Le sessioni aperte adesso: {session_id, path, stato, nome}.

    Fonte prima: `~/.claude/sessions/<pid>.json`, che Claude Code tiene per
    ogni processo vivo e che dice anche se sta lavorando o aspetta. Un file
    rimasto da un processo morto si scarta guardando il pid.

    In mancanza — versioni che quei file non li scrivono — vale il transcript
    toccato negli ultimi `finestra` secondi.
    """
    out: dict[str, dict] = {}
    home = os.path.dirname(os.path.abspath(base))
    for path in glob.glob(os.path.join(home, "sessions", "*.json")):
        try:
            with open(path, encoding="utf-8") as fh:
                rec = json.load(fh)
        except Exception:
            continue
        if not isinstance(rec, dict) or not rec.get("sessionId"):
            continue
        if not vivo(rec.get("pid")):
            continue
        sid = rec["sessionId"]
        hits = glob.glob(os.path.join(base, "*", sid + ".jsonl"))
        if not hits:
            continue                       # aperta ma senza ancora un messaggio
        out[sid] = {"session_id": sid, "path": hits[0],
                    "stato": rec.get("status"), "nome": rec.get("name")}
    if out:
        return list(out.values())

    import time
    adesso = time.time() if adesso is None else adesso
    for path in glob.glob(os.path.join(base, "*", "*.jsonl")):
        try:
            if adesso - os.path.getmtime(path) > finestra:
                continue
        except OSError:
            continue
        sid = os.path.splitext(os.path.basename(path))[0]
        out[sid] = {"session_id": sid, "path": path, "stato": None, "nome": None}
    return list(out.values())
