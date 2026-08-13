# Backlog — claude-monitor

Ogni voce è una **PF** (Product Feature) con un numero che **non cambia più e non viene
riusato**: se una PF viene abbandonata resta qui marcata *scartata*, con il motivo. Un numero
riciclato rende inutili tutti i riferimenti scritti altrove.

Quello che è già implementato non sta qui: sta nel [README](README.md). Questo file è solo
quello che manca.

**Stati:** `da fare` · `in corso` · `fatto` (con la data) · `scartata` (con il perché).

| PF | Cosa | Stato | Dipende da |
|---|---|---|---|
| [PF01](#pf01--github-copilot-postazione-singola) | GitHub Copilot, postazione singola | da fare | — |
| [PF02](#pf02--github-copilot-team) | GitHub Copilot, team | da fare | PF01 |
| [PF03](#pf03--tee-proxy-per-gli-agenti-che-non-scrivono-transcript) | Tee-proxy per gli agenti che non scrivono transcript | da valutare | PF01 |
| [PF04](#pf04--modulo-statistico-trend-e-kpi) | Modulo statistico: trend e KPI | **fatto** 13/08 | — |
| [PF05](#pf05--frammenti-di-ricerca-invece-di-un-elenco-filtrato) | Frammenti di ricerca invece di un elenco filtrato | da fare | — |
| [PF06](#pf06--export-dei-turni-come-dataset-di-valutazione) | Export dei turni come dataset di valutazione | da fare | — |
| [PF07](#pf07--i-prompt-sintetici-contati-come-tuoi) | I prompt sintetici contati come tuoi | da decidere | — |
| [PF08](#pf08--modelli-con-finestra-da-1m-al-prezzo-sbagliato) | Modelli con finestra da 1M al prezzo sbagliato | da fare | — |
| [PF09](#pf09--i-timestamp-sono-il-60-della-cache-di-analisi) | I timestamp sono il 60% della cache di analisi | da fare | — |
| [PF10](#pf10--manutenzione-dellarchivio) | Manutenzione dell'archivio | da fare | — |
| [PF11](#pf11--prove-su-macos-e-linux) | Prove su macOS e Linux | da fare | — |
| [PF12](#pf12--le-postazioni-che-consumano-e-non-sono-in-fattura) | Le postazioni che consumano e non sono in fattura | da fare | — |
| [PF13](#pf13--andamento-delladozione-nel-team) | Andamento dell'adozione nel team | da fare | PF04 |

---

## Sorgenti

### PF01 — GitHub Copilot, postazione singola

**Stato:** da fare · **Dipende da:** —

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

**Stato:** da fare · **Dipende da:** PF01

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

**Stato:** da valutare · **Dipende da:** PF01

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

`cm_statistiche.py`, la scheda **Andamento** e `--trend`. Documentato nel
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
`cm_statistiche.adozione_team()` la calcola già dall'archivio del raccoglitore ed è provata, ma
non è ancora esposta da nessuna vista — vedi PF13.

### PF05 — Frammenti di ricerca invece di un elenco filtrato

**Stato:** da fare · **Dipende da:** —

Oggi, con il testo archiviato, cercando una parola l'elenco dei turni si restringe a quelli che
la contengono — ma **non si vede dove**. L'archivio i frammenti li produce già
(`snippet()` di FTS5, con la parola evidenziata): `cerca()` li restituisce e chi chiama li
butta via, tenendo solo la coppia (sessione, turno).

Serve mostrarli: una riga sotto il turno, o un pannello dei risultati. È la differenza fra una
ricerca che filtra e una che risponde.

### PF06 — Export dei turni come dataset di valutazione

**Stato:** da fare · **Dipende da:** —

Un turno è già una coppia domanda/risposta con dentro il contesto, il costo e l'esito. Esportarne
una selezione in JSONL darebbe un dataset per valutare prompt e modelli — è quello che fa il
menu *Dataset* di ProxyAgent, che ho lasciato fuori per non indovinarne il senso.

Prima di implementarlo va deciso **a cosa serve**: un export senza un consumatore preciso è un
file che nessuno apre. Se il consumatore è una valutazione automatica, servono anche i criteri di
selezione (i turni interrotti? quelli più cari? quelli con più strumenti?).

Dipende da `archivio.testo`: senza il testo non c'è dataset.

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

### PF08 — Modelli con finestra da 1M al prezzo sbagliato

**Stato:** da fare · **Dipende da:** —

I modelli con contesto esteso hanno un listino premium, ma il transcript registra l'id **senza**
il suffisso `[1m]`: a posteriori non si distinguono e vengono contati al prezzo standard. Il
costo di chi lavora con la finestra grande è quindi sottostimato.

Da capire se esiste un altro segnale nel transcript da cui dedurlo — per esempio una dimensione
del contesto che il listino standard non potrebbe reggere. Se non c'è, resta un limite noto e va
lasciato scritto invece che aggirato con una stima.

### PF09 — I timestamp sono il 60% della cache di analisi

**Stato:** da fare · **Dipende da:** —

Nella tabella `file` il campo `ts` — la lista di **tutti** i timestamp di un transcript — pesa
693 KB su 1.159 KB misurati. Serve solo a ricalcolare durata e tempo attivo a livello di
sessione, cioè: minimo, massimo, e la somma degli intervalli sotto la soglia di inattività.

Tutte e tre si possono calcolare durante l'analisi e conservare come tre numeri, invece della
lista intera. L'unica cosa che si perde è la possibilità di **cambiare `--idle-gap` senza
rileggere i transcript**, che è il motivo per cui oggi la lista c'è.

Compromesso possibile: tenere gli intervalli fra eventi consecutivi già aggregati per fascia,
così un `--idle-gap` diverso si ricalcola senza i timestamp assoluti. Da valutare se ne vale la
complessità: 1 MB di cache non è un problema oggi, lo diventa con dieci volte i dati.

---

## Archivio e manutenzione

### PF10 — Manutenzione dell'archivio

**Stato:** da fare · **Dipende da:** —

`cm-local.db` cresce e non cala mai da solo. Servono, in ordine di utilità:

- vedere **quanto pesa** e da cosa (oggi il pannello mostra i conteggi, non i byte);
- un `VACUUM` dopo `--dimentica-testo`, che altrimenti libera pagine senza restituire spazio;
- decidere se serve una **politica di conservazione** — per esempio buttare il testo più vecchio
  di N mesi tenendo i numeri. Da fare solo se il file diventa davvero grande: una politica di
  cancellazione automatica è una cosa che, se sbagliata, distrugge l'unica copia rimasta.

### PF11 — Prove su macOS e Linux

**Stato:** da fare · **Dipende da:** —

Sviluppato e provato solo su Windows. Il codice usa percorsi portabili (`expanduser`,
`USERPROFILE`/`LOCALAPPDATA`) e non ha dipendenze, ma «dovrebbe funzionare» non è «funziona».

Da verificare in particolare: la forma canonica dei percorsi in `cm_archivio.chiave()`
(`normcase` si comporta diversamente), la barra del titolo scura della GUI (è codice Windows), e
il fatto che le prove girino.

---

## Team

### PF12 — Le postazioni che consumano e non sono in fattura

**Stato:** da fare · **Dipende da:** —

La riconciliazione con l'export di fatturazione c'è già, e i due casi anomali li rileva
entrambi: chi **paga senza consumare** (compare come riga propria nel pannello) e chi
**consuma senza comparire in fattura** (oggi solo contato, in una riga di riepilogo:
*«N consumano ma non sono in fattura»*).

Il secondo caso è il più grave dei due — è il segnale di una postazione assegnata fuori dal
processo di acquisto — e ha il trattamento più debole: un numero, senza il modo di vedere
**quali**. Serve poterle elencare e portare via, come si fa già con le altre righe.

### PF13 — Andamento dell'adozione nel team

**Stato:** da fare · **Dipende da:** PF04

Quante delle postazioni pagate sono attive, **mese per mese**. Oggi la scheda Persone dice
quante lo sono *adesso*; quello che manca è se il numero cresce, e quanto ci mette una
postazione nuova a entrare nel lavoro.

Il calcolo esiste già e ha le sue prove: `cm_statistiche.adozione_team()` legge la tabella
`sessions` dell'archivio del raccoglitore e restituisce postazioni, sessioni e costo per
periodo, buchi compresi. Manca solo la vista — e la decisione su dove metterla: una sezione
della scheda Persone, o una seconda serie nella scheda Andamento quando l'archivio del team c'è.

Una cosa da non dimenticare nella presentazione: **le postazioni ferme restano invisibili**,
perché chi non usa lo strumento non manda niente. La riga «attive» va sempre letta contro le
postazioni dichiarate, mai da sola.
