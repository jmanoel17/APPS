import uvicorn
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import JSONResponse
import cv2
import numpy as np
from ultralytics import YOLO
import io
import os



from contextlib import asynccontextmanager

# Variáveis globais para o modelo
model = None
# Define o caminho ABSOLUTO baseado na localização deste script
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(BASE_DIR, 'runs', 'detect', 'train3', 'weights', 'best.pt')

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Gerencia o ciclo de vida da aplicação (startup e shutdown)"""
    global model
    print(f"🔄 Carregando modelo YOLO de: {MODEL_PATH}")
    try:
        if os.path.exists(MODEL_PATH):
            model = YOLO(MODEL_PATH)
            print("✅ Modelo carregado com sucesso!")
        else:
            print(f"❌ Arquivo do modelo não encontrado: {MODEL_PATH}")
    except Exception as e:
        print(f"❌ Erro ao carregar modelo: {e}")
    
    yield
    
    # Código de shutdown (se necessário)
    print("🛑 Encerrando servidor de IA...")
    model = None

app = FastAPI(title="YOLO Detection Server", lifespan=lifespan)

@app.post("/detect")
async def detect(file: UploadFile = File(...), conf: float = 0.25, iou: float = 0.7):
    """
    Endpoint para detectar objetos em uma imagem enviada.
    Retorna as coordenadas do centro dos objetos detectados.
    """
    global model
    if model is None:
        raise HTTPException(status_code=503, detail="Modelo não carregado")

    try:
        # Ler o arquivo de imagem
        contents = await file.read()
        nparr = np.frombuffer(contents, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        if img is None:
            raise HTTPException(status_code=400, detail="Imagem inválida")

        # Realizar inferência
        results = model(img, conf=conf, iou=iou, verbose=False)
        
        coordinates = []
        for result in results:
            boxes = result.boxes
            if boxes is not None:
                for box in boxes:
                    x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
                    center_x = int((x1 + x2) / 2)
                    center_y = int((y1 + y2) / 2)
                    confidence = float(box.conf[0].cpu().numpy())
                    class_id = int(box.cls[0].cpu().numpy())

                    coordinates.append({
                        'x': center_x,
                        'y': center_y,
                        'confidence': confidence,
                        'class': class_id
                    })

        # Ordenar por confiança
        coordinates.sort(key=lambda x: x['confidence'], reverse=True)

        return JSONResponse(content={
            "success": True,
            "count": len(coordinates),
            "detections": coordinates
        })

    except Exception as e:
        print(f"❌ Erro na detecção: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import asyncio
    import sys
    
    if sys.platform == 'win32':
        # Evita erro [WinError 64] O nome da rede especificado não está mais disponível
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    # Roda o servidor na porta 8000
    print("🚀 Iniciando servidor de IA...")
    uvicorn.run(app, host="0.0.0.0", port=8000)
