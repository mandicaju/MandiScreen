# MandiScreen

A tela física dos servidores de casa (o console que aparece no monitor) no navegador, com teclado. Um endereço, http://mandiscreen.lan: o PIN escolhe o servidor (ml-srv-0002 ou o Pi-hole .198).

    celular -> mandiscreen.lan/api/* -> hub do MandiRemote (ml-srv-0002, guarda os PINs)
                                        -> 127.0.0.1:8097 (ml-srv-0002) ou 192.168.15.198:8097 (Pi-hole)

- **Página:** `index.html`, servida pelo Caddy em http://mandiscreen.lan
- **Agente:** `agent/mandiscreen.py` (Python 3.7+, só biblioteca padrão), na porta 8097, sem PIN: aceita só o hub. No ml-srv-0002 escuta em `127.0.0.1` (o firewall do hub barra outros usuários e containers); em outro servidor, `--bind <ip> --allow 192.168.15.199`.
  - Lê a tela por `/dev/vcsa` e `/dev/vcsu` (sem `vcsu`, kernel antes do 5.0 como o do Pi-hole, os caracteres vêm do `vcsa`; acentos podem sair trocados).
  - Digita por um teclado virtual em `/dev/uinput`, porque o kernel bloqueia injetar direto no tty (`dev.tty.legacy_tiocsti = 0`).
- **PIN:** no hub do MandiRemote (aba Acessos do mandilabs.lan, ou `hub.py add-screen <nome> <endereço>`). `MANDISCREEN_PIN` no `.env` só vale para instalações antigas.

## Servidor

    ~/apps/mandiscreen/              clone do git (o cron roda o agente daqui)
    ~/apps/mandiscreen-deploy/       deploy.sh, README.txt, .env
    /srv/mandiscreen/                pagina que o Caddy serve

Permissões: `mandicaju` nos grupos `tty` (ler a tela) e `uinput` (digitar), com a regra udev
`KERNEL=="uinput", GROUP="uinput", MODE="0660"` em `/etc/udev/rules.d/99-uinput.rules`.

## Pi-hole (.198)

Clone em `~/apps/mandiscreen`, crontab do mandicaju:

    * * * * * /usr/bin/python3 /home/mandicaju/apps/mandiscreen/agent/mandiscreen.py --bind 192.168.15.198 --allow 192.168.15.199 >/dev/null 2>&1

Mesmas permissões (grupos `tty` e `uinput`, regra udev).

Autoteste do agente: `python3 agent/mandiscreen.py test`
