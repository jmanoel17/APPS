import argparse
import os
import subprocess
import sys
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


def run_app(device, package, label, cycles, runtime, mode):
    env = os.environ.copy()
    env["SLN_APP_PACKAGE"] = package
    env["SLN_APP_LABEL"] = label
    env["SLN_TEST_MAX_CYCLES"] = str(cycles)
    env["SLN_TEST_MAX_RUNTIME_S"] = str(runtime)

    os.makedirs(LOGS, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(LOGS, "apps_{}_{}.log".format(package, stamp))

    cmd = [sys.executable, "-u", BOT, device, str(mode)]
    print("[{}] iniciando bot | ciclos={} runtime={}s | log={}".format(
        label, cycles, runtime, log_path
    ))
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

    try:
        with open(log_path, "w", encoding="utf-8") as fh:
            for line in proc.stdout:
                line = line.rstrip("\r\n")
                print("[{}] {}".format(label, line))
                fh.write(line + "\n")
        proc.wait()
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()

    ok = proc.returncode == 0
    print("[{}] bot encerrado rc={}".format(label, proc.returncode))
    return ok


def main():
    parser = argparse.ArgumentParser(
        description="Roda o bot completo (SLN V2) por app, com ciclo, no device."
    )
    parser.add_argument("apps", nargs="*", help="indices/pacotes, ou 'all'")
    parser.add_argument("--device", default=DEFAULT_DEVICE)
    parser.add_argument("--cycles", type=int, default=1, help="ciclos por app")
    parser.add_argument("--runtime", type=int, default=180, help="limite de segundos por app")
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

    print("device: {} | apps: {} | ciclos/app: {} | runtime/app: {}s".format(
        args.device, len(targets), args.cycles, args.runtime
    ))
    results = []
    for idx, (package, label) in enumerate(targets):
        force_stop(args.device, package)
        time.sleep(1)
        ok = run_app(args.device, package, label, args.cycles, args.runtime, args.mode)
        results.append((label, package, ok))
        force_stop(args.device, package)
        if idx < len(targets) - 1 and args.delay > 0:
            time.sleep(args.delay)

    print("\n===== RESUMO =====")
    for label, package, ok in results:
        print("{:<22} {:<28} {}".format(label, package, "OK" if ok else "FALHA"))
    failures = sum(1 for _, _, ok in results if not ok)
    return 0 if failures == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
