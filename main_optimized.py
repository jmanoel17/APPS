import sys
import time
import os
import random
import subprocess
import re
import uiautomator2 as u2
from datetime import datetime
import cv2
import numpy as np
import cv2
import numpy as np
import requests
import math
import xml.etree.ElementTree as ET


# Função auxiliar para timestamp
def get_timestamp():
    """Retorna timestamp formatado para logs"""
    return datetime.now().strftime('[%H:%M:%S]')


# Variáveis globais
device = sys.argv[1]
modo_ponto = int(sys.argv[2])
tempo_inicio = 5
package = "com.israbyte.dropbin"
rodadas = 0
d = None
ai_session = requests.Session()

# Configurações consolidadas
SELECTORS = [
    "inmobi_close_btn", "com.inmobi.rendering.inmobi_close_btn", "com.israbyte.dropbin:id/inmobi_close_btn",
    "com.unity3d.services:id/closeButton", "com.google.android.gms:id/close_button",
    "close_button", "btn_close", "btnClose", "iv_close",
    "com.israbyte.dropbin:id/ia_clickable_close_button", "applovin_close_button",
    "vungle_close_button", "m-playable-close", "m-playable-skip",
    "com.israbyte.dropbin:id/mbridge_video_common_alertview_confirm_button",
    "com.emaphoto.pic:id/mbridge_rl_playing_close",
    "com.israbyte.dropbin:id/ia_iv_close_button", "com.israbyte.dropbin:id/ia_fl_close_button",
    "com.israbyte.dropbin:id/mbridge_rl_playing_close", "com.israbyte.dropbin:id/bigo_ad_btn_close",
    "interstitial_close_button", "ia_iv_close_button", "ia_fl_close_button",
    "mbridge_rl_playing_close", "bigo_ad_btn_close",
    "com.flx.duplicate.files.clean:id/thinkup_myoffer_btn_close_id",
    "com.best.quick.browser:id/i", "com.israbyte.dropbin:id/ia_tv_skip",
    "com.israbyte.dropbin:id/mbridge_windwv_close", "com.israbyte.dropbin:id/iv_ad_close",
    "com.israbyte.dropbin:id/ad_close", "skipBtnImg", "skip-button"
]

TEXTS = ["", "Close", "Continue", "Ir para o app", "AGORA NÃO", "FECHAR", "Fechar",
         "Skip", "Ignorar", "Continuar", "x", "X", "Aceitar", "CLOSE", "Cerrar",
         "Concordar com tudo", "NO THANKS", "No Thanks", "no thanks", "No thanks", "skipAllIcon"]

DESCRIPTIONS = ["IACloseButton", "Close", "Fechar", "No Thanks", "no thanks", "NO THANKS", "Skip All"]

WHITELIST = [
    "com.googlecode.eyesfree.setorientation", "com.arlosoft.macrodroid",
    "com.zhiliaoapp.musically", "com.oak.cryptosense", "com.israbyte.dropbin",
    "com.appswork.inspiracoes", "com.workdigital.menteprospera", "com.sln.prepositions",
    "com.frases.frasesvip", "com.workdigital.radiomusic", "com.cmastudio.jokenpoduelo",
    "com.lkss.oraculodigital", "com.sln.marketfrases"
]

XPATHS_PERSONALIZADOS = [
    '//*[@text="Close"]',
    '//*[@text="Skip"]',
    '//*[@resource-id="com.android.chrome:id/menu_button_wrapper"]',
    '//*[@resource-id="com.israbyte.dropbin:id/ia_tv_skip"]',
    '//*[@resource-id="com.israbyte.dropbin:id/ia_clickable_close_button"]',
    '//*[@resource-id="android:id/content"]/android.widget.RelativeLayout[1]/android.widget.LinearLayout[1]/android.widget.ImageView[1]',
    '//*[@resource-id="te-clr1-4f8bce0e-6193-4784-839d-08f4cd5b0649-icon"]',
    '//*[@resource-id="te-clr1-f4f660de-3a14-4e02-bdf3-70f2c804dc2d-icon"]',
    # '//*[@resource-id="android:id/content"]/android.widget.FrameLayout[1]/android.widget.FrameLayout[1]/android.widget.ImageView[1]'
]

BOTOES_RANDOMICOS = [
    '//*[@resource-id="com.israbyte.dropbin:id/action_bar_root"]/android.widget.FrameLayout[1]/android.view.ViewGroup[1]/android.widget.ImageView[5]',
    '//*[@resource-id="com.israbyte.dropbin:id/action_bar_root"]/android.widget.FrameLayout[1]/android.view.ViewGroup[1]/android.widget.ImageView[6]',
    '//*[@resource-id="com.israbyte.dropbin:id/action_bar_root"]/android.widget.FrameLayout[1]/android.view.ViewGroup[1]/android.widget.ImageView[7]'
]

FORCAR_VOLTAR_KEYWORDS = ["browser", "sg.bigo.ads.api.AdActivity", "com.ironsource.sdk.controller.OpenUrlActivity"]
IGNORAR_PACKAGES = ["com.best.quick.browser"]


def set_window_title(title):
    """Define o título da janela do terminal"""
    if os.name == 'nt':
        os.system(f"title {title}")
    else:
        sys.stdout.write(f"\033]0;{title}\007")
        sys.stdout.flush()


def safe_connect(device):
    """Conecta ao dispositivo com retry automático"""
    global d
    while True:
        try:
            print(f"{get_timestamp()} Conectando ao dispositivo: {device}")
            d = u2.connect(device)
            if d.info:
                print(f"{get_timestamp()} Conectado com sucesso!")
                return
            raise Exception("Falha na conexão. Dispositivo não disponível.")
        except Exception as e:
            print(f"{get_timestamp()} Erro ao conectar: {e}")
            time.sleep(5)


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


def get_hierarchy_data():
    """Obtém e parseia a hierarquia do dispositivo uma única vez"""
    tree_str = d.dump_hierarchy()
    return ET.fromstring(tree_str)


def find_element_in_hierarchy(dump_xml, attr_name, attr_values):
    """Busca genérica de elementos na hierarquia"""
    for node in dump_xml.iter("node"):
        value = node.attrib.get(attr_name, "")
        if value in attr_values:
            return value
    return None


def clicar_por_atributo(dump_xml, attr_name, attr_values, click_func):
    """Função genérica para clicar em elementos por atributo"""
    found = find_element_in_hierarchy(dump_xml, attr_name, attr_values)
    if found:
        print(f"{get_timestamp()} [LOG] Elemento '{found}' detectado ({attr_name}). Clicando...")
        click_func(found)
        time.sleep(0.3)
        return True
    return False


def tentar_clicar_xpath(xpaths):
    """Tenta clicar em elementos via XPath"""
    for xpath in xpaths:
        try:
            if d.xpath(xpath).exists:
                print(f"{get_timestamp()} [LOG] Elemento encontrado via XPath: '{xpath}'. Clicando...")
                d.xpath(xpath).click()
                time.sleep(0.3)
                return True
        except Exception as e:
            print(f"{get_timestamp()} [ERRO] Falha ao clicar em XPath {xpath}: {e}")
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
    for txt in ["Fechar", "AGORA NÃO", "No Thanks", "no thanks", "NO THANKS"]:
        for node in dump_xml.iter("node"):
            if (node.attrib.get("text", "") == txt and
                    node.attrib.get("class", "") == "android.widget.TextView"):
                print(f"{get_timestamp()} [LOG] TextView '{txt}' encontrado. Clicando...")
                d(text=txt, className="android.widget.TextView").click()
                time.sleep(0.3)
                return True
    return False


def tentar_deteccao_ia():
    """Tenta detectar e clicar usando IA via API Server (In-Memory)"""
    # Debug para investigar travamento
    # print(f"{get_timestamp()} [DEBUG] Iniciando captura de tela para IA...")
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
                print(
                    f"{get_timestamp()} [INFO] IA encontrou {count} objetos. Iniciando sequência de cliques inteligente...")

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

                # Ordena ambos por confiança decrescente
                top_priority.sort(key=lambda k: k['confidence'], reverse=True)
                others.sort(key=lambda k: k['confidence'], reverse=True)

                # Lista final ordenada
                final_detections = top_priority + others

                # Itera sobre todos os objetos detectados (já ordenados)
                for i, obj in enumerate(final_detections):
                    x, y = obj['x'], obj['y']
                    conf = obj['confidence']

                    # Filtro de proximidade (30 pixels)
                    too_close = False
                    for cx, cy in clicked_points:
                        if math.hypot(x - cx, y - cy) < 50:
                            too_close = True
                            break

                    if too_close:
                        print(
                            f"{get_timestamp()} [INFO] Ponto ({x}, {y}) ignorado por estar muito próximo de um anterior.")
                        continue

                    print(f"{get_timestamp()} [INFO] [{i + 1}/{count}] Clicando em ({x}, {y}) - Confiança: {conf:.2f}")
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
                        print(
                            f"{get_timestamp()} [INFO] Clique inválido (Browser/Outro App). Tentando próximo ponto...")
                        continue

                    # 2. Verifica se mudou de tela (Sucesso ou outra navegação)
                    try:
                        current_info = d.app_current()
                        current_activity = current_info.get('activity')
                        current_package = current_info.get('package')

                        if current_package != initial_package or current_activity != initial_activity:
                            if '.MainActivity' in str(current_activity):
                                print(
                                    f"{get_timestamp()} [SUCESSO] Anúncio fechado! Voltou para MainActivity. Parando cliques.")
                            else:
                                print(
                                    f"{get_timestamp()} [INFO] Tela mudou para '{current_activity}'. Parando cliques para verificação.")
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
    # Não precisa mais de finally/os.remove pois não salvamos arquivo


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


def click_botao_por_tela(d):
    """
    Detecta automaticamente a resolução física do device
    e executa o clique correto.
    Retorna True se clicou, False se não mapeado.
    """

    try:
        w, h = d.window_size()
        tamanho_tela = f"{w}x{h}"
    except Exception:
        return False

    CLICKS = {
        # Samsung
        "1440x3200": (0.187, 0.74),  # S20
        "1440x3040": (0.187, 0.74),  # S10
        "1440x2960": (0.187, 0.74),  # S8

        # Motorola
        "1080x2160": (0.193, 0.768),  # Moto G6 Plus
        "1080x1920": (0.189, 0.801),  # Moto G5S / G5S Plus
        "1080x2280": (0.173, 0.765),  # Moto G7
        "720x1600": (0.184, 0.750),  # Moto G9 / G9 Play
        "720x1520": (0.186, 0.755),  # Moto G7 Power
        "720x1440": (0.204, 0.766),  # Moto G6 Play
    }

    coords = CLICKS.get(tamanho_tela)

    if not coords:
        return False

    d.click(coords[0], coords[1])
    return True


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

        # Verifica se botão de iniciar batalha está presente ANTES de esperar 5s
        if not d(resourceId="com.israbyte.dropbin:id/btnIniciarBatalha").exists(timeout=2):
            print(f"{get_timestamp()} [LOG] Possível anúncio detectado. Aguardando {tempo_inicio}s...")
            time.sleep(tempo_inicio)
            d.press("back")


        for ciclo in range(30):
            tempo_decorrido = time.time() - start_time_global
            if tempo_decorrido > MAX_TIMEOUT_ADS:
                print(f"{get_timestamp()} [ALERTA] Timeout global de {MAX_TIMEOUT_ADS}s atingido em fechar_anuncio.")
                break

            print(f"{get_timestamp()} [LOG] Ciclo {ciclo + 1}/30... (T: {int(tempo_decorrido)}s)")

            # FAST PATH: Antes de dar dump na hierarquia (caro), tenta IDs mais comuns rápido
            # Isso economiza muito CPU em 100 devices
            for fast_id in ["com.israbyte.dropbin:id/ad_close", "com.google.android.gms:id/close_button",
                            "inmobi_close_btn"]:
                try:
                    if d(resourceId=fast_id).exists(timeout=0):
                        print(f"{get_timestamp()} [FAST-PATH] Detectado '{fast_id}'. Clicando...")
                        d(resourceId=fast_id).click()
                        time.sleep(1)
                        # Se já voltou pro MainActivity, nem precisa do dump
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
            if clicar_por_atributo(dump_xml, "resource-id", SELECTORS, lambda x: d(resourceId=x).click()):
                clicou = True

            # 2. Tenta clicar por texto
            if not clicou and clicar_por_atributo(dump_xml, "text", TEXTS, lambda x: d(text=x).click()):
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
                                                  lambda x: d(description=x).click()):
                clicou = True

            # 7. Tenta TextViews especiais
            if not clicou and tentar_clicar_textview_especial(dump_xml):
                clicou = True

            # Limpa XML da memória o quanto antes
            dump_xml = None

            # 8. IA apenas se nada funcionou e a cada 3 ciclos agora (mais raro para poupar o servidor IA)
            if not clicou and (ciclo % 3 == 0):
                if tentar_deteccao_ia():
                    clicou = True

            # Verifica e volta de browser se necessário
            detectar_e_voltar_se_browser(d)

            # Verifica se voltou para tela principal
            current_info = verificar_e_corrigir_package()
            current_activity = current_info.get('activity', '')

            if current_info['package'] == package and '.MainActivity' in current_activity:
                print(
                    f"{get_timestamp()} [LOG] Voltou para MainActivity após {int(time.time() - start_time_global)}s. Sucesso.")
                # Passa force=False para usar o cache de ciclos
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
        return sucesso


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

        # Converte bytes em imagem com OpenCV para processamento (se necessário)
        # ou apenas repassa os bytes se for para memória

        if save_file:
            try:
                activity_name = d.app_current()['activity']
            except:
                activity_name = "unknown"

            folder_path = os.path.join(base_dir, activity_name)
            os.makedirs(folder_path, exist_ok=True)

            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            gray_image_path = os.path.join(folder_path, f"{timestamp}.png")

            # Decodifica para salvar em grayscale (opcional, mas mantendo comportamento original)
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
            # Retorna os bytes originais (PNG) direto do ADB
            # Ou se quiser garantir grayscale, teria que decodificar e recodificar
            # Mas para a IA, enviar colorido ou cinza geralmente funciona.
            # Vamos enviar o PNG original do ADB para ser mais rápido.
            return raw_bytes

    except subprocess.TimeoutExpired:
        print(f"{get_timestamp()} [ERRO] Timeout ao tirar screenshot via ADB. Processo demorou mais que 10s.")
        return None
    except Exception as e:
        print(f"{get_timestamp()} [ERRO] Falha ao tirar screenshot via ADB: {e}")
        return None


def clicar_botao_randomico(d):
    """Clica em um botão aleatório se disponível"""
    if not d(resourceId="com.israbyte.dropbin:id/btnIniciarBatalha").exists:
        return False

    botoes = BOTOES_RANDOMICOS.copy()
    random.shuffle(botoes)

    for botao_id in botoes:
        if d.xpath(botao_id).exists:
            print(f"{get_timestamp()} [LOG] Botão aleatório encontrado. Clicando...")
            d.xpath(botao_id).click()
            return True

    print(f"{get_timestamp()} [LOG] Nenhum botão aleatório disponível.")
    return False


def detectar_e_voltar_se_browser(d):
    """Detecta e sai de browsers/AdActivities indesejadas"""
    pressionou_back = False

    tentativas_back = 0

    while True:
        if tentativas_back >= 5:
            print(
                f"{get_timestamp()} [ALERTA] Preso em loop de voltar ({tentativas_back}x). Forçando parada de '{current_package}'...")
            d.app_stop(current_package)
            time.sleep(1)
            break

        current_info = d.app_current()
        current_activity = current_info['activity']
        current_package = current_info['package']

        if current_package in IGNORAR_PACKAGES:
            print(f"{get_timestamp()} [LOG] Ignorando pacote '{current_package}'.")
            break

        # Verifica keywords proibidas
        ainda_proibido = any(kw.lower() in current_activity.lower() or
                             kw.lower() in current_package.lower()
                             for kw in FORCAR_VOLTAR_KEYWORDS)

        # Verifica WebView de anúncio
        try:
            xpath_ad = '//*[@resource-id="com.israbyte.dropbin:id/ia_ad_content"]/android.widget.RelativeLayout[1]/android.widget.RelativeLayout[2]/android.webkit.WebView[1]'
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


def reset_id():
    """Reseta o ID de publicidade"""
    print("=" * 52)
    print(f"{get_timestamp()} Abrindo configurações de ID de publicidade...")
    d.shell("am start -a com.google.android.gms.settings.ADS_PRIVACY")
    time.sleep(2)

    try:
        reset_options = [
            "Redefinir ID de publicidade", "Redefinir código de publicidade",
            "Repor ID de publicidade", "Reset advertising ID", "Reset Advertising ID"
        ]

        for option in reset_options:
            if d(text=option).exists:
                print(f"{get_timestamp()} [LOG] Opção de reset encontrada: '{option}'. Clicando...")
                d(text=option).click()
                break
        else:
            print(f"{get_timestamp()} [AVISO] Elemento de reset não encontrado.")

        time.sleep(2)
        if d(resourceId="android:id/button1").exists:
            print(f"{get_timestamp()} [LOG] Botão de confirmação 'OK' (button1) encontrado. Clicando...")
            d(resourceId="android:id/button1").click()
        else:
            print(f"{get_timestamp()} [AVISO] Botão 'OK' não encontrado.")
    except Exception as e:
        print(f"{get_timestamp()} Erro ao resetar ID: {e}")


def clicar_por_descricao(possiveis_descricoes, timeout=5):
    """Clica em elemento por descrição"""
    dump_xml = get_hierarchy_data()
    found = find_element_in_hierarchy(dump_xml, "content-desc", possiveis_descricoes)
    if found:
        print(f"{get_timestamp()} [INFO] Clicando na descrição '{found}'")
        d(description=found).click()
        return True
    print(f"{get_timestamp()} [INFO] Nenhuma descrição encontrada em {possiveis_descricoes}")
    return False


def clicar_por_id(possiveis_ids, timeout=5):
    """Clica em elemento por ID"""
    dump_xml = get_hierarchy_data()
    found = find_element_in_hierarchy(dump_xml, "resource-id", possiveis_ids)
    if found:
        print(f"{get_timestamp()} [INFO] Clicando no ID '{found}'")
        d(resourceId=found).click()
        return True
    print(f"{get_timestamp()} [INFO] Nenhum ID encontrado em {possiveis_ids}")
    return False


def clicar_por_texto(possiveis_textos, timeout=5):
    """Clica em elemento por texto"""
    dump_xml = get_hierarchy_data()
    found = find_element_in_hierarchy(dump_xml, "text", possiveis_textos)
    if found:
        print(f"{get_timestamp()} [INFO] Clicando em '{found}'")
        d(text=found).click()
        return True
    print(f"{get_timestamp()} [INFO] Nenhum texto encontrado em {possiveis_textos}")
    return False


def obter_pontos():
    """Retorna o número de pontos do elemento"""
    try:
        el = d(descriptionStartsWith="Total Points:") or d(descriptionStartsWith="Points:")
        if not el.exists:
            el = d(textStartsWith="Total Points:") or d(textStartsWith="Points:")

        if el.exists:
            texto = el.get_text() or el.info.get('contentDescription', '')
            match = re.search(r'\d+', str(texto))
            if match:
                return int(match.group())

        print(f"{get_timestamp()} [INFO] Elemento de pontos não encontrado.")
        return 0
    except Exception as e:
        print(f"{get_timestamp()} [ERRO] Falha ao obter pontos: {e}")
        return 0


def clicar_por_coordenada(d):
    # Clique em coordenada específica (proporcional)
    width, height = d.window_size()
    print(f"{get_timestamp()} Tamanho da tela: {width}x{height}")

    x = int(width * 0.921)
    y = int(height * 0.067)

    print(f"{get_timestamp()} Clicando em: {x}, {y}")
    d.click(x, y)

def is_doze(d):
    try:
        out = d.shell("dumpsys power").output
        if "mWakefulness=Dozing" in out or "mWakefulness=Asleep" in out:
            print("[LOG] Device em DOZE. Acordando tela...")
            d.press("power")
            time.sleep(1)
            return True
        return False
    except Exception as e:
        print(f"[ERRO] Falha ao verificar/corrigir Doze: {e}")
        return False


def saque_automatico():
    """Realiza saque automático quando pontos > 2000 ou atingiu limite"""
    print(f"{get_timestamp()} [INFO] Iniciando processo de saque...")

    # Verifica se já estamos na tela de saque (botão Send presente)
    if not d(description="Send").exists:
        print(f"{get_timestamp()} [LOG] Navegando para tela de saque...")
        button_xpath = '//*[@resource-id="android:id/content"]/android.widget.FrameLayout[1]/android.view.View[1]/android.view.View[1]/android.view.View[1]/android.view.View[1]/android.view.View[1]/android.widget.Button[1]'

        # Tenta clicar no botão de saque se ele existir
        if d.xpath(button_xpath).exists:
            d.xpath(button_xpath).click()
            time.sleep(5)
    else:
        print(f"{get_timestamp()} [LOG] Já estamos na tela de saque.")

    tentativa = 0
    max_tentativas = 10

    # Loop de verificação para realizar o saque
    # Ajustado para tentar enquanto o botão Send existir ou tivermos pontos
    while tentativa < max_tentativas:
        print(f"{get_timestamp()} [PROCESSANDO] Tentativa de saque {tentativa + 1}/{max_tentativas}")
        tentativa += 1

        # Verifica se precisa preencher o email
        if not d(description="Email FaucetPay: joaogomes183@yahoo.com").exists:
            # Procura campo de texto para email
            if d.xpath('//android.widget.EditText').exists:
                current_text = d.xpath('//android.widget.EditText').get_text()
                target_email = "joaogomes183@yahoo.com"

                if not current_text or target_email not in current_text:
                    print(f"{get_timestamp()} [LOG] Preenchendo email: {target_email}")
                    d.xpath('//android.widget.EditText').set_text(target_email)
                    time.sleep(1)

                    if clicar_por_descricao(["Confirm Email FaucetPay"]):
                        time.sleep(5)
                else:
                    print(f"{get_timestamp()} [LOG] Email já presente no campo.")
            else:
                # Se não achou email nem campo, pode ser que já tenha enviado ou mudou a tela
                pass

        # Clica em Send se disponível
        if d(description="Send").exists:
            print(f"{get_timestamp()} [LOG] Clicando em Send...")
            clicar_por_descricao(["Send"])
            time.sleep(10)

            # Se sumiu o botão Send, provavelmente foi sucesso
            if not d(description="Send").exists:
                print(f"{get_timestamp()} [SUCESSO] Saque realizado (provável).")
                break
        else:
            # Se não tem botão Send e já passamos do inicio, talvez tenha acabado ou pontos insuficientes
            # Mas vamos conferir se temos pontos
            p = obter_pontos()
            if p < 2000:
                print(f"{get_timestamp()} [INFO] Pontos insuficientes ({p}) ou saque concluído.")
                break

    # Volta para a tela principal se ainda estiver na de saque
    if d(description="Send").exists:
        d.press("back")
        time.sleep(1)

    # Reinicia app para garantir estado limpo após saque
    print(f"{get_timestamp()} [INFO] Reiniciando app após tentativa de saque...")
    d.app_stop(package)
    time.sleep(1)
    d.app_start(package)


# ==================== INÍCIO DO SCRIPT ====================
safe_connect(device)
set_window_title(f"Dispositivo: {device}")
qtd_reset = 0
d.app_stop(package)
button_xpath = '//*[@resource-id="android:id/content"]/android.widget.FrameLayout[1]/android.view.View[1]/android.view.View[1]/android.view.View[1]/android.view.View[1]/android.view.View[1]/android.widget.Button[1]'

while True:
    try:
        d.app_start(package)
        time.sleep(0.5)

        current_info = d.app_current()
        if current_info['package'] == package:
            print(f"{get_timestamp()} Aplicativo iniciado com sucesso.")

        clicks_point = 0
        falhas_consecutivas = 0

        while True:
            time.sleep(1)  # Pausa fundamental para não martelar o ADB

            # Cache do estado atual para evitar múltiplas chamadas por segundo
            try:
                current_info = d.app_current()
                current_package = current_info.get('package', '')
                current_activity = current_info.get('activity', '')
            except:
                continue

            if current_package != package:
                d.app_start(package)
                continue

            if '.MainActivity' not in current_activity:
                break

            # Verifica erros de app
            if d(resourceId="android:id/aerr_wait").exists:
                d(resourceId="android:id/aerr_wait").click()
            if d(resourceId="android:id/aerr_close").exists:
                d(resourceId="android:id/aerr_close").click()

            # Verifica Dialogo de pontos máximos
            if d(description="You have accumulated maximum points, send/reset..").exists:
                print(f"{get_timestamp()} [AVISO] Dialogo de pontos máximos detectado. Iniciando saque...")
                if d(description="OK").exists:
                    d(description="OK").click()
                    time.sleep(2)
                saque_automatico()
                # Interrompe loop atual para reiniciar (saque_automatico já reinicia, mas o loop externo garante)
                break

            points = obter_pontos()

            # Saque automático
            if points > 2000:
                saque_automatico()

            # Volta para tela de pontos
            if points < 2000 and d(description="Send").exists:
                d.xpath(button_xpath).click()

            # Clica em Points/Login
            if clicar_por_descricao(["Points", "Login/Register"]):
                clicks_point += 1
                falhas_consecutivas = 0  # Reseta contador de falhas
                time.sleep(0.5)  # Pequena pausa para activity mudar
                try:
                    current_ad_activity = d.app_current().get('activity', 'Unknown')
                    print(f"{get_timestamp()} [LOG] Abrindo anúncio via 'Points/Login'... Activity: {current_ad_activity}")
                except:
                    pass
                time.sleep(4.5)  # Completa os 5 segundos
            else:
                falhas_consecutivas += 1
                print(f"{get_timestamp()} [AVISO] Falha ao encontrar botão Points/Login ({falhas_consecutivas}/10)")
                is_doze(d)

                if falhas_consecutivas >= 10:
                    print(f"{get_timestamp()} [LOG] Muitas falhas consecutivas. Reiniciando app...")

                    d.app_stop(package)
                    time.sleep(1)
                    d.app_start(package)
                    clicks_point = 0
                    falhas_consecutivas = 0
                    break

            # Reinicia a cada 10 cliques
            if clicks_point % 10 == 0 and clicks_point != 0:
                d.app_stop(package)
                time.sleep(1)
                d.app_start(package)

            # Reset após 25 cliques
            if clicks_point >= 25:
                d.app_stop(package)
                time.sleep(1)
                reset_id()
                time.sleep(1)
                d.app_start(package)
                clicks_point = 0
                break

        print(f"{get_timestamp()} Contador de resets: {qtd_reset}")

        # Reset periódico a cada 100 rodadas
        if qtd_reset >= 100:
            print(f"{get_timestamp()} Resetando ID de publicidade...")
            d.app_stop(package)
            time.sleep(1)
            reset_id()
            qtd_reset = 0

        current_info = d.app_current()
        activity = current_info.get('activity', '')
        fechar_anuncio(d, activity)
        qtd_reset += 1
        rodadas += 1  # Incrementa rodadas para controle de throttling

        # Auto-limpeza periódica (Log Self-Cleanup)
        # Dessincronizado para evitar pico quando todos os 100 devices limpam juntos
        if rodadas % 50 == 0:
            # Delay aleatório de 0-10s para espalhar a carga
            time.sleep(random.uniform(0, 10))
            
            print(f"{get_timestamp()} [CLEANUP] Executando auto-limpeza periódica...")
            
            # Força finalização completa do app para limpar memória
            try:
                d.app_stop(package)
                d.shell(f"am force-stop {package}")
                time.sleep(0.5)
            except:
                pass
            
            # Limpa terminal (apenas local, não afeta outros devices)
            if os.name == 'nt':
                os.system('cls')
            else:
                os.system('clear')

            # Limpa logcat do dispositivo
            try:
                d.shell("logcat -c")
            except:
                pass

            print(f"{get_timestamp()} [CLEANUP] Memória limpa. Aguardando 2s antes de continuar...")
            time.sleep(2)  # Pausa para estabilizar antes de recomeçar

    except Exception as e:
        print(f"{get_timestamp()} Erro durante a execução: {e}")
        print(f"{get_timestamp()} Tentando reconectar ao dispositivo...")
        try:
            safe_connect(device)
        except ConnectionError:
            print(f"{get_timestamp()} Reconexão falhou. Aguardando antes de tentar novamente...")
            time.sleep(30)