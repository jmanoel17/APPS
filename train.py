from ultralytics import YOLO

# Carregar modelo pré-treinado (pode ser "n" (nano), "s" (small), "m" (medium), etc.)
model = YOLO("yolov8n.pt")

# Treinar
model.train(data="dataset/dataset.yaml", epochs=50, imgsz=640)