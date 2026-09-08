import sys
import time
import json
import os
import random
import subprocess
import re
import uiautomator2 as u2
from datetime import datetime
import requests
import math
import xml.etree.ElementTree as ET
import io
# NOTE: Removed 'cv2', 'numpy', 'check' to save memory on 95 instances.

# ==================== CONFIGURAÇÕES GLOBAIS ====================
device = sys.argv[1]
modo_ponto = int(sys.argv[2])
tempo_inicio = 10
package = "com.workdigital.radiomusic"
rodadas = 0
d = None
qtd_reset = 0
espera_anuncio = 0
total_ads_shown = 0

# Seletores de fechamento de anúncios (Consolidado)
SELECTORS = [
    # InMobi
    "inmobi_close_btn", "com.inmobi.rendering.inmobi_close_btn", "com.workdigital.radiomusic:id/inmobi_close_btn",
    # Unity Ads
    "com.unity3d.services:id/closeButton",
    # AdMob
    "com.google.android.gms:id/close_button",
    # Chartboost
    "close_button", "btn_close",
    # IronSource
    "btnClose",
    # Fyber
    "iv_close", "com.workdigital.radiomusic:id/ia_clickable_close_button",
    # AppLovin
    "applovin_close_button",
    # Vungle
    "vungle_close_button",
    # Mintegral
    "m-playable-close", "m-playable-skip",
    "com.workdigital.radiomusic:id/mbridge_video_common_alertview_confirm_button",
    "com.emaphoto.pic:id/mbridge_rl_playing_close",
    # SDKs e internos
    "com.workdigital.radiomusic:id/ia_iv_close_button", "com.workdigital.radiomusic:id/ia_fl_close_button",
    "com.workdigital.radiomusic:id/mbridge_rl_playing_close", "com.workdigital.radiomusic:id/bigo_ad_btn_close",
    # Genéricos
    "interstitial_close_button", "ia_iv_close_button", "ia_fl_close_button",
    "mbridge_rl_playing_close", "bigo_ad_btn_close",
    "com.flx.duplicate.files.clean:id/thinkup_myoffer_btn_close_id",
    "com.best.quick.browser:id/i",
    "com.workdigital.radiomusic:id/ia_tv_skip",
    "com.workdigital.radiomusic:id/mbridge_windwv_close"
]

TEXTS = [
    "Confirm choices", "", "Close", "Continue", "Ir para o app", "AGORA NÃO", "FECHAR", "Fechar",
    "Skip", "Ignorar", "Continuar", "x", "X", "Aceitar", "CLOSE", "Cerrar", "Concordar com tudo", "NO THANKS", "Skip All", "Skip", "Ver Próximo"
]

DESCRIPTIONS = [
    "IACloseButton", "Close", "Fechar", "Interstitial close button"
]

WHITELIST = [
    "com.googlecode.eyesfree.setorientation", "com.arlosoft.macrodroid",
    "com.zhiliaoapp.musically", "com.oak.cryptosense", "com.cmastudio.jokenpoduelo",
    "com.appswork.inspiracoes", "com.workdigital.menteprospera", "com.sln.prepositions",
    "com.frases.frasesvip", "com.workdigital.radiomusic", "com.israbyte.dropbin",
    "com.lkss.oraculodigital"
]

XPATHS_PERSONALIZADOS = [
    '//*[@text="Close"]',
    '//*[@text="Skip"]',
    '//*[@resource-id="com.android.chrome:id/menu_button_wrapper"]'
    '//*[@resource-id="com.israbyte.dropbin:id/ia_tv_skip"]',
    '//*[@resource-id="com.israbyte.dropbin:id/ia_clickable_close_button"]',
    '//*[@resource-id="android:id/content"]/android.widget.RelativeLayout[1]/android.widget.LinearLayout[1]/android.widget.ImageView[1]',
#   '//*[@resource-id="android:id/content"]/android.widget.FrameLayout[1]/android.widget.FrameLayout[1]/android.widget.ImageView[1]'
]

BOTOES_RANDOMICOS = [
    # Botões de categorias (resourceId)
    'com.workdigital.radiomusic:id/flash',
    'com.workdigital.radiomusic:id/Energiafm',
    'com.workdigital.radiomusic:id/graufm',
    'com.workdigital.radiomusic:id/jangadeiro',
    'com.workdigital.radiomusic:id/reggae',
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
            print(f"Conectando ao dispositivo: {device}")
            d = u2.connect(device)
            if d.info:
                print("Conectado com sucesso!")
                return
            else:
                raise Exception("Falha na conexão. Dispositivo não disponível.")
        except Exception as e:
            print(f"Erro ao conectar: {e}")
            time.sleep(5)

def get_hierarchy_data():
    """Obtém e parseia a hierarquia do dispositivo uma única vez"""
    try:
        tree_str = d.dump_hierarchy()
        return ET.fromstring(tree_str)
    except Exception as e:
        print(f"[ERRO] Falha ao obter hierarquia: {e}")
        return None

def find_element_in_hierarchy(dump_xml, attr_name, attr_values):
    """Busca genérica de elementos na hierarquia"""
    if dump_xml is None: return None
    for node in dump_xml.iter("node"):
        value = node.attrib.get(attr_name, "")
        if value in attr_values:
            return value
    return None

def clicar_por_atributo(dump_xml, attr_name, attr_values, click_func):
    """Função genérica para clicar em elementos por atributo"""
    found = find_element_in_hierarchy(dump_xml, attr_name, attr_values)
    if found:
        print(f"[LOG] Elemento '{found}' detectado ({attr_name}). Clicando...")
        try:
            click_func(found)
            time.sleep(0.3)
            return True
        except Exception as e:
            print(f"[ERRO] Falha ao clicar em {found}: {e}")
    return False

def tentar_clicar_xpath(xpaths):
    """Tenta clicar em elementos via XPath"""
    for xpath in xpaths:
        try:
            if d.xpath(xpath).exists:
                print(f"[LOG] Elemento encontrado via XPath: {xpath}. Clicando...")
                d.xpath(xpath).click()
                time.sleep(0.3)
                return True
        except Exception as e:
            print(f"[ERRO] Falha ao clicar em XPath: {e}")
    return False

def tentar_clicar_botao_vazio(dump_xml):
    """Tenta clicar em botões com texto vazio"""
    if find_element_in_hierarchy(dump_xml, "text", ["Ir para o app"]):
        print("[LOG] Texto 'Ir para o app' detectado. Ignorando clique via XPath.")
        return False
    
    try:
        btns = [b for b in d.xpath('//android.widget.Button[@text=""]').all() 
                if not b.info.get("focusable", True)]
        if btns:
            print("[LOG] Botão com texto vazio encontrado. Clicando...")
            btns[0].click()
            return True
    except Exception as e:
        print(f"[ERRO] Falha ao clicar via XPath: {e}")
    return False

def tentar_clicar_textview_especial(dump_xml):
    """Tenta clicar em TextViews especiais (Fechar, AGORA NÃO)"""
    if dump_xml is None: return False
    for txt in ["Fechar", "AGORA NÃO"]:
        for node in dump_xml.iter("node"):
            if (node.attrib.get("text", "") == txt and 
                node.attrib.get("class", "") == "android.widget.TextView"):
                print(f"[LOG] TextView '{txt}' encontrado. Clicando...")
                try:
                    d(text=txt, className="android.widget.TextView").click()
                    time.sleep(0.3)
                    return True
                except: pass
    return False

def take_screenshot(device_id, base_dir='screenshots', save_file=True):
    """
    Captura screenshot usando uiautomator2 para economizar conexões ADB.
    Se save_file=True, salva em disco (grayscale) e retorna o caminho.
    Se save_file=False, retorna os bytes da imagem (png) em memória.
    """
    try:
        if save_file:
            try:
                activity_name = d.app_current().get('activity', 'unknown')
            except:
                activity_name = "unknown"
            
            folder_path = os.path.join(base_dir, activity_name)
            os.makedirs(folder_path, exist_ok=True)

            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            gray_image_path = os.path.join(folder_path, f"{timestamp}.png")
            
            # d.screenshot() retorna PIL Image
            pil_img = d.screenshot()
            
            # Converte para Grayscale usando PIL
            pil_img = pil_img.convert('L')
            pil_img.save(gray_image_path)
            
            print(f"[INFO] Screenshot salva em: {gray_image_path}")
            return gray_image_path
        else:
            # Retorna bytes do PNG
            b = io.BytesIO()
            d.screenshot().save(b, format='PNG')
            return b.getvalue()

    except Exception as e:
        print(f"[ERRO] Falha ao tirar screenshot via uiautomator2: {e}")
        return None

def remover_apps_nao_autorizados(device_serial, whitelist):
    """Remove apps não autorizados do dispositivo"""
    try:
        # Usando uiautomator2 para listar pacotes
        output = d.shell("pm list packages -3").output
        pacotes_terceiros = {line.replace("package:", "").strip() for line in output.strip().splitlines() if line.strip()}
        a_remover = [pkg for pkg in pacotes_terceiros if pkg not in whitelist]

        if not a_remover:
            print("[OK] Nenhum app não autorizado detectado.")
            return

        print(f"[ALERTA] {len(a_remover)} apps não autorizados serão removidos.")
        for pacote in a_remover:
            print(f" - Desinstalando: {pacote}")
            d.app_uninstall(pacote)
        print("[INFO] Remoção concluída.")
    except Exception as e:
        print(f"[ERRO] Falha ao remover apps não autorizados: {e}")

def detectar_e_voltar_se_browser(d):
    """Detecta e sai de browsers/AdActivities indesejadas"""
    pressionou_back = False
    tentativas_back = 0

    while True:
        if tentativas_back >= 5:
            try:
                pkg = d.app_current().get('package')
                print(f"[ALERTA] Preso em loop de voltar ({tentativas_back}x). Forçando parada de '{pkg}'...")
                d.app_stop(pkg)
            except:
                pass
            time.sleep(1)
            break

        current_info = d.app_current()
        current_activity = current_info.get('activity')
        current_package = current_info.get('package')

        if current_package in IGNORAR_PACKAGES:
            print(f"[LOG] Ignorando pacote '{current_package}'.")
            break

        # Verifica keywords proibidas
        ainda_proibido = any(kw.lower() in str(current_activity).lower() or
                             kw.lower() in str(current_package).lower()
                             for kw in FORCAR_VOLTAR_KEYWORDS)

        # Verifica WebView de anúncio
        try:
            xpath_ad = '//*[@resource-id="com.workdigital.radiomusic:id/ia_ad_content"]/android.widget.RelativeLayout[1]/android.widget.RelativeLayout[2]/android.webkit.WebView[1]'
            if d.xpath(xpath_ad).exists:
                print("[LOG] WebView de anúncio detectado. Pressionando BACK...")
                d.press("back")
                time.sleep(0.5)
                pressionou_back = True
                tentativas_back += 1
                continue
        except Exception as e:
            print(f"[ERRO] Verificação de WebView falhou: {e}")

        if not ainda_proibido:
            break

        print(f"[LOG] Activity proibida '{current_activity}'. Pressionando BACK...")
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
        print(f"[ALERTA] Saiu do pacote '{package}' para '{current_package}'. Corrigindo...")
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


def reset_id():
    """Reseta o ID de publicidade"""
    print("=" * 52)
    print("Abrindo configurações de ID de publicidade...")
    d.shell("am start -a com.google.android.gms.settings.ADS_PRIVACY")
    time.sleep(2)
    
    try:
        reset_options = [
            "Redefinir ID de publicidade", "Redefinir código de publicidade",
            "Repor ID de publicidade", "Reset advertising ID", "Reset Advertising ID"
        ]
        
        for option in reset_options:
            if d(text=option).exists:
                print(f"Elemento '{option}' encontrado. Clicando...")
                d(text=option).click()
                break
        else:
            print("Elemento de reset não encontrado.")

        time.sleep(2)
        if d(resourceId="android:id/button1").exists:
            print("Botão 'OK' encontrado. Clicando...")
            d(resourceId="android:id/button1").click()
        else:
            print("Botão 'OK' não encontrado.")
    except Exception as e:
        print(f"Erro ao resetar ID: {e}")

def clicar_botao_randomico(d):
    """
    Clica em 2 ou 3 botões aleatórios diferentes (resourceId ou XPATH) 
    em sequência para garantir que o app 'acorde' e mostre o anúncio.
    """
    botoes = BOTOES_RANDOMICOS.copy()
    random.shuffle(botoes)

    clicks_realizados = 0
    max_clicks = random.randint(2, 3) # Varia entre 2 e 3 cliques

    print(f"[LOG] Tentando clicar em {max_clicks} botões diferentes...")

    for botao in botoes:
        clique_sucesso = False
        
        # --- Se começar com '/', é XPATH ---
        if botao.startswith('/'):
            if d.xpath(botao).exists:
                print(f"[LOG] ({clicks_realizados+1}/{max_clicks}) Botão aleatório (XPATH) encontrado: {botao}. Clicando...")
                try:
                    d.xpath(botao).click()
                    clique_sucesso = True
                except Exception as e:
                    print(f"[ERRO] Falha ao clicar no XPATH {botao}: {e}")

        # --- Caso contrário, é resourceId ---
        else:
            if d(resourceId=botao).exists:
                print(f"[LOG] ({clicks_realizados+1}/{max_clicks}) Botão aleatório (ID) encontrado: {botao}. Clicando...")
                try:
                    d(resourceId=botao).click()
                    clique_sucesso = True
                except Exception as e:
                    print(f"[ERRO] Falha ao clicar no ID {botao}: {e}")

        if clique_sucesso:
            clicks_realizados += 1
            if clicks_realizados >= max_clicks:
                break
            time.sleep(1.5) # Pausa entre cliques para o app processar

    if clicks_realizados > 0:
        return True
    
    print("[LOG] Nenhum botão aleatório disponível para clique.")
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
    # Usando a função take_screenshot otimizada com PIL
    img_bytes = take_screenshot(device, 'screenshots', save_file=False)

    if not img_bytes:
        print("[ERRO] Não foi possível capturar a imagem para análise.")
        return False

    try:
        # Enviar para o servidor API
        url = "http://localhost:8000/detect"

        # Envia os bytes diretamente
        files = {'file': ('screenshot.png', img_bytes, 'image/png')}

        # Timeout de 5s para não travar o script se o servidor estiver off
        response = requests.post(url, files=files, timeout=5)

        if response.status_code == 200:
            data = response.json()
            count = data.get("count", 0)
            if data.get("success") and count > 0:
                print(f"[INFO] IA encontrou {count} objetos. Iniciando sequência de cliques inteligente...")

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
                top_priority = [d for d in detections if d['y'] <= 250]
                others = [d for d in detections if d['y'] > 250]

                # Ordena ambos por confiança decrescente
                top_priority.sort(key=lambda k: k['confidence'], reverse=True)
                others.sort(key=lambda k: k['confidence'], reverse=True)

                # Lista final ordenada
                final_detections = top_priority + others

                # Itera sobre todos os objetos detectados (já ordenados)
                for i, obj in enumerate(final_detections):
                    x, y = obj['x'], obj['y']
                    conf = obj['confidence']

                    # Filtro de proximidade (100 pixels)
                    too_close = False
                    for cx, cy in clicked_points:
                        if math.hypot(x - cx, y - cy) < 100:
                            too_close = True
                            break

                    if too_close:
                        print(f"[INFO] Ponto ({x}, {y}) ignorado por estar muito próximo de um anterior.")
                        continue

                    print(f"[INFO] [{i+1}/{count}] Clicando em ({x}, {y}) - Confiança: {conf:.2f}")
                    d.click(x, y)
                    clicked_points.append((x, y))

                    if len(clicked_points) >= 3:
                        print("[INFO] Limite de 3 cliques atingido. Parando por aqui.")
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
                            print(f"[INFO] Saiu do pacote para '{curr_pkg}'. Corrigindo...")
                            verificar_e_corrigir_package()
                            package_changed = True
                    except:
                        pass

                    if browser_detected or package_changed:
                        print(f"[INFO] Clique inválido (Browser/Outro App). Tentando próximo ponto...")
                        continue

                    # 2. Verifica se mudou de tela (Sucesso ou outra navegação)
                    try:
                        current_info = d.app_current()
                        current_activity = current_info.get('activity')
                        current_package = current_info.get('package')

                        if current_package != initial_package or current_activity != initial_activity:
                            if '.MainActivity' in str(current_activity):
                                print(f"[SUCESSO] Anúncio fechado! Voltou para MainActivity. Parando cliques.")
                            else:
                                print(f"[INFO] Tela mudou para '{current_activity}'. Parando cliques para verificação.")
                            return True
                    except:
                        pass

                print("[AVISO] Todos os pontos foram clicados, mas a tela parece não ter mudado.")
                return True  # Retorna True pois tentamos tudo
            else:
                print("[AVISO] Nenhum objeto detectado pela API.")
        else:
            print(f"[ERRO] Erro na API: {response.status_code} - {response.text}")

        return False

    except requests.exceptions.ConnectionError:
        print("[ERRO] Falha ao conectar no servidor de IA (aiMain.py). Ele está rodando?")
        return False
    except Exception as e:
        print(f"[ERRO] Falha na requisição de IA: {e}")
        return False

# ==================== LÓGICA PRINCIPAL ====================

def processar_jogo(d):
    print("[LOG] Estamos no MainActivity principal do app.")

    dump_xml = get_hierarchy_data()
    
    # Cliques iniciais de confirmação
    clicar_por_atributo(dump_xml, "text", ["Continue", "Confirm choices"], lambda x: d(text=x).click())

    # Clica no botão randomico
    print("[LOG] Clicando no botão randomico...")
    if not clicar_botao_randomico(d):
        print("[AVISO] Nenhum botão randomico encontrado. Aguardando...")
        time.sleep(2)
    
    # Espera 2 minutos e 30 segundos (150 segundos) para o anúncio aparecer
    print("[LOG] Aguardando 2:30 para o anúncio aparecer...")
    tempo_espera = 0
    tempo_minimo = 150  # 2:30
    tempo_maximo = 153  # 2:33
    
    while tempo_espera < tempo_maximo:
        # Verifica se saiu da MainActivity (anúncio apareceu)
        current_activity = d.app_current()['activity']
        if '.MainActivity' not in current_activity:
            print(f"[LOG] Anúncio detectado após {tempo_espera}s! Saiu da MainActivity para: {current_activity}")
            return True  # Anúncio apareceu, vai para fechar_anuncio
        
        time.sleep(1)
        tempo_espera += 1
        
        # Log a cada 30 segundos
        if tempo_espera % 30 == 0:
            print(f"[LOG] Aguardando anúncio... {tempo_espera}s / {tempo_maximo}s")
    
    # Se chegou aqui, passou de 2:33 sem anúncio
    print("[LOG] Timeout! Passou de 2:33 sem anúncio aparecer. Reiniciando ID e app...")
    d.app_stop(package)
    reset_id()
    time.sleep(1)
    d.app_start(package)
    return False  # Retorna False para reiniciar o loop principal


def fechar_anuncio(d, activity_name):
    """Lógica para fechar anúncios em outras Activities"""
    global qtd_reset
    global espera_anuncio
    global total_ads_shown

    dump_xml = get_hierarchy_data()
    ids_presentes = [node.attrib.get("resource-id", "") for node in dump_xml.iter("node")]
    
    if "com.workdigital.radiomusic:id/btnIniciarBatalha" not in ids_presentes:
        print(f"[LOG] Aguardando {tempo_inicio}s antes de iniciar cliques...")
        time.sleep(tempo_inicio)

    sucesso = False
    ciclos_maximos = 15
    
    for ciclo in range(ciclos_maximos):
        print(f"[LOG] Ciclo {ciclo + 1}/{ciclos_maximos}...")

        
        check_and_close_errors(d)
        
        dump_xml = get_hierarchy_data()
        
        # Estratégia de cliques otimizada
        if clicar_por_atributo(dump_xml, "resource-id", SELECTORS, lambda x: d(resourceId=x).click()): pass
        elif clicar_por_atributo(dump_xml, "text", TEXTS, lambda x: d(text=x).click()): pass
        elif tentar_clicar_botao_vazio(dump_xml): pass
        elif clicar_por_atributo(dump_xml, "content-desc", ["Interstitial close button"], lambda x: d(description=x).click()): pass
        elif tentar_clicar_xpath(XPATHS_PERSONALIZADOS): pass
        elif clicar_por_atributo(dump_xml, "content-desc", DESCRIPTIONS, lambda x: d(description=x).click()): pass
        elif tentar_clicar_textview_especial(dump_xml): pass
        else:
            tentar_deteccao_ia()

        if detectar_e_voltar_se_browser(d):
                print("[LOG] Pressionado back para sair de browser.")

        # Verifica retorno ao app
        current_info = d.app_current()
        if current_info['package'] != package:
            print(f"[ALERTA] Saiu do pacote. Corrigindo...")
            d.app_stop(current_info['package'])
            d.app_start(package)
            time.sleep(1)
            current_info = d.app_current()

        if current_info['package'] == package and '.MainActivity' in current_info['activity']:
            print("[LOG] Voltou para MainActivity. OK.")
            remover_apps_nao_autorizados(device, WHITELIST)
            
            # Ações pós-anúncio (cliques de coordenadas)
            width, height = d.window_size()
            x = int(width * 0.95)
            y = int(height * 0.08)
            d.click(x, y)
            d.press("home")
            time.sleep(1)
            d.app_start(package)
            
            qtd_reset += 1
            total_ads_shown += 1
            espera_anuncio = 0
            
            print(f"[INFO] Total de anúncios exibidos: {total_ads_shown}")
            
            if total_ads_shown >= 100:
                print(f"[ALERTA] Atingiu 100 anúncios. Limpando dados do aplicativo...")
                d.app_clear(package)
                total_ads_shown = 0
                qtd_reset = 0
                time.sleep(2) # Espera um pouco para garantir que o clear terminou
            
            sucesso = True
            break

    if not sucesso:
        print("[LOG] Tentativas esgotadas. Reiniciando app...")
        take_screenshot(device, 'errors')
        d.app_stop(package)
        time.sleep(0.5)
        d.app_start(package)


# ==================== LOOP PRINCIPAL ====================
safe_connect(device)
set_window_title(f"Dispositivo: {device}")
d.app_stop(package)

while True:
    try:
        d.app_start(package)
        time.sleep(0.5)

        current_package = d.app_current()['package']
        if current_package == package:
            print("Aplicativo iniciado com sucesso.")

        print(f"Contador de resets: {qtd_reset}")
        print(f"Contador espera de anuncio: {espera_anuncio}")

        if espera_anuncio == 20:
            d.app_stop(package)
            reset_id()
            time.sleep(1)
            d.app_start(package)
            espera_anuncio = 0

        if qtd_reset >= 20:
            print("Resetando ID de publicidade...")
            d.app_stop(package)
            time.sleep(1)
            reset_id()
            qtd_reset = 0

        current_info = d.app_current()
        activity = current_info['activity']
        
        check_and_close_errors(d)

        # Lógica do Jogo (MainActivity)
        if ('.MainActivity' in activity) and (current_info['package'] == package):
            if not processar_jogo(d):
                continue # Se retornou False, reiniciou o app, volta pro início do loop
        
        # Lógica de Fechamento de Anúncios (Activities diversas)
        current_info = d.app_current() # Atualiza info
        activity = current_info['activity']
        
        if ('.MainActivity' not in activity and 
            '.SplashActivity' not in activity and 
            '.adid.settings.AdsSettingsActivity' not in activity):
            
            fechar_anuncio(d, activity)

    except Exception as e:
        print(f"Erro durante a execução: {e}")
        print("Tentando reconectar ao dispositivo...")
        try:
            safe_connect(device)
        except:
            print("Reconexão falhou. Aguardando...")
            time.sleep(30)
