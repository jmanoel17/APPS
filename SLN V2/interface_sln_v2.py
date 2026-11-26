#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
interface_sln_v2.py - Interface do SLN V2 (Drama Classic)

Mesma estrutura da interface_optimized_v2.py com MELHORIAS:
  - Basa em Python 3.13 + uiautomator2/psutil
  - Headless por padrão (CREATE_NO_WINDOW) + logs em pasta logs/
  - Semáforo de arranque (max 10 simultâneos)
  - Monitor adaptativo baseado no nº de devices
  - Pausar/Parar/Per-Ada (adotar bots via psutil)
  - Status Bar com totais
  - Guard contra duplicar start
  - Path do script configuravel (SCRIPTS_MAP)
  - Re-adocao de processos externos
  - "UUID" da UI hash p/ evitar redraw desnecessario
  - Suporte a groups.json, paused.json, ip_ranges.json (scanner)
"""
import os
import sys
import json
import time
import random
import ctypes
import hashlib
import threading
import uuid
import subprocess
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk, scrolledtext, Entry
import connect
import config_sln_v2 as CONFIG
import sln_proc
try:
    from dpn.regions import region_label, country_label
except Exception:
    def region_label(code): return code or '?'
    def country_label(code): return code or '?'

# ==================== CONFIG ====================
SCRIPT_DIR = os.getcwd()
if not os.path.exists(os.path.join(SCRIPT_DIR, "main_sln_v2.py")):
    sln_dir = os.path.join(SCRIPT_DIR, "SLN V2")
    parent = os.path.dirname(SCRIPT_DIR)
    if os.path.exists(os.path.join(sln_dir, "main_sln_v2.py")):
        SCRIPT_DIR = sln_dir
    elif os.path.exists(os.path.join(parent, "main_sln_v2.py")):
        SCRIPT_DIR = parent

SCRIPTS_MAP = {
    "SLN V2 (main_sln_v2.py)": "main_sln_v2.py",
}
DEFAULT_SCRIPT_NAME = "SLN V2 (main_sln_v2.py)"

# Apps suportados pelo bot completo (SLN_APP_PACKAGE). "Todos" roda cada um em sequência
# via run_apps.py (na raiz do projeto). Chave -> (pacote, label).
APPS = {
    "1": ("com.MW.MxMotosV2", "Mx MotosV2"),
    "2": ("com.flixdigital.dramaclassic", "cas"),
    "3": ("com.MW.MxPuxouNoGrau", "Puxo No Grau"),
    "4": ("com.ManecoGames.MxBikesGrau", "Bike Mx"),
    "5": ("com.Wg.GrauBahia", "Grau Bahia"),
    "6": ("com.ManecoGames.BikesMilGrau", "Bikes Mil Grau"),
}
# Onde ficam os scripts do runner (raiz do projeto).
ROOT_DIR = SCRIPT_DIR if os.path.exists(os.path.join(SCRIPT_DIR, "run_apps.py")) else os.path.dirname(SCRIPT_DIR)
RUN_APPS_PATH = os.path.join(ROOT_DIR, "run_apps.py")

GROUPS_FILE = os.path.join(SCRIPT_DIR, "groups.json")
SCAN_CONFIG_FILE = os.path.join(SCRIPT_DIR, "ip_ranges.json")
PAUSED_FILE = os.path.join(SCRIPT_DIR, "paused.json")
LOGS_DIR = os.path.join(SCRIPT_DIR, "logs")

# Semáforo de ARRANQUE: limita quantos bots INICIAM ao mesmo tempo — não o total rodando.
# Configurável por SLN_START_CONCURRENCY (default 8; benchmark 5/10 devices usa 8–10).
START_CONCURRENCY = max(1, int(os.environ.get("SLN_START_CONCURRENCY", "8") or 8))
START_SEMAPHORE = threading.Semaphore(START_CONCURRENCY)

if os.name == 'nt':
    try:
        ctypes.windll.kernel32.SetErrorMode(0x0002 | 0x8000)
    except Exception:
        pass

# Estados globais
os.makedirs(LOGS_DIR, exist_ok=True)
processos_ativos = {}
paused_devices = set()
stopping_devices = set()
last_groups_cache = {}
last_devices_cache = []
last_running_cache = set()
_last_ui_hash = ""

print(f"[INFO] Diretório de trabalho: {SCRIPT_DIR}")

# ==================== GRUPOS ====================
def load_groups_cached():
    global last_groups_cache
    if not last_groups_cache and os.path.exists(GROUPS_FILE):
        force_reload_groups()
    return last_groups_cache

def force_reload_groups():
    global last_groups_cache
    if os.path.exists(GROUPS_FILE):
        try:
            with open(GROUPS_FILE, 'r', encoding='utf-8') as f:
                last_groups_cache = json.load(f)
        except Exception:
            last_groups_cache = {}
    else:
        last_groups_cache = {}

def save_groups(groups):
    global last_groups_cache
    last_groups_cache = groups
    with open(GROUPS_FILE, 'w', encoding='utf-8') as f:
        json.dump(groups, f, indent=2, ensure_ascii=False)

def create_group():
    nome = simpledialog.askstring("Novo Grupo", "Nome do grupo:")
    if nome:
        groups = load_groups_cached()
        if nome in groups:
            messagebox.showwarning("Aviso", f"Grupo '{nome}' já existe!")
            return
        groups[nome] = []
        save_groups(groups)
        atualizar_combo_grupos()
        messagebox.showinfo("Sucesso", f"Grupo '{nome}' criado!")

def delete_group():
    g = combo_grupos.get()
    if not g or g == "Todos os Dispositivos":
        messagebox.showwarning("Aviso", "Selecione um grupo para deletar")
        return
    if messagebox.askyesno("Confirmar", f"Deletar grupo '{g}'?"):
        groups = load_groups_cached()
        if g in groups:
            del groups[g]
            save_groups(groups)
            atualizar_combo_grupos()
            combo_grupos.set("Todos os Dispositivos")
            atualizar_lista_thread_safe()
            messagebox.showinfo("Sucesso", f"Grupo '{g}' deletado!")

def add_to_group():
    g = combo_grupos.get()
    if not g or g == "Todos os Dispositivos":
        messagebox.showwarning("Aviso", "Selecione um grupo primeiro")
        return
    sel = lista_dispositivos.curselection()
    if not sel:
        messagebox.showinfo("Aviso", "Selecione ao menos um dispositivo")
        return
    groups = load_groups_cached()
    to_add = []
    for i in sel:
        entry = lista_dispositivos.get(i)
        if "Nenhum" in entry:
            continue
        try:
            parts = entry.split(" ", 1)[1]
            to_add.append(parts.split(" [")[0])
        except Exception:
            continue
    for d_dev in to_add:
        if d_dev not in groups[g]:
            groups[g].append(d_dev)
    save_groups(groups)
    atualizar_lista_thread_safe()
    messagebox.showinfo("Sucesso", f"{len(to_add)} dispositivo(s) adicionado(s) a '{g}'")

def remove_from_group():
    g = combo_grupos.get()
    if not g or g == "Todos os Dispositivos":
        messagebox.showwarning("Aviso", "Selecione um grupo primeiro")
        return
    sel = lista_dispositivos.curselection()
    if not sel:
        messagebox.showinfo("Aviso", "Selecione ao menos um dispositivo")
        return
    groups = load_groups_cached()
    to_remove = []
    for i in sel:
        entry = lista_dispositivos.get(i)
        if "Nenhum" in entry:
            continue
        try:
            parts = entry.split(" ", 1)[1]
            to_remove.append(parts.split(" [")[0])
        except Exception:
            continue
    for d_dev in to_remove:
        if d_dev in groups[g]:
            groups[g].remove(d_dev)
    save_groups(groups)
    atualizar_lista_thread_safe()
    messagebox.showinfo("Sucesso", f"{len(to_remove)} removido(s) de '{g}'")

def atualizar_combo_grupos():
    groups = load_groups_cached()
    valores = ["Todos os Dispositivos"] + list(groups.keys())
    combo_grupos['values'] = valores
    if combo_grupos.get() not in valores:
        combo_grupos.set("Todos os Dispositivos")

# ==================== PROCESSOS / BOTS ====================
def load_paused_state():
    global paused_devices
    if os.path.exists(PAUSED_FILE):
        try:
            with open(PAUSED_FILE, 'r') as f:
                paused_devices = set(json.load(f))
        except Exception:
            paused_devices = set()

def save_paused_state():
    try:
        with open(PAUSED_FILE, 'w') as f:
            json.dump(list(paused_devices), f)
    except Exception as e:
        print(f"[ERRO] Falha ao salvar paused: {e}")

load_paused_state()

# Headless (padrão): bot sem janela. Desmarcado na tela inicial: abre também uma janela de
# console por aparelho mostrando o log ao vivo (o bot e o log em arquivo continuam iguais).
headless_flag = {"on": True}      # espelho thread-safe do checkbox (lido pelos workers)
profile_flag = {"name": "dev"}    # espelho thread-safe do perfil (lido pelos workers)
# Perfil -> overlay de config (SLN_CONFIG_OVERLAY). "dev" não passa overlay (usa config_sln_v2.json).
PROFILE_OVERLAYS = {
    "dev": None,
    "benchmark": "config_benchmark_sln_v2.json",
    "e38b": "config_e38b_hidden_controls.json",
}
console_viewers = {}              # device_id -> Popen da janela de console


def _abrir_console_log(device_id, log_file):
    """Janela de console (PowerShell) acompanhando o log do aparelho em tempo real."""
    if os.name != 'nt':
        return None
    titulo = f"Bot {device_id}".replace("'", "")
    caminho = log_file.replace("'", "''")
    cmd = ["powershell", "-NoProfile", "-Command",
           f"$host.UI.RawUI.WindowTitle='{titulo}'; "
           f"Get-Content -LiteralPath '{caminho}' -Wait -Tail 40 -Encoding UTF8"]
    try:
        return subprocess.Popen(cmd, creationflags=subprocess.CREATE_NEW_CONSOLE, cwd=SCRIPT_DIR)
    except Exception as e:
        print(f"[ERRO] Falha ao abrir console de {device_id}: {e}")
        return None


def _fechar_console_log(device_id):
    viewer = console_viewers.pop(device_id, None)
    if viewer is not None:
        try:
            viewer.terminate()
        except Exception:
            pass


def run_script(device_id):
    if device_id in processos_ativos:
        print(f"[AVISO] Bot já rodando para {device_id}. Ignorando.")
        return
    headless = headless_flag["on"]
    log_dir = LOGS_DIR
    safe_id = device_id.replace(":", "_").replace(".", "_")
    log_file = os.path.join(log_dir, f"{safe_id}.log")

    app_keys = selected_apps()
    if not app_keys:
        print("[AVISO] Nenhum app selecionado. Marque ao menos um app em 'Apps a rodar'.")
        return
    try:
        cmin = max(1, int(spin_cycles_min.get() or "5"))
        cmax = max(1, int(spin_cycles_max.get() or "15"))
    except Exception:
        cmin, cmax = 5, 15
    if cmax < cmin:
        cmin, cmax = cmax, cmin
    try:
        kmin = max(1, int(spin_chunk_min.get() or "1"))
        kmax = max(1, int(spin_chunk_max.get() or "3"))
    except Exception:
        kmin, kmax = 1, 3
    if kmax < kmin:
        kmin, kmax = kmax, kmin
    try:
        rounds = max(0, int(spin_rounds.get() or "0"))
    except Exception:
        rounds = 0

    def worker():
        # O semáforo controla APENAS o ARRANQUE (Popen + registro + pequena estabilização).
        # O process.wait() roda FORA do semáforo: a quantidade TOTAL de bots rodando não é
        # limitada por ele. Sem isso, o slot ficava preso por todo o tempo de vida do bot.
        f_log = None
        process = None
        try:
            if not os.path.isfile(RUN_APPS_PATH):
                print(f"[ERRO] Runner não encontrado: {RUN_APPS_PATH}")
                return
            # Rodar cada app em sequência, com ciclos ALEATÓRIOS entre cmin e cmax por app,
            # até terminar todos os apps selecionados (quem encerra é o próprio runner).
            cmd = [sys.executable, "-u", RUN_APPS_PATH] + app_keys + [
                "--device", device_id,
                "--cycles-min", str(cmin), "--cycles-max", str(cmax),
                "--chunk-min", str(kmin), "--chunk-max", str(kmax),
                "--runtime", "0", "--rounds", str(rounds), "--delay", "2"]
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            env = os.environ.copy()
            env.pop("SLN_APP_PACKAGE", None)
            env.pop("SLN_APP_LABEL", None)
            env.pop("SLN_CONFIG_OVERLAY", None)
            START_SEMAPHORE.acquire()
            try:
                f_log = open(log_file, "a", encoding='utf-8')
                f_log.write(f"\n{'='*40}\n[INIT] Iniciando {device_id} em {time.strftime('%Y-%m-%d %H:%M:%S')}\n{'='*40}\n")
                f_log.flush()
                process = subprocess.Popen(
                    cmd, stdout=f_log, stderr=subprocess.STDOUT,
                    creationflags=creationflags, cwd=ROOT_DIR, text=True, env=env
                )
                processos_ativos[device_id] = process
                last_running_cache.add(device_id)
                if not headless:
                    viewer = _abrir_console_log(device_id, log_file)
                    if viewer is not None:
                        console_viewers[device_id] = viewer
                # pequena estabilização do arranque; o slot é liberado logo em seguida
                time.sleep(0.2)
            finally:
                START_SEMAPHORE.release()   # arranque concluído -> próximo device pode iniciar
            janela.after(0, lambda: atualizar_lista_ui(use_cache=True))
            process.wait()
            f_log.write(f"\n[FINISH] Bot {device_id} finalizado (código {process.returncode})\n")
        except Exception as e:
            print(f"[ERRO WORKER] {e}")
        finally:
            try:
                if f_log is not None:
                    f_log.close()
            except Exception:
                pass
            _fechar_console_log(device_id)

    threading.Thread(target=worker, daemon=True).start()

def stop_script(device_id):
    """Stop the whole device bot tree, including run_apps children and logcat."""
    stopping_devices.add(device_id)
    proc = processos_ativos.pop(device_id, None)
    try:
        import psutil
        # Kill the exact main_sln_v2.py workers for this device, plus logcat.
        result = sln_proc.terminate_worker_verified(device_id, timeout=10.0,
                                                    kill_logcat=True)
        # Also kill the run_apps.py wrapper/process object if it remains.
        if proc is not None:
            try:
                p = proc if hasattr(proc, "children") else psutil.Process(proc.pid)
                for child in p.children(recursive=True):
                    try:
                        child.terminate()
                    except Exception:
                        pass
                try:
                    p.terminate()
                except Exception:
                    pass
            except Exception:
                pass
        if not result.get("ok"):
            # One final verified pass; never report stopped while workers live.
            sln_proc.terminate_worker_verified(device_id, timeout=5.0,
                                               kill_logcat=True)
        _fechar_console_log(device_id)
        last_running_cache.discard(device_id)
        paused_devices.discard(device_id)
        print(f"[INFO] Bot parado: {device_id} (ok={result.get('ok')})")
    except Exception as e:
        print(f"[ERRO] Falha ao parar {device_id}: {e}")
    finally:
        stopping_devices.discard(device_id)
    janela.after(0, lambda: atualizar_lista_ui(use_cache=True))


def toggle_pause_script(device_id):
    if device_id not in processos_ativos:
        return
    try:
        p = psutil_process(processos_ativos[device_id])
        if device_id in paused_devices:
            p.resume()
            paused_devices.remove(device_id)
            print(f"[INFO] Bot RESUMIDO: {device_id}")
        else:
            p.suspend()
            paused_devices.add(device_id)
            print(f"[INFO] Bot PAUSADO: {device_id}")
        save_paused_state()
        janela.after(0, lambda: atualizar_lista_ui(use_cache=True))
    except Exception as e:
        print(f"[ERRO] Falha ao pausar/resumir {device_id}: {e}")

def psutil_process(proc):
    import psutil
    if isinstance(proc, subprocess.Popen):
        return psutil.Process(proc.pid)
    return proc

def get_running_devices():
    import psutil
    running = {}
    target_scripts = {"main_sln_v2.py", "main_optimized.py", "main_market.py", "main_market_v2.py",
                      "mainmusicvip.py", "mainjokennews.py", "mainfrasesvip.py"}
    try:
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            pname = proc.info.get('name', '')
            if not pname or 'python' not in pname.lower():
                continue
            cmdline = proc.info.get('cmdline')
            if not cmdline or len(cmdline) < 3:
                continue
            idx = None
            for i, arg in enumerate(cmdline):
                base = os.path.basename(arg) if arg else ''
                if base in target_scripts:
                    idx = i
                    break
            if idx is None:
                continue
            # o bot é lançado como: python <script> <device_id> <modo>; o device_id pode ser
            # IP:porta OU serial USB (ex.: RQ8M305G5PK, sem '.' nem ':')
            if idx + 1 < len(cmdline) and cmdline[idx + 1]:
                running[cmdline[idx + 1]] = proc.info['pid']
    except Exception as e:
        print(f"[ERRO] get_running_devices: {e}")
    return running

def check_active_processes():
    to_remove = []
    for device_id, process in processos_ativos.items():
        try:
            if hasattr(process, 'poll'):
                if process.poll() is not None:
                    to_remove.append(device_id)
            elif hasattr(process, 'is_running'):
                if not process.is_running():
                    to_remove.append(device_id)
            else:
                to_remove.append(device_id)
        except Exception:
            to_remove.append(device_id)
    for d in to_remove:
        del processos_ativos[d]
        paused_devices.discard(d)
    if to_remove:
        save_paused_state()

def monitor_devices_loop():
    global last_running_cache, last_devices_cache
    force_reload_groups()
    while True:
        try:
            devs = listar_dispositivos_safe()
            if devs is not None:
                for d in list(processos_ativos.keys()):
                    if d not in devs:
                        devs.append(d)
                last_devices_cache = devs
            running_map = get_running_devices()
            last_running_cache = set(running_map.keys())
            for dev_id, pid in running_map.items():
                if dev_id in stopping_devices:
                    continue
                if dev_id not in processos_ativos:
                    try:
                        import psutil
                        print(f"[INFO] Re-adotando {dev_id} (PID {pid})")
                        processos_ativos[dev_id] = psutil.Process(pid)
                    except Exception:
                        pass
            check_active_processes()
            force_reload_groups()
            janela.after(0, lambda: atualizar_lista_ui(use_cache=True))
        except Exception as e:
            print(f"[ERRO Monitor]: {e}")
        n = len(last_devices_cache)
        if n > 100:
            sleep = 60
        elif n > 50:
            sleep = 30
        else:
            sleep = 15
        time.sleep(sleep)

# ==================== UI / ADB ====================
def listar_dispositivos_safe():
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        creationflags = 0x08000000 if os.name == 'nt' else 0
        result = subprocess.check_output(
            "adb devices", shell=True, text=True, timeout=20,
            startupinfo=si, creationflags=creationflags
        )
        lines = result.strip().split("\n")[1:]
        return [line.split("\t")[0] for line in lines if "device" in line]
    except Exception:
        # Timeout/ADB busy must not clear the device list while bots are running.
        return None

def listar_dispositivos():
    return listar_dispositivos_safe()

def _compute_ui_hash(dispositivos, active_devices):
    data = f"{sorted(dispositivos)}|{sorted(active_devices)}|{sorted(paused_devices)}|{combo_grupos.get()}"
    return hashlib.md5(data.encode()).hexdigest()

def atualizar_lista_ui(dispositivos=None, active_devices=None, use_cache=True):
    global last_devices_cache, last_running_cache, _last_ui_hash
    try:
        if dispositivos is None:
            dispositivos = last_devices_cache
        if active_devices is None:
            active_devices = last_running_cache
        new_hash = _compute_ui_hash(dispositivos, active_devices)
        if new_hash == _last_ui_hash:
            return
        _last_ui_hash = new_hash

        groups = load_groups_cached()
        device_to_group = {}
        for gname, gdevs in groups.items():
            for d_item in gdevs:
                device_to_group[d_item] = gname

        selected_devices = set()
        for i in lista_dispositivos.curselection():
            try:
                entry = lista_dispositivos.get(i)
                if "Nenhum" not in entry:
                    parts = entry.split(" ", 1)[1]
                    selected_devices.add(parts.split(" [")[0])
            except Exception:
                pass

        lista_dispositivos.delete(0, tk.END)
        grupo_filtro = combo_grupos.get()

        if not dispositivos:
            lista_dispositivos.insert(tk.END, "Nenhum dispositivo encontrado (ou aguardando scan...)")
            lista_dispositivos.itemconfigure(tk.END, fg="gray")
        else:
            dispositivos_filtrados = dispositivos
            if grupo_filtro != "Todos os Dispositivos" and grupo_filtro in groups:
                dispositivos_filtrados = [d for d in dispositivos if d in groups[grupo_filtro]]
            if not dispositivos_filtrados and grupo_filtro != "Todos os Dispositivos":
                lista_dispositivos.insert(tk.END, f"Nenhum dispositivo no grupo '{grupo_filtro}'")
                lista_dispositivos.itemconfigure(tk.END, fg="gray")
            else:
                for i, d_item in enumerate(dispositivos_filtrados, start=1):
                    is_active = d_item in active_devices
                    is_paused = d_item in paused_devices
                    status = " [PARADO]"
                    if is_active:
                        status = " [RODANDO]"
                    if is_paused:
                        status = " [PAUSADO]"
                    grupo = device_to_group.get(d_item)
                    grupo_tag = f" [{grupo}]" if grupo and grupo_filtro == "Todos os Dispositivos" else ""
                    texto = f"{i}. {d_item}{status}{grupo_tag}"
                    lista_dispositivos.insert(tk.END, texto)
                    if is_paused:
                        lista_dispositivos.itemconfigure(i-1, bg="#fff3cd", fg="#856404")
                    elif is_active:
                        lista_dispositivos.itemconfigure(i-1, bg="#d4edda", fg="#155724")
                    else:
                        lista_dispositivos.itemconfigure(i-1, bg="#f8d7da", fg="#721c24")
                    if d_item in selected_devices:
                        lista_dispositivos.selection_set(i-1)

        total_devices = len(dispositivos)
        total_running = len([d for d in dispositivos if d in active_devices])
        total_paused = len([d for d in dispositivos if d in paused_devices])
        lbl_status.config(text=f"📊 Total: {total_devices} | Rodando: {total_running} | Pausados: {total_paused}")
    except Exception as e:
        print(f"Erro ao atualizar UI: {e}")

def atualizar_lista_thread_safe(use_cache=True):
    janela.after(0, lambda: atualizar_lista_ui(use_cache=use_cache))

def atualizar_lista():
    global _last_ui_hash
    def worker():
        devs = listar_dispositivos_safe()
        if devs is not None:
            last_devices_cache[:] = devs
        active = get_running_devices()
        last_running_cache.clear()
        last_running_cache.update(active.keys())
        force_reload_groups()
        global _last_ui_hash
        _last_ui_hash = ""
        janela.after(0, lambda: atualizar_lista_ui(use_cache=True))
    threading.Thread(target=worker, daemon=True).start()

def iniciar_bot():
    sel = lista_dispositivos.curselection()
    if not sel:
        messagebox.showinfo("Aviso", "Selecione ao menos um dispositivo")
        return
    for i in sel:
        entry = lista_dispositivos.get(i)
        if "Nenhum" in entry:
            continue
        try:
            parts = entry.split(" ", 1)[1]
            run_script(parts.split(" [")[0])
        except Exception:
            continue

def iniciar_todos_os_bots():
    dispositivos = last_devices_cache
    if not dispositivos:
        refreshed = listar_dispositivos_safe()
        if refreshed:
            dispositivos = sorted(set(list(dispositivos) + list(refreshed)))
        if not dispositivos:
            messagebox.showinfo("Aviso", "Nenhum dispositivo encontrado")
            return
    def worker():
        count = 0
        for dev_id in dispositivos:
            if dev_id not in processos_ativos:
                run_script(dev_id)
                count += 1
                # stagger com jitter: nunca inicia todos exatamente no mesmo instante
                time.sleep(random.uniform(1.5, 3.0))
        if count > 0:
            janela.after(0, lambda: messagebox.showinfo("Sucesso", f"{count} bot(s) iniciado(s)"))
        else:
            janela.after(0, lambda: messagebox.showinfo("Aviso", "Todos já rodando"))
    threading.Thread(target=worker, daemon=True).start()

def iniciar_grupo():
    g = combo_grupos.get()
    if not g or g == "Todos os Dispositivos":
        messagebox.showwarning("Aviso", "Selecione um grupo específico")
        return
    groups = load_groups_cached()
    if g not in groups:
        messagebox.showwarning("Aviso", "Grupo não encontrado")
        return
    def worker():
        count = 0
        for dev_id in groups[g]:
            if dev_id in last_devices_cache and dev_id not in processos_ativos:
                run_script(dev_id)
                count += 1
                # stagger com jitter: nunca inicia todos exatamente no mesmo instante
                time.sleep(random.uniform(1.5, 3.0))
        if count == 0:
            janela.after(0, lambda: messagebox.showinfo("Aviso", "Nenhum dispositivo do grupo conectado ou já rodando"))
        else:
            janela.after(0, lambda: messagebox.showinfo("Sucesso", f"{count} dispositivo(s) iniciado(s) do grupo '{g}'"))
    threading.Thread(target=worker, daemon=True).start()

def parar_selecao():
    sel = lista_dispositivos.curselection()
    if not sel:
        messagebox.showinfo("Aviso", "Selecione ao menos um dispositivo")
        return
    for i in sel:
        entry = lista_dispositivos.get(i)
        if "Nenhum" in entry:
            continue
        try:
            parts = entry.split(" ", 1)[1]
            stop_script(parts.split(" [")[0])
        except Exception:
            continue

def parar_todos():
    # Adota bots j? em execu??o que esta interface ainda n?o conhece.
    try:
        import psutil
        for dev_id, pid in get_running_devices().items():
            if dev_id not in processos_ativos:
                processos_ativos[dev_id] = psutil.Process(pid)
    except Exception as e:
        print(f"[ERRO] parar_todos/adotar: {e}")
    if not processos_ativos:
        messagebox.showinfo("Aviso", "Nenhum bot rodando")
        return
    if not messagebox.askyesno("Confirmar", f"Parar todos os {len(processos_ativos)} bots?"):
        return
    devices = list(processos_ativos.keys())
    stopping_devices.update(devices)
    janela.after(0, lambda: messagebox.showinfo(
        "Parando", f"Parando {len(devices)} bot(s); aguarde a confirma??o final."))

    def worker():
        for dev_id in devices:
            stop_script(dev_id)
            # stop_script clears stopping_devices for this device; keep it until done
            stopping_devices.discard(dev_id)
        left = []
        try:
            left = list(get_running_devices().keys())
        except Exception:
            pass
        for dev_id in left:
            try:
                stopping_devices.add(dev_id)
                sln_proc.terminate_worker_verified(dev_id, timeout=8.0, kill_logcat=True)
            except Exception:
                pass
            finally:
                stopping_devices.discard(dev_id)
        janela.after(0, lambda: messagebox.showinfo(
            "Parar Todos",
            "Todos parados." if not left else
            "Solicita??o conclu?da; ainda h? %d worker(s) para verificar." % len(left)))

    threading.Thread(target=worker, daemon=True).start()


def toggle_pause_selection():
    sel = lista_dispositivos.curselection()
    for i in sel:
        try:
            entry = lista_dispositivos.get(i)
            if "Nenhum" in entry:
                continue
            parts = entry.split(" ", 1)[1]
            toggle_pause_script(parts.split(" [")[0])
        except Exception:
            pass

def stop_all_scripts():
    parar_todos()

def toggle_pause_all():
    if not processos_ativos:
        return
    total = len(processos_ativos)
    pausados = len([d for d in processos_ativos if d in paused_devices])
    should_pause = pausados < total / 2
    for dev_id in list(processos_ativos.keys()):
        if should_pause and dev_id not in paused_devices:
            toggle_pause_script(dev_id)
        elif not should_pause and dev_id in paused_devices:
            toggle_pause_script(dev_id)

def start_api_server():
    path = os.path.join(SCRIPT_DIR, "aiMain_sln_v2.py")
    if not os.path.isfile(path):
        messagebox.showerror("Erro", f"aiMain_sln_v2.py não encontrado: {path}")
        return
    cmd = f'start "IA Server SLN V2" cmd /k python "{path}"'
    subprocess.Popen(cmd, shell=True)
    messagebox.showinfo("Sucesso", "Servidor de IA SLN V2 iniciado (verifique nova janela CMD)")

# ==================== MENU CONFIGURAÇÕES ====================
class ToolTip:
    """Tooltip simples em tkinter (aparece ao passar o mouse)."""
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tip = None
        widget.bind("<Enter>", self.show)
        widget.bind("<Leave>", self.hide)

    def show(self, event=None):
        if self.tip or not self.text:
            return
        x = self.widget.winfo_rootx() + 18
        y = self.widget.winfo_rooty() + 12
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x}+{y}")
        lbl = tk.Label(self.tip, text=self.text, justify="left", bg="#ffffe0",
                       relief="solid", borderwidth=1, wraplength=360, padx=6, pady=4)
        lbl.pack()

    def hide(self, event=None):
        if self.tip:
            self.tip.destroy()
            self.tip = None

# descrições (tooltips) de cada opção — aparecem ao passar o mouse
CAMPO_HINTS = {
    "watch_time_min": "Tempo MÍNIMO em segundos que o bot ASSISTE o anúncio premiado sem tocar na tela, antes de tentar fechar.",
    "watch_time_max": "Tempo MÁXIMO em segundos que o bot assiste o anúncio premiado. O valor real é sorteado entre min e max a cada anúncio (não fica padrão).",
    "download_apps_pct": "Percentual de anúncios em que o bot INSTALA de verdade o app sugerido na Play Store (farm de instalação). Ex.: 25 = instala 1 a cada 4.",
    "move_ad_pct": "Percentual de anúncios intersticiais em que o bot 'movimenta' a tela (swipes) para parecer humano. Anúncios premiados nunca são movimentados.",
    "move_duration_min": "Duração MÍNIMA (s) dos movimentos de tela quando o bot decide movimentar o anúncio.",
    "move_duration_max": "Duração MÁXIMA (s) dos movimentos. Valor real sorteado entre min e max.",
    "reset_id_pct": "Percentual de RODADAS em que o bot redefine o ID de publicidade do Google (abre configurações, clica em Redefinir e OK).",
    "clear_data_pct": "Percentual de RODADAS em que o bot LIMPA os dados do app (zera progresso/créditos e reinicia do zero).",
    "install_wait_s": "Tempo máximo (s) que o bot espera o download/instalação terminar antes de voltar ao app.",
    "min_watch_budget_pct": "Percentual do tempo de exibição que o bot assiste antes de tentar fechar um anúncio INTERSTICIAL (ex.: 60% do tempo).",
    "max_ads_before_clear": "Número máximo de anúncios processados antes de forçar uma limpeza completa do app.",
    "progress_bar_wait_s": "Tempo (s) que o bot aguarda anúncios com barra de progresso/conto ativo fecharem sozinhos antes de tentar o X.",
    "banner_timeout_s": "Tempo (s) que o bot espera o banner de anúncio recarregar antes de clicar de novo no botão 'Assistir anúncio'.",
}
CAMPO_HINTS["use_ia"] = "Usa o servidor de IA (aiMain_sln_v2.py) para detectar o ícone >| fechar dos playables por imagem quando o dump não acha nada."
CAMPO_HINTS["save_errors"] = "Salva screenshot + XML em 'errors/' quando o bot NÃO consegue fechar um anúncio — vira base de re-treino da IA."

def abrir_configuracoes():
    """Janela de edição das percentagens/comportamentos do bot."""
    win = tk.Toplevel(janela)
    win.title("Configurações do Bot - SLN V2")
    win.geometry("460x560")
    win.grab_set()

    cfg = CONFIG.load_config()

    fields = [
        ("watch_time_min", "Assistir min (s):"),
        ("watch_time_max", "Assistir max (s):"),
        ("download_apps_pct", "Baixar app (%):"),
        ("move_ad_pct", "Movimentar anúncio (%):"),
        ("move_duration_min", "Movimentar min (s):"),
        ("move_duration_max", "Movimentar max (s):"),
        ("reset_id_pct", "Redefinir ID (% rodadas):"),
        ("clear_data_pct", "Limpar dados (% rodadas):"),
        ("install_wait_s", "Tempo instalação (s):"),
        ("min_watch_budget_pct", "Mínimo assistir (%)"),
        ("max_ads_before_clear", "Máx ads antes limpa:"),
        ("progress_bar_wait_s", "Espera barra progresso (s):"),
        ("banner_timeout_s", "Timeout banner (s):"),
    ]
    vars_ = {}
    row = 0
    for key, label in fields:
        lbl = tk.Label(win, text=label)
        lbl.grid(row=row, column=0, padx=8, pady=3, sticky="w")
        var = tk.StringVar(value=cfg.get(key, ""))
        ent = tk.Entry(win, textvariable=var, width=10)
        ent.grid(row=row, column=1, padx=8, pady=3, sticky="w")
        # tooltip no rótulo E no campo
        hint = CAMPO_HINTS.get(key, "")
        if hint:
            ToolTip(lbl, hint)
            ToolTip(ent, hint)
        vars_[key] = var
        row += 1

    # use_ia e save_errors como checkboxes
    var_use_ia = tk.BooleanVar(value=bool(cfg.get("use_ia", True)))
    chk_ia = tk.Checkbutton(win, text="Usar IA central (detectar X)", variable=var_use_ia)
    chk_ia.grid(row=row, column=0, columnspan=2, pady=2, sticky="w")
    ToolTip(chk_ia, CAMPO_HINTS.get("use_ia", ""))
    row += 1
    var_save_err = tk.BooleanVar(value=bool(cfg.get("save_errors", True)))
    chk_err = tk.Checkbutton(win, text="Salvar erros em errors/ (re-treino)", variable=var_save_err)
    chk_err.grid(row=row, column=0, columnspan=2, pady=2, sticky="w")
    ToolTip(chk_err, CAMPO_HINTS.get("save_errors", ""))
    row += 1

    tk.Label(win, text=f"IA URL: {cfg.get('ia_url','')}", fg="gray").grid(row=row, column=0, columnspan=2, pady=2, sticky="w")

    def salvar():
        new_cfg = dict(CONFIG.DEFAULTS)
        new_cfg.update(CONFIG.load_config())
        # converte valores
        for key, var in vars_.items():
            try:
                raw = var.get().strip()
                if not raw:
                    continue
                val = float(raw)
                if key in ("watch_time_min", "watch_time_max"):
                    continue  # tratado abaixo
                if val == int(val):
                    val = int(val)
                new_cfg[key] = val
            except Exception:
                pass
        # watch range
        try:
            mi = float(vars_["watch_time_min"].get())
            ma = float(vars_["watch_time_max"].get())
            new_cfg["watch_time_range"] = [int(mi), int(ma)]
        except Exception:
            pass
        try:
            mi = float(vars_["move_duration_min"].get())
            ma = float(vars_["move_duration_max"].get())
            new_cfg["move_duration_range"] = [int(mi), int(ma)]
        except Exception:
            pass
        new_cfg["use_ia"] = bool(var_use_ia.get())
        new_cfg["save_errors"] = bool(var_save_err.get())
        CONFIG.save_config(new_cfg)
        messagebox.showinfo("OK", "Configurações salvas. Novos bots usarão os novos valores.")
        win.destroy()

    tk.Button(win, text="Salvar", command=salvar, bg="#4CAF50", fg="white").grid(row=row+1, column=0, padx=8, pady=10)
    tk.Button(win, text="Cancelar", command=win.destroy).grid(row=row+1, column=1, padx=8, pady=10)

def show_cmd_window():
    messagebox.showinfo("Info", f"Bots rodando em modo headless.\nPara ver logs, veja a pasta 'logs/'.\n\nAtivos: {len(processos_ativos)}")

def hide_cmd_window():
    messagebox.showinfo("Info", f"Bots já rodam em modo headless.\nTotal ativos: {len(processos_ativos)}")

# ==================== SCANNER ====================
class ScannerTab:
    def __init__(self, parent_frame):
        self.frame = parent_frame
        self.ip_ranges = []
        self.next_id = 1
        self.scanner = connect.NetworkScanner(max_threads=50, timeout=1.0)
        self._build_ui()
        self._load_ranges()

    def _build_ui(self):
        frame_port = ttk.LabelFrame(self.frame, text="Step 1 - Porta de rede")
        frame_port.pack(fill="x", padx=10, pady=5)
        ttk.Label(frame_port, text="Porta:").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        self.entry_port = ttk.Entry(frame_port, width=8)
        self.entry_port.insert(0, "5555")
        self.entry_port.grid(row=0, column=1, padx=5, pady=5, sticky="w")
        ttk.Button(frame_port, text="Confirmar", command=self.on_enable_port).grid(row=0, column=2, padx=5)

        frame_scan = ttk.LabelFrame(self.frame, text="Faixas de IP (rede)")
        frame_scan.pack(fill="x", padx=10, pady=5)
        ttk.Label(frame_scan, text="Oct1").grid(row=0, column=0)
        ttk.Label(frame_scan, text="Oct2").grid(row=0, column=1)
        ttk.Label(frame_scan, text="Oct3(ini-fim)").grid(row=0, column=2, columnspan=2)
        ttk.Label(frame_scan, text="Oct4(ini-fim)").grid(row=0, column=4, columnspan=2)
        self.entries = []
        defaults = ["192","168","1","1","1","255"]
        for c in range(6):
            e = ttk.Entry(frame_scan, width=5)
            e.insert(0, defaults[c])
            e.grid(row=1, column=c, padx=1)
            self.entries.append(e)
        self.btn_add = ttk.Button(frame_scan, text="+ Add", command=self.on_add_range)
        self.btn_add.grid(row=1, column=6, padx=5)

        frame_list = ttk.LabelFrame(self.frame, text="Ranges Cadastrados")
        frame_list.pack(fill="both", expand=True, padx=10, pady=5)
        cols = ("id", "start_ip", "end_ip", "port")
        self.tree = ttk.Treeview(frame_list, columns=cols, show="headings", height=8)
        for c in cols:
            self.tree.heading(c, text=c.title())
            self.tree.column(c, width=110 if c in ("start_ip","end_ip") else 50, anchor="center")
        self.tree.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(frame_list, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscroll=sb.set)
        sb.pack(side="right", fill="y")

        frame_act = ttk.Frame(self.frame)
        frame_act.pack(side="bottom", fill="x", padx=10, pady=5)
        self.btn_sel = ttk.Button(frame_act, text="SCAN (Sel)", command=self.on_scan_selected)
        self.btn_sel.pack(side="left", padx=5)
        self.btn_all = ttk.Button(frame_act, text="SCAN (Todos)", command=self.on_scan_all)
        self.btn_all.pack(side="left", padx=5)
        ttk.Button(frame_act, text="Remover", command=self.on_remove_selected).pack(side="left", padx=5)
        ttk.Button(frame_act, text="Salvar", command=self._save_ranges).pack(side="right", padx=5)

        frame_logs = ttk.LabelFrame(self.frame, text="Logs")
        frame_logs.pack(fill="both", expand=True, padx=10, pady=5)
        self.txt_logs = scrolledtext.ScrolledText(frame_logs, height=8, state='disabled')
        self.txt_logs.pack(fill="both", expand=True)

    def log(self, message):
        self.txt_logs.config(state='normal')
        self.txt_logs.insert(tk.END, message + "\n")
        self.txt_logs.see(tk.END)
        self.txt_logs.config(state='disabled')

    def on_enable_port(self):
        p = self.entry_port.get().strip()
        if p.isdigit():
            messagebox.showinfo("OK", f"Porta {p} definida")
        else:
            messagebox.showerror("Erro", "Porta inválida")

    def _validate(self, v):
        if not v.isdigit() or not (0 <= int(v) <= 255):
            raise ValueError("Valor inválido (0-255)")
        return int(v)

    def on_add_range(self):
        try:
            vals = [self._validate(e.get()) for e in self.entries]
            o1, o2, o3s, o3e, o4s, o4e = vals
            port = int(self.entry_port.get())
            r = {"id": self.next_id, "start_ip": f"{o1}.{o2}.{o3s}.{o4s}",
                 "end_ip": f"{o1}.{o2}.{o3e}.{o4e}", "port": port,
                 "raw": [o1, o2, o3s, o3e, o4s, o4e]}
            self.ip_ranges.append(r)
            self.tree.insert("", "end", iid=str(r["id"]),
                             values=(r["id"], r["start_ip"], r["end_ip"], r["port"]))
            self.next_id += 1
        except Exception as e:
            messagebox.showerror("Erro", str(e))

    def on_remove_selected(self):
        for item in self.tree.selection():
            rid = int(self.tree.item(item, "values")[0])
            self.ip_ranges = [x for x in self.ip_ranges if x["id"] != rid]
            self.tree.delete(item)

    def _load_ranges(self):
        if os.path.exists(SCAN_CONFIG_FILE):
            try:
                with open(SCAN_CONFIG_FILE, 'r') as f:
                    data = json.load(f)
                ranges = data.get("ranges", data if isinstance(data, list) else [])
                self.next_id = 1
                for r in ranges:
                    r["id"] = self.next_id
                    self.ip_ranges.append(r)
                    self.tree.insert("", "end", iid=str(r["id"]),
                                     values=(r["id"], r["start_ip"], r["end_ip"], r["port"]))
                    self.next_id += 1
            except Exception as e:
                print(f"[ERRO] Falha ao carregar ranges: {e}")

    def _save_ranges(self):
        try:
            with open(SCAN_CONFIG_FILE, 'w') as f:
                json.dump({"ranges": self.ip_ranges}, f, indent=2)
            messagebox.showinfo("Salvo", "Faixas salvas.")
        except Exception as e:
            messagebox.showerror("Erro", str(e))

    def generate_ips(self, r):
        if "raw" not in r:
            return [r["start_ip"]]
        o1, o2, o3s, o3e, o4s, o4e = r["raw"]
        ips = []
        for o3 in range(o3s, o3e+1):
            for o4 in range(o4s, o4e+1):
                ips.append(f"{o1}.{o2}.{o3}.{o4}")
        return ips

    def run_scan_thread(self, ranges):
        self.btn_sel["state"] = "disabled"
        self.btn_all["state"] = "disabled"
        self.log(f"--- Iniciando Scan em {len(ranges)} faixas ---")
        all_ips = []
        for r in ranges:
            all_ips.extend(self.generate_ips(r))
        self.scanner.scan_ips(all_ips, ranges[0]["port"] if ranges else 5555, self.log)
        self.log("--- Fim do Scan ---")
        self.btn_sel["state"] = "normal"
        self.btn_all["state"] = "normal"

    def on_scan_selected(self):
        sel = self.tree.selection()
        if not sel:
            return
        ranges = []
        for item in sel:
            rid = int(self.tree.item(item, "values")[0])
            ranges.extend([x for x in self.ip_ranges if x["id"] == rid])
        threading.Thread(target=self.run_scan_thread, args=(ranges,), daemon=True).start()

    def on_scan_all(self):
        if not self.ip_ranges:
            return
        threading.Thread(target=self.run_scan_thread, args=(self.ip_ranges,), daemon=True).start()

# ==================== INTERFACE PRINCIPAL ====================
janela = tk.Tk()
janela.title("SLN V2 - Drama Classic Studio")
janela.geometry("1000x800")

notebook = ttk.Notebook(janela)
notebook.pack(fill="both", expand=True)

tab_bots = ttk.Frame(notebook)
notebook.add(tab_bots, text="🤖 Bots")

tk.Label(tab_bots, text="🚀 Gerenciador de Bots SLN V2", font=("Arial", 14, "bold")).pack(pady=5)
lbl_status = tk.Label(tab_bots, text="📊 Total: 0 | Rodando: 0 | Pausados: 0", font=("Arial", 9), fg="#666")
lbl_status.pack(pady=2)

# Script
frame_script = tk.LabelFrame(tab_bots, text="⚙️ Configuração", padx=10, pady=5)
frame_script.pack(pady=5, padx=10, fill="x")
tk.Label(frame_script, text="Script:").pack(side=tk.LEFT)
combo_scripts = ttk.Combobox(frame_script, state="readonly", width=30)
combo_scripts['values'] = list(SCRIPTS_MAP.keys())
combo_scripts.set(DEFAULT_SCRIPT_NAME)
combo_scripts.pack(side=tk.LEFT, padx=5)

tk.Label(frame_script, text="  Perfil:").pack(side=tk.LEFT)
combo_profile = ttk.Combobox(frame_script, state="readonly", width=14)
combo_profile['values'] = list(PROFILE_OVERLAYS.keys())
combo_profile.set("dev")
combo_profile.pack(side=tk.LEFT, padx=5)
combo_profile.bind("<<ComboboxSelected>>", lambda e: profile_flag.update(name=combo_profile.get()))

# Grupos
frame_grupos = tk.LabelFrame(tab_bots, text="📁 Grupos", padx=10, pady=5)
frame_grupos.pack(pady=5, padx=10, fill="x")
f_sel_grp = tk.Frame(frame_grupos); f_sel_grp.pack(fill="x", pady=2)
tk.Label(f_sel_grp, text="Grupo:").pack(side=tk.LEFT)
combo_grupos = ttk.Combobox(f_sel_grp, state="readonly", width=25)
combo_grupos.pack(side=tk.LEFT, padx=5)
combo_grupos.bind("<<ComboboxSelected>>", lambda e: atualizar_lista_thread_safe(use_cache=True))
f_btns_grp = tk.Frame(frame_grupos); f_btns_grp.pack(pady=2)
tk.Button(f_btns_grp, text="Novo", command=create_group).grid(row=0, column=0, padx=2)
tk.Button(f_btns_grp, text="Deletar", command=delete_group).grid(row=0, column=1, padx=2)
f_btns_dev = tk.Frame(frame_grupos); f_btns_dev.pack(pady=2)
tk.Button(f_btns_dev, text="+ Add Sel", command=add_to_group).grid(row=0, column=0, padx=2)
tk.Button(f_btns_dev, text="- Rem Sel", command=remove_from_group).grid(row=0, column=1, padx=2)

# Dispositivos
frame_devs = tk.LabelFrame(tab_bots, text="📱 Dispositivos Conectados", padx=10, pady=5)
frame_devs.pack(pady=5, padx=10, fill="both", expand=True)
tk.Button(frame_devs, text="🔄 Atualizar", command=atualizar_lista).pack(pady=2)
lista_dispositivos = tk.Listbox(frame_devs, selectmode=tk.MULTIPLE, width=60, height=14)
lista_dispositivos.pack(pady=2, fill="both", expand=True)

# Execução
frame_exec = tk.Frame(tab_bots)
frame_exec.pack(pady=10)
tk.Button(frame_exec, text="▶ Grupo", command=iniciar_grupo, bg="#4CAF50", fg="white").grid(row=0, column=0, padx=5)
tk.Button(frame_exec, text="▶ Seleção", command=iniciar_bot).grid(row=0, column=1, padx=5)
tk.Button(frame_exec, text="▶ Todos", command=iniciar_todos_os_bots).grid(row=0, column=2, padx=5)
tk.Button(frame_exec, text="⏸ Pausar Sel", command=toggle_pause_selection, bg="#FF9800", fg="white").grid(row=0, column=3, padx=5)
tk.Button(frame_exec, text="⏹ Parar Sel", command=parar_selecao, bg="#f44336", fg="white").grid(row=0, column=4, padx=5)

frame_exec2 = tk.Frame(tab_bots)
frame_exec2.pack(pady=2)
tk.Button(frame_exec2, text="⏸ Pausar Todos", command=toggle_pause_all, bg="#FF9800", fg="white").grid(row=0, column=0, padx=5)
tk.Button(frame_exec2, text="⏹ Parar Todos", command=stop_all_scripts, bg="#f44336", fg="white").grid(row=0, column=1, padx=5)
tk.Button(frame_exec2, text="📋 Logs", command=show_cmd_window, bg="#2196F3", fg="white").grid(row=0, column=2, padx=5)
tk.Button(frame_exec2, text="🤖 API Server", command=start_api_server, bg="#9C27B0", fg="white").grid(row=0, column=3, padx=5)

frame_exec3 = tk.Frame(tab_bots)
frame_exec3.pack(pady=2)
tk.Button(frame_exec3, text="⚙️ Configurações", command=abrir_configuracoes, bg="#607D8B", fg="white").pack(side="left", padx=5)
var_headless = tk.BooleanVar(value=True)
chk_headless = tk.Checkbutton(
    frame_exec3, text="Headless (sem janela de console)", variable=var_headless,
    command=lambda: headless_flag.update(on=bool(var_headless.get())))
chk_headless.pack(side="left", padx=5)
ToolTip(chk_headless, "Marcado: bot roda sem janela (só log em logs/). Desmarcado: ao iniciar o(s) "
                      "aparelho(s) selecionado(s), abre também uma janela de console por aparelho com o log ao vivo.")

# ==================== SELEÇÃO DE APP ====================
frame_apps = tk.LabelFrame(tab_bots, text="Apps a rodar (marque quais)", padx=8, pady=4)
frame_apps.pack(pady=4, padx=10, fill="x")
app_vars = {}
row = 0
col = 0
for key in sorted(APPS, key=int):
    package, label = APPS[key]
    var = tk.BooleanVar(value=True)
    app_vars[key] = var
    chk = tk.Checkbutton(frame_apps, text=f"{key}. {label}", variable=var,
                         font=("Arial", 9))
    chk.grid(row=row, column=col, sticky="w", padx=6, pady=1)
    col += 1
    if col >= 3:
        col = 0
        row += 1
chk_all = tk.Checkbutton(frame_apps, text="(de)selecionar todos", font=("Arial", 9, "bold"),
                         command=lambda: toggle_all_apps())
chk_all.grid(row=row, column=0, sticky="w", padx=6, pady=2)


def toggle_all_apps():
    value = not all(app_vars[k].get() for k in app_vars)
    for k in app_vars:
        app_vars[k].set(value)


tk.Label(frame_apps, text="  Anuncios/app (quota):", font=("Arial", 9, "bold")).grid(row=0, column=3, sticky="w", padx=(20, 0))
spin_cycles_min = tk.Spinbox(frame_apps, from_=1, to=999, width=4)
spin_cycles_min.delete(0, "end")
spin_cycles_min.insert(0, "5")
spin_cycles_min.grid(row=0, column=4, sticky="w", padx=(0, 2))
tk.Label(frame_apps, text="a", font=("Arial", 9)).grid(row=0, column=5, sticky="w")
spin_cycles_max = tk.Spinbox(frame_apps, from_=1, to=999, width=4)
spin_cycles_max.delete(0, "end")
spin_cycles_max.insert(0, "15")
spin_cycles_max.grid(row=0, column=6, sticky="w", padx=(2, 14))
ToolTip(spin_cycles_min, "QUOTA MÍNIMA de anúncios por app em cada grande ciclo. No início do ciclo é sorteada a quota total de cada app (ex.: 10, 12, 15).")
ToolTip(spin_cycles_max, "QUOTA MÁXIMA de anúncios por app em cada grande ciclo. Ex.: 5 a 15 = cada app sorteia sua quota total entre 5 e 15 anúncios.")
tk.Label(frame_apps, text="  por passo:", font=("Arial", 9, "bold")).grid(row=0, column=7, sticky="w", padx=(0, 2))
spin_chunk_min = tk.Spinbox(frame_apps, from_=1, to=99, width=4)
spin_chunk_min.delete(0, "end")
spin_chunk_min.insert(0, "1")
spin_chunk_min.grid(row=0, column=8, sticky="w", padx=(0, 2))
tk.Label(frame_apps, text="a", font=("Arial", 9)).grid(row=0, column=9, sticky="w")
spin_chunk_max = tk.Spinbox(frame_apps, from_=1, to=99, width=4)
spin_chunk_max.delete(0, "end")
spin_chunk_max.insert(0, "3")
spin_chunk_max.grid(row=0, column=10, sticky="w", padx=(2, 14))
ToolTip(spin_chunk_min, "Anúncios MÍNIMOS assistidos por PASS0 dentro da quota. Ex.: quota 10, passo sorteia 2 -> restam 8; repete até zerar.")
ToolTip(spin_chunk_max, "Anúncios MÁXIMOS assistidos por PASS0. Ex.: 1 a 3 -> a cada passo sorteia quantos assistir, até completar a quota de cada app.")
tk.Label(frame_apps, text="  Grandes (0=inf):", font=("Arial", 9, "bold")).grid(row=0, column=11, sticky="w", padx=(20, 0))
spin_rounds = tk.Spinbox(frame_apps, from_=0, to=999, width=4)
spin_rounds.delete(0, "end")
spin_rounds.insert(0, "0")
spin_rounds.grid(row=0, column=12, sticky="w", padx=(0, 10))
ToolTip(spin_rounds, "0 = loop infinito: após todos os apps completarem suas quotas, faz RESET DE ID (pm clear) e sorteia novas quotas. 1+ = número fixo de grandes ciclos.")


def selected_apps():
    return [k for k in sorted(APPS, key=int) if app_vars.get(k, None) and app_vars[k].get()]

# Scanner tab
tab_scan = ttk.Frame(notebook)
notebook.add(tab_scan, text="📡 Network Scanner")
app_scan = ScannerTab(tab_scan)

# ==================== DPN TAB (CLIENTE) ====================
# A interface é CLIENTE: só lê state/dpn/status.json (snapshot do runner) e publica comandos em
# state/dpn/commands/. NUNCA consulta a DPN diretamente nem bloqueia a thread do Tkinter.
tab_dpn = ttk.Frame(notebook)
notebook.add(tab_dpn, text="🌐 DPN")
if os.environ.get("SLN_UI_START_TAB", "").strip().lower() == "dpn":
    janela.after(200, lambda: notebook.select(tab_dpn))

_dpn_cols = ("state", "tunnel", "tip", "online", "total", "mode", "lan")
_dpn_tree = ttk.Treeview(tab_dpn, columns=_dpn_cols, show="tree headings", height=16)
_dpn_tree.heading("#0", text="Nome")
_dpn_tree.column("#0", width=230, anchor="w")
for _c, _h, _w in (("state", "Estado", 110), ("tunnel", "Tunnel", 70),
                   ("tip", "Tunnel IP", 120), ("online", "Online", 60),
                   ("total", "Total", 55), ("mode", "RouteMode", 90), ("lan", "LAN IP", 120)):
    _dpn_tree.heading(_c, text=_h)
    _dpn_tree.column(_c, width=_w, anchor="w")
_dpn_tree.pack(fill="both", expand=True, padx=8, pady=6)

_dpn_bar = tk.Frame(tab_dpn)
_dpn_bar.pack(fill="x", padx=8, pady=4)
_lbl_coord = tk.Label(_dpn_bar, text="Status Coordinator: OFFLINE", fg="#b71c1c",
                      font=("Arial", 9, "bold"))
_lbl_coord.pack(side="left", padx=6)


def _dpn_state_dir():
    try:
        sd = CONFIG.load_config().get("dpn_state_dir", "state/dpn")
    except Exception:
        sd = "state/dpn"
    if not os.path.isabs(sd):
        sd = os.path.join(SCRIPT_DIR, sd)
    return sd


def _dpn_status_path():
    return os.path.join(_dpn_state_dir(), "status.json")


def _dpn_publish(action, tree=None):
    tree = tree or _dpn_tree
    sel = tree.selection()
    if not sel:
        messagebox.showwarning("DPN", "Selecione uma DPN na tabela.")
        return
    dpn_id = str(sel[0]).split(":", 1)[0]
    if action in ("rotate_now", "reset_node_now"):
        if not messagebox.askyesno("Confirmação", "Confirmar %s em %s?" % (action, dpn_id)):
            return
    try:
        import dpn.ui_model as UI
        cmd = UI.make_command(dpn_id, action, requested_by="interface")
        UI.write_command(_dpn_state_dir(), cmd)
        print(f"[DPN][UI] comando {action} dpn={dpn_id} id={cmd['command_id']}")
    except Exception as e:
        messagebox.showerror("DPN", f"Falha ao publicar comando: {e}")


def _dpn_start_coordinator():
    try:
        script = os.path.join(SCRIPT_DIR, "dpn_coordinator_run.py")
        subprocess.Popen([sys.executable, script], cwd=SCRIPT_DIR,
                         creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        print("[DPN][UI] coordinator iniciado")
    except Exception as e:
        messagebox.showerror("DPN", f"Falha ao iniciar coordinator: {e}")


def _dpn_open_logs():
    try:
        os.startfile(os.path.join(SCRIPT_DIR, "logs"))
    except Exception as e:
        messagebox.showinfo("DPN", f"Logs: {os.path.join(SCRIPT_DIR, 'logs')}")


tk.Button(_dpn_bar, text="Pausar", command=lambda: _dpn_publish("pause")).pack(side="left", padx=3)
tk.Button(_dpn_bar, text="Retomar", command=lambda: _dpn_publish("resume")).pack(side="left", padx=3)
tk.Button(_dpn_bar, text="Rotacionar agora", command=lambda: _dpn_publish("rotate_now"),
          bg="#1565c0", fg="white").pack(side="left", padx=3)
tk.Button(_dpn_bar, text="Resetar nó", command=lambda: _dpn_publish("reset_node_now"),
          bg="#c62828", fg="white").pack(side="left", padx=3)
tk.Button(_dpn_bar, text="Refresh Rotas", command=lambda: _dpn_publish("refresh_routes_now"),
          bg="#6a1b9a", fg="white").pack(side="left", padx=3)
tk.Button(_dpn_bar, text="Iniciar Coordinator", command=_dpn_start_coordinator).pack(side="right", padx=3)
tk.Button(_dpn_bar, text="Abrir Logs", command=_dpn_open_logs).pack(side="right", padx=3)


def _dpn_apply_status(status, tree=None, label=None):
    try:
        import dpn.ui_model as UI
        tree = tree or _dpn_tree
        label = label or _lbl_coord
        online = UI.coordinator_online(status, stale_s=8.0)
        label.config(text="Status Coordinator: %s" % ("ONLINE" if online else "OFFLINE"),
                     fg=("#2e7d32" if online else "#b71c1c"))
        for iid in tree.get_children(""):
            tree.delete(iid)
        for r in UI.tree_rows(status):
            parent = r.get("parent") or ""
            tree.insert(parent, "end", iid=r["iid"], text=r["name"], values=(
                r.get("state", ""), r.get("tunnel", ""), r.get("tunnel_ip", ""),
                r.get("online", ""), r.get("total", ""), r.get("route_mode", ""),
                r.get("lan_ip", "")))
    except Exception:
        pass


def _dpn_reader_loop():
    import dpn.ui_model as UI
    path = _dpn_status_path()
    while True:
        try:
            st = UI.read_status(path, default=None)
            if isinstance(st, dict):
                try:
                    janela.after(0, lambda s=st: _dpn_apply_status(s))
                except Exception:
                    pass
            else:
                try:
                    janela.after(0, lambda: _lbl_coord.config(
                        text="Status Coordinator: OFFLINE", fg="#b71c1c"))
                except Exception:
                    pass
        except Exception:
            pass
        time.sleep(1.5)


threading.Thread(target=_dpn_reader_loop, daemon=True).start()

# ==================== AGENTES DPN (CONTROLLER UNIFICADO) ====================
# Controller embutido nesta tela. A interface e cliente do snapshot e do command bus;
# nenhuma credencial DPN passa pelo Tkinter.
tab_agents = ttk.Frame(notebook)
# Agentes DPN legado mantido oculto; DPN Control centraliza as fun??es

_agent_nb = ttk.Notebook(tab_agents)
_agent_nb.pack(fill="both", expand=True, padx=4, pady=4)

_tab_agent_overview = ttk.Frame(_agent_nb)
_tab_agent_list = ttk.Frame(_agent_nb)
_tab_agent_dpns = ttk.Frame(_agent_nb)
_tab_agent_manual = ttk.Frame(_agent_nb)
_tab_agent_cmds = ttk.Frame(_agent_nb)
_agent_nb.add(_tab_agent_overview, text="Visao geral")
_agent_nb.add(_tab_agent_list, text="Agentes")
_agent_nb.add(_tab_agent_dpns, text="DPNs fisicas")
_agent_nb.add(_tab_agent_manual, text="DPN Control manual")
_agent_nb.add(_tab_agent_cmds, text="Comandos")

_agent_ov = tk.Frame(_tab_agent_overview)
_agent_ov.pack(fill="x", padx=12, pady=12)
_lbl_agent_ov = tk.Label(_agent_ov, text="Agent Controller: OFFLINE", fg="#b71c1c",
                         font=("Arial", 11, "bold"), anchor="w", justify="left",
                         wraplength=900)
_lbl_agent_ov.pack(fill="x", pady=(0, 8))
_lbl_agent_ov2 = tk.Label(_agent_ov, text="", anchor="w", justify="left")
_lbl_agent_ov2.pack(fill="x", pady=2)

_agent_cols = ("dpn", "status", "auth", "ready", "latency", "seen", "error")
_agent_tree = ttk.Treeview(_tab_agent_list, columns=_agent_cols, show="tree headings", height=16)
_agent_tree.heading("#0", text="Agent")
_agent_tree.column("#0", width=300, anchor="w")
for _c, _h, _w in (("dpn", "DPN fisico", 240), ("status", "Status", 80),
                   ("auth", "Auth", 55), ("ready", "DPN ready", 75),
                   ("latency", "Latencia", 75), ("seen", "Visto", 80),
                   ("error", "Info", 160)):
    _agent_tree.heading(_c, text=_h)
    _agent_tree.column(_c, width=_w, anchor="w")
_agent_tree.pack(fill="both", expand=True, padx=8, pady=6)

_dpn_agent_cols = ("agents", "online", "auth", "best", "latency")
_agent_dpn_tree = ttk.Treeview(_tab_agent_dpns, columns=_dpn_agent_cols,
                               show="tree headings", height=14)
_agent_dpn_tree.heading("#0", text="DPN fisica")
_agent_dpn_tree.column("#0", width=390, anchor="w")
for _c, _h, _w in (("agents", "Agents", 75), ("online", "Online", 70),
                   ("auth", "Auth", 65), ("best", "Best Agent", 310),
                   ("latency", "Latencia", 80)):
    _agent_dpn_tree.heading(_c, text=_h)
    _agent_dpn_tree.column(_c, width=_w, anchor="w")
_agent_dpn_tree.pack(fill="both", expand=True, padx=8, pady=6)

_agent_manual_tree = ttk.Treeview(_tab_agent_manual, columns=_dpn_cols,
                                   show="tree headings", height=15)
_agent_manual_tree.heading("#0", text="Nome")
_agent_manual_tree.column("#0", width=230, anchor="w")
for _c, _h, _w in (("state", "Estado", 110), ("tunnel", "Tunnel", 70),
                   ("tip", "Tunnel IP", 120), ("online", "Online", 60),
                   ("total", "Total", 55), ("mode", "RouteMode", 90),
                   ("lan", "LAN IP", 120)):
    _agent_manual_tree.heading(_c, text=_h)
    _agent_manual_tree.column(_c, width=_w, anchor="w")
_agent_manual_tree.pack(fill="both", expand=True, padx=8, pady=6)
_agent_manual_bar = tk.Frame(_tab_agent_manual)
_agent_manual_bar.pack(fill="x", padx=8, pady=4)
_lbl_manual_coord = tk.Label(_agent_manual_bar, text="Status Coordinator: OFFLINE",
                             fg="#b71c1c", font=("Arial", 9, "bold"))
_lbl_manual_coord.pack(side="left", padx=6)
tk.Button(_agent_manual_bar, text="Pausar",
          command=lambda: _dpn_publish("pause", _agent_manual_tree)).pack(side="left", padx=2)
tk.Button(_agent_manual_bar, text="Retomar",
          command=lambda: _dpn_publish("resume", _agent_manual_tree)).pack(side="left", padx=2)
tk.Button(_agent_manual_bar, text="Rotacionar",
          command=lambda: _dpn_publish("rotate_now", _agent_manual_tree),
          bg="#1565c0", fg="white").pack(side="left", padx=2)
tk.Button(_agent_manual_bar, text="Resetar no",
          command=lambda: _dpn_publish("reset_node_now", _agent_manual_tree),
          bg="#c62828", fg="white").pack(side="left", padx=2)
tk.Button(_agent_manual_bar, text="Refresh rotas",
          command=lambda: _dpn_publish("refresh_routes_now", _agent_manual_tree),
          bg="#6a1b9a", fg="white").pack(side="left", padx=2)
tk.Button(_agent_manual_bar, text="Iniciar coordinator",
          command=_dpn_start_coordinator).pack(side="right", padx=2)

_agent_cmd_bar = tk.Frame(_tab_agent_cmds)
_agent_cmd_bar.pack(fill="x", padx=8, pady=4)
tk.Button(_agent_cmd_bar, text="Atualizar comandos",
          command=lambda: _agent_refresh_commands()).pack(side="left")
_agent_cmd_text = scrolledtext.ScrolledText(_tab_agent_cmds, height=16, wrap="none")
_agent_cmd_text.pack(fill="both", expand=True, padx=8, pady=6)

_agent_bar = tk.Frame(tab_agents)
_agent_bar.pack(fill="x", padx=8, pady=4)
_lbl_agent_ctrl = tk.Label(_agent_bar, text="Agent Controller: OFFLINE", fg="#b71c1c",
                           font=("Arial", 9, "bold"), anchor="w", justify="left",
                           wraplength=900)
_lbl_agent_ctrl.pack(fill="x", padx=6, pady=(0, 2))

_agent_runtime = None
_agent_runtime_lock = threading.Lock()


def _agent_status_path():
    return os.path.join(_dpn_state_dir(), "agents_status.json")


def _agent_runtime_alive():
    rt = _agent_runtime
    t = getattr(rt, "_thread", None) if rt is not None else None
    return bool(rt is not None and t is not None and t.is_alive()
                and getattr(rt, "ready", None) is not None and rt.ready.is_set()
                and not getattr(rt, "start_error", ""))


def _agent_command(action, **fields):
    try:
        sd = _dpn_state_dir()
        cdir = os.path.join(sd, "agent_commands")
        os.makedirs(cdir, exist_ok=True)
        cid = uuid.uuid4().hex
        cmd = {"command_id": cid, "action": action, "created_at": time.time()}
        cmd.update(fields)
        path = os.path.join(cdir, cid + ".json")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cmd, f)
        os.replace(tmp, path)
        return cid
    except Exception as e:
        messagebox.showerror("Agentes DPN", "Falha ao publicar comando: %s" % e)
        return None


def _agent_start_controller():
    global _agent_runtime
    try:
        import dpn.ui_model as UI
        st = UI.read_status(_agent_status_path(), default=None)
        if isinstance(st, dict) and UI.agent_controller_online(st, stale_s=8.0):
            messagebox.showinfo("Agentes DPN", "Controller ja esta ONLINE. Use as abas internas.")
            return
        with _agent_runtime_lock:
            if _agent_runtime_alive():
                messagebox.showinfo("Agentes DPN", "Controller ja esta em execucao nesta tela.")
                return
            # A start may still be in progress (ready event not set yet).
            _pending = _agent_runtime
            _pending_thread = getattr(_pending, "_thread", None) if _pending is not None else None
            if _pending_thread is not None and _pending_thread.is_alive():
                _lbl_agent_ctrl.config(text="Agent Controller: INICIANDO", fg="#f9a825")
                return
            from dpn_agent_controller import ControllerRuntime
            cfg = CONFIG.load_config()
            sd = _dpn_state_dir()
            store = os.path.join(SCRIPT_DIR, cfg.get("device_agent_state_dir", "state/device_agents"))
            rt = ControllerRuntime(cfg, SCRIPT_DIR, sd, store)
            _agent_runtime = rt
        def boot():
            ok = rt.start()
            def apply():
                if ok:
                    _lbl_agent_ctrl.config(text="Agent Controller: ONLINE", fg="#2e7d32")
                    print("[AGENT][UI] controller interno ONLINE")
                else:
                    _lbl_agent_ctrl.config(text="Agent Controller: ERROR", fg="#b71c1c")
                    messagebox.showerror("Agentes DPN", "Falha ao iniciar controller: %s" %
                                         (rt.start_error or "porta/certificado"))
            try:
                janela.after(0, apply)
            except Exception:
                pass
        threading.Thread(target=boot, daemon=True, name="agent-controller-ui").start()
        _lbl_agent_ctrl.config(text="Agent Controller: INICIANDO", fg="#f9a825")
        print("[AGENT][UI] controller interno iniciado")
    except Exception as e:
        messagebox.showerror("Agentes DPN", "Falha ao iniciar controller: %s" % e)


def _agent_stop_controller():
    global _agent_runtime
    with _agent_runtime_lock:
        rt = _agent_runtime
        _agent_runtime = None
    if rt is None:
        messagebox.showinfo("Agentes DPN", "Nao ha controller interno para parar.")
        return
    def stop():
        rt.stop()
        try:
            janela.after(0, lambda: _lbl_agent_ctrl.config(
                text="Agent Controller: OFFLINE", fg="#b71c1c"))
        except Exception:
            pass
    threading.Thread(target=stop, daemon=True, name="agent-controller-stop").start()


def _agent_refresh_identities():
    try:
        max_agents = int(CONFIG.load_config().get("dpn_agent_refresh_max", 8) or 8)
    except Exception:
        max_agents = 8
    if not messagebox.askyesno(
            "Agentes DPN",
            "Consultar a identidade AtomOS de ate %d Agents online?\n"
            "Isso autentica no AtomOS e pode sofrer throttle se repetido." % max_agents):
        return
    if _agent_command("refresh_identities", max_agents=max_agents):
        print("[AGENT][UI] refresh_identities solicitado")


def _agent_open_pairing():
    cid = _agent_command("open_pairing", intent="interface", ttl_s=300)
    if not cid:
        return
    done = os.path.join(_dpn_state_dir(), "agent_commands_done", cid + ".json")

    def wait_result():
        for _ in range(20):
            try:
                with open(done, "r", encoding="utf-8") as f:
                    res = json.load(f)
                code = res.get("pairing_code")
                if code:
                    try:
                        os.unlink(done)
                    except Exception:
                        pass
                    janela.after(0, lambda c=code: messagebox.showinfo(
                        "Pareamento DPN",
                        "Codigo temporario (300s):\n%s\n\nUse no Provision do Agent." % c))
                    return
            except Exception:
                pass
            time.sleep(0.5)
        janela.after(0, lambda: messagebox.showwarning(
            "Pareamento DPN", "Controller sem resposta. Inicie o Agent Controller."))

    threading.Thread(target=wait_result, daemon=True).start()


def _agent_refresh_commands():
    try:
        base = os.path.join(_dpn_state_dir(), "agent_commands_done")
        names = []
        if os.path.isdir(base):
            names = [os.path.join(base, n) for n in os.listdir(base) if n.endswith(".json")]
        names.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        lines = []
        for path in names[:40]:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    r = json.load(f)
                if r.get("action") == "open_pairing":
                    continue
                when = time.strftime("%H:%M:%S", time.localtime(float(r.get("at") or 0)))
                err = (r.get("error") or "")[:80]
                lines.append("%s  %-20s %-8s %s" % (
                    when, str(r.get("action") or ""), str(r.get("status") or ""), err))
            except Exception:
                pass
        _agent_cmd_text.configure(state="normal")
        _agent_cmd_text.delete("1.0", "end")
        _agent_cmd_text.insert("end", "\n".join(lines) if lines else "(sem comandos)")
        _agent_cmd_text.configure(state="disabled")
    except Exception:
        pass


def _agent_apply_status(status):
    try:
        import dpn.ui_model as UI
        online = UI.agent_controller_online(status, stale_s=8.0)
        rows = UI.agent_rows(status)
        dpns = UI.agent_dpn_rows(status)
        n_on = sum(1 for r in rows if r["status"].upper() == "ONLINE")
        n_auth = sum(1 for r in rows if r["authenticated"])
        n_id = sum(1 for r in rows if r["physical_dpn_id"])
        label = "Agent Controller: %s | agents=%d/%d | online=%d | identidade=%d | auth=%d" % (
            "ONLINE" if online else "OFFLINE", n_on, len(rows), n_on, n_id, n_auth)
        _lbl_agent_ctrl.config(text=label, fg=("#2e7d32" if online else "#b71c1c"))
        _lbl_agent_ov.config(text=label, fg=("#2e7d32" if online else "#b71c1c"))
        try:
            upd = time.strftime("%H:%M:%S", time.localtime(float(status.get("updated_at") or 0)))
            srv = status.get("server") or {}
            _lbl_agent_ov2.config(
                text="Atualizado: %s   Connections: %s   Pending: %s   DPNs: %d" % (
                    upd, srv.get("connections", 0), srv.get("pending", 0), len(dpns)))
        except Exception:
            _lbl_agent_ov2.config(text="")
        for iid in _agent_tree.get_children(""):
            _agent_tree.delete(iid)
        for r in rows:
            seen = r.get("last_seen") or 0
            seen_s = time.strftime("%H:%M:%S", time.localtime(seen)) if seen else ""
            _agent_tree.insert("", "end", iid=r["agent_id"], text=r["agent_id"], values=(
                r.get("physical_dpn_id") or "(identidade pendente)",
                r.get("status", ""), "yes" if r.get("authenticated") else "no",
                "yes" if r.get("dpn_ready") else "no",
                ("%sms" % r.get("latency_ms")) if r.get("latency_ms") else "",
                seen_s, r.get("error", "")))
        for iid in _agent_dpn_tree.get_children(""):
            _agent_dpn_tree.delete(iid)
        for d in dpns:
            _agent_dpn_tree.insert("", "end", iid=d["physical_dpn_id"],
                                   text=d["physical_dpn_id"], values=(
                                       d["agents"], d["online"], d["authenticated"],
                                       d["best_agent_id"], d["latency_ms"]))
    except Exception:
        pass


def _agent_manual_apply(status):
    _dpn_apply_status(status, _agent_manual_tree, _lbl_manual_coord)


def _agent_manual_reader_loop():
    import dpn.ui_model as UI
    path = _dpn_status_path()
    while True:
        try:
            st = UI.read_status(path, default=None)
            if isinstance(st, dict):
                try:
                    janela.after(0, lambda s=st: _agent_manual_apply(s))
                except Exception:
                    pass
            else:
                try:
                    janela.after(0, lambda: _lbl_manual_coord.config(
                        text="Status Coordinator: OFFLINE", fg="#b71c1c"))
                except Exception:
                    pass
        except Exception:
            pass
        time.sleep(1.5)


def _agent_reader_loop():
    import dpn.ui_model as UI
    path = _agent_status_path()
    while True:
        try:
            st = UI.read_status(path, default=None)
            if isinstance(st, dict):
                try:
                    janela.after(0, lambda s=st: _agent_apply_status(s))
                except Exception:
                    pass
            else:
                try:
                    janela.after(0, lambda: _lbl_agent_ctrl.config(
                        text="Agent Controller: OFFLINE", fg="#b71c1c"))
                except Exception:
                    pass
        except Exception:
            pass
        time.sleep(1.5)


tk.Button(_agent_bar, text="Iniciar", command=_agent_start_controller,
          bg="#2e7d32", fg="white").pack(side="left", padx=2)
tk.Button(_agent_bar, text="Parar", command=_agent_stop_controller,
          bg="#c62828", fg="white").pack(side="left", padx=2)
tk.Button(_agent_bar, text="Identidades", command=_agent_refresh_identities).pack(side="left", padx=2)
tk.Button(_agent_bar, text="Codigo", command=_agent_open_pairing).pack(side="left", padx=2)
tk.Button(_agent_bar, text="Logs", command=_dpn_open_logs).pack(side="right", padx=2)

threading.Thread(target=_agent_reader_loop, daemon=True).start()
threading.Thread(target=_agent_manual_reader_loop, daemon=True).start()

# ==================== DPN CONTROL (AGENT POOL) ====================
# Layout inspirado no DPN Control desktop: lista de unidades ? esquerda e
# painel da unidade selecionada ? direita. Fonte: Device Agent Controller novo.
tab_dpn_control = ttk.Frame(notebook)
notebook.add(tab_dpn_control, text="DPN Control")

_dc_outer = tk.Frame(tab_dpn_control, bg="#20232b")
_dc_outer.pack(fill="both", expand=True)
_dc_left = tk.Frame(_dc_outer, width=300, bg="#20232b")
_dc_left.pack(side="left", fill="y")
_dc_left.pack_propagate(False)
_dc_right = tk.Frame(_dc_outer, bg="#20232b")
_dc_right.pack(side="left", fill="both", expand=True)

tk.Label(_dc_left, text="DPN Control", bg="#20232b", fg="#e5e7eb",
         font=("Arial", 12, "bold"), anchor="w").pack(fill="x", padx=8, pady=(8, 4))
_dc_left_buttons = tk.Frame(_dc_left, bg="#20232b")
_dc_left_buttons.pack(fill="x", padx=6, pady=4)
tk.Button(_dc_left_buttons, text="Atualizar", command=lambda: _dc_refresh_units()).pack(side="left", padx=2)
tk.Button(_dc_left_buttons, text="Verificar todas", command=lambda: _dc_refresh_units()).pack(side="left", padx=2)
_dc_list = tk.Listbox(_dc_left, bg="#2b2f3a", fg="#d1d5db",
                      selectbackground="#0b5cad", selectforeground="white",
                      activestyle="none", exportselection=False, height=20)
_dc_list.pack(fill="both", expand=True, padx=6, pady=4)
_dc_left_note = tk.Label(_dc_left, text="Agents ONLINE e DPNs f?sicas",
                         bg="#20232b", fg="#9ca3af", anchor="w", wraplength=280)
_dc_left_note.pack(fill="x", padx=8, pady=4)
_dc_ctrl_label = tk.Label(_dc_left, text="Controller: verificando...", bg="#20232b",
                          fg="#f59e0b", anchor="w")
_dc_ctrl_label.pack(fill="x", padx=8, pady=(2, 0))
_dc_start_btn = tk.Button(_dc_left, text="Iniciar controller",
                          command=lambda: _agent_start_controller())
_dc_start_btn.pack(fill="x", padx=8, pady=4)

_dc_header = tk.Label(_dc_right, text="Nenhuma unidade selecionada", bg="#1f2937",
                      fg="#e5e7eb", anchor="w", font=("Arial", 10, "bold"),
                      padx=10, pady=7)
_dc_header.pack(fill="x", padx=4, pady=(4, 0))
_dc_nb = ttk.Notebook(_dc_right)
_dc_nb.pack(fill="both", expand=True, padx=4, pady=4)
_tab_dc_unit = ttk.Frame(_dc_nb)
_tab_dc_access = ttk.Frame(_dc_nb)
_tab_dc_tunnels = ttk.Frame(_dc_nb)
_tab_dc_agents = ttk.Frame(_dc_nb)
_tab_dc_coord = ttk.Frame(_dc_nb)
_dc_nb.add(_tab_dc_unit, text="Unidade")
_dc_nb.add(_tab_dc_access, text="Access Control")
_dc_nb.add(_tab_dc_tunnels, text="T?neis")
_dc_nb.add(_tab_dc_agents, text="Agentes")
_dc_nb.add(_tab_dc_coord, text="Coordinator")

# Unidade
_dc_u = tk.Frame(_tab_dc_unit, bg="#2b2f3a")
_dc_u.pack(fill="both", expand=True, padx=8, pady=8)
_dc_title = tk.Label(_dc_u, text="?", bg="#2b2f3a", fg="#e5e7eb",
                     font=("Arial", 14, "bold"), anchor="w")
_dc_title.pack(fill="x")
_dc_status = tk.Label(_dc_u, text="", bg="#2b2f3a", fg="#22c55e", anchor="w")
_dc_status.pack(fill="x", pady=(0, 6))
_dc_conn = tk.Label(_dc_u, text="", bg="#2b2f3a", fg="#cbd5e1",
                    anchor="w", justify="left", wraplength=780)
_dc_conn.pack(fill="x", pady=3)
_dc_identity = tk.Label(_dc_u, text="", bg="#2b2f3a", fg="#cbd5e1",
                        anchor="w", justify="left", wraplength=780)
_dc_identity.pack(fill="x", pady=3)
_dc_validation = tk.Label(_dc_u, text="", bg="#2b2f3a", fg="#f59e0b",
                          anchor="w", justify="left", wraplength=780)
_dc_validation.pack(fill="x", pady=3)
_dc_mode = tk.Label(_dc_u, text="Modo DPN: ?", bg="#2b2f3a", fg="#e5e7eb", anchor="w")
_dc_mode.pack(fill="x", pady=(12, 3))
_dc_tunnel_label = tk.Label(_dc_u, text="T?nel: ?", bg="#2b2f3a", fg="#e5e7eb", anchor="w")
_dc_tunnel_label.pack(fill="x", pady=3)
_dc_unit_actions = tk.Frame(_dc_u, bg="#2b2f3a")
_dc_unit_actions.pack(fill="x", pady=12)
tk.Button(_dc_unit_actions, text="Atualizar unidade", command=lambda: _dc_refresh_detail()).pack(side="left", padx=3)
tk.Button(_dc_unit_actions, text="Atualizar t?neis", command=lambda: _dc_read("LIST_DPN_TUNNELS")).pack(side="left", padx=3)
tk.Button(_dc_unit_actions, text="Atualizar Access Control", command=lambda: _dc_read("LIST_DPN_ACCESS_CONTROL")).pack(side="left", padx=3)
_dc_last = tk.Label(_dc_u, text="?ltima a??o: ?", bg="#2b2f3a", fg="#9ca3af", anchor="w")
_dc_last.pack(fill="x", pady=(8, 0))

# Access Control
_dc_access_bar = tk.Frame(_tab_dc_access)
_dc_access_bar.pack(fill="x", padx=6, pady=4)
tk.Label(_dc_access_bar, text="Dispositivos conectados (somente leitura no Agent)",
         anchor="w").pack(side="left")
tk.Button(_dc_access_bar, text="Atualizar", command=lambda: _dc_read("LIST_DPN_ACCESS_CONTROL")).pack(side="right")
_dc_access = ttk.Treeview(_tab_dc_access, columns=("bucket", "mac", "name", "ip", "mode"),
                          show="headings", height=14)
for _c, _h, _w in (("bucket", "Online/Offline", 100), ("mac", "MAC", 150),
                   ("name", "Nome", 170), ("ip", "IP", 140), ("mode", "Modo", 100)):
    _dc_access.heading(_c, text=_h)
    _dc_access.column(_c, width=_w, anchor="w")
_dc_access.pack(fill="both", expand=True, padx=6, pady=4)

# T?neis
_dc_tun_bar = tk.Frame(_tab_dc_tunnels)
_dc_tun_bar.pack(fill="x", padx=6, pady=4)
tk.Button(_dc_tun_bar, text="Atualizar lista", command=lambda: _dc_read("LIST_DPN_TUNNELS")).pack(side="left")
tk.Button(_dc_tun_bar, text="Switch active IP", command=lambda: _dc_switch_selected(),
          bg="#1565c0", fg="white").pack(side="left", padx=4)
tk.Button(_dc_tun_bar, text="Refresh", command=lambda: _dc_refresh_selected(),
          bg="#6a1b9a", fg="white").pack(side="left")
_dc_tun_status = tk.Label(_tab_dc_tunnels, text="", anchor="w")
_dc_tun_status.pack(fill="x", padx=6)
_dc_tun = ttk.Treeview(_tab_dc_tunnels, columns=("region", "country", "count", "ip"),
                       show="headings", height=14)
for _c, _h, _w in (("region", "Regi?o", 120), ("country", "Pa?s", 90),
                   ("count", "# IPs ativos", 100), ("ip", "IP ativo", 160)):
    _dc_tun.heading(_c, text=_h)
    _dc_tun.column(_c, width=_w, anchor="w")
_dc_tun.pack(fill="both", expand=True, padx=6, pady=4)

_dc_agents_bar = tk.Frame(_tab_dc_agents)
_dc_agents_bar.pack(fill="x", padx=6, pady=4)
tk.Button(_dc_agents_bar, text="Atualizar agentes", command=lambda: _dc_refresh_units()).pack(side="left", padx=2)
tk.Button(_dc_agents_bar, text="Identidades", command=lambda: _agent_refresh_identities()).pack(side="left", padx=2)
tk.Button(_dc_agents_bar, text="Atualizar identidades", command=lambda: _dc_refresh_identities()).pack(side="left", padx=2)
tk.Button(_dc_agents_bar, text="Gerar c?digo", command=lambda: _agent_open_pairing()).pack(side="left", padx=2)
tk.Button(_dc_agents_bar, text="Iniciar controller", command=lambda: _agent_start_controller()).pack(side="right", padx=2)
_dc_agents = ttk.Treeview(_tab_dc_agents,
                          columns=("dpn", "status", "auth", "lat", "ip"),
                          show="tree headings", height=14)
_dc_agents.heading("#0", text="Agent")
_dc_agents.column("#0", width=280, anchor="w")
for _c, _h, _w in (("dpn", "DPN fisica", 250), ("status", "Status", 75),
                   ("auth", "Auth", 55), ("lat", "Latencia", 75), ("ip", "LAN", 120)):
    _dc_agents.heading(_c, text=_h)
    _dc_agents.column(_c, width=_w, anchor="w")
_dc_agents.pack(fill="both", expand=True, padx=6, pady=4)

_dc_coord_bar = tk.Frame(_tab_dc_coord)
_dc_coord_bar.pack(fill="x", padx=6, pady=4)
_dc_coord_label = tk.Label(_dc_coord_bar, text="Status Coordinator: OFFLINE", anchor="w",
                           font=("Arial", 9, "bold"))
_dc_coord_label.pack(side="left", padx=4)
tk.Button(_dc_coord_bar, text="Pausar",
          command=lambda: _dpn_publish("pause", _dc_coord_tree)).pack(side="left", padx=2)
tk.Button(_dc_coord_bar, text="Retomar",
          command=lambda: _dpn_publish("resume", _dc_coord_tree)).pack(side="left", padx=2)
tk.Button(_dc_coord_bar, text="Rotacionar",
          command=lambda: _dpn_publish("rotate_now", _dc_coord_tree),
          bg="#1565c0", fg="white").pack(side="left", padx=2)
tk.Button(_dc_coord_bar, text="Refresh rotas",
          command=lambda: _dpn_publish("refresh_routes_now", _dc_coord_tree),
          bg="#6a1b9a", fg="white").pack(side="left", padx=2)
_dc_coord_tree = ttk.Treeview(_tab_dc_coord, columns=_dpn_cols,
                              show="tree headings", height=14)
_dc_coord_tree.heading("#0", text="DPN / grupo")
_dc_coord_tree.column("#0", width=250, anchor="w")
for _c, _h, _w in (("state", "Estado", 100), ("tunnel", "Tunnel", 65),
                   ("tip", "IP", 130), ("online", "Ativos", 60),
                   ("total", "Members", 70), ("mode", "Modo", 80),
                   ("lan", "Erro", 200)):
    _dc_coord_tree.heading(_c, text=_h)
    _dc_coord_tree.column(_c, width=_w, anchor="w")
_dc_coord_tree.pack(fill="both", expand=True, padx=6, pady=4)

_dc_history_frame = tk.LabelFrame(_dc_right, text="Hist?rico")
_dc_history_frame.pack(fill="both", expand=False, padx=4, pady=(0, 4))
_dc_history = ttk.Treeview(_dc_history_frame, columns=("when", "group", "action", "result"),
                           show="headings", height=6)
for _c, _h, _w in (("when", "Quando", 95), ("group", "Unidade", 100),
                   ("action", "A??o", 170), ("result", "Resultado", 430)):
    _dc_history.heading(_c, text=_h)
    _dc_history.column(_c, width=_w, anchor="w")
_dc_history.pack(fill="both", expand=True, padx=4, pady=4)

_dc_sel_phys = ""
_dc_sel_group = ""
_dc_unit_rows = {}


def _dc_controller_online():
    try:
        import dpn.ui_model as UI
        st = UI.read_status(_agent_status_path(), default=None)
        return bool(isinstance(st, dict) and UI.agent_controller_online(st, stale_s=8.0))
    except Exception:
        return False


def _dc_controller_tick():
    online = _dc_controller_online()
    try:
        _dc_ctrl_label.config(text="Controller: " + ("ONLINE" if online else "OFFLINE"),
                              fg=("#22c55e" if online else "#f59e0b"))
    except Exception:
        pass


def _dc_refresh_identities():
    if not _dc_sel_phys:
        messagebox.showinfo("DPN Control", "Selecione uma unidade.")
        return
    ag = _dc_load_json(_dc_agents_path(), {})
    ids = []
    for a in (ag.get("agents") or []):
        if str(a.get("physical_dpn_id") or "") == _dc_sel_phys and str(a.get("status") or "").upper() == "ONLINE":
            ids.append(str(a.get("agent_id") or ""))
    ids = [x for x in ids if x][:8]
    if not ids:
        messagebox.showwarning("DPN Control", "Nenhum Agent ONLINE para esta DPN.")
        return
    cid = _agent_command("refresh_identities", agent_ids=ids, max_agents=len(ids))
    if not cid:
        return
    _dc_set_last("refresh_identities (%d agents)" % len(ids))
    done = os.path.join(_dpn_state_dir(), "agent_commands_done", cid + ".json")
    def wait():
        for _ in range(100):
            r = _dc_load_json(done, None)
            if isinstance(r, dict):
                janela.after(0, _dc_refresh_units)
                janela.after(0, lambda: _dc_tun_status.config(
                    text="identidades: %s" % r.get("status")))
                return
            time.sleep(0.5)
    threading.Thread(target=wait, daemon=True).start()


def _dc_auto_start():
    if _dc_controller_online():
        return
    _agent_start_controller()
    janela.after(3000, _dc_auto_start)


def _dc_state_path():
    return os.path.join(_dpn_state_dir(), "status.json")


def _dc_agents_path():
    return os.path.join(_dpn_state_dir(), "agents_status.json")


def _dc_auto_groups():
    try:
        with open(os.path.join(SCRIPT_DIR, "dpn_groups_auto.json"), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _dc_load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _dc_set_last(text):
    _dc_last.config(text="?ltima a??o: " + str(text))


def _dc_refresh_units():
    global _dc_unit_rows
    st = _dc_load_json(_dc_state_path(), {})
    ag = _dc_load_json(_dc_agents_path(), {})
    auto = _dc_auto_groups()
    by_phys = {}
    for gid, g in auto.items():
        if isinstance(g, dict):
            by_phys[str(g.get("expected_dpn_id") or "")] = (gid, g)
    prev = _dc_sel_phys
    _dc_list.delete(0, "end")
    _dc_unit_rows = {}
    for phys, d in (ag.get("dpns") or {}).items():
        gid, spec = by_phys.get(phys, ("", {}))
        g = ((st.get("dpns") or {}).get(gid) if gid else {}) or {}
        auth = int(d.get("authenticated") or 0)
        name = gid or ("DPN_" + str(phys).split("|")[-1][-10:])
        online = g.get("state") or "SEM GRUPO"
        label = "%s  (%s)\n%s  |  auth=%d" % (name, phys[-12:], online, auth)
        _dc_list.insert("end", label)
        _dc_unit_rows[str(phys)] = {"gid": gid, "group": g, "agents": d,
                                    "spec": spec, "index": _dc_list.size() - 1}
        if str(phys) == prev:
            _dc_list.selection_set(_dc_list.size() - 1)
    try:
        for iid in _dc_agents.get_children(""):
            _dc_agents.delete(iid)
        for a in (ag.get("agents") or []):
            dpn = str(a.get("physical_dpn_id") or "")
            if _dc_sel_phys and dpn != _dc_sel_phys:
                continue
            _dc_agents.insert("", "end", iid=str(a.get("agent_id")),
                              text=str(a.get("agent_id")),
                              values=(dpn, a.get("status"),
                                      "yes" if a.get("authenticated") else "no",
                                      a.get("latency_ms", 0), a.get("lan_ip", "")))
    except Exception:
        pass
    if _dc_list.size() and not _dc_list.curselection():
        _dc_list.selection_set(0)
        try:
            _dc_on_select()
        except Exception:
            pass
    _dc_history_refresh()


def _dc_selected():
    sel = _dc_list.curselection()
    if not sel:
        return None
    idx = int(sel[0])
    for phys, row in _dc_unit_rows.items():
        if row.get("index") == idx:
            return phys, row
    return None


def _dc_on_select(_event=None):
    global _dc_sel_phys, _dc_sel_group
    got = _dc_selected()
    if not got:
        return
    _dc_sel_phys, row = got
    _dc_sel_group = row.get("gid", "")
    g = row.get("group") or {}
    agents = row.get("agents") or {}
    _dc_header.config(text="%s  |  DPN f?sica: %s  |  Agentes: %d online / %d auth" % (
        _dc_sel_group or "SEM GRUPO", _dc_sel_phys,
        int(agents.get("online") or 0), int(agents.get("authenticated") or 0)))
    _dc_title.config(text=_dc_sel_group or "DPN")
    _dc_status.config(text=str(g.get("state") or "SEM GRUPO"))
    _dc_conn.config(text="Gerenciamento: via Agent pool  |  IP: %s  |  Epoch: %s" % (
        g.get("current_public_ip") or "?", g.get("current_epoch") or "?"))
    _dc_identity.config(text="Physical DPN: %s" % _dc_sel_phys)
    _dc_validation.config(text="Ativos: %s  |  Safe: %s  |  Erro: %s" % (
        g.get("active_devices", "?"), g.get("safe_devices", "?"),
        g.get("last_error") or "?"))
    _dc_mode.config(text="Modo DPN: consultando...")
    _dc_tunnel_label.config(text="T?nel: consultando...")
    auth_count = int(agents.get("authenticated") or 0)
    if auth_count <= 0:
        _dc_prepare()
    else:
        _dc_refresh_detail()


def _dc_prepare():
    if not _dc_sel_phys:
        return
    ag = _dc_load_json(_dc_agents_path(), {})
    ids = []
    for a in (ag.get("agents") or []):
        if (str(a.get("physical_dpn_id") or "") == _dc_sel_phys
                and str(a.get("status") or "").upper() == "ONLINE"):
            ids.append(str(a.get("agent_id") or ""))
    ids = [x for x in ids if x][:8]
    if not ids:
        _dc_validation.config(text="Nenhum Agent ONLINE para esta DPN.")
        return
    _dc_validation.config(text="Preparando identidade (m?x. 8 Agents)...")
    cid = _agent_command("refresh_identities", agent_ids=ids, max_agents=len(ids))
    if not cid:
        return
    done = os.path.join(_dpn_state_dir(), "agent_commands_done", cid + ".json")
    def wait():
        for _ in range(100):
            r = _dc_load_json(done, None)
            if isinstance(r, dict):
                janela.after(0, _dc_refresh_units)
                janela.after(0, _dc_refresh_detail)
                return
            time.sleep(0.5)
        janela.after(0, lambda: _dc_validation.config(
            text="Identidade sem resposta; verifique auth/backoff."))
    threading.Thread(target=wait, daemon=True).start()


def _dc_refresh_detail():
    if not _dc_sel_phys:
        return
    _dc_read("GET_DPN_MODE")
    _dc_read("LIST_DPN_TUNNELS")
    _dc_set_last("atualizar unidade")


def _dc_show_tunnels(data):
    for iid in _dc_tun.get_children(""):
        _dc_tun.delete(iid)
    ts = data.get("tunnels") if isinstance(data, dict) else data
    for t in ts or []:
        if isinstance(t, dict):
            iid = "%s:%s" % (t.get("tunnelCode"), len(_dc_tun.get_children("")))
            _dc_tun.insert("", "end", iid=iid,
                           values=(region_label(t.get("regionCode")),
                                   country_label(t.get("countryCode")),
                                   t.get("activeNum", ""), t.get("activeIp", "")),
                           tags=(str(t.get("tunnelCode") or ""),))
    _dc_tun_status.config(text="t?neis=%d" % len(_dc_tun.get_children("")))


def _dc_show_mode(data):
    if isinstance(data, dict):
        mode = data.get("dpnMode") or data.get("mode") or "?"
        tunnel = data.get("fullTunnel") or data.get("smartTunnel") or "?"
        _dc_mode.config(text="Modo DPN: %s" % mode)
        _dc_tunnel_label.config(text="T?nel: %s" % tunnel)


def _dc_show_access(data):
    for iid in _dc_access.get_children(""):
        _dc_access.delete(iid)
    if not isinstance(data, dict):
        return
    for bucket in ("online", "offline"):
        for x in data.get(bucket) or []:
            if isinstance(x, dict):
                _dc_access.insert("", "end", values=(
                    bucket, x.get("mac", ""), x.get("name", ""),
                    x.get("ip") or x.get("lan_ip", ""),
                    x.get("routeMode") or x.get("mode", "")))


def _dc_read(op):
    if not _dc_sel_phys:
        messagebox.showinfo("DPN Control", "Selecione uma unidade.")
        return
    if not _dc_controller_online():
        _dc_controller_tick()
        messagebox.showinfo("DPN Control", "Controller OFFLINE. Clique em Iniciar controller.")
        return
    cid = _agent_command("read", physical_dpn_id=_dc_sel_phys, op=op)
    if not cid:
        return
    done = os.path.join(_dpn_state_dir(), "agent_commands_done", cid + ".json")
    def wait():
        for _ in range(60):
            r = _dc_load_json(done, None)
            if isinstance(r, dict):
                if r.get("status") == "ok":
                    data = r.get("data")
                    if op == "LIST_DPN_TUNNELS":
                        janela.after(0, lambda d=data: _dc_show_tunnels(d))
                    elif op == "GET_DPN_MODE":
                        janela.after(0, lambda d=data: _dc_show_mode(d))
                    elif op == "LIST_DPN_ACCESS_CONTROL":
                        janela.after(0, lambda d=data: _dc_show_access(d))
                else:
                    janela.after(0, lambda e=r.get("error"): messagebox.showerror(
                        "DPN Control", "Falha na leitura: %s" % e))
                return
            time.sleep(0.5)
        janela.after(0, lambda: messagebox.showwarning("DPN Control", "Controller sem resposta."))
    threading.Thread(target=wait, daemon=True).start()


def _dc_selected_tunnel():
    sel = _dc_tun.selection()
    if not sel:
        return None, ""
    vals = _dc_tun.item(sel[0], "values")
    code = ""
    for t in (_dc_tun.item(sel[0], "tags") or []):
        code = str(t)
    return code, (vals[3] if len(vals) > 3 else "")


def _dc_mutation(action, code, ip=""):
    if not code:
        messagebox.showinfo("DPN Control", "Selecione um t?nel.")
        return
    if not messagebox.askyesno("DPN Control", "%s em %s?" % (action, _dc_sel_phys)):
        return
    fields = {"physical_dpn_id": _dc_sel_phys, "tunnel_code": code}
    if action == "switch_node":
        fields["current_ip"] = ip
    cid = _agent_command(action, **fields)
    if not cid:
        return
    _dc_set_last("%s %s" % (action, code))
    done = os.path.join(_dpn_state_dir(), "agent_commands_done", cid + ".json")
    def wait():
        for _ in range(100):
            r = _dc_load_json(done, None)
            if isinstance(r, dict):
                if r.get("status") == "ok":
                    janela.after(0, lambda: _dc_tun_status.config(
                        text="%s %s: OK" % (action, code)))
                    janela.after(0, _dc_history_refresh)
                else:
                    janela.after(0, lambda e=r.get("error"): messagebox.showerror(
                        "DPN Control", "%s: %s" % (action, e)))
                return
            time.sleep(0.5)
    threading.Thread(target=wait, daemon=True).start()


def _dc_switch_selected():
    code, ip = _dc_selected_tunnel()
    _dc_mutation("switch_node", code, ip)


def _dc_refresh_selected():
    code, _ip = _dc_selected_tunnel()
    _dc_mutation("refresh_route_set", code)


def _dc_history_refresh():
    try:
        base = os.path.join(_dpn_state_dir(), "agent_commands_done")
        files = [os.path.join(base, n) for n in os.listdir(base) if n.endswith(".json")]
        files.sort(key=os.path.getmtime, reverse=True)
        for iid in _dc_history.get_children(""):
            _dc_history.delete(iid)
        for path in files[:20]:
            r = _dc_load_json(path, {})
            if not isinstance(r, dict):
                continue
            when = time.strftime("%d/%m %H:%M:%S", time.localtime(float(r.get("at") or 0)))
            _dc_history.insert("", "end", values=(
                when, str(r.get("physical_dpn_id") or _dc_sel_group),
                str(r.get("action") or ""), str(r.get("status") or "")))
    except Exception:
        pass


_dc_list.bind("<<ListboxSelect>>", _dc_on_select)


def _dc_loop():
    while True:
        try:
            janela.after(0, _dc_refresh_units)
            janela.after(0, _dc_history_refresh)
            janela.after(0, _dc_controller_tick)
            _st = _dc_load_json(_dc_state_path(), {})
            if isinstance(_st, dict):
                janela.after(0, lambda st=_st: _dpn_apply_status(
                    st, _dc_coord_tree, _dc_coord_label))
        except Exception:
            pass
        time.sleep(2.0)


threading.Thread(target=_dc_loop, daemon=True).start()

_agent_autostart_attempted = False


def _agent_bootstrap():
    """Start the controller once on app startup when no controller is online."""
    global _agent_autostart_attempted
    if _agent_autostart_attempted:
        return
    _agent_autostart_attempted = True
    try:
        if _dc_controller_online():
            _dc_controller_tick()
            return
        _agent_start_controller()
    except Exception:
        pass


try:
    janela.after(1800, _agent_bootstrap)
except Exception:
    pass

# ============ SCAN INICIAL DE REDE (1-254) ============
# ============ SCAN INICIAL DE REDE (1-254) ============

def scan_inicial_rede():
    """Varre toda a faixa 192.168.1.1-254 (porta 5555) ao iniciar,
    conecta os devices encontrados via adb e atualiza a lista."""
    global last_devices_cache
    print("[SCAN] Varrendo faixa 192.168.1.1-254 :5555...")
    ips = [f"192.168.1.{i}" for i in range(1, 255)]
    scanner = connect.NetworkScanner(max_threads=120, timeout=0.6)
    t0 = time.time()
    try:
        res = scanner.scan_ips(ips, 5555, lambda m: print(f"[SCAN] {m}"))
    except Exception as e:
        print(f"[SCAN] Erro: {e}")
        res = []
    dur = time.time() - t0
    print(f"[SCAN] Faixa concluída em {dur:.1f}s.")

    # último recurso: adb devices pega o que já está conectado
    devs = listar_dispositivos_safe()
    if devs is None:
        devs = list(last_devices_cache)
    print(f"[SCAN] {len(devs)} devices disponíveis na rede.")

    # ativa dispositivos que o scan conectou mas adb devices ainda não refletiu
    for r in res or []:
        ip = r.split(" ")[1] if len(r.split(" ")) > 1 else ""
        if ip and ip not in devs and ip.endswith(":5555"):
            devs.append(ip)
    last_devices_cache = sorted(set(devs))
    janela.after(0, lambda: atualizar_lista_ui(use_cache=True))


def iniciar_com_sucesso(func):
    """Chama a função em thread e mostra mensagem de erro se der problema"""
    try:
        func()
    except Exception as e:
        print(f"[ERRO] Falha na inicialização: {e}")


# Inicialização
atualizar_combo_grupos()
force_reload_groups()
atualizar_lista()
threading.Thread(target=monitor_devices_loop, daemon=True).start()
threading.Thread(target=iniciar_com_sucesso, args=(scan_inicial_rede,), daemon=True).start()

def _ui_autostart_e38b():
    """Optional test hook: start one device through the normal interface worker."""
    req = os.environ.get("SLN_UI_AUTOSTART_DEVICE", "").strip()
    if not req:
        return
    prof = os.environ.get("SLN_UI_AUTOSTART_PROFILE", "").strip()
    if prof in PROFILE_OVERLAYS:
        combo_profile.set(prof)
        profile_flag.update(name=prof)
        print(f"[UI][AUTOSTART] profile={prof}")
    apps_req = [x.strip() for x in os.environ.get("SLN_UI_AUTOSTART_APPS", "").split(",") if x.strip()]
    if apps_req:
        for k in app_vars:
            app_vars[k].set(k in apps_req)
        print(f"[UI][AUTOSTART] apps={apps_req}")
    cycles_req = os.environ.get("SLN_UI_AUTOSTART_CYCLES", "").strip()
    if cycles_req:
        spin_cycles.delete(0, "end")
        spin_cycles.insert(0, cycles_req)
        print(f"[UI][AUTOSTART] cycles={cycles_req}")

    def pick():
        for i in range(lista_dispositivos.size()):
            text = lista_dispositivos.get(i)
            if req in text and "Nenhum" not in text:
                lista_dispositivos.selection_clear(0, tk.END)
                lista_dispositivos.selection_set(i)
                print(f"[UI][AUTOSTART] device={req} starting")
                iniciar_bot()
                return
        janela.after(2000, pick)

    janela.after(3000, pick)


janela.after(1000, _ui_autostart_e38b)
janela.mainloop()