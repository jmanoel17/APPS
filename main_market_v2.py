"""
main_market_v2.py — Versão otimizada para 150+ dispositivos simultâneos
Changelog vs main_market.py:
  - Lazy import cv2/numpy (economiza ~80MB RAM/processo)
  - gc.collect() periódico (a cada 10 rodadas)
  - safe_connect com exponential backoff (max 20 retries)
  - requests.Session com timeout e pool limitada
  - Limpeza explícita de XML dumps (try/finally)
  - Subprocess cleanup no take_screenshot (Popen + communicate)
  - Bug fix: linha 909 original — tentar_clicar_open_store_topo sem (dump_xml)
"""

import sys
import time
import json
import os
import random
import subprocess
import re
import gc
import uiautomator2 as u2
from datetime import datetime
import requests
from requests.adapters import HTTPAdapter
import math
import xml.etree.ElementTree as ET

# cv2 e numpy são importados sob demanda (lazy) para economizar ~80MB RAM/processo
# Só são carregados quando take_screenshot(save_file=True) é chamado
_cv2 = None
_np = None

def _lazy_import_cv2():
    """Importa cv2 e numpy sob demanda (lazy import)"""
    global _cv2, _np
    if _cv2 is None:
        import cv2
        import numpy as np
        _cv2 = cv2
        _np = np
    return _cv2, _np


# Função auxiliar para timestamp
def get_timestamp():
    """Retorna timestamp formatado para logs"""
    return datetime.now().strftime('[%H:%M:%S]')


# ==================== CONFIGURAÇÕES GLOBAIS ====================
device = sys.argv[1]
modo_ponto = int(sys.argv[2])
tempo_inicio = 5
package = "com.sln.prepositions"
rodadas = 0
d = None
qtd_reset = 0
espera_anuncio = 0
cliques_sem_anuncio = 0

# Session otimizada com timeout padrão e pool limitada
ai_session = requests.Session()
ai_session.mount('http://', HTTPAdapter(pool_connections=2, pool_maxsize=2, max_retries=1))

# Seletores de fechamento de anúncios (Consolidado)
SELECTORS = [
    "inmobi_close_btn", "com.inmobi.rendering.inmobi_close_btn", "com.sln.prepositions.Quote:id/inmobi_close_btn",
    "com.unity3d.services:id/closeButton", "com.google.android.gms:id/close_button",
    "close_button", "btn_close", "btnClose", "iv_close",
    "com.sln.prepositions.Quote:id/ia_clickable_close_button", "applovin_close_button",
    "vungle_close_button", "m-playable-close", "m-playable-skip",
    "com.sln.prepositions.Quote:id/mbridge_video_common_alertview_confirm_button",
    "com.emaphoto.pic:id/mbridge_rl_playing_close",
    "com.sln.prepositions.Quote:id/ia_iv_close_button", "com.sln.prepositions.Quote:id/ia_fl_close_button",
    "com.sln.prepositions.Quote:id/mbridge_rl_playing_close", "com.sln.prepositions.Quote:id/bigo_ad_btn_close",
    "interstitial_close_button", "ia_iv_close_button", "ia_fl_close_button",
    "mbridge_rl_playing_close", "bigo_ad_btn_close",
    "com.flx.duplicate.files.clean:id/thinkup_myoffer_btn_close_id",
    "com.best.quick.browser:id/i", "com.sln.prepositions.Quote:id/ia_tv_skip",
    "com.sln.prepositions.Quote:id/mbridge_windwv_close", "com.sln.prepositions.Quote:id/iv_ad_close",
    "com.sln.prepositions.Quote:id/ad_close", "skipBtnImg", "skip-button", "inmobi_skip_btn", "btn-skip", "close"
]

TEXTS = ["", "Confirm choices", "", "Close", "Continue", "Ir para o app", "AGORA NÃO", "FECHAR", "Fechar",
         "Skip", "Ignorar", "Continuar", "x", "X", "Aceitar", "CLOSE", "Cerrar",
         "Concordar com tudo", "NO THANKS", "No Thanks", "no thanks", "No thanks", "skipAllIcon", "close"]

DESCRIPTIONS = ["IACloseButton", "Close", "Fechar", "No Thanks", "no thanks", "NO THANKS", "Skip All", "Interstitial close button"]

WHITELIST = [
    "com.googlecode.eyesfree.setorientation", "com.arlosoft.macrodroid",
    "com.zhiliaoapp.musically", "com.oak.cryptosense", "com.israbyte.dropbin",
    "com.appswork.inspiracoes", "com.workdigital.menteprospera", "com.sln.prepositions",
    "com.frases.frasesvip", "com.workdigital.radiomusic", "com.cmastudio.jokenpoduelo",
    "com.lkss.oraculodigital", "com.sln.prepositions", "com.sln.marketfrases", "com.github.uiautomator"
]

XPATHS_PERSONALIZADOS = [
    '//*[@text=""]',
    '//*[@text="Close"]',
    '//*[@text="Skip"]',
    '//*[@content-desc="Skip"]',
    '//*[@resource-id="com.android.chrome:id/menu_button_wrapper"]',
    '//*[@resource-id="com.sln.prepositions.Quote:id/ia_tv_skip"]',
    '//*[@resource-id="com.sln.prepositions.Quote:id/ia_clickable_close_button"]',
    '//*[@resource-id="com.sln.prepositions.Quote:id/mbridge_iv_close"]',
    '//*[@content-desc="Anúncio fechado"]',
    '//*[@resource-id="android:id/content"]/android.widget.RelativeLayout[1]/android.widget.LinearLayout[1]/android.widget.ImageView[1]',
    '//*[@resource-id="te-clr1-4f8bce0e-6193-4784-839d-08f4cd5b0649-icon"]',
    '//*[@resource-id="te-clr1-f4f660de-3a14-4e02-bdf3-70f2c804dc2d-icon"]',
    '//*[@resource-id="android:id/content"]/android.widget.FrameLayout[1]/android.widget.FrameLayout[1]/android.widget.ImageView[1]',
    '//*[@resource-id="skipButton"]'
]

BOTOES_RANDOMICOS = [
    # Botões de navegação e funcionais
    'home_about_button',
    'home_card_new_reading',
    'home_card_history',
    'save_reading_button',
    'reveal_cards_button',
    'question_input',
    'mode_selector',
    # IDs antigos (mantidos por garantia/compatibilidade se ainda existirem)
    'com.sln.prepositions.Quote:id/flash',
    'com.sln.prepositions.Quote:id/Energiafm',
]

FORCAR_VOLTAR_KEYWORDS = [
    "browser", "sg.bigo.ads.api.AdActivity", "com.ironsource.sdk.controller.OpenUrlActivity"
]

IGNORAR_PACKAGES = ["com.best.quick.browser"]


# ==================== FUNÇÕES HELPER OTIMIZADAS ====================

def set_window_title(title):
    if os.name == 'nt':
        os.system(f"title {title}")
    else:
        sys.stdout.write(f"\033]0;{title}\007")
        sys.stdout.flush()

def safe_connect(device_id, max_retries=20):
    """Conecta ao dispositivo com retry e exponential backoff"""
    global d
    delay = 5
    for attempt in range(1, max_retries + 1):
        try:
            print(f"{get_timestamp()} Conectando ao dispositivo: {device_id} (tentativa {attempt}/{max_retries})")
            d = u2.connect(device_id)
            if d.info:
                print(f"{get_timestamp()} Conectado com sucesso!")
                return
            else:
                raise Exception("Falha na conexão. Dispositivo não disponível.")
        except Exception as e:
            print(f"{get_timestamp()} Erro ao conectar: {e}")
            if attempt >= max_retries:
                print(f"{get_timestamp()} [FATAL] Máximo de tentativas ({max_retries}) atingido. Saindo.")
                sys.exit(1)
            print(f"{get_timestamp()} Aguardando {delay:.0f}s antes de tentar novamente...")
            time.sleep(delay)
            delay = min(delay * 1.5, 60)  # Backoff: 5 → 7.5 → 11.25 → ... → max 60s

def get_hierarchy_data():
    """Obtém e parseia a hierarquia do dispositivo uma única vez"""
    try:
        tree_str = d.dump_hierarchy()
        return ET.fromstring(tree_str)
    except Exception as e:
        print(f"{get_timestamp()} [ERRO] Falha ao obter hierarquia: {e}")
        return None

def is_doze(d):
    """Verifica se o dispositivo está em modo Doze/Sleep e o acorda"""
    try:
        out = d.shell("dumpsys power").output
        if "mWakefulness=Dozing" in out or "mWakefulness=Asleep" in out:
            print(f"{get_timestamp()} [LOG] Device em DOZE. Acordando tela...")
            d.press("power")
            time.sleep(1)
            return True
        return False
    except Exception as e:
        print(f"{get_timestamp()} [ERRO] Falha ao verificar/corrigir Doze: {e}")
        return False

def find_element_in_hierarchy(dump_xml, attr_name, attr_values):
    """Busca genérica de elementos na hierarquia"""
    if dump_xml is None: return None
    for node in dump_xml.iter("node"):
        value = node.attrib.get(attr_name, "")
        if value in attr_values:
            return value
    return None

def clicar_por_atributo(dump_xml, attr_name, attr_values, click_func_factory):
    """Função genérica para clicar em elementos por atributo"""
    found = find_element_in_hierarchy(dump_xml, attr_name, attr_values)
    if found:
        print(f"{get_timestamp()} [LOG] Elemento '{found}' detectado ({attr_name}). Tentando clicar...")
        try:
            element = click_func_factory(found)
            if element is None:
                return True
            
            element.click()
            print(f"{get_timestamp()} [LOG] Clique em '{found}' enviado com sucesso.")
            return True
        except Exception as e:
            print(f"{get_timestamp()} [ERRO] Falha ao tentar clicar em {found}: {e}")
    return False

def tentar_clicar_xpath(xpaths):
    """Tenta clicar em elementos via XPath"""
    for xpath in xpaths:
        try:
            el = d.xpath(xpath)
            if el.exists:
                print(f"{get_timestamp()} [LOG] Elemento encontrado via XPath: {xpath}. Clicando...")
                el.click()
                return True
        except Exception as e:
            print(f"{get_timestamp()} [ERRO] Falha ao clicar em XPath: {e}")
    return False

def tentar_clicar_botao_vazio(dump_xml):
    """Tenta clicar em botões com texto vazio"""
    if find_element_in_hierarchy(dump_xml, "text", ["Ir para o app"]):
        print(f"{get_timestamp()} [LOG] Texto 'Ir para o app' detectado. Ignorando clique via XPath.")
        return False

    try:
        btns = [b for b in d.xpath('//android.widget.Button[@text=""]').all()
                if not b.info.get("focusable", True)]
        if btns:
            print(f"{get_timestamp()} [LOG] Botão com texto vazio encontrado. Clicando...")
            btns[0].click()
            return True
    except Exception as e:
        print(f"{get_timestamp()} [ERRO] Falha ao clicar via XPath: {e}")
    return False

def tentar_clicar_textview_especial(dump_xml):
    """Tenta clicar em TextViews especiais (Fechar, AGORA NÃO, No Thanks)"""
    for txt in ["Fechar", "AGORA NÃO", "No Thanks", "no thanks", "NO THANKS", "Google Play"]:
        for node in dump_xml.iter("node"):
            if (node.attrib.get("text", "") == txt and
                    node.attrib.get("class", "") == "android.widget.TextView"):
                print(f"{get_timestamp()} [LOG] TextView '{txt}' encontrado. Clicando...")
                d(text=txt, className="android.widget.TextView").click()
                time.sleep(0.3)
                return True
    return False


def tentar_clicar_open_store_topo(dump_xml):
    """Clica no 'Open Store' apenas se estiver na região superior da tela"""

    for node in dump_xml.iter("node"):
        if (node.attrib.get("text", "") == "Open Store" and
                node.attrib.get("class", "") == "android.widget.TextView"):

            bounds = node.attrib.get("bounds", "")

            try:
                # exemplo: [99,162][270,175]
                coords = bounds.replace("[", "").replace("]", ",").split(",")
                x1, y1, x2, y2 = map(int, coords[:4])

                # 🔥 condição: topo da tela
                if x1 < 180 and y1 < 250:
                    print(f"{get_timestamp()} [LOG] 'Open Store' no topo detectado. Clicando...")

                    # clique no centro do elemento
                    d.click((x1 + x2) // 2, (y1 + y2) // 2)
                    time.sleep(0.3)
                    return True

            except Exception as e:
                print(f"{get_timestamp()} [ERRO] Falha ao processar bounds: {e}")

    return False


def remover_apps_nao_autorizados(device_serial, whitelist, force=False):
    """Remove apps não autorizados - otimizado para não rodar sempre"""
    global rodadas
    # Só roda a cada 30 rodadas ou se forçado
    if not force and rodadas % 30 != 0:
        return

    try:
        output = d.shell("pm list packages -3").output
        pacotes_terceiros = {line.replace("package:", "").strip() for line in output.strip().splitlines() if
                             line.strip()}
        a_remover = [pkg for pkg in pacotes_terceiros if pkg not in whitelist]

        if not a_remover:
            print(f"{get_timestamp()} [OK] Nenhum app não autorizado detectado.")
            return

        print(f"{get_timestamp()} [ALERTA] {len(a_remover)} apps não autorizados serão removidos.")
        for pacote in a_remover:
            print(f"{get_timestamp()}  - Desinstalando: {pacote}")
            d.app_uninstall(pacote)
        print(f"{get_timestamp()} [INFO] Remoção concluída.")
    except Exception as e:
        print(f"{get_timestamp()} [ERRO] Falha ao remover apps não autorizados: {e}")

def detectar_e_voltar_se_browser(d):
    """Detecta e sai de browsers/AdActivities indesejadas"""
    pressionou_back = False
    tentativas_back = 0

    while True:
        if tentativas_back >= 5:
            try:
                current_package = d.app_current().get('package', '')
            except:
                current_package = ''
            print(f"{get_timestamp()} [ALERTA] Preso em loop de voltar ({tentativas_back}x). Forçando parada de '{current_package}'...")
            try:
                if current_package:
                    d.app_stop(current_package)
            except:
                pass
            time.sleep(1)
            break

        current_info = d.app_current()
        current_activity = current_info.get('activity')
        current_package = current_info.get('package')

        if current_package in IGNORAR_PACKAGES:
            print(f"{get_timestamp()} [LOG] Ignorando pacote '{current_package}'.")
            break

        # Verifica keywords proibidas
        ainda_proibido = any(kw.lower() in str(current_activity).lower() or
                             kw.lower() in str(current_package).lower()
                             for kw in FORCAR_VOLTAR_KEYWORDS)

        # Verifica WebView de anúncio
        try:
            xpath_ad = '//*[@resource-id="com.sln.prepositions:id/ia_ad_content"]/android.widget.RelativeLayout[1]/android.widget.RelativeLayout[2]/android.webkit.WebView[1]'
            if d.xpath(xpath_ad).exists:
                print(f"{get_timestamp()} [LOG] WebView de anúncio detectado. Pressionando BACK...")
                d.press("back")
                time.sleep(0.5)
                pressionou_back = True
                tentativas_back += 1
                continue
        except Exception as e:
            print(f"{get_timestamp()} [ERRO] Verificação de WebView falhou: {e}")

        if not ainda_proibido:
            break

        print(f"{get_timestamp()} [LOG] Activity proibida '{current_activity}'. Pressionando BACK...")
        d.press("back")
        time.sleep(0.5)
        pressionou_back = True
        tentativas_back += 1

    return pressionou_back


def verificar_e_corrigir_package():
    """Verifica se está no package correto e corrige se necessário"""
    current_info = d.app_current()
    current_package = current_info['package']

    if current_package != package:
        print(f"{get_timestamp()} [ALERTA] Saiu do pacote '{package}' para '{current_package}'. Corrigindo...")
        d.app_stop(current_package)
        d.app_start(package)
        time.sleep(1)
        return d.app_current()
    return current_info


def preparar_ambiente_anuncio():
    """
    Função para preparar o ambiente antes de iniciar/receber anúncios.
    """
    print("[INFO] Preparando ambiente para anúncios...")
    
    # 1. Garante que estamos no app certo
    verificar_e_corrigir_package()
    
    # 2. Fecha diálogos de erro se houver
    check_and_close_errors(d)
    
    print("[INFO] Ambiente preparado.")


def take_screenshot(device_id, base_dir='screenshots', save_file=True):
    """
    Captura screenshot usando ADB direto.
    Se save_file=True, salva em disco e retorna o caminho (carrega cv2/numpy sob demanda).
    Se save_file=False, retorna os bytes da imagem (png) em memória (sem cv2/numpy).
    """
    proc = None
    try:
        # Usa Popen + communicate para melhor cleanup de handles no Windows
        si = None
        creationflags = 0
        if os.name == 'nt':
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            creationflags = 0x08000000  # CREATE_NO_WINDOW

        proc = subprocess.Popen(
            f'adb -s {device_id} exec-out screencap -p',
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            startupinfo=si,
            creationflags=creationflags
        )
        raw_bytes, stderr = proc.communicate(timeout=10)

        if not raw_bytes:
            print(f"{get_timestamp()} [ERRO] Screenshot vazia via ADB.")
            return None

        if save_file:
            # Lazy import de cv2/numpy — só carrega quando necessário
            cv2, np = _lazy_import_cv2()

            try:
                activity_name = d.app_current()['activity']
            except:
                activity_name = "unknown"

            folder_path = os.path.join(base_dir, activity_name)
            os.makedirs(folder_path, exist_ok=True)

            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            gray_image_path = os.path.join(folder_path, f"{timestamp}.png")

            # Decodifica para salvar em grayscale
            image_array = np.frombuffer(raw_bytes, np.uint8)
            image = cv2.imdecode(image_array, cv2.IMREAD_COLOR)

            if image is None:
                print(f"{get_timestamp()} [ERRO] Falha ao decodificar imagem do ADB.")
                return None

            gray_image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            cv2.imwrite(gray_image_path, gray_image)
            
            # Limpeza explícita de arrays grandes
            del image_array, image, gray_image

            print(f"{get_timestamp()} [INFO] Screenshot salva em: {gray_image_path}")
            return gray_image_path
        else:
            return raw_bytes

    except subprocess.TimeoutExpired:
        print(f"{get_timestamp()} [ERRO] Timeout ao tirar screenshot via ADB.")
        if proc:
            try:
                proc.kill()
                proc.communicate(timeout=2)
            except:
                pass
        return None
    except Exception as e:
        print(f"{get_timestamp()} [ERRO] Falha ao tirar screenshot via ADB: {e}")
        return None
    finally:
        # Garante cleanup do handle do processo
        if proc and proc.returncode is None:
            try:
                proc.kill()
            except:
                pass

def click_botao_por_tela(d):
    """
    Detecta automaticamente a resolução física do device
    e executa o clique correto no botão NOVA FRASE.
    Retorna True se clicou, False se não mapeado.
    """
    try:
        w, h = d.window_size()
        tamanho_tela = f"{w}x{h}"
    except Exception:
        return False

    CLICKS = {
        # Samsung
        "1440x3200": (0.5, 0.74),
        "1080x2400": (0.5, 0.72),
        "1080x2160": (0.5, 0.75),
        "720x1600": (0.5, 0.75),
        "720x1520": (0.5, 0.75),
    }

    coords_perc = CLICKS.get(tamanho_tela)

    if not coords_perc:
        # Clique padrão genérico se não mapeado
        d.click(0.5, 0.75)
        return True

    d.click(coords_perc[0], coords_perc[1])
    return True

def reset_id():
    """Reseta o ID de publicidade"""
    print("=" * 52)
    print(f"{get_timestamp()} Abrindo configurações de ID de publicidade...")
    os.system(f"adb -s {device} shell am start -a com.google.android.gms.settings.ADS_PRIVACY")
    time.sleep(2)
    
    try:
        reset_options = [
            "Redefinir ID de publicidade", "Redefinir código de publicidade",
            "Repor ID de publicidade", "Reset advertising ID", "Reset Advertising ID"
        ]
        
        for option in reset_options:
            if d(text=option).exists:
                print(f"{get_timestamp()} Elemento '{option}' encontrado. Clicando...")
                d(text=option).click()
                break
        else:
            print(f"{get_timestamp()} Elemento de reset não encontrado.")

        time.sleep(1.5)
        if d(resourceId="android:id/button1").exists:
            print(f"{get_timestamp()} Botão 'OK' encontrado. Clicando...")
            d(resourceId="android:id/button1").click()
        else:
            print(f"{get_timestamp()} Botão 'OK' não encontrado.")
    except Exception as e:
        print(f"{get_timestamp()} Erro ao resetar ID: {e}")

def clicar_botao_randomico(d):
    """Clica em um botão aleatório (resourceId ou XPATH) se disponível"""
    botoes = BOTOES_RANDOMICOS.copy()
    random.shuffle(botoes)

    for botao in botoes:
        if botao.startswith('/'):
            if d.xpath(botao).exists:
                print(f"{get_timestamp()} [LOG] Botão aleatório (XPATH) encontrado: {botao}. Clicando...")
                d.xpath(botao).click()
                return True
        else:
            if d(resourceId=botao).exists:
                print(f"{get_timestamp()} [LOG] Botão aleatório (ID) encontrado: {botao}. Clicando...")
                d(resourceId=botao).click()
                return True
            elif d(resourceId=f"{package}:id/{botao}").exists:
                print(f"{get_timestamp()} [LOG] Botão aleatório (ID) encontrado: {package}:id/{botao}. Clicando...")
                d(resourceId=f"{package}:id/{botao}").click()
                return True

    print(f"{get_timestamp()} [LOG] Nenhum botão aleatório disponível.")
    return False


def check_and_close_errors(d):
    """Verifica e fecha diálogos de erro do sistema (Timeout de 0.5s)"""
    try:
        if d(resourceId="android:id/aerr_wait").exists(timeout=0.5):
            print(f"{get_timestamp()} [LOG] Fechando diálogo 'aerr_wait'...")
            d(resourceId="android:id/aerr_wait").click()
            time.sleep(0.3)
        if d(resourceId="android:id/aerr_close").exists(timeout=0.5):
            print(f"{get_timestamp()} [LOG] Fechando diálogo 'aerr_close'...")
            d(resourceId="android:id/aerr_close").click()
            time.sleep(0.3)
    except: pass

def tentar_deteccao_ia():
    """Tenta detectar e clicar usando IA via API Server (In-Memory)"""
    img_bytes = take_screenshot(device, 'screenshots', save_file=False)

    if not img_bytes:
        print(f"{get_timestamp()} [ERRO] Não foi possível capturar a imagem para análise.")
        return False

    try:
        url = "http://localhost:8000/detect"
        files = {'file': ('screenshot.png', img_bytes, 'image/png')}

        # Timeout de 5s para não travar o script se o servidor estiver off
        response = ai_session.post(url, files=files, timeout=5)

        if response.status_code == 200:
            data = response.json()
            count = data.get("count", 0)
            if data.get("success") and count > 0:
                print(f"{get_timestamp()} [INFO] IA encontrou {count} objetos. Iniciando sequência de cliques inteligente...")

                # Estado inicial para verificação
                try:
                    initial_info = d.app_current()
                    initial_activity = initial_info.get('activity')
                    initial_package = initial_info.get('package')
                except:
                    initial_activity = None
                    initial_package = None

                clicked_points = []

                # Separa em prioritários (topo da tela) e outros
                detections = data["detections"]
                top_priority = [det for det in detections if det['y'] <= 250]
                others = [det for det in detections if det['y'] > 250]

                top_priority.sort(key=lambda k: k['confidence'], reverse=True)
                others.sort(key=lambda k: k['confidence'], reverse=True)

                final_detections = top_priority + others

                for i, obj in enumerate(final_detections):
                    x, y = obj['x'], obj['y']
                    conf = obj['confidence']

                    # Filtro de proximidade (50 pixels)
                    too_close = False
                    for cx, cy in clicked_points:
                        if math.hypot(x - cx, y - cy) < 50:
                            too_close = True
                            break

                    if too_close:
                        print(f"{get_timestamp()} [INFO] Ponto ({x}, {y}) ignorado por estar muito próximo de um anterior.")
                        continue

                    print(f"{get_timestamp()} [INFO] [{i+1}/{count}] Clicando em ({x}, {y}) - Confiança: {conf:.2f}")
                    d.click(x, y)
                    clicked_points.append((x, y))

                    if len(clicked_points) >= 3:
                        print(f"{get_timestamp()} [INFO] Limite de 3 cliques atingido. Parando por aqui.")
                        break

                    time.sleep(1.0)

                    # Smart Retry: Verifica se abriu navegador ou saiu do app
                    browser_detected = detectar_e_voltar_se_browser(d)

                    package_changed = False
                    try:
                        curr_pkg = d.app_current()['package']
                        if curr_pkg != package:
                            print(f"{get_timestamp()} [INFO] Saiu do pacote para '{curr_pkg}'. Corrigindo...")
                            verificar_e_corrigir_package()
                            package_changed = True
                    except:
                        pass

                    if browser_detected or package_changed:
                        print(f"{get_timestamp()} [INFO] Clique inválido (Browser/Outro App). Tentando próximo ponto...")
                        continue

                    # Verifica se mudou de tela
                    try:
                        current_info = d.app_current()
                        current_activity = current_info.get('activity')
                        current_package = current_info.get('package')

                        if current_package != initial_package or current_activity != initial_activity:
                            if '.MainActivity' in str(current_activity):
                                print(f"{get_timestamp()} [SUCESSO] Anúncio fechado! Voltou para MainActivity. Parando cliques.")
                            else:
                                print(f"{get_timestamp()} [INFO] Tela mudou para '{current_activity}'. Parando cliques para verificação.")
                            return True
                    except:
                        pass

                print(f"{get_timestamp()} [AVISO] Todos os pontos foram clicados, mas a tela parece não ter mudado.")
                return True
            else:
                print(f"{get_timestamp()} [AVISO] Nenhum objeto detectado pela API.")
        else:
            print(f"{get_timestamp()} [ERRO] Erro na API: {response.status_code} - {response.text}")

        return False

    except requests.exceptions.ConnectionError:
        print(f"{get_timestamp()} [ERRO] Falha ao conectar no servidor de IA (aiMain.py). Ele está rodando?")
        return False
    except Exception as e:
        print(f"{get_timestamp()} [ERRO] Falha na requisição de IA: {e}")
        return False

# ==================== LÓGICA PRINCIPAL ====================


def tentar_clicar_anuncio_overlay(dump_xml):
    """Tenta clicar em botões de fechar que podem ser overlays na MainActivity"""
    if dump_xml is None: return False
    
    # 1. Tenta por ID (SELECTORS)
    if clicar_por_atributo(dump_xml, "resource-id", SELECTORS, lambda x: d(resourceId=x)):
        return True
    
    # 2. Tenta por Texto (TEXTS)
    if clicar_por_atributo(dump_xml, "text", TEXTS, lambda x: d(text=x)):
        return True
    
    # 3. Tenta XPaths Personalizados
    if tentar_clicar_xpath(XPATHS_PERSONALIZADOS):
        return True

    # 4. Outras descrições
    if clicar_por_atributo(dump_xml, "content-desc", DESCRIPTIONS, lambda x: d(description=x)):
        return True

    # 5. Tenta fechar pelo X (Coordenadas)
    if fechar_anuncio_pelo_x(d, dump_xml, tentativas=1):
        return True

    return False

def processar_jogo(d):
    print(f"{get_timestamp()} [LOG] Iniciando loop do Market Frases (Otimizado V2)...")
    
    # Trava rotação da tela fora do loop
    try:
        d.shell("settings put system accelerometer_rotation 0")
        d.shell("settings put system user_rotation 0")
    except:
        pass

    xpath_nova_frase = '//*[@text="NOVA FRASE"]'
    max_tentativas_botao = 5
    tentativas_botao = 0

    while True:
        # 0. Verifica estado inicial
        try:
            check_and_close_errors(d)
            curr = d.app_current()
            is_main = curr['package'] == package and '.MainActivity' in curr['activity']
            
            el_botao_check = d.xpath(xpath_nova_frase)
            
            if not is_main:
                if curr['package'] == package:
                    print(f"{get_timestamp()} [LOG] No app, mas fora da MainActivity ({curr['activity']}).")
                    return True 
                else:
                    print(f"{get_timestamp()} [ALERTA] Fora do app ({curr['package']}). Voltando...")
                    d.app_start(package)
                    time.sleep(2)
                    continue

            if not el_botao_check.exists:
                print(f"{get_timestamp()} [LOG] Botão não visível. Verificando overlays...")
                dump_xml = get_hierarchy_data()
                try:
                    if tentar_clicar_anuncio_overlay(dump_xml):
                        print(f"{get_timestamp()} [SUCESSO] Overlay removido.")
                        continue
                finally:
                    dump_xml = None

                print(f"{get_timestamp()} [LOG] Tentando voltar internamente...")
                d.press("back")
                time.sleep(2)
                tentativas_botao += 1
                if tentativas_botao >= max_tentativas_botao:
                    return False
                continue
        except Exception as e:
            print(f"{get_timestamp()} [ERRO] Falha ao verificar estado: {e}")
            continue

        # 1. Delay inicial seguro
        print(f"{get_timestamp()} [LOG] MainActivity Home detectada. Aguardando 10s...")
        time.sleep(10)
        
        tentativas_botao = 0

        # 2. Clica no botão NOVA FRASE
        print(f"{get_timestamp()} [LOG] Buscando botão NOVA FRASE para clicar...")
        el_botao = d.xpath(xpath_nova_frase)
        
        if el_botao.exists:
            print(f"{get_timestamp()} [LOG] Clicando em NOVA FRASE...")
            el_botao.click()
            global cliques_sem_anuncio
            cliques_sem_anuncio += 1
        else:
            print(f"{get_timestamp()} [AVISO] Botão não encontrado via XPath. Tentando por tela...")
            if click_botao_por_tela(d):
                cliques_sem_anuncio += 1
            else:
                print(f"{get_timestamp()} [ERRO] Falha ao clicar por tela.")

        # 3. Aguarda checar se abriu anúncio
        print(f"{get_timestamp()} [LOG] Aguardando possível anúncio (até 10s)...")
        for _ in range(5):
            time.sleep(2)
            
            try:
                curr = d.app_current()
                if curr['package'] != package or '.MainActivity' not in curr['activity']:
                     print(f"{get_timestamp()} [SUCESSO] Saiu da MainActivity.")
                     return True
                elif '.MainActivity' in curr['activity']:
                     continue
            except: pass
            
            # 4. Verifica overlay após clique
            dump_xml = get_hierarchy_data()
            try:
                if tentar_clicar_anuncio_overlay(dump_xml):
                    print(f"{get_timestamp()} [SUCESSO] Overlay detectado após clique.")
                    cliques_sem_anuncio = 0
                    return True
            finally:
                dump_xml = None
            
        print(f"{get_timestamp()} [LOG] Continuou na MainActivity após clique.")

        # Reinicia a cada 10 cliques sem anuncio
        if cliques_sem_anuncio % 10 == 0 and cliques_sem_anuncio != 0:
            print(f"{get_timestamp()} [LOG] {cliques_sem_anuncio} cliques sem anúncio. Reiniciando app...")
            d.app_stop(package)
            time.sleep(1)
            d.app_start(package)

        # Reset após 25 cliques sem anuncio
        if cliques_sem_anuncio >= 25:
            print(f"{get_timestamp()} [LOG] {cliques_sem_anuncio} cliques sem anúncio. Resetando ID...")
            d.app_stop(package)
            time.sleep(1)
            reset_id()
            time.sleep(1)
            d.app_start(package)
            cliques_sem_anuncio = 0
            return False

        time.sleep(1)
    

def fechar_anuncio_pelo_x(d, dump_xml, tentativas=1):
    """
    Fecha anúncio clicando no botão X no canto superior direito.
    """
    try:
        w, h = d.window_size()
    except Exception:
        w, h = 1080, 2400

    min_x = w * 0.75
    max_y = h * 0.20

    for tentativa in range(tentativas):

        print(f"[LOG] Tentativa {tentativa+1} de fechar anúncio. (Verificando X > {int(min_x)}, Y < {int(max_y)})")

        for node in dump_xml.iter("node"):

            classe = node.attrib.get("class", "")
            clickable = node.attrib.get("clickable", "")
            bounds = node.attrib.get("bounds", "")
            text = node.attrib.get("text", "")

            is_valid_type = (classe == "android.widget.ImageView") or \
                            (classe == "android.widget.TextView" and text.lower() in ["x", "close"])

            if is_valid_type and clickable == "true" and bounds:

                nums = list(map(int, re.findall(r"\d+", bounds)))
                x1, y1, x2, y2 = nums

                if x1 > min_x and y2 < max_y:

                    cx = (x1 + x2) // 2
                    cy = (y1 + y2) // 2

                    print(f"[LOG] Clicando no X em {cx},{cy} (Bounds: {bounds})")
                    d.click(cx, cy)
                    time.sleep(0.8)

                    return True

        time.sleep(0.5)

    return False


def fechar_anuncio(d, activity_name, max_tentativas=5):
    """Função principal para fechar anúncios (Otimizada V2)"""
    start_time_global = time.time()
    MAX_TIMEOUT_ADS = 100
    sucesso = False
    pular_textos = False

    # Destravar erros sistêmicos
    try:
        check_and_close_errors(d)
    except: pass

    if ('.MainActivity' not in activity_name and
            '.SplashActivity' not in activity_name and
            '.adid.settings.AdsSettingsActivity' not in activity_name):

        # Fast Check para evitar espera desnecessária
        xpath_home_check = '//*[@text="NOVA FRASE"]'
        if not d.xpath(xpath_home_check).exists:
             if not d(text="NOVA FRASE").exists(timeout=1):
                print(f"{get_timestamp()} [LOG] Anúncio confirmado ({activity_name}). Aguardando {tempo_inicio}s...")
                time.sleep(tempo_inicio)
                d.press("back")

        for ciclo in range(30):
            tempo_decorrido = time.time() - start_time_global
            if tempo_decorrido > MAX_TIMEOUT_ADS:
                print(f"{get_timestamp()} [ALERTA] Timeout global em fechar_anuncio.")
                break

            print(f"{get_timestamp()} [LOG] Ciclo de fechamento {ciclo + 1}/30... (T: {int(tempo_decorrido)}s)")

            # FAST PATH: Sem timeout no exists para poupar desempenho
            for fast_id in ["com.sln.prepositions:id/ad_close", "com.google.android.gms:id/close_button",
                            "inmobi_close_btn", "com.sln.prepositions:id/ia_clickable_close_button"]:
                try:
                    if d(resourceId=fast_id).exists(timeout=0):
                        print(f"{get_timestamp()} [FAST-PATH] Detectado '{fast_id}'.")
                        d(resourceId=fast_id).click()
                        time.sleep(0.5)
                        if '.MainActivity' in str(d.app_current().get('activity', '')):
                            sucesso = True
                            break
                except: pass

            if sucesso: break

            # Dump & Click (Com limpeza de memória via try/finally)
            dump_xml = get_hierarchy_data()
            try:
                clicou = False
                usou_texto = False
                
                if clicar_por_atributo(dump_xml, "resource-id", SELECTORS, lambda x: d(resourceId=x)):
                    clicou = True
                elif not pular_textos and clicar_por_atributo(dump_xml, "text", TEXTS, lambda x: d(text=x)):
                    clicou = True
                    usou_texto = True
                elif find_element_in_hierarchy(dump_xml, "content-desc", ["Interstitial close button"]):
                    d(description="Interstitial close button").click()
                    clicou = True
                elif not pular_textos and tentar_clicar_xpath(XPATHS_PERSONALIZADOS):
                    clicou = True
                    usou_texto = True
                elif not pular_textos and clicar_por_atributo(dump_xml, "content-desc", DESCRIPTIONS, lambda x: d(description=x)):
                    clicou = True
                    usou_texto = True
                elif not pular_textos and tentar_clicar_textview_especial(dump_xml):
                    clicou = True
                    usou_texto = True
                # BUG FIX: original tinha tentar_clicar_open_store_topo sem (dump_xml) — era referência à função, não chamada
                elif not pular_textos and tentar_clicar_open_store_topo(dump_xml):
                    clicou = True
                    usou_texto = True
                elif fechar_anuncio_pelo_x(d, dump_xml):
                    clicou = True
            finally:
                # Limpeza explícita de XML da memória
                dump_xml = None

            # Alterna tentativas baseadas em texto/xpath
            pular_textos = True if usou_texto else False

            # IA a cada 3 ciclos
            if not clicou and (ciclo % 3 == 0):
                if tentar_deteccao_ia():
                    clicou = True

            detectar_e_voltar_se_browser(d)

            # Verificação de retorno
            current_info = verificar_e_corrigir_package()
            if '.MainActivity' in current_info.get('activity', ''):
                print(f"{get_timestamp()} [LOG] Voltou para MainActivity após {int(time.time() - start_time_global)}s.")
                global cliques_sem_anuncio
                cliques_sem_anuncio = 0 
                
                # Clique de limpeza
                try:
                    width, height = d.window_size()
                    d.click(int(width * 0.95), int(height * 0.08))
                except: pass

                remover_apps_nao_autorizados(device, WHITELIST)
                sucesso = True
                break
            
            time.sleep(1)

        if not sucesso:
            print(f"{get_timestamp()} [LOG] Falha no fechamento. Reiniciando...")
            screenshot_path = take_screenshot(device, 'errors')
            if screenshot_path:
                try:
                    xml_path = screenshot_path.replace('.png', '.xml')
                    with open(xml_path, 'w', encoding='utf-8') as f:
                        f.write(d.dump_hierarchy())
                except:
                    pass
            d.app_stop(package)
            time.sleep(1)
            
    return sucesso


# ==================== INÍCIO DO SCRIPT ====================
safe_connect(device)
set_window_title(f"Dispositivo: {device}")
d.app_stop(package)

last_clear_time = time.time()

while True:
    try:
        # 0. Garante que a tela esteja acesa
        is_doze(d)

        rodadas += 1
        d.app_start(package)
        time.sleep(1)

        current_package = d.app_current()['package']
        if current_package == package:
            print(f"{get_timestamp()} Aplicativo iniciado com sucesso. Rodada: {rodadas}")

        print(f"{get_timestamp()} Contador de resets: {qtd_reset}")
        print(f"{get_timestamp()} Contador espera de anuncio: {espera_anuncio}")

        if espera_anuncio == 20:
            d.app_stop(package)
            reset_id()
            time.sleep(1)
            d.app_start(package)
            espera_anuncio = 0

        if qtd_reset >= 20:
            print(f"{get_timestamp()} Resetando ID de publicidade...")
            d.app_stop(package)
            time.sleep(1)
            reset_id()
            qtd_reset = 0

        current_info = d.app_current()
        activity = current_info['activity']
        pkg = current_info['package']
        
        print(f"{get_timestamp()} [DEBUG] App Atual: {pkg} | Activity: {activity}")

        # Destravar erros
        try:
            if d(resourceId="android:id/aerr_wait").exists(timeout=0.1):
                d(resourceId="android:id/aerr_wait").click()
            if d(resourceId="android:id/aerr_close").exists(timeout=0.1):
                d(resourceId="android:id/aerr_close").click()
        except:
            pass

        # Lógica do Jogo (MainActivity)
        if ('.MainActivity' in activity) and (pkg == package):
            if not processar_jogo(d):
                print(f"{get_timestamp()} [LOG] Falha no processar_jogo. Forçando reinício do app...")
                d.app_stop(package)
                time.sleep(1)
                continue 

        # Lógica de Fechamento de Anúncios
        current_info = d.app_current() 
        activity = current_info.get('activity', '')
        pkg = current_info.get('package', '')
        
        print(f"{get_timestamp()} [DEBUG] Pós-Jogo: {pkg} | Activity: {activity}")
        
        if (activity and '.MainActivity' not in activity and 
            '.SplashActivity' not in activity and 
            '.adid.settings.AdsSettingsActivity' not in activity):
            
            print(f"{get_timestamp()} [LOG] Iniciando fechar_anuncio para: {activity}")
            if fechar_anuncio(d, activity):
                qtd_reset += 1
                espera_anuncio = 0
            else:
                espera_anuncio += 1
        else:
            print(f"{get_timestamp()} [LOG] Pulando fechar_anuncio (Activity irrelevante ou Home).")

        # gc.collect() periódico — a cada 10 rodadas
        if rodadas % 10 == 0:
            collected = gc.collect()
            if collected > 0:
                print(f"{get_timestamp()} [GC] Coletados {collected} objetos.")

        # Limpeza de logs de cmd (terminal) a cada 4 horas
        if time.time() - last_clear_time >= 4 * 3600:
            print(f"{get_timestamp()} [CLEANUP] Executando limpeza de logs do terminal (4 em 4 horas)...")
            try:
                d.app_stop(package)
                d.shell(f"am force-stop {package}")
                time.sleep(0.5)
            except: pass

            if os.name == 'nt':
                os.system('cls')
            else:
                os.system('clear')
            try:
                d.shell("logcat -c")
            except:
                pass

            # Força gc.collect na limpeza periódica também
            gc.collect()

            last_clear_time = time.time()
            print(f"{get_timestamp()} [CLEANUP] Limpeza concluída.")

    except Exception as e:
        print(f"{get_timestamp()} Erro durante a execução: {e}")
        print(f"{get_timestamp()} Tentando reconectar ao dispositivo...")
        try:
            safe_connect(device)
        except SystemExit:
            print(f"{get_timestamp()} [FATAL] Não foi possível reconectar. Encerrando.")
            break
        except:
            print(f"{get_timestamp()} Reconexão falhou. Aguardando...")
            time.sleep(30)
