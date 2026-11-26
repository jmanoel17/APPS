import sys
import time
import json
import os
import random
import subprocess
import re
import uiautomator2 as u2
from datetime import datetime
import cv2
import numpy as np
import requests
import math
import xml.etree.ElementTree as ET

# Função auxiliar para timestamp
def get_timestamp():
    """Retorna timestamp formatado para logs"""
    return datetime.now().strftime('[%H:%M:%S]')

# ==================== CONFIGURAÇÕES GLOBAIS ====================
device = sys.argv[1]
modo_ponto = int(sys.argv[2])
tempo_inicio = 5
package = "com.lkss.oraculodigital"
rodadas = 0
d = None
qtd_reset = 0
espera_anuncio = 0
ai_session = requests.Session()

# Seletores de fechamento de anúncios (Consolidado)
SELECTORS = [
    "inmobi_close_btn", "com.inmobi.rendering.inmobi_close_btn", "com.lkss.oraculodigital:id/inmobi_close_btn",
    "com.unity3d.services:id/closeButton", "com.google.android.gms:id/close_button",
    "close_button", "btn_close", "btnClose", "iv_close",
    "com.lkss.oraculodigital:id/ia_clickable_close_button", "applovin_close_button",
    "vungle_close_button", "m-playable-close", "m-playable-skip",
    "com.lkss.oraculodigital:id/mbridge_video_common_alertview_confirm_button",
    "com.emaphoto.pic:id/mbridge_rl_playing_close",
    "com.lkss.oraculodigital:id/ia_iv_close_button", "com.lkss.oraculodigital:id/ia_fl_close_button",
    "com.lkss.oraculodigital:id/mbridge_rl_playing_close", "com.lkss.oraculodigital:id/bigo_ad_btn_close",
    "interstitial_close_button", "ia_iv_close_button", "ia_fl_close_button",
    "mbridge_rl_playing_close", "bigo_ad_btn_close",
    "com.flx.duplicate.files.clean:id/thinkup_myoffer_btn_close_id",
    "com.best.quick.browser:id/i", "com.lkss.oraculodigital:id/ia_tv_skip",
    "com.lkss.oraculodigital:id/mbridge_windwv_close", "com.lkss.oraculodigital:id/iv_ad_close",
    "com.lkss.oraculodigital:id/ad_close", "skipBtnImg", "skip-button"
]

TEXTS = ["", "Confirm choices", "", "Close", "Continue", "Ir para o app", "AGORA NÃO", "FECHAR", "Fechar",
         "Skip", "Ignorar", "Continuar", "x", "X", "Aceitar", "CLOSE", "Cerrar",
         "Concordar com tudo", "NO THANKS", "No Thanks", "no thanks", "No thanks", "skipAllIcon"]

DESCRIPTIONS = ["IACloseButton", "Close", "Fechar", "No Thanks", "no thanks", "NO THANKS", "Skip All", "Interstitial close button"]

WHITELIST = [
    "com.googlecode.eyesfree.setorientation", "com.arlosoft.macrodroid",
    "com.zhiliaoapp.musically", "com.oak.cryptosense", "com.israbyte.dropbin",
    "com.appswork.inspiracoes", "com.workdigital.menteprospera", "com.sln.prepositions",
    "com.frases.frasesvip", "com.workdigital.radiomusic", "com.cmastudio.jokenpoduelo",
    "com.lkss.oraculodigital"
]

XPATHS_PERSONALIZADOS = [
    '//*[@text=""]',
    '//*[@text="Close"]',
    '//*[@text="Skip"]',
    '//*[@content-desc="Skip"]',
    '//*[@resource-id="com.android.chrome:id/menu_button_wrapper"]',
    '//*[@resource-id="com.lkss.oraculodigital:id/ia_tv_skip"]',
    '//*[@resource-id="com.lkss.oraculodigital:id/ia_clickable_close_button"]',
    '//*[@resource-id="com.lkss.oraculodigital:id/mbridge_iv_close"]',
    '//*[@content-desc="Anúncio fechado"]',
    '//*[@resource-id="android:id/content"]/android.widget.RelativeLayout[1]/android.widget.LinearLayout[1]/android.widget.ImageView[1]',
    '//*[@resource-id="te-clr1-4f8bce0e-6193-4784-839d-08f4cd5b0649-icon"]',
    '//*[@resource-id="te-clr1-f4f660de-3a14-4e02-bdf3-70f2c804dc2d-icon"]',
    '//*[@resource-id="android:id/content"]/android.widget.FrameLayout[1]/android.widget.FrameLayout[1]/android.widget.ImageView[1]'
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
    'com.lkss.oraculodigital:id/flash',
    'com.lkss.oraculodigital:id/Energiafm',
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

def safe_connect(device):
    global d
    while True:
        try:
            print(f"{get_timestamp()} Conectando ao dispositivo: {device}")
            d = u2.connect(device)
            if d.info:
                print(f"{get_timestamp()} Conectado com sucesso!")
                return
            else:
                raise Exception("Falha na conexão. Dispositivo não disponível.")
        except Exception as e:
            print(f"{get_timestamp()} Erro ao conectar: {e}")
            time.sleep(5)

def get_hierarchy_data():
    """Obtém e parseia a hierarquia do dispositivo uma única vez"""
    tree_str = d.dump_hierarchy()
    return ET.fromstring(tree_str)

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
    """Tenta clicar em TextViews especiais"""
    if dump_xml is None: return False
    for txt in ["Fechar", "AGORA NÃO"]:
        for node in dump_xml.iter("node"):
            if (node.attrib.get("text", "") == txt and 
                node.attrib.get("class", "") == "android.widget.TextView"):
                print(f"{get_timestamp()} [LOG] TextView '{txt}' encontrado. Clicando...")
                try:
                    d(text=txt, className="android.widget.TextView").click()
                    return True
                except: pass
    return False

def tentar_deteccao_ia():
    """Tenta detectar e clicar usando IA"""
    caminho = take_screenshot(device, 'screenshots')
    try:
        coord_list = detect_objects_and_get_coordinates(
            caminho, model_path='runs/detect/train3/weights/best.pt',
            show_image=False, verbose=True
        )
        if coord_list:
            x, y = coord_list[0]['x'], coord_list[0]['y']
            print(f"[INFO] Tocando em ({x}, {y}) via IA...")
            d.click(x, y)
            return True
        print("[AVISO] Nenhum objeto detectado.")
        return False
    except Exception as e:
        print(f"[ERRO] {e}")
        return False
    finally:
        try:
            os.remove(caminho)
            print(f"[INFO] Imagem removida: {caminho}")
        except Exception as e:
            print(f"[ERRO] Falha ao remover imagem: {e}")

def take_screenshot(device_id, base_dir='screenshots', save_file=True):
    """
    Captura screenshot usando ADB direto.
    Se save_file=True, salva em disco e retorna o caminho.
    Se save_file=False, retorna os bytes da imagem (png) em memória.
    """
    try:
        # Tira screenshot direto para o PC usando adb exec-out
        # Adicionado timeout de 10s para evitar travamento
        raw_bytes = subprocess.check_output(f'adb -s {device_id} exec-out screencap -p', shell=True, timeout=10)

        if save_file:
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
            print(f"{get_timestamp()} [INFO] Screenshot salva em: {gray_image_path}")
            return gray_image_path
        else:
            return raw_bytes

    except subprocess.TimeoutExpired:
        print(f"{get_timestamp()} [ERRO] Timeout ao tirar screenshot via ADB.")
        return None
    except Exception as e:
        print(f"{get_timestamp()} [ERRO] Falha ao tirar screenshot via ADB: {e}")
        return None

def remover_apps_nao_autorizados(device_serial, whitelist, force=False):
    """Remove apps não autorizados - otimizado para não rodar sempre"""
    global rodadas
    # Só roda a cada 30 rodadas ou se forçado
    if not force and rodadas % 30 != 0:
        return

    try:
        # Usando d.shell que é mais integrado que subprocess manual
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
            print(f"{get_timestamp()} [ALERTA] Preso em loop de voltar ({tentativas_back}x). Forçando parada de '{d.app_current().get('package')}'...")
            try:
                d.app_stop(d.app_current().get('package'))
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
            xpath_ad = '//*[@resource-id="com.lkss.oraculodigital:id/ia_ad_content"]/android.widget.RelativeLayout[1]/android.widget.RelativeLayout[2]/android.webkit.WebView[1]'
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
    Aqui você pode colocar lógicas de limpeza, reset, ou verificação de estado.
    """
    print("[INFO] Preparando ambiente para anúncios...")
    
    # 1. Garante que estamos no app certo
    verificar_e_corrigir_package()
    
    # 2. Fecha diálogos de erro se houver
    check_and_close_errors(d)
    
    # 3. (Opcional) Resetar ID de publicidade se necessário
    # reset_id() 
    
    print("[INFO] Ambiente preparado.")


def take_screenshot(device_id, base_dir='screenshots', save_file=True):
    """
    Captura screenshot usando ADB direto.
    Se save_file=True, salva em disco e retorna o caminho.
    Se save_file=False, retorna os bytes da imagem (png) em memória.
    """
    try:
        # Tira screenshot direto para o PC usando adb exec-out
        raw_bytes = subprocess.check_output(f'adb -s {device_id} exec-out screencap -p', shell=True)

        if save_file:
            try:
                activity_name = d.app_current()['activity']
            except:
                activity_name = "unknown"
            
            folder_path = os.path.join(base_dir, activity_name)
            os.makedirs(folder_path, exist_ok=True)

            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            gray_image_path = os.path.join(folder_path, f"{timestamp}.png")
            
            image_array = np.frombuffer(raw_bytes, np.uint8)
            image = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
            
            if image is None:
                print("[ERRO] Falha ao decodificar imagem do ADB.")
                return None
                
            gray_image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            cv2.imwrite(gray_image_path, gray_image)
            print(f"[INFO] Screenshot salva em: {gray_image_path}")
            return gray_image_path
        else:
            return raw_bytes

    except Exception as e:
        print(f"[ERRO] Falha ao tirar screenshot via ADB: {e}")
        return None

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
    """Verifica e fecha diálogos de erro do sistema"""
    if d(resourceId="android:id/aerr_wait").exists:
        print("[LOG] Fechando diálogo 'aerr_wait'...")
        d(resourceId="android:id/aerr_wait").click()
        time.sleep(0.3)
    if d(resourceId="android:id/aerr_close").exists:
        print("[LOG] Fechando diálogo 'aerr_close'...")
        d(resourceId="android:id/aerr_close").click()
        time.sleep(0.3)

def tentar_deteccao_ia():
    """Tenta detectar e clicar usando IA via API Server (In-Memory)"""
    # Pega a imagem direto em bytes, sem salvar no disco
    img_bytes = take_screenshot(device, 'screenshots', save_file=False)

    if not img_bytes:
        print(f"{get_timestamp()} [ERRO] Não foi possível capturar a imagem para análise.")
        return False

    try:
        # Enviar para o servidor API
        url = "http://localhost:8000/detect"

        # Envia os bytes diretamente
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

                # Separa em prioritários ( topo da tela) e outros
                detections = data["detections"]
                top_priority = [det for det in detections if det['y'] <= 250]
                others = [det for det in detections if det['y'] > 250]

                # Ordena ambos por confiança decrescente
                top_priority.sort(key=lambda k: k['confidence'], reverse=True)
                others.sort(key=lambda k: k['confidence'], reverse=True)

                # Lista final ordenada
                final_detections = top_priority + others

                # Itera sobre todos os objetos detectados (já ordenados)
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

                    # Espera para verificar se funcionou
                    time.sleep(1.0)

                    # 1. Smart Retry: Verifica se abriu navegador ou saiu do app
                    # Verifica browser
                    browser_detected = detectar_e_voltar_se_browser(d)

                    # Verifica se saiu do pacote (Play Store, etc)
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

                    # 2. Verifica se mudou de tela (Sucesso ou outra navegação)
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
                return True  # Retorna True pois tentamos tudo
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


def navegar_aleatoriamente(d):
    """Navegação direta sem simulação humana"""
    return # Desativado a pedido do usuário
    


 
    
    


def fechar_anuncio(d, activity_name, max_tentativas=5):
    """Função principal para fechar anúncios com timeout global de 100s"""
    start_time_global = time.time()
    MAX_TIMEOUT_ADS = 100  # Tempo máximo em segundos para tentar fechar o anúncio
    sucesso = False

    # Destravar se necessário (erros do Android)
    try:
        if d(resourceId="android:id/aerr_wait").exists(timeout=1):
            print(f"{get_timestamp()} [LOG] Fechando diálogo 'aerr_wait'...")
            d(resourceId="android:id/aerr_wait").click()
        if d(resourceId="android:id/aerr_close").exists(timeout=1):
            print(f"{get_timestamp()} [LOG] Fechando diálogo 'aerr_close'...")
            d(resourceId="android:id/aerr_close").click()
    except:
        pass

    # Verifica se não está em MainActivity ou SplashActivity
    if ('.MainActivity' not in activity_name and
            '.SplashActivity' not in activity_name and
            '.adid.settings.AdsSettingsActivity' not in activity_name):

        # Verifica se botão de iniciar batalha (ou similar) está presente ANTES de esperar 5s
        # No Oraculo, não temos btnIniciarBatalha na home, mas temos home_about_button
        if not d(resourceId="home_about_button").exists(timeout=2):
            print(f"{get_timestamp()} [LOG] Possível anúncio detectado. Aguardando {tempo_inicio}s...")
            time.sleep(tempo_inicio)

        for ciclo in range(30):
            tempo_decorrido = time.time() - start_time_global
            if tempo_decorrido > MAX_TIMEOUT_ADS:
                print(f"{get_timestamp()} [ALERTA] Timeout global de {MAX_TIMEOUT_ADS}s atingido em fechar_anuncio.")
                break

            print(f"{get_timestamp()} [LOG] Ciclo {ciclo + 1}/30... (T: {int(tempo_decorrido)}s)")

            # FAST PATH: Antes de dar dump na hierarquia (caro), tenta IDs mais comuns rápido
            for fast_id in ["com.lkss.oraculodigital:id/ad_close", "com.google.android.gms:id/close_button",
                            "inmobi_close_btn", "com.lkss.oraculodigital:id/ia_clickable_close_button"]:
                try:
                    if d(resourceId=fast_id).exists(timeout=0):
                        print(f"{get_timestamp()} [FAST-PATH] Detectado '{fast_id}'. Clicando...")
                        d(resourceId=fast_id).click()
                        time.sleep(1)
                        if '.MainActivity' in str(d.app_current().get('activity', '')):
                            sucesso = True
                            break
                except:
                    pass

            if sucesso: break

            # Verificação rápida de erros do app sem dump completo
            try:
                if d(resourceId="android:id/aerr_wait").exists(timeout=0.1):
                    d(resourceId="android:id/aerr_wait").click()
                if d(resourceId="android:id/aerr_close").exists(timeout=0.1):
                    d(resourceId="android:id/aerr_close").click()
            except:
                pass

            # Obtém hierarquia (Lento/Caro em RAM/CPU)
            dump_xml = get_hierarchy_data()
            if dump_xml is None:
                continue

            # Estratégia de cliques em cascata
            clicou = False

            # 1. Tenta clicar por ID
            if clicar_por_atributo(dump_xml, "resource-id", SELECTORS, lambda x: d(resourceId=x)):
                clicou = True

            # 2. Tenta clicar por texto
            if not clicou and clicar_por_atributo(dump_xml, "text", TEXTS, lambda x: d(text=x)):
                clicou = True

            # 3. Tenta botão vazio
            if not clicou and tentar_clicar_botao_vazio(dump_xml):
                clicou = True

            # 4. Tenta descrição específica
            if not clicou and find_element_in_hierarchy(dump_xml, "content-desc", ["Interstitial close button"]):
                print(f"{get_timestamp()} [LOG] Botão 'Interstitial close button' encontrado. Clicando...")
                d(description="Interstitial close button").click()
                clicou = True

            # 5. Tenta XPaths genéricos customizados
            if not clicou and tentar_clicar_xpath(XPATHS_PERSONALIZADOS):
                clicou = True

            # 6. Tenta outras descrições
            if not clicou and clicar_por_atributo(dump_xml, "content-desc", DESCRIPTIONS,
                                                  lambda x: d(description=x)):
                clicou = True

            # 7. Tenta TextViews especiais
            if not clicou and tentar_clicar_textview_especial(dump_xml):
                clicou = True

            # Limpa XML da memória o quanto antes
            dump_xml = None

            # 8. IA apenas se nada funcionou e a cada 3 ciclos agora
            if not clicou and (ciclo % 3 == 0):
                if tentar_deteccao_ia():
                    clicou = True

            # Verifica e volta de browser se necessário
            detectar_e_voltar_se_browser(d)

            # Verifica se voltou para tela principal
            current_info = verificar_e_corrigir_package()
            current_activity = current_info.get('activity', '')

            if current_info['package'] == package and '.MainActivity' in current_activity:
                print(f"{get_timestamp()} [LOG] Voltou para MainActivity após {int(time.time() - start_time_global)}s. Sucesso.")
                
                # Ações pós-anúncio para tirar blocker do Oraculo
                width, height = d.window_size()
                x = int(width * 0.95)
                y = int(height * 0.08)
                d.click(x, y)
                
                # Tenta tirar o blocker do compose
                xpath_blocker_final = (
                    '//androidx.compose.ui.platform.ComposeView'
                    '/android.view.View[1]/android.view.View[1]'
                    '/android.view.View[1]/android.view.View[1]'
                    '/android.view.View[1]/android.view.View[1]'
                )
                el_final = d.xpath(xpath_blocker_final)
                if el_final.exists:
                     el_final.click()

                remover_apps_nao_autorizados(device, WHITELIST)
                sucesso = True
                break

        if not sucesso:
            print(f"{get_timestamp()} [LOG] Falha ao fechar anúncio. Reiniciando app...")
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
def processar_jogo(d):
    print(f"{get_timestamp()} [LOG] Iniciando loop principal do Oraculo...")
    
    # Configurações
    xpath_blocker = '//androidx.compose.ui.platform.ComposeView/android.view.View[1]/android.view.View[1]/android.view.View[1]/android.view.View[1]/android.view.View[1]/android.view.View[1]'
    res_id_sobre = "home_about_button"
    
    max_tentativas_sobre = 5
    tentativas_sobre = 0
    tentativas_blocker = 0

    while True:
        # 0. Verifica se ainda está no pacote (prevenção de loop no launcher)
        try:
            curr = d.app_current()
            if curr['package'] != package:
                print(f"{get_timestamp()} [ALERTA] Fora do app ({curr['package']}). Tentando voltar...")
                d.app_start(package)
                time.sleep(2)
                continue
        except: pass

        # 1. Garante que estamos na MainActivity e na tela Home do app
        try:
            curr = d.app_current()
            is_main = curr['package'] == package and '.MainActivity' in curr['activity']
            el_sobre_check = d(resourceId=res_id_sobre) or d(resourceId=f"{package}:id/{res_id_sobre}") or d(description="Sobre")
            
            if not is_main:
                if curr['package'] == package:
                    print(f"{get_timestamp()} [LOG] No app, mas fora da MainActivity ({curr['activity']}). Saindo para fechar anúncio ou voltando...")
                    return True # Assume que saiu para anúncio
                else:
                    print(f"{get_timestamp()} [ALERTA] Fora do app ({curr['package']}). Tentando voltar...")
                    d.app_start(package)
                    time.sleep(2)
                    continue

            if not el_sobre_check.exists:
                print(f"{get_timestamp()} [LOG] Na MainActivity, mas botão Sobre não visível. Tentando voltar internamente...")
                d.press("back")
                time.sleep(2)
                tentativas_sobre += 1
                if tentativas_sobre >= max_tentativas_sobre:
                    return False
                continue
        except Exception as e:
            print(f"{get_timestamp()} [ERRO] Falha ao verificar estado: {e}")
            continue

        # 2. Estamos na Home do App (MainActivity)! Agora sim aguardamos os 10 segundos
        print(f"{get_timestamp()} [LOG] MainActivity Home detectada. Aguardando 10s...")
        time.sleep(10)
        
        # Reset de tentativas após sucesso em chegar na Home
        tentativas_sobre = 0

        # 3. Verifica Blocker (Voltar)
        el_blocker = d.xpath(xpath_blocker)
        if el_blocker.exists:
            tentativas_blocker += 1
            if tentativas_blocker >= 6:
                print(f"{get_timestamp()} [ALERTA] Stuck no blocker. Reiniciando app...")
                return False
                
            print(f"{get_timestamp()} [LOG] Blocker detectado ({tentativas_blocker}/6). Clicando em Voltar...")
            try:
                el_blocker.click()
                time.sleep(2)
                continue
            except: pass

        # 4. Verifica Botão Sobre para clicar
        print(f"{get_timestamp()} [LOG] Buscando botão Sobre para clicar...")
        el_sobre = d(resourceId=res_id_sobre) or d(resourceId=f"{package}:id/{res_id_sobre}") or d(description="Sobre")
        
        if el_sobre.exists:
            print(f"{get_timestamp()} [LOG] Clicando no Sobre...")
            el_sobre.click()
            tentativas_blocker = 0
            
            # Aguarda checar se abriu anúncio
            time.sleep(4)
            
            # Se saiu da MainActivity, sucesso
            try:
                curr = d.app_current()
                if curr['package'] != package or '.MainActivity' not in curr['activity']:
                     print(f"{get_timestamp()} [SUCESSO] Saiu da MainActivity.")
                     return True
            except: pass
            
            # Se continua na MainActivity, verifica se o blocker apareceu
            if d.xpath(xpath_blocker).exists:
                print(f"{get_timestamp()} [LOG] Blocker apareceu (Ads falhou?).")
                continue
        else:
             print(f"{get_timestamp()} [LOG] Botão Sobre não encontrado após delay.")
        
        time.sleep(1)
safe_connect(device)
set_window_title(f"Dispositivo: {device}")
d.app_stop(package)

while True:
    try:
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
        
        # Destravar erros
        try:
            if d(resourceId="android:id/aerr_wait").exists(timeout=0.1):
                d(resourceId="android:id/aerr_wait").click()
            if d(resourceId="android:id/aerr_close").exists(timeout=0.1):
                d(resourceId="android:id/aerr_close").click()
        except:
            pass

        # Lógica do Jogo (MainActivity)
        if ('.MainActivity' in activity) and (current_info['package'] == package):
            if not processar_jogo(d):
                print(f"{get_timestamp()} [LOG] Falha no processar_jogo. Forçando reinício do app...")
                d.app_stop(package)
                time.sleep(1)
                continue # Volta pro início do loop, irá dar d.app_start

        # Lógica de Fechamento de Anúncios (Activities diversas)
        current_info = d.app_current() # Atualiza info
        activity = current_info['activity']
        
        if ('.MainActivity' not in activity and 
            '.SplashActivity' not in activity and 
            '.adid.settings.AdsSettingsActivity' not in activity):
            
            if fechar_anuncio(d, activity):
                qtd_reset += 1
                espera_anuncio = 0
            else:
                espera_anuncio += 1

    except Exception as e:
        print(f"{get_timestamp()} Erro durante a execução: {e}")
        print(f"{get_timestamp()} Tentando reconectar ao dispositivo...")
        try:
            safe_connect(device)
        except:
            print(f"{get_timestamp()} Reconexão falhou. Aguardando...")
            time.sleep(30)
