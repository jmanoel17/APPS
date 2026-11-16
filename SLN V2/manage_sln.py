# Gerenciador SLN V2 - inicia/para bot e IA sem derrubar a IA junto.
# Uso:
#   python manage_sln.py start_ia
#   python manage_sln.py start_bot <device>
#   python manage_sln.py stop_bot
#   python manage_sln.py status
import os, sys, subprocess, time

BASE = os.path.dirname(os.path.abspath(__file__))
PY = r"C:\Users\joao_\AppData\Local\Programs\Python\Python313\python.exe"
IA_NAME = "aiMain_sln_v2.py"
BOT_NAME = "main_sln_v2.py"

def procs():
    """Lista (pid, cmdline) de processos python."""
    out, _ = subprocess.Popen(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | ForEach-Object { $_.ProcessId.ToString() + '::' + $_.CommandLine }"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
    ).communicate()
    res = []
    for line in (out or "").splitlines():
        if "::" in line:
            pid, cmd = line.split("::", 1)
            res.append((pid.strip(), cmd))
    return res

def is_script(procs_list, name):
    """True se algum procs contem o script NOME exato (arquivo final)."""
    for _, cmd in procs_list:
        if cmd and cmd.strip().endswith('"' + name + '"') and ("\\" + name) in cmd:
            return True
    return False

def start_ia():
    if is_script(procs(), IA_NAME):
        print("[IA] ja rodando.")
        return
    subprocess.Popen([PY, os.path.join(BASE, IA_NAME)], cwd=BASE,
                     stdout=open(os.path.join(BASE, "ia_server.log"), "a"),
                     stderr=open(os.path.join(BASE, "ia_server_err.log"), "a"),
                     creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    print("[IA] iniciando...")
    time.sleep(11)

def start_bot(device):
    if is_script(procs(), BOT_NAME):
        print("[BOT] ja rodando.")
        return
    log = os.path.join(BASE, "logs", "bot_" + device.replace(":", "_").replace(".", "_") + ".log")
    os.makedirs(os.path.dirname(log), exist_ok=True)
    p = subprocess.Popen([PY, os.path.join(BASE, BOT_NAME), device, "0"], cwd=BASE,
                         stdout=open(log, "a", encoding="utf-8"),
                         stderr=subprocess.STDOUT,
                         creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    print(f"[BOT] {device} PID {p.pid} -> {log}")

def stop_bot():
    killed = False
    for pid, cmd in procs():
        if cmd and ("\\" + BOT_NAME) in cmd:
            subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True)
            print(f"[BOT] parado PID {pid}")
            killed = True
    if not killed:
        print("[BOT] nenhum rodando.")

def status():
    ia = [p for p, c in procs() if c and ("\\" + IA_NAME) in c]
    bot = [p for p, c in procs() if c and ("\\" + BOT_NAME) in c]
    print("IA : " + ("rodando " + ",".join(ia) if ia else "PARADO"))
    print("BOT: " + ("rodando " + ",".join(bot) if bot else "PARADO"))

if __name__ == "__main__":
    c = sys.argv[1] if len(sys.argv) > 1 else "status"
    if c == "start_ia":
        start_ia()
    elif c == "start_bot":
        start_bot(sys.argv[2])
    elif c == "stop_bot":
        stop_bot()
    elif c == "status":
        status()
    else:
        print(__doc__)