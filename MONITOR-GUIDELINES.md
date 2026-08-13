# Monitor di Claude Code — Linee guida per la realizzazione

> Obiettivo: tracciare **tempo**, **costo** e **numero di messaggi** di una conversazione
> Claude Code (e più in generale monitorare cosa fa). Questo documento raccoglie tutto il
> necessario per costruire lo strumento in una sessione dedicata. Fonte: analisi dei transcript
> reali + documentazione ufficiale Claude Code (osservabilità/monitoring, agosto 2026).

---

## 1. Approccio consigliato — Analisi dei transcript JSONL

Claude Code salva ogni conversazione in un file JSONL, una riga per evento. È una fonte
**supportata e stabile** per calcolare costo/durata a posteriori (e anche in live, tailando).

### Dove sono i file
```
~/.claude/projects/<project-encoded>/<session-uuid>.jsonl
```
- Su Windows: `C:\Users\<user>\.claude\projects\<project-encoded>\<session-uuid>.jsonl`
- Il `<project-encoded>` è il path del progetto con `:` `\` `/` `.` sostituiti da `-`
  (es. `c--Users-nome-Progetti-MioProgetto`).
- Un file per **sessione** (conversazione). Più file = più conversazioni.
- NB: verificare a runtime; alcune versioni usano una sottocartella `sessions/`. Nel setup
  osservato i .jsonl stanno **direttamente** nella cartella del progetto.

### Schema di una riga (campi utili — osservati sui file reali)
```jsonc
{
  "type": "assistant" | "user" | ...,     // tipo evento
  "sessionId": "uuid",
  "timestamp": "2026-08-10T12:34:56.789Z", // ISO-8601 UTC
  "message": {
    "model": "claude-opus-4-8",
    "usage": {
      "input_tokens": 7218,
      "output_tokens": 669,
      "cache_creation_input_tokens": 26058,
      "cache_read_input_tokens": 0,
      "cache_creation": {                    // dettaglio TTL della cache
        "ephemeral_5m_input_tokens": 0,
        "ephemeral_1h_input_tokens": 26058
      },
      "server_tool_use": { "web_search_requests": 0, "web_fetch_requests": 0 }
    }
  }
}
```
- I messaggi **assistant** portano `usage` + `model`. I messaggi **user** (e i tool_result,
  che in Claude Code arrivano come contenuto di messaggi `user`) di norma non hanno `usage`.
- Il **modello** può variare tra i messaggi (es. subagent con modello diverso): calcolare il
  costo **per riga**, usando il modello di quella riga.
- **Streaming**: le righe vengono scritte durante la risposta; i conteggi finali sono
  affidabili solo quando la riga è completa. Per `--watch` gestire l'ultima riga parziale.

### Metriche calcolabili
- **Tempo (durata)**: `ultimo.timestamp − primo.timestamp` della sessione (wall-clock). In
  alternativa somma delle durate per turno se si vuole "active time".
- **Messaggi**: conteggio per `type` (user / assistant) e — volendo — quanti blocchi
  `tool_use` / `tool_result` (ispezionando `message.content[]`).
- **Token**: somma per tipo su tutte le righe assistant (input, output, cache read, cache
  write 5m, cache write 1h, web search/fetch).
- **Costo**: vedi tabella prezzi sotto.

---

## 2. Prezzi (listino API) e formula di costo

Costo per singola riga assistant, dato il suo `model` e `usage`:
```
costo =  input_tokens        * IN
       + output_tokens       * OUT
       + cache_read_input_tokens        * IN * 0.10     # lettura cache = 10% dell'input
       + cache_creation.ephemeral_5m   * IN * 1.25     # scrittura cache TTL 5m = +25%
       + cache_creation.ephemeral_1h   * IN * 2.00     # scrittura cache TTL 1h = +100%
       + web_search_requests * 0.01                     # ~ $10 / 1000 ricerche
```
dove IN/OUT sono $ per token (listino / 1_000_000).

### Tabella modelli ($ per 1M token) — verificare/aggiornare a runtime
| Modello              | id                    | Input | Output |
|----------------------|-----------------------|-------|--------|
| Claude Opus 5        | `claude-opus-5`       | 5.00  | 25.00  |
| Claude Opus 4.8      | `claude-opus-4-8`     | 5.00  | 25.00  |
| Claude Opus 4.7/4.6  | `claude-opus-4-7/6`   | 5.00  | 25.00  |
| Claude Sonnet 5      | `claude-sonnet-5`     | 3.00  | 15.00  |
| Claude Sonnet 4.6    | `claude-sonnet-4-6`   | 3.00  | 15.00  |
| Claude Haiku 4.5     | `claude-haiku-4-5`    | 1.00  | 5.00   |
| Claude Fable 5       | `claude-fable-5`      | 10.00 | 50.00  |

- Regole cache (moltiplicatori sull'input): **read ×0.10**, **write 5m ×1.25**, **write 1h ×2.00**.
- I prezzi cambiano: mettere la tabella in un file di config (JSON) editabile, con data.
- **Attenzione al piano** (vedi §5): sotto abbonamento il costo è **nozionale**.

---

## 3. Design del CLI (proposto)

`CodeAgentMonitor` (Python, nessuna dipendenza esterna obbligatoria):

- **Riepilogo** (default): elenca le conversazioni con durata, #messaggi, token (per tipo) e
  **costo stimato** per modello; totale in fondo. Filtri: `--project <nome|all>`, `--since`,
  `--top N`, `--json`.
- **Live** (`--watch`): individua la sessione **attiva** (file .jsonl modificato più di
  recente) e mostra un totale che cresce (tempo trascorso, costo, messaggi) aggiornando ogni
  N secondi; gestisce l'ultima riga parziale.
- **Dettaglio** (`--session <uuid>`): breakdown per messaggio/turno.
- **Config**: `pricing.json` (tabella prezzi + moltiplicatori cache), `plan` = `subscription`
  | `api` (etichetta il costo come nozionale o reale).

### Pseudocodice del core
```python
import json, glob, os, datetime as dt

def load_session(path):
    rows = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line: continue
        try: rows.append(json.loads(line))
        except json.JSONDecodeError: pass   # ultima riga parziale in live
    return rows

def cost_of(model, u, price):
    p = price[model]; IN, OUT = p["in"]/1e6, p["out"]/1e6
    cc = u.get("cache_creation", {}) or {}
    return (u.get("input_tokens",0)*IN
          + u.get("output_tokens",0)*OUT
          + u.get("cache_read_input_tokens",0)*IN*0.10
          + cc.get("ephemeral_5m_input_tokens",0)*IN*1.25
          + cc.get("ephemeral_1h_input_tokens",0)*IN*2.00
          + (u.get("server_tool_use",{}) or {}).get("web_search_requests",0)*0.01)

def summarize(rows, price):
    a = [r for r in rows if r.get("type")=="assistant"]
    ts = [r["timestamp"] for r in rows if r.get("timestamp")]
    dur = None
    if ts:
        t0, t1 = min(ts), max(ts)
        dur = (dt.datetime.fromisoformat(t1.replace("Z","+00:00"))
             - dt.datetime.fromisoformat(t0.replace("Z","+00:00")))
    cost = sum(cost_of(r["message"]["model"], r["message"]["usage"], price)
               for r in a if r.get("message",{}).get("usage") and r["message"].get("model") in price)
    return {"messages_user": sum(1 for r in rows if r.get("type")=="user"),
            "messages_assistant": len(a), "duration": dur, "cost_usd": round(cost, 4)}
```

### Layout file del tool
```
<cartella-del-progetto>\
  MONITOR-GUIDELINES.md   (questo file)
  cam.py       (CLI)
  pricing.json            (prezzi + moltiplicatori cache, con data)
  README.md
```

---

## 4. Alternativa "live" ufficiale — OpenTelemetry (OTEL)

Per un monitoraggio continuo/ricco (dashboard), Claude Code emette metriche/eventi via OTEL.

**Abilitazione (variabili d'ambiente):**
```
CLAUDE_CODE_ENABLE_TELEMETRY=1
OTEL_METRICS_EXPORTER=otlp        # oppure prometheus | console | none
OTEL_LOGS_EXPORTER=otlp           # eventi (log)
OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
# opzionali: OTEL_EXPORTER_OTLP_HEADERS, OTEL_METRIC_EXPORT_INTERVAL (def 60s),
#            OTEL_LOGS_EXPORT_INTERVAL (def 5s), OTEL_SERVICE_NAME=claude-code
```
**Metriche (`claude_code.*`):** `session.count`, `cost.usage` (USD), `token.usage`
(input/output/cache), `lines_of_code.count`, `active_time.total` (durata).
**Eventi (`claude_code.*`):** `user_prompt` (testo solo con `OTEL_LOG_USER_PROMPTS=1`),
`api_request` (model, latenza, token), `api_error`, `tool_decision`, `tool_result`
(dettagli solo con `OTEL_LOG_TOOL_DETAILS=1` / `OTEL_LOG_TOOL_CONTENT=1`), `mcp_server_connection`.
**Attributi comuni:** `session.id`, `user.id`, `organization.id`, `user.email`.

**Setup locale minimo:** un collector OTLP locale
`docker run -p 4317:4317 -p 4318:4318 otel/opentelemetry-collector`; da lì verso
Prometheus/Grafana o un file. L'exporter `console` scrive su stdout (utile per test).

**Quando preferirlo:** dashboard continua, più utenti/team, storicizzazione. Più setup del
CLI JSONL. Per il solo "costo/tempo/messaggi per conversazione", il CLI JSONL basta e avanza.

---

## 5. Hook, /cost e caveat piano

- **Hook** (`PreToolUse`, `PostToolUse`, `UserPromptSubmit`, `Stop`, `SessionStart/End`,
  `SubagentStop`, …): ricevono I/O dei tool e decisioni di permesso, **NON** token/costo/modello.
  Utili per logging/side-effect e controllo di flusso, **non** per il costo live. Per il costo
  via hook: al `Stop`/`SessionEnd` rileggere le ultime righe del JSONL (approccio §1).
- **`/cost`**: comando slash (solo CLI interattiva) che mostra token cumulati, costo stimato,
  durata e statistiche di modifica del codice per la **sessione corrente**.
- **Caveat costo**: con **abbonamento** (Pro/Max) il costo di `/cost` e delle metriche OTEL è
  **nozionale** (stima del costo-equivalente API), non l'addebito reale (che è la quota fissa
  mensile, salvo overflow/limiti). Con **API a consumo** i costi sono reali. Il monitor deve
  poter etichettare la modalità (config `plan`).

---

## 6. Note d'implementazione (Windows/VSCode)
- Percorso base: `os.path.join(os.environ["USERPROFILE"], ".claude", "projects")`.
- Sessione attiva per `--watch`: il .jsonl con `mtime` più recente.
- Timestamp ISO con `Z`: `datetime.fromisoformat(ts.replace("Z","+00:00"))`.
- Robustezza: saltare righe non-JSON (parziali) senza crashare; un modello sconosciuto nella
  tabella prezzi → costo 0 + warning (così si aggiorna `pricing.json`).
- Zero dipendenze esterne: usare solo stdlib (json, glob, datetime, argparse). Per un output
  colorato/tabellare opzionale si può aggiungere `rich`.

---

### TL;DR per la prossima sessione
Costruisci `cam.py`: legge `~/.claude/projects/**/*.jsonl`, per ogni sessione
calcola durata (min/max timestamp), conta i messaggi per tipo e somma il costo per riga
assistant con la formula §2 (attenzione a cache read/write 5m/1h) usando `pricing.json`.
Aggiungi `--watch` sulla sessione attiva. OTEL (§4) solo se vuoi una dashboard live continua.
