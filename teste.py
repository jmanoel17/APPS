from ultralytics import YOLO

model = YOLO('runs/detect/train2/weights/best.pt')

results = model('testes/1.jpeg')

results[0].show()
