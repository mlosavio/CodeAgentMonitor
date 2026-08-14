# Attivare CAM nel team, in sede

Guida per il caso più semplice e più solido: **tutte le postazioni sulla stessa rete
aziendale**, e il raccoglitore sulla macchina dell'amministratore.

Dodici passi su tre ruoli, nell'ordine in cui vanno fatti. Ogni comando dice **dove** si esegue:
sbagliare macchina è il modo più comune di rompere questa catena, e il secondo è dimenticare di
riavviare Claude Code.

> **Le postazioni non sono tutte sulla stessa rete?** Allora questa guida non basta — apre la
> porta in chiaro e dà a ogni postazione un token che apre anche il cruscotto. Vai a
> [Attivare CAM in un team distribuito geograficamente](team-distribuito.md).

---

## Cosa stai per costruire

Tre ruoli, non tre programmi: due di questi girano sulla stessa macchina.

```
   ┌──────────────┐        ┌──────────────────┐        ┌──────────────┐
   │ Ogni         │  HTTP  │ Il raccoglitore  │ scrive │ L'archivio   │
   │ postazione   │──────▶ │ porta 4318       │──────▶ │ cam-team.db  │
   │ cam_agent.py │ uscita └──────────────────┘        └──────┬───────┘
   └──────────────┘                                           │ legge il FILE
                                                              │ non la rete
                                                      ┌───────▼──────┐
                                                      │ La console   │
                                                      │ cam_gui.py   │
                                                      └──────────────┘
```

| Ruolo | Che cosa gira | Su quale macchina |
|---|---|---|
| **raccoglitore** | `cam_collector.py` | Una sola, accesa quando il team lavora. Nel caso tipico: **il PC dell'amministratore** |
| **console** | `cam_gui.py`, `cam.py` | Il PC dell'amministratore. Deve poter **aprire il file** dell'archivio |
| **postazione** | `cam_agent.py` + `cam.py` | Ogni sviluppatore. Non apre porte, parla solo in uscita |

La freccia che conta è l'ultima: **la console apre l'archivio come file**, non parla col
raccoglitore via rete. Perciò console e raccoglitore stanno bene sulla stessa macchina — ed è il
motivo per cui spostare il raccoglitore altrove le toglie la scheda Persone.

---

## Due decisioni da prendere prima

Cambiarle dopo si può, ma la prima riscrive quello che è già in archivio.

### Quanto dettaglio sulle persone

La telemetria di Claude Code manda `user.email` **in chiaro, sempre**, e nessuna impostazione
sulla postazione lo toglie. Il livello si impone quindi **nel raccoglitore**, nel momento in cui
il dato viene scritto — che è anche l'unico punto verificabile e l'unico sotto il tuo controllo
invece che di ogni singola macchina.

| `--privacy` | Cosa finisce in archivio | Cosa resta possibile |
|---|---|---|
| `aggregato` | nessun identificativo di persona | costo per modello, resa complessiva, peso della cache |
| `pseudonimo` *(predefinito)* | un codice stabile a chiave, non l'indirizzo | anche postazioni ferme e saturazione dei limiti |
| `nominativo` | l'indirizzo di posta | attribuzione diretta |

Il testo delle richieste **non esce mai**: `OTEL_LOG_USER_PROMPTS` resta a `0`, che è anche il
valore predefinito. La configurazione generata al passo 9 lo scrive comunque in modo esplicito,
perché è la riga che si mostra a chi chiede.

### Quante postazioni stai pagando

Va **dichiarato**, perché le postazioni dormienti sono invisibili alla telemetria: chi non usa lo
strumento non manda niente e non compare da nessuna parte. Senza quel numero la domanda che conta
— *quante licenze pago a vuoto* — non ha risposta. Serve al passo 7.

---

# Il raccoglitore

Passi 1–6, tutti sul PC dell'amministratore.

### 1. Genera il token condiviso

```bat
python -c "import secrets; print(secrets.token_urlsafe(24))"
```

Aprire alla rete senza token è come lasciare l'archivio scrivibile da chiunque sia in rete: il
raccoglitore avverte all'avvio, ma non impedisce.

> **Prima di distribuirlo, sappi cosa distribuisci.** Il token è **uno solo, e vale in lettura
> come in scrittura**. Lo stesso token che dài a ogni postazione per mandare i dati apre anche il
> cruscotto, dove si vede quanto consuma ogni collega. Non è un dettaglio tecnico: o lo accetti,
> o il cruscotto non va esposto alla rete. Token separati sono
> [PF14](../TODO.md#pf14--il-raccoglitore-fuori-dalla-rete-aziendale).

### 2. Prepara due cartelle, non una

L'archivio e la chiave degli pseudonimi vanno in **posti diversi, con permessi diversi**: chi ha
l'archivio non deve poter risalire alle persone. È tutto il senso del livello intermedio, e
metterli nella stessa cartella lo annulla senza che nulla smetta di funzionare — cioè senza che
nessuno se ne accorga.

```powershell
New-Item -ItemType Directory -Force C:\claude-team
New-Item -ItemType Directory -Force C:\claude-team\chiavi

# togli l'ereditarietà e lascia le chiavi al solo amministratore
icacls C:\claude-team\chiavi /inheritance:r
icacls C:\claude-team\chiavi /grant:r "$env:USERNAME:(OI)(CI)F"
```

### 3. Avvialo una prima volta, a mano

`--host 0.0.0.0` significa «su tutte le interfacce». Non è un indirizzo a cui collegarsi: le
postazioni useranno il nome della macchina.

```powershell
python cam_collector.py --host 0.0.0.0 --port 4318 --token IL-TOKEN `
    --privacy pseudonimo `
    --db  C:\claude-team\cam-team.db `
    --key C:\claude-team\chiavi\cam-pseudonimi.key
```

> **Non c'è TLS.** Il raccoglitore parla HTTP in chiaro: in tutto il modulo non compare `ssl`. In
> rete aziendale è una scelta difendibile. Su un indirizzo raggiungibile da Internet no — vedi la
> [guida per il team distribuito](team-distribuito.md).

### 4. Apri la porta, solo sul profilo di dominio

Non è un servizio da esporre altrove. Da PowerShell **come amministratore**:

```powershell
New-NetFirewallRule -DisplayName 'cam-collector' -Direction Inbound `
    -Protocol TCP -LocalPort 4318 -Action Allow -Profile Domain
```

### 5. Verifica da un'altra macchina, adesso

Prima di andare a configurare dieci postazioni, non dopo.

```bat
curl "http://NOME-DEL-PC-ADMIN:4318/healthz?token=IL-TOKEN"
```

Se non risponde, il problema è la rete o il firewall. Il token nell'indirizzo serve perché un
browser non manda intestazioni — ed è anche il modo in cui aprirai il cruscotto:
`http://NOME-DEL-PC-ADMIN:4318/?token=IL-TOKEN`

### 6. Fallo ripartire da solo

Questo passo non è facoltativo, ed è il motivo: **un raccoglitore fermo non lascia traccia**.
L'esportatore di Claude Code ritenta per poco e poi lascia perdere, e quell'intervallo *non si
recupera più*. L'agente invece l'arretrato lo ricalcola — ma l'agente copre le sessioni, non la
telemetria.

```powershell
python cam_collector.py --setup-service --host 0.0.0.0 --port 4318 `
    --privacy pseudonimo --db C:\claude-team\cam-team.db
```

Stampa il comando già compilato, in tre versioni: collegamento in Esecuzione automatica, attività
pianificata e unità systemd. Per una macchina di servizio scegli l'**attività pianificata**, che
parte anche senza che nessuno faccia l'accesso.

Ricordati di aggiungere `--token` e `--key` agli argomenti: `--setup-service` ripete quello che
gli passi, e quei due non glieli hai passati.

---

# La console

Passi 7–8, sulla stessa macchina.

### 7. Dì alla console dove sta l'archivio

La console cerca `cam-team.db` accanto a sé stessa; tu l'hai messo in `C:\claude-team`, quindi va
indicato. In `config.json` — le barre rovesciate vanno raddoppiate:

```json
"team": {
  "seats": 8,
  "fee_per_seat": 122.0,
  "currency": "EUR",
  "db": "C:\\claude-team\\cam-team.db"
}
```

`seats` è il numero di postazioni **pagate**, quello deciso sopra. Lasciarlo a `0` spegne le
colonne di spesa e mostra solo il consumo: funziona, ma non risponde alla domanda per cui hai
attivato tutto questo.

### 8. Aprila

```bat
python cam_gui.py
```

La scheda **Persone** è quella nuova. Sarà vuota finché non arriva la prima postazione: è il
passo che segue.

Facoltativo ma è quello che si usa ogni giorno: `python install_statusline.py` aggiunge costo,
tempo attivo e saturazione dei limiti alla statusline di Claude Code. Con `--wrap` conserva la
statusline che hai già invece di sostituirla.

---

# Ogni postazione

Passi 9–12. Cinque minuti a persona, e sono **due cose distinte**: la telemetria la manda Claude
Code da sé, lo storico lo recupera l'agente.

### 9. Genera il blocco di configurazione

Si genera **sul raccoglitore**, con lo stesso token: altrimenti stampa una configurazione che
verrà rifiutata.

```bat
python cam_collector.py --setup --host NOME-DEL-PC-ADMIN --token IL-TOKEN
```

### 10. Applicalo — meglio una volta sola che dieci

Il blocco `env` può andare nel `~/.claude/settings.json` di ciascuno, ma per un team conviene il
file centralizzato non modificabile dall'utente, così la configurazione non dipende dalla buona
volontà di ognuno.

| Sistema | `managed-settings.json` |
|---|---|
| Windows | `C:\Program Files\ClaudeCode\managed-settings.json` |
| macOS | `/Library/Application Support/ClaudeCode/managed-settings.json` |
| Linux | `/etc/claude-code/managed-settings.json` |

> **Il passo che tutti dimenticano: riavvia Claude Code.** Le variabili si leggono all'avvio.
> Senza riavvio non arriva niente, e non c'è nessun messaggio di errore da nessuna parte a dirtelo.

### 11. Metti i due file e prova a vuoto

Sulla postazione servono **due file soli**: `cam.py` e `cam_agent.py`. Nessuna dipendenza, e
`config.json` non è obbligatorio. Va bene una cartella di rete in sola lettura o un clone del
repository.

```bat
python cam_agent.py --endpoint http://NOME-DEL-PC-ADMIN:4318 --token IL-TOKEN --dry-run
```

Se il numero che stampa è plausibile, manda davvero una volta con `--once`. In alternativa a
`--endpoint` e `--token` si possono usare le variabili d'ambiente `CAM_ENDPOINT` e `CAM_TOKEN`.

L'agente esiste perché **la telemetria parte dal giorno in cui la accendi**. Sulla macchina di
sviluppo, il giorno dell'accensione, copriva lo **0,00%** del consumo totale: $0,10 contro
$2.958,85 già sul disco nei transcript. L'agente rilegge i transcript e manda la differenza
rispetto a quanto già spedito.

### 12. Fallo ripartire a ogni accesso

```bat
python cam_agent.py --setup-service
```

Poi resta acceso e rispedisce ogni 15 minuti. Non apre porte e non resta in ascolto: parla solo
lui, verso il raccoglitore. Funziona identico in sede, in VPN e su un portatile fuori rete.

---

## Il giorno dopo, e ogni mese

```bat
python cam_collector.py --status                         :: chi manda, chi è fermo
python cam_collector.py --relazione relazione-agosto.md  :: il riepilogo per la riunione
python cam_collector.py --import-csv export-console.csv  :: la fatturazione vera
python cam_collector.py --riconcilia                     :: confrontata con quello che misuriamo
python cam_collector.py --dimentica nome@azienda.it      :: cancellazione su richiesta
```

**Quando qualcuno lascia il team** vanno tolte **tre** cose, e dimenticarne una lascia una
postazione che manda dati senza che nessuno la stia guardando: la voce
`OTEL_EXPORTER_OTLP_ENDPOINT` dal suo `settings.json`, il blocco `env`, e il collegamento
dell'agente. I dati già in archivio **restano**, ed è voluto: sono la spesa dei mesi che hai
pagato davvero. Per toglierli anche quelli serve `--dimentica`. E abbassa `seats`.

---

## Elenco di controllo

- [ ] Il token è stato generato e conservato dove stanno le altre credenziali
- [ ] Archivio e chiave in cartelle diverse, con permessi diversi
- [ ] La porta 4318 risponde da un'altra macchina della rete
- [ ] Il raccoglitore riparte da solo dopo un riavvio del PC — provalo davvero
- [ ] `seats` dichiarato e pari alle licenze pagate
- [ ] Claude Code è stato **riavviato** su ogni postazione dopo il blocco `env`
- [ ] `--status` elenca tutte le postazioni attese, nessuna muta
- [ ] Tutti sanno che il token apre anche il cruscotto
