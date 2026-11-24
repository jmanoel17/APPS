#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
config_sln_v2.py - Configuração compartilhada do SLN V2 (bot + interface)

Todos os parâmetros de comportamento ficam num JSON (config_sln_v2.json).
A interface edita; o bot lê. As percentagens são interpretadas por rodada
(0-100) e o bot decide via random.intervalos permitem variar o tempo.

Estrutura:
  watch_time_range: [min, max] segundos para ASSISTIR anúncio premiado/rewarded
    antes de fechar. Se IA classificou intersticial, pode fechar de imediato.
  download_apps_pct: percentual de anúncios em que o bot INSTALA o app sugerido.
  move_ad_pct: percentual de anúncios em que o bot "movimenta" a página (scroll/swipes)
     antes do fechamento, para não parecer padrão.
  move_duration_range: [min, max] segundos de interação (scroll/swipe).
  reset_id_pct: percentual de RODADAS em que reseta o ID de publicidade.
  clear_data_pct: percentual de RODADAS em que limpa dados do app.
  use_ia: usar servidor central de IA para detectar X e classificar tipo.
  ia_url: endereço do servidor IA.
  save_errors: salvar screenshot em errors/ quando falhar fechar anúncio.
  errors_dir: pasta (relativa a SCRIPT_DIR, default 'errors').
  min_watch_budget_pct: segurança - mesmo intersticial aguarda pelo menos este
    percentual do watch_time antes de tentar o X (evita fechar recompensa antes da hora).

AppLovin — Fast Close Watcher (P0):
  use_legacy_applovin: rollback p/ fechar_anuncio_applovin_legacy (só vale com fast_close_enabled=false).
  fast_close_enabled: controlador único AppLovin (1 dump por tick, clique só com evidência).
  fast_close_tick_ms: período-alvo do tick (dorme só o restante do período).
  fast_close_log_every_tick: loga cada tick (dump/parse em ms) — útil durante a validação.
  applovin_fast_close_timeout_seconds: timeout ÚNICO por anúncio AppLovin.
  applovin_rewarded_min_watch_seconds: rewarded/unknown só ficam ARMED após este tempo.
  fast_close_top_region_ratio: centro do X precisa estar acima de height*ratio.
  fast_close_max_width_ratio / fast_close_max_height_ratio: X maior que isso = oversized.
  fast_close_click_cooldown_ms / fast_close_max_close_clicks: limites de clique no mesmo anúncio.
  transient_x_*: X que some sozinho (ver fast_close_watcher_applovin).
  applovin_reveal_tap_x_ratio / applovin_reveal_tap_y_ratio: ponto preferido do reveal tap.
  top_control_experiment_mode / top_reveal_* / top_control_observe_seconds: EXPERIMENTO da
    pílula superior "▶| Google Play" (observação separada do X). "off" = produção inalterada.
  cas_consent_action: diálogo de consentimento da CAS ('Improve Your Ad Experience') na home:
    "accept" (YES, I AGREE), "decline" (NO, THANK YOU) ou "off" (não toca). Detecção forte.
  applovin_skip_to_endcard_enabled: VIDEO -> top reveal -> ícone ▶| (SKIP) -> loja esperada ->
    BACK -> ENDCARD. Só age com o watcher ARMED. false = sem reveal/skip no vídeo.
  applovin_endcard_structural_x_enabled / applovin_endcard_cascade_enabled / applovin_endcard_ai_*:
    fechamento no ENDCARD (X estrutural anônimo -> XML -> rotinas antigas -> XPath -> YOLO).
  applovin_expected_store_window_s: loja até N s após o SKIP = esperada (não é falso clique).
  applovin_endcard_click_verify_s: tempo para o X do endcard resultar em MainActivity.
  applovin_endcard_x_not_found_s: endcard sem X por N s -> artefato endcard_x_not_found.
  applovin_skip_max_per_ad: máximo de toques SKIP por anúncio.
  applovin_reward_safe_mode / applovin_reward_safe_min_watch_seconds: SKIP (e o reveal para
    obtê-lo) só depois de N s assistidos. Vale para rewarded/unknown; interstitial só fica fora
    se applovin_reward_safe_trust_interstitial=true (classificação ainda não é confiável).
    applovin_rewarded_min_watch_seconds (30) continua só como ARMED do X genérico.
  applovin_store_initial_back_delay_s / _back_retry_interval_s / _max_backs: BACK adaptativo na
    loja esperada (espera estabilizar, BACK, verifica, repete só se ainda na loja).
  applovin_endcard_ai_grace_s: YOLO só se TODAS as camadas locais falharem por N s no endcard.
  applovin_store_back_settle_s: após um BACK, nenhum outro BACK por N s (a volta leva ~0.4s).
  applovin_reward_completion_required: SKIP só com REWARD_COMPLETED observado (countdown
    confirmado + terminou). Sem isso NÃO há SKIP, nem por tempo (min_watch vira telemetria).
  applovin_countdown_*: detector de contagem (região inferior, nó pequeno, número; confirmação
    por sequência descendente) e fim (sumiu por N ticks com último valor <= end_max_value).
  applovin_reward_completion_pill_wait_s: após o fim da contagem, espera a pílula espontânea
    por N s antes de usar o reveal superior.
  applovin_format_from_logcat: formato pelo evento do app (AdHelperLog FORMAT=X EVENT=SHOWED) ou
    CAS.AI; interstitial confirmado assim não tem reward gate. max_age/retry/max_tries da leitura.
  applovin_interstitial_control_wait_s: interstitial sem pílula espontânea por N s desde o START
    -> REVEAL_TOP (contingência). Pílulas espontâneas observadas em T+10..18s.
  applovin_skip_min_pill_age_s: pílula precisa estar em 2 dumps seguidos (~1 tick) antes do SKIP.
"""
import json
import os
import random

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config_sln_v2.json")


def _script_dir():
    return os.path.dirname(os.path.abspath(__file__))


def _resolve_config_file():
    """Config BASE. Default: config_sln_v2.json (config de desenvolvimento).

    Permite um PERFIL completo sem destruir o JSON de desenvolvimento:
        SLN_CONFIG_FILE=meu_perfil.json   (relativo ao dir do script ou absoluto)
    Um caminho inexistente é ignorado (volta ao default). Compatibilidade preservada: sem a
    variável de ambiente, o comportamento é exatamente o de antes."""
    env = os.environ.get("SLN_CONFIG_FILE")
    if env:
        p = env if os.path.isabs(env) else os.path.join(_script_dir(), env)
        if os.path.exists(p):
            return p
    return CONFIG_FILE


def _resolve_overlay_file():
    """Overlay OPCIONAL mesclado SOBRE a base (ex.: config_benchmark_sln_v2.json).

    Diferente de SLN_CONFIG_FILE (que SUBSTITUI a base), o overlay apenas sobrescreve as
    chaves que ele contém, preservando o resto do perfil de desenvolvimento — corrigindo o
    vazamento de valores como download_apps_pct (5 no dev). Ativado por:
        SLN_CONFIG_OVERLAY=config_benchmark_sln_v2.json
    Um caminho inexistente é ignorado (sem overlay)."""
    env = os.environ.get("SLN_CONFIG_OVERLAY")
    if env:
        p = env if os.path.isabs(env) else os.path.join(_script_dir(), env)
        if os.path.exists(p):
            return p
    return None


def _read_json(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def _merge(data, cfg):
    if data:
        cfg.update({k: v for k, v in data.items() if k in DEFAULTS})
    return cfg

DEFAULTS = {
    "watch_time_range": [5, 12],
    "download_apps_pct": 25,
    "move_ad_pct": 35,
    "move_duration_range": [2, 8],
    "scroll_direction": "alternate",
    "reset_id_pct": 8,
    "clear_data_pct": 5,
    "use_ia": True,
    "ia_url": "http://localhost:8000",
    "save_errors": True,
    "errors_dir": "errors",
    "min_watch_budget_pct": 60,
    "install_wait_s": 20,
    "max_ads_before_clear": 100,
    "banner_wait_tries": 6,
    "banner_timeout_s": 30,
    "progress_bar_wait_s": 30,
    "wait_for_banner": True,
    "use_legacy_applovin": False,
    "fast_close_enabled": True,
    "fast_close_tick_ms": 300,
    "fast_close_log_every_tick": True,
    "applovin_fast_close_timeout_seconds": 90,
    "applovin_rewarded_min_watch_seconds": 30,
    "fast_close_top_region_ratio": 0.15,
    "fast_close_max_width_ratio": 0.20,
    "fast_close_max_height_ratio": 0.10,
    "fast_close_click_cooldown_ms": 700,
    "fast_close_max_close_clicks": 6,
    "fast_close_hidden_controls_reveal_enabled": False,
    "hidden_controls_min_age_s": 5.0,
    "max_reveal_attempts_per_stage": 1,
    "reveal_observation_window_ms": 1000,
    "hidden_controls_require_history": True,
    "hidden_controls_use_ui_control_history": True,
    "hidden_controls_prefer_top_center": False,
    "hidden_controls_top_reveal_y_ratio": 0.075,
    "transient_x_enabled": True,
    "transient_x_miss_ticks": 3,
    "transient_x_min_visible_seconds": 2.0,
    "transient_x_max_visible_seconds": 10.0,
    "transient_x_max_reveals": 2,
    "transient_x_reveal_wait_seconds": 2.5,
    "transient_x_reveal_cooldown_seconds": 2.0,
    "applovin_reveal_tap_x_ratio": 0.5,
    "applovin_reveal_tap_y_ratio": 0.45,
    # EXPERIMENTO pílula "▶| Google Play" (off | reveal_only | reveal_and_icon). Não é produção.
    "top_control_experiment_mode": "off",
    "top_reveal_y_ratio": 0.075,
    "top_reveal_x_ratios": [0.50, 0.65, 0.80, 0.35],
    "top_reveal_wait_seconds": 3.0,
    "top_reveal_cooldown_seconds": 5.0,
    "top_reveal_max_per_ad": 2,
    "top_reveal_after_lost_seconds": 1.0,
    "top_control_observe_seconds": 6.0,
    # Fluxo AppLovin VIDEO -> reveal -> pílula -> SKIP -> loja -> BACK -> ENDCARD -> cascata de X
    "applovin_skip_to_endcard_enabled": False,
    "cas_consent_action": "accept",
    "applovin_endcard_structural_x_enabled": True,
    "applovin_endcard_cascade_enabled": True,
    "applovin_endcard_ai_enabled": True,
    "applovin_endcard_ai_interval_s": 1.5,
    "applovin_endcard_ai_min_conf": 0.25,
    "applovin_expected_store_window_s": 3.0,
    "applovin_endcard_click_verify_s": 1.5,
    "applovin_endcard_x_not_found_s": 10.0,
    "applovin_skip_max_per_ad": 2,
    # Recompensa: SKIP só depois da janela mínima (rewarded/unknown). 60s = dado real (T+31 -> 0, T+60 -> +10)
    "applovin_reward_safe_mode": True,
    "applovin_reward_safe_min_watch_seconds": 60,
    "applovin_reward_safe_trust_interstitial": False,
    # Loja esperada pós-SKIP: BACK adaptativo por estado
    "applovin_store_initial_back_delay_s": 1.5,
    "applovin_store_back_retry_interval_s": 1.0,
    "applovin_store_back_settle_s": 0.8,
    # Recompensa por CONCLUSÃO observada (countdown), não por tempo
    "applovin_reward_completion_required": True,
    "applovin_reward_completion_pill_wait_s": 3.0,
    "applovin_countdown_region_min_y_ratio": 0.80,
    "applovin_countdown_max_side_ratio": 0.12,
    "applovin_countdown_max_value": 120,
    "applovin_countdown_miss_ticks": 3,
    "applovin_countdown_end_max_value": 2,
    # Formato real (evento do app/CAS no logcat) e prioridade DETECTAR > AGIR > REVELAR
    "applovin_format_from_logcat": True,
    "applovin_format_logcat_max_age_s": 30,
    "applovin_format_retry_s": 2.0,
    "applovin_format_max_tries": 3,
    "applovin_interstitial_control_wait_s": 20.0,
    "applovin_skip_min_pill_age_s": 0.25,
    "applovin_store_max_backs": 3,
    # YOLO no endcard só após as camadas locais falharem por este tempo (X surge ~2-3s após o BACK)
    "applovin_endcard_ai_grace_s": 4.0,
    # DIAGNÓSTICO (item #3): rewarded sem countdown/pílula (playable) é observado além do timeout
    # normal, sem liberar SKIP por tempo. 0 = usa applovin_fast_close_timeout_seconds (produção).
    # Apenas prolonga a OBSERVAÇÃO; o gate permanece estrutural/comportamental.
    "rewarded_no_countdown_diag_timeout_s": 180,
    # DIAGNÓSTICO (item #9): permite clicar no controle de fechamento do playable quando ele
    # transicionar para onscreen (confirmado em 2 dumps). Somente dentro do caminho de diagnóstico
    # (rewarded sem countdown + assinatura MW_PLFRAME + close offscreen). Falso = produção.
    "playable_diag_click_enabled": False,
    # RECOVERY CLOSE CASCADE (pós-timeout, antes do escape): tenta fechar o anúncio pelos métodos
    # complementares em vez de HOME+force-stop. NUNCA declara reward_completed — só encerra a tela.
    # recovery_close_enabled: liga/desliga a cascata inteira.
    # recovery_close_ai_last_enabled: permite IA/YOLO como último locator do recovery.
    # recovery_close_observe_s: janela de observação pós-clique (MAIN? Store? Endcard? reward?).
    "recovery_close_enabled": True,
    "recovery_close_ai_last_enabled": True,
    "recovery_close_observe_s": 5.0,
    "recovery_close_max_attempts": 1,
    # ===== FASE DE ESCALA (flags; todas default OFF = comportamento atual) =====
    # FASE 3/4: logcat contínuo por device (sem subprocesso por lookup). Default OFF até regressão.
    "persistent_logcat_enabled": False,
    "persistent_logcat_pid_check_s": 3.0,
    # FASE 5/6: tick do watcher variável por estado. Default OFF = tick fixo (fast_close_tick_ms).
    "adaptive_tick_enabled": False,
    "adaptive_tick_ms": {},          # {} = usa DEFAULT_TICK_MS do adaptive_tick.py
    "adaptive_tick_burst_ms": 250,
    "adaptive_tick_burst_seconds": 3.0,
    # FASE 2: heartbeat por device + supervisor externo (watchdog.py). Default OFF.
    "heartbeat_enabled": False,
    "heartbeat_dir": "heartbeats",
    "heartbeat_interval_s": 5.0,
    "watchdog_enabled": False,
    "watchdog_stale_s": 120.0,
    "watchdog_restart_max": 3,
    # FASE 7: backoff crescente + jitter para devices sem fill (NO_AD/banner recharge).
    "idle_backoff_enabled": False,
    "idle_backoff_base_s": 30,
    "idle_backoff_max_s": 300,
    "idle_backoff_jitter_s": 15,
    # FASE 10: static sharding (HOST_ID / grupo). Não obriga descoberta global.
    "host_id": "",
    "device_group": "",
    # ===== QA REALISTIC BEHAVIOR (camada de simulação/agendamento; default OFF) =====
    # NÃO é a state machine do anúncio. Ativada só pelo perfil qa_realistic.
    "qa_realistic_behavior_enabled": False,
    "qa_action_delay_min_s": 10,
    "qa_action_delay_max_s": 60,
    "qa_reset_interval_min_cycles": 2,
    "qa_reset_interval_max_cycles": 30,
    "qa_reset_population_pct_min": 5,
    "qa_reset_population_pct_max": 25,
    "qa_max_concurrent_resets": 2,
    "qa_reset_start_jitter_min_s": 0,
    "qa_reset_start_jitter_max_s": 15,
    "qa_install_pct_min": 5,
    "qa_install_pct_max": 10,
    "qa_install_cooldown_hours": 24,
    "qa_random_seed": None,
    "qa_reset_window_minutes": 30,
    "qa_install_window_hours": 24,
    # ===== SUB_AD_CLOSE (WebView sub-ad): detector estrutural topo-direito (evidência real) =====
    "applovin_subad_close_enabled": True,
    "applovin_subad_close_max_per_ad": 2,
    "applovin_subad_close_cooldown_s": 1.0,
    "applovin_subad_close_pending_timeout_s": 8.0,
    # ===== DPN / ORQUESTRAÇÃO DE IP (camada ACIMA dos workers; default OFF) =====
    # Não toca Fast Close/reward/SUB_AD_CLOSE/CycleSession/CYCLE_ACCOUNTING_V2.
    "dpn_routing_enabled": False,
    "dpn_work_units_per_ip": 20,
    "dpn_ip_reuse_cooldown_s": 3600,
    "dpn_rotation_poll_s": 2,
    "dpn_rotation_timeout_s": 60,
    "dpn_network_settle_s": 5,
    "dpn_duplicate_rotations_before_node_reset": 5,
    "dpn_drain_timeout_s": 120,
    "dpn_coordinator_tick_s": 1.0,
    "dpn_state_dir": os.path.join("state", "dpn"),
    "dpn_groups_file": "dpn_groups.json",
    # ===== DPN E2.6 GROUP LEDGER (default OFF; legacy path preserved) =====
    "dpn_group_ledger_v2_enabled": False,
    "dpn_availability_probe_enabled": False,
    "dpn_availability_probe_retries": 2,
    "dpn_availability_probe_interval_s": 5,
    "dpn_max_route_refreshes": 2,
    "dpn_route_refresh_settle_s": 5,
    "dpn_route_refresh_backoff_s": 10,
    "dpn_console_log_enabled": True,
    # ===== E3.10 UNIFIED DEVICE AGENT + DPN TRANSPORT POOL (default OFF) =====
    "device_agent_dpn_ops_enabled": False,
    "dpn_agent_pool_enabled": False,
    "dpn_presence_interval_s": 20,
    "dpn_presence_stale_s": 60,
    "dpn_mutation_lease_s": 30,
    "dpn_agent_controller_host": "0.0.0.0",
    "dpn_agent_controller_port": 8766,
    "dpn_agent_controller_cert": os.path.join("state", "device_agents", "tls", "device-controller-cert.pem"),
    "dpn_agent_controller_key": os.path.join("state", "device_agents", "tls", "device-controller-key.pem"),
    "dpn_agent_request_timeout_s": 25,
    "dpn_pool_stale_s": 60,
    "dpn_auth_backoff_s": 300,
    "dpn_agent_refresh_max": 8,
    "dpn_agent_reverse_interval_s": 30,
    "dpn_embed_agent_pool_coordinator": False,
    "dpn_auto_register_enabled": False,
    "dpn_auto_groups_file": "dpn_groups_auto.json",
    "dpn_auto_catalog_file": "dpn_groups.json",
    "device_agent_state_dir": os.path.join("state", "device_agents"),
    # ===== DEVICE BACKEND ABSTRACTION (default ADB; agent must be opt-in) =====
    "device_backend": "adb",
    "execution_mode": "normal",
}


def load_config():
    """Layering de config: DEFAULTS -> base (SLN_CONFIG_FILE ou config_sln_v2.json)
    -> overlay (SLN_CONFIG_OVERLAY). O overlay só sobrescreve o que declara, preservando
    os valores de desenvolvimento que ele NÃO toca (ex.: download_apps_pct)."""
    cfg = dict(DEFAULTS)
    _merge(_read_json(_resolve_config_file()), cfg)
    overlay = _resolve_overlay_file()
    if overlay:
        _merge(_read_json(overlay), cfg)
    env_backend = os.environ.get("SLN_DEVICE_BACKEND")
    if env_backend:
        cfg["device_backend"] = env_backend
    env_mode = os.environ.get("SLN_EXECUTION_MODE")
    if env_mode:
        cfg["execution_mode"] = env_mode
    return cfg


def save_config(cfg):
    """Salva config no JSON efetivo (o mesmo que load_config leu)."""
    try:
        with open(_resolve_config_file(), 'w', encoding='utf-8') as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        return True
    except Exception:
        return False


def decide(cfg, key_pct, rand=None):
    """Decide True/False conforme percentual (0-100)."""
    pct = int(cfg.get(key_pct, 0) or 0)
    r = random.random() if rand is None else rand
    return (r * 100) < pct


def rand_in_range(cfg, key_range):
    """Retorna valor aleatório dentro do intervalo [min,max]."""
    rng = cfg.get(key_range) or [1, 1]
    lo, hi = int(rng[0]), max(int(rng[1]), int(rng[0]))
    return random.uniform(lo, hi)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == '--init':
        save_config(DEFAULTS)
        print(f"[OK] Config inicial criada em {CONFIG_FILE}")
    else:
        print(json.dumps(load_config(), indent=2, ensure_ascii=False))