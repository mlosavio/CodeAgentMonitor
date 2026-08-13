#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Installa il segmento di CodeAgentMonitor nella statusline di Claude Code.

    python install_statusline.py            installa
    python install_statusline.py --wrap     installa conservando la statusline attuale
    python install_statusline.py --remove   disinstalla

Cosa fa, in concreto:
  1. copia `statusline/cam-statusline.js` in ~/.claude/hooks/
  2. scrive ~/.claude/cam-statusline.json con il percorso di questo progetto,
     così lo script sa dove trovare config.json
  3. imposta `statusLine` in ~/.claude/settings.json

Con `--wrap`, la statusline che avevi già non viene sostituita: viene eseguita
come processo figlio e il segmento le viene appeso in fondo. Il suo comando
finisce in `statusline.wrap_command` dentro config.json.

Prima di toccare settings.json ne fa una copia in settings.json.bak.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CLAUDE_DIR = os.path.join(os.path.expanduser("~"), ".claude")
HOOKS_DIR = os.path.join(CLAUDE_DIR, "hooks")
SETTINGS = os.path.join(CLAUDE_DIR, "settings.json")
POINTER = os.path.join(CLAUDE_DIR, "cam-statusline.json")
SOURCE = os.path.join(HERE, "statusline", "cam-statusline.js")
TARGET = os.path.join(HOOKS_DIR, "cam-statusline.js")
# Come si chiamavano prima che il progetto diventasse CAM. Vanno tolti quando si
# reinstalla, altrimenti restano due segmenti installati e uno non lo aggiorna
# piu' nessuno.
PRECEDENTI = (os.path.join(HOOKS_DIR, "cm-statusline.js"),
              os.path.join(CLAUDE_DIR, "cm-statusline.json"))


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {} if default is None else default


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def command_string() -> str:
    # slash in avanti: Claude Code riscrive i backslash nei comandi degli hook
    return 'node "%s"' % TARGET.replace("\\", "/")


def install(wrap: bool) -> int:
    if not os.path.isfile(SOURCE):
        print(f"non trovo {SOURCE}", file=sys.stderr)
        return 2
    if not os.path.isdir(CLAUDE_DIR):
        print(f"non trovo {CLAUDE_DIR}: Claude Code è installato?", file=sys.stderr)
        return 2

    os.makedirs(HOOKS_DIR, exist_ok=True)
    shutil.copy2(SOURCE, TARGET)
    print(f"copiato   {TARGET}")

    write_json(POINTER, {"project_dir": HERE.replace("\\", "/")})
    print(f"scritto   {POINTER}")

    settings = read_json(SETTINGS)
    previous = (settings.get("statusLine") or {}).get("command")

    if wrap and previous:
        config_path = os.path.join(HERE, "config.json")
        config = read_json(config_path)
        if not config:
            example = os.path.join(HERE, "config.example.json")
            config = read_json(example)
            if not config:
                print("config.json mancante: copia config.example.json", file=sys.stderr)
                return 2
        # il comando precedente diventa il processo figlio da avvolgere
        config.setdefault("statusline", {})["wrap_command"] = split_command(previous)
        write_json(config_path, config)
        print(f"avvolgo   {previous}")

    if os.path.isfile(SETTINGS):
        shutil.copy2(SETTINGS, SETTINGS + ".bak")
        print(f"backup    {SETTINGS}.bak")
    settings["statusLine"] = {"type": "command", "command": command_string()}
    write_json(SETTINGS, settings)
    print(f"impostato statusLine in {SETTINGS}")
    for vecchio in PRECEDENTI:
        if os.path.isfile(vecchio):
            os.remove(vecchio)
            print(f"tolto     {vecchio} (nome precedente)")
    print("\nfatto. Riavvia Claude Code per vedere il segmento.")
    return 0


def split_command(command: str) -> list[str]:
    """Spezza un comando in argomenti, rispettando le virgolette."""
    import shlex
    try:
        return shlex.split(command, posix=False)
    except ValueError:
        return command.split()


def remove() -> int:
    settings = read_json(SETTINGS)
    current = (settings.get("statusLine") or {}).get("command") or ""
    # Anche col nome vecchio: chi l'ha installata prima del rename deve poterla
    # disinstallare, altrimenti resterebbe attaccata senza un modo per toglierla.
    if not any(n in current for n in ("cam-statusline", "cm-statusline")):
        print("la statusline non è la nostra: non tocco niente")
    else:
        config = read_json(os.path.join(HERE, "config.json"))
        wrapped = (config.get("statusline") or {}).get("wrap_command")
        if os.path.isfile(SETTINGS):
            shutil.copy2(SETTINGS, SETTINGS + ".bak")
        if wrapped:
            # rimetto al suo posto la statusline che c'era prima
            settings["statusLine"] = {"type": "command",
                                      "command": " ".join(wrapped)}
            print("ripristinata la statusline precedente")
        else:
            settings.pop("statusLine", None)
            print("rimossa la voce statusLine")
        write_json(SETTINGS, settings)
    for path in (TARGET, POINTER) + PRECEDENTI:
        if os.path.isfile(path):
            os.remove(path)
            print(f"rimosso   {path}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description="Installa il segmento di CodeAgentMonitor nella statusline.")
    p.add_argument("--wrap", action="store_true",
                   help="conserva la statusline attuale eseguendola come figlio")
    p.add_argument("--remove", action="store_true", help="disinstalla")
    args = p.parse_args()
    return remove() if args.remove else install(args.wrap)


if __name__ == "__main__":
    sys.exit(main())
