# Documentazione

Tutto quello che c'è da leggere sta **qui dentro o nel repository**. Niente vive solo altrove:
se un documento è stato pubblicato come pagina su una piattaforma esterna, la sua fonte è in
questa cartella, e la pagina è una copia.

## I documenti

| | Cos'è |
|---|---|
| [../README.md](../README.md) | Il documento principale. Cosa fa, come si installa, come funziona |
| [../README.en.md](../README.en.md) | Lo stesso in inglese |
| [../TODO.md](../TODO.md) | Il backlog: solo quello che manca, una PF per riga |
| [team-in-sede.md](team-in-sede.md) | Attivare CAM in un team **sulla stessa rete aziendale** |
| [team-distribuito.md](team-distribuito.md) | Attivare CAM in un team **sparso geograficamente**, col pannello riservato a un amministratore |
| [post-linkedin.md](post-linkedin.md) | Il testo del post di lancio |

## Le pagine in `pagine/`

Le due guide di attivazione esistono anche come **pagine HTML autonome**, comode da tenere aperte
accanto al terminale mentre si esegue l'installazione: hanno diagrammi, comandi etichettati con la
macchina su cui vanno eseguiti, e un elenco di controllo spuntabile.

| Pagina | Fonte |
|---|---|
| [pagine/team-in-sede.html](pagine/team-in-sede.html) | [team-in-sede.md](team-in-sede.md) |
| [pagine/team-distribuito.html](pagine/team-distribuito.html) | [team-distribuito.md](team-distribuito.md) |

Sono file singoli, senza dipendenze esterne: si aprono con doppio click, funzionano offline e
seguono il tema chiaro/scuro del sistema.

I **diagrammi ci sono in entrambi i formati**, con tecniche diverse: nelle pagine sono SVG scritti
a mano, nel Markdown sono blocchi ```mermaid``` — che GitHub disegna da solo, senza immagini da
tenere allineate al testo. Una figura che è codice si corregge insieme alla riga che spiega, e
non resta indietro come farebbe uno screenshot.

### La regola, perché non divergano

**Il Markdown è la fonte, l'HTML è una resa.** Non sono generate automaticamente — sono scritte a
mano, perché la pagina ha diagrammi e riquadri che il Markdown non ha. Il che vuol dire che
**possono divergere**, ed è il difetto noto di questa sistemazione.

Quindi: quando cambia una procedura, **si modifica prima il `.md`**, e la pagina si allinea
subito dopo. Se non hai tempo di fare entrambe le cose, fai il `.md` e basta: un Markdown giusto
accanto a una pagina vecchia è recuperabile, il contrario no — perché nessuno saprebbe più quale
delle due era quella corretta.

È lo stesso ragionamento per cui i due README non sono la traduzione riga per riga l'uno
dell'altro: due documenti lunghi allineati a mano diventano due documenti che divergono, e il
secondo è sbagliato senza che nessuno se ne accorga.

## Le immagini

`CodeAgentMonitor.png` è la copertina del README. Si ricostruisce dalle finestre reali del
pannello; nomi di progetti e titoli delle conversazioni sono sfocati, i numeri sono veri.

## Le copie pubblicate

Le due guide sono state pubblicate anche come pagine su claude.ai. **Non sono la fonte** — la
fonte è qui — ma l'indirizzo va scritto da qualche parte, altrimenti fra sei mesi non si sa più
quale pagina corrisponda a quale documento, o se ne esista una.

| Documento | Copia pubblicata |
|---|---|
| [team-in-sede.md](team-in-sede.md) | `claude.ai/code/artifact/82532f2c-1b46-45c1-b49e-6fb79310e70d` |
| [team-distribuito.md](team-distribuito.md) | `claude.ai/code/artifact/41a76463-48ca-4204-aa3e-4f74868ff421` |

Sono private finché non vengono condivise esplicitamente. Quando cambia il `.md`, la copia
pubblicata va ripubblicata: vale la stessa regola dell'HTML qui sopra.

## Cosa NON sta qui, e perché

In `interno/` — **escluso da git** — c'è il documento di valutazione da cui è nato il pannello di
team. Resta fuori dal repository perché è materiale aziendale: contiene il ragionamento sulle
licenze, i riferimenti al piano in uso e la ricostruzione di una revisione in cui erano emersi
il nome di un'azienda e quello di una persona terza. Questo repository è **pubblico**, e la
cartella è in `.gitignore` apposta.

Stessa regola per tutto il resto: documenti di altri progetti non entrano qui nemmeno se sono
comodi da avere sottomano. Un repository pubblico non è un archivio personale.
