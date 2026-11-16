#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
aiMain_sln_v2.py - Servidor de IA central (SLN V2)

Funções:
  POST /detect      -> recebe screenshot (multipart), roda YOLO, retorna coordenadas dos X
  POST /classify    -> recebe screenshot, retorna tipo de anúncio:
                        "rewarded" (premiado: aguardar tempo antes do X)
                        "interstitial" (pode fechar rápido)
                        "playstore" (intersticial de instalação)
                        "playable" (HTML5 sem elementos -> precisa HOME+force-stop)
  GET  /health      -> status + latência acumulada (queue_wait_ms / inference_ms / total_ms)

Escala: UM serviço central para todos os bots (1 modelo). NÃO criar 1 modelo por device.
A inferência roda FORA do event loop (asyncio.to_thread) com um lock único — o servidor
continua respondendo /health enquanto o YOLO trabalha. Sem batching (decisão adiada para
depois do benchmark 5/10 devices).

MODEL_PATH: env `SLN_IA_MODEL_PATH` > chave `ia_model_path` em config_sln_v2.json > default.
"""
import asyncio
import io
import json
import os
import threading
import time
from contextlib import asynccontextmanager
import numpy as np
import cv2
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse
from ultralytics import YOLO

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Modelo treinado para detectar o botão X/Close dos anúncios (default preservado)
_DEFAULT_MODEL_PATH = os.path.join(r'C:\AndroidProjects\APPS', 'runs', 'detect', 'train3', 'weights', 'best.pt')


def _resolve_model_path():
    """(path, origem). Prioridade: env > config_sln_v2.json (chave ia_model_path) > default.
    Um caminho inexistente nunca substitui o default (preserva o comportamento atual)."""
    env = os.environ.get("SLN_IA_MODEL_PATH")
    if env and os.path.exists(env):
        return env, "env"
    try:
        with open(os.path.join(BASE_DIR, "config_sln_v2.json"), "r", encoding="utf-8") as f:
            v = (json.load(f) or {}).get("ia_model_path")
        if v and os.path.exists(v):
            return v, "config"
    except Exception:
        pass
    return _DEFAULT_MODEL_PATH, "default"


MODEL_PATH, MODEL_PATH_SOURCE = _resolve_model_path()

model = None
_lock = threading.Lock()          # 1 modelo => 1 inferência por vez (segurança do YOLO)
_stats_lock = threading.Lock()

# Warm-up da primeira inferência (cold-start do YOLO ~3.7-4.9s). Feito no startup do servidor
# para que a primeira requisição real NÃO pague esse custo (evita timeout de 4s do bot).
_warmup = {"done": False, "ok": False, "ms": None, "error": None}

# Latência acumulada (queue_wait = espera pelo lock + troca de thread; inference = YOLO puro)
_stats = {
    "requests": 0,
    "queue_wait_ms_sum": 0.0, "queue_wait_ms_max": 0.0,
    "inference_ms_sum": 0.0, "inference_ms_max": 0.0,
    "total_ms_sum": 0.0, "total_ms_max": 0.0,
}


def _stats_add(queue_ms, inference_ms, total_ms):
    """Contadores baratos (ints/floats) — não bloqueiam o event loop."""
    with _stats_lock:
        _stats["requests"] += 1
        _stats["queue_wait_ms_sum"] += queue_ms
        _stats["queue_wait_ms_max"] = max(_stats["queue_wait_ms_max"], queue_ms)
        _stats["inference_ms_sum"] += inference_ms
        _stats["inference_ms_max"] = max(_stats["inference_ms_max"], inference_ms)
        _stats["total_ms_sum"] += total_ms
        _stats["total_ms_max"] = max(_stats["total_ms_max"], total_ms)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global model
    print(f"🔄 Carregando modelo YOLO de: {MODEL_PATH} (origem={MODEL_PATH_SOURCE})")
    try:
        if os.path.exists(MODEL_PATH):
            model = YOLO(MODEL_PATH)
            print("✅ Modelo carregado com sucesso!")
        else:
            print(f"❌ Modelo não encontrado: {MODEL_PATH}")
    except Exception as e:
        print(f"❌ Erro ao carregar modelo: {e}")

    # Warm-up: 1 inferência dummy fora do event loop (evita o cold-start na 1ª requisição real).
    if model is not None:
        def _warmup_infer():
            with _lock:
                model(np.zeros((640, 640, 3), dtype=np.uint8), conf=0.25, iou=0.7, verbose=False)
        t0 = time.perf_counter()
        try:
            await asyncio.to_thread(_warmup_infer)
            _warmup["done"] = True
            _warmup["ok"] = True
            _warmup["ms"] = round((time.perf_counter() - t0) * 1000, 1)
            print(f"✅ Warm-up concluído ({_warmup['ms']}ms).")
        except Exception as e:
            _warmup["done"] = True
            _warmup["ok"] = False
            _warmup["error"] = str(e)
            print(f"⚠️ Warm-up falhou: {e}")
    yield
    print("🛑 Encerrando servidor de IA...")
    model = None


app = FastAPI(title="SLN V2 IA Detector", lifespan=lifespan)


def decode_image(contents):
    nparr = np.frombuffer(contents, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(status_code=400, detail="Imagem inválida")
    return img


@app.post("/detect")
async def detect(file: UploadFile = File(...), conf: float = 0.25, iou: float = 0.7):
    """Detecta botões X/Close de anúncio em screenshot. Retorna centros ordenados por confiança.

    A inferência NÃO roda no event loop: `asyncio.to_thread` + lock único. Resultado funcional
    idêntico ao anterior (mesmo modelo, mesmos filtros, mesma ordem)."""
    global model
    if model is None:
        raise HTTPException(status_code=503, detail="Modelo não carregado")
    contents = await file.read()
    img = decode_image(contents)
    t_total = time.perf_counter()
    queue_wait_ms = [0.0]

    def _infer():
        """Executa no worker thread: o lock serializa o YOLO (o tempo até obter o lock é a
        parcela de fila). Nada aqui toca o event loop."""
        with _lock:
            queue_wait_ms[0] = (time.perf_counter() - t_total) * 1000
            t_inf = time.perf_counter()
            results = model(img, conf=conf, iou=iou, verbose=False)
            return results, (time.perf_counter() - t_inf) * 1000

    results, inference_ms = await asyncio.to_thread(_infer)
    total_ms = (time.perf_counter() - t_total) * 1000
    _stats_add(queue_wait_ms[0], inference_ms, total_ms)

    coords = []
    for result in results:
        boxes = result.boxes
        if boxes is not None:
            for box in boxes:
                x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
                coords.append({
                    'x': int((x1 + x2) / 2),
                    'y': int((y1 + y2) / 2),
                    'w': float(x2 - x1),
                    'h': float(y2 - y1),
                    'confidence': float(box.conf[0].cpu().numpy()),
                    'class': int(box.cls[0].cpu().numpy())
                })
    coords.sort(key=lambda c: c['confidence'], reverse=True)
    # Filtra por região superior (X de anúncio fica no topo)
    img_h = img.shape[0]
    top_coords = [c for c in coords if c['y'] <= img_h * 0.25]
    return JSONResponse(content={
        "success": True,
        "count": len(top_coords),
        "detections": top_coords,
        "elapsed_s": round(inference_ms / 1000.0, 3),
        "queue_wait_ms": round(queue_wait_ms[0], 1),
        "inference_ms": round(inference_ms, 1),
        "total_ms": round(total_ms, 1),
        "img_size": [img.shape[1], img.shape[0]]
    })


@app.post("/classify")
async def classify(file: UploadFile = File(...)):
    """
    Classifica o tipo de anúncio a partir do screenshot + heurísticas.
    Retorna tipo e instruções para o bot.
    """
    contents = await file.read()
    img = decode_image(contents)

    h, w = img.shape[:2]

    # Heurísticas baratas de "tela de loja" via cor/dominância: se for play store (fundo branco/gray típico)
    # Usa média de brilho + variância -> play store é mais clara
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    mean_b = float(gray.mean())
    std_b = float(gray.std())

    # Play Store intersticial geralmente tem header com app + botão Instalar (detectável por texto/dump, não aqui)
    # Aqui classificamos por padrão da tela

    # Se já rodou o modelo, aproveita detect
    global model
    ad_type = "interstitial"
    detail = {
        "mean_brightness": round(mean_b, 1),
        "std_brightness": round(std_b, 1),
        "bright": mean_b > 180,
        "screen_w": w,
        "screen_h": h,
    }
    if mean_b > 200 and std_b < 60:
        ad_type = "playstore"  # tela muito clara uniforme: Play Store / interstitial de loja
    elif 120 < mean_b < 210:
        # pode ser rewarded (playable) ou interstitial
        ad_type = "rewarded" if std_b > 55 else "interstitial"

    return JSONResponse(content={
        "success": True,
        "type": ad_type,
        "detail": detail,
        "advice": {
            "wait_before_close_s": 5 if ad_type == "rewarded" else 0,
            "use_playstore_escape": ad_type == "playstore",
            "force_home_restart": ad_type == "playable"
        }
    })


@app.get("/health")
async def health():
    """Status + latência acumulada. Nunca toca o modelo: responde mesmo com o YOLO ocupado."""
    with _stats_lock:
        n = _stats["requests"]
        out = {
            "status": "ok",
            "model_loaded": model is not None,
            "model_path": MODEL_PATH,
            "model_path_source": MODEL_PATH_SOURCE,
            "requests": n,
            "warmup": _warmup,
        }
        if n:
            out.update({
                "queue_wait_ms_avg": round(_stats["queue_wait_ms_sum"] / n, 1),
                "queue_wait_ms_max": round(_stats["queue_wait_ms_max"], 1),
                "inference_ms_avg": round(_stats["inference_ms_sum"] / n, 1),
                "inference_ms_max": round(_stats["inference_ms_max"], 1),
                "total_ms_avg": round(_stats["total_ms_sum"] / n, 1),
                "total_ms_max": round(_stats["total_ms_max"], 1),
            })
    return out


if __name__ == "__main__":
    import uvicorn
    if os.name == 'win32':
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        except Exception:
            pass
    uvicorn.run(app, host="0.0.0.0", port=8000)
