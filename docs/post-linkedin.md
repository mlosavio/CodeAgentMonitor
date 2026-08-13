# Post LinkedIn — lancio

> Copia il testo qui sotto. Allega `docs/CodeAgentMonitor.png`.

---

In quattro mesi di Claude Code ho pagato **488 € di abbonamento**.
A listino API lo stesso consumo sarebbe costato **2.862 $**.

Ma il numero che non mi aspettavo è un altro: **il 70% di quel valore non sono
risposte generate. Sono riletture della cache.**

Il contesto di una conversazione viene rispedito al modello a ogni messaggio.
Su una sessione lunga sono miliardi di token: il 98% di tutto quello che passa.
Detto altrimenti: il costo di una sessione non lo fa quanto scrive l'assistente,
lo fa **quanto contesto si trascina dietro**.

Non lo sapevo perché non lo misuravo. Così ho scritto uno strumento per farlo, e
oggi lo rendo pubblico: **CodeAgentMonitor**.

Legge i transcript che Claude Code salva già da solo sul tuo computer e risponde
a tre domande che è facilissimo confondere:

→ **quanto ho consumato** — token, messaggi, tempo di lavoro effettivo, e quanta
parte dei limiti del piano ho bruciato
→ **quanto varrebbe a listino API** — che con un abbonamento non paghi mai
→ **quanto ho speso davvero** — la quota mensile, e in quali mesi l'ho pagata a vuoto

C'è un'interfaccia grafica, un cruscotto live nel terminale, un segmento nella
statusline di Claude Code, e la possibilità di rileggere ed esportare in Markdown
le conversazioni passate — perché il valore di una sessione di sei ore è anche il
percorso che ci hai fatto dentro.

**Zero dipendenze**: solo la libreria standard di Python. Si clona e parte.

Due cose imparate scrivendolo, che regalo a chi vuole fare di meglio:

1. Claude Code scrive **una riga per blocco di contenuto** durante lo streaming,
tutte con la stessa `usage`. Sommarle raddoppia il costo: 7.155 righe erano
3.270 messaggi veri.
2. Quelle righe **non sono contigue**: interi segmenti di storia vengono riemessi
migliaia di righe dopo. L'ottimizzazione ovvia gonfia il conto dell'11%.

**Ora la parte che mi interessa di più.**

L'ho scritto per me, su Windows, in italiano, per Claude Code. Sono quattro limiti,
e mi piacerebbe che diventassero quattro direzioni:

🔹 **Multi-agente** — oggi conta i subagent, ma un vero cruscotto per sistemi
multi-agente è un'altra cosa: chi ha delegato a chi, quanto costa un ramo
dell'albero, dove si spreca.
🔹 **Non solo Claude** — la struttura è già generica: token, cache, modelli,
prezzi. Serve un lettore per altri assistenti e diventa uno strumento neutro.
🔹 **Multilingua** — README e interfaccia sono in italiano. L'inglese è il primo
passo per renderlo utile fuori di qui.
🔹 **macOS e Linux** — il codice usa solo percorsi portabili, ma non è mai stato
provato altrove.

Licenza MIT, tutto gratuito, issue e pull request benvenute.

👉 github.com/mlosavio/CodeAgentMonitor

Se lo provi e trovi un numero che non torna, aprimi una issue: i due errori qui
sopra li ho trovati esattamente così, guardando un totale e dicendo "non mi torna".

#ClaudeCode #OpenSource #AI #DeveloperTools #Python #LLM #FinOps
