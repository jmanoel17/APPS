#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
main_sln_v2.py - Bot Drama Classic (SLN V2)

Baseado em captura real (session_20260907_225551) do app com.flixdigital.dramaclassic.

Fluxo:
  1. Abrir app na MainActivity
  2. Clicar 'Assistir anúncio' (btnRewardAds) -> anúncio (gestão de recompensa)
  3. ASSISTIR o anúncio (sem tocar na tela) por um tempo configurável/randomizado
  4. Apenas depois de assistir, iniciar o fechamento (X via IA + heurísticas)
  5. MOVIMENTAR o anúncio (swipes) em % das rodadas para parecer humano
  6. INSTALAR app sugerido em % das rodadas (quando abre Play Store)
  7. Reset de ID / limpeza de dados por %
  8. Salvar screenshot em errors/ quando falhar (para re-treino da IA)

Depende de:
  - config_sln_v2.py        (parâmetros/percentagens)
  - aiMain_sln_v2.py        (servidor central de IA, opcional)

Uso:
    python main_sln_v2.py <device_id> <modo>
"""
import sys
import time
import os
import random
import threading
import subprocess
import re
import gc
import io
import requests
import xml.etree.ElementTree as ET
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import uiautomator2 as u2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config_sln_v2 as CONFIG

_cv2 = None
_np = None

def _lazy_cv2():
    global _cv2, _np
    if _cv2 is None:
        import cv2
        import numpy as np
        _cv2 = cv2
        _np = np
    return _cv2, _np

# ==================== CONFIGURAÇÕES GLOBAIS ====================
device = sys.argv[1]
modo_ponto = int(sys.argv[2]) if len(sys.argv) > 2 else 0
TEST_LIMIT_ADS = int(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3].isdigit() else 0  # 0 = ilimitado (produção)
# Guards de teste (env): encerram o bot após N ciclos ou N segundos de execução (wall-clock).
# Protegem benchmark/teste contra rodadas infinitas. 0/ausente = sem limite.
SLN_TEST_MAX_CYCLES = int(os.environ.get("SLN_TEST_MAX_CYCLES", "0") or 0)
SLN_TEST_MAX_RUNTIME_S = float(os.environ.get("SLN_TEST_MAX_RUNTIME_S", "0") or 0)
# Identidade de instância (ownership/generation): o supervisor usa para validar o heartbeat.
WORKER_INSTANCE_ID = os.environ.get("SLN_WORKER_INSTANCE_ID", "") or ""
try:
    WORKER_GENERATION = int(os.environ.get("SLN_WORKER_GENERATION", "0") or 0)
except Exception:
    WORKER_GENERATION = 0
_t_startup = time.time()
package = "com.flixdigital.dramaclassic"
rodadas = 0
d = None
qtd_reset = 0
total_ads = 0
total_installs = 0
total_moves = 0
total_escapes = 0
falhas_consecutivas = 0
SESS = requests.Session()

cfg = CONFIG.load_config()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ERRORS_DIR = os.path.join(BASE_DIR, cfg.get("errors_dir", "errors"))
os.makedirs(ERRORS_DIR, exist_ok=True)
IA_URL = cfg.get("ia_url", "http://localhost:8000").rstrip('/')
# QA REALISTIC: agendador por device (delay/reset/install). Fora da state machine do anúncio.
# Montado em __main__ SOMENTE quando qa_realistic_behavior_enabled=true (default OFF).
qa_scheduler = None
# AD SESSION / CYCLE: 1 clique "Assistir anúncio" = 1 ciclo = 1 sessão com N stages.
_cycle = None            # ad_session.CycleSession (montado em __main__)
_last_ad_format = None   # último FORMAT observado (telemetria de stage)

import ad_session  # noqa: E402  (modelagem de ciclo/ad-session)
import subad_close  # noqa: E402  (detector estrutural do SUB_AD_CLOSE)

# ===== DPN (orquestração de IP; camada ACIMA dos workers; default OFF) =====
# NÃO altera CycleSession/_cycle_after_ad/record_cycle_result/Fast Close/reward/SUB_AD_CLOSE.
# Com dpn_routing_enabled=false: gate=True imediato e hook=None (byte-idêntico ao fluxo atual).
import uuid as _dpn_uuid
DPN_ENABLED = bool(cfg.get("dpn_routing_enabled", False))
# instance_id ÚNICO por boot do worker (nunca por ciclo) — só para idempotência do DPN.
DPN_INSTANCE_ID = WORKER_INSTANCE_ID or (_dpn_uuid.uuid4().hex if DPN_ENABLED else "")
dpn_worker = None
if DPN_ENABLED:
    try:
        import dpn.worker_hook as dpn_worker
    except Exception:
        dpn_worker = None
        DPN_ENABLED = False


def _fc_detectar_subad_close(root, w, h):
    """Wrapper puro: detecta o SUB_AD_CLOSE (WebView sub-ad, topo-direito, sem id/text/desc).
    APENAS detecta — não clica. Retorna dict (found/bounds/center/norm/reason/reject_reason)."""
    try:
        return subad_close.detect_subad_close(root, w, h, package, _FC_CTA_RE)
    except Exception as e:
        return {"found": False, "reason": "detector_error:%s" % type(e).__name__, "reject_reason": "error"}


def _set_last_format(fmt):
    global _last_ad_format
    _last_ad_format = fmt


def _cycle_on_main():
    try:
        c = cur_app()
        return c.get('package') == package and '.MainActivity' in str(c.get('activity', ''))
    except Exception:
        return False


def _cycle_app_foreground():
    try:
        return cur_app().get('package') == package
    except Exception:
        return False


def _cycle_activity():
    try:
        return str(cur_app().get('activity', ''))
    except Exception:
        return ""


def _qa_record_cycle(result, st):
    """Contabiliza o ciclo uma única vez (COMPLETED/FAILED/ABORTED). Persiste o estado QA."""
    if qa_scheduler is None or st is None:
        return
    try:
        qa_scheduler.record_cycle_result(True, result, st.get("cycle_id"), reason=st.get("reason", ""))
        qa_scheduler.save()
    except Exception:
        pass


def _cycle_after_ad(returned_ok):
    """Atualiza a SESSÃO após processar_ciclo_anuncio (fronteira de ciclo).
    functional: MAIN->COMPLETED | foreground não-MAIN->CONTINUE | escape->FAILED.
    A CONTABILIDADE (qa.record_cycle_result) conta COMPLETED e FAILED (1 ciclo cada)."""
    if _cycle is None or not _cycle.active:
        return None
    if _cycle_on_main():
        st = _cycle.complete(close_layer="main")
        _qa_record_cycle("COMPLETED", st)
        return "COMPLETED"
    if _cycle_app_foreground():
        # fechou um controle mas NÃO voltou à MAIN -> continua a MESMA sessão (novo stage)
        _cycle.continue_stage(activity=_cycle_activity(), ad_format=_last_ad_format, reason="nao_main")
        return "CONTINUE"
    # fora do app (HOME/launcher/force-stop) -> falha terminal da sessão -> CONTA 1 ciclo
    st = _cycle.fail(reason="escape_or_force_stop")
    _qa_record_cycle("FAILED", st)
    return "FAILED"


def _cycle_abort_if_active(reason):
    """Sessão interrompida SEM resultado terminal (guard de teste/watchdog/crash): ABORTED, NÃO conta."""
    if _cycle is not None and _cycle.active:
        st = _cycle.abort(reason)
        _qa_record_cycle("ABORTED", st)

# --- Seletores de fechamento de anúncios (consolidado da captura) ---
SELECTORS = [
    "unityads", "unityads-btn-close", "unityads_button_close", "unity-ads-close",
    "com.unity3d.ads.adplayer.CloseButton",
    "applovin_close_button", "dismiss_button", "webview_close", "applovin-close",
    "com.android.vending:id/close_btn", "close", "com.android.vending:id/0_resource_name_obfuscated",
    "inmobi_close_btn", "com.inmobi.rendering.inmobi_close_btn", "com.google.android.gms:id/close_button",
    "close_button", "btn_close", "btnClose", "iv_close", "m-playable-close", "m-playable-skip",
]

TEXTS = ["Close", "AGORA NÃO", "FECHAR", "Fechar",
         "Skip Ad", "Ignorar", "x", "X", "CLOSE",
         "Cerrar", "NO THANKS", "No Thanks", "no thanks", "Cancelar"]

DESCRIPTIONS = ["IACloseButton", "Close", "Fechar", "No Thanks", "no thanks", "NO THANKS", "Skip All",
                "Fechar planilha", "Cancelar", "close", "Close"]

XPATHS_PERSONALIZADOS = [
    '//*[@content-desc="Close"]',
    '//*[@content-desc="Fechar"]',
    '//*[@content-desc="Fechar planilha"]',
    '//*[@text="Close"]',
    '//*[@text="Fechar"]',
    '//*[@text="Skip"]',
    '//*[@text="Skip Ad"]',
    '//*[@resource-id="com.android.vending:id/close_btn"]',
    '//*[@resource-id="unityads-btn-close"]',
]

FORCAR_VOLTAR_KEYWORDS = [
    "com.android.vending", "play.google.com", "finsky", "google.android.finsky",
    "browser", "com.android.chrome", "sg.bigo.ads", "OpenUrlActivity", "AdActivity"
]
IGNORAR_PACKAGES = ["com.best.quick.browser"]
STORE_PACKAGES = ["com.android.vending"]

# ==================== HELPERS ====================

def get_timestamp():
    return datetime.now().strftime('[%H:%M:%S]')

def log(msg):
    print(f"{get_timestamp()} {msg}", flush=True)

def set_window_title(title):
    if os.name == 'nt':
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleTitleW(title)
        except Exception:
            pass
    else:
        sys.stdout.write(f"\033]0;{title}\007")
        sys.stdout.flush()

# ==================== TELEMETRIA LEVE ([PERF]) ====================
# Contadores por processo/device. NÃO loga por tick: um resumo [PERF] a cada ~60s.
# Custo: inteiros + deque curto (sem I/O, sem acesso ao device). Instrumentação de escala:
# se algo aqui começar a pesar, o próprio [PERF] vai mostrar (dump_ms/ai_latency).
PERF_INTERVAL_S = float(os.environ.get("SLN_PERF_INTERVAL_S", "60") or 60)
PERF_SAMPLE_MAX = 300   # janela rolante para avg/p95 (barata e suficiente)

_PERF_START = time.perf_counter()
_perf = {
    "hierarchy_dump_count": 0, "hierarchy_dump_bytes": 0,
    "format_lookup_count": 0, "format_from_memory_count": 0, "adb_subprocess_count": 0,
    "screenshot_count": 0, "screenshot_bytes": 0,
    "ai_request_count": 0, "ai_over_4s_count": 0,
    "reset_count": 0, "reset_ms": 0, "clear_count": 0, "clear_ms": 0,
    "reconnect_count": 0, "errors": 0,
    "ticks_total": 0,
    "dump_gt_1s": 0, "dump_gt_2s": 0, "dump_gt_5s": 0, "dump_gt_10s": 0, "dump_gt_30s": 0,
}
_perf_samples = {
    "dump_ms": deque(maxlen=PERF_SAMPLE_MAX),
    "format_lookup_ms": deque(maxlen=PERF_SAMPLE_MAX),
    "ai_latency_ms": deque(maxlen=PERF_SAMPLE_MAX),
    "requested_tick_ms": deque(maxlen=PERF_SAMPLE_MAX),
    "actual_tick_ms": deque(maxlen=PERF_SAMPLE_MAX),
}
_perf_ticks_by_state = {}


def perf_inc(key, n=1):
    """Incrementa um contador do [PERF]. Nunca levanta exceção."""
    try:
        _perf[key] = _perf.get(key, 0) + n
    except Exception:
        pass


def perf_bytes(key, n):
    """Acumula bytes (ignora None/0)."""
    try:
        if n:
            perf_inc(key, int(n))
    except Exception:
        pass


def perf_sample(key, ms):
    """Registra uma amostra de latência (janela rolante) para avg/p95/max."""
    try:
        _perf_samples[key].append(float(ms))
    except Exception:
        pass


def perf_ai_latency(ms):
    """Telemetria de IA POR REQUISIÇÃO: conta request e conta >4s SÓ se ESTA requisição passou de 4s."""
    perf_inc("ai_request_count")
    perf_sample("ai_latency_ms", ms)
    if float(ms) > 4000:
        perf_inc("ai_over_4s_count")


def _perf_stat(samples):
    """(avg, p95, max) da janela; (0,0,0) se vazia."""
    if not samples:
        return 0.0, 0.0, 0.0
    vals = sorted(samples)
    avg = sum(vals) / len(vals)
    p95 = vals[min(len(vals) - 1, int(round(0.95 * (len(vals) - 1))))]
    return avg, p95, vals[-1]


def _perf_log():
    """Uma linha [PERF] com o resumo do período. Barato: só lê contadores."""
    du = _perf_stat(_perf_samples["dump_ms"])
    fl = _perf_stat(_perf_samples["format_lookup_ms"])
    ai = _perf_stat(_perf_samples["ai_latency_ms"])
    rt = _perf_stat(_perf_samples["requested_tick_ms"])
    at = _perf_stat(_perf_samples["actual_tick_ms"])
    up = max(1e-6, time.perf_counter() - _PERF_START)
    dump_rate = _perf["hierarchy_dump_count"] / (up / 60.0)
    logcat_mode = "persistent" if (cfg.get("persistent_logcat_enabled", False) and _fc_logcat_reader) else "snapshot"
    extra = ""
    if cfg.get("persistent_logcat_enabled", False) and _fc_logcat_reader:
        try:
            m = _fc_logcat_reader.metrics()
            extra = " ".join(f"{k}={v}" for k, v in m.items())
        except Exception:
            extra = ""
    flog(f"[PERF] device={device} uptime={up:.0f}s "
         f"hierarchy_dump_count={_perf['hierarchy_dump_count']} hierarchy_dump_bytes={_perf['hierarchy_dump_bytes']} "
         f"dump_ms_avg={du[0]:.0f} dump_ms_p95={du[1]:.0f} dump_ms_max={du[2]:.0f} "
         f"format_lookup_count={_perf['format_lookup_count']} format_from_memory_count={_perf['format_from_memory_count']} "
         f"format_lookup_ms_avg={fl[0]:.0f} "
         f"format_lookup_ms_max={fl[2]:.0f} adb_subprocess_count={_perf['adb_subprocess_count']} "
         f"screenshot_count={_perf['screenshot_count']} screenshot_bytes={_perf['screenshot_bytes']} "
         f"ai_request_count={_perf['ai_request_count']} ai_latency_avg={ai[0]:.0f} ai_latency_max={ai[2]:.0f} "
         f"ai_over_4s_count={_perf['ai_over_4s_count']} "
         f"reset_count={_perf['reset_count']} reset_ms={_perf['reset_ms']} "
         f"clear_count={_perf['clear_count']} clear_ms={_perf['clear_ms']} "
         f"reconnect_count={_perf['reconnect_count']} errors={_perf['errors']} "
         f"logcat_mode={logcat_mode} ticks_total={_perf['ticks_total']} "
         f"ticks_by_state={_perf_ticks_by_state} requested_tick_ms_avg={rt[0]:.0f} actual_tick_ms_avg={at[0]:.0f} "
         f"dump_rate_per_min={dump_rate:.0f} "
         f"dump_gt_1s={_perf['dump_gt_1s']} dump_gt_2s={_perf['dump_gt_2s']} "
         f"dump_gt_5s={_perf['dump_gt_5s']} dump_gt_10s={_perf['dump_gt_10s']} "
         f"dump_gt_30s={_perf['dump_gt_30s']}" + (" " + extra if extra else ""))


def _perf_loop():
    """Thread de telemetria: um [PERF] a cada PERF_INTERVAL_S (~60s). Sem log por tick."""
    while True:
        try:
            time.sleep(PERF_INTERVAL_S)
            _perf_log()
        except Exception:
            pass


def _dump_hierarchy_medido():
    """ÚNICO choke point de d.dump_hierarchy(): mede count/bytes/ms para o [PERF].
    Exceções sobem como antes (o tratamento continua com quem chamou)."""
    t0 = time.perf_counter()
    raw = d.dump_hierarchy()
    ms = (time.perf_counter() - t0) * 1000
    perf_inc("hierarchy_dump_count")
    perf_bytes("hierarchy_dump_bytes", len(raw) if raw else 0)
    perf_sample("dump_ms", ms)
    # buckets de latência (telemetria de capacidade)
    if ms > 1000: perf_inc("dump_gt_1s")
    if ms > 2000: perf_inc("dump_gt_2s")
    if ms > 5000: perf_inc("dump_gt_5s")
    if ms > 10000: perf_inc("dump_gt_10s")
    if ms > 30000: perf_inc("dump_gt_30s")
    return raw


def safe_connect(device):
    global d
    retries = 0
    while True:
        try:
            log(f"Conectando ao dispositivo: {device}")
            d = u2.connect(device)
            if d.info:
                log("Conectado com sucesso!")
                return
            raise Exception("Dispositivo nao disponivel")
        except Exception as e:
            retries += 1
            perf_inc("reconnect_count")
            perf_inc("errors")
            sleep_t = min(5 * (1.5 ** min(retries, 8)), 60)
            log(f"Erro ao conectar: {e}. Tentando novamente em {sleep_t:.0f}s (retry {retries})")
            time.sleep(sleep_t)

_DEVICE_LOCK_RECHECK_S = 30      # pausa entre verificações quando bloqueado (sem apertar POWER)
_DEVICE_LOCK_LOG_EVERY_S = 300


def _device_bloqueado(d):
    """(bloqueado, motivo). Tela apagada/dormindo, keyguard (lockscreen / 'Proteção contra
    toque acidental' da Samsung) ou dreaming lockscreen. Não tenta contornar nada."""
    try:
        pw = d.shell("dumpsys power").output
        if "mWakefulness=Dozing" in pw or "mWakefulness=Asleep" in pw:
            return True, "screen_off"
        win = d.shell("dumpsys window").output
        if "isKeyguardShowing=true" in win or "mDreamingLockscreen=true" in win:
            return True, "keyguard_showing"
        return False, ""
    except Exception as e:
        return False, f"check_failed:{type(e).__name__}"


def is_doze(d):
    """UMA tentativa de acordar (POWER só com a tela apagada). Se continuar bloqueado
    (lockscreen / proteção contra toque acidental / sensor coberto), pausa o ciclo deste
    aparelho sem apertar POWER de novo até alguém intervir manualmente."""
    bloq, motivo = _device_bloqueado(d)
    if not bloq:
        return False
    if motivo == "screen_off":
        log("[LOG] Device em DOZE. Acordando tela (1 tentativa)...")
        try:
            d.press("power")
        except Exception:
            pass
        time.sleep(2)
        bloq, motivo = _device_bloqueado(d)
        if not bloq:
            return True
    inicio = time.time()
    ultimo_log = 0.0
    while bloq:
        if time.time() - ultimo_log >= _DEVICE_LOCK_LOG_EVERY_S:
            ultimo_log = time.time()
            log(f"[DEVICE][LOCKED] manual_intervention_required reason={motivo} "
                f"paused_for={time.time() - inicio:.0f}s (sem POWER; rechecando a cada {_DEVICE_LOCK_RECHECK_S}s)")
        time.sleep(_DEVICE_LOCK_RECHECK_S)
        bloq, motivo = _device_bloqueado(d)
    log(f"[DEVICE][UNLOCKED] retomando após {time.time() - inicio:.0f}s")
    return True

def cur_app():
    try:
        return d.app_current()
    except Exception:
        return {}

# ==================== HIERARQUIA / CLICKS ====================

def get_hierarchy_data():
    try:
        return ET.fromstring(_dump_hierarchy_medido())
    except Exception as e:
        log(f"[ERRO] dump_hierarchy: {e}")
        return None

def find_element_in_hierarchy(dump_xml, attr_name, attr_values):
    if dump_xml is None:
        return None
    for node in dump_xml.iter("node"):
        value = node.attrib.get(attr_name, "")
        if value in attr_values:
            return value
    return None

def clicar_por_atributo(dump_xml, attr_name, attr_values, click_func, min_region=None):
    if dump_xml is None:
        return False
    for node in dump_xml.iter("node"):
        value = node.attrib.get(attr_name, "")
        if value in attr_values:
            bounds = node.attrib.get("bounds", "")
            if min_region:
                nums = list(map(int, re.findall(r"\d+", bounds)))
                if len(nums) != 4:
                    continue
                x1, y1, x2, y2 = nums
                mx1, my1, mx2, my2 = min_region
                if not (x1 >= mx1 and y1 >= my1 and x2 <= mx2 and y2 <= my2):
                    continue
            log(f"[LOG] Elemento '{value}' ({attr_name}) encontrado. Clicando...")
            try:
                el = click_func(value)
                if el is None:
                    return True
                el.click()
                time.sleep(0.3)
                return True
            except Exception as e:
                log(f"[ERRO] Falha ao clicar em {value}: {e}")
    return False

def clicar_coordenada_por_seletor(attr_name, attr_values):
    """Clica no CENTRO do primeiro elemento do dump cujo atributo casa (coordenada via bounds).
    Evita UiObject.click() (bloqueio ~20s, -32002 method=wait) no hot path do banner."""
    dump = get_hierarchy_data()
    if dump is None:
        return False
    for node in dump.iter("node"):
        value = node.attrib.get(attr_name, "")
        if value in attr_values:
            bounds = node.attrib.get("bounds", "")
            nums = list(map(int, re.findall(r"\d+", bounds)))
            if len(nums) != 4:
                continue
            x1, y1, x2, y2 = nums
            cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
            log(f"[LOG] Elemento '{value}' ({attr_name}) -> clique por coordenada ({cx},{cy})")
            try:
                d.click(cx, cy)
                time.sleep(0.3)
                return True
            except Exception as e:
                log(f"[ERRO] Falha ao clicar coordenada em {value}: {e}")
                return False
    return False


def tentar_clicar_botao_vazio(dump_xml):
    """Clica em botão com texto vazio (não focado), usado em alguns anúncios."""
    try:
        btns = [b for b in d.xpath('//android.widget.Button[@text=""]').all()
                if not b.info.get("focusable", True)]
        if btns:
            log("[LOG] Botão com texto vazio encontrado. Clicando...")
            btns[0].click()
            return True
    except Exception:
        pass
    return False

def tentar_clicar_textview_especial(dump_xml):
    if dump_xml is None:
        return False
    for txt in ["Fechar", "AGORA NÃO", "No Thanks", "no thanks", "NO THANKS", "Cancelar"]:
        for node in dump_xml.iter("node"):
            if (node.attrib.get("text", "") == txt and
                    node.attrib.get("class", "") == "android.widget.TextView"):
                log(f"[LOG] TextView '{txt}' encontrado. Clicando...")
                try:
                    d(text=txt, className="android.widget.TextView").click()
                    time.sleep(0.3)
                    return True
                except Exception:
                    pass
    return False

def tentar_clicar_xpath(xpaths):
    for xpath in xpaths:
        try:
            el = d.xpath(xpath)
            if el.exists:
                log(f"[LOG] Elemento via XPath: {xpath}. Clicando...")
                el.click()
                return True
        except Exception:
            pass
    return False

# ==================== IA (servidor central) ====================

def screenshot_bytes():
    """Screenshot em PNG bytes (via uiautomator2). Instrumentado no [PERF] (count/bytes)."""
    png = None
    try:
        img = d.screenshot()
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        png = buf.getvalue()
    except Exception:
        try:
            perf_inc("adb_subprocess_count")
            png = subprocess.check_output(f'adb -s {device} exec-out screencap -p', shell=True, timeout=10)
        except Exception:
            png = None
    if png:
        perf_inc("screenshot_count")
        perf_bytes("screenshot_bytes", len(png))
    return png

def ia_detect_x():
    """Chama servidor IA /detect. Retorna (x,y,confidence) do melhor X ou None. Timeout curto.
    Diagnóstico (sem spam): distingue timeout / http!=200 / json_invalido / sem_deteccao / exception."""
    if not cfg.get("use_ia", True):
        return None
    png = screenshot_bytes()
    if not png:
        flog(f"[FASTCLOSE][IA] device={device} screenshot_null")
        return None
    try:
        _t_ia = time.perf_counter()
        try:
            r = SESS.post(f"{IA_URL}/detect", files={'file': ('shot.png', png, 'image/png')}, timeout=4)
        finally:
            perf_ai_latency((time.perf_counter() - _t_ia) * 1000)
        if r.status_code != 200:
            flog(f"[FASTCLOSE][IA] device={device} http_status={r.status_code}")
            return None
        try:
            data = r.json()
        except Exception as e:
            flog(f"[FASTCLOSE][IA] device={device} json_invalid {type(e).__name__}: {e}")
            return None
        dets = data.get("detections", [])
        if not dets:
            flog(f"[FASTCLOSE][IA] device={device} no_detection")
            return None
        best = dets[0]
        return (best['x'], best['y'], best['confidence'])
    except Exception as e:
        flog(f"[FASTCLOSE][IA] device={device} error={type(e).__name__}: {e}")
        return None

def ia_classify_type():
    """Chama servidor IA /classify. Retorna tipo ('rewarded'/'interstitial'/'playstore'/'playable')."""
    if not cfg.get("use_ia", True):
        return "interstitial"
    png = screenshot_bytes()
    if not png:
        return "interstitial"
    try:
        r = SESS.post(f"{IA_URL}/classify", files={'file': ('shot.png', png, 'image/png')}, timeout=4)
        if r.status_code == 200:
            return r.json().get("type", "interstitial")
    except Exception:
        pass
    return "interstitial"

# ==================== FECHAMENTO DE ANÚNCIO ====================

def achar_botao_fechar_no_dump(dump_xml):
    """Busca botão de fechar no dump XML.
    Retorna (cx, cy) ou None.
    Prioridade dentro do dump:
      1) node clickable com content-desc/text close/fechar/skip/dismiss (mais confiável)
      2) ImageView/TextView no topo (direita ou esquerda)
    """
    if dump_xml is None:
        return None

    w, h = 1080, 2280
    try:
        w, h = d.window_size()
    except Exception:
        pass

    # CTAs que NÃO são fechamento — nunca clicar (abrem Play Store/app)
    CTAS_INVALIDOS = ["google play", "skip google play", "install", "instalar",
                      "play now", "continue", "continuar", "ver mais", "open store",
                      "ir para o app", "concordar com tudo", "aceitar"]

    # 1) node clickable com close/fechar/skip (qualquer posição), excluindo CTAs
    for node in dump_xml.iter("node"):
        desc = node.attrib.get("content-desc", "").lower()
        text = node.attrib.get("text", "").lower()
        combined = desc + "|" + text
        if any(c in combined for c in CTAS_INVALIDOS):
            continue
        if node.attrib.get("clickable", "") == "true" and any(
                k in combined for k in ["close", "fechar", "cerrar", "skip", "dismiss"]):
            bounds = node.attrib.get("bounds", "")
            nums = list(map(int, re.findall(r"\d+", bounds)))
            if len(nums) != 4:
                continue
            x1, y1, x2, y2 = nums
            # Ignora bounds vazios/degenerados (ex: descendentes com [0,0][0,0])
            if x2 - x1 <= 10 or y2 - y1 <= 10:
                continue
            return ((x1 + x2) // 2, (y1 + y2) // 2)

    # 2) ImageView/TextView topo (direita ou esquerda)
    for node in dump_xml.iter("node"):
        classe = node.attrib.get("class", "")
        clickable = node.attrib.get("clickable", "")
        bounds = node.attrib.get("bounds", "")
        text = node.attrib.get("text", "").lower()
        is_valid = (classe == "android.widget.ImageView" or
                    (classe == "android.widget.TextView" and text in ["x", "close", "✕"]))
        if is_valid and clickable == "true" and bounds:
            nums = list(map(int, re.findall(r"\d+", bounds)))
            if len(nums) != 4:
                continue
            x1, y1, x2, y2 = nums
            if x2 - x1 <= 10 or y2 - y1 <= 10:
                continue
            if y2 < h * 0.20 and (x1 > w * 0.75 or x2 < w * 0.25):
                return ((x1 + x2) // 2, (y1 + y2) // 2)
    return None


def voltar_de_externo(d, max_backs=3):
    """Se estiver fora do anúncio/app (Play Store, browser, launcher), pressiona BACK
    para retornar ao anúncio. Retorna True se algo externo foi detectado."""
    try:
        cur = cur_app()
        act = str(cur.get('activity', ''))
        pkg = str(cur.get('package', ''))
    except Exception:
        return False

    is_external = (any(s in act for s in ["vending", "finsky", "HsdpAlias", "play.google"]) or
                   any(s in pkg for s in ["browser", "chrome", "vending", "finsky"]) or
                   'launcher' in act)
    if not is_external:
        return False

    log(f"[LOG] Aberto app externo ({pkg}/{act}). Pressionando BACK para voltar...")
    for _ in range(max_backs):
        d.press("back")
        time.sleep(0.8)
        try:
            cur = cur_app()
            act2 = str(cur.get('activity', ''))
            pkg2 = str(cur.get('package', ''))
            # só considera "voltou" se NÃO estiver mais em loja/browser/launcher
            ainda_externo = (any(s in act2 for s in ["vending", "finsky", "HsdpAlias", "play.google"]) or
                             any(s in pkg2 for s in ["browser", "chrome", "vending", "finsky"]) or
                             'launcher' in act2)
            if not ainda_externo:
                log(f"[LOG] Voltou para: {pkg2}/{act2}")
                return True
        except Exception:
            pass
    # BACK não resolveu (ex.: Chrome não sai com BACK) -> força parada do pacote externo
    log("[LOG] BACK não saiu do app externo. Forçando parada...")
    try:
        d.app_stop(pkg)
        time.sleep(1)
    except Exception:
        pass
    return True


def tocar_para_revelar_x(d, tentativas=2):
    """Alguns anúncios (ex: TikTok) não mostram o X até tocar na tela.
    Toca em pontos aleatórios não-destrutivos e refaz o dump procurando o X.
    Se o toque abrir Play Store/browser, volta com BACK."""
    w, h = 1080, 2280
    try:
        w, h = d.window_size()
    except Exception:
        pass
    for _ in range(tentativas):
        cx = int(w * random.uniform(0.30, 0.70))
        cy = int(h * random.uniform(0.30, 0.70))
        log(f"[LOG] Tocando na tela do anúncio em ({cx},{cy}) para revelar X...")
        d.click(cx, cy)
        time.sleep(1.2)

        # Se o toque abriu Play Store/browser, volta com BACK
        voltar_de_externo(d, max_backs=2)

        dump = get_hierarchy_data()
        pos = achar_botao_fechar_no_dump(dump)
        dump = None
        if pos:
            log(f"[LOG] X revelado após toque em ({pos[0]},{pos[1]}). Clicando...")
            d.click(pos[0], pos[1])
            time.sleep(0.8)
            # Se o clique no X abriu Play Store, volta com BACK
            voltar_de_externo(d, max_backs=2)
            return True
    return False


def cliques_proporcionais_topo(d, tentativas=1):
    """Fallback final: cliques proporcionais no topo (direita e esquerda)."""
    w, h = 1080, 2280
    try:
        w, h = d.window_size()
    except Exception:
        pass
    for _ in range(tentativas):
        for cx, cy in [(int(w*0.95), int(h*0.07)), (int(w*0.06), int(h*0.09)),
                       (int(w*0.93), int(h*0.11)), (int(w*0.08), int(h*0.07)),
                       (int(w*0.94), int(h*0.28))]:
            log(f"[LOG] X proporcional ({cx},{cy})")
            d.click(cx, cy)
            time.sleep(1.0)
    return False


def eh_playable_loja(dump):
    """Detecta playable de loja (AppLovin com 'Install'/'Google Play'), que não tem X
    no dump e só abre a loja ao clicar. Sinal: nodes text 'Install'/'Google Play'
    e nenhum close/skip."""
    if dump is None:
        return False
    tem_install = False
    tem_x = False
    for node in dump.iter("node"):
        t = node.attrib.get("text", "")
        low = (node.attrib.get("resource-id", "") + " " + node.attrib.get("content-desc", "")).lower()
        if t in ("Install", "Instalar", "Google Play", "Play now!"):
            tem_install = True
        if "close" in low or "fechar" in low or "skip" in low or "dismiss" in low:
            tem_x = True
    return tem_install and not tem_x


def unity_ads_com_barra(dump):
    """Detecta anúncio Unity Ads (FullScreenWebViewDisplay) com barra de progresso.
    Estrutura típica: WebView + ids root/loader/main/canvas + ProgressBar,
    com botão 'Google Play' como CTA (não é fechamento!). Nesses anúncios a barra
    de tempo termina e o anúncio fecha sozinho; clicar em 'Google Play'/'Skip Google Play'
    só abre a Play Store."""
    if dump is None:
        return False
    tem_progress = False
    tem_google = False
    for node in dump.iter("node"):
        cls = node.attrib.get("class", "")
        t = node.attrib.get("text", "")
        if "ProgressBar" in cls or "SeekBar" in cls:
            tem_progress = True
        if "Google Play" in t or "Skip Google Play" in t:
            tem_google = True
        # grid de canvas/loader indica playable Unity Ads
        rid = node.attrib.get("resource-id", "")
        if rid in ("root", "loader", "main", "canvas2d", "canvas3d"):
            return tem_progress and tem_google
    return tem_progress and tem_google


# ==================== APPLOVIN (estratégia específica) ====================

def eh_anuncio_applovin(dump=None):
    """Identifica se a tela atual pertence a um anúncio AppLovin (Regra 1).
    Sinais: activity/package com 'applovin', resource-id/content-desc/text com
    'applovin'/'applovin_close_button', ou XML/hierarchy do anúncio."""
    try:
        cur = cur_app()
        act = str(cur.get('activity', ''))
        pkg_ = str(cur.get('package', ''))
    except Exception:
        act = pkg_ = ''
    if 'applovin' in act.lower() or 'applovin' in pkg_.lower():
        return True
    if dump is None:
        try:
            dump = get_hierarchy_data()
        except Exception:
            dump = None
    if dump is not None:
        for node in dump.iter("node"):
            rid = (node.attrib.get("resource-id", "") or "").lower()
            desc = (node.attrib.get("content-desc", "") or "").lower()
            text = (node.attrib.get("text", "") or "").lower()
            if ('applovin' in rid or 'applovin_close_button' in rid or
                    'applovin' in desc or 'applovin' in text):
                return True
    return False


def applovin_confirmar_aberto():
    """Confirma que o anúncio AppLovin ainda está ativo (Regra 2).
    Retorna True se ainda aberto; False se fechou/mudou (cancela clique pendente)."""
    if not eh_anuncio_applovin():
        log("[APPLOVIN] Anúncio não está mais ativo. Cancelando clique pendente.")
        return False
    return True


def fechar_anuncio_applovin(d):
    """Estratégia específica p/ anúncios AppLovin (Regras 1-10).
    - Detecta tipo (interstitial/rewarded) e ajusta janela (30s/80s).
    - A cada ação confirma que o AppLovin segue aberto ANTES de clicar.
    - Procura opções de fechamento reutilizando dump/seletores/IA existentes.
    - Interstitial: após 30s sem botão útil, executa UM clique central único
      e volta a procurar fechamento (sem repetir o centro).
    Retorna True se voltou à MainActivity; False se não resolveu
    (permitindo o fallback do fluxo atual)."""
    INTERSTITIAL_APPLOVIN_TIMEOUT = 30   # gatilho do clique central (interstitial)
    REWARDED_APPLOVIN_TIMEOUT = 80       # janela de fechamento (rewarded)

    applovin_type = "interstitial"
    if cfg.get("use_ia", True):
        applovin_type = ia_classify_type() or "interstitial"
    log(f"[APPLOVIN] Anúncio AppLovin detectado. Tipo: {applovin_type}")

    # Para interstitial: aos 30s faz UM clique central e CONTINUA procurando o botão.
    # Para rewarded: janela total de 80s, sem clique central.
    if applovin_type == "rewarded":
        timeout = REWARDED_APPLOVIN_TIMEOUT
        janela_total = REWARDED_APPLOVIN_TIMEOUT
    else:
        timeout = INTERSTITIAL_APPLOVIN_TIMEOUT   # gatilho do centro
        janela_total = INTERSTITIAL_APPLOVIN_TIMEOUT + 25  # pós-centro segue procurando
    inicio_applovin = time.time()
    clicou_centro_applovin = False   # Regra 4/5/10 (estado por anúncio)
    tentou_canto_applovin = False    # canto sup. esquerdo (botão surge ~14-15s)

    while time.time() - inicio_applovin < janela_total:
        decorrido = time.time() - inicio_applovin

        try:
            cur = cur_app()
            act = str(cur.get('activity', ''))
            pkg_ = str(cur.get('package', ''))
        except Exception:
            time.sleep(0.5)
            continue

        # Voltou ao app -> fechado
        if pkg_ == package and '.MainActivity' in act:
            log("[APPLOVIN] Voltou à MainActivity. Fechamento concluído.")
            return True

        # Loja / browser -> tratamento existente e continua
        if any(s in act for s in ["vending", "finsky", "HsdpAlias"]) or 'play.google' in pkg_:
            tratar_play_store(d)
            continue
        if any(s in pkg_ for s in ["browser", "chrome"]) or 'launcher' in act:
            voltar_de_externo(d, max_backs=2)
            continue

        # AppLovin fechou/mudou de activity -> cancela (Regra 2)
        if not eh_anuncio_applovin():
            log("[APPLOVIN] Anúncio não está mais ativo. Cancelando cliques pendentes.")
            return False

        label = "REWARDED" if applovin_type == "rewarded" else "INTERSTITIAL"
        if label == "REWARDED":
            log(f"[APPLOVIN][REWARDED] Aguardando liberação do fechamento. Decorrido: {decorrido:.1f}s / {timeout}s")

        # ---- procura opções úteis de fechamento (Regra 3/5) ----
        alvo = None
        dump2 = get_hierarchy_data()

        # FAST-PATH ids de fechamento (inclui applovin_close_button)
        for fast_id in ["applovin_close_button", "com.google.android.gms:id/close_button",
                        "inmobi_close_btn", "com.android.vending:id/close_btn", "unityads-btn-close"]:
            try:
                if d(resourceId=fast_id).exists(timeout=0):
                    alvo = ("fast", fast_id)
                    break
            except Exception:
                pass

        # X/Close por posição no dump (já exclui CTAs Install/Google Play)
        if not alvo:
            pos = achar_botao_fechar_no_dump(dump2)
            if pos:
                alvo = ("coord", pos[0], pos[1])

        # Cascata normal (reutiliza helpers) — somente Interstitial (não arriscar reward)
        if not alvo and label == "INTERSTITIAL":
            if not applovin_confirmar_aberto():
                return False
            clicou_cascata = (
                clicar_por_atributo(dump2, "resource-id", SELECTORS, lambda x: d(resourceId=x)) or
                clicar_por_atributo(dump2, "text", TEXTS, lambda x: d(text=x)) or
                clicar_por_atributo(dump2, "content-desc", DESCRIPTIONS, lambda x: d(description=x)) or
                tentar_clicar_xpath(XPATHS_PERSONALIZADOS) or
                tentar_clicar_textview_especial(dump2) or
                tentar_clicar_botao_vazio(dump2)
            )
            try:
                p2, a2 = cur_app().get('package'), str(cur_app().get('activity', ''))
                if p2 == package and '.MainActivity' in a2:
                    log("[APPLOVIN][INTERSTITIAL] Botão de fechamento encontrado antes do timeout.")
                    return True
            except Exception:
                pass
            if clicou_cascata:
                continue

        # IA (reutiliza detecção existente) - threshold baixo p/ capturar o botão
        # assim que ele aparece (o >| costuma demorar a surgir no AppLovin)
        if not alvo:
            hit = ia_detect_x()
            if hit and len(hit) == 3:
                x, y, conf = hit
                try:
                    _, h_ = d.window_size()
                except Exception:
                    h_ = 2280
                if conf >= 0.25 and y < h_ * 0.35:
                    alvo = ("ia", x, y)

        if alvo:
            # Confirma AppLovin ainda aberto ANTES de clicar (Regra 2)
            if not applovin_confirmar_aberto():
                return False
            if alvo[0] == "fast":
                log(f"[APPLOVIN] Close encontrado ({alvo[1]}). Clicando imediatamente.")
                try:
                    d(resourceId=alvo[1]).click()
                except Exception as e:
                    log(f"[APPLOVIN] Erro ao clicar em {alvo[1]}: {e}")
            else:
                x, y = alvo[1], alvo[2]
                log(f"[APPLOVIN] Close encontrado em ({x},{y}). Clicando imediatamente.")
                d.click(x, y)
            time.sleep(1.5)
            try:
                p2, a2 = cur_app().get('package'), str(cur_app().get('activity', ''))
                if p2 == package and '.MainActivity' in a2:
                    log("[APPLOVIN] Fechado (voltou ao app).")
                    return True
            except Exception:
                pass
            continue

        # ---- Janela do botão (9~20s): o botão do AppLovin surge automaticamente
        # entre ~9s e ~18s no canto superior. Polling rotativo: a cada iteração
        # do loop tenta UMA posição da lista (confirmando AppLovin antes do
        # clique) enquanto decorrido estiver dentro da janela.
        if (label == "INTERSTITIAL" and not alvo
                and decorrido >= 9 and decorrido <= 20):
            # posições comprovadas: esquerda (77,209 / 75,181) e direita (997,176)
            cantos_applovin = [(77, 209), (75, 181), (997, 176), (60, 190)]
            idx = int((decorrido - 9) // 3) % len(cantos_applovin)
            cx, cy = cantos_applovin[idx]
            if applovin_confirmar_aberto():
                log(f"[APPLOVIN][INTERSTITIAL] Janela do botão (9~20s). Tentando fechar na posição ({cx},{cy}).")
                d.click(cx, cy)
                time.sleep(1.2)
                try:
                    cur2 = cur_app()
                    if cur2.get('package') == package and '.MainActivity' in str(cur2.get('activity', '')):
                        log(f"[APPLOVIN] Close na posição ({cx},{cy}) fechou. Voltou ao app.")
                        return True
                except Exception:
                    pass
            continue

        # ---- Fallback central (somente Interstitial, UMA vez, após 30s) ----
        if (label == "INTERSTITIAL" and decorrido >= INTERSTITIAL_APPLOVIN_TIMEOUT
                and not clicou_centro_applovin):
            if not applovin_confirmar_aberto():
                return False
            try:
                w_, h_ = d.window_size()
            except Exception:
                w_, h_ = 1080, 2280
            log("[APPLOVIN][INTERSTITIAL] 30s sem botão útil. Confirmando anúncio antes do fallback.")
            log(f"[APPLOVIN][INTERSTITIAL] Executando clique central único em ({w_ // 2},{h_ // 2}).")
            d.click(w_ // 2, h_ // 2)
            clicou_centro_applovin = True   # Regra 4/5: UMA ÚNICA vez
            time.sleep(1.0)
            # POLLING rápido de IA logo após o clique central: o botão costuma
            # aparecer nos ~6s seguintes. Se detectar, clica e verifica retorno.
            try:
                _, h_p = d.window_size()
            except Exception:
                h_p = 2280
            fim_p = time.time() + 8   # janela de captura pós-clique
            while time.time() < fim_p:
                if not applovin_confirmar_aberto():
                    return False
                hit = ia_detect_x()
                if hit and len(hit) == 3:
                    x, y, conf = hit
                    # evita o CTA "Google Play" [719..1008,177..246]
                    gp1x, gp1y, gp2x, gp2y = 719, 177, 1008, 246
                    dentro_cta = (gp1x <= x <= gp2x and gp1y <= y <= gp2y)
                    if conf >= 0.25 and y < h_p * 0.20 and not dentro_cta:
                        log(f"[APPLOVIN] Close encontrado após clique central em ({x},{y}) conf={conf:.2f}. Clicando imediatamente.")
                        d.click(x, y)
                        time.sleep(1.5)
                        try:
                            cur2 = cur_app()
                            if cur2.get('package') == package and '.MainActivity' in str(cur2.get('activity', '')):
                                log("[APPLOVIN] Fechado após clique central + IA.")
                                return True
                        except Exception:
                            pass
                        break
                time.sleep(0.5)
            continue  # volta a procurar Close/Skip (Regra 5)

        # ---- Após clique central: procurar fechamento via IA contínua ----
        # NÃO clicar cegamente nos cantos (podem abrir a Play Store). O botão do
        # AppLovin costuma surgir ~30-50s; usa polling de IA até a janela total.
        if (label == "INTERSTITIAL" and clicou_centro_applovin and not alvo):
            if not applovin_confirmar_aberto():
                return False
            try:
                _, h_p = d.window_size()
            except Exception:
                h_p = 2280
            # polling até a janela total (já começou há 30s)
            while time.time() - inicio_applovin < janela_total:
                if not applovin_confirmar_aberto():
                    return False
                hit = ia_detect_x()
                if hit and len(hit) == 3:
                    x, y, conf = hit
                    gp1x, gp1y, gp2x, gp2y = 719, 177, 1008, 246
                    dentro_cta = (gp1x <= x <= gp2x and gp1y <= y <= gp2y)
                    if conf >= 0.25 and y < h_p * 0.20 and not dentro_cta:
                        log(f"[APPLOVIN] Close encontrado após clique central em ({x},{y}) conf={conf:.2f}. Clicando imediatamente.")
                        d.click(x, y)
                        time.sleep(1.5)
                        try:
                            cur2 = cur_app()
                            if cur2.get('package') == package and '.MainActivity' in str(cur2.get('activity', '')):
                                log("[APPLOVIN] Fechado após clique central + IA.")
                                return True
                        except Exception:
                            pass
                        break
                time.sleep(0.6)
            log(f"[APPLOVIN][INTERSTITIAL] Janela de {janela_total:.0f}s esgotada sem fechamento. Abrindo p/ fallback do fluxo atual.")
            return False

        if label == "INTERSTITIAL":
            log(f"[APPLOVIN][INTERSTITIAL] Procurando fechamento. Decorrido: {decorrido:.1f}s / {timeout}s")
        time.sleep(0.8)

    log(f"[APPLOVIN][{label}] Janela de {janela_total:.0f}s esgotada sem fechamento. Abrindo p/ fallback do fluxo atual.")
    return False


def fechar_anuncio_applovin_legacy(d):
    """Réplica fiel da lógica de fechamento do main_market.py (melhor performance
    em fechamento) adaptada aos helpers da V2 — SEM timing fixo/centro.

    Por ciclo (máx 30 / timeout 100s):
      1) se voltou à MainActivity -> sucesso
      2) FAST-PATH: resource-ids com exists(timeout=0) -> 1 clique
      3) DUMP cascata: resource-id -> text -> content-desc -> xpath -> textview -> botão vazio
         (com pular_textos alternando p/ evitar loop em falsos botões que abrem loja)
      4) X por posição no dump (fechar_anuncio_pelo_x)
      5) IA a cada 3 ciclos (só quando nada clicou)
      6) após qualquer clique, volta de browser/loja com BACK e revalida package
    """ 
    start_time_global = time.time()
    MAX_TIMEOUT_ADS = 100
    pular_textos = False

    def _fechar_x_pelo_dump(dump_xml):
        """Padrão fechar_anuncio_pelo_x do main_market: X clickable topo-direita."""
        if dump_xml is None:
            return False
        try:
            w, h = d.window_size()
        except Exception:
            w, h = 1080, 2400
        min_x = int(w * 0.75)
        max_y = int(h * 0.20)
        for node in dump_xml.iter("node"):
            classe = node.attrib.get("class", "")
            clickable = node.attrib.get("clickable", "")
            bounds = node.attrib.get("bounds", "")
            text = (node.attrib.get("text", "") or "").lower()
            is_valid = (classe == "android.widget.ImageView" or
                        (classe == "android.widget.TextView" and text in ["x", "close"]))
            if is_valid and clickable == "true" and bounds:
                import re as _re
                nums = list(map(int, _re.findall(r"\d+", bounds)))
                if len(nums) == 4:
                    x1, y1, x2, y2 = nums
                    if x1 > min_x and y2 < max_y:
                        cx = (x1 + x2) // 2
                        cy = (y1 + y2) // 2
                        log(f"[LEGACY] Clicando no X em {cx},{cy} (bounds: {bounds})")
                        d.click(cx, cy)
                        time.sleep(0.8)
                        return True
        return False

    for ciclo in range(30):
        tempo_decorrido = time.time() - start_time_global
        if tempo_decorrido > MAX_TIMEOUT_ADS:
            log("[LEGACY] Timeout global 100s em fechamento.")
            break

        # AppLovin não está mais ativo -> cancela (proteção V2)
        if not applovin_confirmar_aberto():
            return False

        try:
            cur = cur_app()
            act = str(cur.get('activity', ''))
            pkg = str(cur.get('package', ''))
        except Exception:
            time.sleep(1)
            continue

        # Voltou ao app -> sucesso
        if pkg == package and '.MainActivity' in act:
            log("[LEGACY][SUCESSO] Voltou à MainActivity.")
            return True

        # Loja/browser -> tratamento existente e continua
        is_playstore = (any(s in act for s in ["vending", "finsky", "HsdpAlias", "play.google"]) or
                        any(s in pkg for s in ["vending", "finsky"]))
        is_browser = (any(s in pkg for s in ["browser", "chrome"]) or 'launcher' in act)
        if is_playstore:
            tratar_play_store(d)
            continue
        if is_browser:
            voltar_de_externo(d, max_backs=2)
            continue

        # FAST-PATH (ids conhecidos, exists timeout=0)
        clicou = False
        for fast_id in ["applovin_close_button", "com.google.android.gms:id/close_button",
                        "inmobi_close_btn", "com.android.vending:id/close_btn",
                        "unityads-btn-close"]:
            try:
                if d(resourceId=fast_id).exists(timeout=0):
                    log(f"[LEGACY][FAST-PATH] Detectado '{fast_id}'. Clicando...")
                    d(resourceId=fast_id).click()
                    time.sleep(0.8)
                    if '.MainActivity' in str(d.app_current().get('activity', '')):
                        return True
                    clicou = True
                    break
            except Exception:
                pass
        if clicou:
            continue

        # FAKE-PATH POSIÇÃO REAL (weditor): o botão do AppLovin fica no canto
        # superior esquerdo (~0.062,0.072 => x=67,y=164; Unity ~0.105,0.096 => 113,219).
        # Tenta 1 posição por iteração, confirmando AppLovin antes do clique.
        if not clicou:
            for cx, cy in [(67, 164), (113, 219), (77, 209), (60, 190)]:
                if not applovin_confirmar_aberto():
                    return False
                log(f"[LEGACY][CANTO] Tentando botão real em ({cx},{cy}).")
                d.click(cx, cy)
                time.sleep(1.2)
                try:
                    if applovin_confirmar_aberto():
                        cur = cur_app()
                        if cur.get('package') == package and '.MainActivity' in str(cur.get('activity', '')):
                            log(f"[LEGACY][CANTO] Fechei em ({cx},{cy}). Voltou ao app.")
                            return True
                except Exception:
                    pass
                clicou = True
                break  # 1 clique por iteração; próximo ciclo tenta a próxima posição

        # DUMP cascata (igual main_market) COM pular_textos
        dump_xml = get_hierarchy_data()
        usou_texto = False

        if not clicou:
            if clicar_por_atributo(dump_xml, "resource-id", SELECTORS, lambda x: d(resourceId=x)):
                clicou = True
            elif not pular_textos and clicar_por_atributo(dump_xml, "text", TEXTS, lambda x: d(text=x)):
                clicou = True
                usou_texto = True
            elif clicar_por_atributo(dump_xml, "content-desc", ["Interstitial close button", "Close", "Fechar"],
                                     lambda x: d(description=x)):
                clicou = True
                usou_texto = True
            elif not pular_textos and tentar_clicar_xpath(XPATHS_PERSONALIZADOS):
                clicou = True
                usou_texto = True
            elif not pular_textos and clicar_por_atributo(dump_xml, "content-desc", DESCRIPTIONS,
                                                          lambda x: d(description=x)):
                clicou = True
                usou_texto = True
            elif not pular_textos and tentar_clicar_textview_especial(dump_xml):
                clicou = True
                usou_texto = True
            elif not pular_textos and tentar_clicar_botao_vazio(dump_xml):
                clicou = True
                usou_texto = True

        # X por posição no dump (padrão main_market)
        if not clicou:
            clicou = _fechar_x_pelo_dump(dump_xml)

        # Alterna pular_textos (evita loop em falsos botões que abrem loja)
        pular_textos = True if usou_texto else False
        dump_xml = None

        # IA a cada 3 ciclos (só quando nada clicou) -> igual main_market
        if not clicou and cfg.get("use_ia", True) and (ciclo % 3 == 0):
            hit = ia_detect_x()
            if hit and len(hit) == 3:
                x, y, conf = hit
                try:
                    _, h_ = d.window_size()
                except Exception:
                    h_ = 2280
                if conf >= 0.30 and y < h_ * 0.30:
                    log(f"[LEGACY][IA] X detectado em ({x},{y}) conf={conf:.2f}. Clicando...")
                    d.click(x, y)
                    time.sleep(1.0)
                    clicou = True

        # Após qualquer clique, se abriu Play Store/browser, volta com BACK
        voltar_de_externo(d, max_backs=2)

        # Verificação de retorno (padrão main_market)
        try:
            if applovin_confirmar_aberto():
                cur = cur_app()
                if cur.get('package') == package and '.MainActivity' in str(cur.get('activity', '')):
                    log("[LEGACY] Voltou para MainActivity. Sucesso.")
                    return True
        except Exception:
            pass

        time.sleep(1)

    log("[LEGACY] Fechamento esgotou ciclos.")
    return False


# ==================== FAST CLOSE WATCHER (AppLovin — P0) ====================
# Controlador ÚNICO do anúncio AppLovin. Observa desde T0 com UM dump por tick,
# decide localmente e só clica com evidência: X confiável nos bounds do próprio
# dump -> d.click(cx, cy). Sem cliques cegos, sem swipes, sem UiObject.click()
# (must_wait de até 20s), sem app_current() por tick, sem IA no loop.
# Rollback: "fast_close_enabled": false (+ "use_legacy_applovin": true).

FC_CLOSED = "CLOSED"      # voltou à MainActivity
FC_TIMEOUT = "TIMEOUT"    # applovin_fast_close_timeout_seconds esgotado
FC_LEFT_AD = "LEFT_AD"    # loja/app externo repetidamente (não voltou ao anúncio)
FC_ERROR = "ERROR"        # dumps falhando seguidamente / exceção no watcher

_applovin_handled = False  # True = o anúncio atual já teve seu controlador AppLovin (sem reentrada)
_fc_net_pool = None        # 1 thread SÓ de rede (POST /classify); nunca acessa o device

# Constantes documentadas (tudo proporcional à tela; nenhuma coordenada fixa)
_FC_CLOSE_RE = re.compile(r"\b(close|dismiss|skip|fechar|cerrar)\b")
_FC_CLOSE_TEXTS = {"close", "fechar", "skip", "skip ad", "cerrar", "dismiss"}  # texto exato; "x" NÃO é prova
_FC_CTA_RE = re.compile(r"\b(install|instalar|download|baixar|get|open|abrir|play|shop|continue|"
                        r"continuar|learn more|saiba mais|ver mais|visit|visitar)\b")
_FC_MAIN_MARKERS = (":id/btnRewardAds",)  # elementos exclusivos da MainActivity
_FC_MIN_SIDE_RATIO = 0.012         # lado mínimo de um X (fração da largura); menor = ruído
_FC_BLOCKER_MAX_AREA = 0.35        # clicável < 35% da tela = botão/CTA -> reveal nunca toca nele
_FC_REVEAL_MARGIN_RATIO = 0.04     # folga (fração da largura) ao redor de botões no reveal
_FC_REVEAL_ALTERNATIVES = ((0.5, 0.55), (0.3, 0.45), (0.7, 0.45), (0.5, 0.35))  # se o ponto do config cair em botão
_FC_APP_CURRENT_MIN_INTERVAL = 1.5  # s — app_current() só em estado ambíguo, no máx. 1x por intervalo
_FC_APP_CURRENT_RECHECK = 5.0       # s — ambíguo já resolvido como applovin vale por este tempo
_FC_BACK_MIN_INTERVAL = 1.0         # s entre BACKs de recuperação (BACK duplo poderia sair do app)
_FC_MAX_EXTERNAL_EVENTS = 3         # loja/app externo mais vezes que isso -> LEFT_AD
_FC_MAX_DUMP_ERRORS = 10
_FC_ARTIFACTS_PER_EVENT = 2         # PNG+XML por tipo de evento por anúncio
_FC_FALSE_CLICK_AFTER = 3           # X ainda visível no N-ésimo clique -> suspeita de falso clique


def flog(msg):
    """Log com milissegundos (instrumentação do fast_close)."""
    now = datetime.now()
    print(f"[{now.strftime('%H:%M:%S')}.{now.microsecond // 1000:03d}] {msg}", flush=True)


class _FastCloseAdState:
    """Estado de UMA instância de anúncio. Criado no início do watcher -> nada vaza
    para o anúncio seguinte."""

    def __init__(self):
        self.x_seen_before = False
        self.x_first_seen_at = None
        self.x_last_seen_at = None
        self.x_visible_since = None     # None = X não visível (ou declarado perdido)
        self.x_miss_count = 0
        self.last_age_log = 0.0
        self.transient_x_mode = False
        self.reveal_attempts = 0
        self.last_reveal_at = None
        self.waiting_after_reveal = False   # WAIT_X_AFTER_REVEAL
        self.reappeared_after_reveal = False
        self.last_action = "none"
        self.last_action_at = None
        self.last_click_at = None
        self.clicks = 0
        self.click_limit_logged = False
        self.external_events = 0
        self.rejects_logged = set()
        self.artifacts = {}
        self.history = deque(maxlen=30)
        self.dump_ms = []
        self.ex = None   # estado do EXPERIMENTO top_control (None = desligado)
        self.flow = None  # fluxo VIDEO -> SKIP -> ENDCARD (None = desligado)


def _fc_bounds(node):
    nums = re.findall(r"-?\d+", node.attrib.get("bounds", ""))
    if len(nums) != 4:
        return None
    x1, y1, x2, y2 = map(int, nums)
    if x2 <= x1 or y2 <= y1:
        return None
    return x1, y1, x2, y2


def _fc_norm(s):
    """camelCase/snake/kebab -> palavras minúsculas (para regex com \\b)."""
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", s or "")
    return re.sub(r"[_\-.:/|]+", " ", s).lower().strip()


def _fc_palavras(node):
    rid = node.attrib.get("resource-id", "").split(":id/")[-1]
    return _fc_norm(" ".join((node.attrib.get("text", ""), node.attrib.get("content-desc", ""), rid)))


def _fc_tem_cta(node, parent, max_w, max_h):
    """CTA no próprio node, nos filhos diretos ou no pai pequeno (wrapper do botão)."""
    if _FC_CTA_RE.search(_fc_palavras(node)):
        return True
    for c in node:
        if _FC_CTA_RE.search(_fc_palavras(c)):
            return True
    p = parent.get(node)
    if p is not None:
        pb = _fc_bounds(p)
        if (pb and pb[2] - pb[0] <= max_w and pb[3] - pb[1] <= max_h
                and _FC_CTA_RE.search(_fc_palavras(p))):
            return True
    return False


def _fc_analisar_dump(root, w, h):
    """Analisa UM dump (sem I/O): estado da tela + candidatos a X + rejeições.
    Estados: main | store | applovin | ambiguous."""
    nodes = list(root.iter("node"))
    parent = {c: p for p in nodes for c in p}
    packages = set()
    main = applovin = app_ids = False
    for n in nodes:
        packages.add(n.attrib.get("package", ""))
        rid = n.attrib.get("resource-id", "")
        if rid.endswith(_FC_MAIN_MARKERS):
            main = True
        if "applovin" in (rid + " " + n.attrib.get("content-desc", "")).lower():
            applovin = True
        elif rid.startswith(package + ":id/"):
            app_ids = True
    if main:
        state = "main"
    elif any("com.android.vending" in p for p in packages):
        state = "store"
    elif package in packages and (applovin or not app_ids):
        # o AppLovin roda dentro do nosso package: ids applovin ou nenhum id próprio do app
        state = "applovin"
    else:
        state = "ambiguous"

    top = h * float(cfg.get("fast_close_top_region_ratio", 0.15))
    max_w = w * float(cfg.get("fast_close_max_width_ratio", 0.20))
    max_h = h * float(cfg.get("fast_close_max_height_ratio", 0.10))
    min_side = w * _FC_MIN_SIDE_RATIO
    cands, rejects = [], []
    for n in nodes:
        if n.attrib.get("package", "") != package:
            continue  # overlays de sistema/loja nunca são o X do anúncio
        rid = n.attrib.get("resource-id", "")
        desc = n.attrib.get("content-desc", "")
        text = n.attrib.get("text", "")
        if _FC_CLOSE_RE.search(_fc_norm(rid.split(":id/")[-1])):
            src, prio = "resource_id", 0
        elif _FC_CLOSE_RE.search(_fc_norm(desc)):
            src, prio = "content_desc", 1
        elif text.strip().lower() in _FC_CLOSE_TEXTS:
            src, prio = "text", 2
        else:
            continue
        b = _fc_bounds(n)
        if b is None:
            continue  # bounds degenerados ([0,0][0,0]) = X escondido
        x1, y1, x2, y2 = b
        bw, bh = x2 - x1, y2 - y1
        info = {"src": src, "prio": prio, "id": rid, "desc": desc, "text": text,
                "bounds": n.attrib.get("bounds", ""), "cx": (x1 + x2) // 2, "cy": (y1 + y2) // 2,
                "area": bw * bh}
        if n.attrib.get("visible-to-user", "true") == "false" or n.attrib.get("enabled", "true") == "false":
            info["reason"] = "not_visible"
        elif bw > max_w or bh > max_h:
            info["reason"] = "oversized"
        elif bw < min_side or bh < min_side:
            info["reason"] = "tiny"
        elif info["cy"] > top:
            info["reason"] = "outside_top_region"
        elif _fc_tem_cta(n, parent, max_w, max_h):
            info["reason"] = "cta"
        if "reason" in info:
            rejects.append(info)
        else:
            cands.append(info)
    cands.sort(key=lambda c: (c["prio"], c["area"]))
    return {"state": state, "nodes": nodes, "cands": cands, "rejects": rejects,
            "best": cands[0] if cands else None}


def _fc_estado_app_current():
    """Fallback CARO (~0.45s, dumpsys). Só para estado ambíguo/confirmação de fechamento."""
    try:
        cur = d.app_current()
        act = str(cur.get('activity', ''))
        pkg_ = str(cur.get('package', ''))
    except Exception:
        return "unknown"
    if pkg_ == package and '.MainActivity' in act:
        return "main"
    if 'vending' in pkg_ or any(s in act for s in ("vending", "finsky", "HsdpAlias")):
        return "store"
    if 'applovin' in act.lower():
        return "applovin"
    if pkg_ == package:
        return "app_other"
    return "external"


def _fc_ponto_reveal(an, w, h):
    """Ponto proporcional para o reveal tap que NÃO caia em botão clicável pequeno,
    CTA ou na faixa do X. Retorna (x, y, origem) ou None se não houver ponto seguro."""
    area_tela = w * h
    m = w * _FC_REVEAL_MARGIN_RATIO
    blockers = []
    for n in an["nodes"]:
        b = _fc_bounds(n)
        if not b:
            continue
        area = (b[2] - b[0]) * (b[3] - b[1])
        botao = n.attrib.get("clickable") == "true" and area < _FC_BLOCKER_MAX_AREA * area_tela
        if botao or _FC_CTA_RE.search(_fc_palavras(n)):
            blockers.append(b)
    top = h * float(cfg.get("fast_close_top_region_ratio", 0.15))
    pontos = [(float(cfg.get("applovin_reveal_tap_x_ratio", 0.5)),
               float(cfg.get("applovin_reveal_tap_y_ratio", 0.45)))] + list(_FC_REVEAL_ALTERNATIVES)
    for i, (rx, ry) in enumerate(pontos):
        x, y = int(w * rx), int(h * ry)
        if y <= top:
            continue
        if any(b[0] - m <= x <= b[2] + m and b[1] - m <= y <= b[3] + m for b in blockers):
            continue
        return x, y, ("config" if i == 0 else f"alternativa_{i}")
    return None


def _fc_salvar_artefato(st, evento, raw_xml=None, prefixo="fastclose"):
    """PNG + XML em errors/ (ANTES de qualquer force-stop/escape). Limitado por evento/anúncio."""
    if not cfg.get("save_errors", True):
        return
    chave = f"{prefixo}_{evento}"
    if st.artifacts.get(chave, 0) >= _FC_ARTIFACTS_PER_EVENT:
        return
    st.artifacts[chave] = st.artifacts.get(chave, 0) + 1
    t = time.perf_counter()
    dev = device.replace(':', '_').replace('.', '_')
    base = os.path.join(ERRORS_DIR, f"{prefixo}_{evento}_{dev}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:-3]}")
    try:
        png = screenshot_bytes()
        if png:
            with open(base + ".png", 'wb') as f:
                f.write(png)
        xml = raw_xml if raw_xml is not None else _dump_hierarchy_medido()
        with open(base + ".xml", 'w', encoding='utf-8') as f:
            f.write(xml)
        flog(f"[FASTCLOSE][ARTIFACT] event={evento} path={base}.png/.xml took={(time.perf_counter() - t) * 1000:.0f}ms")
    except Exception as e:
        flog(f"[FASTCLOSE][ARTIFACT] event={evento} erro={type(e).__name__}: {e}")


def _fc_log_historico(st, motivo):
    flog(f"[FASTCLOSE][HISTORY] reason={motivo} últimos {len(st.history)} ticks:")
    for e in st.history:
        flog("[FASTCLOSE][HIST] " + " ".join(f"{k}={v}" for k, v in e.items()))


def _fc_log_tipo(tipo, motivo, detalhe):
    if tipo == "unknown":
        flog(f"[APPLOVIN][TYPE] unknown reason={motivo}" + (f" detail={detalhe}" if detalhe else ""))
    else:
        flog(f"[APPLOVIN][TYPE] {tipo} source={motivo}")


def _fc_classificar_async():
    """Classifica o anúncio JÁ ABERTO sem bloquear o watcher: screenshot agora (thread
    principal = único acesso ao device) + POST /classify numa thread SÓ de rede.
    Retorna (future|None, tipo_inicial). Falha -> 'unknown' com motivo (nunca 'interstitial')."""
    global _fc_net_pool
    if not cfg.get("use_ia", True):
        return None, ("unknown", "ia_disabled", "")
    t = time.perf_counter()
    png = screenshot_bytes()
    if not png:
        return None, ("unknown", "classification_failed", "screenshot_failed")
    flog(f"[APPLOVIN][TYPE] screenshot para classificação em {(time.perf_counter() - t) * 1000:.0f}ms (POST assíncrono)")

    def _post():
        try:
            _t_ia = time.perf_counter()
            try:
                r = SESS.post(f"{IA_URL}/classify", files={'file': ('shot.png', png, 'image/png')}, timeout=4)
            finally:
                perf_ai_latency((time.perf_counter() - _t_ia) * 1000)
            if r.status_code != 200:
                return ("unknown", "classification_failed", f"http_{r.status_code}")
            tipo = r.json().get("type") or ""
            if not tipo:
                return ("unknown", "classification_failed", "empty_type")
            return (tipo, "ia_classify", "")
        except Exception as e:
            return ("unknown", "classification_failed", type(e).__name__)

    if _fc_net_pool is None:
        _fc_net_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="fastclose-net")
    return _fc_net_pool.submit(_post), ("pending", "", "")


# ---------- EXPERIMENTO top_control (pílula "▶| Google Play") ----------
# Observação SEPARADA do X (não entra em close_candidates). Só roda com
# top_control_experiment_mode != "off". Objetivo: caracterizar o controle, não fechar.

_fc_top_reveal_bloqueado = False  # sessão: reveal superior abriu a loja -> estratégia interrompida


def _fc_b(b):
    return f"[{b[0]},{b[1]}][{b[2]},{b[3]}]"


def _fc_detectar_pilula(an, w, h):
    """Pílula superior do AppLovin: TextView com texto EXATO 'Google Play' do nosso package,
    no topo e pequeno; container = ancestral mais externo que ainda é pequeno (<=40% w, <=12% h)."""
    top = h * float(cfg.get("fast_close_top_region_ratio", 0.15))
    nodes = an["nodes"]
    parent = None
    for n in nodes:
        a = n.attrib
        if (a.get("text") != "Google Play" or a.get("package") != package
                or not a.get("class", "").endswith("TextView")):
            continue
        tb = _fc_bounds(n)
        if not tb or (tb[1] + tb[3]) / 2 > top or tb[2] - tb[0] > 0.4 * w:
            continue
        if parent is None:
            parent = {c: p for p in nodes for c in p}
        pill = tb
        p = parent.get(n)
        while p is not None:
            pb = _fc_bounds(p)
            if not pb or pb[2] - pb[0] > 0.4 * w or pb[3] - pb[1] > 0.12 * h:
                break
            pill = pb
            p = parent.get(p)
        return {"pill": pill, "text": tb, "cx": (pill[0] + pill[2]) // 2, "cy": (pill[1] + pill[3]) // 2}
    return None


def _fc_bloqueadores(an, w, h):
    """Retângulos a evitar: clicáveis pequenos (botões) e qualquer node com palavra de CTA."""
    area_tela = w * h
    blockers = []
    for n in an["nodes"]:
        b = _fc_bounds(n)
        if not b:
            continue
        area = (b[2] - b[0]) * (b[3] - b[1])
        if ((n.attrib.get("clickable") == "true" and area < _FC_BLOCKER_MAX_AREA * area_tela)
                or _FC_CTA_RE.search(_fc_palavras(n))):
            blockers.append(b)
    return blockers


def _fc_ponto_reveal_topo(an, w, h, y, pill_hist):
    """Reveal na FAIXA SUPERIOR (linha da pílula), longe da região histórica da pílula
    e de qualquer botão/CTA conhecido. Nunca desce para o centro do criativo."""
    m = w * _FC_REVEAL_MARGIN_RATIO
    blockers = _fc_bloqueadores(an, w, h)
    for rx in cfg.get("top_reveal_x_ratios", [0.50, 0.65, 0.80, 0.35]):
        x = int(w * float(rx))
        if pill_hist and pill_hist[0] - m <= x <= pill_hist[2] + m:
            continue
        if any(b[0] - m <= x <= b[2] + m and b[1] - m <= y <= b[3] + m for b in blockers):
            continue
        return x, y, float(rx)
    return None


def _fc_assinatura(an):
    """Estrutura do anúncio (sem bounds e sem a pílula) p/ detectar troca de criativo/endcard."""
    return frozenset((n.attrib.get("class", ""), n.attrib.get("resource-id", ""), n.attrib.get("text", ""),
                      n.attrib.get("content-desc", ""))
                     for n in an["nodes"]
                     if n.attrib.get("package") == package and n.attrib.get("text") != "Google Play")


def _fc_ex_novo(modo):
    return {"mode": modo, "pill_seen": False, "pill_since": None, "pill_last_seen": None, "pill_last": None,
            "pill_miss": 0, "pill_lost_at": None, "pill_age_log": 0.0, "origin": "-",
            "reveals": 0, "reveal_at": None, "reveal_waiting": False,
            "control_done": False, "control_pending": False, "control_at": None, "control_sig": None,
            "control_artifact": False, "endcard": False}


def _fc_experimento_topo(st, an, w, h, ts, t0, raw, armed, miss_ticks):
    """Um passo do experimento top_control (estado applovin). Retorna True p/ próximo dump imediato."""
    global _fc_top_reveal_bloqueado
    ex = st.ex
    el = ts - t0
    skip = False
    pil = _fc_detectar_pilula(an, w, h)

    # ---- rastreio da pílula (observação, não X) ----
    if pil:
        ex["pill_last"] = pil
        ex["pill_miss"] = 0
        ex["pill_last_seen"] = ts
        if ex["pill_since"] is None:
            ex["pill_since"] = ts
            ex["pill_seen"] = True
            ex["pill_age_log"] = ts
            desde = f"{(ts - ex['reveal_at']) * 1000:.0f}ms" if ex["reveal_at"] else "-"
            if ex["reveal_waiting"]:
                ex["reveal_waiting"] = False
                ex["origin"] = "reveal"
            else:
                ex["origin"] = "spontaneous"
            flog(f"[FASTCLOSE][TOP_CONTROL] detected bounds={_fc_b(pil['pill'])} text_bounds={_fc_b(pil['text'])} "
                 f"age={el:.3f}s since_last_reveal={desde} origin={ex['origin']} armed={armed}")
            if ex["origin"] == "reveal":
                flog(f"[FASTCLOSE][TOP_CONTROL] appeared_after_reveal={(ts - ex['reveal_at']) * 1000:.0f}ms")
            _fc_salvar_artefato(st, "pill_visible", raw, prefixo="experiment")
        elif ts - ex["pill_age_log"] >= 1.0:
            ex["pill_age_log"] = ts
            flog(f"[FASTCLOSE][TOP_CONTROL] visible_age={ts - ex['pill_since']:.3f}s origin={ex['origin']}")
    elif ex["pill_since"] is not None:
        ex["pill_miss"] += 1
        if ex["pill_miss"] >= miss_ticks:
            dur = ex["pill_last_seen"] - ex["pill_since"]
            flog(f"[FASTCLOSE][TOP_CONTROL] gone visible_duration={dur:.2f}s origin={ex['origin']} "
                 f"age={el:.3f}s bounds={_fc_b(ex['pill_last']['pill'])}")
            ex["pill_since"] = None
            ex["pill_miss"] = 0
            ex["pill_lost_at"] = ts

    # ---- observação pós CONTROL TAP (sem novo clique) ----
    if ex["control_pending"]:
        dt_ms = (ts - ex["control_at"]) * 1000
        if not ex["control_artifact"]:
            ex["control_artifact"] = True
            _fc_salvar_artefato(st, "after_control", raw, prefixo="experiment")
        if an["best"]:
            flog(f"[FASTCLOSE][CONTROL_RESULT] state=x_appeared after={dt_ms:.0f}ms "
                 f"x_bounds={an['best']['bounds']} src={an['best']['src']}")
            _fc_salvar_artefato(st, "x_after_control", raw, prefixo="experiment")
            ex["control_pending"] = False
        else:
            if not ex["endcard"] and _fc_assinatura(an) != ex["control_sig"]:
                ex["endcard"] = True
                flog(f"[FASTCLOSE][CONTROL_OBS] estrutura do anúncio mudou after={dt_ms:.0f}ms")
                _fc_salvar_artefato(st, "endcard_after_control", raw, prefixo="experiment")
            if dt_ms >= float(cfg.get("top_control_observe_seconds", 6.0)) * 1000:
                estado = "new_endcard" if ex["endcard"] else "applovin_continues"
                flog(f"[FASTCLOSE][CONTROL_RESULT] state={estado} after={dt_ms:.0f}ms "
                     f"pill_visible={pil is not None}")
                ex["control_pending"] = False
        return skip

    # ---- CONTROL TAP: UM toque no ÍCONE esquerdo (modo reveal_and_icon), nunca no texto ----
    if (ex["mode"] == "reveal_and_icon" and pil and ex["pill_since"] is not None and not ex["control_done"]
            and not _fc_top_reveal_bloqueado and ts - ex["pill_since"] >= 0.5):
        pb, tb = pil["pill"], pil["text"]
        ix = (pb[0] + tb[0]) // 2 if tb[0] > pb[0] + 10 else pb[0] + int(0.13 * (pb[2] - pb[0]))
        iy = pil["cy"]
        ex["control_sig"] = _fc_assinatura(an)
        flog(f"[FASTCLOSE][CONTROL_TEST] part=icon x={ix} y={iy} pill_bounds={_fc_b(pb)} text_bounds={_fc_b(tb)} "
             f"text_left={tb[0]} pill_age={ts - ex['pill_since']:.2f}s origin={ex['origin']} "
             f"age={el:.2f}s armed={armed}")
        d.click(ix, iy)
        ex["control_done"] = True
        ex["control_pending"] = True
        ex["control_at"] = time.perf_counter()
        st.last_action, st.last_action_at = "control_icon", ex["control_at"]
        return True

    # ---- REVEAL superior: pílula já vista, sumiu, e nada na tela ----
    wait_s = float(cfg.get("top_reveal_wait_seconds", 3.0))
    if ex["reveal_waiting"] and ts - ex["reveal_at"] > wait_s:
        ex["reveal_waiting"] = False
        flog(f"[FASTCLOSE][REVEAL_TOP_NO_CONTROL] waited={ts - ex['reveal_at']:.2f}s attempt={ex['reveals']}")
        _fc_salvar_artefato(st, "reveal_no_control", raw, prefixo="experiment")
    if (not _fc_top_reveal_bloqueado and pil is None and ex["pill_since"] is None and ex["pill_seen"]
            and not an["best"] and not ex["reveal_waiting"] and not _fc_detectar_endcard(an)["confirmed"]
            and ex["reveals"] < int(cfg.get("top_reveal_max_per_ad", 2))
            and ts - ex["pill_lost_at"] >= float(cfg.get("top_reveal_after_lost_seconds", 1.0))
            and (ex["reveal_at"] is None or ts - ex["reveal_at"] >= float(cfg.get("top_reveal_cooldown_seconds", 5.0)))):
        # y = centro vertical REAL da última pílula vista (runtime); x longe da pílula
        y = ex["pill_last"]["cy"] if ex["pill_last"] else int(h * float(cfg.get("top_reveal_y_ratio", 0.075)))
        pt = _fc_ponto_reveal_topo(an, w, h, y, ex["pill_last"]["pill"] if ex["pill_last"] else None)
        if pt:
            _fc_salvar_artefato(st, "before_reveal", raw, prefixo="experiment")
            d.click(pt[0], pt[1])
            ex["reveals"] += 1
            ex["reveal_at"] = time.perf_counter()
            ex["reveal_waiting"] = True
            st.last_action, st.last_action_at = "reveal_top", ex["reveal_at"]
            flog(f"[FASTCLOSE][REVEAL_TOP] x={pt[0]} y={pt[1]} x_ratio={pt[2]:.2f} y_ratio={pt[1] / h:.4f} "
                 f"screen={w}x{h} attempt={ex['reveals']} state_before=pill_absent "
                 f"since_pill_gone={ts - ex['pill_lost_at']:.2f}s age={el:.2f}s source=manual_behavior_hypothesis")
            skip = True
        else:
            ex["reveal_at"] = ts  # respeita cooldown antes de reavaliar
            flog("[FASTCLOSE][REVEAL_TOP_SKIPPED] reason=no_safe_point_in_top_band")
    return skip


def _fc_ex_evento_externo(st, state, ts, raw):
    """Loja/app externo durante o experimento: atribui à última ação experimental."""
    global _fc_top_reveal_bloqueado
    ex = st.ex
    if ex is None:
        return
    if st.last_action == "reveal_top" and not _fc_top_reveal_bloqueado:
        _fc_top_reveal_bloqueado = True
        ex["reveal_waiting"] = False
        flog(f"[FASTCLOSE][REVEAL_TOP_ABORT] reveal superior abriu {state} "
             f"after={(ts - ex['reveal_at']) * 1000:.0f}ms — estratégia interrompida nesta sessão")
        _fc_salvar_artefato(st, "store", raw, prefixo="experiment")
    if ex["control_pending"]:
        flog(f"[FASTCLOSE][CONTROL_RESULT] state=play_store after={(ts - ex['control_at']) * 1000:.0f}ms "
             f"ext_state={state}")
        _fc_salvar_artefato(st, "store", raw, prefixo="experiment")
        ex["control_pending"] = False


def _fc_moedas(an):
    for n in an["nodes"]:
        if n.attrib.get("resource-id", "").endswith(":id/txtMoedas"):
            return n.attrib.get("text", "")
    return "?"


# ---------- Fluxo AppLovin: VIDEO -> TOP REVEAL -> SKIP -> loja -> BACK -> ENDCARD -> X ----------
# Estados (st.flow["phase"]): video | top_control_visible | skipping_to_endcard |
# store_after_skip | endcard | closing_endcard | closed.
# O watcher continua sendo o ÚNICO controlador: reutiliza FUNÇÕES antigas (listas, XPaths,
# achar_botao_fechar_no_dump, ia_detect_x), nunca os controladores legacy/cirúrgico inteiros.

_fc_coins_last = None   # último texto de txtMoedas visto na MainActivity (recompensa)
_fc_banner_falhas = 0   # falhas seguidas do banner (backoff quando não há anúncio disponível)

# X anônimo do endcard (observado: View [958,140][1039,224] + Image [984,169][1013,196] em 1080x2280)
_FC_ECX_MIN_CX = 0.80          # centro da View à direita de 80% da largura
_FC_ECX_SIDE = (0.04, 0.12)    # lado da View em fração da largura (observado 0.075)
_FC_ECX_ASPECT = (0.7, 1.4)    # View ~quadrada
_FC_ECX_IMG_SIDE = (0.015, 0.06)  # lado do Image filho (observado 0.027)
_FC_ECX_IMG_CENTER_TOL = 0.25  # Image centrada na View (fração do lado)
_FC_ENDCARD_TEXTS = {"Interested", "Not interested", "Don't miss out!"}
_FC_SKIP_EFFECT_WINDOW = 3.0   # s para o SKIP produzir endcard/loja antes de SKIP_NO_EFFECT
_FC_ENDCARD_CLICK_COOLDOWN = 1.5  # s entre cliques no X do endcard
_FC_AI_HEALTH_TIMEOUT = 0.5    # s do GET /health antes de usar YOLO no endcard
_FC_OLD_TEXTS = [t for t in TEXTS if t.lower() != "x"]  # "x"/"X" sozinho não é prova de X


def _fc_flow_novo():
    return {"phase": "video", "phase_at": None, "pill_since": None, "pill_last": None, "pill_miss": 0,
            "reveals": 0, "reveal_at": None, "reveal_waiting": False, "reveal_blocked": False,
            "skips": 0, "skip_at": None, "store_back_at": None, "store_after_skip": False,
            "endcard_since": None, "back_from_store_at": None,
            "x_clicks": 0, "x_click_at": None, "x_source": None, "not_found_logged": False,
            "ai_ok": None, "ai_at": None, "ai_invoked_logged": False, "cascade_log_at": 0.0,
            "gate_open": False, "gate_action": None, "reward_wait_log_at": -999.0, "waiting_pill_logged": False,
            "store_entered_at": None, "store_backs": 0, "store_back_pending": False, "store_max_logged": False,
            "cd": _fc_cd_novo(), "pill_windows": [], "skip_elapsed": None,
            "subad_clicks": 0, "subad_click_at": None, "subad_xml_to_click_ms": None,
            "subad_seen": False, "subad_last_auth": None, "subad_log_at": -999.0,
            "subad_pending": False, "subad_last_bounds": None, "subad_last_sig": None,
            "format": None, "format_source": None, "format_meta": {}, "format_at": None,
            "fmt_tries": 1, "fmt_next_at": None,
            "diag_unknown_since": None, "diag_last_log": 0.0, "diag_last_art": 0.0, "diag_sig": None,
            "diag_playable": False, "diag_last_close": None, "diag_last_bucket": -1,
            "diag_close_consec": 0, "diag_clicked": False,
            "diag_close_on_art": False, "diag_pill_art": False,
            "pill_origin": None, "pill_first_seen": None, "pill_source_used": None,
            "detect_to_skip_ms": None, "skip_reason": None,
            "recovery_invoked": False, "recovery_reason": None, "recovery_layers_tried": None,
            "recovery_candidate_source": None, "recovery_click_x": None, "recovery_click_y": None,
            "recovery_confidence": None, "recovery_result": None, "recovery_duration_ms": None,
            "reward_after_recovery": None,
            # classificador VISUAL LAZY (só entra se o FORMAT explícito esgotar as tentativas)
            "visual_classifier_invoked": False, "visual_classifier_reason": None,
            "visual_classifier_at": None, "visual_classifier_ms": None}


def _fc_fase(st, fase, t, extra=""):
    fl = st.flow
    if fl["phase"] != fase:
        fl["phase"], fl["phase_at"] = fase, t
        flog(f"[FASTCLOSE][STATE] {fase}" + (f" {extra}" if extra else ""))


def _fc_detectar_endcard(an):
    """ENDCARD por sinais do dump (não depende de um único texto): id 'endcard', textos de
    feedback/tooltip, Button de CTA. Confirmado = id 'endcard' ou >= 2 sinais distintos."""
    sinais = set()
    for n in an["nodes"]:
        a = n.attrib
        if a.get("package") != package:
            continue
        rid = a.get("resource-id", "")
        txt = a.get("text", "").strip()
        if rid.split(":id/")[-1].lower() == "endcard":
            sinais.add("endcard_id")
        if txt in _FC_ENDCARD_TEXTS:
            sinais.add("feedback_tooltip")
        if a.get("class", "").endswith("Button") and _FC_CTA_RE.search(_fc_norm(txt)):
            sinais.add("cta_button")
    return {"confirmed": "endcard_id" in sinais or len(sinais) >= 2, "signals": sorted(sinais)}


def _fc_find_endcard_close_structural(an, w, h):
    """X ANÔNIMO do endcard: View pequena ~quadrada no canto superior direito, sem texto/desc/id,
    com Image pequena ~centralizada dentro e sem CTA. Chamar SÓ com ENDCARD confirmado."""
    top = h * float(cfg.get("fast_close_top_region_ratio", 0.15))
    best = None
    for n in an["nodes"]:
        a = n.attrib
        if (a.get("package") != package or not a.get("class", "").endswith(".View")
                or a.get("text") or a.get("content-desc") or a.get("resource-id")):
            continue
        b = _fc_bounds(n)
        if not b:
            continue
        vw, vh = b[2] - b[0], b[3] - b[1]
        cx, cy = (b[0] + b[2]) // 2, (b[1] + b[3]) // 2
        if not (_FC_ECX_SIDE[0] * w <= vw <= _FC_ECX_SIDE[1] * w and _FC_ECX_SIDE[0] * w <= vh <= _FC_ECX_SIDE[1] * w):
            continue
        if not (_FC_ECX_ASPECT[0] <= vw / vh <= _FC_ECX_ASPECT[1]) or cx < _FC_ECX_MIN_CX * w or cy > top:
            continue
        img = None
        for m in n.iter("node"):
            if m is n or not m.attrib.get("class", "").endswith(".Image") or m.attrib.get("text") or m.attrib.get("content-desc"):
                continue
            ib = _fc_bounds(m)
            if not ib:
                continue
            iw, ih = ib[2] - ib[0], ib[3] - ib[1]
            if (_FC_ECX_IMG_SIDE[0] * w <= iw <= _FC_ECX_IMG_SIDE[1] * w and _FC_ECX_IMG_SIDE[0] * w <= ih <= _FC_ECX_IMG_SIDE[1] * w
                    and abs((ib[0] + ib[2]) / 2 - cx) <= _FC_ECX_IMG_CENTER_TOL * vw
                    and abs((ib[1] + ib[3]) / 2 - cy) <= _FC_ECX_IMG_CENTER_TOL * vh):
                img = ib
                break
        if not img or any(_FC_CTA_RE.search(_fc_palavras(m)) for m in n.iter("node")):
            continue
        if best is None or cx > best["cx"]:
            best = {"bounds": _fc_b(b), "image": _fc_b(img), "cx": cx, "cy": cy}
    return best


def _fc_alvo_valido(b, an, w, h):
    """Alvo das camadas antigas: não gigante e fora de qualquer CTA conhecido do dump."""
    bw, bh = b[2] - b[0], b[3] - b[1]
    if bw <= 0 or bh <= 0 or bw > float(cfg.get("fast_close_max_width_ratio", 0.20)) * w \
            or bh > float(cfg.get("fast_close_max_height_ratio", 0.10)) * h:
        return False
    cx, cy = (b[0] + b[2]) // 2, (b[1] + b[3]) // 2
    for n in an["nodes"]:
        nb = _fc_bounds(n)
        if nb and _FC_CTA_RE.search(_fc_palavras(n)) and nb[0] <= cx <= nb[2] and nb[1] <= cy <= nb[3]:
            return False
    return True


def _fc_listas_antigas(an, w, h):
    """Camada B: listas antigas (SELECTORS/DESCRIPTIONS/TEXTS) casadas no XML JÁ obtido,
    como clicar_por_atributo, mas retornando bounds (sem UiObject.click / must_wait)."""
    for attr, valores in (("resource-id", SELECTORS), ("content-desc", DESCRIPTIONS), ("text", _FC_OLD_TEXTS)):
        for n in an["nodes"]:
            v = n.attrib.get(attr, "")
            if n.attrib.get("package") == package and v and v in valores:
                b = _fc_bounds(n)
                if b and _fc_alvo_valido(b, an, w, h):
                    return b, f"{attr}={v!r}"
    return None


def _fc_xpath_na_source(raw, an, w, h):
    """Camada B2: XPATHS_PERSONALIZADOS aplicados ao XML JÁ obtido (PageSource), sem novo dump."""
    try:
        from uiautomator2.xpath import PageSource
        src = PageSource.parse(raw)
    except Exception as e:
        flog(f"[FASTCLOSE][ENDCARD_XPATH] indisponível: {type(e).__name__}: {e}")
        return None
    for xp in XPATHS_PERSONALIZADOS:
        try:
            for el in src.find_elements(xp):
                b = tuple(int(v) for v in el.bounds)
                if _fc_alvo_valido(b, an, w, h):
                    return b, xp
        except Exception:
            continue
    return None


def _fc_ia_endcard(st, an, w, h, t):
    """Camada C: YOLO (ia_detect_x) SÓ no ENDCARD, com health check e intervalo; região
    superior direita (onde o X do endcard foi observado). Nunca aplicado ao VIDEO."""
    fl = st.flow
    if not (cfg.get("applovin_endcard_ai_enabled", True) and cfg.get("use_ia", True)) or fl["ai_ok"] is False:
        return None
    if fl["ai_at"] is not None and t - fl["ai_at"] < float(cfg.get("applovin_endcard_ai_interval_s", 1.5)):
        return None
    fl["ai_at"] = t
    if fl["ai_ok"] is None:
        try:
            fl["ai_ok"] = SESS.get(f"{IA_URL}/health", timeout=_FC_AI_HEALTH_TIMEOUT).status_code == 200
        except Exception as e:
            fl["ai_ok"] = False
            flog(f"[FASTCLOSE][ENDCARD_AI] device={device} disabled reason=ia_offline err={type(e).__name__}")
            return None
    t1 = time.perf_counter()
    hit = ia_detect_x()
    ms = (time.perf_counter() - t1) * 1000
    if not hit:
        flog(f"[FASTCLOSE][ENDCARD_AI] device={device} no_detection took={ms:.0f}ms")
        return None
    x, y, conf = hit
    top = h * float(cfg.get("fast_close_top_region_ratio", 0.15))
    min_conf = float(cfg.get("applovin_endcard_ai_min_conf", 0.25))
    reason = None
    if conf < min_conf:
        reason = "confidence"
    elif x < 0.70 * w:
        reason = "x_region"
    elif y > top:
        reason = "y_region"
    elif not _fc_alvo_valido((x - 1, y - 1, x + 1, y + 1), an, w, h):
        reason = "target_validation"
    if reason:
        flog(f"[FASTCLOSE][ENDCARD_AI] device={device} rejected reason={reason} x={x} y={y} conf={conf:.2f} took={ms:.0f}ms "
             f"(exige conf>={min_conf}, x>=0.70w, y<=top, target_valido)")
        return None
    return (x, y, conf, ms)


# ---------- RECOVERY CLOSE CASCADE (pós-timeout, antes do escape) ----------
# Executa SOMENTE quando o fluxo normal não resolveu (timeout/left/error) e o bot está prestes a
# fazer HOME+force-stop. Tenta localizar e clicar um fechamento pelos métodos complementares.
# NUNCA declara reward_completed=True — apenas tenta encerrar a tela de forma normal.

_FC_REC_TEXTS = {t for t in TEXTS + DESCRIPTIONS if t.lower() not in ("x",)}  # sem "x" sozinho
_FC_REC_FORBID = ("install", "instalar", "download", "baixar", "get it", "continue", "continuar",
                  "learn more", "saiba mais", "visit", "visitar", "open", "abrir", "play now",
                  "shop", "google play", "concordar", "aceitar")


def _fc_recovery_anon_top_right(an, w, h):
    """Camada 6 (recovery) — candidato anônimo no topo-direito: View/Image pequeno ~quadrado,
    sem texto/desc/id de CTA, na região superior direita, onscreen. Separação do detector
    estrutural principal (não generaliza o endcard X)."""
    top = h * float(cfg.get("fast_close_top_region_ratio", 0.15))
    min_side = w * _FC_MIN_SIDE_RATIO
    max_side = w * 0.12
    nodes = an["nodes"]
    parent = {c: p for p in nodes for c in p}
    max_w = w * float(cfg.get("fast_close_max_width_ratio", 0.20))
    max_h = h * float(cfg.get("fast_close_max_height_ratio", 0.10))
    for n in nodes:
        a = n.attrib
        if a.get("package") != package:
            continue
        rid = a.get("resource-id", "").split(":id/")[-1]
        txt = a.get("text", "").strip()
        desc = a.get("content-desc", "").strip()
        cls = a.get("class", "").split(".")[-1]
        if cls not in ("View", "Image", "Button", "TextView", "ImageView"):
            continue
        if rid or txt or desc:
            continue  # anônimo: sem id/text/desc
        b = _fc_bounds(n)
        if not b:
            continue
        x1, y1, x2, y2 = b
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        vw, vh = x2 - x1, y2 - y1
        if vw <= 0 or vh <= 0 or vw < min_side or vh < min_side:
            continue
        if vw > max_side or vh > max_side:
            continue
        if not (0.45 <= vw / vh <= 2.2):
            continue
        if cx < 0.75 * w or cy > top:
            continue
        if _fc_tem_cta(n, parent, max_w, max_h):
            continue
        return {"bounds": b, "cx": cx, "cy": cy}
    return None


def _fc_recovery_try_click(cand, an, w, h, motivo):
    """Valida candidato de recuperação (espacial/semântico) e clica UMA vez se ok."""
    if cand is None:
        return None
    b = cand["bounds"]
    bw, bh = b[2] - b[0], b[3] - b[1]
    if bw <= 0 or bh <= 0 or b[2] > w or b[3] > h or b[0] < 0 or b[1] < 0:
        flog(f"[RECOVERY_CLOSE] rejected reason=fora_da_tela motivo={motivo} bounds={_fc_b(b)}")
        return None
    if bw > float(cfg.get("fast_close_max_width_ratio", 0.20)) * w or \
            bh > float(cfg.get("fast_close_max_height_ratio", 0.10)) * h:
        flog(f"[RECOVERY_CLOSE] rejected reason=oversized motivo={motivo} bounds={_fc_b(b)}")
        return None
    if not _fc_alvo_valido(b, an, w, h):
        flog(f"[RECOVERY_CLOSE] rejected reason=target_validation motivo={motivo} bounds={_fc_b(b)}")
        return None
    cx, cy = (b[0] + b[2]) // 2, (b[1] + b[3]) // 2
    t0 = time.perf_counter()
    d.click(cx, cy)
    ms = (time.perf_counter() - t0) * 1000
    flog(f"[RECOVERY_CLOSE][CLICK] motivo={motivo} center=({cx},{cy}) bounds={_fc_b(b)} "
         f"click_rpc={ms:.0f}ms")
    return (cx, cy)


def _fc_recovery_close_cascade(st, an, root, raw, w, h, reason):
    """Cascata de recuperação ANTES do escape. Retorna dict com resultado ou None se EXHAUSTED.
    Camadas: 1 estrutural endcard 2 resource-id 3 content-desc 4 textos 5 XPath
             6 anônimo topo-direito 7 IA/YOLO. Reutiliza o MESMO XML/source do tick."""
    fl = st.flow
    t_inicio = time.perf_counter()
    camadas = []
    alvo = None

    # 1) estrutural fortemente validado (endcard X conhecido)
    if cfg.get("applovin_endcard_structural_x_enabled", True):
        s = _fc_find_endcard_close_structural(an, w, h)
        if s:
            alvo = (s["cx"], s["cy"], s["bounds"])
            camadas.append("structural")
            flog(f"[RECOVERY_CLOSE] candidato estrutural bounds={s['bounds']}")

    # 2) resource-id conhecido
    if alvo is None:
        for n in an["nodes"]:
            rid = n.attrib.get("resource-id", "").split(":id/")[-1]
            if rid in [s.split(":id/")[-1] for s in SELECTORS if s]:
                b = _fc_bounds(n)
                if b and _fc_alvo_valido(b, an, w, h):
                    alvo = ((b[0] + b[2]) // 2, (b[1] + b[3]) // 2, _fc_b(b))
                    camadas.append("resource_id")
                    break

    # 3) content-desc
    if alvo is None:
        for n in an["nodes"]:
            desc = n.attrib.get("content-desc", "").strip()
            if desc and desc in DESCRIPTIONS and not any(f in desc.lower() for f in _FC_REC_FORBID):
                b = _fc_bounds(n)
                if b and _fc_alvo_valido(b, an, w, h):
                    alvo = ((b[0] + b[2]) // 2, (b[1] + b[3]) // 2, _fc_b(b))
                    camadas.append("content_desc")
                    break

    # 4) textos de fechamento (sem "x" sozinho, sem CTA)
    if alvo is None:
        for n in an["nodes"]:
            txt = n.attrib.get("text", "").strip()
            if txt and txt in _FC_REC_TEXTS and not any(f in txt.lower() for f in _FC_REC_FORBID):
                b = _fc_bounds(n)
                if b and _fc_alvo_valido(b, an, w, h):
                    alvo = ((b[0] + b[2]) // 2, (b[1] + b[3]) // 2, _fc_b(b))
                    camadas.append("text")
                    break

    # 5) XPath sobre a source JÁ obtida (sem novo dump)
    if alvo is None:
        r = _fc_xpath_na_source(raw, an, w, h)
        if r:
            alvo = ((r[0][0] + r[0][2]) // 2, (r[0][1] + r[0][3]) // 2, _fc_b(r[0]))
            camadas.append(f"xpath:{r[1]}")

    # 6) anônimo topo-direito (WebView/adFrame)
    if alvo is None:
        anon = _fc_recovery_anon_top_right(an, w, h)
        if anon:
            alvo = (anon["cx"], anon["cy"], _fc_b(anon["bounds"]))
            camadas.append("anonymous_top_right")

    # 7) IA/YOLO como último locator
    if alvo is None and cfg.get("recovery_close_ai_last_enabled", True) and cfg.get("use_ia", True):
        t1 = time.perf_counter()
        hit = ia_detect_x()
        ms = (time.perf_counter() - t1) * 1000
        if hit:
            x, y, conf = hit
            top = h * float(cfg.get("fast_close_top_region_ratio", 0.15))
            min_conf = float(cfg.get("applovin_endcard_ai_min_conf", 0.25))
            aceito = conf >= min_conf and x >= 0.70 * w and y <= top
            if aceito and _fc_alvo_valido((x - 1, y - 1, x + 1, y + 1), an, w, h):
                alvo = (x, y, f"conf={conf:.2f}")
                camadas.append("ai")
            flog(f"[RECOVERY_CLOSE][AI] x={x} y={y} confidence={conf:.2f} accepted={aceito} "
                 f"took={ms:.0f}ms")
        else:
            flog(f"[RECOVERY_CLOSE][AI] no_detection took={ms:.0f}ms")

    if alvo is None:
        _fc_salvar_artefato(st, "recovery_exhausted", raw, prefixo="fastclose")
        info = " + ".join(camadas)
        flog(f"[RECOVERY_CLOSE][EXHAUSTED] layers_tried={info} reason={reason}")
        return {"invoked": True, "reason": reason, "layers_tried": camadas,
                "candidate_source": [], "click_x": None, "click_y": None,
                "result": "exhausted", "duration_ms": int((time.perf_counter() - t_inicio) * 1000)}

    cx, cy, detail = alvo
    cand = {"bounds": (cx - 1, cy - 1, cx + 1, cy + 1)}
    clicado = _fc_recovery_try_click(cand, an, w, h, f"cascade:{camadas[-1]}")
    if clicado is None:
        flog(f"[RECOVERY_CLOSE][EXHAUSTED] candidate={camadas[-1]} rejeitado no filtro reason={reason}")
        _fc_salvar_artefato(st, "recovery_exhausted", raw, prefixo="fastclose")
        return None

    st.last_action = "recovery_close_click"
    st.last_action_at = time.perf_counter()
    res = {
        "invoked": True, "reason": reason, "layers_tried": camadas,
        "candidate_source": camadas[-1:], "click_x": cx, "click_y": cy,
        "result": "clicked", "duration_ms": int((time.perf_counter() - t_inicio) * 1000),
    }
    flog(f"[RECOVERY_CLOSE] clicked layer={camadas[-1]} center=({cx},{cy}) detail={detail}")
    return res


def _fc_recovery_observe(observe_s):
    """Janela curta pós-clique de recovery: segue o estado (Main? Store? Endcard? outros) sem
    novos cliques. Retorna dict com o estado final observado + moedas se chegou a MAIN."""
    flog(f"[RECOVERY_CLOSE][OBSERVE] janela de {observe_s:.1f}s pós-clique (sem novos cliques)...")
    t0 = time.perf_counter()
    ultimo_estado = "?"
    chegou_main = False
    moedas_txt = None
    while time.perf_counter() - t0 < observe_s:
        try:
            dumpr = _dump_hierarchy_medido()
            rootr = ET.fromstring(dumpr)
        except Exception:
            time.sleep(0.3)
            continue
        try:
            anr = _fc_analisar_dump(rootr, 0, 0)
            est = anr["state"] if anr is not None else None
        except Exception:
            est = None
        if est is None:
            est = _fc_estado_app_current()
        if est:
            ultimo_estado = est
        if est == "main":
            chegou_main = True
            try:
                moedas_txt = _fc_moedas({"nodes": list(rootr.iter("node"))})
            except Exception:
                pass
            break
        time.sleep(0.4)
    flog(f"[RECOVERY_CLOSE][OBSERVE] resultado estado={ultimo_estado} chegou_main={chegou_main} "
         f"moedas={moedas_txt!r}")
    return {"ultimo_estado": ultimo_estado, "chegou_main": chegou_main, "moedas_txt": moedas_txt}


def _fc_endcard_close(st, an, root, raw, w, h, t, t0):
    """ENDCARD confirmado: cascata SEM clique cego. 1) X estrutural 2) XML id/desc/text
    3) achar_botao_fechar_no_dump 4) listas antigas 5) XPaths na source 6) YOLO.
    Clica pelos bounds (d.click) e verifica no(s) próximo(s) tick(s)."""
    fl = st.flow
    if fl["phase"] == "closing_endcard":
        if t - fl["x_click_at"] < float(cfg.get("applovin_endcard_click_verify_s", 1.5)):
            return
        flog(f"[FASTCLOSE][ENDCARD_X_CLICK_FAILED] source={fl['x_source']} ainda no endcard "
             f"{(t - fl['x_click_at']) * 1000:.0f}ms após o clique")
        _fc_salvar_artefato(st, "endcard_x_click_failed", raw)
        _fc_fase(st, "endcard", t, "reason=click_failed")
    if fl["x_clicks"] >= int(cfg.get("fast_close_max_close_clicks", 6)):
        return
    if fl["x_click_at"] is not None and t - fl["x_click_at"] < _FC_ENDCARD_CLICK_COOLDOWN:
        return

    alvo = None
    if cfg.get("applovin_endcard_structural_x_enabled", True):
        s = _fc_find_endcard_close_structural(an, w, h)
        if s:
            alvo = ("structural", s["cx"], s["cy"], f"bounds={s['bounds']} image={s['image']}")
    if alvo is None and cfg.get("applovin_endcard_cascade_enabled", True):
        if an["best"]:
            b = an["best"]
            alvo = ("xml", b["cx"], b["cy"], f"bounds={b['bounds']} src={b['src']} id={b['id']}")
        if alvo is None:
            pos = achar_botao_fechar_no_dump(root)
            if pos and _fc_alvo_valido((pos[0] - 1, pos[1] - 1, pos[0] + 1, pos[1] + 1), an, w, h):
                alvo = ("legacy_dump", pos[0], pos[1], "achar_botao_fechar_no_dump")
        if alvo is None:
            r = _fc_listas_antigas(an, w, h)
            if r:
                alvo = ("legacy_lists", (r[0][0] + r[0][2]) // 2, (r[0][1] + r[0][3]) // 2, f"{r[1]} bounds={_fc_b(r[0])}")
        if alvo is None:
            r = _fc_xpath_na_source(raw, an, w, h)
            if r:
                alvo = ("xpath", (r[0][0] + r[0][2]) // 2, (r[0][1] + r[0][3]) // 2, f"{r[1]} bounds={_fc_b(r[0])}")
        # YOLO totalmente LAZY: só se TODAS as camadas locais falharam por grace_s neste endcard
        # (o X surge ~2-3s após o BACK; o endcard pré-loja nunca tem X). Health check só aqui dentro.
        if alvo is None and t - fl["endcard_since"] >= float(cfg.get("applovin_endcard_ai_grace_s", 4.0)):
            if not fl["ai_invoked_logged"]:
                fl["ai_invoked_logged"] = True
                flog(f"[FASTCLOSE][ENDCARD_AI] device={device} invoked reason=all_local_layers_failed "
                     f"endcard_age={t - fl['endcard_since']:.2f}s")
            r = _fc_ia_endcard(st, an, w, h, t)
            if r:
                alvo = ("yolo", r[0], r[1], f"conf={r[2]:.2f} took={r[3]:.0f}ms")

    if alvo is None:
        if not fl["not_found_logged"] and t - fl["endcard_since"] >= float(cfg.get("applovin_endcard_x_not_found_s", 10.0)):
            fl["not_found_logged"] = True
            flog(f"[FASTCLOSE][ENDCARD_X_NOT_FOUND] endcard há {t - fl['endcard_since']:.1f}s sem X em nenhuma camada")
            _fc_log_historico(st, "ENDCARD_X_NOT_FOUND")
            _fc_salvar_artefato(st, "endcard_x_not_found", raw)
        return

    fonte, x, y, det = alvo
    flog(f"[FASTCLOSE][ENDCARD_X_FOUND] source={fonte} {det} center=({x},{y})")
    t_c = time.perf_counter()
    d.click(x, y)  # bounds do dump atual; NUNCA UiObject.click()
    t_c2 = time.perf_counter()
    fl["x_clicks"] += 1
    fl["x_click_at"] = t_c2
    fl["x_source"] = fonte
    st.last_action, st.last_action_at = "endcard_x_click", t_c2
    since_back = f"{(t_c - fl['back_from_store_at']) * 1000:.0f}ms" if fl["back_from_store_at"] else "-"
    flog(f"[FASTCLOSE][ENDCARD_X_CLICK] x={x} y={y} source={fonte} detect_to_click={(t_c - t) * 1000:.0f}ms "
         f"click_rpc={(t_c2 - t_c) * 1000:.0f}ms since_back={since_back} age={t_c - t0:.2f}s n={fl['x_clicks']}")
    _fc_fase(st, "closing_endcard", t_c2)


# ---------- Conclusão da recompensa: COUNTDOWN observado (não tempo) ----------
# Estados (fl["cd"]["state"]): unknown -> candidate -> active -> ending -> completed (| lost).
# CANDIDATO: nó do app com texto só numérico (<= applovin_countdown_max_value), centro na
#   região inferior (y >= applovin_countdown_region_min_y_ratio*h), lados <= max_side_ratio*w,
#   visible-to-user != false. Mesmo XML do tick (sem dump extra).
# CONFIRMAÇÃO (critério exato): 2 transições descendentes CONSECUTIVAS (3 valores distintos),
#   cada queda de 1..3 unidades e cada transição entre 0.5s e 4.0s (contagem ~1/s, tolera tick
#   perdido). Valor repetido não conta (4->4->4 não confirma); subida ou salto incoerente
#   reinicia a contagem de transições.
# FIM: confirmado + ausente por applovin_countdown_miss_ticks ticks seguidos + último valor
#   <= applovin_countdown_end_max_value -> ending. Sumir com valor alto -> lost (sem conclusão).
# COMPLETED: ending + pílula espontânea visível, ou ending + pill_wait_s esgotado (aí o reveal
#   superior passa a ser permitido).
_FC_CD_DROP = (1, 3)
_FC_CD_INTERVAL = (0.5, 4.0)
_FC_CD_CONFIRM_TRANSITIONS = 2
_FC_NUM_RE = re.compile(r"^\d{1,3}$")


def _fc_cd_novo():
    return {"state": "unknown", "bounds": None, "last_value": None, "last_change_at": None,
            "last_seen_at": None, "miss": 0, "desc": 0, "values": [], "first_value": None,
            "confirmed_at": None, "ended_at": None, "completed_source": None, "logged": set()}


def _fc_tl(st, t0, t, evento, extra=""):
    flog(f"[FASTCLOSE][TIMELINE] event={evento} elapsed={t - t0:.1f}s" + (f" {extra}" if extra else ""))


def _fc_detectar_countdown(an, w, h):
    """Candidatos a contagem regressiva no XML do tick (região inferior, pequeno, só dígitos)."""
    min_y = h * float(cfg.get("applovin_countdown_region_min_y_ratio", 0.80))
    max_side = w * float(cfg.get("applovin_countdown_max_side_ratio", 0.12))
    max_v = int(cfg.get("applovin_countdown_max_value", 120))
    cands = []
    for n in an["nodes"]:
        a = n.attrib
        txt = a.get("text", "").strip()
        if a.get("package") != package or not _FC_NUM_RE.match(txt) or int(txt) > max_v:
            continue
        if a.get("visible-to-user", "true") == "false":
            continue
        b = _fc_bounds(n)
        if not b or b[2] - b[0] > max_side or b[3] - b[1] > max_side or (b[1] + b[3]) / 2 < min_y:
            continue
        cands.append({"value": int(txt), "bounds": b})
    return cands


def _fc_countdown_tick(st, an, w, h, t, t0, pill_visible):
    """Atualiza a máquina de conclusão da recompensa neste tick (sem I/O)."""
    cd = st.flow["cd"]
    cands = _fc_detectar_countdown(an, w, h)
    c = None
    if cands:
        if cd["bounds"]:
            ref = cd["bounds"]
            c = min(cands, key=lambda k: abs(k["bounds"][0] - ref[0]) + abs(k["bounds"][1] - ref[1]))
        else:
            c = cands[0]
    if c:
        v = c["value"]
        cd["miss"], cd["last_seen_at"], cd["bounds"] = 0, t, c["bounds"]
        if cd["state"] == "unknown":
            cd["state"] = "candidate"
            _fc_tl(st, t0, t, "countdown_candidate_first", f"value={v} bounds={_fc_b(c['bounds'])}")
        elif cd["state"] in ("ending", "lost"):
            flog(f"[FASTCLOSE][COUNTDOWN] reappeared value={v} prev_state={cd['state']}")
            cd["state"] = "active" if cd["confirmed_at"] else "candidate"
            cd["ended_at"] = None
        if v != cd["last_value"]:
            if cd["last_value"] is not None and cd["last_change_at"] is not None:
                queda, dt = cd["last_value"] - v, t - cd["last_change_at"]
                if _FC_CD_DROP[0] <= queda <= _FC_CD_DROP[1] and _FC_CD_INTERVAL[0] <= dt <= _FC_CD_INTERVAL[1]:
                    cd["desc"] += 1
                else:
                    cd["desc"] = 0
            cd["values"].append(v)
            del cd["values"][:-60]
            cd["last_value"], cd["last_change_at"] = v, t
            if cd["confirmed_at"] is None:
                flog(f"[FASTCLOSE][COUNTDOWN_CANDIDATE] value={v} bounds={_fc_b(c['bounds'])} "
                     f"desc_transitions={cd['desc']} elapsed={t - t0:.1f}s")
                if cd["desc"] >= _FC_CD_CONFIRM_TRANSITIONS:
                    cd["confirmed_at"] = t
                    cd["state"] = "active"
                    seq = cd["values"][-(cd["desc"] + 1):]
                    cd["first_value"] = seq[0]
                    flog(f"[FASTCLOSE][COUNTDOWN] confirmed start_value={seq[0]} sequence={seq} elapsed={t - t0:.1f}s")
                    _fc_tl(st, t0, t, "countdown_confirmed", f"start={seq[0]} sequence={seq}")
            else:
                flog(f"[FASTCLOSE][COUNTDOWN] value={v}")
                _fc_tl(st, t0, t, "countdown", f"value={v}")
    elif cd["last_seen_at"] is not None and cd["state"] in ("candidate", "active"):
        cd["miss"] += 1
        if cd["miss"] >= max(1, int(cfg.get("applovin_countdown_miss_ticks", 3))):
            if cd["state"] == "active":
                if cd["last_value"] is not None and cd["last_value"] <= int(cfg.get("applovin_countdown_end_max_value", 2)):
                    cd["state"], cd["ended_at"] = "ending", t
                    _fc_tl(st, t0, cd["last_seen_at"], "countdown_last_value", f"value={cd['last_value']}")
                    _fc_tl(st, t0, t, "countdown_disappeared", f"miss_ticks={cd['miss']}")
                else:
                    cd["state"] = "lost"
                    flog(f"[FASTCLOSE][COUNTDOWN] lost_early last_value={cd['last_value']} (sem conclusão)")
            else:
                cd["state"], cd["desc"] = "unknown", 0
    if cd["state"] == "ending":
        espera = float(cfg.get("applovin_reward_completion_pill_wait_s", 3.0))
        fonte = ("countdown_finished_and_pill_visible" if pill_visible
                 else "countdown_finished_pill_timeout" if t - cd["ended_at"] >= espera else None)
        if fonte:
            cd["state"], cd["completed_source"] = "completed", fonte
            flog(f"[FASTCLOSE][REWARD_COMPLETION] source=countdown_finished detail={fonte} "
                 f"last_value={cd['last_value']} elapsed={t - t0:.1f}s")
            _fc_tl(st, t0, t, "reward_completed", f"source={fonte}")


_FC_DIAG_ART_INTERVAL_S = 20.0   # durante rewarded sem countdown: artefato PNG+XML a cada N s (diagnóstico)
_FC_DIAG_LOG_INTERVAL_S = 10.0
_FC_DIAG_KEY_TIMES = (20, 40, 60, 80, 100, 120)   # screenshot+XML nesses momentos (item #7)
_FC_CLOSE_IDS_SEMI = ("mv_close_btn", "m-playable-close", "m-playable-skip", "close_button", "btn_close")


def _fc_find_close_btn(an, w, h):
    """Encontra um controle de fechamento de playable no XML: recurso-id semi-conhecido.
    Marca 'onscreen' = bounds efetivamente dentro da área visível da tela (com área > 0) — um
    controle pode existir no XML mas estar OFESCREEN (ex.: mv_close_btn em [0,2280][1080,2280]).
    Retorna None se não houver, SENÃO o dict de atributos (mesmo offscreen, p/ monitorar transição)."""
    for n in an["nodes"]:
        rid = n.attrib.get("resource-id", "")
        rid_c = rid.split(":id/")[-1] if rid else ""
        if rid_c not in _FC_CLOSE_IDS_SEMI:
            continue
        nums = re.findall(r"-?\d+", n.attrib.get("bounds", ""))
        if len(nums) < 4:
            continue
        x1, y1, x2, y2 = map(int, nums[:4])
        if x1 == 0 and y1 == 0 and x2 == 0 and y2 == 0:
            onscreen = False
        else:
            area = (x2 - x1) * (y2 - y1)
            # dentro da tela E com área real > 0 (offscreen típico = [0,H][W,H] => altura 0)
            onscreen = area > 0 and x2 > 0 and y2 > 0 and x1 < w and y1 < h
        b = (x1, y1, x2, y2)
        return {
            "bounds": b, "rid": rid_c, "cls": n.attrib.get("class", ""),
            "visible": n.attrib.get("visible-to-user", "true"),
            "clickable": n.attrib.get("clickable", ""), "enabled": n.attrib.get("enabled", "true"),
            "text": n.attrib.get("text", ""), "desc": n.attrib.get("content-desc", ""),
            "onscreen": onscreen,
        }
    return None


def _fc_diag_rewarded_sem_countdown(st, an, w, h, raw, t, t0, ad_type):
    """Observação pura (NÃO gate) para rewarded/unknown sem countdown observado: loga mudanças
    estruturais, presença/ausência de pílula, monitora mv_close_btn (transição offscreen->onscreen)
    e preserva PNG+XML periódicos para descobrir a classe real (#4/#6). Só roda enquanto o gate
    ainda não abriu (sem SKIP ainda)."""
    fl = st.flow
    if fl is None:
        return
    cd = fl["cd"]
    if not (ad_type == "rewarded" and cd["state"] in ("unknown", "candidate") and fl.get("skips", 0) == 0):
        return
    el = t - t0
    pill = _fc_detectar_pilula(an, w, h)
    close_btn = _fc_find_close_btn(an, w, h)

    # ---- assinatura playable (item #4) ----
    rid_set = {a.attrib.get("resource-id", "").split(":id/")[-1] for a in an["nodes"]}
    mw_frame = any(r in ("MW_PLFRAME", "GameDiv", "Cocos2dGameContainer") for r in rid_set) or \
        any("MW_PLFRAME" in (a.attrib.get("resource-id", "")) for a in an["nodes"])
    if not fl.get("diag_playable"):
        if mw_frame and close_btn is not None and not close_btn["onscreen"] and pill is None:
            fl["diag_playable"] = True
            fl["diag_last_close"] = close_btn
            flog(f"[FASTCLOSE][PLAYABLE_DIAG] signature=rewarded_no_countdown "
                 f"mw_plframe={mw_frame} close_present=True close_bounds={_fc_b(close_btn['bounds'])} "
                 f"close_visible={close_btn['visible']} elapsed={el:.2f}s ids={sorted(rid_set)[:10]}")
    elif fl["diag_playable"]:
        # ---- monitora mv_close_btn: offscreen -> onscreen (item #5) ----
        prev = fl.get("diag_last_close")
        if prev is not None and close_btn is not None:
            prev_on = prev["onscreen"]
            new_on = close_btn["onscreen"]
            mudou = prev_on != new_on or prev["bounds"] != close_btn["bounds"]
            if mudou:
                fl["diag_last_close"] = close_btn
                fl["diag_close_consec"] = 1
                flog(f"[FASTCLOSE][PLAYABLE_CLOSE_TRANSITION] elapsed={el:.2f}s "
                     f"old_bounds={_fc_b(prev['bounds'])} old_onscreen={prev_on} "
                     f"new_bounds={_fc_b(close_btn['bounds'])} new_onscreen={new_on} "
                     f"visible={close_btn['visible']} clickable={close_btn['clickable']} "
                     f"enabled={close_btn['enabled']} text={close_btn['text']!r} desc={close_btn['desc']!r}")
                _fc_salvar_artefato(st, "playable_close_transition", raw, prefixo="fastclose")
            elif new_on and not fl.get("diag_clicked") \
                    and bool(cfg.get("playable_diag_click_enabled", False)):
                # estabilidade onscreen em 2 dumps consecutivos -> clique diagnóstico (item #9)
                fl["diag_close_consec"] = fl.get("diag_close_consec", 0) + 1
                if fl["diag_close_consec"] >= 2:
                    fl["diag_clicked"] = True
                    b = close_btn["bounds"]
                    cx, cy = (b[0] + b[2]) // 2, (b[1] + b[3]) // 2
                    fl["diag_click_at"] = time.perf_counter()
                    flog(f"[FASTCLOSE][PLAYABLE_CLICK] close onscreen estável x={cx} y={cy} "
                         f"bounds={_fc_b(b)} elapsed={el:.2f}s — diagnóstico (gate NÃO alterado)")
                    d.click(cx, cy)
                    st.last_action, st.last_action_at = "diag_playable_close", fl["diag_click_at"]
        elif prev is not None and close_btn is None:
            fl["diag_last_close"] = None
            fl["diag_close_consec"] = 0
            flog(f"[FASTCLOSE][PLAYABLE_CLOSE_TRANSITION] elapsed={el:.2f}s close_btn_sumiu "
                 f"old_bounds={_fc_b(prev['bounds'])}")
        elif close_btn is None:
            fl["diag_close_consec"] = 0

    sig = _fc_assinatura(an)
    if fl.get("diag_sig") != sig:
        mudou = fl.get("diag_sig") is not None
        fl["diag_sig"] = sig
        if mudou:
            logs = [a.attrib.get("resource-id", "").split(":id/")[-1] for a in an["nodes"]
                    if a.attrib.get("package") == package and a.attrib.get("resource-id")]
            flog(f"[FASTCLOSE][DIAG_REWARD] estrutura_mudou elapsed={el:.2f}s pill={pill is not None} "
                 f"n_nodes={len(an['nodes'])} ids={logs[:12]}")
    if fl.get("diag_unknown_since") is None:
        fl["diag_unknown_since"] = el
    if el - fl.get("diag_last_log", 0.0) >= _FC_DIAG_LOG_INTERVAL_S:
        fl["diag_last_log"] = el
        cb = _fc_b(close_btn["bounds"]) if close_btn else "-"
        flog(f"[FASTCLOSE][DIAG_REWARD] rewarded_sem_countdown elapsed={el:.1f}s desde={fl['diag_unknown_since']:.1f}s "
             f"countdown={cd['state']} pill={pill is not None} cands={len(an['cands'])} "
             f"close_btn={cb if close_btn else 'ausente'} "
             f"close_onscreen={close_btn['onscreen'] if close_btn else '-'}")
    # artefatos nos momentos-chave + pílula/endcard/close novo (item #7)
    chave = None
    if el // 20 != fl.get("diag_last_bucket", -1) and any(abs(el - k) <= 2.5 for k in _FC_DIAG_KEY_TIMES):
        chave = f"diag_t{int(round(el // 20) * 20)}"
        fl["diag_last_bucket"] = el // 20
    elif close_btn is not None and close_btn["onscreen"] and not fl.get("diag_close_on_art"):
        fl["diag_close_on_art"] = True
        chave = "playable_close_onscreen"
    elif pill is not None and not fl.get("diag_pill_art"):
        fl["diag_pill_art"] = True
        chave = "playable_pill"
    if chave:
        _fc_salvar_artefato(st, chave, raw, prefixo="fastclose")
    elif el - fl.get("diag_last_art", 0.0) >= _FC_DIAG_ART_INTERVAL_S:
        fl["diag_last_art"] = el
        _fc_salvar_artefato(st, "diag_reward_sem_countdown", raw, prefixo="fastclose")


# ---------- Formato real do anúncio (evento do próprio app / CAS no logcat) ----------
# Prioridade: app_event (AdHelperLog FORMAT=X EVENT=SHOWED) > cas_event (CAS.AI "X Ad Impression").
# Consulta pontual `adb logcat -d` numa thread própria (não toca na UI, não bloqueia o watcher).
_FC_FMT_APP_RE = re.compile(r"^(\d\d-\d\d \d\d:\d\d:\d\d\.\d{3})\s+(\d+)\s+\d+\s+\w\s+AdHelperLog\s*:\s*FORMAT=(\w+)\s+EVENT=SHOWED")
_FC_FMT_CAS_RE = re.compile(r"^(\d\d-\d\d \d\d:\d\d:\d\d\.\d{3})\s+(\d+)\s+\d+\s+\w\s+CAS\.AI\s*:\s*(Interstitial|Rewarded) Ad Impression (\w+)"
                            r".*?CreativeId: ([^ |]+).*?UnitId: ([^ |]+)")
_fc_fmt_pool = None
_fc_fmt_session_perf = None   # perf_counter() no início do anúncio atual (piso p/ o evento SHOWED)


def _fc_parse_formato(texto, agora_str, max_age_s, min_ts=None):
    """Parser puro (testável offline): último SHOWED/impressão com idade <= max_age_s em relação a
    agora_str ('MM-DD HH:MM:SS' do aparelho). Se min_ts (datetime, relógio do aparelho) for dado,
    eventos ANTERIORES ao início do anúncio atual são descartados (evita o SHOWED do anúncio anterior
    dentro da janela). Retorna dict ou None."""
    def _ts(s):
        return datetime.strptime("2000-" + s[:14], "%Y-%m-%d %H:%M:%S")
    agora = _ts(agora_str)
    app = cas = None
    for linha in texto.splitlines():
        m = _FC_FMT_APP_RE.match(linha)
        if m:
            app = {"ts": m.group(1), "pid": m.group(2), "format": m.group(3).lower()}
            continue
        m = _FC_FMT_CAS_RE.match(linha)
        if m:
            cas = {"ts": m.group(1), "pid": m.group(2), "format": m.group(3).lower(), "network": m.group(4),
                   "creative_id": m.group(5), "unit_id": m.group(6)}
    def _valido(item):
        it = _ts(item["ts"])
        return 0 <= (agora - it).total_seconds() <= max_age_s and (min_ts is None or it >= min_ts)
    res = None
    if app and _valido(app):
        res = dict(app, source="app_event")
    elif cas and _valido(cas):
        res = dict(cas, source="cas_event")
    if res and cas and cas["format"] == res["format"] and abs((_ts(cas["ts"]) - _ts(res["ts"])).total_seconds()) <= 5:
        res.update({k: cas[k] for k in ("network", "creative_id", "unit_id")})
    return res


def _fc_ler_formato_logcat():
    """Uma leitura rápida: relógio do aparelho + logcat -d por PID do app. Nunca levanta exceção.
    O filtro por tags (logcat -s) é LENTO neste aparelho (~12s); por PID com --pid é ~0.4s."""
    t0 = time.perf_counter()
    perf_inc("format_lookup_count")
    perf_inc("adb_subprocess_count", 3)   # date + pidof + logcat
    piso = None
    try:
        agora = subprocess.run(["adb", "-s", device, "shell", "date '+%m-%d %H:%M:%S'"],
                               capture_output=True, text=True, timeout=3).stdout.strip()
        dev_now = datetime.strptime("2000-" + agora[:14], "%Y-%m-%d %H:%M:%S")
        # Piso = instante do início do anúncio no relógio do aparelho (evita evento anterior dentro da janela)
        if _fc_fmt_session_perf is not None:
            piso = dev_now - timedelta(seconds=max(0.0, time.perf_counter() - _fc_fmt_session_perf))
        pid_str = ""
        try:
            pid = subprocess.run(["adb", "-s", device, "shell", "pidof com.flixdigital.dramaclassic"],
                                 capture_output=True, text=True, timeout=3).stdout.strip()
            if re.fullmatch(r"\d+", pid):
                pid_str = f"--pid={pid} "
        except Exception:
            pid_str = ""
        out = subprocess.run(["adb", "-s", device, "logcat", "-d", "-v", "threadtime"] + (pid_str.split() if pid_str else ["-s", "AdHelperLog:D", "CAS.AI:I"]),
                             capture_output=True, text=True, timeout=6, encoding="utf-8", errors="ignore").stdout
        r = _fc_parse_formato(out, agora, float(cfg.get("applovin_format_logcat_max_age_s", 30)), piso)
        ms = round((time.perf_counter() - t0) * 1000)
        if isinstance(r, dict):
            r["lookup_ms"] = ms
        perf_sample("format_lookup_ms", ms)
        return r
    except Exception as e:
        ms = round((time.perf_counter() - t0) * 1000)
        perf_sample("format_lookup_ms", ms)
        return {"error": f"{type(e).__name__}", "lookup_ms": ms}


def _fc_formato_async():
    global _fc_fmt_pool
    if not cfg.get("applovin_format_from_logcat", True):
        return None
    # FASE 3: se o persistent logcat estiver ligado e pronto, responde da MEMÓRIA (sem subprocesso).
    if cfg.get("persistent_logcat_enabled", False):
        r = _fc_ensure_logcat_reader()
        if r is not None and getattr(r, "ready", False):
            floor = _fc_fmt_session_perf
            res = r.format_from_memory(floor, float(cfg.get("applovin_format_logcat_max_age_s", 30)))
            perf_inc("format_from_memory_count")
            return _FcDoneFuture(res if res else {"error": "no_recent_event_memory", "source_memory": True})
    if _fc_fmt_pool is None:
        _fc_fmt_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="fastclose-fmt")
    return _fc_fmt_pool.submit(_fc_ler_formato_logcat)


class _FcDoneFuture:
    """Future pré-resolvida (o watcher não muda: usa .done()/.result())."""
    def __init__(self, value):
        self._value = value
    def done(self):
        return True
    def result(self):
        return self._value


_fc_logcat_reader = None


def _fc_ensure_logcat_reader():
    """Liga o leitor contínuo de logcat (FASE 3) uma vez por processo/device. None se indisponível."""
    global _fc_logcat_reader
    if _fc_logcat_reader is False:      # já falhou antes -> não insiste
        return None
    if _fc_logcat_reader is None:
        try:
            import persistent_logcat
            _fc_logcat_reader = persistent_logcat.LogcatReader(
                device, package, pid_check_s=float(cfg.get("persistent_logcat_pid_check_s", 3.0)))
            _fc_logcat_reader.start()
            flog(f"[FASTCLOSE][LOGCAT] reader persistente iniciado (mode=persistent) "
                 f"pid_check={cfg.get('persistent_logcat_pid_check_s', 3.0)}s")
        except Exception as e:
            _fc_logcat_reader = False
            flog(f"[FASTCLOSE][LOGCAT] falha ao iniciar reader: {type(e).__name__}: {e} "
                 f"(degrada para snapshot)")
            return None
    return _fc_logcat_reader


def _fc_reward_safe(ad_type, fonte=None):
    """Reward-safe vale para rewarded/unknown/pending/etc. Interstitial só sai do reward-safe se o
    formato veio de evento explícito (app/CAS) ou se applovin_reward_safe_trust_interstitial=true."""
    if not cfg.get("applovin_reward_safe_mode", True):
        return False
    confiavel = fonte in ("app_event", "cas_event") or cfg.get("applovin_reward_safe_trust_interstitial", False)
    return not (ad_type == "interstitial" and confiavel)


def _fc_gate_motivo(st, ad_type, armed, t, t0):
    """(aberto, motivo) sem efeitos colaterais. Reward-safe + completion_required: SÓ abre com
    REWARD_COMPLETED observado (countdown terminou). Tempo NÃO abre o gate (min_watch = telemetria)."""
    fonte = st.flow.get("format_source") if st.flow is not None else None
    if not _fc_reward_safe(ad_type, fonte):
        if ad_type == "interstitial" and fonte in ("app_event", "cas_event"):
            return True, f"interstitial_confirmed_{fonte}"   # sem reward gate: pílula visível = SKIP
        return armed, ("interstitial_trusted" if armed else "not_armed")
    if cfg.get("applovin_reward_completion_required", True):
        cd = st.flow["cd"]
        if cd["state"] == "completed":
            return True, cd["completed_source"]
        return False, {"active": "countdown_active", "ending": "countdown_ending_wait_pill",
                       "candidate": "countdown_candidate", "lost": "countdown_lost_early"}.get(cd["state"], "reward_not_completed")
    min_w = float(cfg.get("applovin_reward_safe_min_watch_seconds", 60))
    return (t - t0 >= min_w), ("min_watch_elapsed" if t - t0 >= min_w else "min_watch_pending")


def _fc_reward_gate(st, ad_type, armed, t, t0, pill_visible):
    """True = pode avançar (reveal/SKIP). Loga REWARD_WAIT a cada ~5s e a abertura do gate."""
    fl = st.flow
    el = t - t0
    aberto, motivo = _fc_gate_motivo(st, ad_type, armed, t, t0)
    if not aberto:
        if cfg.get("applovin_skip_to_endcard_enabled", False) and t - fl["reward_wait_log_at"] >= 5.0:
            fl["reward_wait_log_at"] = t
            flog(f"[FASTCLOSE][REWARD_WAIT] elapsed={el:.1f}s reason={motivo} countdown_state={fl['cd']['state']} "
                 f"pill_visible={pill_visible}")
        return False
    if not fl["gate_open"]:
        fl["gate_open"] = True
        restante = float(cfg.get("applovin_fast_close_timeout_seconds", 90)) - el
        flog(f"[FASTCLOSE][REWARD_GATE] opened source={motivo} elapsed={el:.1f}s type={ad_type} "
             f"timeout_remaining={restante:.1f}s pill_visible={pill_visible}")
    return True


def _fc_fluxo_applovin(st, an, root, raw, w, h, t, t0, armed, ad_type="unknown"):
    """Um tick do fluxo AppLovin (estado applovin). Retorna (handled, skip_sleep).
    handled=True -> o X genérico/transient NÃO roda neste tick (endcard ou ação tomada)."""
    fl = st.flow
    skip_on = bool(cfg.get("applovin_skip_to_endcard_enabled", False))

    # ---- ENDCARD tem prioridade: primeira ação é DETECTAR X (sem reveal/centro/canto/swipe) ----
    ec = _fc_detectar_endcard(an)
    if ec["confirmed"]:
        if fl["phase"] not in ("endcard", "closing_endcard"):
            fl["endcard_since"] = t
            since = f"{(t - fl['back_from_store_at']) * 1000:.0f}ms" if fl["back_from_store_at"] else "-"
            _fc_fase(st, "endcard", t, f"signals={ec['signals']} since_back={since} age={t - t0:.2f}s")
        fl["reveal_waiting"] = False
        _fc_endcard_close(st, an, root, raw, w, h, t, t0)
        return True, False
    if fl["phase"] == "closing_endcard":
        # endcard sumiu mas ainda AppLovin (transição após o clique): só observar até o verify
        if t - fl["x_click_at"] < float(cfg.get("applovin_endcard_click_verify_s", 1.5)):
            return True, False
        _fc_fase(st, "video", t, "reason=endcard_lost_after_click")

    # ---- pílula TOP_CONTROL (observação separada do X) ----
    pil = _fc_detectar_pilula(an, w, h)
    if pil:
        fl["pill_last"], fl["pill_miss"] = pil, 0
        if fl["pill_since"] is None:
            fl["pill_since"] = t
            fl["pill_windows"].append([round(t - t0, 1), None])
            revelada = fl["reveal_waiting"]
            fl["pill_origin"] = "reveal" if revelada else "spontaneous"
            if fl["pill_first_seen"] is None:
                fl["pill_first_seen"] = round(t - t0, 1)
            _fc_tl(st, t0, t, "first_pill" if len(fl["pill_windows"]) == 1 else "pill_reappeared",
                   f"origin={fl['pill_origin']}")
            desde = f" appeared_after={(t - fl['reveal_at']) * 1000:.0f}ms" if revelada else ""
            _fc_countdown_tick(st, an, w, h, t, t0, pill_visible=not revelada)
            aberto, motivo = _fc_gate_motivo(st, ad_type, armed, t, t0)
            acao = "immediate_skip" if (skip_on and aberto) else "observe"
            flog(f"[FASTCLOSE][TOP_CONTROL] source={fl['pill_origin']}{desde} visible=True format={ad_type} "
                 f"format_source={fl['format_source']} action={acao} bounds={_fc_b(pil['pill'])} "
                 f"text_bounds={_fc_b(pil['text'])} elapsed={t - t0:.1f}s "
                 f"reward_safe={_fc_reward_safe(ad_type, fl['format_source'])} "
                 f"reward_completed={fl['cd']['state'] == 'completed'} skip_allowed={skip_on and aberto} reason={motivo}")
            fl["reveal_waiting"] = False
    elif fl["pill_since"] is not None:
        fl["pill_miss"] += 1
        if fl["pill_miss"] >= max(1, int(cfg.get("transient_x_miss_ticks", 3))):
            flog(f"[FASTCLOSE][TOP_CONTROL] gone visible_duration={t - fl['pill_since']:.2f}s age={t - t0:.3f}s")
            _fc_tl(st, t0, t, "pill_gone", f"visible_duration={t - fl['pill_since']:.1f}s")
            if fl["pill_windows"]:
                fl["pill_windows"][-1][1] = round(t - t0, 1)
            fl["pill_since"], fl["pill_miss"] = None, 0

    # ---- conclusão da recompensa (countdown) no MESMO XML; pílula espontânea conta p/ completed ----
    if not (pil and len(fl["pill_windows"]) and fl["pill_since"] == t):  # já avaliado acima neste tick
        _fc_countdown_tick(st, an, w, h, t, t0,
                           pill_visible=pil is not None and fl["pill_since"] is not None and not fl["reveal_waiting"])

    if fl["phase"] == "skipping_to_endcard":
        if t - fl["skip_at"] < _FC_SKIP_EFFECT_WINDOW:
            return True, False  # aguardando endcard/loja; nenhum toque
        flog(f"[FASTCLOSE][SKIP_NO_EFFECT] {(t - fl['skip_at']) * 1000:.0f}ms sem endcard/loja")

    # ---- REWARD GATE: SKIP (e o reveal para obtê-lo) só depois da janela segura ----
    gate = _fc_reward_gate(st, ad_type, armed, t, t0, pil is not None and fl["pill_since"] is not None)

    if pil and fl["pill_since"] is not None:
        _fc_fase(st, "top_control_visible", t)
        # DETECTAR > AGIR: pílula visível + gate aberto -> SKIP já, com o XML deste tick (sem
        # screenshot/IA/app_current/novo dump/sleep). Única confirmação: estar em 2 dumps seguidos.
        if (skip_on and gate and fl["skips"] < int(cfg.get("applovin_skip_max_per_ad", 2))
                and t - fl["pill_since"] >= float(cfg.get("applovin_skip_min_pill_age_s", 0.25))):
            if fl["gate_action"] is None:
                fl["gate_action"] = "skip_existing_pill"
                flog("[FASTCLOSE][REWARD_GATE] action=skip_existing_pill")
            pb, tb = pil["pill"], pil["text"]
            ix = (pb[0] + tb[0]) // 2 if tb[0] > pb[0] + 10 else pb[0] + int(0.13 * (pb[2] - pb[0]))
            iy = pil["cy"]
            t_c = time.perf_counter()
            d.click(ix, iy)
            fl["skips"] += 1
            fl["skip_at"] = time.perf_counter()
            _, fl["skip_reason"] = _fc_gate_motivo(st, ad_type, armed, t, t0)
            fl["pill_source_used"] = "already_visible" if fl["pill_origin"] == "spontaneous" else "reveal"
            fl["detect_to_skip_ms"] = round((t_c - t) * 1000)
            fl["skip_elapsed"] = round(t - t0, 1)
            st.last_action, st.last_action_at = "skip_control", fl["skip_at"]
            _fc_tl(st, t0, t, "skip", f"gate_action={fl['gate_action']} reason={fl['skip_reason']}")
            flog(f"[FASTCLOSE][SKIP_CONTROL] source={fl['pill_source_used']} detect_to_skip={fl['detect_to_skip_ms']}ms "
                 f"pill_age={t - fl['pill_since']:.2f}s click_rpc={(fl['skip_at'] - t_c) * 1000:.0f}ms "
                 f"format={ad_type} reason={fl['skip_reason']} x={ix} y={iy} pill_bounds={_fc_b(pb)} "
                 f"text_bounds={_fc_b(tb)} age={t - t0:.2f}s n={fl['skips']}")
            _fc_fase(st, "skipping_to_endcard", fl["skip_at"])
            return True, True
        return False, False

    # ---- SUB_AD_CLOSE (WebView sub-ad): close estrutural topo-direito, SEM id/text/desc ----
    # Evidência real: FrameLayout clickable [958,217][1028,287] (center 993,252). DETECÇÃO !=
    # AUTORIZAÇÃO: usa o MESMO gate; rewarded não confirmado apenas OBSERVA (não clica e NÃO
    # cai no reveal). Processado ANTES de qualquer REVEAL_TOP (evita abrir a loja).
    if cfg.get("applovin_subad_close_enabled", True):
        sub = _fc_detectar_subad_close(root, w, h)
        if sub.get("found"):
            # DEDUPE orientado a estado: após clicar, NÃO reclica o MESMO candidato (mesmos bounds)
            # enquanto a UI não transicionar. Libera se o candidato sumir, os bounds mudarem, MAIN/
            # store, ou safety timeout. (Usar a árvore inteira é ruidoso em WebView.)
            _pending = bool(fl.get("subad_pending"))
            _stale = (_pending and fl.get("subad_click_at") is not None
                      and (t - fl["subad_click_at"]) >= float(cfg.get("applovin_subad_close_pending_timeout_s", 8.0)))
            _same = subad_close.dedupe_same(_pending, sub["bounds"], fl.get("subad_last_bounds"), _stale)
            _awaiting = _same
            if _awaiting:
                motivo_auth = "awaiting_transition"
            elif gate:
                motivo_auth = "allowed"
            elif skip_on and _fc_reward_safe(ad_type, fl["format_source"]):
                motivo_auth = "reward_gate"
            else:
                motivo_auth = "not_armed_or_gate"
            autorizado = (not _awaiting) and subad_close.authorize(
                gate, skip_on, fl.get("subad_clicks", 0),
                int(cfg.get("applovin_subad_close_max_per_ad", 2)),
                (fl.get("subad_click_at") is None
                 or t - fl["subad_click_at"] >= float(cfg.get("applovin_subad_close_cooldown_s", 1.0))))
            # log completo da assinatura na 1ª detecção, em mudança de autorização, no click, ou a
            # cada 5s (evita flood de 1 linha/tick).
            if (not fl.get("subad_seen")) or fl.get("subad_last_auth") != motivo_auth or autorizado \
                    or (t - float(fl.get("subad_log_at", -999))) >= 5.0:
                flog("[FASTCLOSE][SUBAD_CLOSE] found=True action=%s reason=%s bounds=%s norm=%s clickable=%s "
                     "child_same_bounds=%s webview_context=%s authorization=%s" % (
                         "click" if autorizado else "observe",
                         ("awaiting_transition" if _awaiting else sub["reason"]), _fc_b(sub["bounds"]), sub["norm"],
                         sub["clickable"], sub["child_same_bounds"], sub["webview_context"], motivo_auth))
                fl["subad_log_at"] = t
            fl["subad_seen"] = True
            fl["subad_last_auth"] = motivo_auth
            if autorizado:
                cx, cy = sub["center"]
                t_c = time.perf_counter()
                d.click(cx, cy)
                fl["subad_clicks"] = fl.get("subad_clicks", 0) + 1
                fl["subad_click_at"] = t_c
                fl["skip_at"] = t_c            # MESMA fase de skip: evita t - None depois
                fl["subad_pending"] = True
                fl["subad_last_bounds"] = sub["bounds"]
                fl["subad_xml_to_click_ms"] = round((t_c - t) * 1000)
                st.last_action, st.last_action_at = "subad_close", fl["subad_click_at"]
                _fc_tl(st, t0, t, "subad_close", f"center={sub['center']} layer=structural")
                flog("[FASTCLOSE][SUBAD_CLOSE_CLICK] center=%s click_rpc=%.0fms xml_candidate_to_click_ms=%d "
                     "format=%s age=%.2fs n=%d" % (
                         sub["center"], (fl["subad_click_at"] - t_c) * 1000, fl["subad_xml_to_click_ms"],
                         ad_type, t - t0, fl["subad_clicks"]))
                _fc_fase(st, "skipping_to_endcard", fl["subad_click_at"])
                return True, True
            # detectado mas NÃO autorizado (gate/awaiting): não clicar e NÃO tentar reveal
            return True, False
        else:
            # candidato desapareceu -> libera um novo clique (transição ocorreu)
            if fl.get("subad_pending"):
                fl["subad_pending"] = False
                fl["subad_last_bounds"] = None
                fl["subad_last_sig"] = None

    _fc_fase(st, "video", t)
    # ---- TOP REVEAL (validado 4/4): só com skip ligado, ARMED, sem pílula/X/endcard ----
    wait_s = float(cfg.get("top_reveal_wait_seconds", 3.0))
    if fl["reveal_waiting"] and t - fl["reveal_at"] > wait_s:
        fl["reveal_waiting"] = False
        flog(f"[FASTCLOSE][REVEAL_TOP_NO_CONTROL] waited={t - fl['reveal_at']:.2f}s attempt={fl['reveals']}")
    # Condições VALIDADAS (4/4 seguro): a pílula já apareceu neste anúncio e sumiu. Antes disso
    # (ex.: criativo com contagem regressiva) um toque no topo é clique no anúncio -> loja.
    # Reveal: (a) condição validada = pílula já vista neste anúncio, ou (b) conclusão da
    # recompensa observada (countdown terminou e a pílula não veio em pill_wait_s).
    # (c) interstitial CONFIRMADO por evento: reveal só como RECUPERAÇÃO, depois de uma janela sem
    # pílula espontânea (observadas em T+10..18s); a espontânea sempre tem prioridade.
    concluido = fl["cd"]["state"] == "completed"
    inter_ok = (ad_type == "interstitial" and fl["format_source"] in ("app_event", "cas_event")
                and t - t0 >= float(cfg.get("applovin_interstitial_control_wait_s", 20.0)))
    if (skip_on and gate and fl["pill_last"] is None and not concluido and not inter_ok
            and ad_type != "interstitial" and not fl["waiting_pill_logged"]):
        fl["waiting_pill_logged"] = True
        flog(f"[FASTCLOSE][REWARD_GATE] waiting_pill reason=pill_never_seen (reveal antes da 1ª pílula "
             f"abriu a loja no criativo com contagem) age={t - t0:.2f}s")
    if (skip_on and gate and not an["best"] and not fl["reveal_waiting"]
            and (fl["pill_last"] is not None or concluido or inter_ok) and fl["pill_since"] is None and not fl["reveal_blocked"]
            and fl["reveals"] < int(cfg.get("top_reveal_max_per_ad", 2))
            and fl["skips"] < int(cfg.get("applovin_skip_max_per_ad", 2))
            and (fl["reveal_at"] is None or t - fl["reveal_at"] >= float(cfg.get("top_reveal_cooldown_seconds", 5.0)))):
        y = fl["pill_last"]["cy"] if fl["pill_last"] else int(h * float(cfg.get("top_reveal_y_ratio", 0.075)))
        pt = _fc_ponto_reveal_topo(an, w, h, y, fl["pill_last"]["pill"] if fl["pill_last"] else None)
        if pt:
            d.click(pt[0], pt[1])
            fl["reveals"] += 1
            fl["reveal_at"] = time.perf_counter()
            fl["reveal_waiting"] = True
            st.last_action, st.last_action_at = "reveal_top", fl["reveal_at"]
            if fl["gate_action"] is None:
                fl["gate_action"] = "reveal_then_skip"
                flog("[FASTCLOSE][REWARD_GATE] action=reveal_then_skip")
            origem = "pill_seen_before" if fl["pill_last"] is not None else ("countdown_completed" if concluido else "interstitial_wait_timeout")
            flog(f"[FASTCLOSE][REVEAL_TOP] x={pt[0]} y={pt[1]} x_ratio={pt[2]:.2f} y_ratio={pt[1] / h:.4f} "
                 f"screen={w}x{h} attempt={fl['reveals']} age={t - t0:.2f}s trigger={origem} "
                 f"format={ad_type} format_source={fl['format_source']} pill_seen_before_reveal={fl['pill_last'] is not None}")
            return True, True
        fl["reveal_at"] = t
        flog("[FASTCLOSE][REVEAL_TOP_SKIPPED] reason=no_safe_point_in_top_band")
    return False, False


def _fc_coins_int(txt):
    m = re.search(r"-?\d+", txt or "")
    return int(m.group()) if m else None


def _fc_registrar_moedas(txt):
    global _fc_coins_last
    if txt and txt != "?":
        _fc_coins_last = txt


_FC_REWARD_POLL_MAX_S = 3.0      # verificação curta e limitada do saldo pós-fechamento
_FC_REWARD_POLL_INTERVAL_S = 0.4


def _fc_log_reward(before_txt):
    """Mede recompensa (não altera lógica): lê txtMoedas na MainActivity por até 3s
    (a UI pode atualizar com atraso); para assim que o saldo mudar."""
    antes = _fc_coins_int(before_txt)
    depois_txt = _fc_coins_last
    t0 = time.perf_counter()
    update_ms = None
    leituras = 0
    while True:
        dump = get_hierarchy_data()
        leituras += 1
        if dump is not None:
            txt = _fc_moedas({"nodes": list(dump.iter("node"))})
            if txt != "?":
                _fc_registrar_moedas(txt)
                depois_txt = txt
        depois = _fc_coins_int(depois_txt)
        if antes is not None and depois is not None and depois != antes:
            update_ms = (time.perf_counter() - t0) * 1000
            break
        if time.perf_counter() - t0 >= _FC_REWARD_POLL_MAX_S:
            break
        time.sleep(_FC_REWARD_POLL_INTERVAL_S)
    depois = _fc_coins_int(depois_txt)
    delta = (depois - antes) if (antes is not None and depois is not None) else "?"
    upd = f"{update_ms:.0f}ms" if update_ms is not None else f"none(waited {(time.perf_counter() - t0) * 1000:.0f}ms)"
    flog(f"[FASTCLOSE][REWARD] before={antes} after={depois} delta={delta} update_ms={upd} reads={leituras}")


def _fc_eventos_pid(t0_perf):
    """Instrumentação pura (item #2): consulta logcat --pid do app e devolve timestamps (relógio do
    aparelho) de CLICKED / REWARD_EARNED / DISMISSED do anúncio ATUAL. Filtra eventos anteriores ao
    início da sessão (piso device-clock) para não associar REWARD_EARNED/CLICKED de anúncio antigo
    ao anúncio atual. NÃO altera o gate."""
    try:
        pid = subprocess.run(["adb", "-s", device, "shell", "pidof com.flixdigital.dramaclassic"],
                             capture_output=True, text=True, timeout=3).stdout.strip()
        if not re.fullmatch(r"\d+", pid):
            return {}
        agora = subprocess.run(["adb", "-s", device, "shell", "date '+%m-%d %H:%M:%S'"],
                               capture_output=True, text=True, timeout=3).stdout.strip()
        out = subprocess.run(["adb", "-s", device, "logcat", "-d", "--pid=" + pid, "-v", "threadtime"],
                             capture_output=True, text=True, timeout=6, encoding="utf-8", errors="ignore").stdout
    except Exception as e:
        return {"error": type(e).__name__}
    perf_inc("adb_subprocess_count", 3)   # pidof + date + logcat
    piso = None
    if _fc_fmt_session_perf is not None and agora:
        try:
            dev_now = datetime.strptime("2000-" + agora[:14], "%Y-%m-%d %H:%M:%S")
            piso = dev_now - timedelta(seconds=max(0.0, time.perf_counter() - _fc_fmt_session_perf))
        except Exception:
            piso = None
    eventos = {}
    for ev in ("CLICKED", "REWARD_EARNED", "DISMISSED"):
        for linha in reversed(out.splitlines()):
            m = re.match(r"^(\d\d-\d\d \d\d:\d\d:\d\d\.\d{3}).*EVENT=" + ev, linha)
            if m:
                if piso is not None:
                    try:
                        ev_ts = datetime.strptime("2000-" + m.group(1)[:14], "%Y-%m-%d %H:%M:%S")
                        if ev_ts < piso:
                            break  # evento mais recente deste tipo já é anterior ao anúncio atual
                    except Exception:
                        pass
                eventos[ev] = m.group(1)
                break
    return eventos


def _fc_dormir(tick_start, tick_s):
    """Dorme só o restante do período-alvo (dump lento -> não dorme)."""
    resto = tick_s - (time.perf_counter() - tick_start)
    if resto > 0:
        time.sleep(resto)


def _fc_tick_eff(st, ad_type, tick_s_fixed, burst=None):
    """Tick efetivo. Se `adaptive_tick_enabled=false` -> tick fixo (comportamento atual).
    Se true -> período por ESTADO do anúncio (não usa CPU/delay do host)."""
    if not cfg.get("adaptive_tick_enabled", False):
        return tick_s_fixed
    try:
        import adaptive_tick as AT
        fl = st.flow
        phase = fl["phase"] if fl else None
        cd = fl["cd"]["state"] if fl else None
        pill = bool(fl and fl.get("pill_since") is not None)
        rec = bool(fl and fl.get("recovery_invoked"))
        key = AT.state_key(phase=phase, ad_type=ad_type, cd_state=cd,
                           pill_visible=pill, in_recovery=rec)
        now = time.perf_counter()
        ms = burst.effective_ms(key, now, cfg) if burst is not None else AT.tick_ms_for(key, cfg)
        perf_inc("ticks_total")
        try:
            _perf_ticks_by_state[key] = _perf_ticks_by_state.get(key, 0) + 1
        except Exception:
            pass
        perf_sample("requested_tick_ms", ms)
        return max(0.05, ms / 1000.0)
    except Exception:
        return tick_s_fixed


def fast_close_watcher_applovin(tipo_future=None, tipo_inicial=("unknown", "", ""), fmt_future=None):
    """Fast Close Watcher (single-thread). Máquina de estados por anúncio:

      OBSERVING -> X_VISIBLE -> (ARMED) CLICK -> CLOSED (próximo tick vê a MainActivity)
      X_VISIBLE -> X_MISS x N -> X_LOST -> (duração em [min,max]) TRANSIENT
      TRANSIENT + ARMED + X ausente -> REVEAL (toque seguro) -> WAIT_X_AFTER_REVEAL
      WAIT_X_AFTER_REVEAL -> X_REAPPEARED -> CLICK   |   expira -> REVEAL_NO_X (até max_reveals)

    ARMED: interstitial desde T0; rewarded/unknown/pending só após
    applovin_rewarded_min_watch_seconds. Retorna FC_CLOSED/FC_TIMEOUT/FC_LEFT_AD/FC_ERROR."""
    tick_s = max(0.05, float(cfg.get("fast_close_tick_ms", 300)) / 1000.0)
    timeout = float(cfg.get("applovin_fast_close_timeout_seconds", 90))
    timeout_limite = timeout  # limite efetivo (0 = normal); diag playable estende para observação
    diag_timeout = float(cfg.get("rewarded_no_countdown_diag_timeout_s", 0) or 0)
    rewarded_min = float(cfg.get("applovin_rewarded_min_watch_seconds", 30))
    click_cd = float(cfg.get("fast_close_click_cooldown_ms", 700)) / 1000.0
    max_clicks = int(cfg.get("fast_close_max_close_clicks", 6))
    tr_on = bool(cfg.get("transient_x_enabled", True))
    miss_ticks = max(1, int(cfg.get("transient_x_miss_ticks", 3)))
    tr_min = float(cfg.get("transient_x_min_visible_seconds", 2.0))
    tr_max = float(cfg.get("transient_x_max_visible_seconds", 10.0))
    max_reveals = int(cfg.get("transient_x_max_reveals", 2))
    reveal_wait = float(cfg.get("transient_x_reveal_wait_seconds", 2.5))
    reveal_cd = float(cfg.get("transient_x_reveal_cooldown_seconds", 2.0))
    log_ticks = bool(cfg.get("fast_close_log_every_tick", True))
    burst = None
    if cfg.get("adaptive_tick_enabled", False):
        try:
            import adaptive_tick as AT
            burst = AT.BurstController(cfg)
            flog(f"[FASTCLOSE][TICK] adaptive_tick enabled burst_ms={burst.burst_ms} "
                 f"burst_s={burst.burst_s}")
        except Exception as e:
            burst = None
            flog(f"[FASTCLOSE][TICK] adaptive_tick indisponível: {type(e).__name__}: {e}")

    try:
        w, h = d.window_size()  # 1x por anúncio (cache)
    except Exception as e:
        w = h = None
        flog(f"[FASTCLOSE][WARN] window_size falhou ({type(e).__name__}); usando bounds do 1º dump.")

    ad_type, type_reason, type_detail = tipo_inicial
    st = _FastCloseAdState()
    t0 = time.perf_counter()
    global _fc_fmt_session_perf
    _fc_fmt_session_perf = t0  # piso p/ o evento SHOWED do anúncio atual (evita leaks da janela)
    armed = False
    n = 0
    dump_errors = 0
    last_ac = -999.0
    sticky_applovin = False
    main_marker_unreliable = False
    last_back = -999.0
    prev_state = None
    result = None
    last_dump = None   # (raw, root, an, w, h) do último tick processado — p/ recovery pós-loop
    recovery_res = None
    flog(f"[FASTCLOSE][START] device={device} type={ad_type} tick={tick_s * 1000:.0f}ms "
         f"timeout={timeout:.0f}s screen={w}x{h}")
    _hb_write("RUNNING", "fastclose", force=True)
    ex_mode = str(cfg.get("top_control_experiment_mode", "off"))
    if ex_mode in ("reveal_only", "reveal_and_icon"):
        st.ex = _fc_ex_novo(ex_mode)
        flog(f"[FASTCLOSE][EXPERIMENT] top_control_test=True mode={ex_mode} "
             f"session_reveal_blocked={_fc_top_reveal_bloqueado}")
    skip_on = bool(cfg.get("applovin_skip_to_endcard_enabled", False))
    if (skip_on or cfg.get("applovin_endcard_structural_x_enabled", True)
            or cfg.get("applovin_endcard_cascade_enabled", True)):
        st.flow = _fc_flow_novo()
        st.flow["phase_at"] = t0
        if skip_on and st.ex is not None:
            st.ex = None
            flog("[FASTCLOSE][EXPERIMENT] ignorado: applovin_skip_to_endcard_enabled assume a pílula")
        flog(f"[FASTCLOSE][FLOW] skip_to_endcard={skip_on} "
             f"structural_x={bool(cfg.get('applovin_endcard_structural_x_enabled', True))} "
             f"cascade={bool(cfg.get('applovin_endcard_cascade_enabled', True))} "
             f"endcard_ai={bool(cfg.get('applovin_endcard_ai_enabled', True))} coins_before={_fc_coins_last!r}")
        if skip_on:
            flog(f"[FASTCLOSE][REWARD_SAFE] enabled={bool(cfg.get('applovin_reward_safe_mode', True))} "
                 f"min_watch={float(cfg.get('applovin_reward_safe_min_watch_seconds', 60)):.0f}s "
                 f"trust_interstitial={bool(cfg.get('applovin_reward_safe_trust_interstitial', False))}")
        flog("[FASTCLOSE][STATE] video")

    try:
        while True:
            ts = time.perf_counter()
            el = ts - t0
            # DIAGNÓSTICO playable reward: estende o limite de OBSERVAÇÃO quando o rewarded está
            # sem countdown/pílula (nunca libera SKIP por tempo). Limite normal volta se o coaxar
            # ganhar countdown ou se o gate abrir.
            if (diag_timeout > timeout and st.flow is not None and skip_on
                    and st.flow["cd"]["state"] in ("unknown", "candidate")
                    and st.flow.get("skips", 0) == 0 and ad_type == "rewarded"):
                if timeout_limite == timeout:
                    timeout_limite = diag_timeout
                    flog(f"[FASTCLOSE][DIAG_PLAYABLE] rewarded sem countdown: limite de observação "
                         f"estendido de {timeout:.0f}s -> {diag_timeout:.0f}s (sem SKIP por tempo)")
            elif timeout_limite > timeout:
                timeout_limite = timeout
                flog(f"[FASTCLOSE][DIAG_PLAYABLE] volta ao timeout normal={timeout:.0f}s (countdown/gate ativo)")
            if el > timeout_limite:
                result = FC_TIMEOUT
                break
            n += 1
            skip_sleep = False

            # Formato real (evento do app/CAS no logcat) — autoridade sobre o classificador visual
            if fmt_future is not None and fmt_future.done() and st.flow is not None:
                r = fmt_future.result()
                fmt_future = None
                fl0 = st.flow
                if r and "format" in r:
                    ad_type = r["format"]
                    fl0["format"], fl0["format_source"] = r["format"], r["source"]
                    _set_last_format(r["format"])
                    fl0["format_meta"] = {k: r.get(k) for k in ("network", "creative_id", "unit_id", "pid", "ts")}
                    fl0["format_at"] = time.perf_counter()
                    flog(f"[APPLOVIN][FORMAT] {r['format']} source={r['source']} network={r.get('network')} "
                         f"creative_id={r.get('creative_id')} unit_id={r.get('unit_id')} event_ts={r.get('ts')} "
                         f"lookup_ms={r.get('lookup_ms')} elapsed={el:.2f}s")
                else:
                    motivo = r.get("error", "no_recent_event") if r else "no_recent_event"
                    if fl0["fmt_tries"] < int(cfg.get("applovin_format_max_tries", 3)):
                        fl0["fmt_next_at"] = ts + float(cfg.get("applovin_format_retry_s", 2.0))
                    else:
                        flog(f"[APPLOVIN][FORMAT] unknown source=none reason={motivo} tries={fl0['fmt_tries']}")
                        # FORMAT esgotou as tentativas -> SÓ AGORA o classificador visual é fallback.
                        # Enquanto o FORMAT retenta, o tipo permanece "unknown" (conservador).
                        if tipo_future is None and not fl0["visual_classifier_invoked"]:
                            fl0["visual_classifier_invoked"] = True
                            fl0["visual_classifier_reason"] = f"format_unknown_{motivo}"
                            fl0["visual_classifier_at"] = time.perf_counter()
                            tipo_future, ti2 = _fc_classificar_async()
                            if tipo_future is None:
                                # IA desligada/indisponível: registra e mantém "unknown" (conservador)
                                ad_type, type_reason, type_detail = ti2
                                fl0["visual_classifier_ms"] = round(
                                    (time.perf_counter() - fl0["visual_classifier_at"]) * 1000)
                                _fc_log_tipo(ad_type, type_reason, type_detail)
                                flog(f"[APPLOVIN][TYPE] visual_classifier_invoked=False reason={type_reason} "
                                     f"ms={fl0['visual_classifier_ms']} (tipo permanece {ad_type})")
                            else:
                                flog(f"[APPLOVIN][TYPE] visual_classifier_invoked=True "
                                     f"reason={fl0['visual_classifier_reason']} (POST em thread)")
            if (st.flow is not None and fmt_future is None and st.flow["fmt_next_at"] is not None
                    and ts >= st.flow["fmt_next_at"]):
                st.flow["fmt_next_at"] = None
                st.flow["fmt_tries"] += 1
                fmt_future = _fc_formato_async()

            # Tipo (classificação assíncrona — nunca bloqueia o tick)
            if tipo_future is not None and tipo_future.done():
                tipo_c, type_reason, type_detail = tipo_future.result()
                tipo_future = None
                flt = st.flow
                if flt is not None and flt.get("visual_classifier_at"):
                    flt["visual_classifier_ms"] = round(
                        (time.perf_counter() - flt["visual_classifier_at"]) * 1000)
                if flt is not None and flt["format_source"] in ("app_event", "cas_event"):
                    if flt.get("visual_classifier_invoked"):
                        flog(f"[APPLOVIN][TYPE] visual_classifier result={tipo_c} IGNORADO "
                             f"(formato já veio de {flt['format_source']}) "
                             f"ms={flt.get('visual_classifier_ms')}")
                    else:
                        flog(f"[APPLOVIN][TYPE] classificador={tipo_c} ignorado "
                             f"(formato já veio de {flt['format_source']})")
                else:
                    ad_type = tipo_c
                    _fc_log_tipo(ad_type, type_reason, type_detail)
                    if flt is not None and flt.get("visual_classifier_invoked"):
                        flog(f"[APPLOVIN][TYPE] visual_classifier_invoked=True "
                             f"reason={flt.get('visual_classifier_reason')} "
                             f"ms={flt.get('visual_classifier_ms')}")
            if not armed:
                if ad_type == "interstitial":
                    armed = True
                    flog(f"[FASTCLOSE][ARMED] reason=interstitial elapsed={el:.2f}s")
                elif el >= rewarded_min:
                    armed = True
                    flog(f"[FASTCLOSE][ARMED] reason=min_watch type={ad_type} elapsed={el:.2f}s")

            # 1) UM dump por tick
            _hb_mark_dump_start()
            _hb_write("RUNNING", "dump_hierarchy")
            try:
                raw = _dump_hierarchy_medido()
                t_dump = time.perf_counter()
                root = ET.fromstring(raw)
            except Exception as e:
                dump_errors += 1
                _hb_mark_dump_end()
                flog(f"[FASTCLOSE][DUMP_ERROR] n={n} count={dump_errors} err={type(e).__name__}: {e}")
                if dump_errors >= _FC_MAX_DUMP_ERRORS:
                    result = FC_ERROR
                    break
                _fc_dormir(ts, _fc_tick_eff(st, ad_type, tick_s, burst))
                continue
            dump_errors = 0
            if w is None:
                bs = [b for b in (_fc_bounds(x) for x in root.iter("node")) if b]
                if not bs:
                    _fc_dormir(ts, _fc_tick_eff(st, ad_type, tick_s, burst))
                    continue
                w, h = max(b[2] for b in bs), max(b[3] for b in bs)

            # 2) Análise local do MESMO XML
            an = _fc_analisar_dump(root, w, h)
            last_dump = (raw, root, an, w, h)
            _hb_mark_dump_end()
            _hb_write("RUNNING", "watcher")
            t_parse = time.perf_counter()
            dump_ms = (t_dump - ts) * 1000
            parse_ms = (t_parse - t_dump) * 1000
            st.dump_ms.append(dump_ms)
            state = an["state"]
            best = an["best"]

            for r in an["rejects"]:
                key = (r["reason"], r["id"], r["bounds"])
                if key not in st.rejects_logged:
                    st.rejects_logged.add(key)
                    flog(f"[FASTCLOSE][REJECT] reason={r['reason']} src={r['src']} id={r['id']} "
                         f"desc={r['desc']!r} text={r['text']!r} bounds={r['bounds']}")

            # 3) app_current() só para confirmar fechamento ou resolver ambiguidade
            if state == "main" and main_marker_unreliable:
                state = "ambiguous"
            if state == "main":
                ac = _fc_estado_app_current()
                if ac != "main":
                    main_marker_unreliable = True
                    flog(f"[FASTCLOSE][WARN] marcador da MainActivity no dump, mas app_current={ac}. "
                         f"Ignorando marcador neste anúncio.")
                    state = ac
                    last_ac = time.perf_counter()
                    sticky_applovin = ac == "applovin"
            elif state == "ambiguous":
                if sticky_applovin and ts - last_ac < _FC_APP_CURRENT_RECHECK:
                    state = "applovin"
                elif ts - last_ac >= _FC_APP_CURRENT_MIN_INTERVAL:
                    last_ac = ts
                    state = _fc_estado_app_current()
                    sticky_applovin = state == "applovin"
                    flog(f"[FASTCLOSE][STATE] ambiguous -> app_current={state}")

            if log_ticks:
                flog(f"[FASTCLOSE][TICK] n={n} t={el:.2f}s dump={dump_ms:.0f}ms parse={parse_ms:.0f}ms "
                     f"total={(time.perf_counter() - ts) * 1000:.0f}ms state={state} "
                     f"candidates={len(an['cands'])} armed={armed} type={ad_type} "
                     f"miss={st.x_miss_count} transient={st.transient_x_mode}")

            if state == "main":
                if st.ex is not None:
                    if st.ex["control_pending"]:
                        flog(f"[FASTCLOSE][CONTROL_RESULT] state=closed "
                             f"after={(t_parse - st.ex['control_at']) * 1000:.0f}ms")
                    flog(f"[FASTCLOSE][MAIN] moedas={_fc_moedas(an)!r} last_action={st.last_action}")
                _fc_registrar_moedas(_fc_moedas(an))
                fonte = "endcard_x" if st.last_action == "endcard_x_click" else st.last_action
                camada = f" layer={st.flow['x_source']}" if st.flow is not None and st.flow["x_source"] else ""
                if st.flow is not None:
                    _fc_fase(st, "closed", t_parse)
                flog(f"[FASTCLOSE][RESULT] closed=True source={fonte}{camada} elapsed={el:.2f}s "
                     f"last_action={st.last_action} clicks={st.clicks} reveals={st.reveal_attempts}")
                result = FC_CLOSED
                break

            fl = st.flow
            loja_esperada = (fl is not None and state == "store" and (
                fl["store_after_skip"] or (st.last_action == "skip_control" and fl["skip_at"] is not None
                                           and t_parse - fl["skip_at"] <= float(cfg.get("applovin_expected_store_window_s", 3.0)))))
            if loja_esperada:
                # Loja automática após o SKIP: esperada. BACK orientado por estado (este dump É a
                # verificação: só manda BACK se AINDA está na loja; nunca em sequência cega).
                if not fl["store_after_skip"]:
                    fl["store_after_skip"] = True
                    fl["store_entered_at"] = t_parse
                    fl["store_backs"] = 0
                    fl["store_back_pending"] = False
                    flog(f"[FASTCLOSE][EXPECTED_STORE_AFTER_SKIP] after_skip={(t_parse - fl['skip_at']) * 1000:.0f}ms")
                    flog(f"[FASTCLOSE][STORE] expected=True entered={datetime.now().strftime('%H:%M:%S.%f')[:-3]}")
                    _fc_fase(st, "store_after_skip", t_parse)
                # SETTLE: após um BACK, a volta leva ~0.4s; só concluímos "still_store" (e só
                # liberamos outro BACK) depois de settle_s. Controle por timestamp, sem sleep.
                settle = float(cfg.get("applovin_store_back_settle_s", 0.8))
                retry = max(settle, float(cfg.get("applovin_store_back_retry_interval_s", 1.0)))
                if fl["store_back_pending"] and t_parse - fl["store_back_at"] >= settle:
                    fl["store_back_pending"] = False
                    flog(f"[FASTCLOSE][STORE_BACK] result=still_store attempt={fl['store_backs']} "
                         f"after_back={(t_parse - fl['store_back_at']) * 1000:.0f}ms (settle={settle:.1f}s)")
                idade_loja = t_parse - fl["store_entered_at"]
                max_backs = int(cfg.get("applovin_store_max_backs", 3))
                pronto = (idade_loja >= float(cfg.get("applovin_store_initial_back_delay_s", 1.5)) if fl["store_backs"] == 0
                          else not fl["store_back_pending"] and t_parse - fl["store_back_at"] >= retry)
                if fl["store_backs"] < max_backs and pronto:
                    d.press("back")
                    fl["store_back_at"] = last_back = time.perf_counter()
                    fl["store_backs"] += 1
                    fl["store_back_pending"] = True
                    st.last_action, st.last_action_at = "back_from_store", last_back
                    flog(f"[FASTCLOSE][STORE_BACK] attempt={fl['store_backs']} age={last_back - fl['store_entered_at']:.2f}s "
                         f"after_skip={(last_back - fl['skip_at']) * 1000:.0f}ms")
                    skip_sleep = True  # verificar no próximo dump imediatamente
                elif fl["store_backs"] >= max_backs and not fl["store_max_logged"]:
                    fl["store_max_logged"] = True
                    flog(f"[FASTCLOSE][STORE_BACK] result=max_backs_reached attempts={fl['store_backs']} "
                         f"store_age={idade_loja:.2f}s — sem novos BACKs (observando até o timeout)")

            elif state in ("store", "external"):
                # EVENTO ANORMAL: não procura X aqui; BACK controlado e reavalia no próximo tick
                if prev_state not in ("store", "external"):
                    st.external_events += 1
                    tag = "UNEXPECTED_STORE" if state == "store" else "UNEXPECTED_EXTERNAL"
                    idade = f"{(t_parse - st.last_action_at) * 1000:.0f}ms" if st.last_action_at else "-"
                    flog(f"[FASTCLOSE][{tag}] n={n} elapsed={el:.2f}s last_action={st.last_action} "
                         f"last_action_age={idade} event={st.external_events}")
                    _fc_log_historico(st, tag)
                    _fc_ex_evento_externo(st, state, t_parse, raw)
                    if fl is not None and st.last_action == "reveal_top" and not fl["reveal_blocked"]:
                        fl["reveal_blocked"] = True
                        fl["reveal_waiting"] = False
                        flog("[FASTCLOSE][REVEAL_TOP_BLOCKED] reveal superior abriu a loja: sem novos reveals neste anúncio")
                    _fc_salvar_artefato(st, "store" if state == "store" else "external", raw)
                    if st.external_events > _FC_MAX_EXTERNAL_EVENTS:
                        result = FC_LEFT_AD
                        break
                if time.perf_counter() - last_back >= _FC_BACK_MIN_INTERVAL:
                    d.press("back")
                    last_back = time.perf_counter()
                    flog(f"[FASTCLOSE][BACK] from={state}")
                    st.last_action, st.last_action_at = "recovery_back", last_back
                    skip_sleep = True

            elif state == "applovin":
                fl_handled = False
                if fl is not None:
                    if fl["store_after_skip"]:
                        fl["store_after_skip"] = False
                        fl["store_back_pending"] = False
                        fl["back_from_store_at"] = fl["store_back_at"] if fl["store_backs"] else t_parse
                        destino = "endcard" if _fc_detectar_endcard(an)["confirmed"] else "applovin"
                        after = f"{(t_parse - fl['store_back_at']) * 1000:.0f}ms" if fl["store_backs"] else "no_back"
                        flog(f"[FASTCLOSE][STORE_BACK] result={destino} attempts={fl['store_backs']} after_back={after} "
                             f"store_time={t_parse - fl['store_entered_at']:.2f}s")
                    fl_handled, fl_skip = _fc_fluxo_applovin(st, an, root, raw, w, h, t_parse, t0, armed, ad_type)
                    skip_sleep = skip_sleep or fl_skip
                    _fc_diag_rewarded_sem_countdown(st, an, w, h, raw, t_parse, t0, ad_type)
                if fl_handled:
                    pass  # endcard / ação do fluxo neste tick: X genérico e transient não rodam
                elif best:
                    # ---- X visível ----
                    if st.x_visible_since is None:
                        st.x_visible_since = t_parse
                        st.last_age_log = t_parse
                        if not st.x_seen_before:
                            st.x_seen_before = True
                            st.x_first_seen_at = t_parse
                            flog(f"[FASTCLOSE][X_VISIBLE] first_seen={t_parse - t0:.3f}s armed={armed} type={ad_type}")
                        elif st.waiting_after_reveal:
                            st.reappeared_after_reveal = True
                            flog(f"[FASTCLOSE][X_REAPPEARED] after_reveal={(t_parse - st.last_reveal_at) * 1000:.0f}ms")
                        else:
                            flog(f"[FASTCLOSE][X_VISIBLE] reappeared_without_reveal at={t_parse - t0:.3f}s")
                        st.waiting_after_reveal = False
                        flog(f"[FASTCLOSE][FOUND] source={best['src']} id={best['id']} desc={best['desc']!r} "
                             f"text={best['text']!r} bounds={best['bounds']} center=({best['cx']},{best['cy']})")
                    elif t_parse - st.last_age_log >= 1.0:
                        st.last_age_log = t_parse
                        flog(f"[FASTCLOSE][X_VISIBLE] age={t_parse - st.x_visible_since:.3f}s armed={armed}")
                    st.x_last_seen_at = t_parse
                    st.x_miss_count = 0

                    if armed:
                        if st.clicks >= max_clicks:
                            if not st.click_limit_logged:
                                st.click_limit_logged = True
                                flog(f"[FASTCLOSE][CLICK_LIMIT] {st.clicks} cliques e X continua visível. Só observando.")
                                _fc_salvar_artefato(st, "false_click", raw)
                        elif st.last_click_at is None or time.perf_counter() - st.last_click_at >= click_cd:
                            fonte = "reappeared_x" if st.reappeared_after_reveal else best["src"]
                            t_c = time.perf_counter()
                            d.click(best["cx"], best["cy"])  # coordenada do dump; NUNCA UiObject.click()
                            t_c2 = time.perf_counter()
                            st.clicks += 1
                            st.last_click_at = t_c2
                            st.last_action, st.last_action_at = "close_click", t_c2
                            st.reappeared_after_reveal = False
                            flog(f"[FASTCLOSE][CLICK] source={fonte} x={best['cx']} y={best['cy']} "
                                 f"bounds={best['bounds']} detect_to_click={(t_c - t_parse) * 1000:.0f}ms "
                                 f"click_rpc={(t_c2 - t_c) * 1000:.0f}ms x_age={(t_c - st.x_visible_since) * 1000:.0f}ms "
                                 f"click_n={st.clicks}")
                            if st.clicks == _FC_FALSE_CLICK_AFTER:
                                flog(f"[FASTCLOSE][SUSPECT] X continua visível após {st.clicks - 1} cliques (possível falso X).")
                                _fc_salvar_artefato(st, "false_click", raw)
                else:
                    # ---- X ausente ----
                    if st.x_visible_since is not None:
                        st.x_miss_count += 1
                        flog(f"[FASTCLOSE][X_MISS] count={st.x_miss_count}/{miss_ticks}")
                        if st.x_miss_count >= miss_ticks:
                            dur = st.x_last_seen_at - st.x_visible_since
                            st.x_visible_since = None
                            st.x_miss_count = 0
                            flog(f"[FASTCLOSE][X_LOST] visible_duration={dur:.2f}s last_action={st.last_action}")
                            if tr_on and not st.transient_x_mode:
                                if tr_min <= dur <= tr_max:
                                    st.transient_x_mode = True
                                    flog(f"[FASTCLOSE][TRANSIENT] detected visible_duration={dur:.2f}s")
                                else:
                                    flog(f"[FASTCLOSE][TRANSIENT] not_detected visible_duration={dur:.2f}s "
                                         f"range=[{tr_min},{tr_max}]")

                    if st.waiting_after_reveal and ts - st.last_reveal_at > reveal_wait:
                        st.waiting_after_reveal = False
                        flog(f"[FASTCLOSE][REVEAL_NO_X] waited={ts - st.last_reveal_at:.2f}s "
                             f"attempt={st.reveal_attempts}/{max_reveals}")
                        _fc_log_historico(st, "REVEAL_NO_X")
                        _fc_salvar_artefato(st, "reveal_fail", raw)

                    # Reveal: SÓ se este anúncio já mostrou X, ele sumiu (N misses) e estamos ARMED
                    # (reveal em 0.45h desativado no AppLovin quando o fluxo novo está ativo: usa o top reveal)
                    if (tr_on and st.flow is None and st.transient_x_mode and st.x_seen_before and st.x_visible_since is None
                            and armed and not st.waiting_after_reveal and st.reveal_attempts < max_reveals
                            and (st.last_reveal_at is None or ts - st.last_reveal_at >= reveal_cd)):
                        pt = _fc_ponto_reveal(an, w, h)
                        if pt:
                            d.click(pt[0], pt[1])
                            st.reveal_attempts += 1
                            st.last_reveal_at = time.perf_counter()
                            st.waiting_after_reveal = True
                            st.last_action, st.last_action_at = "reveal_tap", st.last_reveal_at
                            flog(f"[FASTCLOSE][REVEAL] x={pt[0]} y={pt[1]} point={pt[2]} reason=x_disappeared "
                                 f"attempt={st.reveal_attempts}/{max_reveals}")
                            skip_sleep = True  # próximo dump IMEDIATO
                        else:
                            st.last_reveal_at = ts  # respeita o cooldown antes de reavaliar
                            flog("[FASTCLOSE][REVEAL_SKIPPED] reason=no_safe_point")

                # EXPERIMENTO top_control (observação separada do X; off em produção)
                if st.ex is not None and _fc_experimento_topo(st, an, w, h, t_parse, t0, raw, armed, miss_ticks):
                    skip_sleep = True

            st.history.append({
                "n": n, "t": f"{el:.2f}", "dump": int(dump_ms), "state": state, "x": bool(best),
                "cands": len(an["cands"]), "armed": armed, "miss": st.x_miss_count,
                "transient": st.transient_x_mode, "action": st.last_action})
            prev_state = state
            if not skip_sleep:
                _fc_dormir(ts, _fc_tick_eff(st, ad_type, tick_s, burst))
            try:
                perf_sample("actual_tick_ms", (time.perf_counter() - ts) * 1000)
            except Exception:
                pass
    except Exception as e:
        flog(f"[FASTCLOSE][ERROR] n={n} {type(e).__name__}: {e}")
        result = FC_ERROR

    el = time.perf_counter() - t0
    if st.dump_ms:
        dm = st.dump_ms
        flog(f"[FASTCLOSE][STATS] ticks={n} elapsed={el:.2f}s rate={n / max(el, 0.001):.2f}/s "
             f"dump_ms min={min(dm):.0f} avg={sum(dm) / len(dm):.0f} max={max(dm):.0f} "
             f"clicks={st.clicks} reveals={st.reveal_attempts} external={st.external_events} "
             f"x_seen_before={st.x_seen_before} transient={st.transient_x_mode}")
    if result != FC_CLOSED:
        flog(f"[FASTCLOSE][RESULT] closed=False result={result} elapsed={el:.2f}s type={ad_type} "
             f"last_action={st.last_action}")
        _fc_log_historico(st, result)
        _fc_salvar_artefato(st, result.lower())  # dump novo + screenshot ANTES do escape

    # ---- RECOVERY CLOSE CASCADE: só quando o fluxo normal não resolveu (nunca no caminho feliz) ----
    # Executa ANTES do escape (HOME/force-stop). NÃO declara reward_completed; apenas tenta encerrar.
    if (result != FC_CLOSED and cfg.get("recovery_close_enabled", True)
            and st.flow is not None and last_dump is not None):
        fl = st.flow
        rawR, rootR, anR, wR, hR = last_dump
        coins_antes_rec = _fc_coins_int(_fc_coins_last)
        flog(f"[RECOVERY_CLOSE][INVOKED] result={result} type={ad_type} reason=fluxo_nao_resolveu "
             f"elapsed={time.perf_counter() - t0:.1f}s")
        try:
            rres = _fc_recovery_close_cascade(st, anR, rootR, rawR, wR, hR,
                                              f"result={result}_type={ad_type}")
            fl["recovery_invoked"] = True
            fl["recovery_reason"] = f"result={result}_type={ad_type}"
            if rres:
                fl["recovery_layers_tried"] = rres.get("layers_tried") or []
                fl["recovery_candidate_source"] = rres.get("candidate_source")
                fl["recovery_click_x"] = rres.get("click_x")
                fl["recovery_click_y"] = rres.get("click_y")
                fl["recovery_result"] = rres.get("result")
                fl["recovery_duration_ms"] = rres.get("duration_ms")
            if rres and rres.get("result") != "exhausted" and rres.get("click_x") is not None:
                # apenas houve clique: janela curta de observação pós-clique (sem novos cliques)
                obs = _fc_recovery_observe(float(cfg.get("recovery_close_observe_s", 5.0)))
                if obs["chegou_main"]:
                    fl["recovery_result"] = "closed_to_main"
                    result = FC_CLOSED
                elif obs["ultimo_estado"] in ("store", "applovin"):
                    fl["recovery_result"] = "closed_to_" + obs["ultimo_estado"]
                # mede reward (se chegou a main, coin pode atualizar)
                try:
                    _fc_log_reward(coins_antes_rec)
                except Exception:
                    pass
                fl["reward_after_recovery"] = _fc_coins_last
        except Exception as e:
            flog(f"[RECOVERY_CLOSE][ERROR] {type(e).__name__}: {e}")
    if st.flow is not None:
        fl, cd = st.flow, st.flow["cd"]
        if (result == FC_TIMEOUT and cfg.get("applovin_skip_to_endcard_enabled", False)
                and cfg.get("applovin_reward_completion_required", True) and cd["state"] != "completed"):
            flog(f"[FASTCLOSE][REWARD_COMPLETION] not_detected countdown_state={cd['state']} "
                 f"values={cd['values'][-15:]} — sem SKIP (nenhum fallback temporal)")
            _fc_salvar_artefato(st, "reward_completion_not_detected")
        meta = fl["format_meta"]
        flog(f"[FASTCLOSE][AD_SUMMARY_FORMAT] format={fl['format'] or ad_type} format_source={fl['format_source'] or 'none'} "
             f"network={meta.get('network')} creative_id={meta.get('creative_id')} unit_id={meta.get('unit_id')} "
             f"pill_first_seen={fl['pill_first_seen']} pill_source={fl['pill_source_used']} "
             f"reveal_used={fl['reveals'] > 0} detect_to_skip_ms={fl['detect_to_skip_ms']} "
             f"skip_reason={fl['skip_reason']} reward_completed={cd['state'] == 'completed'} "
             f"visual_classifier_invoked={fl.get('visual_classifier_invoked')} "
             f"visual_classifier_reason={fl.get('visual_classifier_reason')} "
             f"visual_classifier_ms={fl.get('visual_classifier_ms')}")
        flog(f"[FASTCLOSE][AD_SUMMARY] result={result} elapsed={el:.1f}s type={ad_type} "
             f"first_pill={fl['pill_windows'][0][0] if fl['pill_windows'] else None} pill_windows={fl['pill_windows']} "
             f"countdown_seen={bool(cd['values'])} countdown_confirmed={cd['confirmed_at'] is not None} "
             f"sequence={cd['values'][-20:]} countdown_state={cd['state']} "
             f"reward_completed={cd['state'] == 'completed'} completion_source={cd['completed_source']} "
             f"skip={fl['skip_elapsed']} store_backs={fl['store_backs']} x_layer={fl['x_source']} "
             f"recovery_invoked={fl.get('recovery_invoked')} recovery_reason={fl.get('recovery_reason')} "
             f"recovery_layers_tried={fl.get('recovery_layers_tried')} "
             f"recovery_candidate_source={fl.get('recovery_candidate_source')} "
             f"recovery_click_x={fl.get('recovery_click_x')} recovery_click_y={fl.get('recovery_click_y')} "
             f"recovery_confidence={fl.get('recovery_confidence')} recovery_result={fl.get('recovery_result')} "
             f"reward_after_recovery={fl.get('reward_after_recovery')} "
             f"recovery_duration_ms={fl.get('recovery_duration_ms')}")
    _hb_write("RUNNING", "main", force=True)
    return result


def processar_applovin_fast(origem):
    """Controlador ÚNICO do anúncio AppLovin atual. Retorna True se fechou.
    Se não fechou: artefatos já salvos pelo watcher -> escape HOME + force-stop.
    Nunca chama legacy / fechar_anuncio_applovin / cirúrgico sobre o mesmo anúncio."""
    global _applovin_handled, total_escapes
    _applovin_handled = True
    # SESSION_START: a sessão de anúncio realmente começa aqui (ad assumido). Se o ciclo ainda não
    # foi iniciado (ex.: anúncio abriu pelo retry de banner dentro do ciclo), inicia AGORA — sem
    # novo clique. Guardado para não duplicar (1 clique = 1 ciclo).
    if _cycle is not None and not _cycle.active:
        try:
            _cycle.begin(activity="applovin", ad_format=_last_ad_format)
            if qa_scheduler is not None:
                qa_scheduler.mark_session_started()
        except Exception:
            pass
    flog(f"[APPLOVIN] fast_close assumiu o anúncio (origem={origem}).")
    coins_before = _fc_coins_last
    try:
        # FORMAT PRIMEIRO (autoridade). O classificador VISUAL é lazy: só é disparado dentro do
        # watcher se o FORMAT esgotar applovin_format_max_tries sem resultado. Isso evita
        # screenshot + POST /classify em anúncios que o evento do app já resolveu.
        fmt_fut = _fc_formato_async()  # formato pelo evento do app/CAS (logcat), não bloqueia
        tipo_ini = ("unknown", "visual_classifier_deferred", "")
        res = fast_close_watcher_applovin(None, tipo_ini, fmt_fut)
    except Exception as e:
        flog(f"[FASTCLOSE][ERROR] {type(e).__name__}: {e}")
        res = FC_ERROR
    if res == FC_CLOSED:
        try:
            evs = _fc_eventos_pid(0.0)
            _fc_log_reward(coins_before)
            if isinstance(evs, dict):
                flog("[FASTCLOSE][EVENTS] " + " ".join(f"{k}={v}" for k, v in evs.items() if k != "error") or
                     "[FASTCLOSE][EVENTS] nao_observados")
        except Exception as e:
            flog(f"[FASTCLOSE][REWARD] erro={type(e).__name__}: {e}")
        return True
    total_escapes += 1
    log(f"[APPLOVIN] fast_close result={res}. Escape {total_escapes} (HOME + force-stop)...")
    try:
        d.press("home")
        time.sleep(1)
        d.app_stop(package)
        time.sleep(1)
    except Exception:
        pass
    return False


def _fc_contabilizar(ok):
    global total_ads, falhas_consecutivas
    if ok:
        total_ads += 1
        falhas_consecutivas = 0
    else:
        falhas_consecutivas += 1
    return ok


def _fc_pre_anuncio():
    """Antes de classificar: garante que o anúncio ABRIU (banner em recarga) e, se for
    AppLovin, entrega ao fast_close em T0 (sem assistir/movimentar antes).
    Retorna None para seguir o fluxo antigo (anúncio não-AppLovin)."""
    cur = cur_app()
    act = str(cur.get('activity', ''))
    if cur.get('package') == package and '.MainActivity' in act:
        dump_m = get_hierarchy_data()  # saldo ANTES do anúncio (medição de recompensa)
        if dump_m is not None:
            moedas = _fc_moedas({'nodes': list(dump_m.iter('node'))})
            _fc_registrar_moedas(moedas)
            flog(f"[FASTCLOSE][PRE_AD] moedas={moedas!r}")
        if not cfg.get("wait_for_banner", True):
            return None
        log("[LOG] Banner em recarga na MainActivity. Tentando abrir anúncio...")
        global _fc_banner_falhas
        if not abrir_anuncio_banner(d):
            _fc_banner_falhas += 1
            # Telemetria (FASE 17): publica no_fill SEMPRE que o banner não carrega, independente de
            # idle_backoff_enabled. NÃO altera o comportamento de backoff.
            _hb_write("BACKOFF", "no_fill", last_dump_ok=False, force=True)
            if cfg.get("idle_backoff_enabled", False):
                # FASE 7: backoff crescente + jitter (evita 113 devices sincronizados)
                base = int(cfg.get("idle_backoff_base_s", 30))
                teto = int(cfg.get("idle_backoff_max_s", 300))
                jit = float(cfg.get("idle_backoff_jitter_s", 15))
                espera = min(base * _fc_banner_falhas, teto) + random.uniform(0, jit)
            else:
                espera = min(30 * _fc_banner_falhas, 120)  # sem anúncio: backoff, não polling
            log(f"[LOG] Banner não carregou mesmo após tentativas. [BANNER][BACKOFF] falhas_seguidas="
                f"{_fc_banner_falhas} aguardando={espera:.0f}s antes de reiniciar o app...")
            d.app_stop(package)
            time.sleep(espera)
            d.app_start(package)
            return False
        _fc_banner_falhas = 0
    if eh_anuncio_applovin():
        return _fc_contabilizar(processar_applovin_fast("inicio_ciclo"))
    return None


def fechar_anuncio_cirurgico(d):
    """Fechamento de anúncio no padrão COMPROVADO do main_market.py (que fechava bem).

    Por ciclo:
      1) se voltou à MainActivity -> sucesso
      2) loja/browser/launcher -> tratar (instalar por % / BACK) e continua
      3) FAST PATH: resource-ids conhecidos (exists timeout=0) -> 1 clique
      4) DUMP cascata: resource-id -> text -> content-desc - xpath -> textview -> botão vazio
         (com pular_textos alternando, evitando loop em botões falsos que abrem loja)
      5) X por posição no dump (close/fechar/ImageView no topo)
      6) IA a cada 3 ciclos (só quando dump falhou) -> 1 clique exato
      7) após qualque clique:volta de browser/loja com BACK
      Máx 30 ciclos ou timeout 100s (padrão comprovado).
    """
    start_time_global = time.time()
    MAX_TIMEOUT_ADS = 100
    pular_textos = False

    for ciclo in range(30):
        tempo_decorrido = time.time() - start_time_global
        if tempo_decorrido > MAX_TIMEOUT_ADS:
            log(f"[ALERTA] Timeout global {MAX_TIMEOUT_ADS}s em fechamento.")
            break

        try:
            cur = cur_app()
            act = str(cur.get('activity', ''))
            pkg = str(cur.get('package', ''))
        except Exception:
            time.sleep(1)
            continue

        # 1) Voltou ao app -> sucesso
        if pkg == package and '.MainActivity' in act:
            log(f"[SUCESSO] Voltou à MainActivity (ciclo {ciclo}).")
            return True

        # 2) Play Store: oportunidade de INSTALAR (por %); browser -> BACK
        is_playstore = (any(s in act for s in ["vending", "finsky", "HsdpAlias", "play.google"]) or
                        any(s in pkg for s in ["vending", "finsky"]))
        is_browser = (any(s in pkg for s in ["browser", "chrome"]) or 'launcher' in act)
        if is_playstore:
            tratar_play_store(d)
            continue
        if is_browser:
            voltar_de_externo(d, max_backs=2)
            continue

        # ===== APPLOVIN (estratégia específica, aditiva) =====
        # Se a tela atual é um anúncio AppLovin, tenta a estratégia dedicada
        # (Regras 1-10), ou a lógica legada (main_market) se habilitada.
        # Se resolver, termina; se não resolver (False),
        # segue o fluxo normal abaixo (que já trata AppLovin via playable).
        if eh_anuncio_applovin():
            if cfg.get("fast_close_enabled", True):
                # Um único controlador AppLovin por anúncio: sem legacy/novo/playable aqui
                if _applovin_handled:
                    log("[APPLOVIN] Anúncio já tratado pelo fast_close. Sem reentrada.")
                    return False
                return processar_applovin_fast("cirurgico")
            if cfg.get("use_legacy_applovin", True):
                if fechar_anuncio_applovin_legacy(d):
                    return True
            elif fechar_anuncio_applovin(d):
                return True

        # 3) FAST PATH: resource-ids conhecidos (exists timeout=0, barato)
        clicou = False
        for fast_id in ["com.google.android.gms:id/close_button", "inmobi_close_btn",
                        "com.android.vending:id/close_btn", "unityads-btn-close",
                        "applovin_close_button"]:
            try:
                if d(resourceId=fast_id).exists(timeout=0):
                    log(f"[FAST-PATH] Detectado '{fast_id}'. Clicando...")
                    d(resourceId=fast_id).click()
                    time.sleep(0.8)
                    clicou = True
                    break
            except Exception:
                pass
        if clicou:
            continue

        eh_playable = ('unity3d' in act or 'applovin' in act or 'FullScreen' in act or 'WebViewDisplay' in act)

        # ===== PLAYABLE (Unity/AppLovin) =====
        # O X do playable está no CANVAS (não aparece no dump) e o botão fica ativo
        # só ~6s quando aparece. Estratégia: POLLING SÓ DE IA (screenshot, sem dump),
        # o mais rápido possível; no instante que a IA detecta o >|, clica.
        # Antes, aguarda a barra de recompensa terminar (fim do vídeo) para o botão surgir.
        if (not clicou) and eh_playable:
            try:
                h_ = d.window_size()[1]
            except Exception:
                h_ = 2280
            gp_x1, gp_y1, gp_x2, gp_y2 = 719, 177, 1008, 246

            def _fora_do_cta(x, y):
                if gp_x1 <= x <= gp_x2 and gp_y1 <= y <= gp_y2:
                    return False
                return y < gp_y1 or x < 160

            inicio = time.time()
            fechou_p = False

            # janela total: permite que o vídeo termine e o botão apareça
            while time.time() - inicio < 45:
                try:
                    cur = cur_app()
                    act = str(cur.get('activity', ''))
                    pkg_ = str(cur.get('package', ''))
                except Exception:
                    time.sleep(0.5)
                    continue

                if pkg_ == package and '.MainActivity' in act:
                    log("[LOG] Playable fechou sozinho (voltou ao app).")
                    fechou_p = True
                    break
                if any(s in act for s in ["vending", "finsky", "HsdpAlias"]) or 'play.google' in pkg_:
                    tratar_play_store(d)
                    fechou_p = True
                    break

                # SÓ IA (rápido): captura o >| na janela de 6s em que ele aparece
                if cfg.get("use_ia", True):
                    hit = ia_detect_x()
                    if hit and len(hit) == 3:
                        x, y, conf = hit
                        if conf >= 0.25 and y < h_ * 0.16 and _fora_do_cta(x, y):
                            log(f"[IA] >| detectado em ({x},{y}) conf={conf:.2f}. Clique único...")
                            d.click(x, y)
                            time.sleep(1.5)
                            p__, a__ = cur_app().get('package'), str(cur_app().get('activity', ''))
                            if p__ == package and '.MainActivity' in a__:
                                fechou_p = True
                                log("[LOG] Fechou (voltou ao app).")
                            break

                time.sleep(0.4)  # polling rápido (captura a janela de ~6s)

            if fechou_p:
                clicou = True
            else:
                # não capturou: reinicia o app p/ o ciclo seguir (o anúncio pode auto-fechar)
                log("[LOG] Playable sem >| na janela. Reiniciando app p/ seguir ciclo...")
                try:
                    d.app_stop(package)
                    time.sleep(1)
                    d.app_start(package)
                    time.sleep(1)
                except Exception:
                    pass
                clicou = True
            continue

        # ===== NÃO-PLAYABLE (ads com hierarquia real) =====
        # 4) DUMP cascata COM pular_textos
        dump = get_hierarchy_data()
        usou_texto = False

        if clicar_por_atributo(dump, "resource-id", SELECTORS, lambda x: d(resourceId=x)):
            clicou = True
        elif not pular_textos and clicar_por_atributo(dump, "text", TEXTS, lambda x: d(text=x)):
            clicou = True
            usou_texto = True
        elif clicar_por_atributo(dump, "content-desc", ["Interstitial close button", "Close", "Fechar"],
                                 lambda x: d(description=x)):
            clicou = True
            usou_texto = True
        elif not pular_textos and tentar_clicar_xpath(XPATHS_PERSONALIZADOS):
            clicou = True
            usou_texto = True
        elif not pular_textos and clicar_por_atributo(dump, "content-desc", DESCRIPTIONS,
                                                      lambda x: d(description=x)):
            clicou = True
            usou_texto = True
        elif not pular_textos and tentar_clicar_textview_especial(dump):
            clicou = True
            usou_texto = True
        elif not pular_textos and tentar_clicar_botao_vazio(dump):
            clicou = True
            usou_texto = True

        # 5) X por posição no dump (close/fechar/ImageView no topo)
        if not clicou:
            pos = achar_botao_fechar_no_dump(dump)
            if pos:
                log(f"[LOG] X no dump em ({pos[0]},{pos[1]}). Clique...")
                d.click(pos[0], pos[1])
                time.sleep(1.0)
                clicou = True

        # Alterna pular_textos (se usou texto e não resolveu, pula textos no próximo)
        pular_textos = True if usou_texto else False
        dump = None
        gc.collect()

        # 6) IA a cada 3 ciclos (SÓ quando dump falhou, ads não-playable)
        if not clicou and cfg.get("use_ia", True) and (ciclo % 3 == 0):
            hit = ia_detect_x()
            if hit and len(hit) == 3:
                x, y, conf = hit
                try:
                    _, h_ = d.window_size()
                except Exception:
                    h_ = 2280
                if 0 <= y <= h_ * 0.30 and conf >= 0.30:
                    log(f"[IA] X detectado em ({x},{y}) conf={conf:.2f}. Clique...")
                    d.click(x, y)
                    time.sleep(1.0)
                    clicou = True

    # Esgotou ciclos/timeout: um único escape limpo (padrão original)
    global total_escapes
    total_escapes += 1
    log(f"[LOG] Fechamento esgotou ciclos. Escape {total_escapes} (HOME + force-stop)...")
    try:
        d.press("home")
        time.sleep(1)
        d.app_stop(package)
        time.sleep(1)
    except Exception:
        pass

    try:
        cur = cur_app()
        if cur.get('package') == package and '.MainActivity' in cur.get('activity', ''):
            return True
    except Exception:
        pass
    return False

def movimentar_anuncio(d, duracao):
    """Move/interage com o anúncio (swipes) por duração segundos - parecer humano."""
    global total_moves
    w, h = d.window_size()
    end = time.time() + duracao
    while time.time() < end:
        direcao = random.choice(["up", "down", "left", "right"])
        if direcao == "up":
            d.swipe(int(w*0.5), int(h*0.8), int(w*0.5), int(h*0.2), duration=0.5)
        elif direcao == "down":
            d.swipe(int(w*0.5), int(h*0.2), int(w*0.5), int(h*0.8), duration=0.5)
        elif direcao == "left":
            d.swipe(int(w*0.9), int(h*0.5), int(w*0.1), int(h*0.5), duration=0.5)
        else:
            d.swipe(int(w*0.1), int(h*0.5), int(w*0.9), int(h*0.5), duration=0.5)
        time.sleep(random.uniform(0.8, 1.5))
    total_moves += 1
    return True

def dump_tem_conteudo_util(dump_xml):
    """Considera que o dump tem conteúdo útil (botões/close) se achar qualquer
    node com desc/text/id/clickable além dos containers puros de playable
    (root, playable, canvas, loader etc). Distingue rewarded em progresso
    (TEM botão Privacy/skip) de playable HTML5 realmente vazio."""
    if dump_xml is None:
        return False
    for node in dump_xml.iter("node"):
        rid = node.attrib.get("resource-id", "")
        desc = node.attrib.get("content-desc", "")
        text = node.attrib.get("text", "")
        clk = node.attrib.get("clickable", "")
        cls = node.attrib.get("class", "").split('.')[-1]
        if rid in ("root", "playable", "main", "canvas2d", "canvas3d",
                   "loader", "page", "video-background-content-frame"):
            continue
        if clk == "true" or rid or desc or text:
            return True
    return False


def aguardar_x_recompensa(d, max_wait):
    """Aguarda (SEM tocar na tela) até que o botão de fechar do anúncio
    premiado fique visível/clicável = fim do vídeo. Retorna (cx, cy),
    ou 'main' se já voltou ao app, 'store' se abriu Play Store, ou None."""
    start = time.time()
    while time.time() - start < max_wait:
        try:
            cur = cur_app()
            act = str(cur.get('activity', ''))
            if cur.get('package') == package and '.MainActivity' in act:
                log(f"[LOG] Voltou à MainActivity durante a espera.")
                return 'main'
            if any(s in act for s in ["vending", "finsky", "HsdpAlias"]):
                log(f"[LOG] Play Store abriu durante a espera.")
                return 'store'
        except Exception:
            pass
        dump = get_hierarchy_data()
        pos = achar_botao_fechar_no_dump(dump)
        dump = None
        if pos:
            # X do rewarded fica no TOPO (zona do >|). X no meio = CTA falso (abre Chrome/loja)
            try:
                _, h_ = d.window_size()
            except Exception:
                h_ = 2280
            if pos[1] < h_ * 0.15:
                log(f"[LOG] X disponível após {time.time()-start:.0f}s em ({pos[0]},{pos[1]}).")
                return pos
        time.sleep(2.0)
    return None


def sair_de_playable_travado(d, forcar=False):
    """Escape de playable HTML5 REALMENTE vazio (dump sem elementos úteis).
    Só dispara com forcar=True (após muitas tentativas). NUNCA derruba um
    rewarded/premiado em progresso apenas pelo nome da activity."""
    try:
        cur = cur_app()
        act = str(cur.get('activity', ''))
        if '.MainActivity' in act or not act:
            return False
        if not any(s in act for s in ["applovin", "unity3d", "FullScreen", "AdActivity", "WebView"]):
            return False
        dump = get_hierarchy_data()
        empty = not dump_tem_conteudo_util(dump)
        dump = None
        if empty and forcar:
            log("[LOG] Playable sem elementos confirmado. Aplicando HOME + force-stop...")
            d.press("home")
            time.sleep(1)
            d.app_stop(package)
            time.sleep(1)
            return True
        return False
    except Exception as e:
        log(f"[ERRO] sair_de_playable_travado: {e}")
    return False

def salvar_error_screenshot(extra=""):
    """Salva screenshot + xml em errors/ para re-treino da IA."""
    try:
        ts = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        base = os.path.join(ERRORS_DIR, f"{device.replace(':', '_').replace('.', '_')}_{ts}")
        png = screenshot_bytes()
        if png:
            with open(base + f"_{extra}.png", 'wb') as f:
                f.write(png)
        try:
            with open(base + f"_{extra}.xml", 'w', encoding='utf-8') as f:
                f.write(_dump_hierarchy_medido())
        except Exception:
            pass
        log(f"[ERRO] Screenshot salvo em {base}")
    except Exception as e:
        log(f"[ERRO] salvar_error_screenshot: {e}")

# ==================== PLAY STORE (instalar app) ====================

def tratar_play_store(d):
    """Trata Play Store (intersticial de instalação). Decide instalar ou voltar.
    Se instalar: clica em 'Instalar', aguarda o download concluir (botão vira 'Abrir'
    ou aparece 'Instalado'), só então volta para o app."""
    global total_installs
    try:
        cur = cur_app()
        act = str(cur.get('activity', ''))
        if not any(s in act for s in ["vending", "finsky", "play.google", "HsdpAlias"]):
            return False

        if qa_scheduler is not None:
            deve_instalar = qa_scheduler.install_decision()
        else:
            deve_instalar = CONFIG.decide(cfg, "download_apps_pct")
        log(f"[STORE] Play Store detectada. instalar decide: {deve_instalar}")

        dump_ps = get_hierarchy_data()
        if deve_instalar:
            instalou = (clicar_por_atributo(dump_ps, "text", ["Instalar", "Instalar agora", "Install"],
                                            lambda x: d(text=x)) or
                        clicar_por_atributo(dump_ps, "content-desc", ["Instalar", "Install"],
                                            lambda x: d(description=x)) or
                        tentar_clicar_xpath(['//*[@text="Instalar"]', '//*[@content-desc="Instalar"]']))
            if instalou:
                total_installs += 1
                log(f"[STORE] Clicou em Instalar ({total_installs}). Aguardando download concluir...")
                # Aguarda até sumir o 'Instalando/Cancelar' ou aparecer 'Abrir'/'Instalado'
                t0 = time.time()
                instalou_completo = False
                while time.time() - t0 < cfg.get("install_wait_s", 20):
                    time.sleep(2)
                    try:
                        cur2 = cur_app()
                        pkg2 = str(cur2.get('package', ''))
                        if any(s in pkg2 for s in ["vending", "finsky", "play.google"]):
                            # na loja ainda: checa progresso
                            dump_ = get_hierarchy_data()
                            textos = [n.attrib.get("text", "") for n in dump_.iter("node")]
                            dump_ = None
                            if any("Abrir" in t or "Instalado" in t or "Jogar" in t for t in textos):
                                instalou_completo = True
                                log("[STORE] App instalado (botão 'Abrir'/'Instalado').")
                                break
                            if any("Cancelar" in t for t in textos):
                                continue  # ainda baixando
                    except Exception:
                        break
                if not instalou_completo:
                    log("[STORE] Download ainda em andamento ou timeout. Prosseguindo...")
                if qa_scheduler is not None:
                    # QA: instalação só é SUCESSO quando confirmada ('Abrir'/'Instalado').
                    qa_scheduler.on_install_result(bool(instalou_completo))
                    qa_scheduler.save()
                # volta pro app
                for _ in range(4):
                    d.press("back")
                    time.sleep(0.8)
            else:
                log("[STORE] Botão Instalar não encontrado. Voltando...")
                if qa_scheduler is not None:
                    qa_scheduler.on_install_result(False)
                    qa_scheduler.save()
                d.press("back")
                time.sleep(1)
        else:
            # Não instalar: intersticial PLAY STORE transparente (HsdpAlias).
            # IMPORTANTE: NÃO clicar em 'Fechar planilha'/'Fechar' — isso abre a tela
            # completa da loja (finsky MainActivity) e causa loop até timeout.
            # O overlay transparente fecha limpo apenas com BACK (padrão efetivo original).
            is_overlay = "HsdpAlias" in act or "transparent" in act.lower()
            if is_overlay:
                log("[STORE] Overlay Play Store transparente detectado. Fechando com BACK (sem clicar em Fechar planilha)...")
                d.press("back")
                time.sleep(1)
            else:
                # Loja completa: tenta fechar com BACK também (mais seguro)
                log("[STORE] Tela da Play Store. Fechando com BACK...")
                d.press("back")
                time.sleep(1)
        dump_ps = None
        gc.collect()
        return True
    except Exception as e:
        log(f"[ERRO] tratar_play_store: {e}")
        return False

# ==================== LOOP PRINCIPAL ====================

def reset_id():
    """/ Reset de ID de publicidade (Google Ads Privacy). Retorna True se o reset foi concluído.
    Valida retornando à home (o fluxo seguinte exige app parado antes do pm clear)."""
    log("[RESET][INFO] Iniciando reset de ID de publicidade...")
    try:
        d.shell("am start -a com.google.android.gms.settings.ADS_PRIVACY")
        time.sleep(2)
        clicou = False
        for option in ["Redefinir ID de publicidade", "Redefinir código de publicidade",
                       "Repor ID de publicidade", "Reset advertising ID", "Reset Advertising ID"]:
            try:
                if d(text=option).exists:
                    log(f"[RESET][ID] Clicando em '{option}'...")
                    d(text=option).click()
                    clicou = True
                    break
            except Exception:
                continue
        time.sleep(2)
        if d(resourceId="android:id/button1").exists(timeout=1):
            d(resourceId="android:id/button1").click()
        time.sleep(1)
        # confirma retorno à home (reset concluído / saiu da tela de publicidade)
        d.press("home")
        time.sleep(1)
        if not clicou:
            # Sem ação real de reset não há reset: falha IDENTIFICÁVEL (nunca success silencioso).
            log("[RESET][ERROR] stage=id_reset reason=reset_action_not_found "
                "(nenhuma opção de reset encontrada; home pressionado sem conclusão real)")
            try:
                salvar_error_screenshot("reset_id_not_found")
            except Exception:
                pass
            return False
        log("[RESET][ID] success")
        return True
    except Exception as e:
        log(f"[RESET][ERROR] stage=id_reset {type(e).__name__}: {e}")
        return False


# ---- Pendência de reset/clear (POR DEVICE: cada bot é um processo próprio) ----
# Falha de reset/clear NÃO é esquecida: vira pendência repetida com backoff, e o app NÃO é
# relançado como se nada tivesse acontecido. Como o estado é local ao processo, a falha de um
# device não bloqueia os outros.
_reset_pending = False          # reset de identidade falhou -> repetir com backoff (sem abrir o app)
_clear_pending = False          # clear avulso falhou -> repetir com backoff (sem abrir o app)
_pending_backoff_s = 0.0        # backoff corrente desta pendência
_PENDING_BACKOFF_BASE_S = 15.0
_PENDING_BACKOFF_MAX_S = 300.0
_last_clear_ms = 0              # duração do último pm clear (telemetria/[PERF])
_did_clear_this_round = False   # o reset JÁ limpou nesta rodada -> clear_data_pct não repete


def _clear_app_data_verified(contexto="reset", max_tent=3):
    """Rotina ÚNICA de limpeza (reusada pelo reset e pelo clear avulso):

    - força a parada do app antes de limpar (em cada tentativa);
    - `pm clear` validado por "Success" na saída E exit_code == 0
      (d.shell() devolve ShellResponse, NUNCA str);
    - retry controlado (max_tent), sem loop infinito;
    - mede a duração e alimenta o [PERF] (clear_count/clear_ms);
    - NUNCA levanta exceção: retorna bool.

    (Corrige o risco de `r` não definida quando d.shell() lança exceção.)"""
    global _last_clear_ms
    log(f"[RESET][APP] clear_data start context={contexto}")
    t_clear = time.perf_counter()
    saiu = ""
    for tent in range(1, max_tent + 1):
        # force-stop antes do pm clear (garante nenhum processo segurando os dados)
        try:
            d.app_stop(package)
            time.sleep(0.5)
            d.shell(f"am force-stop {package}")
        except Exception as e:
            log(f"[RESET][WARN] force_stop {type(e).__name__}: {e}")
        r = None
        try:
            r = d.shell(f"pm clear {package}")
            # d.shell() retorna ShellResponse(.output, .exit_code) — normaliza p/ texto
            saiu = str(getattr(r, "output", r) or "").strip()
        except Exception as e:
            # sem o objeto r: nunca avaliar exit_code aqui (evita NameError)
            saiu = ""
            log(f"[RESET][ERROR] stage=app_data_clear tentativa={tent} {type(e).__name__}: {e}")
        # pm clear retorna "Success" em caso de sucesso (e exit_code 0)
        exit_ok = True if (r is None or isinstance(r, str)) else getattr(r, "exit_code", 0) == 0
        if saiu.lower().startswith("success") and exit_ok:
            ms = int((time.perf_counter() - t_clear) * 1000)
            _last_clear_ms = ms
            perf_inc("clear_count")
            perf_inc("clear_ms", ms)
            log(f"[RESET][APP] clear_data success context={contexto} clear_data_ms={ms} tentativas={tent}")
            return True
        log(f"[RESET][WARN] pm clear não confirmou (saída={saiu!r}) tentativa={tent}/{max_tent} — retry")
        if tent < max_tent:
            time.sleep(2)
    ms = int((time.perf_counter() - t_clear) * 1000)
    _last_clear_ms = ms
    log(f"[RESET][ERROR] stage=app_data_clear context={contexto} clear_data_ms={ms} "
        f"tentativas={max_tent} saida={saiu!r}")
    return False


def clear_app_data_pct():
    """Caminho INDEPENDENTE de clear (clear_data_pct): usa o MESMO helper validado do reset.
    Nunca relança o app aqui (o controlador superior decide). Falha -> _clear_pending."""
    global _fc_coins_last, _clear_pending, _did_clear_this_round
    log("[CLEAN] Limpando dados do app (clear_data_pct)...")
    if _clear_app_data_verified(contexto="clear_data_pct"):
        _did_clear_this_round = True
        _clear_pending = False
        _pending_backoff_reset()
        _fc_coins_last = None   # dados zerados: saldo/textos antigos inválidos
        time.sleep(2)
        return True
    _clear_pending = True
    log("[CLEAN] clear falhou — sem relançar o app (pendência registrada; retry com backoff).")
    return False


def _pending_backoff_reset():
    global _pending_backoff_s
    _pending_backoff_s = 0.0


def _pending_retry_step(op):
    """Repete a operação PENDENTE ("reset"|"clear") com backoff POR DEVICE.
    Retorna True se resolveu. Em falha, dobra o backoff (para o chamador dormir)."""
    global _pending_backoff_s, _reset_pending, _clear_pending
    if _pending_backoff_s <= 0:
        _pending_backoff_s = _PENDING_BACKOFF_BASE_S
    log(f"[PENDING] op={op} tentando resolver pendência (backoff atual={_pending_backoff_s:.0f}s)")
    ok = reset_identity_and_app_state() if op == "reset" else clear_app_data_pct()
    if ok:
        _reset_pending = False
        _clear_pending = False
        _pending_backoff_s = 0.0
        return True
    _pending_backoff_s = min(_pending_backoff_s * 2, _PENDING_BACKOFF_MAX_S)
    return False


def reset_identity_and_app_state():
    """Fluxo OBRIGATÓRIO: reset de ID de publicidade + limpeza completa dos dados do app.
    Ordem: RESET ID -> force-stop -> pm clear (validado) -> estabilização.
    NÃO abre o app — o controlador superior decide quando relançar.
    Retorna True só se reset + pm clear concluídos; False interrompe o ciclo (não abrir o app)."""
    global _fc_coins_last, _did_clear_this_round, _reset_pending
    t_cycle = time.perf_counter()
    log("[RESET][ID] start")
    t_id = time.perf_counter()
    ok_reset = reset_id()          # valida conclusão REAL (falha identificável se não houve reset)
    reset_id_ms = int((time.perf_counter() - t_id) * 1000)
    perf_inc("reset_count")
    perf_inc("reset_ms", reset_id_ms)
    if not ok_reset:
        _reset_pending = True
        log(f"[RESET][ERROR] stage=id_reset | reset_id_ms={reset_id_ms} | CICLO ABORTADO (app não será aberto)")
        return False

    # RESET SEM CLEAR = CICLO INCOMPLETO: limpeza obrigatória pelo mesmo helper do clear avulso.
    # Se o ID já passou e SÓ o clear falhou, a pendência é de CLEAR (não de reset): o retry
    # repete apenas o pm clear, sem re-executar reset_id() desnecessariamente.
    if not _clear_app_data_verified(contexto="reset"):
        _clear_pending = True
        log("[RESET][ERROR] stage=app_data_clear | CICLO ABORTADO "
            "(app não será aberto sem pm clear confirmado; retry repetirá APENAS o clear)")
        return False
    _did_clear_this_round = True
    _reset_pending = False

    # pm clear devolve o app ao estado de primeira execução: saldo/estado antigos são inválidos
    _fc_coins_last = None

    # estabilização (pm clear força parada; dá tempo ao sistema e invalida qualquer estado em UI)
    time.sleep(1.5)
    total = int((time.perf_counter() - t_cycle) * 1000)
    log(f"[RESET][APP] ready_for_launch | reset_id_ms={reset_id_ms} clear_data_ms={_last_clear_ms} "
        f"total_reset_cycle_ms={total}")
    return True

def verificar_e_corrigir_package():
    try:
        current_info = cur_app()
        current_package = current_info.get('package')
        if current_package != package:
            log(f"[ALERTA] Fora do app ({current_package}). Voltando...")
            d.app_stop(current_package)
            d.app_start(package)
            time.sleep(1)
            return cur_app()
        return current_info
    except Exception:
        return None

def check_and_close_errors():
    try:
        if d(resourceId="android:id/aerr_wait").exists(timeout=0.3):
            d(resourceId="android:id/aerr_wait").click()
        if d(resourceId="android:id/aerr_close").exists(timeout=0.3):
            d(resourceId="android:id/aerr_close").click()
    except Exception:
        pass

_CONSENT_ACCEPT = "YES, I AGREE"
_CONSENT_DECLINE = "NO, THANK YOU"
_CONSENT_TITLE = "IMPROVE YOUR AD EXPERIENCE"


def tratar_consentimento_cas(d):
    """Diálogo de consentimento da CAS ('Improve Your Ad Experience'). Detecção FORTE: botão
    'YES, I AGREE' + (título ou 'NO, THANK YOU') no mesmo dump. Clica pelos bounds do botão
    'YES, I AGREE' (accept) e confirma que sumiu. Sem filtro de package: o diálogo pode ser
    renderizado por outra package/overlay conforme a versão do SDK da CAS.
    Retorna True se havia diálogo e ele foi tratado."""
    acao = str(cfg.get("cas_consent_action", "accept")).lower()
    if acao == "off":
        return False
    dump = get_hierarchy_data()
    if dump is None:
        return False
    textos = {}
    for n in dump.iter("node"):
        t = n.attrib.get("text", "").strip().upper()
        if t in (_CONSENT_ACCEPT, _CONSENT_DECLINE, _CONSENT_TITLE):
            textos[t] = n
    if _CONSENT_ACCEPT not in textos or not (_CONSENT_TITLE in textos or _CONSENT_DECLINE in textos):
        return False
    alvo = textos.get(_CONSENT_ACCEPT)
    nums = list(map(int, re.findall(r"\d+", alvo.attrib.get("bounds", "")))) if alvo is not None else []
    if len(nums) != 4:
        log(f"[CONSENT][CAS] detected action={acao} result=target_not_found")
        return False
    cx, cy = (nums[0] + nums[2]) // 2, (nums[1] + nums[3]) // 2
    log(f"[CONSENT][CAS] detected signals={sorted(textos)} action={acao} click=({cx},{cy}) "
        f"bounds={alvo.attrib.get('bounds')}")
    d.click(cx, cy)
    time.sleep(1.5)
    dump2 = get_hierarchy_data()
    ainda = dump2 is not None and any(
        n.attrib.get("text", "").strip().upper() == _CONSENT_ACCEPT
        for n in dump2.iter("node"))
    log(f"[CONSENT][CAS] result={'still_visible' if ainda else 'dismissed'}")
    return not ainda


def processar_home(d):
    try:
        cur = cur_app()
        if not (cur.get('package') == package and '.MainActivity' in cur.get('activity', '')):
            return False
    except Exception:
        return False
    # Consentimento CAS por cima da MainActivity bloqueia o btnRewardAds: tratar antes de procurá-lo
    try:
        tratar_consentimento_cas(d)
    except Exception as e:
        log(f"[CONSENT][CAS] erro={type(e).__name__}: {e}")
    for _ in range(3):
        if d(resourceId="com.flixdigital.dramaclassic:id/btnRewardAds").exists(timeout=1):
            break
        d.swipe_ext("up", 0.3)
        time.sleep(0.5)
    # Saldo ANTES do anúncio (medição de recompensa). Falha não impede o anúncio.
    try:
        dump_m = get_hierarchy_data()
        moedas = _fc_moedas({'nodes': list(dump_m.iter('node'))}) if dump_m is not None else "?"
        _fc_registrar_moedas(moedas)
        flog(f"[FASTCLOSE][REWARD] before={_fc_coins_int(moedas) if moedas != '?' else None} source=processar_home")
    except Exception as e:
        flog(f"[FASTCLOSE][REWARD] before=None source=processar_home erro={type(e).__name__}")
    try:
        if clicar_coordenada_por_seletor("resource-id", ["com.flixdigital.dramaclassic:id/btnRewardAds"]):
            return True
        if clicar_coordenada_por_seletor("text", ["Assistir anúncio"]):
            return True
    except Exception as e:
        log(f"[ERRO] clicar reward: {e}")
    return False

def dump_signature():
    """Assinatura leve do dump (ids+textos+bounds ordenados). Muda quando o banner recarrega."""
    try:
        r = ET.fromstring(_dump_hierarchy_medido())
        sig = []
        for n in r.findall('.//node'):
            rid = n.attrib.get('resource-id', '')
            t = n.attrib.get('text', '')
            b = n.attrib.get('bounds', '')
            if rid or t:
                sig.append((rid, b, t))
        return tuple(sorted(sig))
    except Exception:
        return None


def abrir_anuncio_banner(d, max_tries=6, wait_each=6):
    """Clica 'Assistir anúncio'; se o banner está em recarga (fica em MainActivity),
    detecta via dump quando ele atualizou e re-clica até o anúncio abrir.
    Retorna True quando o anúncio abriu (saiu da MainActivity / abriu Play Store)."""
    for tent in range(max_tries):
        # já abriu?
        try:
            cur = cur_app()
            act = str(cur.get('activity', ''))
            if cur.get('package') == package and '.MainActivity' not in act:
                log("[BANNER] Anúncio já aberto.")
                return True
            if any(s in act for s in ["vending", "finsky", "HsdpAlias"]):
                return True
        except Exception:
            pass

        # garante botão visível
        if not d(resourceId="com.flixdigital.dramaclassic:id/btnRewardAds").exists(timeout=1):
            d.app_start(package)
            time.sleep(1)
            d.swipe_ext("up", 0.3)
            time.sleep(0.5)

        sig_antes = dump_signature()
        log(f"[BANNER] Clique {tent+1}/{max_tries} no botão de anúncio...")
        try:
            if not clicar_coordenada_por_seletor("resource-id", ["com.flixdigital.dramaclassic:id/btnRewardAds"]):
                clicar_coordenada_por_seletor("text", ["Assistir anúncio"])
        except Exception as e:
            log(f"[BANNER] Erro no clique: {e}")
            return False

        # espera curta verificando se abriu (evita espera fixa longa)
        for _ in range(wait_each):
            time.sleep(1)
            try:
                cur = cur_app()
                act = str(cur.get('activity', ''))
                if cur.get('package') == package and '.MainActivity' not in act:
                    log(f"[BANNER] Anúncio abriu ({act}).")
                    return True
                if any(s in act for s in ["vending", "finsky", "HsdpAlias"]):
                    return True
            except Exception:
                pass

        # banner em recarga: espera o dump mudar (=atualizou) antes de reclicar
        if tent < max_tries - 1:
            log("[BANNER] Banner em recarga. Esperando dump atualizar para reclicar...")
            for _ in range(8):
                time.sleep(1)
                sig_depois = dump_signature()
                if sig_depois and sig_depois != sig_antes:
                    log("[BANNER] Banner atualizou (dump mudou). Reclicando...")
                    break
    log("[BANNER] Não conseguiu abrir anúncio após várias tentativas.")
    return False


def tem_barra_progresso_dump(dump_xml):
    """Detecta barra de progresso (recompensa) via dump XML: ProgressBar/SeekBar.
    Barato e instantâneo. Usada primeiro; OpenCV é fallback."""
    if dump_xml is None:
        return False
    for node in dump_xml.iter("node"):
        cls = node.attrib.get("class", "")
        if any(k in cls for k in ["ProgressBar", "SeekBar", "CountDown"]):
            return True
    return False


def tem_barra_progresso_azul(n_amostras=3):
    """Detecta a barra azul de tempo na parte inferior do anúncio via OpenCV
    (fallback quando o dump não expõe a barra). Cor azul HSV H~90-145 no ~10% inferior."""
    try:
        cv2, np = _lazy_cv2()
    except Exception:
        return False
    acertos = 0
    for _ in range(n_amostras):
        try:
            img = d.screenshot()
            buf = io.BytesIO()
            img.save(buf, format='PNG')
            png = buf.getvalue()
            bgr = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
            h, w = bgr.shape[:2]
            hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
            mask = cv2.inRange(hsv, np.array([90, 70, 60]), np.array([145, 255, 255]))
            for y in range(int(h * 0.86), h - 20, 8):
                row = mask[y:y + 14, :]
                strong = int((row.mean(axis=0) > 40).sum())
                if strong / w > 0.15:
                    acertos += 1
                    break
        except Exception:
            pass
        time.sleep(0.3)
    return acertos >= max(1, n_amostras // 2)


def tem_barra_progresso(dump_xml=None, n_amostras=1):
    """Detecta barra de progresso: dump primeiro (rápido), depois OpenCV (fallback)."""
    if tem_barra_progresso_dump(dump_xml):
        return True
    return tem_barra_progresso_azul(n_amostras=n_amostras)


def aguardar_barra_progresso(d, max_wait=None):
    """Aguarda a barra de progresso (rizinho - rewarded) sumir = fim do vídeo.
    Detecção por dump (ProgressBar) é instantânea; OpenCV é fallback.
    Retorna 'main' / 'store' / True (terminou) / None (timeout)."""
    if max_wait is None:
        max_wait = cfg.get("progress_bar_wait_s", 30)
    start = time.time()
    while time.time() - start < max_wait:
        time.sleep(1.5)
        try:
            cur = cur_app()
            act = str(cur.get('activity', ''))
            if cur.get('package') == package and '.MainActivity' in act:
                return 'main'
            if any(s in act for s in ["vending", "finsky", "HsdpAlias"]):
                return 'store'
        except Exception:
            pass
        # dump primeiro (barato)
        dump = get_hierarchy_data()
        tem = tem_barra_progresso(dump, n_amostras=1)
        dump = None
        if not tem:
            log(f"[LOG] Barra de progresso concluída em {time.time()-start:.0f}s.")
            return True
    log(f"[LOG] Barra de progresso ainda ativa após {max_wait}s.")
    return None


def processar_ciclo_anuncio():
    """Ciclo completo: classificar -> assistir -> possivel movimentar -> fechar -> possivel playstore."""
    global total_ads, falhas_consecutivas, _applovin_handled

    start = time.time()
    MAX = 120
    _applovin_handled = False  # novo anúncio: nenhum controlador AppLovin ainda

    # AppLovin: controlador fast_close em T0 (classificação só depois do anúncio aberto)
    if cfg.get("fast_close_enabled", True):
        r = _fc_pre_anuncio()
        if r is not None:
            return r

    # 0) Classifica o tipo de anúncio via IA (premiado vs intersticial vs playstore vs playable)
    ad_type = "interstitial"
    if cfg.get("use_ia", True):
        ad_type = ia_classify_type()
        log(f"[IA] Tipo de anúncio detectado: {ad_type}")
    else:
        log("[LOG] IA desativada. Usando comportamento genérico (assistir e fechar).")

    # 1) FASE ASSISTIR (rewarded button): espera sem tocar na tela.
    #    Se IA diz "rewarded", assiste o tempo total randomizado.
    #    Se "interstitial", assiste apenas o mínimo (min_watch_budget_pct) e fecha.
    #    Se "playstore"/"playable", não precisa assistir.
    watch = CONFIG.rand_in_range(cfg, "watch_time_range")
    if ad_type == "interstitial":
        min_budget = cfg.get("min_watch_budget_pct", 60)
        watch = max(2.0, watch * min_budget / 100.0)
        log(f"[LOG] Anúncio intersticial. Assistindo reduzido: {watch:.1f}s")
    elif ad_type in ("playstore", "playable"):
        watch = 2.0
        log("[LOG] Anúncio playstore/playable. Sem espera longa.")
    else:
        log(f"[LOG] Anúncio premiado. Assistindo por {watch:.1f}s (sem tocar na tela)...")

    assistido = 0
    while time.time() - start < watch:
        time.sleep(1.0)
        assistido += 1
        try:
            cur = cur_app()
            act = str(cur.get('activity', ''))
            if any(s in act for s in ["vending", "finsky", "HsdpAlias"]):
                log("[LOG] Play Store abriu durante espera.")
                break
            if cur.get('package') == package and '.MainActivity' in cur.get('activity', ''):
                # Banner em recarga: usa abrir_anuncio_banner (clica até o anúncio abrir)
                if cfg.get("wait_for_banner", True):
                    log("[LOG] Banner em recarga na MainActivity. Tentando abrir anúncio...")
                    abriu = abrir_anuncio_banner(d)
                    if not abriu:
                        log("[LOG] Banner não carregou mesmo após tentativas. Reiniciando app...")
                        d.app_stop(package)
                        time.sleep(1)
                        d.app_start(package)
                        return False
                    break
                else:
                    log("[LOG] Voltou à MainActivity ainda na espera.")
                    return True
        except Exception:
            pass
        if assistido % 5 == 0:
            log(f"[LOG] Assistindo... {assistido:.0f}s / {watch:.0f}s")

    # 2) FASE MOVIMENTAR (opcional, %) - só para intersticiais/playables, NUNCA rewarded
    #    (interferir no vídeo premiado quebra a recompensa)
    if CONFIG.decide(cfg, "move_ad_pct") and ad_type != "rewarded":
        dur = CONFIG.rand_in_range(cfg, "move_duration_range")
        try:
            cur = cur_app()
            act = str(cur.get('activity', ''))
            if cfg.get("fast_close_enabled", True) and 'applovin' in act.lower():
                log("[APPLOVIN] Movimentação desativada para AppLovin (fast_close P0).")
            elif not any(s in act for s in ["vending", "finsky", "HsdpAlias"]):
                log(f"[LOG] Movimentando anúncio por {dur:.1f}s...")
                movimentar_anuncio(d, dur)
        except Exception:
            pass

    # 3) FASE FECHAMENTO - CIRÚRGICA
    #    - Se o anúncio é "rewarded" (premiado): aguarda o X REAL aparecer
    #      (aguardar_x_recompensa) sem tocar na tela, só então clica 1x no ponto exato.
    #    - Pós-espera, usa o fechamento cirúrgico (cascata de seletores + 1 ação).
    sucesso = False
    try:
        # ===== APPLOVIN PRIMEIRO (estratégia específica, aditiva) =====
        # Antes do fluxo rewarded/interstitial genérico: se o anúncio é AppLovin,
        # usa a estratégia dedicada (janelas próprias) ou a lógica legada
        # (main_market) se habilitada. Se resolver, encerra; se não (False),
        # segue o fluxo normal abaixo como fallback.
        if eh_anuncio_applovin():
            if cfg.get("fast_close_enabled", True):
                # virou AppLovin depois do início do ciclo -> controlador único assume
                return _fc_contabilizar(processar_applovin_fast("fase_fechamento"))
            fechou_applovin =fechar_anuncio_applovin_legacy(d) if cfg.get("use_legacy_applovin", True) else fechar_anuncio_applovin(d)
            if fechou_applovin:
                total_ads += 1
                falhas_consecutivas = 0
                return True
            # fallback: continua o fluxo existente

        # Se durante a espera abriu Play Store / voltou ao app já foi tratado acima.
        # Aqui só fechamos o anúncio de verdade.
        if ad_type == "rewarded":
            # Aguarda a barra de progresso encher (vídeo premiado), SEM tocar na tela.
            # Se não houver barra, cai no aguardar_x_recompensa.
            max_rest = min(MAX - (time.time() - start), 60)
            if tem_barra_progresso_azul(2):
                log("[LOG] Barra de progresso (azul) detectada. Aguardando completar...")
                res_barra = aguardar_barra_progresso(d, max_wait=min(max_rest, cfg.get("progress_bar_wait_s", 30)))
                if res_barra == 'main':
                    total_ads += 1
                    falhas_consecutivas = 0
                    return True
                if res_barra == 'store':
                    tratar_play_store(d)
                    total_ads += 1
                    falhas_consecutivas = 0
                    return True
                # barra concluída: prossegue para aguardar o X
            dump_inicial = None

            # Aguarda o fim do vídeo: X fica visível/clicável. NUNCA derruba o app.
            max_rest = min(MAX - (time.time() - start), 60)
            resulto = aguardar_x_recompensa(d, max_wait=max_rest)
            if resulto == 'main':
                total_ads += 1
                falhas_consecutivas = 0
                return True
            if resulto == 'store':
                tratar_play_store(d)
                total_ads += 1
                falhas_consecutivas = 0
                return True
            if isinstance(resulto, tuple):
                # X encontrado após assistir completo: 1 clique cirúrgico
                log(f"[LOG] Reward completo. Clique no X em ({resulto[0]},{resulto[1]}) (1x).")
                d.click(resulto[0], resulto[1])
                time.sleep(1.5)
                # se abriu loja, volta
                voltar_de_externo(d, max_backs=2)
                try:
                    cur = cur_app()
                    if cur.get('package') == package and '.MainActivity' in cur.get('activity', ''):
                        total_ads += 1
                        falhas_consecutivas = 0
                        return True
                except Exception:
                    pass

        # Fechamento cirúrgico geral (interstitial/playable/outros)
        if fechar_anuncio_cirurgico(d):
            total_ads += 1
            falhas_consecutivas = 0
            sucesso = True
    except Exception as e:
        log(f"[ERRO] fase fechamento: {e}")

    if not sucesso:
        log("[LOG] Falha ao fechar anúncio. Salvando erro + reiniciando...")
        falhas_consecutivas += 1
        if cfg.get("save_errors", True):
            salvar_error_screenshot("fail")
        try:
            d.app_stop(package)
            time.sleep(1)
        except Exception:
            pass
    return sucesso

# ==================== HEARTBEAT (FASE 2, default OFF) ====================
_hb_last = 0.0
_hb_cycle_start = time.monotonic()
_hb_last_op = "cycle_start"
_hb_last_state = "RUNNING"
_hb_dump_start = None            # monotônico do dump em andamento (None = fora de dump)
_HB_DUMP_STALL_S = 30.0          # acima disso o dump é considerado TRAVADO (progresso congela)


def _hb_write(state="RUNNING", op="", last_dump_ok=True, force=False, progress_ts=None):
    """Publica heartbeat barato (JSON atômico) para o supervisor externo (watchdog.py).

    Semântica de progresso (FASE 12/13): por padrão `last_progress=agora` (operação legítima —
    incluindo backoff/no-fill/countdown). Se `progress_ts` for passado, ele é usado (permite
    CONGELAR o progresso quando um dump está travado)."""
    global _hb_last, _hb_last_op, _hb_last_state
    if not cfg.get("heartbeat_enabled", False):
        return
    now = time.monotonic()
    if not force and (now - _hb_last) < float(cfg.get("heartbeat_interval_s", 5.0)):
        return
    _hb_last = now
    if op:
        _hb_last_op = op
    _hb_last_state = state
    lp = now if progress_ts is None else progress_ts
    try:
        import watchdog as WD
        hb_dir = os.path.join(BASE_DIR, cfg.get("heartbeat_dir", "heartbeats"))
        logcat_child = None
        try:
            if _fc_logcat_reader and getattr(_fc_logcat_reader, "proc", None):
                logcat_child = _fc_logcat_reader.proc.pid
        except Exception:
            logcat_child = None
        WD.write_heartbeat(WD.heartbeat_path(hb_dir, device), {
            "device": device, "pid": os.getpid(), "mono_ts": round(now, 3),
            "instance_id": WORKER_INSTANCE_ID, "generation": WORKER_GENERATION,
            "state": state, "current_operation": op,
            "cycle_start": round(_hb_cycle_start, 3), "last_progress": round(lp, 3),
            "last_dump_ok": bool(last_dump_ok), "errors": int(_perf.get("errors", 0)),
            "logcat_child_pid": logcat_child,
            "host_id": cfg.get("host_id") or os.environ.get("SLN_HOST_ID", ""),
            "group": cfg.get("device_group") or os.environ.get("SLN_DEVICE_GROUP", ""),
        })
    except Exception:
        pass


def _hb_loop():
    """Ticker de heartbeat: mantém `heartbeat_at` fresco e avança `last_progress` em operações
    legítimas (inclusive sleeps longos de backoff) — MAS congela o progresso quando um
    dump_hierarchy está travado (FASE 13: 'vivo porém bloqueado')."""
    iv = float(cfg.get("heartbeat_interval_s", 5.0)) or 5.0
    while True:
        try:
            time.sleep(iv)
            now = time.monotonic()
            if _hb_dump_start is not None and (now - _hb_dump_start) > _HB_DUMP_STALL_S:
                _hb_write("RUNNING", "dump_hierarchy", last_dump_ok=False, force=True,
                          progress_ts=_hb_dump_start)
            else:
                op = "dump_hierarchy" if _hb_dump_start is not None else (_hb_last_op or "watcher")
                _hb_write(_hb_last_state or "RUNNING", op, force=True)
        except Exception:
            pass


def _hb_mark_dump_start():
    global _hb_dump_start
    _hb_dump_start = time.monotonic()


def _hb_mark_dump_end():
    global _hb_dump_start
    _hb_dump_start = None


if __name__ == '__main__':
    safe_connect(device)
    set_window_title(f"Dispositivo: {device} | Drama Classic")
    log(f"[APPLOVIN][CONFIG] legacy={bool(cfg.get('use_legacy_applovin'))} "
        f"fast_close={bool(cfg.get('fast_close_enabled'))} tick={cfg.get('fast_close_tick_ms')}ms "
        f"timeout={cfg.get('applovin_fast_close_timeout_seconds')}s "
        f"rewarded_min_watch={cfg.get('applovin_rewarded_min_watch_seconds')}s "
        f"transient={bool(cfg.get('transient_x_enabled'))} miss_ticks={cfg.get('transient_x_miss_ticks')} "
        f"max_reveals={cfg.get('transient_x_max_reveals')} use_ia={bool(cfg.get('use_ia'))} "
        f"experiment={cfg.get('top_control_experiment_mode')} "
        f"skip_to_endcard={bool(cfg.get('applovin_skip_to_endcard_enabled'))} "
        f"endcard_structural_x={bool(cfg.get('applovin_endcard_structural_x_enabled'))}")
    # FASE 3: inicia o reader persistente de logcat no boot (fica pronto antes do 1º anúncio).
    if cfg.get("persistent_logcat_enabled", False):
        _fc_ensure_logcat_reader()
        # Ownership do subprocesso logcat: encerra-o no shutdown do bot (não deixa órfão em saída limpa).
        import atexit
        def _sln_stop_logcat():
            try:
                if _fc_logcat_reader and hasattr(_fc_logcat_reader, "stop"):
                    _fc_logcat_reader.stop()
            except Exception:
                pass
        atexit.register(_sln_stop_logcat)

    # QA REALISTIC: monta o agendador por device (persistente). Default OFF.
    if cfg.get("qa_realistic_behavior_enabled", False):
        try:
            import qa_behavior as QA
            _qa_pop = int(os.environ.get("SLN_QA_POPULATION", "1") or 1)
            qa_scheduler, _qa_cfg = QA.build_scheduler(device, cfg, base_dir=BASE_DIR,
                                                       log_fn=log, population_fn=lambda: _qa_pop)
            qa_scheduler.ensure_initialized()
            log("[QA][PROFILE] qa_realistic enabled seed=%s delay=%g..%gs reset=%d..%d cycles "
                "pct=%g..%g install=%g..%g%% cooldown=%gh pop=%d state=%s" % (
                    _qa_cfg.seed, _qa_cfg.delay_min_s, _qa_cfg.delay_max_s, _qa_cfg.reset_min_cycles,
                    _qa_cfg.reset_max_cycles, _qa_cfg.reset_pct_min, _qa_cfg.reset_pct_max,
                    _qa_cfg.install_pct_min, _qa_cfg.install_pct_max, _qa_cfg.install_cooldown_h,
                    _qa_pop, qa_scheduler.state.reset_interval_cycles))
            import atexit as _qa_atexit
            _qa_atexit.register(lambda: (qa_scheduler.emit_summary(), qa_scheduler.save()))
        except Exception as e:
            qa_scheduler = None
            log(f"[QA][ERROR] falha ao iniciar scheduler: {type(e).__name__}: {e}")

    rodadas_local = 0
    # AD SESSION / CYCLE: 1 clique "Assistir anúncio" = 1 ciclo (com N stages até a MAIN).
    _cycle = ad_session.CycleSession(device, log)
    # Telemetria de escala: um resumo [PERF] a cada ~60s (não loga por tick)
    threading.Thread(target=_perf_loop, daemon=True).start()
    # Ticker de heartbeat (só relevante com heartbeat_enabled): progresso legítimo + dump travado.
    if cfg.get("heartbeat_enabled", False):
        threading.Thread(target=_hb_loop, daemon=True).start()
    while True:
        try:
            _hb_cycle_start = time.monotonic()
            _hb_write("RUNNING", "cycle_start")
            # DPN: gate ANTES de qualquer NOVO ciclo (delay/reset/app/clique). Só bloqueia NOVO
            # ciclo; NUNCA interrompe sessão ativa (condição) nem reset/clear em andamento.
            if DPN_ENABLED and dpn_worker is not None \
                    and not (_cycle is not None and _cycle.active) \
                    and not _reset_pending and not _clear_pending:
                try:
                    if not dpn_worker.gate(device, DPN_INSTANCE_ID):
                        _hb_write("RUNNING", "dpn_wait", force=True)
                        time.sleep(2.0)
                        continue
                except Exception as _dpn_e:
                    log(f"[DPN][ERROR] gate: {type(_dpn_e).__name__}: {_dpn_e}")
            # QA REALISTIC (camada de agendamento; NÃO toca a state machine do anúncio):
            # delay + decisão de reset APENAS ENTRE CICLOS (nunca com ad-session ativa).
            if qa_scheduler is not None and not (_cycle is not None and _cycle.active):
                _qa_plan = qa_scheduler.begin_cycle()
                if _qa_plan.get("action") == "RESET_ALLOWED":
                    _qa_ok = reset_identity_and_app_state()   # RESET ID + pm clear (acoplados)
                    qa_scheduler.on_reset_result(_qa_ok)
                    qa_scheduler.save()
                    if not _qa_ok:
                        continue
                    # TEST-ONLY: encerra após N RESET TREES concluídas (safety cap = runtime).
                    _qa_target = int(os.environ.get("SLN_QA_TEST_TARGET_RESET_TREES", "0") or 0)
                    if _qa_target and qa_scheduler.state.reset_success >= _qa_target:
                        log(f"[QA][TEST] target reset trees atingido "
                            f"({qa_scheduler.state.reset_success}/{_qa_target}) — encerrando.")
                        _cycle_abort_if_active("test_terminated_reset_target")
                        break
            is_doze(d)
            # Consentimento CAS pode aparecer a qualquer momento (pós-clear/primeiro launch):
            # clicar "YES, I AGREE" SEMPRE que surgir, independente do estado atual.
            try:
                tratar_consentimento_cas(d)
            except Exception as e:
                log(f"[CONSENT][CAS] erro_loop={type(e).__name__}: {e}")
            # PENDÊNCIA de reset/clear: NÃO reabre o app como se nada tivesse acontecido.
            # Repete a operação pendente com backoff NESTE device (outros bots não são afetados).
            if _reset_pending or _clear_pending:
                op = "reset" if _reset_pending else "clear"
                if not _pending_retry_step(op):
                    log(f"[PENDING] op={op} continua pendente; aguardando {_pending_backoff_s:.0f}s "
                        f"(app NÃO foi aberto)")
                    time.sleep(_pending_backoff_s)
                    continue
                log(f"[PENDING] op={op} resolvida — retomando o ciclo normal.")
            _did_clear_this_round = False   # nova rodada: nenhum clear ainda

            if _cycle is not None and _cycle.active:
                # CONTINUAÇÃO da MESMA ad-session: NÃO reabrir o app nem clicar
                # "Assistir anúncio" de novo (1 clique = 1 ciclo, N stages).
                pass
            else:
                d.app_start(package)
                time.sleep(1)
                cur = cur_app()
                if cur.get('package') == package:
                    log("Aplicativo iniciado com sucesso.")
                activity = cur.get('activity', '')
                if '.MainActivity' in activity:
                    if not processar_home(d):
                        log("[AVISO] Botão reward não encontrado. Reiniciando app...")
                        d.app_stop(package)
                        time.sleep(1)
                        continue
                    # Aguarda sair da MainActivity (abrir ad)
                    for _ in range(12):
                        time.sleep(1)
                        try:
                            cur = cur_app()
                            if cur['package'] != package or '.MainActivity' not in cur['activity']:
                                break
                        except Exception:
                            break
                    # Saiu da MAIN após o clique => a AD-SESSION começou (1 clique = 1 ciclo).
                    if _cycle is not None and not _cycle_on_main():
                        _cycle.begin(activity=_cycle_activity(), ad_format=_last_ad_format)
                        if qa_scheduler is not None:
                            qa_scheduler.mark_session_started()

            _ad_ret = processar_ciclo_anuncio()
            rodadas_local += 1
            rodadas += 1
            # FRONTEIRA DE CICLO: COMPLETED (MAIN confirmada) / CONTINUE (outro stage) /
            # FAILED (escape forçado). qa.cycle_completed() só roda em COMPLETED.
            # DPN: captura o cycle_id da sessão ANTES da transição terminal (não altera CycleSession).
            _dpn_cid = _cycle.cycle_id if (_cycle is not None and _cycle.active) else None
            _dpn_res = None
            if _cycle is not None:
                _dpn_res = _cycle_after_ad(_ad_ret)
            # DPN: hook ADITIVO após o resultado terminal — o main não conhece o contador DPN.
            if DPN_ENABLED and dpn_worker is not None and _dpn_res in ("COMPLETED", "FAILED"):
                try:
                    dpn_worker.on_terminal_cycle(device_id=device, instance_id=DPN_INSTANCE_ID,
                                                 cycle_id=_dpn_cid, result=_dpn_res)
                except Exception as _dpn_e:
                    log(f"[DPN][ERROR] on_terminal_cycle: {type(_dpn_e).__name__}: {_dpn_e}")

            # CICLO DE TESTE: encerra após processar o limite de anúncios (via argv 3; 0 = ilimitado)
            if TEST_LIMIT_ADS and total_ads >= TEST_LIMIT_ADS:
                _cycle_abort_if_active("test_terminated_ad_limit")
                log(f"[TESTE] Limite de {TEST_LIMIT_ADS} anúncios alcançado. Encerrando ciclo de teste...")
                break

            # Guards de teste por env: ciclos máximos e tempo máximo de execução (0 = ilimitado)
            if SLN_TEST_MAX_CYCLES and rodadas >= SLN_TEST_MAX_CYCLES:
                _cycle_abort_if_active("test_terminated_max_cycles")
                log(f"[TESTE] Limite de {SLN_TEST_MAX_CYCLES} ciclos alcançado (SLN_TEST_MAX_CYCLES). Encerrando...")
                break
            if SLN_TEST_MAX_RUNTIME_S and (time.time() - _t_startup) >= SLN_TEST_MAX_RUNTIME_S:
                _cycle_abort_if_active("test_terminated_runtime")
                log(f"[TESTE] Limite de {SLN_TEST_MAX_RUNTIME_S}s de execução alcançado (SLN_TEST_MAX_RUNTIME_S). Encerrando...")
                break

            # Reset de ID por % (rodadas) — OBRIGA limpeza de dados do app (RESET SEM CLEAR = CICLO INCOMPLETO)
            # Sob QA realistic, o agendador é a autoridade de reset (intervalo individual + quota).
            if qa_scheduler is None and CONFIG.decide(cfg, "reset_id_pct"):
                if reset_identity_and_app_state():
                    d.app_start(package)
                    time.sleep(2)
                    _fc_coins_last = None  # dados do app zerados: saldo antigo inválido
                else:
                    log("[RESET] reset/limpeza falhou — pendência registrada; sem abrir o app no próximo ciclo.")
                    continue

            # Limpar dados por % (rodadas) — MESMO helper validado do reset.
            # Se o reset desta rodada JÁ limpou, não repetir o clear (did_clear_this_round).
            # Sob QA realistic, o clear avulso NÃO roda (o reset do agendador já acopla reset+clear).
            if qa_scheduler is None and _did_clear_this_round:
                if CONFIG.decide(cfg, "clear_data_pct"):
                    log("[CLEAN] clear_data_pct pulado: o reset desta rodada já limpou os dados do app.")
            elif qa_scheduler is None and CONFIG.decide(cfg, "clear_data_pct"):
                if clear_app_data_pct():
                    try:
                        d.app_start(package)
                        time.sleep(2)
                    except Exception:
                        pass
                else:
                    # falha de clear: não relança o app no próximo ciclo (pendência com backoff)
                    continue

            # Limpeza de memória periódica
            if rodadas_local % 30 == 0:
                gc.collect()
                try:
                    d.app_stop(package)
                    d.shell(f"am force-stop {package}")
                    time.sleep(0.5)
                except Exception:
                    pass

            log(f"[STAT] ads={total_ads} installs={total_installs} moves={total_moves} escapes={total_escapes} falhas={falhas_consecutivas}")

        except Exception as e:
            perf_inc("errors")
            _hb_write("DISCONNECTED", "reconnect", last_dump_ok=False, force=True)
            _cycle_abort_if_active("exception")
            log(f"Erro durante execução: {e}")
            log("Tentando reconectar...")
            try:
                safe_connect(device)
            except Exception:
                time.sleep(30)