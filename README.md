# MandiScreen

A tela física do servidor de casa (ml-srv-0002), o console que aparece no monitor, no navegador, com teclado.

- **Página:** `index.html`, servida pelo Caddy em http://mandiscreen.lan
- **Agente:** `agent/mandiscreen.py` (Python, só biblioteca padrão), em `127.0.0.1:8097`. O Caddy repassa `/api/*` para ele.
  - Lê a tela por `/dev/vcsa` e `/dev/vcsu`.
  - Digita por um teclado virtual em `/dev/uinput`, porque o kernel bloqueia injetar direto no tty (`dev.tty.legacy_tiocsti = 0`).
- **PIN:** `MANDISCREEN_PIN` em `~/apps/mandiscreen-deploy/.env`.

## Servidor

    ~/apps/mandiscreen/              clone do git (o cron roda o agente daqui)
    ~/apps/mandiscreen-deploy/       deploy.sh, README.txt, .env
    /srv/mandiscreen/                pagina que o Caddy serve

Permissões: `mandicaju` nos grupos `tty` (ler a tela) e `uinput` (digitar), com a regra udev
`KERNEL=="uinput", GROUP="uinput", MODE="0660"` em `/etc/udev/rules.d/99-uinput.rules`.

Autoteste do agente: `python3 agent/mandiscreen.py test`
