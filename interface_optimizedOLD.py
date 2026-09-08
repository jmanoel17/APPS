import tkinter as tk
from tkinter import messagebox, simpledialog, ttk, scrolledtext
import subprocess
import threading
import os
import json
import psutil
import time
import connect  # Requires connect.py in the same directory

# ==================== CONFIGURAÇÃO GERAL ====================
SCRIPT_DIR = os.getcwd()
SCRIPTS_MAP = {
    "DropsBin (main_optimized.py)": "main_optimized.py",
    "MusicVip (mainmusicvip.py)": "mainmusicvip.py"
}
DEFAULT_SCRIPT_NAME = "DropsBin (main_optimized.py)"
GROUPS_FILE = os.path.join(SCRIPT_DIR, "groups.json")
SCAN_CONFIG_FILE = os.path.join(SCRIPT_DIR, "ip_ranges.json")

print(f"[INFO] Diretório de trabalho: {SCRIPT_DIR}")

# ==================== TAB 1: BOTS (Antigo interface_optimized.py) ====================

# Rastreamento de processos ativos
processos_ativos = {}  # {device_id: subprocess.Popen}
# Caches globais para evitar processamento na thread principal
last_groups_cache = {}
last_devices_cache = []
last_running_cache = set()

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
    """Executa o script principal em uma nova janela de terminal"""
    selected_name = combo_scripts.get()
    script_filename = SCRIPTS_MAP.get(selected_name, "main_optimized.py")
    script_path = os.path.join(SCRIPT_DIR, script_filename)

    def worker():
        try:
            if not os.path.isfile(script_path):
                print(f"[ERRO] Script não encontrado: {script_path}")
                return

            cmd = f'start cmd /k python "{script_path}" {device_id} 0'
            print(f"[DEBUG] Executando comando ({selected_name}): {cmd}")
            
            process = subprocess.Popen(cmd, shell=True)
            processos_ativos[device_id] = process
            
            # Atualiza cache visualmente na hora (fake update para feedback imediato)
            last_running_cache.add(device_id)
            janela.after(0, lambda: atualizar_lista_ui(use_cache=True))
            
        except Exception as e:
            print(f"[ERRO WORKER] {e}")

    threading.Thread(target=worker, daemon=True).start()

def get_running_devices():
    """Retorna um set com IDs de dispositivos (LENTO - Executar em Thread)"""
    running_devices = set()
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
                                     running_devices.add(arg)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass
    except Exception as e:
        print(f"[ERRO] Falha ao escanear processos: {e}")
        
    return running_devices

def check_active_processes():
    """Limpa dicionário de processos locais"""
    to_remove = []
    for device_id, process in processos_ativos.items():
        if process.poll() is not None:
            to_remove.append(device_id)
    for device_id in to_remove:
        del processos_ativos[device_id]

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
            running_set = get_running_devices()
            last_running_cache = running_set
            
            # 3. Limpa processos locais
            check_active_processes()
            
            # 4. Atualiza grupos (Disk I/O)
            force_reload_groups()
            
            # 5. UI Update seguro
            janela.after(0, lambda: atualizar_lista_ui(use_cache=True))
            
        except Exception as e:
            print(f"[ERRO Monitor]: {e}")
        
        time.sleep(10)

def listar_dispositivos_safe():
    """Versão segura de listar dispositivos para chamar da thread"""
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        result = subprocess.check_output("adb devices", shell=True, text=True, timeout=5, startupinfo=si)
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
                    status_text = " [RODANDO]" if is_active else " [PARADO]"
                    
                    # Lookup rápido
                    grupo = device_to_group.get(d, None)
                    grupo_tag = f" [{grupo}]" if grupo and grupo_filtro == "Todos os Dispositivos" else ""
                    
                    texto_item = f"{i}. {d}{status_text}{grupo_tag}"
                    
                    lista_dispositivos.insert(tk.END, texto_item)
                    
                    if is_active:
                        lista_dispositivos.itemconfigure(i-1, bg="#d4edda", fg="#155724")
                    else:
                        lista_dispositivos.itemconfigure(i-1, bg="#f8d7da", fg="#721c24")
                    
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
        last_running_cache = active
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
    dispositivos = last_devices_cache # Use cache for instant feedback
    if not dispositivos:
        messagebox.showinfo("Aviso", "Nenhum dispositivo disponível (Tente 'Atualizar')")
        return
    for device_id in dispositivos:
        run_script(device_id)

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
    
    if iniciados == 0:
        messagebox.showinfo("Aviso", f"Nenhum dispositivo do grupo '{grupo_selecionado}' está conectado")
    else:
        messagebox.showinfo("Sucesso", f"{iniciados} dispositivo(s) iniciado(s) do grupo '{grupo_selecionado}'")

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

# Execução
frame_exec = tk.Frame(tab_bots)
frame_exec.pack(pady=10)
tk.Button(frame_exec, text="▶ Grupo", command=iniciar_grupo, bg="#4CAF50", fg="white").grid(row=0, column=0, padx=5)
tk.Button(frame_exec, text="▶ Seleção", command=iniciar_bot).grid(row=0, column=1, padx=5)
tk.Button(frame_exec, text="▶ Todos", command=iniciar_todos_os_bots).grid(row=0, column=2, padx=5)

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

janela.mainloop()
