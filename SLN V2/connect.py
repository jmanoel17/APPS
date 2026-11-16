
import subprocess
import os
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

class NetworkScanner:
    def __init__(self, max_threads=50, timeout=0.5):
        self.max_threads = max_threads
        self.timeout = timeout
    
    def check_port(self, ip, port):
        """
        Verifica se a porta está aberta usando socket TCP.
        Retorna True se estiver aberta, False caso contrário.
        Isso é muito mais rápido que esperar o ADB dar timeout.
        """
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        try:
            result = s.connect_ex((ip, port))
            s.close()
            return result == 0
        except:
            return False

    def adb_connect(self, ip, port=5555):
        """
        Tenta conectar via ADB. Retorna mensagem de sucesso ou None.
        """
        # 1. Verifica porta primeiro (Scan Rápido)
        if not self.check_port(ip, port):
            return None

        # 2. Se porta aberta, tenta ADB Connect
        cmd = ["adb", "connect", f"{ip}:{port}"]
        
        # CREATE_NO_WINDOW = 0x08000000
        creationflags = 0x08000000 if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
        if not creationflags and os.name == 'nt':
             creationflags = 0x08000000 # Hardcoded para garantir

        try:
            result = subprocess.run(
                cmd, 
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE, 
                text=True, 
                timeout=5,
                creationflags=creationflags
            )
            output = result.stdout.strip() + result.stderr.strip()
            
            if "connected" in output.lower() or "already connected" in output.lower():
                return f"[SUCESSO] {ip}:{port} - {output}"
            else:
                return f"[FALHA] {ip}:{port} - {output}"
        except subprocess.TimeoutExpired:
            return f"[TIMEOUT] {ip}:{port}"
        except Exception as e:
            return f"[ERRO] {ip}:{port} - {str(e)}"

    def scan_ips(self, ip_list, port, progress_callback=None):
        """
        Escaneia uma lista de IPs.
        progress_callback(msg): função para receber logs em tempo real.
        """
        results = []
        if progress_callback:
            progress_callback(f"Iniciando scan de {len(ip_list)} IPs com {self.max_threads} threads...")

        with ThreadPoolExecutor(max_workers=self.max_threads) as executor:
            # Mapeia futuros
            future_to_ip = {executor.submit(self.adb_connect, ip, port): ip for ip in ip_list}

            for future in as_completed(future_to_ip):
                ip = future_to_ip[future]
                try:
                    result = future.result()
                    if result:
                        results.append(result)
                        if progress_callback:
                            progress_callback(result)
                except Exception as e:
                    if progress_callback:
                        progress_callback(f"[ERRO CRÍTICO] Thread {ip}: {e}")

        if progress_callback:
            progress_callback(f"Scan finalizado. Dispositivos encontrados: {len(results)}")
        
        return results

# Bloco para teste isolado
if __name__ == "__main__":
    scanner = NetworkScanner()
    # Exemplo: scan local
    ips = [f"192.168.1.{i}" for i in range(1, 20)]
    print(scanner.scan_ips(ips, 5555, print))
