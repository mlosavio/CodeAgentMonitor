# Backlog — CodeAgentMonitor

Ogni voce è una **PF** (Product Feature) con un numero che **non cambia più e non viene
riusato**: se una PF viene abbandonata resta qui marcata *scartata*, con il motivo. Un numero
riciclato rende inutili tutti i riferimenti scritti altrove.

Quello che è già implementato non sta qui: sta nel [README](README.md). Questo file è solo
quello che manca.

**Stati:** `da fare` · `in corso` · `fatto` (con la data) · `scartata` (con il perché).

| PF | Cosa | Stato | Dipende da |
|---|---|---|---|
| [PF01](#pf01--github-copilot-postazione-singola) | GitHub Copilot, postazione singola | **fatto** 13/08 | — |
| [PF02](#pf02--github-copilot-team) | GitHub Copilot, team | da fare | PF01 |
| [PF03](#pf03--tee-proxy-per-gli-agenti-che-non-scrivono-transcript) | Tee-proxy per gli agenti che non scrivono transcript | da valutare | PF01 |
| [PF04](#pf04--modulo-statistico-trend-e-kpi) | Modulo statistico: trend e KPI | **fatto** 13/08 | — |
| [PF05](#pf05--frammenti-di-ricerca-invece-di-un-elenco-filtrato) | Frammenti di ricerca invece di un elenco filtrato | **fatto** 13/08 | — |
| [PF06](#pf06--export-dei-turni-come-dataset-di-valutazione) | Export dei turni come dataset di valutazione | **fatto** 13/08 | — |
| [PF07](#pf07--i-prompt-sintetici-contati-come-tuoi) | I prompt sintetici contati come tuoi | da decidere | — |
| [PF08](#pf08--modelli-con-finestra-da-1m-al-prezzo-sbagliato) | Modelli con finestra da 1M al prezzo sbagliato | **fatto** 13/08 | — |
| [PF09](#pf09--i-timestamp-sono-il-60-della-cache-di-analisi) | I timestamp sono il 60% della cache di analisi | **fatto** 13/08 | — |
| [PF10](#pf10--manutenzione-dellarchivio) | Manutenzione dell'archivio | **fatto** 13/08 | — |
| [PF11](#pf11--prove-su-macos-e-linux) | Prove su macOS e Linux | **Linux fatto** 13/08 · macOS sospesa | — |
| [PF12](#pf12--le-postazioni-che-consumano-e-non-sono-in-fattura) | Le postazioni che consumano e non sono in fattura | **fatto** 13/08 | — |
| [PF13](#pf13--andamento-delladozione-nel-team) | Andamento dell'adozione nel team | **fatto** 13/08 | PF04 |
| [PF14](#pf14--il-raccoglitore-fuori-dalla-rete-aziendale) | Il raccoglitore fuori dalla rete aziendale | da fare | — |
| [PF15](#pf15--linterfaccia-in-inglese) | L'interfaccia in inglese | in corso — pagina fatta, app no | — |
| [PF16](#pf16--cruscotto-per-sistemi-multi-agente) | Cruscotto per sistemi multi-agente | da valutare | — |

---

## Sorgenti

### PF01 — GitHub Copilot, postazione singola

**Stato:** fatto il 2026-08-13 · **Dipende da:** —

`cam_copilot.py`, acceso di default (`copilot.enabled`), documentato nel
[README](README.md#github-copilot) e provato da `test_copilot.py`. Sulla macchina di
sviluppo legge 10 sessioni, 97 turni e 339 chiamate a strumenti.

La domanda di prodotto ha avuto questa risposta: **«—», mai zero e mai una stima**, con
una riga sotto le tabelle che spiega perche' — un trattino in una colonna di costi si
legge come uno zero se nessuno dice il contrario. Le colonne «Fonte» in Sessioni e Traces
dicono da dove viene ogni riga; in JSON `cost_usd` e `tokens` sono `null`, non 0.

Quello che segue e' la ricognizione su cui e' stato costruito, lasciata perche' serve a
PF02 e PF03.

Leggere l'uso di Copilot sulla macchina, come già si fa con Claude Code.

Il vincolo che decide tutto: **Copilot non scrive un transcript stabile e documentato**. Le
chat di VS Code stanno in `workspaceStorage`, in un formato non pubblico che può cambiare a
ogni versione. Non è una fonte su cui si possa dire "se il parser sbaglia, si rilegge": va
trattata come `origine = 'acquisito'`, cioè letta una volta e conservata.

**Ricognizione fatta il 2026-08-13** su una macchina reale (VS Code, `github.copilot-chat`
0.39.2): 22 file di sessione, 93 turni. Non sono più supposizioni.

```
%APPDATA%\Code\User\workspaceStorage\<hash>\chatSessions\*.json    legate a un progetto
%APPDATA%\Code\User\globalStorage\emptyWindowChatSessions\         senza progetto
```

Un file è una sessione: `sessionId`, `creationDate`, `lastMessageDate`, `customTitle`, e
`requests[]` — un elemento per turno.

| | c'è? | dove |
|---|---|---|
| turni | **sì** | un elemento di `requests[]` per turno |
| orari | **sì** | `timestamp` (epoch ms) |
| **latenza reale** | **sì, misurata** | `timeSpentWaiting`, `result.timings.totalElapsed` e `firstProgress` |
| modello | **sì** | `modelId`, es. `copilot/claude-sonnet-4.5`, `copilot/claude-haiku-4.5`, `copilot/auto` |
| domanda e risposta | **sì** | `message`, `response` |
| **token** | **no** | le uniche chiavi che li nominano sono `maxInputTokens`/`maxOutputTokens`, che sono i **limiti del modello**, non il consumo |
| **costo** | **no** | non deducibile: senza token non c'è niente da moltiplicare |

Due conseguenze che vanno dette subito:

- **la latenza è più precisa di quella di Claude Code.** Per Claude Code la durata di una
  richiesta la [deduciamo](README.md#come-si-ricava-la-durata-di-una-richiesta) dall'evento
  precedente; qui è misurata dal client. Il modello degli span regge entrambe, perché conserva
  durate e non modi di calcolarle.
- **`copilot/auto` non dice quale modello ha risposto.** Va mostrato così com'è, non risolto a
  indovinare.

Il costo va lasciato **vuoto**, non stimato. Una colonna «Se fosse API» inventata su un
prodotto a quota fissa sarebbe un numero senza significato, e verrebbe letta come una spesa.
Serve decidere come si comporta l'interfaccia quando la colonna di punta non si può riempire:
è la vera domanda di prodotto di questa PF, non il parsing.

### PF02 — GitHub Copilot, team

**Stato:** da fare · **Dipende da:** PF01 (fatta)

Dalle API di organizzazione di GitHub: postazioni assegnate, ultima attività, metriche di
utilizzo, e il consumo di *premium request* dalla rendicontazione.

Anche qui **niente token e niente costo per sessione**: la fatturazione di Copilot è per
postazione, più le premium request oltre la quota. È l'informazione che serve per la domanda
che conta davvero — *quante postazioni pagate non vengono usate* — che è la stessa a cui
risponde già la scheda Persone per Claude Code.

Prima di scrivere codice: **verificare gli endpoint sulla documentazione corrente**. Sono API
che si muovono, e quello che si ricorda di sei mesi fa non è una fonte.

Vale la stessa regola della scheda Persone: le postazioni ferme non si vedono, si deducono dal
numero di postazioni pagate, che va dichiarato.

### PF03 — Tee-proxy per gli agenti che non scrivono transcript

**Stato:** da valutare · **Dipende da:** PF01 (fatta)

Un proxy locale che vede il traffico verso le API dei modelli e ne ricava token e costo. È
l'unico modo per avere i **token** di Copilot e di qualunque altro agente. È così che funziona
ProxyAgent per tutto ciò che non è Claude Code.

La ricognizione di PF01 lo ha confermato: nei file locali di Copilot i token **non ci sono**, e
non c'è nessun'altra strada per ricavarli. Quindi la scelta è netta e va posta così: *il proxy
serve a ottenere token e costo per Copilot, e costa una CA nel keystore di ogni postazione*.

**Il vincolo, in chiaro:** vuol dire terminare TLS con una CA installata nel keystore della
macchina. Non è una voce di configurazione, è un cambiamento alla postura di sicurezza di ogni
postazione: da quel momento esiste un certificato che, se rubato, permette di leggere traffico
cifrato. Su un repo che oggi si vanta di leggere solo file già presenti, è la modifica più
invasiva che si possa proporre.

Va deciso **dopo** aver visto quanto si ottiene senza (PF01 e PF02), non prima. Se la risposta è
"quasi tutto", il proxy non vale il suo prezzo.

Se si fa: il modello dati è già pronto — `fonte` e `origine` esistono, e le righe del proxy
sarebbero `acquisito` per definizione.

---

## Analisi

### PF04 — Modulo statistico: trend e KPI

**Stato:** fatto il 2026-08-13 · **Dipende da:** —

`cam_statistiche.py`, la scheda **Andamento** e `--trend`. Documentato nel
[README](README.md#andamenti-e-indicatori), provato da `test_statistiche.py`.

Le due domande aperte hanno avuto questa risposta:

- **serie al volo**, non materializzate: su 725 turni il ricalcolo non si sente, e una tabella
  materializzata sarebbe una terza copia da tenere allineata. Da rivedere solo se i volumi
  crescono di un ordine di grandezza.
- **dieci indicatori**, non venti: uso (turni, giorni di lavoro, turni per giorno), adozione
  (progetti, progetti nuovi), efficienza (costo per turno, durata mediana, cache hit, strumenti
  per turno) e qualità (turni interrotti). Ognuno dichiara da che parte sta il bene, e si
  colora solo se quel verso è certo.

Rimasto fuori di proposito: la **serie storica delle postazioni attive** per il team.
`cam_statistiche.adozione_team()` la calcolava già dall'archivio del raccoglitore ed era provata,
ma non era esposta da nessuna vista — poi PF13.

### PF05 — Frammenti di ricerca invece di un elenco filtrato

**Stato:** fatto il 2026-08-13 · **Dipende da:** —

`snippet()` di FTS5 arriva fino alla riga: `cerca_nel_testo()` restituisce una mappa
(sessione, turno) → frammento invece di un insieme di coppie, e il frammento viaggia dentro il
turno fino alla tabella. Nel CLI c'è una sezione *Dove compare «x»* sotto la tabella dei turni;
nella GUI è una seconda riga sotto il turno, in grigio, con l'etichetta di chi l'ha detto — «tu»
o «risposta», mai «claude», perché il testo archiviato arriva anche da sorgenti che Claude non
l'hanno mai visto.

Una cosa emersa scrivendolo: **anche le domande sono testo archiviato**, quindi il frammento
compare anche quando la parola sta nel prompt. Non è un doppione della colonna: la colonna mostra
il prompt tagliato, il frammento mostra il punto che il taglio nasconderebbe.

La tabella della GUI ha imparato ad avere righe più alte (`DataTable(sub=...)`): tutte della
stessa altezza, variabile fra tabelle ma uniforme dentro una, perché metà del disegno e tutta la
selezione contano in righe.

### PF06 — Export dei turni come dataset di valutazione

**Stato:** fatto il 2026-08-13 · **Dipende da:** PF05 (per il testo)

`--export-turni FILE.jsonl`, e *Esporta → Turni selezionati in JSONL* nella GUI. Un turno per
riga: domanda, risposta, modelli, durata, strumenti, costo, cache hit, se è stato interrotto.

La domanda aperta era **a cosa serve**, e la risposta che l'ha sbloccata riguarda la selezione:
non serve un secondo insieme di criteri, **il criterio è il filtro che si ha davanti**. Cerchi
«riconciliazione», restringi a una sessione, esporti quello. Un export con regole proprie sarebbe
una seconda cosa da imparare per fare quello che si è appena fatto.

Le risposte vengono dall'archivio del testo; senza escono `null` e il comando dice **quanti**
turni sono usciti vuoti. Un dataset con metà delle risposte assenti è peggio di nessun dataset, e
non deve sembrare completo.

---

## Precisione dei conti

### PF07 — I prompt sintetici contati come tuoi

**Stato:** da decidere · **Dipende da:** —

Nella scheda Traces compaiono turni a costo zero con prompt tipo
`This session is being continued from a previous conversation…`: sono iniezioni di sistema dopo
una compattazione, non cose scritte da te. Oggi contano come tuoi prompt.

È comportamento preesistente, non introdotto con i turni — ma i turni lo rendono visibile.
Filtrarli renderebbe gli elenchi più puliti e **cambierebbe i conteggi storici dei messaggi** in
tutte le schede: chi confronta un riepilogo di ieri con uno di domani vedrebbe numeri diversi
senza aver cambiato niente.

È una decisione, non un bug da correggere di nascosto. Se si fa: aggiungere il prefisso a
`SYNTHETIC_PROMPT_PREFIXES`, e dirlo nel README fra le cose che sono cambiate.

La stessa obiezione — *non cambiare i numeri storici sotto i piedi di chi aggiorna* — ha deciso
anche PF08, che infatti dichiara il maggiorato a parte invece di sommarlo. Se qui si sceglie di
filtrare, vale la pena scegliere lo stesso modo: dire quanti sono, non farli sparire.

### PF08 — Modelli con finestra da 1M al prezzo sbagliato

**Stato:** fatto il 2026-08-13 · **Dipende da:** —

Il segnale cercato **c'è**, e non è nei metadati: è nei token. Ispezionando un transcript girato
davvero su `claude-opus-5[1m]` il suffisso della finestra non compare da nessuna parte (l'unico
campo nuovo è `effort`), ma 421 richieste su 658 hanno fatto entrare più di 200.000 token, con un
massimo di 604.000. Una richiesta così **non può essere passata da una finestra standard**, e su
quella non c'è niente da dedurre.

Quindi: si contano le richieste sopra `finestra_standard` e si dichiarano, con il maggiorato
calcolato dal rapporto in `long_context` (`in`, `out`).

**Il maggiorato non entra nei totali**, per due ragioni. È un listino dichiarato dall'utente, e un
numero dichiarato che finisce in una colonna di costi diventa vero appena qualcuno lo legge. E
rifonderlo cambierebbe i numeri storici di chiunque aggiorni, senza che nessuno abbia cambiato
niente — la stessa obiezione che tiene ferma PF07.

Resta fuori: le richieste **sotto** la soglia girate su un modello a finestra estesa sono
indistinguibili, e quelle non si recuperano in nessun modo.

### PF09 — I timestamp sono il 60% della cache di analisi

**Stato:** fatto il 2026-08-13 · **Dipende da:** —

Risolta, ma **non come proposto qui sopra**. Le due strade previste erano tenere tre numeri invece
della lista, oppure bucketizzare gli intervalli: entrambe pagavano con la possibilità di cambiare
`--idle-gap` senza rileggere i transcript, che è l'unica ragione per cui la lista esiste.

Misurando le due opzioni prima di scegliere:

| | KB | quota |
|---|---|---|
| `rec` in chiaro, com'era | 1.176 | 100% |
| solo i `ts`, a delta in millisecondi | −447 | −38% |
| `rec` compresso con zlib | **344** | **29%** |

Comprimere il record intero rende **più** che dimezzare i timestamp, non tocca la semantica di
niente e non toglie niente a nessuno. Sull'archivio vero: cache di analisi da 1,2 MB a 371 KB,
file da 2 MB a 1,1 MB dopo il VACUUM.

C'era una trappola: comprimere solo in scrittura avrebbe convertito **soltanto i transcript che
cambiano**, lasciando grande per mesi l'archivio di chi lavora poco — cioè proprio chi non ne ha
bisogno. Da lì `_ripacchetta()`, che converte una volta sola all'apertura, lo marca in `meta` e
compatta subito dopo. Le righe vecchie in chiaro restano leggibili (`spacchetta`).

Nota per il futuro: **una migrazione di formato che non converte i dati già scritti non è una
migrazione, è un secondo formato.**

---

## Archivio e manutenzione

### PF10 — Manutenzione dell'archivio

**Stato:** fatto il 2026-08-13 · **Dipende da:** —

`python cam.py --archivio`: quanto pesa il file e da cosa, voce per voce, più i
conteggi e cosa si può fare per farlo calare. La stessa frase compare in *Configura → Archivio*.

I byte per tabella si contano **sui dati e non sulle pagine**: la vista `dbstat` che li darebbe
esatti non c'è in tutte le build di SQLite — nemmeno in quella con cui gira questo — e un numero
che a volte c'è e a volte no non si mette in un pannello. La somma delle voci sta sotto il totale,
e lo si dichiara.

`--dimentica-testo` adesso fa `VACUUM` e dice quanti byte ha restituito. Senza, le pagine liberate
restano dentro il file e a chi ha appena chiesto di dimenticare qualcosa sembra — a ragione — che
non sia successo niente. Il `VACUUM` in modalità WAL passa dal giornale: senza svuotarlo il file
principale cala e lo spazio occupato no, e il numero restituito sarebbe una bugia.

**Non fatta di proposito** la politica di conservazione per età. Su un archivio che è l'unica copia
rimasta di conversazioni cancellate da `cleanupPeriodDays`, una regola automatica che sbaglia
distrugge quello che doveva proteggere. Si riapre quando esiste un archivio davvero grande a cui
guardare — oggi, dopo PF09, sono 1,1 MB.

### PF11 — Prove su macOS e Linux

**Stato:** in corso il 2026-08-13 — **Linux fatto, macOS no** · **Dipende da:** —

Provato su **Ubuntu 22.04 con Python 3.10.12**, in una cartella nativa e non su `/mnt/c`:
altrimenti si prova un filesystem Windows travestito da Linux. Le **491 prove** sono verdi lì
come qui, e il CLI è stato usato per davvero — riepilogo, `--traces`, `--trend`, `--archivio`,
`--export-md` — con accenti e cornici al loro posto. Ricadute: il README non dice più
«provato su Windows», dice cosa è provato e cosa no.

Le tre cose da verificare erano indovinate. Ecco come sono andate.

**`cam_archivio.chiave()` — il codice era giusto, la prova era sbagliata.** Su POSIX `normcase`
non tocca le maiuscole, ed è **corretto**: lì `Sessione.jsonl` e `SESSIONE.jsonl` sono due file
diversi che possono esistere entrambi, e appiattirli renderebbe uno invisibile all'altro. A
cadere era la prova, che pretendeva l'insensibilità alle maiuscole ovunque. Ora si divide col
sistema e dichiara il comportamento giusto da entrambe le parti — che è più di quanto facesse
prima, quando su Windows era verde e su Linux non esisteva.

**Il percorso dei progetti Copilot — bug vero, trovato solo perché si è provato.**
`_progetto_di()` faceva `lstrip("/")` sul percorso estratto dall'URI: giusto per
`file:///c%3A/…`, dove la barra precede la lettera di unità, **sbagliato ovunque altro**.
Su macOS e Linux `file:///home/tizio/Progetti/X` diventava `home/tizio/Progetti/X` — un percorso
**relativo**, cioè riferito alla cartella da cui è partito il programma, che non c'entra niente.
Il nome del progetto restava giusto (è l'ultima cartella), quindi in interfaccia non si vedeva:
sbagliava solo il percorso, che è quello che finisce in archivio. La barra ora si toglie solo
davanti a una lettera di unità, e `prova_percorsi_dei_tre_sistemi` tiene ferme tutte e tre le
forme **su qualunque sistema** — perché una prova che dipende da dove gira sarebbe stata verde
su Windows proprio dove il codice sbagliava.

**La GUI — non è la barra del titolo, è `os.startfile`.** La barra scura era già dietro
`os.name != "nt"` e il registro dentro un `try`. Il vero problema era in fondo all'export:
`os.startfile` **non esiste** fuori da Windows, e stava dentro un `except: pass`. Quindi su
Linux la domanda «Apro la cartella?» riceveva un sì e poi non succedeva niente, senza che
nessuno lo dicesse — una domanda a cui mentire è peggio di una domanda non fatta. Adesso c'è
`apri_cartella()`, che usa `open` su macOS e `xdg-open` su Linux e **restituisce se ce l'ha
fatta**; se no, lo scrive nella barra di stato.

**Quello che resta, e la decisione presa il 13/08.**

- **La GUI su Linux non è stata eseguita**, solo compilata. Su Debian e Ubuntu `tkinter` non
  arriva con Python — è `python3-tk`, un pacchetto a parte, e su questa macchina l'installazione
  voleva una password. È finito nel README fra i requisiti, perché «niente altro» lì era falso.
- **macOS: sospesa, non scartata.** Nessuna delle tre correzioni è stata vista girare su un Mac:
  sono scritte perché siano giuste lì, che non è la stessa cosa.

**Perché Linux sì e macOS no.** Non sono lo stesso lavoro, perché i tre pezzi di CAM girano su
macchine diverse e ognuno ha il suo sistema:

| pezzo | dove gira | quale sistema |
|---|---|---|
| `cam.py`, `cam_gui.py` — la console | macchina dell'amministratore | Windows, ed è l'unico che conta |
| `cam_agent.py` — su ogni postazione | ogni sviluppatore | quello che hanno loro |
| `cam_collector.py` — il raccoglitore | il server | **Linux, se finisce su un VPS** — vedi PF14 |

Linux era da fare perché è il sistema del raccoglitore appena esce dalla macchina di casa.
macOS serve solo il giorno in cui una postazione è un Mac: **si riapre allora**, e le prove da
rifare sono già scritte — `prova_percorsi_dei_tre_sistemi` non dipende dal sistema su cui gira.

---

## Team

### PF12 — Le postazioni che consumano e non sono in fattura

**Stato:** fatto il 2026-08-13 · **Dipende da:** —

`cam_collector.segnala_anomalie()` marca ogni riga con `anomalia`: `non_in_fattura` (consuma e non
compare nell'export caricato) o `fatturata_ferma` (fatturata, nessun consumo misurato). Il
riepilogo porta i **nomi**, non i conteggi.

Nel pannello: **⚠** davanti al nome, e un chip *⚠ n da controllare* che isola quelle righe con un
click. Col filtro acceso il totale della colonna *Hai pagato* sparisce — copre tutte le postazioni,
e sotto un elenco di tre righe su otto direbbe una cosa falsa.

Nella relazione Markdown c'è la sezione **Da controllare**, messa **prima** dei progetti: è l'unica
parte del documento su cui qualcuno deve fare qualcosa, e in fondo non la leggerebbe nessuno.

Un caso che andava chiuso: si segnala solo se la fatturazione è stata caricata davvero. Altrimenti
ogni riga avrebbe `billed` vuoto e la tabella si accenderebbe di allarmi che dicono soltanto
«manca un file».

### PF13 — Andamento dell'adozione nel team

**Stato:** fatto il 2026-08-13 · **Dipende da:** PF04

`adozione_team()` esisteva già ed era provata; mancavano la vista e la decisione su dove metterla.
**Scheda Persone, sopra la tabella** — non nella scheda Andamento: là dentro i KPI parlano dei
*tuoi* turni, e una serie sulle postazioni di altri accanto a quelli sarebbe due domande diverse
nello stesso posto. La tabella dice chi, il grafico dice se cresce, e sono due cose che si guardano
insieme.

La riga tratteggiata delle **postazioni dichiarate** (`team.seats`) non è un ornamento: le
postazioni ferme restano invisibili, quindi la curva delle attive va letta contro quel numero e mai
da sola. Per questo entra anche nella scala — se restasse fuori, il grafico mostrerebbe la serie
senza la sola cosa contro cui va letta.

Il grafico compare **da due periodi in su**: con un mese solo un andamento non esiste.

Ricadute su `TrendChart`, che ora accetta una riga di riferimento e un dettaglio del tooltip
pluggabile: prima la seconda riga del tooltip era cablata sulle metriche personali (`turni`,
`progetti`), e con una serie diversa sarebbe esplosa.

### PF14 — Il raccoglitore fuori dalla rete aziendale

**Stato:** da fare · **Dipende da:** —

Il raccoglitore oggi è pensato per una LAN: una macchina sempre accesa, raggiungibile dalle
postazioni, dietro il firewall aziendale. La domanda che lo mette in crisi è legittima e
ricorrente — **i portatili che lavorano fuori sede** — e la risposta ovvia, un VPS con la porta
aperta su Internet, oggi **non si può dare**. Tre ragioni, in ordine di gravità.

**Non c'è TLS. Per niente.** `cam_collector.py` è un `ThreadingHTTPServer` in chiaro: in tutto il
modulo non compare `ssl`. In LAN è una scelta difendibile; su un indirizzo pubblico vuol dire che
passano in chiaro i percorsi dei progetti, i costi, gli indirizzi — e **il token stesso, a ogni
richiesta**.

**Il token è uno solo, e vale in lettura come in scrittura.** `_autorizzato()` non distingue le
rotte, ed è documentato che sia così: un cruscotto aperto mostrerebbe a chiunque quanto consuma
ogni persona. Ma la conseguenza è che **il token dato a ogni postazione per scrivere apre anche
il cruscotto di tutti**. Il messaggio di `--setup` diceva l'opposto — «autorizza a scrivere in
archivio, non a leggerlo» — ed è stato corretto il 13/08: era la frase che porta un
amministratore a distribuirlo credendolo innocuo.

**Su un VPS il livello «pseudonimo» si annulla da solo.** La regola dichiarata è che la chiave
stia in una cartella diversa dall'archivio, con permessi diversi, «altrimenti lo annulla senza
che nulla smetta di funzionare». Su un server affittato finiscono sullo stesso disco, sotto lo
stesso root, dentro gli stessi snapshot del fornitore.

E una conseguenza che non è di sicurezza ma si sente subito: **la GUI legge `cam-team.db` come
file** (`team_db_candidates()`, nessun client HTTP nel modulo). Col raccoglitore su un VPS la
scheda Persone non ha più niente da leggere, e all'amministratore resta il cruscotto web — che è
un'altra interfaccia, non la sua.

**La strada che non richiede di scrivere niente**, ed è quella da provare per prima: una rete
privata (WireGuard, Tailscale) fra postazioni e VPS, col raccoglitore in ascolto **solo**
sull'indirizzo dell'overlay e mai su `0.0.0.0`. Il VPS resta fuori sede per raggiungibilità —
che è quello che serve ai portatili — ma il raccoglitore non è mai su Internet, HTTP in chiaro
torna difendibile, e la GUI ritrova il suo file perché sull'overlay il VPS è una macchina di rete
come le altre. Se funziona, questa PF si chiude **senza una riga di codice**, e quello che
produce è documentazione.

Da scrivere solo se quella strada non basta, in quest'ordine: TLS nel raccoglitore (o l'indicazione
esplicita di un reverse proxy davanti, che è meno codice e più standard), e token distinti per
lettura e scrittura. Il secondo è utile **anche in LAN**, indipendentemente dal VPS.

### PF15 — L'interfaccia in inglese

**Stato:** in corso il 2026-08-13 — **pagina fatta, applicazione no** · **Dipende da:** —

Il progetto è nato in italiano e il post di lancio prometteva l'inglese come «primo passo per
renderlo utile fuori di qui». Sono due lavori di taglia molto diversa, ed è per questo che qui
uno è chiuso e l'altro no.

**Fatto: `README.en.md`**, con il collegamento fra le due pagine in testa a entrambe. Non è una
traduzione meccanica dell'italiano: copre tutte le sezioni e tutte le affermazioni che contano,
ma dove il README italiano si allarga in argomentazioni lunghe la pagina inglese tiene il punto
e stringe, e rimanda all'italiano dichiarandolo. Due documenti da 72 KB da tenere allineati a
mano diventerebbero due documenti che divergono.

**Da fare: l'applicazione.** Misurata, non stimata:

| | quante |
|---|---|
| stringhe visibili all'utente, escluse le docstring | **718** |
| di cui frasi lunghe (>60 caratteri) | **176** |
| `cam_gui.py` / `cam.py` / `cam_collector.py` | 261 / 175 / 150 |

Le 176 frasi lunghe sono il lavoro vero, e sono anche il motivo per cui non va fatto in fretta:
sono le spiegazioni sotto le tabelle — *«SE FOSSE API = quanto varrebbe a listino API. NON l'hai
pagato»* — cioè esattamente la cosa che distingue questo strumento da un contatore. Una
traduzione approssimativa lì dentro toglie al progetto la sua unica qualità distintiva; meglio
nessuna traduzione che una che dice quasi la stessa cosa.

**Il come, deciso ma non ancora scritto.** Catalogo con **la stringa italiana come chiave**
(`_("Sessioni")`), non chiavi simboliche: l'italiano resta il sorgente, il codice resta
leggibile a chi lo mantiene, e il diff è piccolo. Il difetto noto di quell'approccio — cambiare
una parola in italiano fa scadere in silenzio la traduzione — si compensa con **una prova che
elenca le stringhe visibili senza voce nel catalogo e fallisce**: così una traduzione persa
diventa una prova rossa invece di un pezzo di interfaccia che torna italiano da solo. È lo
stesso metodo con cui `test_collector.py` sorveglia la riservatezza guardando i byte del file.

Lingua da `config.json` (`lang`), variabile d'ambiente, oppure dal sistema; `--lang` la
sovrascrive. Il ripiego è sempre l'italiano, mai la chiave nuda.

### PF16 — Cruscotto per sistemi multi-agente

**Stato:** da valutare · **Dipende da:** —

Oggi i subagent vengono **contati**: il loro consumo è sommato al turno del padre e `REQ` li
include. È giusto per la domanda «quanto è costato questo turno», ed è tutto quello che serve
finché gli agenti sono pochi.

Un cruscotto per sistemi multi-agente è un'altra cosa, e le domande sono: *chi ha delegato a
chi*, *quanto costa un ramo dell'albero*, *dove si spreca*. Nei transcript l'albero c'è —
i file dei subagent stanno sotto la sessione padre e i prompt di sidechain dicono chi istruisce
chi — quindi non manca il dato, manca la vista.

Sta qui perché era una delle quattro direzioni dichiarate nel post di lancio, e un backlog
pubblico che non le nomina chiede contributi su cose che non risultano da nessuna parte.
