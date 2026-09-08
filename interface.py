import tkinter as tk
from tkinter import messagebox
import subprocess
import threading
import os
import sys

# Usa o diretório atual de trabalho
SCRIPT_DIR = os.getcwd()
SCRIPT_PATH = os.path.join(SCRIPT_DIR, "main.py")
dispositivos_ativos = set()

def run_script(device_id, modo=0):
    def worker():
        try:
            if not os.path.isfile(SCRIPT_PATH):
                raise FileNotFoundError(f"Script não encontrado: {SCRIPT_PATH}")

            # Usar aspas triplas para caminhos com espaços
            cmd = f'start cmd /k "\"{sys.executable}\" \"{SCRIPT_PATH}\" {device_id} {modo}"'
            subprocess.Popen(cmd, shell=True)
            dispositivos_ativos.add(device_id)
            atualizar_lista()
        except Exception as e:
            messagebox.showerror("Erro", f"Erro ao iniciar subprocesso para {device_id}:\n{e}")

    threading.Thread(target=worker).start()

def listar_dispositivos():
    try:
        result = subprocess.check_output("adb devices", shell=True, text=True)
        lines = result.strip().split("\n")[1:]
        return [line.split("\t")[0] for line in lines if "device" in line]
    except Exception as e:
        messagebox.showerror("Erro", f"Erro ao listar dispositivos:\n{e}")
        return []

def atualizar_lista():
    lista_dispositivos.delete(0, tk.END)
    dispositivos = listar_dispositivos()
    if not dispositivos:
        lista_dispositivos.insert(tk.END, "Nenhum dispositivo encontrado")
    else:
        for i, d in enumerate(dispositivos, start=1):
            lista_dispositivos.insert(tk.END, f"{i}. {d}")

def iniciar_bot():
    selecionados = lista_dispositivos.curselection()
    if not selecionados:
        messagebox.showinfo("Aviso", "Selecione ao menos um dispositivo")
        return

    modo = modo_execucao.get()

    for index in selecionados:
        entry = lista_dispositivos.get(index)
        if "Nenhum dispositivo" in entry:
            continue
        device_id = entry.split(" ", 1)[1]
        run_script(device_id, modo)

def iniciar_todos_os_bots():
    dispositivos = listar_dispositivos()
    if not dispositivos:
        messagebox.showinfo("Aviso", "Nenhum dispositivo disponível")
        return

    modo = modo_execucao.get()
    for device_id in dispositivos:
        run_script(device_id, modo)

# --- INTERFACE ---
janela = tk.Tk()
janela.title("Inspirations Bot")
janela.geometry("420x420")
janela.resizable(False, True)

modo_execucao = tk.IntVar(value=0)

btn_atualizar = tk.Button(janela, text="Atualizar Lista de Dispositivos", command=atualizar_lista)
btn_atualizar.pack(pady=10)

lista_dispositivos = tk.Listbox(janela, selectmode=tk.MULTIPLE, width=55)
lista_dispositivos.pack(pady=10)

# Botões de execução
frame_execucao = tk.Frame(janela)
frame_execucao.pack(pady=5)

btn_iniciar_sel = tk.Button(frame_execucao, text="Iniciar Selecionados", command=iniciar_bot)
btn_iniciar_sel.grid(row=0, column=0, padx=10)

btn_iniciar_todos = tk.Button(frame_execucao, text="Iniciar Todos", command=iniciar_todos_os_bots)
btn_iniciar_todos.grid(row=0, column=1, padx=10)

# Toggle de modo
def alternar_modo():
    atual = modo_execucao.get()
    novo = 1 if atual == 0 else 0
    modo_execucao.set(novo)
    btn_toggle_modo.config(text=f"Modo: {'Manual' if novo else 'Automático'}")

btn_toggle_modo = tk.Button(janela, text="Modo: Automático", command=alternar_modo, bg="#ececec")
btn_toggle_modo.pack(pady=10)

atualizar_lista()
janela.mainloop()
