import subprocess
import cv2
import time
from adb_utils.adb_utils import capture_and_save_screenshot
from utils.mesage_utils import MessageUtils as message
from enums.identifiers import Identifiers
from globals import globals

class ImageUtils():
    
    screenshot_path='./screenshot.png'

    def find_image_and_save(icon_path, method_number):
        location = ImageUtils.find_image_generic(icon_path, method_number, True, 0.9)
        return location

    def find_image(icon_path, method_number):
        location = ImageUtils.find_image_generic(icon_path, method_number, False, 0.9)
        return location

    def find_image_with_threshold(icon_path, method_number, threshold):
        location = ImageUtils.find_image_generic(icon_path, method_number, False, threshold)
        return location
        
    def find_image_with_threshold_and_save(icon_path, method_number, threshold):
        location = ImageUtils.find_image_generic(icon_path, method_number, True, threshold)
        return location
    
    def find_image_generic(icon_path, method_number, save_image, threshold):
        # Carrega a captura de tela em escala de cinza
        screenshot =  cv2.imread(ImageUtils.screenshot_path, 0)

        # Carrega o ícone que você deseja encontrar em escala de cinza
        icon =  cv2.imread(f'./images/{globals.config_name}/{icon_path}', 0)

        print(f'./images/{globals.config_name}/{icon_path}')
        
        h, w = icon.shape

        screenshot2 = cv2.imread(ImageUtils.screenshot_path)
        
        method = None
        if method_number == 0:
            method = cv2.TM_SQDIFF
        if method_number == 1:
            method = cv2.TM_SQDIFF_NORMED
        if method_number == 2:
            method = cv2.TM_CCORR
        if method_number ==3:
            method = cv2.TM_CCORR_NORMED
        if method_number ==4:
            method = cv2.TM_CCOEFF
        if method_number ==5:
            method = cv2.TM_CCOEFF_NORMED
        
        result = cv2.matchTemplate(screenshot, icon, method)
        min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)
        
        if (method in [cv2.TM_SQDIFF, cv2.TM_SQDIFF_NORMED] and min_val <= -0.8) or (method not in [cv2.TM_SQDIFF, cv2.TM_SQDIFF_NORMED] and max_val >= threshold):
            if method in [cv2.TM_SQDIFF, cv2.TM_SQDIFF_NORMED]:
                location = min_loc  # Quanto menor, melhor
            else:
                location = max_loc  # Quanto maior, melhor

            x,y = location
                        
            message.add_item_log(Identifiers.ACTION, 'Icone encontrado em: x:{x} y:{y}')
            
            # Mostra imagem marcando oq encontrou:
            if globals.is_test:
                '''
                    print (f'Chega aqui {save_image}')
                '''

                screenshot2 = cv2.imread(ImageUtils.screenshot_path)
                botton_right = (location[0] + w, location[1] + h)
                cv2.rectangle(screenshot2, location, botton_right, 0, 5)  # Vermelho

                # Salve a imagem de saída com os retângulos
                cv2.imwrite("./img-teste.png", screenshot2)

                # Visualize a imagem com os retângulos
                cv2.imshow('Resultado', screenshot2)
                cv2.waitKey(0)
                cv2.destroyAllWindows()
        
            return location
        else:
            message.add_item_log(Identifiers.ACTION, 'Icone não encontrado!')
            return None
        
    def click(x, y):
        print(f"click em x:{x} e y{y}")
        subprocess.run(f"adb shell input tap {x} {y}", shell=True)
    
    def click_and_hold(x,y, seconds, message):
        print(message)
        time_seconds = seconds * 1000
        subprocess.run(f"adb shell input swipe {x} {y} {x} {y} {time_seconds}", shell=True)
        time.sleep(seconds)        
