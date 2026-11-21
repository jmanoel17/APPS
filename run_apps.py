import argparse
import os
import random
import re
import subprocess
import sys
import threading
import time
from datetime import datetime

from auto_reward import APPS, DEFAULT_DEVICE, ask, resolve

ROOT = os.path.dirname(os.path.abspath(__file__))
BOT = os.path.join(ROOT, "SLN V2", "main_sln_v2.py")
LOGS = os.path.join(ROOT, "logs")

CREATION_FLAGS = 0x08000000 if os.name == "nt" else 0

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def adb(device, *args, timeout=30):
    cmd = ["adb", "-s", device] + list(args)
    try:
        out = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=CREATION_FLAGS,
        )
        return out.stdout or ""
    except subprocess.TimeoutExpired:
        return ""


def force_stop(device, package):
    adb(device, "shell", "am force-stop " + package)
    adb(device, "shell", "input keyevent KEYCODE_HOME")


def reset_apps(device, packages):
    print("[RESET] resetando ID de {} app(s) (force-stop + pm clear)...".format(len(packages)))
    for p in packages:
        adb(device, "shell", "am force-stop " + p)
        time.sleep(0.5)
    for p in packages:
        out = adb(device, "shell", "pm clear " + p)
        print("[RESET] {} -> {}".format(p, out.strip()))
        time.sleep(0.8)
    adb(device, "shell", "input keyevent KEYCODE_HOME")
    time.sleep(2)


# Eventos considerados IMPORTANTES (mostrados no console). O ciclo completo de um anúncio
# é AGRUPADO em 1 linha (ver run_app). Todo o resto vai só para o log em arquivo.
_IMPORTANT_TAGS = (
    "[FASTCLOSE][REWARD_GATE] opened",
    "[FASTCLOSE][STORE] expected=",
    "[FASTCLOSE][ENDCARD_X_CLICK]",
    "[FASTCLOSE][SKIP_CONTROL]",
    "[FASTCLOSE][RECOVERY]",
    "[FASTCLOSE][FOUND]",
    "[CONSENT][CAS]",
    "[STORE]",
    "[RESET]",
    "[PENDING]",
    "[TESTE]",
    "[LOG] Aplicativo iniciado",
    "[LOG] Aberto",
    "[LOG] Voltou",
    "[AVISO]",
    "[ERROR]",
    "[ERRO]",
    "Erro ao conectar",
    "Traceback",
    "Exception",
    "BackendError",
    "Limite de",
    "Não conseguiu abrir anúncio",
)

_PAT_FMT = re.compile(r"\[FASTCLOSE\]\[AD_SUMMARY_FORMAT\] format=(\w+).*?network=([\w.]+)")
_PAT_RES = re.compile(
    r"\[FASTCLOSE\]\[AD_SUMMARY\] result=(\w+) elapsed=([\d.]+)s type=(\w+)"
    r".*?store_backs=(\d+)")
_PAT_RWD = re.compile(r"\[FASTCLOSE\]\[REWARD\] before=\S+ after=\S+ delta=([+\-]?\d+)")
_PAT_CMP = re.compile(r"\[QA\]\[CYCLE_COMPLETED\] cycle_id=(\d+) .*?duration_s=([\d.]+)")
_PAT_FAIL = re.compile(r"\[QA\]\[CYCLE_FAILED\] cycle_id=(\d+)")
_PAT_START = re.compile(r"\[QA\]\[CYCLE_START\] cycle_id=(\d+)")
_PAT_ERR = re.compile(r"\berror\b|\berro\b|exception|traceback")

_FRIENDLY = (
    (re.compile(r"\[FASTCLOSE\]\[REWARD_GATE\] opened"), "anúncio liberado para fechar"),
    (re.compile(r"\[FASTCLOSE\]\[SKIP_CONTROL\]"), "fechando/skip do anúncio..."),
    (re.compile(r"\[FASTCLOSE\]\[STORE\] expected="), "Play Store abriu — voltando ao anúncio..."),
    (re.compile(r"\[FASTCLOSE\]\[ENDCARD_X_CLICK\]"), "fechando endcard (X)..."),
    (re.compile(r"\[FASTCLOSE\]\[FOUND\]"), "botão de fechar (X) encontrado"),
    (re.compile(r"\[FASTCLOSE\]\[RECOVERY\]"), "recuperação de fechamento acionada"),
    (re.compile(r"\[FASTCLOSE\]\[STORE_BACK\] result="), "saindo da Play Store..."),
)


def _friendly(line):
    for rx, msg in _FRIENDLY:
        if rx.search(line):
            ts = ""
            m = re.match(r"^\[(\d\d:\d\d:\d\d(?:\.\d+)?)\]", line)
            if m:
                ts = "[" + m.group(1) + "] "
            return ts + msg
    return line


def _is_important(line):
    for tag in _IMPORTANT_TAGS:
        if tag in line:
            return True
    if "[BANNER]" in line:
        return any(k in line for k in ("Clique", "Não conseguiu", "abrir anúncio", "Banner em recarga na MainActivity"))
    return bool(_PAT_ERR.search(line.lower()))


def run_app(device, package, label, cycles, runtime, mode, log_fn=None):
    if runtime is None or runtime <= 0:
        runtime = cycles * 90 + 120
    env = os.environ.copy()
    env["SLN_APP_PACKAGE"] = package
    env["SLN_APP_LABEL"] = label
    env["SLN_TEST_MAX_CYCLES"] = str(cycles)
    env["SLN_TEST_MAX_RUNTIME_S"] = str(runtime)

    os.makedirs(LOGS, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(LOGS, "apps_{}_{}.log".format(package, stamp))

    cmd = [sys.executable, "-u", BOT, device, str(mode)]
    print("[{}] rodando {} anúncio(s)... | detalhe: {}".format(label, cycles, log_path))
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            cwd=ROOT,
            creationflags=CREATION_FLAGS,
        )
    except Exception as exc:
        print("[{}] falha ao iniciar bot: {}".format(label, exc))
        return False

    ctx = {}
    done = {"n": 0}
    last_activity = {"t": time.monotonic()}
    last_report = {"t": time.monotonic()}

    def watchdog():
        while proc.poll() is None:
            idle = time.monotonic() - last_activity["t"]
            if done["n"] == 0 and idle >= 60 and time.monotonic() - last_report["t"] >= 60:
                last_report["t"] = time.monotonic()
                print("[{}] {}s sem fechar anúncio — aguardando carregar...".format(label, int(idle)))
            time.sleep(5)

    threading.Thread(target=watchdog, daemon=True).start()

    def flush_cycle():
        if not ctx:
            return
        n = ctx.get("id", "?")
        fmt = ctx.get("format", "-")
        dur = ctx.get("dur", "-")
        res = ctx.get("result", "fechado")
        store = ctx.get("store", 0)
        coins = ctx.get("coins")
        extra = ""
        if coins is not None:
            extra = " | moedas+{}".format(coins)
        if store:
            extra += " | store={}".format(store)
        print("[{}] ciclo {}/{}: {} | {} | {}s{}".format(label, n, cycles, res, fmt, dur, extra))
        done["n"] += 1
        last_activity["t"] = time.monotonic()
        ctx.clear()

    try:
        with open(log_path, "w", encoding="utf-8") as fh:
            for line in proc.stdout:
                line = line.rstrip("\r\n")
                fh.write(line + "\n")
                folded = False
                m = _PAT_START.search(line)
                if m:
                    ctx["id"] = m.group(1)
                    print("[{}] ciclo {}/{}: assistindo anúncio...".format(label, m.group(1), cycles))
                    folded = True
                m = _PAT_FMT.search(line)
                if m:
                    ctx["format"] = m.group(1)
                    print("[{}] anúncio carregado: {} ({})".format(label, m.group(1), m.group(2)))
                    folded = True
                m = _PAT_RES.search(line)
                if m:
                    ctx["result"] = m.group(1)
                    ctx["dur"] = m.group(2)
                    ctx["store"] = int(m.group(4))
                    folded = True
                m = _PAT_RWD.search(line)
                if m:
                    ctx["coins"] = int(m.group(1))
                    folded = True
                m = _PAT_FAIL.search(line)
                if m:
                    ctx["id"] = m.group(1)
                    ctx["result"] = "FALHOU"
                    flush_cycle()
                    continue
                m = _PAT_CMP.search(line)
                if m:
                    ctx["id"] = m.group(1)
                    ctx["dur"] = m.group(2)
                    flush_cycle()
                    continue
                if not folded and _is_important(line):
                    print("[{}] {}".format(label, _friendly(line)))
                    last_activity["t"] = time.monotonic()
        proc.wait()
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()

    ok = proc.returncode == 0
    if done["n"] < cycles:
        print("[{}] atenção: fechou só {}/{} anúncio(s) — tempo esgotado ou sem inventário de anúncio".format(
            label, done["n"], cycles))
    print("[{}] concluído ({}/{})".format(label, done["n"], cycles))
    return ok


def main():
    parser = argparse.ArgumentParser(
        description="Roda o bot completo (SLN V2) por app. Cada app tem uma QUOTA de anúncios "
                    "sorteada no início do grande ciclo, consumida em passos aleatórios até zerar; "
                    "depois faz reset de ID e sorteia novas quotas."
    )
    parser.add_argument("apps", nargs="*", help="indices/pacotes, ou 'all'")
    parser.add_argument("--device", default=DEFAULT_DEVICE)
    parser.add_argument("--cycles-min", type=int, default=5,
                        help="quota MÍNIMA de anúncios por app em cada grande ciclo")
    parser.add_argument("--cycles-max", type=int, default=15,
                        help="quota MÁXIMA de anúncios por app em cada grande ciclo")
    parser.add_argument("--chunk-min", type=int, default=1,
                        help="anúncios MÍNIMOS por passo (sorteado a cada passo)")
    parser.add_argument("--chunk-max", type=int, default=3,
                        help="anúncios MÁXIMOS por passo (sorteado a cada passo)")
    parser.add_argument("--runtime", type=int, default=None,
                        help="limite de segundos por passo (default: proporcional aos anúncios)")
    parser.add_argument("--rounds", type=int, default=0,
                        help="quantos GRANDES ciclos (0 = infinito; reset de ID entre eles)")
    parser.add_argument("--mode", type=int, default=0)
    parser.add_argument("--delay", type=float, default=3.0, help="pausa entre apps")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()

    if args.list:
        for key, (package, label) in APPS.items():
            print("{}  {:<22} {}".format(key, package, label))
        return 0

    selectors = args.apps or ask()
    targets = resolve(selectors)
    if not targets:
        print("nenhum app selecionado")
        return 1

    if args.cycles_max < args.cycles_min or args.chunk_max < args.chunk_min:
        print("ERRO: max < min (quota ou passo)")
        return 1

    print("=" * 70)
    print(" DISPOSITIVO : {}".format(args.device))
    print(" APPS        : {} ({})".format(len(targets), ", ".join(lbl for _, lbl in targets)))
    print(" QUOTA/APP   : {} a {} anúncios (sorteada por grande ciclo)".format(
        args.cycles_min, args.cycles_max))
    print(" POR PASSO   : {} a {} anúncios (sorteado a cada passo)".format(
        args.chunk_min, args.chunk_max))
    print(" GRANDES     : {}".format("INFINITOS (reset de ID entre eles)" if args.rounds == 0 else args.rounds))
    print(" RUNTIME     : {} (auto quando 0)".format(args.runtime if args.runtime else "auto"))
    print("=" * 70)
    packages_all = [pkg for _, pkg in targets]
    results = []
    grand = 0
    while True:
        grand += 1
        if args.rounds > 0 and grand > args.rounds:
            break
        total_grand = "?" if args.rounds == 0 else str(args.rounds)
        print("\n" + "=" * 70)
        print(" GRANDE CICLO {}/{} - sorteando quotas de anúncios por app".format(grand, total_grand))
        print("=" * 70)
        quotas = {pkg: random.randint(args.cycles_min, args.cycles_max) for pkg, _ in targets}
        for package, label in targets:
            print("   {:<22} quota = {} anúncios".format(label, quotas[package]))
        remaining = dict(quotas)
        pass_no = 0
        while any(v > 0 for v in remaining.values()):
            pass_no += 1
            print("\n" + "-" * 70)
            print(" PASSO {} - assistindo 1..{} anúncios por app (aleatório)".format(pass_no, args.chunk_max))
            print("-" * 70)
            for idx, (package, label) in enumerate(targets):
                if remaining[package] <= 0:
                    print("   [{}] quota já completa - pulando".format(label))
                    continue
                chunk = random.randint(args.chunk_min, args.chunk_max)
                chunk = min(chunk, remaining[package])
                force_stop(args.device, package)
                time.sleep(1)
                print("\n   ### {}/{} - {} ({}) | assistir {} anúncio(s) | restam {} de {} ###".format(
                    idx + 1, len(targets), label, package, chunk, remaining[package] - chunk,
                    quotas[package]))
                ok = run_app(args.device, package, label, chunk, args.runtime, args.mode)
                results.append((label, package, chunk, ok))
                remaining[package] -= chunk
                force_stop(args.device, package)
                if idx < len(targets) - 1 and args.delay > 0:
                    time.sleep(args.delay)
            print("\n   [restantes] {}".format(
                " | ".join("{}={}".format(lbl, remaining[pkg]) for pkg, lbl in targets)))
        # Quotas de todos os apps zeradas: reset de ID + novo grande ciclo com novas quotas.
        if args.rounds == 0 or grand < args.rounds:
            reset_apps(args.device, packages_all)
            if args.delay > 0:
                time.sleep(args.delay)

    print("\n" + "=" * 70)
    print(" RESUMO FINAL")
    print("=" * 70)
    watched = {}
    ok_map = {}
    for label, package, chunk, ok in results:
        watched[package] = watched.get(package, 0) + chunk
        ok_map[package] = ok_map.get(package, True) and ok
    print("{:<24} {:<28} {:>14}  {}".format("APP", "PACOTE", "ANUNCIOS ASSISTIDOS", "STATUS"))
    for package, label in targets:
        print("{:<24} {:<28} {:>14}  {}".format(label, package, watched.get(package, 0),
                                                "OK" if ok_map.get(package, True) else "FALHA"))
    print("=" * 70)
    failures = sum(1 for _, _, _, ok in results if not ok)
    return 0 if failures == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
