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
from check import detect_objects_and_get_coordinates
import xml.etree.ElementTree as ET

# Variáveis globais
device = sys.argv[1]
modo_ponto = int(sys.argv[2])
tempo_inicio = 35
package = "com.cmastudio.jokenpoduelo"
rodadas = 0
d = None
qtd_reset = 0
espera_anuncio = 0

# selectors = [
#     "com.cmastudio.jokenpoduelo:id/inmobi_close_btn",
#     "com.cmastudio.jokenpoduelo:id/ia_iv_close_button",
#     "com.cmastudio.jokenpoduelo:id/ia_fl_close_button",
#     "inmobi_close_btn",
#     "m-playable-close",
#     "m-playable-skip",
#     "com.cmastudio.jokenpoduelo:id/mbridge_rl_playing_close",
#     "com.cmastudio.jokenpoduelo:id/bigo_ad_btn_close",
#     "com.cmastudio.jokenpoduelo:id/mbridge_video_common_alertview_confirm_button",
#     "com.cmastudio.jokenpoduelo:id/mbridge_video_common_alertview_confirm_button"
# ]

selectors = [
# InMobi
    "inmobi_close_btn",
    "com.inmobi.rendering.inmobi_close_btn",
    "com.cmastudio.jokenpoduelo:id/inmobi_close_btn",

    # Unity Ads
    "com.unity3d.services:id/closeButton",

    # AdMob
    "com.google.android.gms:id/close_button",

    # Chartboost
    "close_button",
    "btn_close",

    # IronSource
    "btnClose",

    # Fyber
    "iv_close",
    "com.cmastudio.jokenpoduelo:id/ia_clickable_close_button"

    # AppLovin
    "applovin_close_button",

    # Vungle
    "vungle_close_button",

    # Mintegral
    "m-playable-close",
    "m-playable-skip",
    "com.cmastudio.jokenpoduelo:id/mbridge_video_common_alertview_confirm_button",
    "com.emaphoto.pic:id/mbridge_rl_playing_close",

    # SDKs e internos do seu app
    "com.cmastudio.jokenpoduelo:id/ia_iv_close_button",
    "com.cmastudio.jokenpoduelo:id/ia_fl_close_button",
    "com.cmastudio.jokenpoduelo:id/mbridge_rl_playing_close",
    "com.cmastudio.jokenpoduelo:id/bigo_ad_btn_close",

    # Genéricos
    "interstitial_close_button",
    "ia_iv_close_button",
    "ia_fl_close_button",
    "mbridge_rl_playing_close",
    "bigo_ad_btn_close",
    "com.flx.duplicate.files.clean:id/thinkup_myoffer_btn_close_id",
    "com.best.quick.browser:id/i",
    "com.cmastudio.jokenpoduelo:id/ia_tv_skip",
    "com.cmastudio.jokenpoduelo:id/mbridge_windwv_close"
]

texts = ["Confirm choices", "","Close", "Continue", "Ir para o app", "AGORA NÃO", "FECHAR", "Fechar","Skip", "Ignorar", "Continuar", "x", "X", "Aceitar", "CLOSE", "Cerrar", "Concordar com tudo", "NO THANKS"]

descricoes = [
    "IACloseButton",
    "Close",
    "Fechar",
]

whitelist = [
    "com.googlecode.eyesfree.setorientation",
    "com.arlosoft.macrodroid",
    "com.zhiliaoapp.musically",
    "com.oak.cryptosense",
    "com.cmastudio.jokenpoduelo",
    "com.appswork.inspiracoes",
    "com.workdigital.menteprospera",
    "com.sln.prepositions",
    "com.frases.frasesvip",
    "com.workdigital.radiomusic",
    "com.israbyte.dropbin",
    "com.lkss.oraculodigital"
]


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



def remover_apps_nao_autorizados(device_serial, whitelist):
    """
    Remove todos os apps de terceiros (instalados pelo usuário) que não estão na whitelist.

    Args:
        device_serial (str): Serial do dispositivo
        whitelist (list): Lista de pacotes permitidos
    """
    try:
        output = subprocess.check_output(f"adb -s {device_serial} shell pm list packages -3", shell=True, text=True)
        pacotes_terceiros = set(line.replace("package:", "").strip() for line in output.strip().splitlines())

        a_remover = [pkg for pkg in pacotes_terceiros if pkg not in whitelist]

        if not a_remover:
            print("[OK] Nenhum app não autorizado foi detectado.")
            return

        print(f"[ALERTA] {len(a_remover)} apps não autorizados serão removidos.")
        for pacote in a_remover:
            print(f" - Desinstalando: {pacote}")
            subprocess.call(f"adb -s {device_serial} uninstall {pacote}", shell=True)

        print("[INFO] Remoção concluída.")

    except Exception as e:
        print(f"[ERRO] Falha ao remover apps não autorizados: {e}")
def parse_dump_for_ids(dump_xml, ids):
    for node in dump_xml.iter("node"):
        if node.attrib.get("resource-id", "") in ids:
            return node.attrib.get("resource-id")
    return None


def parse_dump_for_texts(dump_xml, texts):
    for node in dump_xml.iter("node"):
        if node.attrib.get("text", "") in texts:
            return node.attrib.get("text")
    return None


def clicar_por_id(possiveis_ids, timeout=5):
    for _ in range(timeout):
        tree_str = d.dump_hierarchy()
        dump_xml = ET.fromstring(tree_str)
        btn_id = parse_dump_for_ids(dump_xml, possiveis_ids)
        if btn_id:
            print(f"[INFO] Clicando no ID '{btn_id}'")
            d(resourceId=btn_id).click()
            return True
    print(f"[INFO] Nenhum dos IDs {possiveis_ids} encontrado.")
    return False


def clicar_por_texto(possiveis_textos, timeout=5):
    for _ in range(timeout):
        tree_str = d.dump_hierarchy()
        dump_xml = ET.fromstring(tree_str)
        btn_text = parse_dump_for_texts(dump_xml, possiveis_textos)
        if btn_text:
            print(f"[INFO] Clicando em '{btn_text}'")
            d(text=btn_text).click()
            return True
    print(f"[INFO] Nenhum dos textos {possiveis_textos} encontrado.")
    return False
    return False

def clicar_ou_gravar_ponto(d, activity_name, max_tentativas=5):
    global qtd_reset
    global espera_anuncio


    def parse_dump_for_texts(dump_xml, texts):
        for node in dump_xml.iter("node"):
            if node.attrib.get("text", "") in texts:
                return node.attrib.get("text")
        return None

    def parse_dump_for_ids(dump_xml, ids):
        for node in dump_xml.iter("node"):
            if node.attrib.get("resource-id", "") in ids:
                return node.attrib.get("resource-id")
        return None

    # Trata MainActivity
    if ('.MainActivity' in d.app_current()['activity']) and (d.app_current()['package'] == package):
        print("[LOG] Estamos no MainActivity principal do app.")

        if d(text="Continue").exists:
            clicar_por_texto(["Continue"])

        if d(text="Confirm choices").exists:
            clicar_por_texto(["Confirm choices"])

        # Obtém dimensões da tela (largura, altura)
        width, height = d.window_size()
        print(f"Tamanho da tela: {width}x{height}")

        # Defina a proporção do ponto do clique
        # (exemplo: X no canto superior direito)
        x_ratio = 0.95  # 95% da largura
        y_ratio = 0.08  # 8% da altura

        # Converte proporção em coordenadas absolutas
        x = int(width * x_ratio)
        y = int(height * y_ratio)

        print(f"Clicando em: {x}, {y}")
        d.click(x, y)

        d.click(533, 1300)

        tree_str = d.dump_hierarchy()
        dump_xml = ET.fromstring(tree_str)


        if not parse_dump_for_ids(dump_xml, [
            "com.cmastudio.jokenpoduelo:id/btnIniciarBatalha"
        ]):
            if parse_dump_for_texts(dump_xml, [
                "Acesso Bloqueado"
            ]):
                print("Acesso Bloqueado... reiniciando")
                d(resourceId="android:id/button1").click()
                time.sleep(1)
                d.app_start(package)

        while not d(text="Deseja iniciar uma nova batalha?").exists:
            d(resourceId="com.cmastudio.jokenpoduelo:id/btnIniciarBatalha").click()
            print("[LOG] Botão de ad não encontrado. Jogando...")
            clicar_botao_randomico(d)
            time.sleep(0.3)
            encontrou_algo = True
            activity_name = d.app_current()['activity']

        while d(text="Deseja iniciar uma nova batalha?").exists and d(resourceId="android:id/button1").exists:
            d(resourceId="android:id/button1").click()
            espera_anuncio = espera_anuncio +1

            print("[LOG] Abrindo anúncio...")

            tree_str = d.dump_hierarchy()
            dump_xml = ET.fromstring(tree_str)
            texto_encontrado = parse_dump_for_texts(dump_xml, texts)
            if texto_encontrado:
                print(f"[LOG] Texto '{texto_encontrado}' detectado no MainActivity. Vai tentar clicar.")
                d(text=texto_encontrado).click()
                encontrou_algo = True
                time.sleep(0.3)
                break

        if encontrou_algo:
            return

    # --- A partir daqui: outras activities ---
    if ('.MainActivity' not in activity_name
            and '.SplashActivity' not in activity_name
            and '.adid.settings.AdsSettingsActivity' not in activity_name):
        tree_str = d.dump_hierarchy()
        dump_xml = ET.fromstring(tree_str)

        ids_presentes = [node.attrib.get("resource-id", "") for node in dump_xml.iter("node")]
        if "com.cmastudio.jokenpoduelo:id/btnIniciarBatalha" not in ids_presentes:
            print(f"[LOG] Aguardando {tempo_inicio}s antes de iniciar cliques na activity de anúncio '{activity_name}'")
            time.sleep(tempo_inicio)

    tentativas = 0
    sucesso = False
    modo = int(modo_ponto)
    ciclos_maximos = 15
    ciclos_feitos = 0

    while ciclos_feitos < ciclos_maximos:
        print(f"[LOG] Iniciando volta {ciclos_feitos + 1} nos pontos salvos...")

        tree_str = d.dump_hierarchy()
        dump_xml = ET.fromstring(tree_str)
        botao_clicado = False

        print(f"[LOG] Procurando botões de id...")
        for sel in selectors:
            if any(node.attrib.get("resource-id", "") == sel for node in dump_xml.iter("node")):
                print(f"[LOG] Botão '{sel}' detectado. Clicando...")
                d(resourceId=sel).click()
                time.sleep(0.3)
                botao_clicado = True
                break

        if not botao_clicado:
            print(f"[LOG] Procurando botões de texto...")
            for txt in texts:
                if any(node.attrib.get("text", "") == txt for node in dump_xml.iter("node")):
                    print(f"[LOG] Botão '{txt}' detectado. Clicando...")
                    d(text=txt).click()
                    time.sleep(0.3)
                    botao_clicado = True
                    break

        if not botao_clicado:
            if not any(node.attrib.get("text", "") == "Ir para o app" for node in dump_xml.iter("node")):
                try:
                    btns = [b for b in d.xpath('//android.widget.Button[@text=""]').all() if
                            not b.info.get("focusable", True)]
                    if btns:
                        print("[LOG] Botão com texto vazio encontrado via XPath. Clicando...")
                        btns[0].click()
                        botao_clicado = True
                except Exception as e:
                    print(f"[ERRO] Falha ao clicar via XPath: {e}")
            else:
                print("[LOG] Texto 'Ir para o app' detectado. Ignorando clique via XPath.")

        if not botao_clicado:
            try:
                if any(node.attrib.get("content-desc", "") == "Interstitial close button" for node in
                       dump_xml.iter("node")):
                    print("[LOG] Botão com description 'Interstitial close button' encontrado. Clicando...")
                    d(description="Interstitial close button").click()
                    botao_clicado = True
            except Exception as e:
                print(f"[ERRO] Falha ao clicar via description: {e}")

        if not botao_clicado:
            clicar_elementos_xpath_personalizados(d)

        if not botao_clicado:
            print(f"[LOG] Procurando botões por descrição (content-desc)...")
            for desc in descricoes:
                if any(node.attrib.get("content-desc", "") == desc for node in dump_xml.iter("node")):
                    try:
                        print(f"[LOG] Botão com descrição '{desc}' detectado. Clicando...")
                        d(description=desc).click()
                        time.sleep(0.3)
                        botao_clicado = True
                        break
                    except Exception as e:
                        print(f"[ERRO] Falha ao clicar na descrição '{desc}': {e}")

        if not botao_clicado:
            for txt in ["Fechar", "AGORA NÃO"]:
                if any(node.attrib.get("text", "") == txt and node.attrib.get("class", "") == "android.widget.TextView"
                       for node in dump_xml.iter("node")):
                    print(f"[LOG] Encontrou botão '{txt}'. Clicando...")
                    d(text=txt, className="android.widget.TextView").click()
                    time.sleep(0.3)
                    botao_clicado = True
                    break

        if not botao_clicado:
            caminho = take_screenshot(device, 'screenshots')
            try:
                coord_list = detect_objects_and_get_coordinates(
                    caminho,
                    model_path='runs/detect/train3/weights/best.pt',
                    show_image=False,
                    verbose=True
                )
                if coord_list:
                    x = coord_list[0]['x']
                    y = coord_list[0]['y']
                    print(f"[INFO] Tocando em ({x}, {y}) via ADB...")
                    d.click(x, y)
                else:
                    print("[AVISO] Nenhum objeto detectado para clicar.")
            except Exception as e:
                print(f"[ERRO] {e}")
            finally:
                try:
                    os.remove(caminho)
                    print(f"[INFO] Imagem removida: {caminho}")
                except Exception as e:
                    print(f"[ERRO] Falha ao remover a imagem: {e}")

        if detectar_e_voltar_se_browser(d):
            print("[LOG] Pressionado back para sair de browser ou adactivity.")

        current_info = d.app_current()
        current_activity = current_info['activity']
        current_package = current_info['package']

        if current_package != package:
            print(f"[ALERTA] Saiu do pacote '{package}' para '{current_package}' após clique. Corrigindo...")
            d.app_stop(current_package)
            d.app_start(package)
            time.sleep(1)
            current_info = d.app_current()
            current_activity = current_info['activity']
            current_package = current_info['package']

        if (current_package == package) and ('.MainActivity' in current_activity):
            print("[LOG] Voltou para MainActivity após clique. OK.")
            remover_apps_nao_autorizados(device, whitelist)
            # Obtém dimensões da tela (largura, altura)
            width, height = d.window_size()
            print(f"Tamanho da tela: {width}x{height}")

            # Defina a proporção do ponto do clique
            # (exemplo: X no canto superior direito)
            x_ratio = 0.95  # 95% da largura
            y_ratio = 0.08  # 8% da altura

            # Converte proporção em coordenadas absolutas
            x = int(width * x_ratio)
            y = int(height * y_ratio)

            print(f"Clicando em: {x}, {y}")
            d.click(x, y)

            d.press("home")
            time.sleep(1)
            d.app_start(package)
            qtd_reset += 1
            espera_anuncio = 0
            sucesso = True
            break

        ciclos_feitos += 1


    if not sucesso:
        print("[LOG] Tentativas esgotadas no modo automático. Reiniciando app...")
        take_screenshot(device, 'errors')
        d.app_stop(package)
        time.sleep(0.5)
        d.app_start(package)
        time.sleep(0.5)


def take_screenshot(device_id, base_dir='screenshots'):
    activity_name = d.app_current()['activity']
    folder_path = os.path.join(base_dir, activity_name)
    os.makedirs(folder_path, exist_ok=True)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    gray_image_path = os.path.join(folder_path, f"{timestamp}.png")

    # Tira screenshot direto para o PC usando adb exec-out
    raw_bytes = subprocess.check_output(f'adb -s {device_id} exec-out screencap -p', shell=True)

    # Converte bytes em imagem com OpenCV
    image_array = np.frombuffer(raw_bytes, np.uint8)
    image = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
    gray_image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    # Salva imagem em cinza
    cv2.imwrite(gray_image_path, gray_image)
    print(f"[INFO] Screenshot em cinza salva em: {gray_image_path}")
    return gray_image_path

def clicar_elementos_xpath_personalizados(d):
    """Procura e clica em um dos elementos com XPath personalizados se existirem."""
    xpaths = [
        '//*[@resource-id="com.cmastudio.jokenpoduelo:id/ia_tv_skip"]',
        '//*[@resource-id="com.cmastudio.jokenpoduelo:id/ia_clickable_close_button"]',
        '//*[@resource-id="android:id/content"]/android.widget.RelativeLayout[1]/android.widget.LinearLayout[1]/android.widget.ImageView[1]',
        '//*[@resource-id="android:id/content"]/android.widget.FrameLayout[1]/android.widget.FrameLayout[1]',
        '//android.widget.FrameLayout[1]/android.widget.LinearLayout[1]/android.widget.FrameLayout[1]/android.widget.FrameLayout[1]/android.widget.FrameLayout[1]',
        '//*[@resource-id="android:id/content"]/android.widget.FrameLayout[1]/android.widget.FrameLayout[1]/android.widget.ImageView[1]'

    ]

    for xpath in xpaths:
        try:
            if d.xpath(xpath).exists:
                print(f"[LOG] Elemento encontrado via XPath: {xpath}. Clicando...")
                d.xpath(xpath).click()
                time.sleep(0.3)
                return True
        except Exception as e:
            print(f"[ERRO] Falha ao tentar clicar em {xpath}: {e}")

    print("[LOG] Nenhum dos elementos XPath personalizados foi encontrado.")
    return False

def clicar_botao_randomico(d):

    if d(resourceId="com.cmastudio.jokenpoduelo:id/btnIniciarBatalha").exists:
        """Seleciona um dos 3 botões aleatórios e clica, se existir."""
        botoes = [
            '//*[@resource-id="com.cmastudio.jokenpoduelo:id/action_bar_root"]/android.widget.FrameLayout[1]/android.view.ViewGroup[1]/android.widget.ImageView[5]',
            '//*[@resource-id="com.cmastudio.jokenpoduelo:id/action_bar_root"]/android.widget.FrameLayout[1]/android.view.ViewGroup[1]/android.widget.ImageView[6]',
            '//*[@resource-id="com.cmastudio.jokenpoduelo:id/action_bar_root"]/android.widget.FrameLayout[1]/android.view.ViewGroup[1]/android.widget.ImageView[7]',
        ]

        random.shuffle(botoes)  # Embaralha a lista

        for botao_id in botoes:
            if d.xpath(botao_id).exists:
                print(f"[LOG] Botão aleatório '{botao_id}' encontrado. Clicando...")
                d.xpath(botao_id).click()
                return True  # Clicou e saiu

        print("[LOG] Nenhum botão aleatório disponível para clique.")
        return False


import time

def detectar_e_voltar_se_browser(d):
    """
    Detecta se caiu num navegador, AdActivity ou WebView de anúncio
    e pressiona BACK até sair da activity proibida.
    """

    forcar_voltar_keywords = [
        "browser",
        "sg.bigo.ads.api.AdActivity",
        "com.ironsource.sdk.controller.OpenUrlActivity"
    ]

    ignorar_packages = [
        "com.best.quick.browser"
    ]

    pressionou_back = False

    while True:
        current_info = d.app_current()
        current_activity = current_info['activity']
        current_package = current_info['package']

        if current_package in ignorar_packages:
            print(f"[LOG] Ignorando pacote '{current_package}' (navegador permitido).")
            break

        ainda_proibido = False
        for keyword in forcar_voltar_keywords:
            if keyword.lower() in current_activity.lower() or keyword.lower() in current_package.lower():
                ainda_proibido = True
                break

        # Verifica se é o WebView do anúncio da menteprospera
        try:
            xpath_ad = '//*[@resource-id="com.cmastudio.jokenpoduelo:id/ia_ad_content"]/android.widget.RelativeLayout[1]/android.widget.RelativeLayout[2]/android.webkit.WebView[1]'
            if d.xpath(xpath_ad).exists:
                print("[LOG] WebView de anúncio detectado. Pressionando BACK...")
                d.press("back")
                time.sleep(0.5)
                pressionou_back = True
                continue  # volta ao topo para reavaliar se ainda está na activity proibida
        except Exception as e:
            print(f"[ERRO] Verificação de WebView falhou: {e}")

        if not ainda_proibido:
            break

        print(f"[LOG] Ainda em activity proibida '{current_activity}'. Pressionando BACK...")
        d.press("back")
        time.sleep(0.5)
        pressionou_back = True

    return pressionou_back

def reset_id():
    print("====================================================")
    print("Abrindo configurações de ID de publicidade...")
    os.system(f"adb -s {device} shell am start -a com.google.android.gms.settings.ADS_PRIVACY")
    time.sleep(2)
    try:
        reset_ad_id_options = [
            "Redefinir ID de publicidade",
            "Redefinir código de publicidade",
            "Repor ID de publicidade",
            "Reset advertising ID",
            "Reset Advertising ID"
        ]

        for option in reset_ad_id_options:
            reset_option = d(text=option)
            if reset_option.exists:
                print(f"Elemento '{option}' encontrado. Clicando...")
                reset_option.click()
                break
        else:
            print("Elemento 'Redefinir ID de publicidade' não encontrado.")

        time.sleep(2)
        ok_button = d(resourceId="android:id/button1")
        if ok_button.exists:
            print("Botão 'OK' encontrado. Clicando...")
            ok_button.click()
        else:
            print("Botão 'OK' não encontrado.")
    except Exception as e:
        print(f"Ocorreu um erro ao resetar ID: {e}")

# Início do Script
safe_connect(device)
set_window_title(f"Dispositivo: {device}")
d.app_stop(package)

while True:
    try:
        d.app_start(package)
        time.sleep(0.5)

        activity = d.app_current()['activity']
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
            # d.app_clear(package)
            reset_id()
            qtd_reset = 0

        activity = d.app_current()['activity']
        clicar_ou_gravar_ponto(d, activity)


    except Exception as e:
        print(f"Erro durante a execução: {e}")
        print("Tentando reconectar ao dispositivo...")
        try:
            safe_connect(device)
        except ConnectionError:
            print("Reconexão falhou. Aguardando antes de tentar novamente...")
            time.sleep(30)