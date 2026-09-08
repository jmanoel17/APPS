import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
import subprocess
import threading
import os
import json
import psutil

# Configuração: escolha qual versão do script usar
SCRIPT_DIR = os.getcwd()
SCRIPT_PATH = os.path.join(SCRIPT_DIR, "mainfrasesvip.py")
GROUPS_FILE = os.path.join(SCRIPT_DIR, "groups.json")

print(f"[INFO] Diretório de trabalho: {SCRIPT_DIR}")
print(f"[INFO] Script a ser executado: {SCRIPT_PATH}")
print(f"[INFO] Script existe: {os.path.isfile(SCRIPT_PATH)}")

# Rastreamento de processos ativos
processos_ativos = {}  # {device_id: subprocess.Popen}

# ==================== GERENCIAMENTO DE GRUPOS ====================

def load_groups():
    """Carrega grupos do arquivo JSON"""
    if os.path.exists(GROUPS_FILE):
        try:
            with open(GROUPS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"[ERRO] Falha ao carregar grupos: {e}")
            return {}
    return {}

def save_groups(groups):
    """Salva grupos no arquivo JSON"""
    try:
        with open(GROUPS_FILE, 'w', encoding='utf-8') as f:
            json.dump(groups, f, indent=2, ensure_ascii=False)
    except Exception as e:
        messagebox.showerror("Erro", f"Erro ao salvar grupos:\n{e}")

def create_group():
    """Cria um novo grupo"""
    nome = simpledialog.askstring("Novo Grupo", "Nome do grupo:")
    if nome:
        groups = load_groups()
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
        groups = load_groups()
        if grupo_selecionado in groups:
            del groups[grupo_selecionado]
            save_groups(groups)
            atualizar_combo_grupos()
            combo_grupos.set("Todos os Dispositivos")
            atualizar_lista()
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
    
    groups = load_groups()
    devices_to_add = []
    
    for index in selecionados:
        entry = lista_dispositivos.get(index)
        if "Nenhum dispositivo" in entry:
            continue
        # Extrai o device_id (remove número, status ATIVO e grupo)
        parts = entry.split(" ", 1)[1]
        device_id = parts.split(" [")[0]
        devices_to_add.append(device_id)
    
    # Adiciona ao grupo (sem duplicatas)
    for device in devices_to_add:
        if device not in groups[grupo_selecionado]:
            groups[grupo_selecionado].append(device)
    
    save_groups(groups)
    atualizar_lista()
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
    
    groups = load_groups()
    devices_to_remove = []
    
    for index in selecionados:
        entry = lista_dispositivos.get(index)
        if "Nenhum dispositivo" in entry:
            continue
        parts = entry.split(" ", 1)[1]
        device_id = parts.split(" [")[0]
        devices_to_remove.append(device_id)
    
    # Remove do grupo
    for device in devices_to_remove:
        if device in groups[grupo_selecionado]:
            groups[grupo_selecionado].remove(device)
    
    save_groups(groups)
    atualizar_lista()
    messagebox.showinfo("Sucesso", f"{len(devices_to_remove)} dispositivo(s) removido(s) do grupo '{grupo_selecionado}'")

def atualizar_combo_grupos():
    """Atualiza a lista de grupos no combobox"""
    groups = load_groups()
    valores = ["Todos os Dispositivos"] + list(groups.keys())
    combo_grupos['values'] = valores
    if combo_grupos.get() not in valores:
        combo_grupos.set("Todos os Dispositivos")

def get_device_group(device_id):
    """Retorna o nome do grupo do dispositivo ou None"""
    groups = load_groups()
    for group_name, devices in groups.items():
        if device_id in devices:
            return group_name
    return None

# ==================== GERENCIAMENTO DE PROCESSOS ====================

def run_script(device_id):
    """Executa o script principal em uma nova janela de terminal"""
    def worker():
        try:
            if not os.path.isfile(SCRIPT_PATH):
                raise FileNotFoundError(f"Script não encontrado: {SCRIPT_PATH}")

            # Comando sem aspas extras - deixa o shell resolver
            cmd = f'start cmd /k python "{SCRIPT_PATH}" {device_id} 0'
            print(f"[DEBUG] Executando comando: {cmd}")
            
            # Cria processo e rastreia
            process = subprocess.Popen(cmd, shell=True)
            processos_ativos[device_id] = process
            atualizar_lista()
        except Exception as e:
            messagebox.showerror("Erro", f"Erro ao iniciar subprocesso para {device_id}:\n{e}")

    threading.Thread(target=worker, daemon=True).start()

def check_active_processes():
    """Verifica quais processos ainda estão ativos"""
    to_remove = []
    for device_id, process in processos_ativos.items():
        # Verifica se o processo ainda existe
        try:
            # Tenta encontrar processos filhos (cmd.exe e python.exe)
            parent = psutil.Process(process.pid)
            if not parent.is_running():
                to_remove.append(device_id)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            to_remove.append(device_id)
    
    # Remove processos mortos
    for device_id in to_remove:
        del processos_ativos[device_id]
    
    return len(to_remove) > 0

def update_status_periodically():
    """Atualiza status dos processos periodicamente"""
    if check_active_processes():
        atualizar_lista()
    janela.after(2000, update_status_periodically)  # Verifica a cada 2 segundos

# ==================== DISPOSITIVOS ====================

def listar_dispositivos():
    """Lista todos os dispositivos ADB conectados"""
    try:
        result = subprocess.check_output("adb devices", shell=True, text=True)
        lines = result.strip().split("\n")[1:]
        return [line.split("\t")[0] for line in lines if "device" in line]
    except Exception as e:
        messagebox.showerror("Erro", f"Erro ao listar dispositivos:\n{e}")
        return []

def atualizar_lista():
    """Atualiza a lista de dispositivos na interface"""
    lista_dispositivos.delete(0, tk.END)
    dispositivos = listar_dispositivos()
    grupo_filtro = combo_grupos.get()
    
    if not dispositivos:
        lista_dispositivos.insert(tk.END, "Nenhum dispositivo encontrado")
    else:
        groups = load_groups()
        dispositivos_filtrados = dispositivos
        
        # Aplica filtro de grupo se não for "Todos"
        if grupo_filtro != "Todos os Dispositivos" and grupo_filtro in groups:
            dispositivos_filtrados = [d for d in dispositivos if d in groups[grupo_filtro]]
        
        if not dispositivos_filtrados and grupo_filtro != "Todos os Dispositivos":
            lista_dispositivos.insert(tk.END, f"Nenhum dispositivo no grupo '{grupo_filtro}'")
        else:
            for i, d in enumerate(dispositivos_filtrados, start=1):
                status = " [ATIVO]" if d in processos_ativos else ""
                grupo = get_device_group(d)
                grupo_tag = f" [{grupo}]" if grupo and grupo_filtro == "Todos os Dispositivos" else ""
                lista_dispositivos.insert(tk.END, f"{i}. {d}{status}{grupo_tag}")

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
        # Remove o número, status e grupo
        parts = entry.split(" ", 1)[1]
        device_id = parts.split(" [")[0]
        run_script(device_id)

def iniciar_todos_os_bots():
    """Inicia o bot em todos os dispositivos disponíveis"""
    dispositivos = listar_dispositivos()
    if not dispositivos:
        messagebox.showinfo("Aviso", "Nenhum dispositivo disponível")
        return

    for device_id in dispositivos:
        run_script(device_id)

def iniciar_grupo():
    """Inicia o bot em todos os dispositivos do grupo selecionado"""
    grupo_selecionado = combo_grupos.get()
    if not grupo_selecionado or grupo_selecionado == "Todos os Dispositivos":
        messagebox.showwarning("Aviso", "Selecione um grupo específico")
        return
    
    groups = load_groups()
    if grupo_selecionado not in groups:
        messagebox.showwarning("Aviso", "Grupo não encontrado")
        return
    
    dispositivos_grupo = groups[grupo_selecionado]
    dispositivos_conectados = listar_dispositivos()
    
    # Inicia apenas dispositivos que estão conectados
    iniciados = 0
    for device_id in dispositivos_grupo:
        if device_id in dispositivos_conectados:
            run_script(device_id)
            iniciados += 1
    
    if iniciados == 0:
        messagebox.showinfo("Aviso", f"Nenhum dispositivo do grupo '{grupo_selecionado}' está conectado")
    else:
        messagebox.showinfo("Sucesso", f"{iniciados} dispositivo(s) iniciado(s) do grupo '{grupo_selecionado}'")

# ==================== INTERFACE ====================
janela = tk.Tk()
janela.title("Inspirations Bot - Otimizado com Grupos")
janela.geometry("550x600")
janela.resizable(False, True)

# Título
label_titulo = tk.Label(janela, text="🚀 Inspirations Bot", font=("Arial", 16, "bold"))
label_titulo.pack(pady=10)

# Versão do script
script_name = os.path.basename(SCRIPT_PATH)
label_versao = tk.Label(janela, text=f"Usando: {script_name}", font=("Arial", 9), fg="gray")
label_versao.pack()

# ==================== SEÇÃO DE GRUPOS ====================
frame_grupos = tk.LabelFrame(janela, text="📁 Gerenciamento de Grupos", padx=10, pady=10)
frame_grupos.pack(pady=10, padx=10, fill="x")

# Seleção de grupo
frame_selecao_grupo = tk.Frame(frame_grupos)
frame_selecao_grupo.pack(fill="x", pady=5)

tk.Label(frame_selecao_grupo, text="Grupo:").pack(side=tk.LEFT, padx=5)
combo_grupos = ttk.Combobox(frame_selecao_grupo, state="readonly", width=30)
combo_grupos.pack(side=tk.LEFT, padx=5)
combo_grupos.bind("<<ComboboxSelected>>", lambda e: atualizar_lista())

# Botões de gerenciamento de grupos
frame_btns_grupos = tk.Frame(frame_grupos)
frame_btns_grupos.pack(pady=5)

btn_novo_grupo = tk.Button(frame_btns_grupos, text="➕ Novo Grupo", command=create_group, width=15)
btn_novo_grupo.grid(row=0, column=0, padx=3)

btn_deletar_grupo = tk.Button(frame_btns_grupos, text="🗑️ Deletar Grupo", command=delete_group, width=15)
btn_deletar_grupo.grid(row=0, column=1, padx=3)

# Botões de adicionar/remover dispositivos
frame_btns_devices = tk.Frame(frame_grupos)
frame_btns_devices.pack(pady=5)

btn_add_to_group = tk.Button(frame_btns_devices, text="➕ Adicionar ao Grupo", command=add_to_group, width=20)
btn_add_to_group.grid(row=0, column=0, padx=3)

btn_remove_from_group = tk.Button(frame_btns_devices, text="➖ Remover do Grupo", command=remove_from_group, width=20)
btn_remove_from_group.grid(row=0, column=1, padx=3)

# ==================== SEÇÃO DE DISPOSITIVOS ====================
frame_dispositivos = tk.LabelFrame(janela, text="📱 Dispositivos", padx=10, pady=10)
frame_dispositivos.pack(pady=10, padx=10, fill="both", expand=True)

# Botão atualizar
btn_atualizar = tk.Button(frame_dispositivos, text="🔄 Atualizar Lista", command=atualizar_lista)
btn_atualizar.pack(pady=5)

# Lista de dispositivos
lista_dispositivos = tk.Listbox(frame_dispositivos, selectmode=tk.MULTIPLE, width=70, height=12)
lista_dispositivos.pack(pady=5)

# ==================== BOTÕES DE EXECUÇÃO ====================
frame_execucao = tk.Frame(janela)
frame_execucao.pack(pady=10)

btn_iniciar_grupo = tk.Button(frame_execucao, text="▶️ Iniciar Grupo", command=iniciar_grupo, 
                               width=18, bg="#4CAF50", fg="white", font=("Arial", 10, "bold"))
btn_iniciar_grupo.grid(row=0, column=0, padx=5)

btn_iniciar_sel = tk.Button(frame_execucao, text="▶️ Iniciar Selecionados", command=iniciar_bot, width=18)
btn_iniciar_sel.grid(row=0, column=1, padx=5)

btn_iniciar_todos = tk.Button(frame_execucao, text="▶️▶️ Iniciar Todos", command=iniciar_todos_os_bots, width=18)
btn_iniciar_todos.grid(row=0, column=2, padx=5)

# Informações
label_info = tk.Label(janela, text="Crie grupos, adicione dispositivos e inicie por grupo ou seleção", 
                     font=("Arial", 8), fg="gray")
label_info.pack(pady=5)

# Inicialização
atualizar_combo_grupos()
atualizar_lista()
update_status_periodically()  # Inicia verificação periódica

# Iniciar interface
janela.mainloop()
