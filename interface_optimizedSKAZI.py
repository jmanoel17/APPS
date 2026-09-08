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

# Suppress Windows Error Reporting dialogs (prevents popups on crash)
if os.name == 'nt':
    try:
        ctypes.windll.kernel32.SetErrorMode(0x0002 | 0x8000)
    except:
        pass

# ==================== CONFIGURAÇÃO GERAL ====================
SCRIPT_DIR = os.getcwd()

# Se não encontrar os scripts no diretório atual (caso esteja rodando do dist/), tenta subir um nível
if not os.path.exists(os.path.join(SCRIPT_DIR, "main_optimized.py")):
    parent_dir = os.path.dirname(SCRIPT_DIR)
    if os.path.exists(os.path.join(parent_dir, "main_optimized.py")):
        print(f"[INFO] Scripts não encontrados em {SCRIPT_DIR}. Usando diretório pai: {parent_dir}")
        SCRIPT_DIR = parent_dir
    else:
        print(f"[ERRO] Scripts principais (main_optimized.py) não encontrados em {SCRIPT_DIR} nem no pai.")

SCRIPTS_MAP = {
    "DropsBin (main_optimized.py)": "main_optimized.py",
    "MusicVip (mainmusicvip.py)": "mainmusicvip.py",
    "Oraculo (main_oraculo.py)": "main_oraculo.py"
}
DEFAULT_SCRIPT_NAME = "DropsBin (main_optimized.py)"
GROUPS_FILE = os.path.join(SCRIPT_DIR, "groups.json")
SCAN_CONFIG_FILE = os.path.join(SCRIPT_DIR, "ip_ranges.json")
PAUSED_FILE = os.path.join(SCRIPT_DIR, "paused.json")

print(f"[INFO] Diretório de trabalho: {SCRIPT_DIR}")

# ==================== TAB 1: BOTS (Antigo interface_optimized.py) ====================

# Rastreamento de processos ativos
processos_ativos = {}  # {device_id: subprocess.Popen OR psutil.Process}
paused_devices = set() # {device_id}
# Caches globais para evitar processamento na thread principal
last_groups_cache = {}
last_devices_cache = []
last_running_cache = set()

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
    """Carrega grupos do cache ou do arquivo se necessário (mas tenta evitar I/O na UI)"""
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
    script_filename = SCRIPTS_MAP.get(selected_name, "main_optimized.py")
    script_path = os.path.join(SCRIPT_DIR, script_filename)
    
    # Cria diretório de logs se não existir
    log_dir = os.path.join(SCRIPT_DIR, "logs")
    os.makedirs(log_dir, exist_ok=True)
    # Sanitiza device_id para nome de arquivo (remove :)
    safe_id = device_id.replace(":", "_").replace(".", "_")
    log_file = os.path.join(log_dir, f"{safe_id}.log")

    def worker():
        try:
            if not os.path.isfile(script_path):
                print(f"[ERRO] Script não encontrado: {script_path}")
                return

            # Comando direto para o interpretador Python atual
            # Removemos 'start cmd /k' para evitar abrir janelas
            # Para EXE congelado, sys.executable aponta para o EXE. Usamos 'python' ou 'py' do sistema.
            cmd = ["python", script_path, device_id, "0"]
            
            print(f"[DEBUG] Executando comando ({selected_name}): {' '.join(cmd)}")
            print(f"[DEBUG] Log redirecionado para: {log_file}")
            
            # =================================================================================
            # CONFIGURAÇÃO DE JANELA (BACKGROUND vs CONSOLE)
            # Para DEBUG (Janela Preta): Use subprocess.CREATE_NEW_CONSOLE
            # Para BACKGROUND (Escondido): Use subprocess.CREATE_NO_WINDOW (no Windows) ou 0
            # =================================================================================
            
            creationflags = 0
            if os.name == 'nt':
                creationflags = subprocess.CREATE_NO_WINDOW
            
            # Executa o processo e mantém log aberto enquanto rodar
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
                
                # Atualiza cache visualmente na hora
                global last_running_cache
                last_running_cache.add(device_id)
                janela.after(0, lambda: atualizar_lista_ui(use_cache=True))
                
                # Espera o processo terminar para fechar o log
                process.wait()
                f_log.write(f"\n[FINISH] Bot {device_id} finalizado com código {process.returncode}\n")
            finally:
                f_log.close()
            
        except Exception as e:
            msg = f"Erro ao iniciar bot para {device_id}:\n{e}"
            print(f"[ERRO WORKER] {msg}")
            messagebox.showerror("Erro de Inicialização", msg)

    threading.Thread(target=worker, daemon=True).start()

def stop_script(device_id):
    """Para o script do dispositivo especificado"""
    if device_id in processos_ativos:
        try:
            process = processos_ativos[device_id]
            process.terminate()  # Tenta terminar graciosamente
            # process.kill()     # Se precisar forçar
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
            # RESUME
            p.resume()
            paused_devices.remove(device_id)
            save_paused_state()
            print(f"[INFO] Bot RESUMIDO: {device_id}")
        else:
            # PAUSE
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
    """Alterna pausa para todos (se a maioria estiver rodando, pausa; se a maioria estiver pausada, resume)"""
    if not processos_ativos:
        return
        
    # Heurística simples: se mais da metade estiver pausada, resume todos. Senão, pausa todos.
    total = len(processos_ativos)
    pausados = len([d for d in processos_ativos if d in paused_devices])
    
    should_pause = True
    if pausados >= total / 2:
        should_pause = False # Resume all
        
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
    # Cria lista cópia para iterar e deletar
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
        # Para o servidor, geralmente queremos ver o output (logs do uvicorn)
        # Então vamos abrir em uma nova janela CMD visível
        cmd = f'start "API Server - aiMain" cmd /k python "{script_path}"'
        subprocess.Popen(cmd, shell=True)
        messagebox.showinfo("Sucesso", "Servidor de IA iniciado!\nVerifique a nova janela do CMD.")
    except Exception as e:
        messagebox.showerror("Erro", f"Falha ao iniciar servidor: {e}")

def get_running_devices():
    """Retorna um dict com {device_id: pid} (LENTO - Executar em Thread)"""
    running_devices_map = {}
    target_scripts = ["main_optimized.py", "mainmusicvip.py"]
    
    try:
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                cmdline = proc.info['cmdline']
                if cmdline and 'python' in proc.info['name'].lower():
                    for script in target_scripts:
                         if any(script in arg for arg in cmdline):
                            for arg in cmdline:
                                if arg not in script and "python" not in arg.lower() and len(arg) > 4:
                                     # Assumindo que o argumento que sobra é o device_id
                                     running_devices_map[arg] = proc.info['pid']
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
            # Se for subprocess.Popen
            if hasattr(process, 'poll'):
                if process.poll() is not None:
                    to_remove.append(device_id)
            # Se for psutil.Process (readoptado)
            elif hasattr(process, 'is_running'):
                if not process.is_running():
                    to_remove.append(device_id)
            else:
                # Objeto desconhecido, remove
                to_remove.append(device_id)
        except:
             to_remove.append(device_id)

    for device_id in to_remove:
        del processos_ativos[device_id]
        if device_id in paused_devices:
            paused_devices.remove(device_id)
    
    # Se houve remoção, salva estado
    if to_remove:
        save_paused_state()

def monitor_devices_loop():
    """Monitora dispositivos em background"""
    global last_running_cache, last_devices_cache
    
    # Primeira carga de grupos
    force_reload_groups()
    
    while True:
        try:
            # 1. Busca dispositivos ADB (I/O)
            dispositivos_adb = listar_dispositivos_safe()
            last_devices_cache = dispositivos_adb
            
            # 2. Busca processos rodando (CPU/Syscall intensive)
            # Agora retorna map {device_id: pid}
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
            
            # 5. UI Update seguro
            janela.after(0, lambda: atualizar_lista_ui(use_cache=True))
            
        except Exception as e:
            print(f"[ERRO Monitor]: {e}")
        
        time.sleep(30)

def listar_dispositivos_safe():
    """Versão segura de listar dispositivos para chamar da thread"""
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        # CREATE_NO_WINDOW = 0x08000000
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
    Atualiza a UI. 
    AGORA EXTREMAMENTE LEVE: Não faz I/O, não faz psutil, não roda adb.
    Apenas lê caches e desenha.
    """
    global last_devices_cache, last_running_cache
    
    try:
        # Usa caches se não fornecidos
        if dispositivos is None:
            dispositivos = last_devices_cache
        
        if active_devices is None:
            active_devices = last_running_cache
        
        # Carrega grupos da MEMÓRIA
        groups = load_groups_cached()

        # Preparação de lookup reverso de grupos (O(1) no loop)
        device_to_group = {}
        for gname, gdevs in groups.items():
            for d in gdevs:
                device_to_group[d] = gname

        # Salva seleção
        selected_devices = set()
        for i in lista_dispositivos.curselection():
            try:
                entry = lista_dispositivos.get(i)
                if "Nenhum" not in entry:
                    # Formato: "1. 192.168.x.x [RODANDO] [Grupo]"
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
                 dispositivos_filtrados = [d for d in dispositivos if d in groups[grupo_filtro]]
            
            if not dispositivos_filtrados and grupo_filtro != "Todos os Dispositivos":
                lista_dispositivos.insert(tk.END, f"Nenhum dispositivo no grupo '{grupo_filtro}'")
                lista_dispositivos.itemconfigure(tk.END, fg="gray")
            else:
                for i, d in enumerate(dispositivos_filtrados, start=1):
                    is_active = d in active_devices
                    is_paused = d in paused_devices
                    
                    status_text = " [PARADO]"
                    if is_active:
                        status_text = " [RODANDO]"
                    if is_paused:
                        status_text = " [PAUSADO]"
                    
                    # Lookup rápido
                    grupo = device_to_group.get(d, None)
                    grupo_tag = f" [{grupo}]" if grupo and grupo_filtro == "Todos os Dispositivos" else ""
                    
                    texto_item = f"{i}. {d}{status_text}{grupo_tag}"
                    
                    lista_dispositivos.insert(tk.END, texto_item)
                    
                    if is_paused:
                        lista_dispositivos.itemconfigure(i-1, bg="#fff3cd", fg="#856404") # Amarelo
                    elif is_active:
                        lista_dispositivos.itemconfigure(i-1, bg="#d4edda", fg="#155724") # Verde
                    else:
                        lista_dispositivos.itemconfigure(i-1, bg="#f8d7da", fg="#721c24") # Vermelho
                    
                    if d in selected_devices:
                        lista_dispositivos.selection_set(i-1)

    except Exception as e:
        print(f"Erro ao atualizar UI: {e}")

def atualizar_lista_thread_safe(use_cache=True):
    """Wrapper Helper"""
    janela.after(0, lambda: atualizar_lista_ui(use_cache=use_cache))

def atualizar_lista():
    """Botão Atualizar (Força Scan)"""
    def worker():
        global last_devices_cache, last_running_cache
        # Força atualização dos caches
        devs = listar_dispositivos_safe()
        active = get_running_devices()
        last_devices_cache = devs
        last_running_cache = set(active.keys())
        force_reload_groups()
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
    dispositivos = last_devices_cache
    if not dispositivos:
        # Tenta um scan rápido se o cache estiver vazio
        dispositivos = listar_dispositivos_safe()
        if not dispositivos:
            messagebox.showinfo("Aviso", "Nenhum dispositivo encontrado. Verifique se estão conectados via ADB.")
            return

    count = 0
    for device_id in dispositivos:
        if device_id not in processos_ativos:
            run_script(device_id)
            count += 1
            # Aumentado para 3s para evitar WinError 1455 (recursos insuficientes)
            time.sleep(1.5)
    
    if count > 0:
        messagebox.showinfo("Sucesso", f"{count} bot(s) iniciados com sucesso!")
    else:
        messagebox.showinfo("Aviso", "Todos os dispositivos já possuem bots rodando.")

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
    
    iniciados = 0
    for device_id in dispositivos_grupo:
        if device_id in dispositivos_conectados:
            run_script(device_id)
            iniciados += 1
            time.sleep(1.0)
    
    if iniciados == 0:
        messagebox.showinfo("Aviso", f"Nenhum dispositivo do grupo '{grupo_selecionado}' está conectado")
    else:
        messagebox.showinfo("Sucesso", f"{iniciados} dispositivo(s) iniciado(s) do grupo '{grupo_selecionado}'")

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

# ==================== TAB 2: SCANNER (Lógica do scan.py) ====================

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
            o1, o2 = self._validate(self.e_o1.get()), self._validate(self.e_o2.get())
            o3s, o3e = self._validate(self.e_o3_start.get()), self._validate(self.e_o3_end.get())
            o4s, o4e = self._validate(self.e_o4_start.get()), self._validate(self.e_o4_end.get())
            port = int(self.entry_port.get())
            
            start_ip = f"{o1}.{o2}.{o3s}.{o4s}"
            end_ip = f"{o1}.{o2}.{o3e}.{o4e}"
            
            r = {"id": self.next_id, "start_ip": start_ip, "end_ip": end_ip, "port": port, "raw": (o1, o2, o3s, o3e, o4s, o4e)}
            self.ip_ranges.append(r)
            self._add_tree(r)
            self.next_id += 1
        except Exception as e:
            messagebox.showerror("Erro", str(e))

    def _add_tree(self, r):
        self.tree.insert("", "end", iid=str(r["id"]), values=(r["id"], r["start_ip"], r["end_ip"], r["port"]))

    def on_remove_selected(self):
        for item in self.tree.selection():
            rid = int(self.tree.item(item, "values")[0])
            self.tree.delete(item)
            self.ip_ranges = [x for x in self.ip_ranges if x["id"] != rid]

    def _load_ranges(self):
        if os.path.exists(SCAN_CONFIG_FILE):
            try:
                with open(SCAN_CONFIG_FILE, "r") as f:
                    data = json.load(f)
                    self.ip_ranges = data.get("ranges", [])
                    if self.ip_ranges: self.next_id = max(x["id"] for x in self.ip_ranges) + 1
                    for r in self.ip_ranges: self._add_tree(r)
            except: pass

    def _save_ranges(self):
        try:
            with open(SCAN_CONFIG_FILE, "w") as f:
                json.dump({"ranges": self.ip_ranges}, f, indent=4)
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
        
        # Chama connect.py logic
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
janela.title("Inspirations Bot Studio")
janela.geometry("600x750")

notebook = ttk.Notebook(janela)
notebook.pack(fill="both", expand=True)

# --- ABA BOTS ---
tab_bots = ttk.Frame(notebook)
notebook.add(tab_bots, text="🤖 Bots")

# (Aqui vai toda a UI antiga, reparentada para tab_bots)
# Título TAB BOTS
tk.Label(tab_bots, text="🚀 Gerenciador de Bots", font=("Arial", 14, "bold")).pack(pady=5)

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
# Binding atualizado para ser thread-safe e usar cache
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

# Execução - Painel de Controle
frame_root_exec = tk.LabelFrame(tab_bots, text="🎮 Painel de Controle", padx=10, pady=5)
frame_root_exec.pack(pady=10, fill="x", padx=10)

# Grid layout para colunas
frame_root_exec.columnconfigure(0, weight=1)
frame_root_exec.columnconfigure(1, weight=1)
frame_root_exec.columnconfigure(2, weight=1)

# Seção 1: Inicialização (Verde)
frame_start = tk.LabelFrame(frame_root_exec, text="▶ Inicializar", padx=5, pady=5)
frame_start.grid(row=0, column=0, sticky="nsew", padx=5)

tk.Button(frame_start, text="⚡ START API SERVER", command=start_api_server, bg="#004085", fg="white", width=20, font=("Arial", 9, "bold")).pack(pady=5)
ttk.Separator(frame_start, orient="horizontal").pack(fill="x", pady=5)
tk.Button(frame_start, text="Iniciar Todos", command=iniciar_todos_os_bots, bg="#1e7e34", fg="white", width=20).pack(pady=2)
tk.Button(frame_start, text="Iniciar Selecionados", command=iniciar_bot, bg="#218838", fg="white", width=20).pack(pady=2)
tk.Button(frame_start, text="Iniciar (Grupo)", command=iniciar_grupo, bg="#28a745", fg="white", width=20).pack(pady=2)

# Seção 2: Ações na Seleção (Amarelo/Laranja)
frame_sel = tk.LabelFrame(frame_root_exec, text="⚡ Ações na Seleção", padx=5, pady=5)
frame_sel.grid(row=0, column=1, sticky="nsew", padx=5)

tk.Button(frame_sel, text="⏸ Pause/Resume Sel", command=toggle_pause_selection, bg="#ffc107", fg="black", width=20).pack(pady=2)
tk.Button(frame_sel, text="⏹ Parar Selecionados", command=parar_selecao, bg="#dc3545", fg="white", width=20).pack(pady=2)

# Seção 3: Ações Globais (Vermelho)
frame_global = tk.LabelFrame(frame_root_exec, text="🔥 Ações Globais", padx=5, pady=5)
frame_global.grid(row=0, column=2, sticky="nsew", padx=5)

tk.Button(frame_global, text="⏸ Pause/Resume TODOS", command=toggle_pause_all, bg="#fd7e14", fg="white", width=20).pack(pady=2)
tk.Button(frame_global, text="⏹ PARAR TUDO", command=stop_all_scripts, bg="#bd2130", fg="white", width=20).pack(pady=2)


# --- ABA SCANNER ---
tab_scan = ttk.Frame(notebook)
notebook.add(tab_scan, text="📡 Network Scanner")

# Instancia a classe de scan dentro da aba
app_scan = ScannerTab(tab_scan)

# --- INICIALIZAÇÃO ---
atualizar_combo_grupos()
force_reload_groups()
# Atualização inicial deve ser em thread se possível, mas aqui é init
# Vamos rodar um update fake e deixar a thread popular
atualizar_lista()
# Inicia monitoramento em background
threading.Thread(target=monitor_devices_loop, daemon=True).start()

# Auto-Scan na inicialização (conforme solicitado)
print("[INFO] Agendando Auto-Scan para 1s...")
janela.after(1000, lambda: app_scan.on_scan_all())

janela.mainloop()
