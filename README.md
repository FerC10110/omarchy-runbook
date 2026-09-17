# Runbook

An [Omarchy](https://omarchy.org) bar plugin that keeps the commands you run
now and then, each with a short help text, and runs them from the bar in an
embedded terminal.

- Paste the full command line, `sudo` and all. It runs exactly as written.
- Select a script to read its help; press the small ▶ to run it. Nothing else
  runs anything, so a stray click on a name is harmless.
- Each running script has its own terminal. Type a line (a password, an
  answer) and press Enter to send it. Stop interrupts the command and keeps
  its output; Close discards the terminal.
- Scripts keep running while the panel is closed and survive a shell restart:
  every terminal is a session of a private tmux server (`tmux -L runbook`).
- Drag the bottom-right corner to resize the panel; the size is remembered.

## Install

```bash
omarchy plugin add https://github.com/FerC10110/omarchy-runbook
omarchy plugin enable io.github.ferc10110.runbook --section left
```

Requires tmux 3.2 or newer (Omarchy ships 3.7). Move the icon with
`omarchy bar move io.github.ferc10110.runbook --section right`.

## Usage

Click the icon (or `+`) to add a script: a name, the command, and the help
text you will want next time. Your scripts live in
`~/.config/runbook/scripts.json` (mode 600) and can be edited by hand; the
panel reloads the file when it changes.

Keys inside the panel: `j`/`k` or the arrows move the selection, `x` deletes
the selected script (with confirmation), `Esc` closes the panel or cancels the
form, `Tab` switches to the next bar panel. Enter never runs a script.

## Notes

- Commands run in a non-interactive bash: no aliases and nothing from your
  `.bashrc`. `~/.local/bin` is added to `PATH`.
- The terminal shows plain text (no colours) and sends whole lines, so
  full-screen programs such as `htop` or `vim` are not usable inside it.
- The password you type for `sudo` goes straight to the terminal and is never
  stored.
- To reach a session from a real terminal: `tmux -L runbook attach -t <id>`
  (the id is in `scripts.json`).

## Development

```bash
python3 -m unittest discover -s tests -v   # unit tests plus tmux end-to-end tests
omarchy plugin validate .
omarchy-restart-shell                       # QML changes need a shell restart
```

## License

MIT
