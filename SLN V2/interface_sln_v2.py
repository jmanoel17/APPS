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
import subprocess
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk, scrolledtext, Entry
import connect
import config_sln_v2 as CONFIG

# ==================== CONFIG ====================
SCRIPT_DIR = os.getcwd()
if not os.path.exists(os.path.join(SCRIPT_DIR, "main_sln_v2.py")):
    parent = os.path.dirname(SCRIPT_DIR)
    if os.path.exists(os.path.join(parent, "main_sln_v2.py")):
        SCRIPT_DIR = parent

SCRIPTS_MAP = {
    "SLN V2 (main_sln_v2.py)": "main_sln_v2.py",
}
DEFAULT_SCRIPT_NAME = "SLN V2 (main_sln_v2.py)"

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
    selected = combo_scripts.get()
    script_filename = SCRIPTS_MAP.get(selected, "main_sln_v2.py")
    script_path = os.path.join(SCRIPT_DIR, script_filename)
    log_dir = LOGS_DIR
    safe_id = device_id.replace(":", "_").replace(".", "_")
    log_file = os.path.join(log_dir, f"{safe_id}.log")

    def worker():
        # O semáforo controla APENAS o ARRANQUE (Popen + registro + pequena estabilização).
        # O process.wait() roda FORA do semáforo: a quantidade TOTAL de bots rodando não é
        # limitada por ele. Sem isso, o slot ficava preso por todo o tempo de vida do bot.
        f_log = None
        process = None
        try:
            if not os.path.isfile(script_path):
                print(f"[ERRO] Script não encontrado: {script_path}")
                return
            cmd = ["python", script_path, device_id, "0"]
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            # Perfil: injeta SLN_CONFIG_OVERLAY no filho (dev = sem overlay; benchmark = overlay).
            env = os.environ.copy()
            overlay = PROFILE_OVERLAYS.get(profile_flag.get("name"), None)
            if overlay:
                env["SLN_CONFIG_OVERLAY"] = overlay
            else:
                env.pop("SLN_CONFIG_OVERLAY", None)
            START_SEMAPHORE.acquire()
            try:
                f_log = open(log_file, "a", encoding='utf-8')
                f_log.write(f"\n{'='*40}\n[INIT] Iniciando {device_id} em {time.strftime('%Y-%m-%d %H:%M:%S')}\n{'='*40}\n")
                f_log.flush()
                process = subprocess.Popen(
                    cmd, stdout=f_log, stderr=subprocess.STDOUT,
                    creationflags=creationflags, cwd=SCRIPT_DIR, text=True, env=env
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
    if device_id in processos_ativos:
        try:
            proc = processos_ativos[device_id]
            proc.terminate()
            del processos_ativos[device_id]
            _fechar_console_log(device_id)
            last_running_cache.discard(device_id)
            paused_devices.discard(device_id)
            print(f"[INFO] Bot parado: {device_id}")
            janela.after(0, lambda: atualizar_lista_ui(use_cache=True))
        except Exception as e:
            print(f"[ERRO] Falha ao parar {device_id}: {e}")
    else:
        print(f"[AVISO] Nenhum processo ativo para {device_id}")

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
            last_devices_cache = devs
            running_map = get_running_devices()
            last_running_cache = set(running_map.keys())
            for dev_id, pid in running_map.items():
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
            "adb devices", shell=True, text=True, timeout=5,
            startupinfo=si, creationflags=creationflags
        )
        lines = result.strip().split("\n")[1:]
        return [line.split("\t")[0] for line in lines if "device" in line]
    except Exception:
        return []

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
        active = get_running_devices()
        last_devices_cache[:] = devs
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
        dispositivos = listar_dispositivos_safe()
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
    # adota na hora bots em execução que a interface não iniciou (outra sessão / USB)
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
    if messagebox.askyesno("Confirmar", f"Parar todos os {len(processos_ativos)} bots?"):
        for dev_id in list(processos_ativos.keys()):
            stop_script(dev_id)
        messagebox.showinfo("Sucesso", "Todos parados")

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
janela.geometry("600x800")

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

# Scanner tab
tab_scan = ttk.Frame(notebook)
notebook.add(tab_scan, text="📡 Network Scanner")
app_scan = ScannerTab(tab_scan)

# ==================== DPN TAB (CLIENTE) ====================
# A interface é CLIENTE: só lê state/dpn/status.json (snapshot do runner) e publica comandos em
# state/dpn/commands/. NUNCA consulta a DPN diretamente nem bloqueia a thread do Tkinter.
tab_dpn = ttk.Frame(notebook)
notebook.add(tab_dpn, text="🌐 DPN")

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


def _dpn_publish(action):
    sel = _dpn_tree.selection()
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
tk.Button(_dpn_bar, text="Iniciar Coordinator", command=_dpn_start_coordinator).pack(side="right", padx=3)
tk.Button(_dpn_bar, text="Abrir Logs", command=_dpn_open_logs).pack(side="right", padx=3)


def _dpn_apply_status(status):
    try:
        import dpn.ui_model as UI
        online = UI.coordinator_online(status, stale_s=8.0)
        _lbl_coord.config(text="Status Coordinator: %s" % ("ONLINE" if online else "OFFLINE"),
                          fg=("#2e7d32" if online else "#b71c1c"))
        for iid in _dpn_tree.get_children(""):
            _dpn_tree.delete(iid)
        for r in UI.tree_rows(status):
            parent = r.get("parent") or ""
            _dpn_tree.insert(parent, "end", iid=r["iid"], text=r["name"], values=(
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

janela.mainloop()