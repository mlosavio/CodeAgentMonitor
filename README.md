# claude-code-monitor

Tempo, costo e numero di messaggi delle conversazioni **Claude Code**, ricavati dai transcript
JSONL che Claude Code scrive in locale. Con interfaccia grafica, cruscotto live nel terminale
e un segmento per la statusline.

Per un gruppo di lavoro c'è anche un [raccoglitore della telemetria](#più-macchine-il-pannello-di-team)
che mette insieme più macchine, con tre livelli di riservatezza fra cui scegliere.

Solo libreria standard: **nessuna dipendenza da installare**, né Python né npm.

![licenza MIT](https://img.shields.io/badge/licenza-MIT-blue) ![solo stdlib](https://img.shields.io/badge/dipendenze-nessuna-brightgreen)

![claude-code-monitor](docs/claude-code-monitor.png)

*Nelle schermate i nomi dei progetti e i titoli delle conversazioni sono offuscati.*

---

## A cosa serve

Claude Code non dice quanto stai consumando, né dove. Questo strumento legge i transcript che
scrive già da solo e risponde a tre domande diverse, che è facile confondere:

- **quanto ho consumato** — token, messaggi, tempo di lavoro effettivo, e quanta parte dei
  limiti del piano hai usato;
- **quanto varrebbe a listino API** — il valore di quel consumo, che con un abbonamento
  **non paghi**;
- **quanto ho speso davvero** — la quota mensile, oppure l'addebito a consumo se usi l'API.

```
MESE       SESS  PROG     TOKEN  OUTPUT  SE FOSSE API   PAGATO   RESA
2026-03 *     9     4  1842.20M   5.10M      $1,204.6   $20.00  60.2×
2026-02       6     3   980.44M   2.31M        $612.8   $20.00  30.6×
2026-01       1     1     4.02M     71k          $4.10   $20.00   0.2×
TOTALE                 2826.66M   7.48M      $1,821.5   $60.00  30.4×
```

Gennaio a `0,2×` vuol dire che la quota di quel mese è stata pagata quasi a vuoto.

---

## Installazione

Serve **Python 3.9+** e Claude Code. Niente altro.

```bat
git clone https://github.com/mlosavio/claude-code-monitor.git
cd claude-code-monitor
copy config.example.json config.json
python claude_monitor.py
```

Apri `config.json` (o il pulsante **Configura** nella GUI) e metti il tuo piano e quanto paghi
al mese: è l'unico dato che il tool non può ricavare da solo.

Per il segmento nella statusline di Claude Code:

```bat
python install_statusline.py           :: installa
python install_statusline.py --wrap    :: conserva la statusline che hai già
```

---

## Uso

```bat
python claude_monitor_gui.py           :: interfaccia grafica
python claude_monitor.py               :: riepilogo delle ultime sessioni
python claude_monitor.py --by-month    :: consumo, speso e resa per mese
python claude_monitor.py --by-project  :: totali per progetto
python claude_monitor.py --watch       :: cruscotto live nel terminale
python claude_monitor.py --json        :: output machine-readable
```

| Opzione | Effetto |
|---|---|
| `--base PATH` | cartella dei transcript (default `%USERPROFILE%\.claude\projects`) |
| `--config PATH` | file di configurazione alternativo |
| `--billing subscription\|api` | come viene pagato l'uso, sovrascrive la configurazione |
| `--project NOME` | filtra per sottostringa del percorso o del progetto |
| `--since` | `7d`, `24h`, `90m`, `oggi`, `2026-03-01` |
| `--top N` | massimo di sessioni (o di turni con `--session`); `0` = tutte |
| `--session UUID` | dettaglio di una sessione, anche solo col prefisso |
| `--chat` | con `--session`: la conversazione in Markdown |
| `--watch` / `--interval S` | live sulla sessione modificata più di recente |
| `--idle-gap S` | pausa oltre la quale il tempo non è "attivo" (default 300s) |
| `--no-cache` / `--clear-cache` | ignora / svuota la cache su disco |
| `--no-color` | niente ANSI (rispetta anche `NO_COLOR`) |

---

## Interfaccia grafica

```bat
python  claude_monitor_gui.py     :: con console, per vedere i traceback
pythonw claude_monitor_gui.py     :: senza console
```

Stessa logica del CLI (lo importa come modulo), quindi i numeri coincidono alla cifra.

- **Tessere** in alto: quanto hai pagato, quanto varrebbe a listino, tempo attivo, messaggi,
  sessione in corso e **consumo dei limiti del piano**.
- Quattro schede: **Progetti**, **Sessioni** (con il titolo che Claude Code assegna alla
  conversazione), **Mesi** e **Persone**. Colonne ordinabili; l'ordinamento usa i valori
  numerici, non le stringhe formattate, quindi `$2,055.7` non finisce sotto `$301.4`.
- **Persone** mostra il consumo di più macchine e compare popolata solo se hai avviato il
  [raccoglitore](#più-macchine-il-pannello-di-team). L'intestazione della prima colonna dice
  *Persona*, *Postazione* o *Insieme* a seconda del livello di riservatezza in vigore, così si
  capisce senza chiedere se si stanno guardando persone o codici. **Doppio click su una
  postazione** apre su cosa ha lavorato, progetto per progetto: è la vista per il ribaltamento
  sulle commesse.
- **Ogni intestazione ha la sua spiegazione** al passaggio del mouse.
- **Doppio click su una sessione** apre la conversazione, rileggibile, con export in Markdown.
- **Live**: la tessera di destra segue la sessione attiva e si aggiorna ogni 2 s leggendo solo
  i byte nuovi del transcript. Diventa verde quando quella sessione sta lavorando.
- **Aggiornamento automatico ogni 5 minuti**, configurabile.
- Filtri per periodo e progetto, export JSON, `Ctrl+C` copia la riga, F5 aggiorna.

Aspetto: superfici piatte, tabella disegnata su Canvas, tema chiaro/scuro che segue il sistema
(barra del titolo inclusa) e si forza con `--light` / `--dark`.

Opzioni: `--theme auto|light|dark`, `--tab progetti|sessioni|mesi`, `--live`, `--detail <uuid>`,
`--auto-refresh MIN`, `--locale us|it`.

Il pulsante **Configura** apre le impostazioni vere, non il file JSON: abbonamento, team,
aspetto, statusline e listino. Se una modifica richiede il riavvio, l'applicazione si riavvia
da sola.

---

## Rileggere le conversazioni

**Doppio click su un progetto** nella scheda Progetti scende alle sue conversazioni: filtra e
passa alla scheda Sessioni. Per tornare a tutte, svuota il filtro.

La scheda **Sessioni** è l'indice della history: titolo, data, progetto, durata, costo. Doppio
click su una riga:

- **Conversazione** — quello che hai chiesto e quello che è stato fatto, in ordine, con le
  chiamate a tool riassunte in una riga (`⚙ Read, Bash, Edit`) invece che riportate per intero;
- **Costi** — lo stesso percorso ma con token e costo per turno;
- **Esporta .md** — la conversazione in Markdown, con titolo, data, durata e costi in testa.

### Esportare un progetto intero

Il menu **Esporta ▾** ha due voci: *Dati in JSON* (i numeri, per elaborarli altrove) e
**Conversazioni in Markdown**, che esporta **tutto quello che stai vedendo** — quindi filtra
per progetto o per periodo e poi esporta. Produce una cartella per progetto più un indice:

```
esportazione/
  indice.md
  MioProgetto/
    2026-03-14 Rifare il layout della dashboard [a1b2c3d4].md
    2026-03-02 Aggiungere export CSV [e5f6a7b8].md
  SitoWeb/
    2026-02-20 Ottimizzare le immagini [c9d0e1f2].md
```

`indice.md` raggruppa per progetto e per ognuno elenca data, titolo (in link al file),
messaggi, tempo attivo e valore a listino. Il nome dei file parte dalla data, così l'ordine
alfabetico è già quello cronologico.

Da riga di comando, con gli stessi filtri:

```bat
python claude_monitor.py --session a1b2c3d4 --chat > conversazione.md
python claude_monitor.py --project MioProgetto --export-md .\esportazione
python claude_monitor.py --since 30d --export-md .\esportazione --with-subagents
```

I messaggi dei subagent sono esclusi per default: sono lavoro interno e spezzerebbero il filo
del discorso. Nella finestra si vedono al massimo gli ultimi 400 messaggi; l'esportazione li
contiene tutti.

L'esportazione rilegge ogni sessione per intero — il testo non sta nella cache, che tiene solo
i numeri — quindi su molte conversazioni ci vuole qualche minuto. Nella GUI gira su un thread
separato con avanzamento nella barra di stato, e sopra le 40 conversazioni chiede conferma.

### Dove sono salvate, e serve un database?

**No.** Le conversazioni le scrive già Claude Code, un file JSONL per sessione:

```
%USERPROFILE%\.claude\projects\<progetto>\<uuid>.jsonl
```

Questo strumento le legge **in sola lettura**: l'unica cosa che scrive è `.cache.json`, dati
derivati cancellabili in qualsiasi momento. Un database sarebbe una seconda copia da tenere
sincronizzata, che si disallinea appena Claude Code cambia formato.

**I transcript però scadono.** Claude Code ha `cleanupPeriodDays`, che cancella quelli più
vecchi di N giorni. Se vuoi conservarli a lungo, in `~/.claude/settings.json`:

```json
"cleanupPeriodDays": 3650
```

Il costo è lo spazio su disco, che cresce di qualche centinaio di MB al mese di uso intenso.
Per le conversazioni che ti interessano davvero, l'esportazione in Markdown resta il modo più
solido: è un file tuo, leggibile senza questo tool e senza Claude Code.

---

## Cosa hai speso davvero

Con l'abbonamento **l'unica cifra uscita dal conto è la quota mensile**. Tutto il resto misura
il consumo, non la spesa.

| colonna | cos'è | l'hai pagato? |
|---|---|---|
| **`PAGATO`** (vista `--by-month`) | la quota mensile | **sì**, è l'unico importo vero |
| **`SE FOSSE API`** | quanto sarebbe costato a listino API | **no**, mai |
| **`QUOTA DEL CONSUMO`** | quanto pesa una riga sul consumo totale | è una percentuale, non una spesa |

Gli euro compaiono **solo dove sono veri**: nella vista mensile. Nelle viste per progetto e per
sessione la colonna è una percentuale di consumo.

### Perché non ripartisco la quota sui progetti

Una versione precedente lo faceva, mese per mese, in proporzione al consumo. Il risultato era
indifendibile: un progetto da `$3,80` e 32 minuti di lavoro si prendeva il **25%** di tutto,
perché era l'unico attivo in un mese in cui la quota era stata pagata comunque.

Il difetto non è aritmetico ma concettuale: **quella quota non l'ha causata quel progetto.**
L'avresti pagata anche non aprendo Claude Code. Era capacità comprata e non usata, e il modello
la scaricava sull'unica riga presente. E ripartendo sull'intero periodo invece che mese per
mese, la colonna diventa *identica* alla quota di consumo: non aggiungeva informazione,
aggiungeva solo un modo di sbagliarsi.

La quota non sfruttata si legge dove ha senso, cioè nella vista **Mesi**: un mese con
`RESA 0,2×` è un mese pagato quasi a vuoto.

### Abbonamento o API

Lo switch è in `config.json` → `billing.mode`, ribaltabile con `--billing api` o dal menu nella
GUI:

- **`subscription`** — quota fissa. Il costo per token è solo un riferimento;
- **`api`** — a consumo: il costo per token *è* l'addebito, e le due colonne diventano una sola.

Si possono marcare singoli progetti o sessioni come a consumo pur restando in abbonamento,
utile se li lanci con `ANTHROPIC_API_KEY`:

```json
"billing": { "mode": "subscription", "api_projects": ["ProgettoX"], "api_sessions": [] }
```

### Una sessione può stare a cavallo di due mesi

Attribuirla tutta a un mese falserebbe entrambi, quindi i token vengono **buckettizzati per
mese al momento del parsing**, messaggio per messaggio:

```
MioProgetto  se fosse API $1.204,60
   2026-02   se fosse API   $412,30
   2026-03   se fosse API   $792,30
```

La somma dei bucket mensili coincide con il totale delle sessioni alla sesta cifra decimale.

---

## I limiti del piano

Non esistono finestre "giornaliere": Claude Code ne espone **due**, una di **5 ore** e una di
**7 giorni**, entrambe in percentuale usata. Si vedono in due posti, con affidabilità diversa:

| Dove | Sorgente | Freschezza |
|---|---|---|
| **Statusline** | il payload che Claude Code passa al comando, dagli header della risposta API | sempre attuale |
| **Tessera nella GUI** | `~/.claude.json` → `cachedUsageUtilization` | vale quanto l'ultimo aggiornamento |

Il file su disco lo aggiorna Claude Code **solo quando parla con l'API**, e lo considera
scaduto dopo un'ora. Per questo la tessera **non mostra numeri in cui non si può credere**:
una finestra il cui reset è già passato diventa `—`, e l'età del dato è sempre scritta sotto
(`letto 23 ore fa`). Un `—` non vuol dire "consumo zero", vuol dire "quel numero non vale più".

---

## Più macchine: il pannello di team

I transcript coprono la tua macchina e basta. Per vedere un gruppo di lavoro serve un'altra
fonte, e **Claude Code ce l'ha già**: sa esportare da sé la propria telemetria via
OpenTelemetry, senza che sulle postazioni debba girare niente di scritto da noi.

`cm_collector.py` riceve quel flusso e lo conserva. Riceve **OTLP in codifica JSON su HTTP**,
quindi niente protobuf e nessuna dipendenza: solo stdlib, come tutto il resto.

```bat
python cm_collector.py --setup          :: stampa la configurazione da mettere in settings.json
python cm_collector.py                  :: avvia il raccoglitore su 127.0.0.1:4318
python cm_collector.py --report --by user
```

Poi **riavvia Claude Code**: le variabili d'ambiente si leggono all'avvio, le sessioni già
aperte non esportano nulla. Il cruscotto è su `http://127.0.0.1:4318/`, e nel pannello grafico
compare la scheda **Persone**.

### Cosa arriva

| Metrica | Cosa misura |
|---|---|
| `cost.usage` | valore del consumo a listino API |
| `token.usage` | token, divisi in input, output, cache read, cache creation |
| `active_time.total` | tempo di lavoro effettivo |
| `session.count` | sessioni avviate |
| `subagent.spawn` | subagent generati |
| `tool.execution`, `code_edit_tool.decision` | uso degli strumenti e modifiche accettate |
| `lines_of_code.count`, `commit.count`, `pull_request.count` | prodotto del lavoro |
| `mcp.rpc`, `compaction`, `hook`, `bash.subprocess` | il resto della strumentazione |

Attributi: `user.email`, `user.id`, `organization.id`, `session.id`, `model`, `terminal.type`.

### I tre livelli di riservatezza

**La telemetria manda `user.email` in chiaro, sempre, e non c'è un'impostazione sulla postazione
che lo tolga.** Il livello di dettaglio si impone quindi **nel raccoglitore**, nel momento in cui
il dato viene scritto — che è anche l'unico punto in cui la scelta è verificabile, e l'unico
sotto il controllo di chi amministra invece che di ogni singola macchina.

| `--privacy` | Cosa finisce in archivio | Cosa resta possibile |
|---|---|---|
| `aggregato` | nessun identificativo di persona | costo per modello, resa complessiva, peso della cache |
| `pseudonimo` *(default)* | un codice stabile a chiave, non l'indirizzo | anche postazioni ferme e saturazione dei limiti |
| `nominativo` | l'indirizzo di posta | attribuzione diretta |

Con `pseudonimo` la chiave sta in un file separato dall'archivio: chi ha l'archivio non risale
alle persone, chi ha anche la chiave può ricalcolare i codici da un indirizzo noto. Tenerli
separati è tutto il senso del livello intermedio.

Il testo delle richieste **non esce mai**: `OTEL_LOG_USER_PROMPTS` resta a `0`, ed è il valore
predefinito. La configurazione stampata da `--setup` lo scrive comunque in modo esplicito,
perché è la riga che si mostra a chi chiede.

`python test_collector.py` verifica anche questo, e lo verifica **sui byte del file**: con
`pseudonimo` l'indirizzo non deve comparire, con `nominativo` deve — altrimenti la prova
passerebbe a vuoto. Va guardato anche il file `-wal`, perché finché SQLite non fa il checkpoint
i dati stanno lì e non nel `.db`.

### Lo storico: `cm_agent.py`

La telemetria parte dal giorno in cui la accendi. Su questa macchina, il giorno
dell'accensione, copriva lo **0,00%** del consumo totale: `$0.10` contro `$2.958,85`
ricavati dai transcript, che erano sul disco da tre mesi. È la misura di cosa manca alla sola
telemetria.

`cm_agent.py` colma quel vuoto: rilegge i transcript con lo stesso parser del pannello, calcola
la differenza rispetto a quanto già spedito e manda solo quella.

```bat
python cm_agent.py --dry-run       :: mostra cosa spedirebbe, senza spedire
python cm_agent.py --show-payload  :: stampa il JSON esatto che uscirebbe
python cm_agent.py --once          :: un invio solo
python cm_agent.py                 :: resta e rispedisce ogni 15 minuti
```

Non apre porte e non resta in ascolto: parla solo lui, verso il raccoglitore. Funziona quindi
identico in sede, in VPN e su un portatile fuori rete, e non aggiunge superficie di attacco
sulle postazioni.

**Cosa esce da una macchina** è un elenco di *inclusioni*, non di esclusioni — `CAMPI_SPEDITI`
in cima al file. La differenza conta: un elenco di esclusioni ci si dimentica di aggiornarlo
quando il parser guadagna un campo nuovo, uno di inclusioni lascia il campo nuovo a terra, che
è il verso giusto in cui sbagliare. Titoli, testo delle richieste, percorsi e nomi di ramo non
sono nell'elenco. Il controllo gira **prima di ogni invio**, non solo nelle prove, e
`--show-payload` mostra esattamente i byte che partirebbero.

L'identità è l'indirizzo dell'account letto da `~/.claude.json`, lo stesso che manda la
telemetria: è l'unico modo perché le due fonti si uniscano sulla stessa persona invece di
comparire come due postazioni distinte. Il raccoglitore lo riduce secondo il livello scelto,
esattamente come fa con la telemetria.

### Due fonti, mai sommate

Appena la telemetria è accesa, la stessa sessione esiste in entrambe le fonti. Sommarle la
conterebbe due volte, quindi il pannello sceglie:

| Grandezza | Da dove | Perché |
|---|---|---|
| costo, token, tempo attivo, sessioni, progetti | **transcript** | coprono anche i mesi precedenti all'accensione |
| righe modificate, commit, PR, subagent, strumenti, MCP | **telemetria** | i transcript non le hanno |
| ultima attività | entrambe | è un massimo, non una somma |

Una postazione senza agente compare lo stesso, ma segnata come tale: mostra solo quello che la
telemetria ha visto da quando è stata accesa, che di solito è molto meno del vero.

### Le postazioni ferme non si vedono, si deducono

Chi non usa Claude Code non manda telemetria, quindi **non compare da nessuna parte**. Per
scoprire le postazioni pagate e mai usate bisogna dichiarare quante se ne pagano, nella pagina
**Team** della configurazione o nella sezione `team` di `config.json`:

```json
"team": { "seats": 8, "fee_per_seat": 30.0, "currency": "EUR", "db": null }
```

Da lì il pannello ricava le colonne **Hai pagato** e **Resa**, e il conto di quanto si spende a
vuoto. Con l'abbonamento di gruppo ogni postazione costa uguale che venga usata o no, quindi la
domanda utile non è quanto ha speso una persona — la risposta è sempre la stessa cifra — ma
quanto ha reso la postazione rispetto a quello che si paga comunque.

```
postazione      pagato    se fosse API    resa
anna@x.it       90,00€        $421.00     4,3×
bruno@x.it      90,00€        $180.00     1,9×
carla@x.it      90,00€         $12.00    <0,1×

8 postazioni pagate · 3 usate · 5 ferme = 450 € in 3 mesi
```

Il totale della colonna *Hai pagato* copre tutte le postazioni, ferme comprese: sommare le righe
visibili darebbe una cifra più bassa e farebbe sparire proprio i soldi spesi per niente.

### Tenerlo acceso

Un raccoglitore fermo **non lascia traccia**: l'esportatore ritenta per poco e poi lascia
perdere, e i dati di quell'intervallo non si recuperano. Avviato a mano da un terminale muore
con quel terminale, quindi va installato come servizio:

```bat
python cm_collector.py --setup-service
```

stampa il comando giusto per il sistema in uso — attività pianificata su Windows, unità systemd
altrove. Per un gruppo di lavoro il raccoglitore sta su **una** macchina in sede, installato
come servizio di sistema e con `--host` aperto oltre `127.0.0.1`; le postazioni gli mandano i
dati e non ricevono connessioni da nessuno. Appena apri alla rete serve `--token`, altrimenti
chiunque può scrivere in archivio: l'avvio te lo dice.

### Quanto grande può essere il gruppo

`python test_carico.py 50 20` simula cinquanta postazioni che spediscono insieme. Misurato su un
portatile Windows: **576 richieste al secondo**, mille richieste servite in 1,7 secondi, caso
peggiore 1,5 s, nessuna riga persa né contata due volte.

Per confronto, un agente spedisce ogni 15 minuti: cinquanta postazioni fanno **una richiesta
ogni 18 secondi**. Il margine è di circa quattro ordini di grandezza, quindi il raccoglitore su
una macchina qualunque basta ampiamente — il dimensionamento del server non è un problema di
questo progetto.

---

## Cosa legge

```
%USERPROFILE%\.claude\projects\<progetto>\<uuid>.jsonl                              sessione
%USERPROFILE%\.claude\projects\<progetto>\<uuid>\subagents\agent-*.jsonl            subagent
%USERPROFILE%\.claude\projects\<progetto>\<uuid>\subagents\workflows\wf_*\*.jsonl   workflow
```

I file dei subagent e dei workflow vengono **attribuiti alla sessione padre**: il costo di una
conversazione include quello dei suoi agenti.

### Metriche

- **Durata** — `max(timestamp) − min(timestamp)` della sessione.
- **Attivo** — somma degli intervalli fra eventi consecutivi più corti di `--idle-gap`
  (default 5 min). Distingue "sessione aperta da 3 giorni" da "3 ore di lavoro".
- **Messaggi** — mostrati come `tuoi / di Claude`. Il secondo è molto più alto perché ogni
  lettura di file, comando o modifica è un messaggio a sé. Non contano come tuoi i
  `tool_result` (che Claude Code registra come messaggi di tipo `user`), i messaggi di sistema
  né i segnaposto tipo `[Request interrupted by user]`.
- **Token** — input, output, cache write 5m, cache write 1h, cache read.
- **Costo** — calcolato **riga per riga** con il modello di quella riga:

```
costo = input        × IN
      + output       × OUT
      + cache_read   × IN × 0.10     lettura cache: 10% dell'input
      + cache_w_5m   × IN × 1.25     scrittura cache TTL 5m: +25%
      + cache_w_1h   × IN × 2.00     scrittura cache TTL 1h: +100%
      + web_search_requests × $0.01
```

### La cache è quasi tutto il conto

Su un dataset reale il **98% dei token trattati** sono riletture della cache: non contenuti
nuovi, ma la stessa conversazione ricaricata a ogni messaggio. La scomposizione tipica:

| voce | quota del costo |
|---|---|
| rilettura del contesto (×0,10) | ~70% |
| scrittura cache TTL 1h (×2,00) | ~20% |
| testo generato (prezzo pieno) | ~9% |
| input non in cache | ~0% |

Due conseguenze pratiche: il costo di una sessione lunga lo fa **quanto contesto si trascina
dietro**, non quante risposte produce; e in abbonamento quei token non si pagano in denaro ma
**in limiti**, che è il vincolo vero.

---

## Due dettagli che cambiano il risultato

**1. Deduplica delle righe di streaming.** Durante la risposta Claude Code scrive **una riga
per blocco di contenuto** (`thinking`, `text`, ogni `tool_use`), tutte con lo stesso
`message.id`/`requestId` e con la `usage` ripetuta — `output_tokens` cresce man mano, gli altri
campi restano identici. Sommare tutte le righe assistant **gonfia il costo di oltre il doppio**:
su un dataset reale, 7.155 righe assistant corrispondevano a 3.270 messaggi veri. Il tool
deduplica su `(requestId, message.id)` tenendo il massimo per campo.

**2. Le righe non sono contigue.** Verrebbe da tenere in sospeso solo l'ultima chiave e
chiudere le precedenti, ma Claude Code riemette interi segmenti di storia — stesso `uuid`,
stesso `timestamp` — anche migliaia di righe dopo (fork, `--resume`, compattazione). Al picco
si sono misurate 377 chiavi aperte insieme: quell'ottimizzazione gonfia i token dell'11,3%.

---

## Configurazione

Dalla GUI: pulsante **Configura**. Un pannello su cinque pagine:

| Pagina | Cosa ci trovi |
|---|---|
| **Abbonamento** | abbonamento o API, piano, costo mensile, valuta, data di attivazione, cambio |
| **Team** | postazioni pagate, quota per postazione, valuta, archivio del raccoglitore |
| **Aspetto** | tema, formato numeri, ogni quanto aggiornare, soglia di inattività |
| **Statusline** | quali pezzi mostrare e le soglie di colore dei limiti |
| **Listino** | prezzo input/output di ogni modello |

Nella pagina Team il livello di riservatezza è **in sola lettura**, con scritto dove si cambia:
lo impone il raccoglitore quando scrive, non il pannello quando legge. Metterlo lì come campo
modificabile suggerirebbe un controllo che il pannello non ha.

I valori sono validati prima di scrivere; se cambi il tema **l'applicazione si riavvia da sola**,
perché quello non si applica a caldo.

Sotto c'è un unico file, `config.json` accanto allo script, modificabile a mano — i commenti
dentro il file vengono conservati dal pannello. **`config.json` è ignorato da git**: parti da
`config.example.json`.

**Cosa viene ricordato e dove:**

| | Dove |
|---|---|
| Piano, costo, valuta, tema, listino, statusline, switch abbonamento/API | `config.json` |
| Periodo, filtro, scheda aperta, Live, geometria, ordinamento | `%LOCALAPPDATA%\claude-monitor\gui.json` |

I filtri di vista stanno separati dalla configurazione di proposito: sono comodità d'uso e non
ha senso che finiscano in un file che potresti versionare o copiare su un'altra macchina.

---

## Statusline

`install_statusline.py` mette il segmento nella statusline di Claude Code:

```
$3.42 · 18m · 26 · 5h 31%
└ costo · tempo attivo · messaggi · limite di 5 ore
```

`5h N%` diventa **arancione** oltre il 75% e **rosso** oltre il 90% (soglie configurabili).

**Se avevi già una statusline non viene sostituita**: `install_statusline.py --wrap` la esegue
come processo figlio e le appende il segmento. Quel comando non viene mai modificato, quindi un
suo aggiornamento non rompe niente.

È scritta in Node e non in Python perché la statusline viene ridisegnata di continuo e ogni
render è un processo nuovo: su una macchina di prova l'avvio di `python` costava 205–239 ms
contro gli 80–91 di `node`. I prezzi restano single-source (legge lo stesso `config.json`); si
duplica solo la formula, e `--selftest` la confronta col CLI:

```bat
node %USERPROFILE%\.claude\hooks\cm-statusline.js --selftest a1b2c3d4
python claude_monitor.py --session a1b2c3d4
```

**Come non rompe Claude Code**: budget di 150 ms sul calcolo, timeout di 1500 ms sul processo
figlio, al massimo 8 MB di arretrato letti per render, e qualunque errore fa comunque uscire 0
stampando l'output avvolto invariato. Testato con listino mancante, cache corrotta, transcript
troncato, stdin vuoto o non-JSON e comando avvolto assente.

Rollback: `python install_statusline.py --remove` rimette a posto la statusline precedente.

### Come lo stato incrementale resta esatto

Ogni render è un processo nuovo, quindi lo stato vive su disco in due file per sessione:
`<sid>.sum.json` (piccolo, letto sempre) e `<sid>.keys.json` (la mappa di dedup completa,
aperta solo se qualche file è cresciuto). I totali si aggiornano per differenza, quindi il caso
normale non itera mai tutte le chiavi. A regime: una lettura, **zero scritture**.

Il **tempo attivo** non richiede di salvare i timestamp: si mantiene l'unione dei cluster
separati da più di `idle_gap`, che è identico alla somma dei gap ordinati del CLI — verificato
al secondo su una sessione da 140 MB — e servono qualche centinaio di voci invece di decine di
migliaia.

Il campo `cost.total_cost_usd` che Claude Code passa alla statusline **non** viene usato per il
valore definitivo: è un contatore per-processo, azzerato a ogni `--resume`, mentre il transcript
conserva tutta la storia.

---

## VS Code

Claude Code gira spesso dentro VS Code, quindi il monitoraggio è a due livelli: la **statusline**
sempre visibile nel panel, e i **task** in `.vscode/tasks.json` (`Ctrl+Shift+P` → *Run Task*):
GUI, GUI con console, watch in terminale dedicato, riepilogo per progetto come build di default
(`Ctrl+Shift+B`), dettaglio di una sessione, svuota cache.

---

## Prestazioni

La prima analisi legge tutti i transcript (un file può superare i 100 MB) e salva i risultati in
`.cache.json`, invalidati per file su `(size, mtime)`: le esecuzioni successive sono istantanee.
`--watch` e la modalità Live usano un parser **incrementale** che legge solo i byte aggiunti.

Su un dataset di prova (106 file, 127 MB il maggiore): prima analisi ~1 s a cache disco calda,
successive ~0,3 s, refresh live ~5 ms.

---

## Limiti noti

- **Contesto 1M**: i modelli con finestra estesa hanno un listino premium, ma il transcript
  registra l'id senza il suffisso `[1m]` — non sono distinguibili a posteriori e vengono
  conteggiati al prezzo standard.
- **Modelli sconosciuti**: se un modello non è nel listino il costo è 0 e viene stampato un
  avviso con l'id da aggiungere.
- **I prezzi cambiano**: `config.json` ha un campo `updated`, mostrato in fondo a ogni report.
- **Righe parziali**: durante lo streaming l'ultima riga del file può essere incompleta. Viene
  saltata senza errori.
- **La telemetria parte da quando l'accendi**: non ha memoria di quello che è successo prima,
  e i mesi già passati restano leggibili solo dai transcript, cioè solo sulla macchina che li
  ha prodotti. Portare lo storico di più macchine in un archivio unico richiederebbe un
  componente sulle postazioni che oggi non c'è.
- **"Hai pagato" viene da quello che dichiari**, non da una fattura letta: postazioni per quota
  per mesi coperti dai dati. La riconciliazione con l'export di fatturazione non c'è ancora.
- **Il campo `real_cost` delle sessioni non va sommato per progetto**: è la quota ripartita per
  *mese*, quindi in un mese poco usato un progetto da pochi dollari si prende tutto il canone.
  Per il team la cifra buona è postazioni × quota, che è quella che il pannello mostra.
- Sviluppato e provato su **Windows**. Il codice usa solo percorsi portabili (`expanduser`,
  `USERPROFILE`/`LOCALAPPDATA`) ma su macOS e Linux non è stato testato: segnalazioni benvenute.

---

## Contribuire

Segnalazioni e pull request sono benvenute. Il progetto è volutamente **senza dipendenze**:
proposte che ne aggiungano vanno motivate. Il codice e i commenti sono in italiano.

Prima di aprire una PR, controlla che il CLI non cambi comportamento:

```bat
python claude_monitor.py --json --top 0 > prima.json
:: ... modifiche ...
python claude_monitor.py --json --top 0 > dopo.json
```

Le due uscite devono differire solo per `generated_at` e per le sessioni cresciute nel frattempo.
Se tocchi la formula di costo, `node statusline/cm-statusline.js --selftest <uuid>` deve
coincidere con `python claude_monitor.py --session <uuid>` al centesimo.

Se tocchi il raccoglitore, `python test_collector.py` deve restare verde: copre i punti dove il
conteggio si sbaglia — invii ritentati, metriche cumulative, totali per gruppo — e il confine di
riservatezza.

`python test_scenario.py` fa una cosa diversa: avvia un raccoglitore vero su una porta libera e
ci parla **via rete**, simulando tre postazioni con storici diversi più cinque pagate e mai
usate. Serve a prendere gli errori che stanno *fra* i pezzi invece che dentro un pezzo — il
primo che ha trovato era il raccoglitore che leggeva l'identità da ogni sessione invece che
dalla busta, con il risultato che i costi di persone diverse finivano sommati in una riga sola.
Usa un archivio temporaneo e lo cancella: non tocca il tuo.

---

## Licenza

[MIT](LICENSE) — Marco Losavio
