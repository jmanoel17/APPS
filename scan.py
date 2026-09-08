import threading
import connect  # Importa nossa nova lógica de scanner
from tkinter import scrolledtext
import tkinter as tk
from tkinter import ttk, messagebox
import json
import os

CONFIG_FILE = "ip_ranges.json"


class IPRangeManager(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("Antigravity - OTG Network Scanner")
        self.geometry("800x600")  # Aumentei para caber o log
        # self.resizable(False, False) # Permitir redimensionar é melhor

        self.ip_ranges = []  # lista de dicts
        self.next_id = 1
        self.scanner = connect.NetworkScanner(max_threads=50, timeout=1.0)
        self.ip_ranges = []  # lista de dicts: {id, start_ip, end_ip, port}
        self.next_id = 1

        self._build_ui()
        self._load_ranges()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        # === Step 1: Porta padrão ==========================================
        frame_port = ttk.LabelFrame(self, text="Step 1 - Enable Network Port")
        frame_port.pack(fill="x", padx=10, pady=5)

        ttk.Label(frame_port, text="Porta padrão:").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        self.entry_port = ttk.Entry(frame_port, width=8)
        self.entry_port.insert(0, "5555")
        self.entry_port.grid(row=0, column=1, padx=5, pady=5, sticky="w")

        self.btn_enable = ttk.Button(frame_port, text="Enable", command=self.on_enable_port)
        self.btn_enable.grid(row=0, column=2, padx=5, pady=5, sticky="w")

        # === Step 3: Scan Devices (definição de faixas) ====================
        frame_scan = ttk.LabelFrame(self, text="Step 3 - Scan Devices")
        frame_scan.pack(fill="x", padx=10, pady=5)

        # Linha de labels (octetos)
        ttk.Label(frame_scan, text="Octeto 1").grid(row=0, column=0, padx=3, pady=2)
        ttk.Label(frame_scan, text="Octeto 2").grid(row=0, column=1, padx=3, pady=2)
        ttk.Label(frame_scan, text="3º (inicio)").grid(row=0, column=2, padx=3, pady=2)
        ttk.Label(frame_scan, text="3º (fim)").grid(row=0, column=3, padx=3, pady=2)
        ttk.Label(frame_scan, text="4º (inicio)").grid(row=0, column=4, padx=3, pady=2)
        ttk.Label(frame_scan, text="4º (fim)").grid(row=0, column=5, padx=3, pady=2)

        # Entradas para a faixa
        self.e_o1 = ttk.Entry(frame_scan, width=5)
        self.e_o2 = ttk.Entry(frame_scan, width=5)
        self.e_o3_start = ttk.Entry(frame_scan, width=5)
        self.e_o3_end = ttk.Entry(frame_scan, width=5)
        self.e_o4_start = ttk.Entry(frame_scan, width=5)
        self.e_o4_end = ttk.Entry(frame_scan, width=5)

        self.e_o1.grid(row=1, column=0, padx=3, pady=2)
        self.e_o2.grid(row=1, column=1, padx=3, pady=2)
        self.e_o3_start.grid(row=1, column=2, padx=3, pady=2)
        self.e_o3_end.grid(row=1, column=3, padx=3, pady=2)
        self.e_o4_start.grid(row=1, column=4, padx=3, pady=2)
        self.e_o4_end.grid(row=1, column=5, padx=3, pady=2)

        # Valores default para algo tipo 192.168.1-255
        self.e_o1.insert(0, "192")
        self.e_o2.insert(0, "168")
        self.e_o3_start.insert(0, "1")
        self.e_o3_end.insert(0, "1")
        self.e_o4_start.insert(0, "1")
        self.e_o4_end.insert(0, "255")

        self.btn_add_range = ttk.Button(frame_scan, text="+ Add faixa", command=self.on_add_range)
        self.btn_add_range.grid(row=1, column=6, padx=8, pady=2)

        # === Lista de faixas / Network ====================================
        # === Botões de ação (Pinned to Bottom) ============================
        frame_actions = ttk.Frame(self)
        frame_actions.pack(side="bottom", fill="x", padx=10, pady=5)

        self.btn_scan_sel = ttk.Button(frame_actions, text="▶ START SCAN (Selecionado)", command=self.on_scan_selected)
        self.btn_scan_sel.pack(side="left", padx=5)
        
        self.btn_scan_all = ttk.Button(frame_actions, text="▶ START SCAN (Todos)", command=self.on_scan_all)
        self.btn_scan_all.pack(side="left", padx=5)

        ttk.Button(frame_actions, text="Remover Selecionado", command=self.on_remove_selected).pack(side="left", padx=5)
        ttk.Button(frame_actions, text="Salvar Faixas", command=self._save_ranges).pack(side="right", padx=5)

        # === Lista de faixas / Network ====================================
        frame_list = ttk.LabelFrame(self, text="Network Ranges")
        frame_list.pack(fill="both", expand=True, padx=10, pady=5)

        columns = ("id", "start_ip", "end_ip", "port")
        self.tree = ttk.Treeview(frame_list, columns=columns, show="headings", height=10)
        self.tree.heading("id", text="ID")
        self.tree.heading("start_ip", text="IP Inicial")
        self.tree.heading("end_ip", text="IP Final")
        self.tree.heading("port", text="Porta")

        self.tree.column("id", width=40, anchor="center")
        self.tree.column("start_ip", width=140, anchor="center")
        self.tree.column("end_ip", width=140, anchor="center")
        self.tree.column("port", width=60, anchor="center")

        self.tree.pack(side="left", fill="both", expand=True)

        scrollbar = ttk.Scrollbar(frame_list, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscroll=scrollbar.set)
        scrollbar.pack(side="right", fill="y")


        # === Logs Console =================================================
        frame_logs = ttk.LabelFrame(self, text="Logs / Status")
        # Pack last (fill remaining)
        frame_logs.pack(fill="both", expand=True, padx=10, pady=5)

        self.txt_logs = scrolledtext.ScrolledText(frame_logs, height=10, state='disabled')
        self.txt_logs.pack(fill="both", expand=True)

    # ------------------------------------------------------------------ Lógica

    def on_enable_port(self):
        port = self.entry_port.get().strip()
        if not port.isdigit():
            messagebox.showerror("Erro", "Porta inválida.")
            return

        messagebox.showinfo("Porta habilitada", f"Porta de rede padrão definida como {port}.")
        # Aqui você pode integrar com o resto do antigravity se precisar
        # ex: self.config["default_port"] = int(port)

    def _validate_octet(self, value, name):
        if not value.isdigit():
            raise ValueError(f"{name} deve ser um número (0-255).")
        n = int(value)
        if not (0 <= n <= 255):
            raise ValueError(f"{name} deve estar entre 0 e 255.")
        return n

    def on_add_range(self):
        try:
            o1 = self._validate_octet(self.e_o1.get().strip(), "Octeto 1")
            o2 = self._validate_octet(self.e_o2.get().strip(), "Octeto 2")
            o3_start = self._validate_octet(self.e_o3_start.get().strip(), "3º octeto (início)")
            o3_end = self._validate_octet(self.e_o3_end.get().strip(), "3º octeto (fim)")
            o4_start = self._validate_octet(self.e_o4_start.get().strip(), "4º octeto (início)")
            o4_end = self._validate_octet(self.e_o4_end.get().strip(), "4º octeto (fim)")

            if o3_start > o3_end:
                raise ValueError("3º octeto: início não pode ser maior que fim.")
            if o4_start > o4_end:
                raise ValueError("4º octeto: início não pode ser maior que fim.")

            port = self.entry_port.get().strip()
            if not port.isdigit():
                raise ValueError("Porta inválida.")
            port = int(port)

            start_ip = f"{o1}.{o2}.{o3_start}.{o4_start}"
            end_ip = f"{o1}.{o2}.{o3_end}.{o4_end}"

            range_obj = {
                "id": self.next_id,
                "start_ip": start_ip,
                "end_ip": end_ip,
                "port": port,
                # Armazenar range bruto para reconstrução
                "raw": (o1, o2, o3_start, o3_end, o4_start, o4_end)
            }
            self.ip_ranges.append(range_obj)
            self._add_range_to_tree(range_obj)
            self.next_id += 1

        except ValueError as e:
            messagebox.showerror("Erro ao adicionar faixa", str(e))

    def _add_range_to_tree(self, r):
        self.tree.insert("", "end", iid=str(r["id"]),
                         values=(r["id"], r["start_ip"], r["end_ip"], r["port"]))

    def on_remove_selected(self):
        selected = self.tree.selection()
        if not selected:
            return
        for item in selected:
            rid = int(self.tree.item(item, "values")[0])
            self.tree.delete(item)
            self.ip_ranges = [r for r in self.ip_ranges if r["id"] != rid]

    # --------------------------- SCAN REAL --------------------------------
    def log(self, message):
        """Método seguro para thread atualizar a GUI"""
        self.txt_logs.config(state='normal')
        self.txt_logs.insert(tk.END, message + "\n")
        self.txt_logs.see(tk.END)  # Auto-scroll
        self.txt_logs.config(state='disabled')

    def generate_ip_list(self, r):
        """Gera lista de IPs baseado nos octetos salvos"""
        if "raw" not in r:
            # Fallback se carregar de JSON antigo sem "raw"
            # (Poderia tentar parsear strings, mas vamos assumir que user recriará se precisar)
            return [r["start_ip"], r["end_ip"]] # Apenas extremos (bug fix rápido)
        
        o1, o2, o3_start, o3_end, o4_start, o4_end = r["raw"]
        ip_list = []
        for o3 in range(o3_start, o3_end + 1):
            for o4 in range(o4_start, o4_end + 1):
                ip_list.append(f"{o1}.{o2}.{o3}.{o4}")
        return ip_list

    def run_scan_thread(self, ranges_to_scan):
        self.btn_scan_sel["state"] = "disabled"
        self.btn_scan_all["state"] = "disabled"
        self.log("=== INICIANDO SCAN ===")

        all_ips = []
        for r in ranges_to_scan:
            ips = self.generate_ip_list(r)
            all_ips.extend(ips)
            self.log(f"Preparando faixa ID {r['id']}: {len(ips)} IPs...")

        # Chama o backend
        # O callback log refere-se ao método self.log
        self.scanner.scan_ips(all_ips, ranges_to_scan[0]["port"] if ranges_to_scan else 5555, self.log)

        self.log("=== SCAN FINALIZADO ===")
        self.btn_scan_sel["state"] = "normal"
        self.btn_scan_all["state"] = "normal"

    def on_scan_selected(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo("Scan", "Nenhuma faixa selecionada.")
            return
        
        ranges = []
        for item in selected:
            rid = int(self.tree.item(item, "values")[0])
            r = next((x for x in self.ip_ranges if x["id"] == rid), None)
            if r:
                ranges.append(r)
        
        threading.Thread(target=self.run_scan_thread, args=(ranges,), daemon=True).start()

    def on_scan_all(self):
        if not self.ip_ranges:
            messagebox.showinfo("Scan", "Nenhuma faixa cadastrada.")
            return
        threading.Thread(target=self.run_scan_thread, args=(self.ip_ranges,), daemon=True).start()

    # --------------------------- Persistência ------------------------------
    def _load_ranges(self):
        try:
            if not os.path.exists(CONFIG_FILE):
                return
                
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    return

                data = json.loads(content)
                self.ip_ranges = data.get("ranges", [])
                if self.ip_ranges:
                    self.next_id = max(r["id"] for r in self.ip_ranges) + 1
                for r in self.ip_ranges:
                    self._add_range_to_tree(r)
        except json.JSONDecodeError:
            print("Arquivo de configuração corrompido ou vazio. Iniciando limpo.")
            pass # Inicia com lista vazia
        except Exception as e:
            messagebox.showwarning("Aviso", f"Erro ao carregar faixas: {e}")

    def _save_ranges(self):
        try:
            data = {"ranges": self.ip_ranges}
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
            messagebox.showinfo("Salvar faixas", "Faixas salvas com sucesso.")
        except Exception as e:
            messagebox.showerror("Erro", f"Não foi possível salvar: {e}")


if __name__ == "__main__":
    app = IPRangeManager()
    app.mainloop()
