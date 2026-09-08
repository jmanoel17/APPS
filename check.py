from ultralytics import YOLO

def detect_objects_and_get_coordinates(image_path, model_path='runs/detect/train2/weights/best.pt', show_image=True, verbose=True):
    """
    Detecta objetos em uma imagem e retorna as coordenadas do centro
    
    Args:
        image_path (str): Caminho para a imagem a ser analisada
        model_path (str): Caminho para o modelo YOLO treinado
        show_image (bool): Se deve mostrar a imagem com detecções
        verbose (bool): Se deve exibir logs detalhados
    
    Returns:
        list: Lista de dicionários com coordenadas e informações dos objetos detectados
              Formato: [{'x': int, 'y': int, 'confidence': float, 'class': int}, ...]
    """
    if verbose:
        print(f"🔍 Carregando modelo: {model_path}")
        print(f"📸 Analisando imagem: {image_path}")
    
    model = YOLO(model_path)
    results = model(image_path)
    
    # Lista para armazenar coordenadas
    coordinates = []
    
    # Extrair coordenadas do centro de cada detecção
    for result in results:
        boxes = result.boxes
        if boxes is not None:
            for i, box in enumerate(boxes):
                # Obter coordenadas da bounding box (x1, y1, x2, y2)
                x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
                
                # Calcular coordenadas do centro
                center_x = int((x1 + x2) / 2)
                center_y = int((y1 + y2) / 2)
                
                # Obter confiança e classe
                confidence = box.conf[0].cpu().numpy()
                class_id = int(box.cls[0].cpu().numpy())
                
                # Adicionar às coordenadas
                coordinates.append({
                    'x': center_x,
                    'y': center_y,
                    'confidence': float(confidence),
                    'class': class_id
                })
                
                if verbose:
                    print(f"Detecção {i+1}:")
                    print(f"  Centro: ({center_x}, {center_y})")
                    print(f"  Confiança: {confidence:.2f}")
                    print(f"  Classe: {class_id}")
                    print("-" * 30)
    
    if verbose:
        print(f"\n🎯 Total de {len(coordinates)} objeto(s) detectado(s)")
        print("📍 Coordenadas detectadas:")
        for i, coord in enumerate(coordinates):
            print(f"  Objeto {i+1}: ({coord['x']}, {coord['y']})  # Confiança: {coord['confidence']:.2f}")
    
    if show_image:
        results[0].show()
    
    return coordinates

# Exemplo de uso direto (quando executado como script principal)
if __name__ == "__main__":
    # Detectar objetos e obter coordenadas
    coordinates = detect_objects_and_get_coordinates('testes/13.jpeg')
    
    # Mostrar coordenadas para uso externo
    print("\n📋 Coordenadas prontas para uso:")
    for i, coord in enumerate(coordinates):
        print(f"Posição {i+1}: x={coord['x']}, y={coord['y']}, confiança={coord['confidence']:.2f}")