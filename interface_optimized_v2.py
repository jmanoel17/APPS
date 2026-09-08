"""
interface_optimized_v2.py — Versão otimizada para 150+ dispositivos simultâneos
Changelog vs interface_optimized.py:
  - Semáforo para limitar starts simultâneos (max 10)
  - Staggered startup melhorado (3s entre cada bot)
  - get_running_devices() otimizado (early-exit, set lookup)
  - Monitor adaptativo (15s/30s/60s baseado no nº de devices)
  - UI incremental (só redesenha se dados mudaram)
  - show_cmd_window/hide_cmd_window implementados
  - SCRIPTS_MAP atualizado para main_market_v2.py
"""

import tkinter as tk
import sys
from tkinter import messagebox, simpledialog, ttk, scrolledtext
import subprocess
import threading
import os
import json
import psutil
import time
import connect  # Requires connect.py in the same directory
import ctypes
import hashlib

# Suppress Windows Error Reporting dialogs (prevents popups on crash)
if os.name == 'nt':
    try:
        ctypes.windll.kernel32.SetErrorMode(0x0002 | 0x8000)
    except:
        pass

# ==================== CONFIGURAÇÃO GERAL ====================
SCRIPT_DIR = os.getcwd()

# Se não encontrar os scripts no diretório atual (caso esteja rodando do dist/), tenta subir um nível
if not os.path.exists(os.path.join(SCRIPT_DIR, "main_market_v2.py")):
    parent_dir = os.path.dirname(SCRIPT_DIR)
    if os.path.exists(os.path.join(parent_dir, "main_market_v2.py")):
        print(f"[INFO] Scripts não encontrados em {SCRIPT_DIR}. Usando diretório pai: {parent_dir}")
        SCRIPT_DIR = parent_dir
    else:
        # Fallback: tenta encontrar main_optimized.py
        if not os.path.exists(os.path.join(SCRIPT_DIR, "main_optimized.py")):
            parent_dir = os.path.dirname(SCRIPT_DIR)
            if os.path.exists(os.path.join(parent_dir, "main_optimized.py")):
                SCRIPT_DIR = parent_dir
            else:
                print(f"[ERRO] Scripts principais não encontrados em {SCRIPT_DIR} nem no pai.")

SCRIPTS_MAP = {
    "MarketFrases V2 (main_market_v2.py)": "main_market_v2.py",
    "MarketFrases (main_market.py)": "main_market.py",
    "DropsBin (main_optimized.py)": "main_optimized.py",
    "MusicVip (mainmusicvip.py)": "mainmusicvip.py",
    "Oraculo (main_oraculo.py)": "main_oraculo.py"
}
DEFAULT_SCRIPT_NAME = "MarketFrases V2 (main_market_v2.py)"
GROUPS_FILE = os.path.join(SCRIPT_DIR, "groups.json")
SCAN_CONFIG_FILE = os.path.join(SCRIPT_DIR, "ip_ranges.json")
PAUSED_FILE = os.path.join(SCRIPT_DIR, "paused.json")

# Semáforo para limitar starts simultâneos (max 10 por vez)
START_SEMAPHORE = threading.Semaphore(10)

print(f"[INFO] Diretório de trabalho: {SCRIPT_DIR}")

# ==================== TAB 1: BOTS ====================

# Rastreamento de processos ativos
processos_ativos = {}  # {device_id: subprocess.Popen OR psutil.Process}
paused_devices = set() # {device_id}
# Caches globais para evitar processamento na thread principal
last_groups_cache = {}
last_devices_cache = []
last_running_cache = set()
# Hash do último estado da UI para evitar redesenhos desnecessários
_last_ui_hash = ""

def load_paused_state():
    """Carrega estado de pausa do arquivo"""
    global paused_devices
    if os.path.exists(PAUSED_FILE):
        try:
            with open(PAUSED_FILE, 'r') as f:
                paused_devices = set(json.load(f))
        except:
            paused_devices = set()

def save_paused_state():
    """Salva estado de pausa no arquivo"""
    try:
        with open(PAUSED_FILE, 'w') as f:
            json.dump(list(paused_devices), f)
    except Exception as e:
        print(f"[ERRO] Falha ao salvar estado de pausa: {e}")

# Carrega estado inicial
load_paused_state()

def load_groups_cached():
    """Carrega grupos do cache ou do arquivo se necessário"""
    global last_groups_cache
    if not last_groups_cache and os.path.exists(GROUPS_FILE):
         force_reload_groups()
    return last_groups_cache

def force_reload_groups():
    """Lê do disco e atualiza cache"""
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
    """Salva grupos no arquivo JSON e atualiza cache"""
    global last_groups_cache
    last_groups_cache = groups
    try:
        with open(GROUPS_FILE, 'w', encoding='utf-8') as f:
            json.dump(groups, f, indent=2, ensure_ascii=False)
    except Exception as e:
        messagebox.showerror("Erro", f"Erro ao salvar grupos:\n{e}")

def create_group():
    """Cria um novo grupo"""
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
    """Deleta o grupo selecionado"""
    grupo_selecionado = combo_grupos.get()
    if not grupo_selecionado or grupo_selecionado == "Todos os Dispositivos":
        messagebox.showwarning("Aviso", "Selecione um grupo para deletar")
        return
    
    if messagebox.askyesno("Confirmar", f"Deletar grupo '{grupo_selecionado}'?"):
        groups = load_groups_cached()
        if grupo_selecionado in groups:
            del groups[grupo_selecionado]
            save_groups(groups)
            atualizar_combo_grupos()
            combo_grupos.set("Todos os Dispositivos")
            atualizar_lista_thread_safe()
            messagebox.showinfo("Sucesso", f"Grupo '{grupo_selecionado}' deletado!")

def add_to_group():
    """Adiciona dispositivos selecionados ao grupo"""
    grupo_selecionado = combo_grupos.get()
    if not grupo_selecionado or grupo_selecionado == "Todos os Dispositivos":
        messagebox.showwarning("Aviso", "Selecione um grupo primeiro")
        return
    
    selecionados = lista_dispositivos.curselection()
    if not selecionados:
        messagebox.showinfo("Aviso", "Selecione ao menos um dispositivo")
        return
    
    groups = load_groups_cached()
    devices_to_add = []
    
    for index in selecionados:
        entry = lista_dispositivos.get(index)
        if "Nenhum dispositivo" in entry:
            continue
        try:
            parts = entry.split(" ", 1)[1]
            device_id = parts.split(" [")[0]
            devices_to_add.append(device_id)
        except:
            continue
    
    for device in devices_to_add:
        if device not in groups[grupo_selecionado]:
            groups[grupo_selecionado].append(device)
    
    save_groups(groups)
    atualizar_lista_thread_safe(use_cache=True)
    messagebox.showinfo("Sucesso", f"{len(devices_to_add)} dispositivo(s) adicionado(s) ao grupo '{grupo_selecionado}'")

def remove_from_group():
    """Remove dispositivos selecionados do grupo"""
    grupo_selecionado = combo_grupos.get()
    if not grupo_selecionado or grupo_selecionado == "Todos os Dispositivos":
        messagebox.showwarning("Aviso", "Selecione um grupo primeiro")
        return
    
    selecionados = lista_dispositivos.curselection()
    if not selecionados:
        messagebox.showinfo("Aviso", "Selecione ao menos um dispositivo")
        return
    
    groups = load_groups_cached()
    devices_to_remove = []
    
    for index in selecionados:
        entry = lista_dispositivos.get(index)
        if "Nenhum dispositivo" in entry:
            continue
        try:
            parts = entry.split(" ", 1)[1]
            device_id = parts.split(" [")[0]
            devices_to_remove.append(device_id)
        except:
            continue
    
    for device in devices_to_remove:
        if device in groups[grupo_selecionado]:
            groups[grupo_selecionado].remove(device)
    
    save_groups(groups)
    atualizar_lista_thread_safe(use_cache=True)
    messagebox.showinfo("Sucesso", f"{len(devices_to_remove)} dispositivo(s) removido(s) do grupo '{grupo_selecionado}'")

def atualizar_combo_grupos():
    """Atualiza a lista de grupos no combobox"""
    groups = load_groups_cached()
    valores = ["Todos os Dispositivos"] + list(groups.keys())
    combo_grupos['values'] = valores
    if combo_grupos.get() not in valores:
        combo_grupos.set("Todos os Dispositivos")

def run_script(device_id):
    """Executa o script principal em modo headless (sem janela)"""
    if device_id in processos_ativos:
        print(f"[AVISO] Bot já está rodando para o dispositivo {device_id}. Ignorando.")
        return

    selected_name = combo_scripts.get()
    script_filename = SCRIPTS_MAP.get(selected_name, "main_market_v2.py")
    script_path = os.path.join(SCRIPT_DIR, script_filename)
    
    # Cria diretório de logs se não existir
    log_dir = os.path.join(SCRIPT_DIR, "logs")
    os.makedirs(log_dir, exist_ok=True)
    safe_id = device_id.replace(":", "_").replace(".", "_")
    log_file = os.path.join(log_dir, f"{safe_id}.log")

    def worker():
        # Semáforo limita starts simultâneos (max 10)
        with START_SEMAPHORE:
            try:
                if not os.path.isfile(script_path):
                    print(f"[ERRO] Script não encontrado: {script_path}")
                    return

                cmd = ["python", script_path, device_id, "0"]
                
                print(f"[DEBUG] Executando comando ({selected_name}): {' '.join(cmd)}")
                print(f"[DEBUG] Log redirecionado para: {log_file}")
                
                creationflags = 0
                if os.name == 'nt':
                    creationflags = subprocess.CREATE_NO_WINDOW
                
                f_log = open(log_file, "a", encoding='utf-8')
                try:
                    f_log.write(f"\n{'='*40}\n[INIT] Iniciando {device_id} em {time.strftime('%Y-%m-%d %H:%M:%S')}\n{'='*40}\n")
                    f_log.flush()
                    
                    process = subprocess.Popen(
                        cmd, 
                        stdout=f_log, 
                        stderr=subprocess.STDOUT,
                        creationflags=creationflags,
                        cwd=SCRIPT_DIR,
                        text=True
                    )
                    
                    processos_ativos[device_id] = process
                    
                    global last_running_cache
                    last_running_cache.add(device_id)
                    janela.after(0, lambda: atualizar_lista_ui(use_cache=True))
                    
                    process.wait()
                    f_log.write(f"\n[FINISH] Bot {device_id} finalizado com código {process.returncode}\n")
                finally:
                    f_log.close()
                
            except Exception as e:
                msg = f"Erro ao iniciar bot para {device_id}:\n{e}"
                print(f"[ERRO WORKER] {msg}")

    threading.Thread(target=worker, daemon=True).start()

def stop_script(device_id):
    """Para o script do dispositivo especificado"""
    if device_id in processos_ativos:
        try:
            process = processos_ativos[device_id]
            process.terminate()
            del processos_ativos[device_id]
            if device_id in last_running_cache:
                last_running_cache.remove(device_id)
            if device_id in paused_devices:
                paused_devices.remove(device_id)
            print(f"[INFO] Bot parado para dispositivo: {device_id}")
            janela.after(0, lambda: atualizar_lista_ui(use_cache=True))
        except Exception as e:
            print(f"[ERRO] Falha ao parar bot {device_id}: {e}")
    else:
        print(f"[AVISO] Nenhum processo ativo encontrado para {device_id}")

def toggle_pause_script(device_id):
    """Pausa ou resume o script do dispositivo"""
    if device_id not in processos_ativos:
        return
        
    try:
        proc_obj = processos_ativos[device_id]
        p = psutil.Process(proc_obj.pid)
        
        if device_id in paused_devices:
            p.resume()
            paused_devices.remove(device_id)
            save_paused_state()
            print(f"[INFO] Bot RESUMIDO: {device_id}")
        else:
            p.suspend()
            paused_devices.add(device_id)
            save_paused_state()
            print(f"[INFO] Bot PAUSADO: {device_id}")
            
        janela.after(0, lambda: atualizar_lista_ui(use_cache=True))
    except Exception as e:
        print(f"[ERRO] Falha ao pausar/resumir {device_id}: {e}")

def toggle_pause_selection():
    """Alterna pausa para seleção"""
    selecionados = lista_dispositivos.curselection()
    if not selecionados:
        return
        
    for index in selecionados:
        try:
            entry = lista_dispositivos.get(index)
            if "Nenhum" in entry: continue
            parts = entry.split(" ", 1)[1]
            device_id = parts.split(" [")[0]
            toggle_pause_script(device_id)
        except: pass

def toggle_pause_all():
    """Alterna pausa para todos"""
    if not processos_ativos:
        return
        
    total = len(processos_ativos)
    pausados = len([d for d in processos_ativos if d in paused_devices])
    
    should_pause = True
    if pausados >= total / 2:
        should_pause = False
        
    for device_id in processos_ativos:
        if should_pause:
            if device_id not in paused_devices:
                toggle_pause_script(device_id)
        else:
            if device_id in paused_devices:
                toggle_pause_script(device_id)

def stop_all_scripts():
    """Para todos os scripts ativos"""
    if not processos_ativos:
        messagebox.showinfo("Aviso", "Nenhum bot rodando no momento.")
        return

    if not messagebox.askyesno("Confirmar", f"Deseja parar todos os {len(processos_ativos)} bots?"):
        return

    count = 0
    for device_id in list(processos_ativos.keys()):
        stop_script(device_id)
        count += 1
    
    messagebox.showinfo("Sucesso", f"{count} bots parados.")

def start_api_server():
    """Inicia o servidor de IA (aiMain.py) em uma nova janela"""
    script_path = os.path.join(SCRIPT_DIR, "aiMain.py")
    if not os.path.isfile(script_path):
        messagebox.showerror("Erro", f"Script não encontrado: {script_path}")
        return

    try:
        cmd = f'start "API Server - aiMain" cmd /k python "{script_path}"'
        subprocess.Popen(cmd, shell=True)
        messagebox.showinfo("Sucesso", "Servidor de IA iniciado!\nVerifique a nova janela do CMD.")
    except Exception as e:
        messagebox.showerror("Erro", f"Falha ao iniciar servidor: {e}")

def show_cmd_window():
    """Mostra janelas CMD dos bots (Windows only)"""
    if os.name != 'nt':
        messagebox.showinfo("Aviso", "Esta funcionalidade só está disponível no Windows.")
        return
    
    count = 0
    for device_id, process in processos_ativos.items():
        try:
            pid = process.pid if hasattr(process, 'pid') else None
            if pid:
                # Usa ctypes para mostrar a janela do processo
                import ctypes
                from ctypes import wintypes
                
                def callback(hwnd, pid_target):
                    _, found_pid = ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(ctypes.c_ulong(0)))
                    tid = ctypes.c_ulong(0)
                    p = ctypes.c_ulong(0)
                    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
                    if p.value == pid_target:
                        ctypes.windll.user32.ShowWindow(hwnd, 5)  # SW_SHOW
                        count_holder[0] += 1
                    return True
                
                count_holder = [0]
                WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
                # This approach is simplified; for CREATE_NO_WINDOW processes there's no window to show
                count += 1
        except:
            pass
    
    messagebox.showinfo("Info", f"Os bots estão rodando em modo headless (CREATE_NO_WINDOW).\nPara ver os logs, verifique a pasta 'logs/'.\n\nBots ativos: {len(processos_ativos)}")

def hide_cmd_window():
    """Esconde janelas CMD dos bots (já rodam em headless por padrão)"""
    messagebox.showinfo("Info", f"Os bots já estão rodando em modo headless (sem janela).\nTotal de bots ativos: {len(processos_ativos)}")

def get_running_devices():
    """Retorna um dict com {device_id: pid} — OTIMIZADO para 150+ devices"""
    running_devices_map = {}
    target_scripts = {"main_market_v2.py", "main_market.py", "main_optimized.py", "mainmusicvip.py"}
    
    try:
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                # Early-exit: pula tudo que não é Python
                proc_name = proc.info.get('name', '')
                if not proc_name or 'python' not in proc_name.lower():
                    continue
                    
                cmdline = proc.info.get('cmdline')
                if not cmdline or len(cmdline) < 3:
                    continue
                
                # Verifica se é um dos nossos scripts
                script_found = False
                for arg in cmdline:
                    basename = os.path.basename(arg) if arg else ''
                    if basename in target_scripts:
                        script_found = True
                        break
                
                if not script_found:
                    continue
                
                # O device_id geralmente é o 3º argumento (depois de python e script)
                # Procura argumento que pareça um device_id (IP:porta ou serial)
                for arg in cmdline:
                    if arg and len(arg) > 4 and ('.' in arg or ':' in arg):
                        basename = os.path.basename(arg)
                        if basename not in target_scripts and 'python' not in arg.lower():
                            running_devices_map[arg] = proc.info['pid']
                            break
                            
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass
    except Exception as e:
        print(f"[ERRO] Falha ao escanear processos: {e}")
        
    return running_devices_map

def check_active_processes():
    """Limpa dicionário de processos locais"""
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
        except:
             to_remove.append(device_id)

    for device_id in to_remove:
        del processos_ativos[device_id]
        if device_id in paused_devices:
            paused_devices.remove(device_id)
    
    if to_remove:
        save_paused_state()

def _compute_ui_hash(dispositivos, active_devices):
    """Computa hash rápido do estado da UI para evitar redesenhos desnecessários"""
    data = f"{sorted(dispositivos)}|{sorted(active_devices)}|{sorted(paused_devices)}|{combo_grupos.get()}"
    return hashlib.md5(data.encode()).hexdigest()

def monitor_devices_loop():
    """Monitora dispositivos em background — ADAPTATIVO baseado no nº de devices"""
    global last_running_cache, last_devices_cache
    
    force_reload_groups()
    
    while True:
        try:
            # 1. Busca dispositivos ADB (I/O)
            dispositivos_adb = listar_dispositivos_safe()
            last_devices_cache = dispositivos_adb
            
            # 2. Busca processos rodando (CPU/Syscall intensive)
            running_map = get_running_devices()
            last_running_cache = set(running_map.keys())
            
            # RE-ADOPTION LOGIC: Se está rodando mas não temos controle, adotar.
            for dev_id, pid in running_map.items():
                if dev_id not in processos_ativos:
                    try:
                        print(f"[INFO] Re-adotando controle do bot: {dev_id} (PID: {pid})")
                        proc_obj = psutil.Process(pid)
                        processos_ativos[dev_id] = proc_obj
                    except Exception as e:
                        print(f"[ERRO] Falha ao re-adotar {dev_id}: {e}")
            
            # 3. Limpa processos locais
            check_active_processes()
            
            # 4. Atualiza grupos (Disk I/O)
            force_reload_groups()
            
            # 5. UI Update seguro — só se dados mudaram
            janela.after(0, lambda: atualizar_lista_ui(use_cache=True))
            
        except Exception as e:
            print(f"[ERRO Monitor]: {e}")
        
        # Monitor adaptativo: intervalo baseado no nº de dispositivos
        num_devices = len(last_devices_cache)
        if num_devices > 100:
            sleep_interval = 60  # 100+ devices: a cada 60s
        elif num_devices > 50:
            sleep_interval = 30  # 50-100 devices: a cada 30s
        else:
            sleep_interval = 15  # <50 devices: a cada 15s
        
        time.sleep(sleep_interval)

def listar_dispositivos_safe():
    """Versão segura de listar dispositivos para chamar da thread"""
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        creationflags = 0x08000000 if os.name == 'nt' else 0
        
        result = subprocess.check_output(
            "adb devices", 
            shell=True, 
            text=True, 
            timeout=5, 
            startupinfo=si, 
            creationflags=creationflags
        )
        lines = result.strip().split("\n")[1:]
        devs = [line.split("\t")[0] for line in lines if "device" in line]
        return devs
    except Exception:
        return []

def listar_dispositivos():
    return listar_dispositivos_safe()

def atualizar_lista_ui(dispositivos=None, active_devices=None, use_cache=False):
    """
    Atualiza a UI — INCREMENTAL: só redesenha se dados mudaram.
    """
    global last_devices_cache, last_running_cache, _last_ui_hash
    
    try:
        if dispositivos is None:
            dispositivos = last_devices_cache
        
        if active_devices is None:
            active_devices = last_running_cache
        
        # Incremental update: verifica se dados mudaram
        new_hash = _compute_ui_hash(dispositivos, active_devices)
        if new_hash == _last_ui_hash:
            return  # Nada mudou, skip redesenho
        _last_ui_hash = new_hash
        
        groups = load_groups_cached()

        # Lookup reverso de grupos (O(1) no loop)
        device_to_group = {}
        for gname, gdevs in groups.items():
            for d_item in gdevs:
                device_to_group[d_item] = gname

        # Salva seleção
        selected_devices = set()
        for i in lista_dispositivos.curselection():
            try:
                entry = lista_dispositivos.get(i)
                if "Nenhum" not in entry:
                    parts = entry.split(" ", 1)[1]
                    d_id = parts.split(" [")[0]
                    selected_devices.add(d_id)
            except: pass
        
        lista_dispositivos.delete(0, tk.END)
        grupo_filtro = combo_grupos.get()
        
        if not dispositivos:
            lista_dispositivos.insert(tk.END, "Nenhum dispositivo encontrado (ou aguardando scan...)")
            lista_dispositivos.itemconfigure(tk.END, fg="gray")
        else:
            dispositivos_filtrados = dispositivos
            
            if grupo_filtro != "Todos os Dispositivos" and grupo_filtro in groups:
                 dispositivos_filtrados = [d_item for d_item in dispositivos if d_item in groups[grupo_filtro]]
            
            if not dispositivos_filtrados and grupo_filtro != "Todos os Dispositivos":
                lista_dispositivos.insert(tk.END, f"Nenhum dispositivo no grupo '{grupo_filtro}'")
                lista_dispositivos.itemconfigure(tk.END, fg="gray")
            else:
                for i, d_item in enumerate(dispositivos_filtrados, start=1):
                    is_active = d_item in active_devices
                    is_paused = d_item in paused_devices
                    
                    status_text = " [PARADO]"
                    if is_active:
                        status_text = " [RODANDO]"
                    if is_paused:
                        status_text = " [PAUSADO]"
                    
                    grupo = device_to_group.get(d_item, None)
                    grupo_tag = f" [{grupo}]" if grupo and grupo_filtro == "Todos os Dispositivos" else ""
                    
                    texto_item = f"{i}. {d_item}{status_text}{grupo_tag}"
                    
                    lista_dispositivos.insert(tk.END, texto_item)
                    
                    if is_paused:
                        lista_dispositivos.itemconfigure(i-1, bg="#fff3cd", fg="#856404")
                    elif is_active:
                        lista_dispositivos.itemconfigure(i-1, bg="#d4edda", fg="#155724")
                    else:
                        lista_dispositivos.itemconfigure(i-1, bg="#f8d7da", fg="#721c24")
                    
                    if d_item in selected_devices:
                        lista_dispositivos.selection_set(i-1)

        # Atualizar label de status
        total_devices = len(dispositivos)
        total_running = len([d_item for d_item in dispositivos if d_item in active_devices])
        total_paused = len([d_item for d_item in dispositivos if d_item in paused_devices])
        lbl_status.config(text=f"📊 Total: {total_devices} | Rodando: {total_running} | Pausados: {total_paused}")

    except Exception as e:
        print(f"Erro ao atualizar UI: {e}")

def atualizar_lista_thread_safe(use_cache=True):
    """Wrapper Helper"""
    janela.after(0, lambda: atualizar_lista_ui(use_cache=use_cache))

def atualizar_lista():
    """Botão Atualizar (Força Scan)"""
    def worker():
        global last_devices_cache, last_running_cache, _last_ui_hash
        devs = listar_dispositivos_safe()
        active = get_running_devices()
        last_devices_cache = devs
        last_running_cache = set(active.keys())
        force_reload_groups()
        _last_ui_hash = ""  # Força redesenho
        janela.after(0, lambda: atualizar_lista_ui(use_cache=True))
    
    threading.Thread(target=worker, daemon=True).start()

def iniciar_bot():
    """Inicia o bot nos dispositivos selecionados"""
    selecionados = lista_dispositivos.curselection()
    if not selecionados:
        messagebox.showinfo("Aviso", "Selecione ao menos um dispositivo")
        return

    for index in selecionados:
        entry = lista_dispositivos.get(index)
        if "Nenhum dispositivo" in entry:
            continue
        try:
            parts = entry.split(" ", 1)[1]
            device_id = parts.split(" [")[0]
            run_script(device_id)
        except:
            continue

def iniciar_todos_os_bots():
    """Inicia bots em todos os dispositivos — com staggered startup (3s entre cada)"""
    dispositivos = last_devices_cache
    if not dispositivos:
        dispositivos = listar_dispositivos_safe()
        if not dispositivos:
            messagebox.showinfo("Aviso", "Nenhum dispositivo encontrado. Verifique se estão conectados via ADB.")
            return

    def worker():
        count = 0
        for device_id in dispositivos:
            if device_id not in processos_ativos:
                run_script(device_id)
                count += 1
                # Staggered startup: 3s entre cada bot para evitar sobrecarga
                time.sleep(3)
        
        if count > 0:
            janela.after(0, lambda: messagebox.showinfo("Sucesso", f"{count} bot(s) iniciados com sucesso!"))
        else:
            janela.after(0, lambda: messagebox.showinfo("Aviso", "Todos os dispositivos já possuem bots rodando."))
    
    # Roda em thread para não travar a UI
    threading.Thread(target=worker, daemon=True).start()
    messagebox.showinfo("Info", f"Iniciando {len([d for d in dispositivos if d not in processos_ativos])} bot(s) em background...\nDelay de 3s entre cada start.")

def iniciar_grupo():
    grupo_selecionado = combo_grupos.get()
    if not grupo_selecionado or grupo_selecionado == "Todos os Dispositivos":
        messagebox.showwarning("Aviso", "Selecione um grupo específico")
        return
    groups = load_groups_cached()
    if grupo_selecionado not in groups:
        messagebox.showwarning("Aviso", "Grupo não encontrado")
        return
    dispositivos_grupo = groups[grupo_selecionado]
    dispositivos_conectados = last_devices_cache
    
    def worker():
        iniciados = 0
        for device_id in dispositivos_grupo:
            if device_id in dispositivos_conectados and device_id not in processos_ativos:
                run_script(device_id)
                iniciados += 1
                time.sleep(3)  # Staggered startup
        
        if iniciados == 0:
            janela.after(0, lambda: messagebox.showinfo("Aviso", f"Nenhum dispositivo do grupo '{grupo_selecionado}' está conectado ou todos já estão rodando"))
        else:
            janela.after(0, lambda: messagebox.showinfo("Sucesso", f"{iniciados} dispositivo(s) iniciado(s) do grupo '{grupo_selecionado}'"))
    
    threading.Thread(target=worker, daemon=True).start()

def parar_selecao():
    """Para bots dos dispositivos selecionados"""
    selecionados = lista_dispositivos.curselection()
    if not selecionados:
        messagebox.showinfo("Aviso", "Selecione ao menos um dispositivo para parar")
        return

    for index in selecionados:
        entry = lista_dispositivos.get(index)
        if "Nenhum dispositivo" in entry:
            continue
        try:
            parts = entry.split(" ", 1)[1]
            device_id = parts.split(" [")[0]
            stop_script(device_id)
        except:
            continue

# ==================== TAB 2: SCANNER ====================

class ScannerTab:
    def __init__(self, parent_frame):
        self.frame = parent_frame
        self.ip_ranges = []
        self.next_id = 1
        self.scanner = connect.NetworkScanner(max_threads=50, timeout=1.0)
        
        self._build_ui()
        self._load_ranges()

    def _build_ui(self):
        # === Enable Network Port
        frame_port = ttk.LabelFrame(self.frame, text="Step 1 - Enable Network Port")
        frame_port.pack(fill="x", padx=10, pady=5)

        ttk.Label(frame_port, text="Porta padrão:").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        self.entry_port = ttk.Entry(frame_port, width=8)
        self.entry_port.insert(0, "5555")
        self.entry_port.grid(row=0, column=1, padx=5, pady=5, sticky="w")
        self.btn_enable = ttk.Button(frame_port, text="Enable", command=self.on_enable_port)
        self.btn_enable.grid(row=0, column=2, padx=5, pady=5, sticky="w")

        # === Scan Devices
        frame_scan = ttk.LabelFrame(self.frame, text="Step 3 - Scan Devices")
        frame_scan.pack(fill="x", padx=10, pady=5)

        ttk.Label(frame_scan, text="Oct 1").grid(row=0, column=0)
        ttk.Label(frame_scan, text="Oct 2").grid(row=0, column=1)
        ttk.Label(frame_scan, text="Oct 3 (Ini-Fim)").grid(row=0, column=2, columnspan=2)
        ttk.Label(frame_scan, text="Oct 4 (Ini-Fim)").grid(row=0, column=4, columnspan=2)

        self.e_o1 = ttk.Entry(frame_scan, width=5); self.e_o1.grid(row=1, column=0, padx=2)
        self.e_o2 = ttk.Entry(frame_scan, width=5); self.e_o2.grid(row=1, column=1, padx=2)
        self.e_o3_start = ttk.Entry(frame_scan, width=5); self.e_o3_start.grid(row=1, column=2, padx=1)
        self.e_o3_end = ttk.Entry(frame_scan, width=5); self.e_o3_end.grid(row=1, column=3, padx=1)
        self.e_o4_start = ttk.Entry(frame_scan, width=5); self.e_o4_start.grid(row=1, column=4, padx=1)
        self.e_o4_end = ttk.Entry(frame_scan, width=5); self.e_o4_end.grid(row=1, column=5, padx=1)

        self.e_o1.insert(0, "192"); self.e_o2.insert(0, "168")
        self.e_o3_start.insert(0, "1"); self.e_o3_end.insert(0, "1")
        self.e_o4_start.insert(0, "1"); self.e_o4_end.insert(0, "255")

        self.btn_add_range = ttk.Button(frame_scan, text="+ Add", command=self.on_add_range)
        self.btn_add_range.grid(row=1, column=6, padx=5)

        # === Actions
        frame_actions = ttk.Frame(self.frame)
        frame_actions.pack(side="bottom", fill="x", padx=10, pady=5)
        
        self.btn_scan_sel = ttk.Button(frame_actions, text="SCAN (Sel)", command=self.on_scan_selected)
        self.btn_scan_sel.pack(side="left", padx=5)
        self.btn_scan_all = ttk.Button(frame_actions, text="SCAN (Todos)", command=self.on_scan_all)
        self.btn_scan_all.pack(side="left", padx=5)
        ttk.Button(frame_actions, text="Remover", command=self.on_remove_selected).pack(side="left", padx=5)
        ttk.Button(frame_actions, text="Salvar", command=self._save_ranges).pack(side="right", padx=5)

        # === Treeview
        frame_list = ttk.LabelFrame(self.frame, text="Ranges Cadastrados")
        frame_list.pack(fill="both", expand=True, padx=10, pady=5)
        
        columns = ("id", "start_ip", "end_ip", "port")
        self.tree = ttk.Treeview(frame_list, columns=columns, show="headings", height=8)
        self.tree.heading("id", text="ID"); self.tree.column("id", width=30, anchor="center")
        self.tree.heading("start_ip", text="Start"); self.tree.column("start_ip", width=100, anchor="center")
        self.tree.heading("end_ip", text="End"); self.tree.column("end_ip", width=100, anchor="center")
        self.tree.heading("port", text="Port"); self.tree.column("port", width=50, anchor="center")
        self.tree.pack(side="left", fill="both", expand=True)
        
        sb = ttk.Scrollbar(frame_list, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscroll=sb.set)
        sb.pack(side="right", fill="y")

        # === Logs
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
        port = self.entry_port.get().strip()
        if port.isdigit():
            messagebox.showinfo("Config", f"Porta {port} definida (lógica local).")
        else:
            messagebox.showerror("Erro", "Porta inválida.")

    def _validate(self, v):
        if not v.isdigit() or not (0 <= int(v) <= 255):
             raise ValueError("Valor inválido (0-255)")
        return int(v)

    def on_add_range(self):
        try:
            o1 = self._validate(self.e_o1.get())
            o2 = self._validate(self.e_o2.get())
            o3s = self._validate(self.e_o3_start.get())
            o3e = self._validate(self.e_o3_end.get())
            o4s = self._validate(self.e_o4_start.get())
            o4e = self._validate(self.e_o4_end.get())
            port = int(self.entry_port.get())
        except ValueError as e:
            messagebox.showerror("Erro", str(e))
            return
        
        start_ip = f"{o1}.{o2}.{o3s}.{o4s}"
        end_ip = f"{o1}.{o2}.{o3e}.{o4e}"
        
        r = {
            "id": self.next_id,
            "start_ip": start_ip,
            "end_ip": end_ip,
            "port": port,
            "raw": [o1, o2, o3s, o3e, o4s, o4e]
        }
        self.ip_ranges.append(r)
        self.tree.insert("", "end", values=(r["id"], start_ip, end_ip, port))
        self.next_id += 1

    def on_remove_selected(self):
        sel = self.tree.selection()
        for item in sel:
            rid = int(self.tree.item(item, "values")[0])
            self.ip_ranges = [x for x in self.ip_ranges if x["id"] != rid]
            self.tree.delete(item)

    def _load_ranges(self):
        if os.path.exists(SCAN_CONFIG_FILE):
            try:
                with open(SCAN_CONFIG_FILE, 'r') as f:
                    data = json.load(f)
                for r in data:
                    r["id"] = self.next_id
                    self.ip_ranges.append(r)
                    self.tree.insert("", "end", values=(r["id"], r["start_ip"], r["end_ip"], r["port"]))
                    self.next_id += 1
            except Exception as e:
                print(f"[ERRO] Falha ao carregar ranges: {e}")

    def _save_ranges(self):
        try:
            with open(SCAN_CONFIG_FILE, 'w') as f:
                json.dump(self.ip_ranges, f, indent=2)
            messagebox.showinfo("Salvo", "Faixas salvas.")
        except Exception as e:
             messagebox.showerror("Erro", str(e))

    def generate_ips(self, r):
        if "raw" not in r: return [r["start_ip"]]
        o1, o2, o3s, o3e, o4s, o4e = r["raw"]
        ips = []
        for o3 in range(o3s, o3e+1):
            for o4 in range(o4s, o4e+1):
                ips.append(f"{o1}.{o2}.{o3}.{o4}")
        return ips

    def run_scan_thread(self, ranges):
        self.btn_scan_sel["state"] = "disabled"
        self.btn_scan_all["state"] = "disabled"
        self.log(f"--- Iniciando Scan em {len(ranges)} faixas ---")
        
        all_ips = []
        for r in ranges:
            all_ips.extend(self.generate_ips(r))
        
        self.scanner.scan_ips(all_ips, ranges[0]["port"] if ranges else 5555, self.log)
        
        self.log("--- Fim do Scan ---")
        self.btn_scan_sel["state"] = "normal"
        self.btn_scan_all["state"] = "normal"

    def on_scan_selected(self):
        sel = self.tree.selection()
        if not sel: return
        ranges = []
        for item in sel:
            rid = int(self.tree.item(item, "values")[0])
            ranges.extend([x for x in self.ip_ranges if x["id"] == rid])
        threading.Thread(target=self.run_scan_thread, args=(ranges,), daemon=True).start()

    def on_scan_all(self):
        if not self.ip_ranges: return
        threading.Thread(target=self.run_scan_thread, args=(self.ip_ranges,), daemon=True).start()


# ==================== INTERFACE PRINCIPAL ====================
janela = tk.Tk()
janela.title("Inspirations Bot Studio V2")
janela.geometry("600x800")

notebook = ttk.Notebook(janela)
notebook.pack(fill="both", expand=True)

# --- ABA BOTS ---
tab_bots = ttk.Frame(notebook)
notebook.add(tab_bots, text="🤖 Bots")

tk.Label(tab_bots, text="🚀 Gerenciador de Bots V2", font=("Arial", 14, "bold")).pack(pady=5)

# Status Bar
lbl_status = tk.Label(tab_bots, text="📊 Total: 0 | Rodando: 0 | Pausados: 0", font=("Arial", 9), fg="#666")
lbl_status.pack(pady=2)

# Selection Script
frame_script = tk.LabelFrame(tab_bots, text="⚙️ Configuração", padx=10, pady=5)
frame_script.pack(pady=5, padx=10, fill="x")
tk.Label(frame_script, text="Script:").pack(side=tk.LEFT)
combo_scripts = ttk.Combobox(frame_script, state="readonly", width=30)
combo_scripts['values'] = list(SCRIPTS_MAP.keys())
combo_scripts.set(DEFAULT_SCRIPT_NAME)
combo_scripts.pack(side=tk.LEFT, padx=5)

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
lista_dispositivos = tk.Listbox(frame_devs, selectmode=tk.MULTIPLE, width=60, height=15)
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

# --- ABA SCANNER ---
tab_scan = ttk.Frame(notebook)
notebook.add(tab_scan, text="📡 Network Scanner")

app_scan = ScannerTab(tab_scan)

# --- INICIALIZAÇÃO ---
atualizar_combo_grupos()
force_reload_groups()
atualizar_lista()
threading.Thread(target=monitor_devices_loop, daemon=True).start()

janela.mainloop()
