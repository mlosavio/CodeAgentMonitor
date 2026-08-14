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
