#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Archivio locale di CodeAgentMonitor: `cam-local.db`.

Prende il posto di `.cache.json` e fa due mestieri che prima erano uno solo.

**Cache di analisi** (tabella `file`). Una riga per transcript, con dimensione e
data: se non sono cambiate, il record derivato si riusa invece di rileggere il
file. E' esattamente quello che faceva la cache JSON, ma senza riscrivere tutto
il blocco a ogni giro e senza buttare via tutto quando cambia il formato del
parser.

**Archivio** (tabelle `sessione` e `turno`). La proiezione interrogabile di
quello che si e' misurato. Oggi si potrebbe rifare da capo dai transcript; e
proprio per questo ogni riga dice da dove viene:

    origine = 'derivato'   il file c'e' ancora: si puo' ricostruire
    origine = 'acquisito'  il file non c'e' piu', o non c'e' mai stato

La distinzione non e' burocrazia. Da lei discende la regola che tiene in piedi
tutto il resto: **si puo' cancellare e ricostruire solo cio' che e' derivato**.
Un cambio di schema sulla parte Claude Code costa quanto costa oggi — butta e
rileggi — e solo le righe acquisite hanno bisogno di una migrazione vera. Il
giorno in cui le due cose non si distinguono piu' e' il giorno in cui
dell'archivio non si fida piu' nessuno.

Le colonne `fonte` e `origine` ci sono da subito, con Claude Code come unico
valore possibile: servono a GitHub Copilot, che non ha un transcript a cui
tornare e potra' solo scrivere righe acquisite.

Qui **non entra il testo dei messaggi**: solo numeri e il primo pezzo dei tuoi
prompt, che serve a riconoscere un turno in un elenco. Archiviare le
conversazioni e' una decisione a parte, e va chiesta.

Solo stdlib.
"""

from __future__ import annotations

import bisect
import json
import os
import sqlite3
import time
import zlib

SCHEMA = 2

DDL = """
CREATE TABLE IF NOT EXISTS meta (
    chiave TEXT PRIMARY KEY,
    valore TEXT
);

-- Cache di analisi: un transcript per riga. `rec` e' il record derivato dal
-- parser, in JSON compresso — se cambia lo schema del parser questa tabella si
-- svuota e basta, l'archivio non si tocca. Le righe vecchie in chiaro si
-- rileggono lo stesso: vedi `spacchetta`.
CREATE TABLE IF NOT EXISTS file (
    path       TEXT PRIMARY KEY,
    size       INTEGER NOT NULL,
    mtime      REAL    NOT NULL,
    session_id TEXT,
    subagent   INTEGER NOT NULL DEFAULT 0,
    rec        BLOB    NOT NULL,
    letto      REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS file_sessione ON file(session_id);

CREATE TABLE IF NOT EXISTS sessione (
    session_id     TEXT PRIMARY KEY,
    fonte          TEXT    NOT NULL DEFAULT 'claude-code',
    origine        TEXT    NOT NULL DEFAULT 'derivato',
    file_totali    INTEGER NOT NULL DEFAULT 0,
    file_mancanti  INTEGER NOT NULL DEFAULT 0,
    progetto       TEXT,
    cwd            TEXT,
    titolo         TEXT,
    primo_prompt   TEXT,
    branch         TEXT,
    versione       TEXT,
    inizio         REAL,
    fine           REAL,
    durata         REAL,
    attivo         REAL,
    prompt_utente  INTEGER,
    msg_assistant  INTEGER,
    tool           INTEGER,
    subagent_file  INTEGER,
    errori_api     INTEGER,
    costo          REAL,
    cache_hit      REAL,
    turni          INTEGER,
    span           INTEGER,
    durata_mediana REAL,
    token          TEXT,
    per_modello    TEXT,
    per_mese       TEXT,
    aggiornato     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS sessione_fine ON sessione(fine DESC);

CREATE TABLE IF NOT EXISTS turno (
    session_id TEXT    NOT NULL,
    n          INTEGER NOT NULL,
    inizio     REAL,
    fine       REAL,
    durata     REAL,
    prompt     TEXT,
    richieste  INTEGER,
    tool       INTEGER,
    span       INTEGER,
    subagent   INTEGER,
    interrotto INTEGER,
    costo      REAL,
    cache_hit  REAL,
    token      TEXT,
    modelli    TEXT,
    strumenti  TEXT,
    PRIMARY KEY (session_id, n)
);
CREATE INDEX IF NOT EXISTS turno_inizio ON turno(inizio DESC);

-- Il testo di quello che e' stato detto. Si riempie solo se `archivio.testo`
-- e' acceso in config.json: mettere le proprie conversazioni su disco e' una
-- decisione, non un default da scoprire dopo.
--
-- Ci sta la conversazione, NON i risultati degli strumenti: su una sessione
-- vera il testo e' l'1,6% del transcript e tutto il resto sono contenuti di
-- file e output di comandi, gia' su disco e che nessuno rilegge.
CREATE TABLE IF NOT EXISTS messaggio (
    id         INTEGER PRIMARY KEY,
    session_id TEXT    NOT NULL,
    turno      INTEGER,
    ts         REAL,
    ruolo      TEXT    NOT NULL,      -- 'tu' | 'claude'
    modello    TEXT,
    subagent   INTEGER NOT NULL DEFAULT 0,
    strumenti  TEXT,
    testo      TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS messaggio_sessione ON messaggio(session_id, ts);
CREATE INDEX IF NOT EXISTS messaggio_turno ON messaggio(session_id, turno);
"""

# Indice a testo pieno, tenuto a parte: se la copia di SQLite non ha FTS5 il
# programma deve funzionare lo stesso, con una ricerca piu' lenta invece che
# con un errore all'avvio.
DDL_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS messaggio_fts USING fts5(
    testo,
    content='messaggio',
    content_rowid='id',
    tokenize="unicode61 remove_diacritics 2"
);
"""


def eredita(nuovo: str, vecchio: str) -> str:
    """Raccoglie un file lasciato dal nome precedente del progetto.

    Il tool si chiamava `claude-monitor` e i suoi file `cm-*`. Cambiare nome
    senza portarsi dietro i dati vorrebbe dire ripartire da un archivio vuoto
    senza dirlo — e l'archivio, per le sessioni il cui transcript non c'e' piu',
    e' l'unica copia rimasta. Si sposta, non si copia: due archivi che divergono
    in silenzio sono peggio di uno solo.

    Non fa niente se il file nuovo esiste gia' o se il vecchio non c'e'.
    """
    if os.path.exists(nuovo) or not os.path.exists(vecchio):
        return nuovo
    try:
        os.replace(vecchio, nuovo)
        # SQLite lascia accanto il giornale: senza, il file arriva senza le
        # ultime scritture, cioe' danneggiato in modo non evidente.
        for coda in ("-wal", "-shm"):
            if os.path.exists(vecchio + coda):
                os.replace(vecchio + coda, nuovo + coda)
    except OSError:
        return vecchio
    return nuovo


def db_path(accanto_a: str | None = None) -> str:
    """L'archivio sta accanto allo script, come gia' `cam-team.db`."""
    base = accanto_a or os.path.dirname(os.path.abspath(__file__))
    return eredita(os.path.join(base, "cam-local.db"),
                   os.path.join(base, "cm-local.db"))


def chiave(path: str) -> str:
    """Forma canonica di un percorso.

    Su Windows lo stesso file arriva scritto in modi diversi a seconda di chi
    ha costruito il percorso: maiuscole, barre, percorsi relativi. Senza una
    forma unica lo stesso transcript finirebbe in archivio due volte, e la
    seconda copia sembrerebbe lavoro in piu' che non e' mai esistito.

    Su macOS e Linux `normcase` non tocca le maiuscole, ed e' giusto cosi':
    li' `Sessione.jsonl` e `SESSIONE.jsonl` sono due file diversi che possono
    esistere entrambi. Appiattirli renderebbe uno invisibile all'altro.
    """
    return os.path.normcase(os.path.abspath(path))


def _js(value) -> str:
    return json.dumps(value if value is not None else None,
                      ensure_ascii=False, sort_keys=True)


def impacchetta(rec: dict) -> bytes:
    """Il record di un transcript, compresso, per andare in cache.

    Dentro c'e' soprattutto la lista dei timestamp di ogni evento, che da sola
    pesa piu' di tutto il resto messo insieme. Il modo ovvio di rimpicciolirla
    sarebbe non conservarla — tenere minimo, massimo e la somma degli intervalli
    invece degli istanti — ma quella lista e' l'unica ragione per cui si puo'
    cambiare `--idle-gap` e rivedere i tempi senza rileggere i transcript.

    Comprimerla la riduce piu' di quanto la riduca buttarla via a meta' (misurato:
    a poco piu' di un quarto, contro un terzo), e non toglie niente a nessuno.
    """
    return zlib.compress(json.dumps(rec, ensure_ascii=False).encode("utf-8"), 6)


def spacchetta(blob) -> dict | None:
    """Il contrario, e anche il vecchio formato in chiaro.

    Le righe scritte prima sono testo JSON: si continuano a leggere invece di
    invalidare la cache, che vorrebbe dire rileggere tutti i transcript per un
    cambiamento che non riguarda cosa c'e' scritto ma come e' scritto.
    """
    try:
        if isinstance(blob, (bytes, bytearray)):
            return json.loads(zlib.decompress(blob).decode("utf-8"))
        return json.loads(blob)
    except (ValueError, TypeError, zlib.error):
        return None


def bisect_destra(valori: list[float], x: float) -> int:
    return bisect.bisect_right(valori, x)


def _query_fts(testo: str) -> str:
    """Trasforma quello che si e' digitato in una query FTS5 innocua.

    In FTS5 caratteri come `-`, `*`, `"` e `:` sono operatori: una ricerca
    scritta a mano diventa facilmente un errore di sintassi invece di un
    risultato vuoto. Ogni parola diventa una frase fra virgolette, e le parole
    si sommano: si cerca quello che si e' scritto, tutto.
    """
    parole = [p.replace('"', '""') for p in testo.split() if p.strip()]
    if not parole:
        return '""'
    return " ".join(f'"{p}"' for p in parole)


class Archivio:
    """L'archivio aperto. Una istanza per scansione, chiusa alla fine.

    Non e' pensato per essere condiviso fra thread: chi scansiona se lo apre e
    se lo chiude. Due processi insieme invece vanno bene — WAL e un'attesa di
    dieci secondi bastano per un file letto da poche persone e scritto solo dal
    programma.
    """

    def __init__(self, path: str, formato_parser: int, sola_lettura: bool = False,
                 testo: bool = False):
        self.path = path
        self.sola_lettura = sola_lettura
        self.testo = testo
        self.con = sqlite3.connect(path, timeout=10)
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.execute("PRAGMA synchronous=NORMAL")
        self.con.executescript(DDL)
        self.fts = self._prova_fts()
        self.svuotata = False
        self._fts_sporco = False
        if not sola_lettura:
            self._allinea_schema()
            self._allinea_formato(formato_parser)
            self._allinea_testo(testo)
            self._ripacchetta()
        self.con.commit()

    def _prova_fts(self) -> bool:
        """FTS5 c'e' quasi sempre, ma non e' garantito da nessuna parte.

        Senza, si archivia lo stesso e si cerca con LIKE: piu' lento, ma su
        qualche migliaio di messaggi nessuno se ne accorge. Rifiutarsi di
        partire per un indice mancante sarebbe sproporzionato.
        """
        try:
            self.con.executescript(DDL_FTS)
            return True
        except sqlite3.Error:
            return False

    # -- formato ------------------------------------------------------------ #

    def _leggi_meta(self, chiave_: str) -> str | None:
        riga = self.con.execute(
            "SELECT valore FROM meta WHERE chiave=?", (chiave_,)).fetchone()
        return riga[0] if riga else None

    def _scrivi_meta(self, chiave_: str, valore) -> None:
        self.con.execute(
            "INSERT OR REPLACE INTO meta(chiave, valore) VALUES(?,?)",
            (chiave_, str(valore)))

    def _allinea_schema(self) -> None:
        """Migrazione di schema, applicando la regola invece di enunciarla.

        Le righe derivate si buttano e si rifanno al giro dopo: non c'e' niente
        da migrare, e il codice di migrazione che non scrivi e' quello che non
        sbaglia. Quelle acquisite invece restano intoccate — nessuna rilettura
        potrebbe rifarle — e se un giorno il loro formato dovra' cambiare, la
        migrazione riguardera' solo loro, che sono poche.
        """
        precedente = self._leggi_meta("schema")
        if precedente is None or precedente == str(SCHEMA):
            self._scrivi_meta("schema", SCHEMA)
            return
        self.con.execute(
            "DELETE FROM turno WHERE session_id IN"
            " (SELECT session_id FROM sessione WHERE origine='derivato')")
        self.con.execute("DELETE FROM sessione WHERE origine='derivato'")
        self.con.execute("DELETE FROM file")
        self.svuotata = True
        self._scrivi_meta("schema", SCHEMA)

    def _allinea_testo(self, testo: bool) -> None:
        """Accendere l'archiviazione del testo obbliga a rileggere i transcript.

        I file gia' in cache sono stati letti senza tenere il testo: senza
        questo passaggio l'archivio resterebbe muto proprio sulle conversazioni
        di prima, cioe' quelle a maggior rischio di sparire.

        Spegnendola non si cancella niente: il testo gia' archiviato e' roba
        che si e' chiesto di conservare, e per toglierla c'e' `dimentica_testo`.
        """
        prima = self._leggi_meta("testo")
        if prima is not None and prima != ("1" if testo else "0") and testo:
            self.con.execute("DELETE FROM file")
            self.svuotata = True
        self._scrivi_meta("testo", 1 if testo else 0)

    def _allinea_formato(self, formato_parser: int) -> None:
        """Se cambia il parser si svuota la cache, non l'archivio.

        E' la ragione per cui le due cose stanno in tabelle diverse: prima un
        cambio di formato costava la rilettura di tutti i transcript *e* la
        perdita di tutto quello che si sapeva. Adesso costa solo la rilettura,
        e le righe acquisite — quelle che nessuna rilettura potrebbe rifare —
        non vengono toccate.
        """
        precedente = self._leggi_meta("formato_parser")
        if precedente is not None and precedente != str(formato_parser):
            self.con.execute("DELETE FROM file")
            self.svuotata = True
        self._scrivi_meta("formato_parser", formato_parser)
        self._scrivi_meta("schema", SCHEMA)

    def _ripacchetta(self) -> int:
        """Comprime, una volta sola, i record scritti in chiaro dalle versioni
        precedenti.

        Senza, si comprimerebbero soltanto i transcript che cambiano, e un
        archivio di lavoro fermo resterebbe grande come prima per mesi — cioe'
        il guadagno arriverebbe proprio a chi non ne ha bisogno. E' una
        riscrittura di cache: se si interrompe a meta' non si perde niente, e al
        giro dopo riprende da dove era.
        """
        if self._leggi_meta("rec_compressi") == "1":
            return 0
        try:
            righe = self.con.execute(
                "SELECT path, rec FROM file WHERE typeof(rec)='text'").fetchall()
            for path, rec in righe:
                d = spacchetta(rec)
                if d is None:
                    self.con.execute("DELETE FROM file WHERE path=?", (path,))
                    continue
                self.con.execute("UPDATE file SET rec=? WHERE path=?",
                                 (impacchetta(d), path))
        except sqlite3.Error:
            return 0
        self._scrivi_meta("rec_compressi", 1)
        if righe:
            # Le pagine liberate restano dentro il file: qui conviene ripulire
            # subito, perche' succede una volta sola e libera parecchio.
            self.compatta()
        return len(righe)

    # -- cache di analisi --------------------------------------------------- #

    def indice(self) -> dict[str, tuple[int, float]]:
        """path -> (dimensione, data). Senza i record: pesano e servono di rado."""
        return {p: (s, m) for p, s, m
                in self.con.execute("SELECT path, size, mtime FROM file")}

    def record(self, path: str) -> dict | None:
        riga = self.con.execute(
            "SELECT rec FROM file WHERE path=?", (chiave(path),)).fetchone()
        if not riga:
            return None
        return spacchetta(riga[0])

    # Quello che il parser produce solo per la lettura a fondo: in cache non ci
    # va, perche' e' il testo — che ha una tabella sua e regole sue — e sono i
    # risultati degli strumenti, che non si archiviano affatto.
    _FUORI_CACHE = ("messages", "tool_uses", "tool_res")

    def scrivi_file(self, path: str, size: int, mtime: float, rec: dict) -> None:
        magro = {k: v for k, v in rec.items() if k not in self._FUORI_CACHE}
        self.con.execute(
            "INSERT OR REPLACE INTO file"
            " (path, size, mtime, session_id, subagent, rec, letto)"
            " VALUES (?,?,?,?,?,?,?)",
            (chiave(path), size, mtime, rec.get("session_id"),
             1 if rec.get("is_subagent") else 0,
             impacchetta(magro), time.time()))

    def pota(self, base: str, vivi: set[str]) -> int:
        """Toglie dalla cache i transcript spariti da sotto `base`.

        Solo da sotto `base`: chi lancia il monitor su una cartella diversa non
        deve svuotare quello che sa dell'altra. La cache JSON invece potava su
        tutto, e bastava un `--base` diverso per farsi rileggere tutto al giro
        dopo.

        Le sessioni non spariscono con i loro file: restano, marcate acquisite.
        Sono la sola memoria che rimane di quel lavoro dopo che Claude Code ha
        fatto le pulizie (`cleanupPeriodDays`).
        """
        radice = chiave(base) + os.sep
        spariti = [p for (p,) in self.con.execute("SELECT path FROM file")
                   if p.startswith(radice) and p not in vivi]
        if not spariti:
            return 0
        self.con.executemany("DELETE FROM file WHERE path=?",
                             [(p,) for p in spariti])
        self._ricalcola_origine()
        return len(spariti)

    def _ricalcola_origine(self) -> None:
        """Una sessione e' derivata finche' TUTTI i suoi file esistono ancora.

        Basta che ne manchi uno — anche solo quello di un subagent — perche' la
        riga non sia piu' ricostruibile per intero: da li' in poi e' un dato
        acquisito, e `file_mancanti` dice quanto ci manca.
        """
        self.con.execute("""
            UPDATE sessione SET
              file_mancanti = MAX(0, file_totali -
                  (SELECT COUNT(*) FROM file WHERE file.session_id = sessione.session_id)),
              origine = CASE
                  WHEN fonte <> 'claude-code' THEN 'acquisito'
                  WHEN file_totali > (SELECT COUNT(*) FROM file
                                      WHERE file.session_id = sessione.session_id)
                       THEN 'acquisito'
                  ELSE 'derivato' END
        """)

    def svuota_cache(self) -> int:
        n = self.con.execute("SELECT COUNT(*) FROM file").fetchone()[0]
        self.con.execute("DELETE FROM file")
        self._ricalcola_origine()
        self.con.commit()
        return n

    # -- testo -------------------------------------------------------------- #

    def scrivi_messaggi(self, session_id: str, messaggi: list[dict],
                        confini: list[float] | None = None) -> int:
        """Sostituisce il testo archiviato di una sessione.

        `confini` sono gli istanti di inizio dei turni: servono ad attaccare
        ogni messaggio al suo, cosi' che dal turno si possa risalire a cosa e'
        stato detto anche quando il transcript non c'e' piu'.

        Si riscrive tutto invece di aggiungere: una sessione ripresa rinumera i
        turni, e mezzo testo vecchio accanto a meta' nuovo sarebbe peggio di
        niente.
        """
        if not self.testo:
            return 0
        self.con.execute("DELETE FROM messaggio WHERE session_id=?", (session_id,))
        confini = sorted(confini or [])
        righe = []
        for m in messaggi:
            testo = (m.get("text") or "").strip()
            if not testo:
                continue
            ts = m.get("ts")
            turno = None
            if ts is not None and confini:
                i = bisect_destra(confini, ts) - 1
                turno = i + 1 if i >= 0 else None
            righe.append((
                session_id, turno, ts,
                "tu" if m.get("kind") == "prompt" else "claude",
                m.get("model"), 1 if m.get("subagent") else 0,
                _js(sorted(set(m.get("tools") or []))) if m.get("tools") else None,
                testo,
            ))
        if righe:
            self.con.executemany(
                "INSERT INTO messaggio"
                " (session_id, turno, ts, ruolo, modello, subagent, strumenti, testo)"
                " VALUES (?,?,?,?,?,?,?,?)", righe)
        self._fts_sporco = True
        return len(righe)

    def messaggi_di(self, session_id: str, turno: int | None = None) -> list[dict]:
        """Il testo archiviato, nella forma che si aspetta chi mostra una chat."""
        sql = ("SELECT turno, ts, ruolo, modello, subagent, strumenti, testo"
               " FROM messaggio WHERE session_id=?")
        args: list = [session_id]
        if turno is not None:
            sql += " AND turno=?"
            args.append(turno)
        sql += " ORDER BY ts IS NULL, ts, id"
        out = []
        for t, ts, ruolo, modello, sub, strum, testo in self.con.execute(sql, args):
            out.append({
                "kind": "prompt" if ruolo == "tu" else "assistant",
                "ts": ts, "model": modello, "text": testo,
                "tools": json.loads(strum) if strum else [],
                "subagent": bool(sub), "turno": t,
                "tok": {}, "archiviato": True,
            })
        return out

    def _riallinea_fts(self) -> None:
        if not (self.fts and self._fts_sporco):
            return
        try:
            # Ricostruzione secca invece di trigger sulle singole righe: il
            # testo cambia solo a fine scansione, e su qualche migliaio di
            # messaggi rifare l'indice costa meno di mantenerlo.
            self.con.execute(
                "INSERT INTO messaggio_fts(messaggio_fts) VALUES('rebuild')")
        except sqlite3.Error:
            self.fts = False
        self._fts_sporco = False

    def cerca(self, testo: str, limite: int = 200) -> list[dict]:
        """Cerca nel testo archiviato. Con FTS5 se c'e', altrimenti con LIKE."""
        needle = (testo or "").strip()
        if not needle:
            return []
        try:
            if self.fts:
                sql = ("SELECT m.session_id, m.turno, m.ruolo, m.ts,"
                       "       snippet(messaggio_fts, 0, '«', '»', '…', 12)"
                       " FROM messaggio_fts f JOIN messaggio m ON m.id = f.rowid"
                       " WHERE messaggio_fts MATCH ?"
                       " ORDER BY rank LIMIT ?")
                righe = self.con.execute(sql, (_query_fts(needle), limite))
            else:
                sql = ("SELECT session_id, turno, ruolo, ts, substr(testo, 1, 160)"
                       " FROM messaggio WHERE testo LIKE ? ORDER BY ts DESC LIMIT ?")
                righe = self.con.execute(sql, (f"%{needle}%", limite))
            return [{"session_id": s, "turno": t, "ruolo": r, "ts": ts,
                     "frammento": " ".join((f or "").split())}
                    for s, t, r, ts, f in righe]
        except sqlite3.Error:
            return []

    def dimentica_testo(self) -> int:
        """Cancella il testo archiviato, lasciando in piedi i numeri."""
        n = self.con.execute("SELECT COUNT(*) FROM messaggio").fetchone()[0]
        self.con.execute("DELETE FROM messaggio")
        self._fts_sporco = True
        self._riallinea_fts()
        self.con.commit()
        return n

    # -- archivio ----------------------------------------------------------- #

    def scrivi_sessioni(self, sessioni: list[dict],
                        fonte: str = "claude-code") -> None:
        """Riversa in archivio le sessioni gia' calcolate.

        Non si salva il costo reale ripartito: dipende da quanto dichiari di
        pagare, che e' configurazione e cambia. In archivio vanno i fatti
        misurati; le opinioni si ricalcolano ogni volta a partire da quelli.
        """
        adesso = time.time()
        # Quanti file aveva questa sessione l'ultima volta. Se oggi ne ha meno,
        # il massimo dei due e' l'unico numero onesto: i conti calano — quello
        # che non si legge piu' non si puo' inventare — ma `file_mancanti`
        # continua a dire di quanto, invece di far sparire la domanda.
        visti = dict(self.con.execute("SELECT session_id, file_totali FROM sessione"))
        righe, turni = [], []
        for s in sessioni:
            sid = s.get("session_id")
            if not sid:
                continue
            n_file = max(len(s.get("files") or []), visti.get(sid, 0))
            righe.append((
                sid, fonte, "derivato", n_file, 0,
                s.get("project"), s.get("cwd"), s.get("title"),
                s.get("first_prompt"), s.get("git_branch"), s.get("version"),
                s.get("start"), s.get("end"), s.get("duration"), s.get("active"),
                s.get("user_prompts"), s.get("assistant_msgs"), s.get("tool_calls"),
                s.get("subagent_files"), s.get("api_errors"),
                s.get("cost"), s.get("cache_hit"), s.get("traces_n"),
                s.get("spans_n"), s.get("turn_median"),
                _js(s.get("tokens")), _js(s.get("per_model")), _js(s.get("per_month")),
                adesso,
            ))
            for t in s.get("traces") or []:
                turni.append((
                    sid, t["n"], t.get("ts"), t.get("end"), t.get("duration"),
                    t.get("prompt"), t.get("requests"), t.get("tools"),
                    t.get("spans"), t.get("subagents"),
                    1 if t.get("interrupted") else 0,
                    t.get("cost"), t.get("cache_hit"),
                    _js(t.get("tokens")), _js(t.get("per_model")),
                    _js(t.get("tool_names")),
                ))
        if not righe:
            return
        self.con.executemany(
            "INSERT OR REPLACE INTO sessione"
            " (session_id, fonte, origine, file_totali, file_mancanti,"
            "  progetto, cwd, titolo, primo_prompt, branch, versione,"
            "  inizio, fine, durata, attivo,"
            "  prompt_utente, msg_assistant, tool, subagent_file, errori_api,"
            "  costo, cache_hit, turni, span, durata_mediana,"
            "  token, per_modello, per_mese, aggiornato)"
            " VALUES (" + ",".join("?" * 29) + ")", righe)
        # I turni si riscrivono per intero: una sessione ripresa puo' averne di
        # nuovi, e una rinumerazione lascerebbe in giro i vecchi.
        self.con.executemany("DELETE FROM turno WHERE session_id=?",
                             [(r[0],) for r in righe])
        if turni:
            self.con.executemany(
                "INSERT OR REPLACE INTO turno"
                " (session_id, n, inizio, fine, durata, prompt, richieste, tool,"
                "  span, subagent, interrotto, costo, cache_hit, token, modelli,"
                "  strumenti)"
                " VALUES (" + ",".join("?" * 16) + ")", turni)
        self._ricalcola_origine()

    # -- rilettura ---------------------------------------------------------- #

    def sessioni_orfane(self, escludi: set[str] | None = None,
                        fonti: set[str] | None = None) -> list[dict]:
        """Le sessioni di cui non resta nemmeno un transcript.

        Sono quelle cancellate da `cleanupPeriodDays`: qui dentro c'e' l'unica
        traccia rimasta di quel lavoro. Tornano nella forma esatta che produce
        `finalize`, cosi' ogni vista le tratta come tutte le altre — la sola
        differenza e' che `files` e' vuoto e `archiviata` e' vera, che e' anche
        tutto quello che serve sapere per non provare a rileggerle.

        Restano fuori quelle che qualche file ce l'hanno ancora: quelle vengono
        scansionate, e sommarle due volte raddoppierebbe i conti.

        `fonti` limita a quelle sorgenti. Serve a chi ha spento una sorgente in
        configurazione: le sue righe restano in archivio — nessuno ha chiesto di
        cancellarle — ma non devono ricomparire dalla porta di servizio.
        """
        escludi = escludi or set()
        sql = ("SELECT * FROM sessione s WHERE NOT EXISTS"
               " (SELECT 1 FROM file f WHERE f.session_id = s.session_id)")
        cur = self.con.execute(sql)
        nomi = [d[0] for d in cur.description]
        out = []
        for riga in cur.fetchall():
            r = dict(zip(nomi, riga))
            if r["session_id"] in escludi:
                continue
            if fonti is not None and (r.get("fonte") or "claude-code") not in fonti:
                continue
            out.append(self._a_sessione(r))
        return out

    def _a_sessione(self, r: dict) -> dict:
        def carica(campo, default):
            try:
                v = json.loads(r.get(campo) or "null")
            except (ValueError, TypeError):
                v = None
            return default if v is None else v

        sid = r["session_id"]
        fonte = r.get("fonte") or "claude-code"
        per_model = carica("per_modello", {})
        traces = self._turni_di(sid)
        for t in traces:
            t["costo_noto"] = fonte == "claude-code"
        return {
            "session_id": sid,
            "archiviata": True,
            # Solo Claude Code dichiara i token, quindi solo li' il costo e' un
            # numero misurato invece che una casella vuota.
            "costo_noto": fonte == "claude-code",
            "fonte": fonte,
            "origine": r.get("origine") or "acquisito",
            "file_mancanti": r.get("file_mancanti") or 0,
            "project": r.get("progetto"),
            "project_dir": None,
            "cwd": r.get("cwd"),
            "title": r.get("titolo"),
            "first_prompt": r.get("primo_prompt"),
            "git_branch": r.get("branch"),
            "version": r.get("versione"),
            "entrypoint": None,
            "models": {m: d.get("tokens", {}) for m, d in per_model.items()},
            "by_month": {},
            "user_prompts": r.get("prompt_utente") or 0,
            "subagent_prompts": 0,
            "assistant_msgs": r.get("msg_assistant") or 0,
            "tool_calls": r.get("tool") or 0,
            "tool_results": 0,
            "api_errors": r.get("errori_api") or 0,
            "bad_lines": 0,
            "agents": {},
            "subagent_files": r.get("subagent_file") or 0,
            "files": [],
            "mtime": r.get("aggiornato") or 0.0,
            "start": r.get("inizio"),
            "end": r.get("fine"),
            "duration": r.get("durata") or 0.0,
            "active": r.get("attivo") or 0.0,
            "tokens": carica("token", {}),
            "cost": r.get("costo") or 0.0,
            "per_model": per_model,
            "per_month": carica("per_mese", {}),
            "unknown_models": [],
            "messages_total": (r.get("prompt_utente") or 0) + (r.get("msg_assistant") or 0),
            "traces": traces,
            "traces_n": r.get("turni") or len(traces),
            "spans_n": r.get("span") or 0,
            "cache_hit": r.get("cache_hit"),
            "turn_median": r.get("durata_mediana"),
        }

    def _turni_di(self, session_id: str) -> list[dict]:
        cur = self.con.execute(
            "SELECT n, inizio, fine, durata, prompt, richieste, tool, span,"
            "       subagent, interrotto, costo, cache_hit, token, modelli, strumenti"
            " FROM turno WHERE session_id=? ORDER BY n", (session_id,))
        out = []
        for (n, ini, fine, dur, prompt, req, tool, span, sub, interr,
             costo, ch, token, modelli, strumenti) in cur:
            def js(v, default):
                try:
                    x = json.loads(v or "null")
                except (ValueError, TypeError):
                    x = None
                return default if x is None else x
            per_model = js(modelli, {})
            out.append({
                "n": n, "ts": ini, "end": fine, "duration": dur,
                "prompt": prompt, "requests": req or 0, "tools": tool or 0,
                "spans": span or 0, "subagents": sub or 0,
                "interrupted": bool(interr),
                "cost": costo or 0.0, "cache_hit": ch,
                "tokens": js(token, {}), "per_model": per_model,
                "models": {m: d.get("tokens", {}) for m, d in per_model.items()},
                "tool_names": js(strumenti, []),
                "archiviato": True,
            })
        return out

    def conta(self) -> dict:
        """Quattro numeri per dire com'e' messo l'archivio."""
        def uno(sql, *args):
            return self.con.execute(sql, args).fetchone()[0]
        return {
            "file": uno("SELECT COUNT(*) FROM file"),
            "sessioni": uno("SELECT COUNT(*) FROM sessione"),
            "turni": uno("SELECT COUNT(*) FROM turno"),
            "acquisite": uno("SELECT COUNT(*) FROM sessione WHERE origine='acquisito'"),
            "messaggi": uno("SELECT COUNT(*) FROM messaggio"),
        }

    # -- manutenzione ------------------------------------------------------- #

    def _byte_file(self) -> int:
        """Il file e i suoi compagni. Il WAL conta: e' spazio occupato davvero."""
        totale = 0
        for suffisso in ("", "-wal", "-shm"):
            try:
                totale += os.path.getsize(self.path + suffisso)
            except OSError:
                pass
        return totale

    def _byte_tabella(self, tabella: str, colonna: str | None = None) -> int:
        """Byte dei dati di una tabella (o di una sua colonna), circa.

        Si contano i valori, non le pagine: la vista `dbstat` che li darebbe
        esatti non c'e' in tutte le build di SQLite — nemmeno in quella con cui
        gira questo — e un numero che a volte c'e' e a volte no non si mette in
        un pannello. La somma delle parti resta sotto la dimensione del file,
        che comprende anche indici e pagine libere.
        """
        try:
            if colonna:
                colonne = [colonna]
            else:
                colonne = [r[1] for r in self.con.execute(
                    f"PRAGMA table_info({tabella})")]
            if not colonne:
                return 0
            somma = " + ".join(f"COALESCE(LENGTH({c}),0)" for c in colonne)
            return self.con.execute(
                f"SELECT COALESCE(SUM({somma}),0) FROM {tabella}").fetchone()[0]
        except sqlite3.Error:
            return 0

    def peso(self) -> dict:
        """Quanto occupa l'archivio e dove sono finiti i byte.

        Serve a rispondere a una domanda sola: se il file e' diventato grande,
        cos'e' che lo tiene grande. Senza questa risposta le uniche mosse
        possibili sono cancellare tutto o non toccare niente.
        """
        parti = {
            "cache di analisi": self._byte_tabella("file"),
            "sessioni": self._byte_tabella("sessione"),
            "turni": self._byte_tabella("turno"),
            "testo": self._byte_tabella("messaggio"),
        }
        if self.fts:
            parti["indice di ricerca"] = self._byte_tabella("messaggio_fts_data")
        chiaro, timestamp = self._byte_chiaro()
        return {
            "file": self._byte_file(),
            "parti": {k: v for k, v in parti.items() if v},
            "chiaro": chiaro,
            "timestamp": timestamp,
        }

    def _byte_chiaro(self) -> tuple[int, int]:
        """La cache di analisi in chiaro, e quanto ne sono i soli timestamp.

        Serve a dire due cose che il numero su disco non dice: quanto rende la
        compressione, e quanto costa la scelta di conservare ogni istante invece
        di riassumerlo. Si legge tutto, che su qualche centinaio di righe non si
        sente; e' un comando di manutenzione, non una schermata.
        """
        chiaro = timestamp = 0
        try:
            for (rec,) in self.con.execute("SELECT rec FROM file"):
                d = spacchetta(rec)
                if not isinstance(d, dict):
                    continue
                chiaro += len(json.dumps(d, ensure_ascii=False))
                if d.get("ts"):
                    timestamp += len(_js(d["ts"]))
        except sqlite3.Error:
            return 0, 0
        return chiaro, timestamp

    def compatta(self) -> int:
        """VACUUM: restituisce al disco lo spazio delle righe cancellate.

        Senza, cancellare il testo libera pagine *dentro* il file e il file
        resta grande uguale — che a chi ha appena chiesto di dimenticare
        qualcosa sembra, ragionevolmente, che non sia successo niente.
        Ritorna i byte recuperati.
        """
        if self.sola_lettura:
            return 0
        prima = self._byte_file()
        self.commit()
        livello = self.con.isolation_level
        self.con.isolation_level = None      # VACUUM non gira in transazione
        try:
            self.con.execute("VACUUM")
            # Il VACUUM in modalita' WAL riscrive tutto passando dal giornale:
            # senza svuotarlo, il file principale cala e lo spazio occupato no,
            # e il numero che restituiamo sarebbe una bugia.
            self.con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            return 0
        finally:
            self.con.isolation_level = livello
        return max(0, prima - self._byte_file())

    # -- ciclo di vita ------------------------------------------------------ #

    def commit(self) -> None:
        if not self.sola_lettura:
            self._riallinea_fts()
            self.con.commit()

    def chiudi(self) -> None:
        try:
            self.commit()
        finally:
            self.con.close()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.chiudi()
        return False


# --------------------------------------------------------------------------- #
# Trasloco dalla cache JSON
# --------------------------------------------------------------------------- #


def importa_cache_json(arch: Archivio, path_json: str, formato: int) -> int:
    """Travasa `.cache.json` nell'archivio, una volta sola.

    Vale la pena solo per risparmiare a chi aggiorna una rilettura di tutti i
    transcript — su un archivio vero sono centinaia di MB e qualche minuto. Se
    il formato non combacia non si importa niente: meglio rileggere che portarsi
    dentro numeri calcolati da un parser diverso.
    """
    try:
        with open(path_json, encoding="utf-8") as fh:
            dati = json.load(fh)
    except (OSError, ValueError):
        return 0
    if dati.get("format") != formato:
        return 0
    n = 0
    for path, voce in (dati.get("files") or {}).items():
        rec = voce.get("rec")
        if not isinstance(rec, dict):
            continue
        arch.scrivi_file(path, int(voce.get("size") or 0),
                         float(voce.get("mtime") or 0.0), rec)
        n += 1
    arch.commit()
    return n
