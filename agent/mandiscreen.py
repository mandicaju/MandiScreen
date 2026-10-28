#!/usr/bin/env python3
"""MandiScreen: a tela fisica de um servidor (o console que aparece no monitor) no navegador.
O celular abre mandiscreen.lan e digita um PIN; o hub do MandiRemote no ml-srv-0002 (que guarda
os PINs) descobre qual servidor e repassa para o agente dele, na porta 8097. Mantido vivo pelo
crontab do mandicaju (a cada minuto; uma segunda copia so falha ao bindar :8097).

  mandiscreen.py                                  -> no ml-srv-0002: 127.0.0.1:8097
  mandiscreen.py --bind 192.168.15.198 --allow 192.168.15.199
                                                  -> em outro servidor: so o ml-srv-0002 entra

  GET  /api/screen  -> {tty, rows, cols, cx, cy, lines: [[[texto, attr], ...], ...]}
  POST /api/keys    -> corpo em texto, um comando por linha:
                         t <texto>            digita (ASCII; o console usa layout US)
                         k <tecla>[+<tecla>]  ex.: k enter, k ctrl+c, k alt+f2 (troca de terminal)
Sem PIN aqui: so o hub chega (127.0.0.1 com o firewall do ml-srv-0002, ou --allow). Se ainda houver
MANDISCREEN_PIN em ~/apps/mandiscreen-deploy/.env (instalacao antiga), ele continua sendo exigido.

Le o terminal que esta no monitor agora: /dev/vcsa (cores e cursor) e /dev/vcsu (caracteres
em Unicode; kernel 5.0+. Sem ele, como no rk322x do Pi-hole, os caracteres vem do vcsa). Digita por um teclado virtual (/dev/uinput), que o console trata como um teclado
USB: este kernel bloqueia injetar direto no tty (dev.tty.legacy_tiocsti = 0).
Permissoes: mandicaju nos grupos tty (ler a tela) e uinput (regra udev, ver README.txt do deploy).

  python3 mandiscreen.py test   -> autoteste
"""
import array, fcntl, os, struct, sys, time
from http.server import BaseHTTPRequestHandler, HTTPServer
import json

PORT = 8097
ENV = os.path.expanduser("~/apps/mandiscreen-deploy/.env")

# ---------- Tela ----------

def parse_screen(vcsa, vcsu):
    """vcsa: 4 bytes (linhas, colunas, x, y do cursor) + pares (caractere, atributo).
    vcsu: um inteiro de 32 bits por celula com o caractere em Unicode, ou None sem /dev/vcsu.
    Junta celulas vizinhas de mesmo atributo em trechos, para o JSON ficar pequeno."""
    rows, cols, cx, cy = vcsa[0], vcsa[1], vcsa[2], vcsa[3]
    attrs = vcsa[5::2]
    # ponytail: sem vcsu, o byte do vcsa e a posicao na fonte do console; ASCII bate, acento pode sair trocado
    chars = array.array("I", vcsu) if vcsu is not None else vcsa[4::2]
    lines = []
    for r in range(rows):
        runs = []
        for c in range(cols):
            i = r * cols + c
            ch = chr(chars[i]) if 32 <= chars[i] < 0x110000 else " "
            # ponytail: com fonte de 512 caracteres o bit 3 do atributo e da fonte, nao da cor; o console do servidor usa 256
            a = attrs[i]
            if runs and runs[-1][1] == a:
                runs[-1][0] += ch
            else:
                runs.append([ch, a])
        lines.append(runs)
    return {"rows": rows, "cols": cols, "cx": cx, "cy": cy, "lines": lines}


def read_screen():
    with open("/dev/vcsa", "rb") as f:
        vcsa = f.read()
    try:
        with open("/dev/vcsu", "rb") as f:
            vcsu = f.read()
    except FileNotFoundError:
        vcsu = None
    with open("/sys/class/tty/tty0/active") as f:
        tty = f.read().strip()
    return {"tty": tty, **parse_screen(vcsa, vcsu)}

# ---------- Teclado virtual ----------

# Codigos do kernel (linux/input-event-codes.h)
KEY = {"esc": 1, "backspace": 14, "tab": 15, "enter": 28, "ctrl": 29, "shift": 42, "alt": 56, "space": 57,
       "home": 102, "up": 103, "pgup": 104, "left": 105, "right": 106, "end": 107, "down": 108, "pgdn": 109,
       "insert": 110, "delete": 111}
KEY.update({f"f{n}": 58 + n for n in range(1, 11)})
KEY.update({"f11": 87, "f12": 88})
# Layout US: caractere -> (codigo, precisa de shift)
CHARS = {}
for row, first in (("1234567890", 2), ("qwertyuiop", 16), ("asdfghjkl", 30), ("zxcvbnm", 44)):
    for i, ch in enumerate(row):
        CHARS[ch] = (first + i, False)
for ch in "qwertyuiopasdfghjklzxcvbnm":
    CHARS[ch.upper()] = (CHARS[ch][0], True)
for plain, shifted, code in (("-", "_", 12), ("=", "+", 13), ("[", "{", 26), ("]", "}", 27), (";", ":", 39),
                             ("'", '"', 40), ("`", "~", 41), ("\\", "|", 43), (",", "<", 51), (".", ">", 52),
                             ("/", "?", 53)):
    CHARS[plain] = (code, False)
    CHARS[shifted] = (code, True)
for ch, code in zip("!@#$%^&*()", range(2, 12)):
    CHARS[ch] = (code, True)
CHARS.update({" ": (57, False), "\t": (15, False), "\n": (28, False)})

EV_SYN, EV_KEY = 0, 1
UI_SET_EVBIT, UI_SET_KEYBIT, UI_DEV_CREATE = 0x40045564, 0x40045565, 0x5501


def key_codes(spec):
    """'ctrl+c' -> [29, 46]; 'enter' -> [28]; 'A' -> [42, 30]."""
    codes = []
    for name in spec.split("+"):
        if name.lower() in KEY:
            codes.append(KEY[name.lower()])
        elif len(name) == 1 and name in CHARS:
            code, shift = CHARS[name]
            codes += [KEY["shift"], code] if shift else [code]
        else:
            raise ValueError(f"tecla desconhecida: {name}")
    return codes


class Keyboard:
    def __init__(self):
        self.fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_KEY)
        for code in range(1, 120):
            fcntl.ioctl(self.fd, UI_SET_KEYBIT, code)
        # uinput_user_dev: nome, id (barramento USB, fabricante, produto, versao), ff_effects_max, 4 x 64 eixos
        os.write(self.fd, struct.pack("80sHHHHi", b"MandiScreen", 0x03, 0x1, 0x1, 1, 0) + bytes(4 * 64 * 4))
        fcntl.ioctl(self.fd, UI_DEV_CREATE)
        time.sleep(0.5)  # o kernel registra o teclado novo; teclas antes disso se perdem

    def _ev(self, type_, code, value):
        os.write(self.fd, struct.pack("llHHi", 0, 0, type_, code, value))

    def press(self, codes):
        for c in codes:
            self._ev(EV_KEY, c, 1)
        self._ev(EV_SYN, 0, 0)
        for c in reversed(codes):
            self._ev(EV_KEY, c, 0)
        self._ev(EV_SYN, 0, 0)
        time.sleep(0.003)


kbd = None  # criado no primeiro uso: ver a tela funciona mesmo sem permissao no /dev/uinput


def run(line):
    global kbd
    if not line:
        return
    cmd, arg = line[0], line[2:]
    if cmd == "t":
        # direto pelo mapa (o "+" aqui e caractere, nao separador); fora do ASCII o console US nao tem tecla
        seq = [[KEY["shift"], CHARS[ch][0]] if CHARS[ch][1] else [CHARS[ch][0]] for ch in arg if ch in CHARS]
    elif cmd == "k":
        seq = [key_codes(arg)]
    else:
        raise ValueError(f"comando desconhecido: {cmd}")
    if kbd is None:
        kbd = Keyboard()
    for codes in seq:
        kbd.press(codes)

# ---------- HTTP ----------

def load_pin():
    try:
        with open(ENV) as f:
            for line in f:
                k, _, v = line.strip().partition("=")
                if k == "MANDISCREEN_PIN" and v:
                    return v
    except FileNotFoundError:
        pass
    return None


PIN = None                  # so instalacoes antigas; o PIN agora fica no hub
ALLOW = {"127.0.0.1"}       # quem pode pedir (o hub)
fails, locked_until = 0, 0.0


class H(BaseHTTPRequestHandler):
    def reply(self, status, body=b"", ctype="text/plain; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authorized(self):
        if self.client_address[0] not in ALLOW:
            self.reply(403)
            return False
        if PIN is None:
            return True
        # ponytail: trava global de 1 min depois de 10 PINs errados; por IP se um dia sair da rede de casa
        global fails, locked_until
        if time.time() < locked_until:
            self.reply(429, "Muitas tentativas. Espere um minuto.".encode())
            return False
        if self.headers.get("X-Token") == PIN:
            fails = 0
            return True
        fails += 1
        if fails >= 10:
            fails, locked_until = 0, time.time() + 60
        self.reply(401, "PIN incorreto".encode())
        return False

    def do_GET(self):
        if self.path.split("?")[0] != "/api/screen":
            return self.reply(404)
        if not self.authorized():
            return
        try:
            self.reply(200, json.dumps(read_screen(), ensure_ascii=False).encode(), "application/json")
        except OSError as e:
            self.reply(500, f"sem acesso a tela: {e}".encode())

    def do_POST(self):
        if self.path.split("?")[0] != "/api/keys":
            return self.reply(404)
        if not self.authorized():
            return
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
        try:
            for line in body.split("\n"):
                run(line)
            self.reply(204)
        except (OSError, ValueError) as e:
            self.reply(400 if isinstance(e, ValueError) else 500, str(e).encode())

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    if sys.argv[1:] == ["test"]:
        assert key_codes("enter") == [28] and key_codes("ctrl+c") == [29, 46] and key_codes("alt+f2") == [56, 60]
        assert key_codes("A") == [42, 30] and key_codes("?") == [42, 53] and key_codes("f12") == [88]
        pressed = []
        kbd = type("K", (), {"press": lambda self, c: pressed.append(c)})()
        run("t a+B")
        assert pressed == [[30], [42, 13], [42, 48]], pressed
        for ch in map(chr, range(32, 127)):
            assert ch in CHARS, repr(ch)
        try:
            key_codes("ç")
            raise AssertionError("ç")
        except ValueError:
            pass
        # tela 2x3: "ab " vermelho/vermelho/padrao, "xyz" padrao
        vcsa = bytes([2, 3, 1, 0]) + bytes([97, 0x04, 98, 0x04, 32, 0x07, 120, 0x07, 121, 0x07, 122, 0x07])
        vcsu = array.array("I", [97, 98, 32, 120, 121, 122]).tobytes()
        s = parse_screen(vcsa, vcsu)
        assert (s["rows"], s["cols"], s["cx"], s["cy"]) == (2, 3, 1, 0)
        assert s["lines"] == [[["ab", 4], [" ", 7]], [["xyz", 7]]], s["lines"]
        assert parse_screen(vcsa, None)["lines"] == s["lines"], "sem vcsu, le os caracteres do vcsa"
        print("ok")
    else:
        import argparse
        ap = argparse.ArgumentParser()
        ap.add_argument("--bind", default="127.0.0.1")
        ap.add_argument("--allow", action="append", default=[], help="IP que pode pedir (o servidor do hub)")
        a = ap.parse_args()
        ALLOW |= set(a.allow)
        PIN = load_pin()
        HTTPServer((a.bind, PORT), H).serve_forever()
