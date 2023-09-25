import subprocess
import cv2
import numpy as np
from adb_utils.adb_utils import capture_and_copy_screenshot
from automation.automation import Automation
import time

# Exemplo de uso:
while True:
    Automation.process_alt()
'''
Investido:
'''

'''
def find_and_mark_icon(icon_path, screenshot_path='screenshot.png', output_path='screenshot-circle.png',  similarity_threshold=0.8):
    # Captura e copia a captura de tela do dispositivo
    capture_and_copy_screenshot()

    # Carrega a captura de tela em escala de cinza
    screenshot = cv2.resize(cv2.imread(screenshot_path, 0), (0,0), fx=0.6, fy=0.6)

    # Carrega o ícone que você deseja encontrar em escala de cinza
    icon = cv2.imread(icon_path, 0)

    h, w = icon.shape
    
    methods = [cv2.TM_CCOEFF, cv2.TM_CCOEFF_NORMED, cv2.TM_CCORR,
               cv2.TM_CCORR_NORMED, cv2.TM_SQDIFF, cv2.TM_SQDIFF_NORMED]
        
    for method in methods:
        screenshot2 = cv2.resize(cv2.imread(screenshot_path), (0,0), fx=0.6, fy=0.6)
        
        result = cv2.matchTemplate(screenshot, icon, method)
        min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)
        
        if method in [cv2.TM_SQDIFF, cv2.TM_SQDIFF_NORMED]:
            location = min_loc
        else:
            location = max_loc
        
        botton_right = (location[0] + w, location[1] + h)
        cv2.rectangle(screenshot2, location, botton_right, 0, 5)  # Vermelho

        # Salve a imagem de saída com os retângulos
        cv2.imwrite(output_path, screenshot2)

        # Visualize a imagem com os retângulos
        cv2.imshow('Resultado', screenshot2)
        cv2.waitKey(0)
        cv2.destroyAllWindows()
'''