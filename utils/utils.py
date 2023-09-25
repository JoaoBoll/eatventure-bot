import subprocess
import cv2
import time
from adb_utils.adb_utils import capture_and_copy_screenshot

class Utils():
    
    screenshot_path='./screenshot.png'
    
    def find_image_and_save(icon_path, method_number):
        location = Utils.find_image_generic(icon_path, method_number, True, 0.9)
        return location

    def find_image(icon_path, method_number):
        location = Utils.find_image_generic(icon_path, method_number, False, 0.9)
        return location

    def find_image_with_threshold(icon_path, method_number, threshold):
        location = Utils.find_image_generic(icon_path, method_number, False, threshold)
        return location
        
    def find_image_with_threshold_and_save(icon_path, method_number, threshold):
        location = Utils.find_image_generic(icon_path, method_number, True, threshold)
        return location
    
    def find_image_generic(icon_path, method_number, save_image, threshold):
            # Carrega a captura de tela em escala de cinza
        screenshot =  cv2.imread(Utils.screenshot_path, 0)

        # Carrega o ícone que você deseja encontrar em escala de cinza
        icon =  cv2.imread(icon_path, 0)

        h, w = icon.shape

        screenshot2 = cv2.imread(Utils.screenshot_path)
        
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
        
        if max_val >= threshold:
            print (f'Chega aqui {threshold}')
            
            if method in [cv2.TM_SQDIFF, cv2.TM_SQDIFF_NORMED]:
                location = min_loc
            else:
                location = max_loc

            x,y = location
                        
            print (f'Icone encontrado em: x:{x} y:{y}')
            if save_image:
                print (f'Chega aqui {save_image}')

                screenshot2 = cv2.imread(Utils.screenshot_path)
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
            print ('Icone não encontrado!')
            return None
        
    def click(x, y, message):
        print (message)
        subprocess.run(f"adb shell input tap {x} {y}", shell=True)
    
    def click_and_hold(x,y, seconds, message):
        print(message)
        time_seconds = seconds * 1000
        subprocess.run(f"adb shell input swipe {x} {y} {x} {y} {time_seconds}", shell=True)
        time.sleep(seconds)        
