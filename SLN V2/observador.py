#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
observador.py - SLN V2
Observador continuo de dispositivo Android via ADB (sem dependencias externas).
Captura durante N segundos:
  - XML da hierarquia (ui dump) a cada intervalo
  - Screenshot (PNG) a cada intervalo (com timestamp exato coincidindo com o dump)
  - Eventos de foco/activity (dumpsys window) 
  - Log de eventos cronometrado

Uso:
    python observador.py <device_id> [duracao_segundos] [intervalo_segundos] [--screenshots]
Ex:
    python observador.py 192.168.1.124:5555 300 2 --screenshots
"""
import subprocess
import os
import sys
import time
import json
import base64
from datetime import datetime

def get_timestamp():
    return datetime.now().strftime('%Y%m%d_%H%M%S_%f')

def log(msg, logfile, console=True):
    line = f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] {msg}"
    if console:
        print(line, flush=True)
    try:
        with open(logfile, 'a', encoding='utf-8') as f:
            f.write(line + "\n")
    except Exception:
        pass

def run_adb(device, args, timeout=15):
    """Executa comando adb e retorna stdout bytes"""
    try:
        r = subprocess.run(['adb', '-s', device] + args,
                           capture_output=True, timeout=timeout)
        return r.stdout
    except Exception as e:
        return b''

def get_window_focus(device):
    out = run_adb(device, ['shell', 'dumpsys', 'window']).decode('utf-8', 'ignore')
    focus = None
    for line in out.splitlines():
        if 'mCurrentFocus' in line:
            focus = line.strip()
            break
    return focus if focus else '?'

def get_hierarchy(device):
    """Retorna XML da hierarquia via uiautomator dump (retry simples)"""
    run_adb(device, ['shell', 'uiautomator', 'dump', '/sdcard/window_dump_sln.xml'])
    xml = run_adb(device, ['shell', 'cat', '/sdcard/window_dump_sln.xml'])
    return xml

def get_current_app(device):
    """Retorna package/activity atual (melhor esforço)"""
    out = run_adb(device, ['shell', 'dumpsys', 'activity', 'activities']).decode('utf-8', 'ignore')
    for line in out.splitlines():
        if 'topResumedActivity' in line or 'mResumedActivity' in line:
            if 'ActivityRecord{' in line and '/' in line:
                try:
                    part = line.split('ActivityRecord{')[-1]
                    pkg_act = part.split(' ')[1] if ' ' in part else part
                    return pkg_act
                except Exception:
                    pass
    return '?'
    # fallback: mFocusedApp
    out2 = run_adb(device, ['shell', 'dumpsys', 'window']).decode('utf-8', 'ignore')
    for line in out2.splitlines():
        if 'mFocusedApp' in line and '/' in line:
            try:
                return line.split('ActivityRecord{')[-1].split(' ')[1]
            except Exception:
                pass
    return '?'

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    device = sys.argv[1]
    duracao = int(sys.argv[2]) if len(sys.argv) > 2 else 300
    intervalo = float(sys.argv[3]) if len(sys.argv) > 3 else 2.0
    with_screenshots = '--screenshots' in sys.argv

    base_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'captures')
    os.makedirs(base_dir, exist_ok=True)
    session = datetime.now().strftime('%Y%m%d_%H%M%S')
    sess_dir = os.path.join(base_dir, f'session_{session}')
    os.makedirs(sess_dir, exist_ok=True)

    logfile = os.path.join(sess_dir, 'eventos.log')
    hw_dir = os.path.join(sess_dir, 'hierarchy')
    ss_dir = os.path.join(sess_dir, 'screenshots')
    os.makedirs(hw_dir, exist_ok=True)
    os.makedirs(ss_dir, exist_ok=True)

    log(f"=== Observador iniciado | device={device} | duracao={duracao}s | intervalo={intervalo}s | screenshots={with_screenshots} ===", logfile, True)

    inicio = time.time()
    tick = 0
    last_focus = None

    while time.time() - inicio < duracao:
        tick += 1
        t0 = time.time()
        ts = get_timestamp()

        # 1. Focus / Activity
        focus = get_window_focus(device)
        app = get_current_app(device)

        if focus != last_focus:
            log(f"[FOCUS] {focus} | app={app}", logfile, True)
            last_focus = focus

        # 2. Hierarchy dump
        xml = get_hierarchy(device)
        ts2 = time.time()
        xml_path = os.path.join(hw_dir, f'h_{ts}.xml')
        try:
            with open(xml_path, 'wb') as f:
                f.write(xml)
        except Exception as e:
            log(f"[ERRO] falha ao salvar xml: {e}", logfile, True)

        # 3. Screenshot
        ss_path = None
        if with_screenshots:
            run_adb(device, ['shell', 'screencap', '-p', '/sdcard/sln_ss.png'])
            img = run_adb(device, ['shell', 'cat', '/sdcard/sln_ss.png'])
            ts3 = time.time()
            if len(img) > 100:
                ss_path = os.path.join(ss_dir, f'ss_{ts}.png')
                with open(ss_path, 'wb') as f:
                    f.write(img)
                log(f"[SS] ts={ts} bytes={len(img)} xml_elapsed={ts2-t0:.2f}s ss_elapsed={ts3-t0:.2f}s", logfile, False)

        # 4. Resumo do dump (atributos principais) para inspecao rapida
        try:
            from xml.etree import ElementTree as ET
            root = ET.fromstring(xml)
            nodes = root.findall('.//node')
            text_elems = []
            ids = []
            for n in nodes:
                t = n.attrib.get('text', '')
                rid = n.attrib.get('resource-id', '')
                cls = n.attrib.get('class', '')
                desc = n.attrib.get('content-desc', '')
                bounds = n.attrib.get('bounds', '')
                clickable = n.attrib.get('clickable', '')
                if t or rid or desc:
                    entry = {'cls': cls.split('.')[-1], 'text': t, 'id': rid, 'desc': desc,
                             'clickable': clickable, 'bounds': bounds}
                    text_elems.append(entry)
                    if rid and rid not in ids:
                        ids.append(rid)
            log(f"[DUMP] nodes={len(nodes)} texts={len(text_elems)}", logfile, False)
            # Salva resumo compacto em json (um por tick)
            summary_path = os.path.join(sess_dir, 'summary.jsonl')
            with open(summary_path, 'a', encoding='utf-8') as f:
                rec = {
                    'tick': tick, 'ts': ts, 'focus': focus, 'app': app,
                    'xml_path': os.path.relpath(xml_path, sess_dir),
                    'ss_path': os.path.relpath(ss_path, sess_dir) if ss_path else None,
                    'nodes': len(nodes),
                    'elems': text_elems[:50],
                    'elapsed_dump': round(ts2-t0, 2)
                }
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception as e:
            log(f"[ERRO] parse dump: {e}", logfile, True)

        # 5. Aguarda ate completar o intervalo
        elapsed = time.time() - t0
        if elapsed < intervalo:
            time.sleep(intervalo - elapsed)

    log(f"=== Observador FINALIZADO | ticks={tick} | pasta={sess_dir} ===", logfile, True)
    print(f"\n[OK] Sessao capturada em: {sess_dir}")

if __name__ == '__main__':
    main()