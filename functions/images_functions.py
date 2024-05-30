from utils.image_utils import ImageUtils
from adb_utils.adb_utils import capture_and_save_screenshot
import time
from globals import globals as g
from adb_utils import adb_utils as adb
import sys

class ImagesFunctions:
    
    # Novo print
    def new_screenshot():
        capture_and_save_screenshot()
    
    # Fecha todas as abas abertas
    def find_close_x():
        while True:
            location = ImageUtils.find_image('x.png', 5)
            if location:
                x, y = location
                adb.press(x, y)
                time.sleep(2)
                break
            else:
                location_shops = ImageUtils.find_image("shops_x.png", 5)
                
                if location_shops:
                    x, y = location_shops
                    adb.press(x, y)
                    time.sleep(2)
                    break
                else:
                    ImagesFunctions.open_city()
                    break # Sai do loop se não houver mais locations
    
    # Upgrades da cidade
    def up_upgrades():
        location_arrow = ImageUtils.find_image('upgrades_arrow.png', 5)
        if location_arrow:
            x, y = location_arrow
            adb.press(x,y)
            time.sleep(1)
            
            #Multiplos clicks usando configs de x,y definido nas globals.
            while True:
                x=g.up_upgrade_x
                y=g.up_upgrade_y
                adb.press_multiple_times(x, y, 10)
                break
        
            x_close = g.close_upgrades_x
            y_close = g.close_upgrades_y
        
            time.sleep(1)    
            adb.press(x_close,y_close)
            time.sleep(1)
    
    # Busca de Investidores
    def find_investor():
        while True:
            location = ImageUtils.find_image("investor.png", 5)
        
            if location:
                x, y = location
                adb.press(x, y)
                ImagesFunctions.claim_reward()
            break
            # Confundindo chapéu dos personages com do ivnestidor, arrumar forma de comparar
            #else:
            #    location = ImageUtils.find_image_with_threshold_and_save("investor_2.jpg", 5, 0.8)
            #    if location:
            #        x, y = location
            #        ImageUtils.click(x, y)                
            #        ImagesFunctions.claim_reward()
            #    break
    
    def claim_reward():
        time.sleep(0.5)
        capture_and_save_screenshot()
        time.sleep(1)

        location_open = ImageUtils.find_image("gems.png", 5)
                        
        # Aqui valida apenas gemas.
        if location_open:
            time.sleep(1)
            adb.press(g.claim_reward_x, g.claim_reward_y)

            for remaining in range(40, -1, -1):
                sys.stdout.write(f'\rAssistindo AD: {remaining} segundos')
                sys.stdout.flush()
                time.sleep(1)
            
            sys.stdout.write('\rTimer concluído!\n')
            sys.stdout.flush()
            adb.open_app()
            time.sleep(5)
        else:
            adb.press(770, 870)
            
    # Ativa Boost de Gold
    def activate_boost():
        location = ImageUtils.find_image("boost.png", 5)

        if location:
            x, y = location
            adb.press(550, 2250)

            for remaining in range(40, -1, -1):
                sys.stdout.write(f'\rAssistindo AD: {remaining} segundos')
                sys.stdout.flush()
                time.sleep(1)

            sys.stdout.write('\rTimer concluído!\n')
            sys.stdout.flush()
            adb.open_app()
            time.sleep(5)            
    
    #Evolui o nível dos pratos
    def up_food():
        location_arrow = ImageUtils.find_image('food_upgrade_arrow.png', 5)
        if location_arrow:
            x, y = location_arrow
            adb.press(x + 25,y)
            time.sleep(0.5)

            x_up = x + 25            
            y_up=y-50
            adb.press_and_hold(x_up,y_up, 3)
            time.sleep(0.3)            
            
            y_close = y + 45
            adb.press(x, y_close)
            time.sleep(0.5)
        else:
            adb.press_and_hold(10,300, 0.3)
            
    # Abre as caixas de cozinheiros/comida/etc
    @staticmethod
    def open_box():
        while True:
            location_x = ImageUtils.find_image("box.png", 5)
        
            if location_x:
                x, y = location_x
                adb.press(x, y)
            else:
                location_x = ImageUtils.find_image("box_2.png", 5)
        
                if location_x:
                    x, y = location_x
                    adb.press(x, y)
                else:
                    break # Sai do loop se não houver mais locations   
            
            capture_and_save_screenshot()
            time.sleep(0.5)
   
    @staticmethod
    def open_box_test():
        while True:
            location_x = ImageUtils.find_image("box.png", 5)
        
            if location_x:
                x, y = location_x
                adb.press(x, y)
            else:
                location_x = ImageUtils.find_image("box_2.png", 5)
        
                if location_x:
                    x, y = location_x
                    adb.press(x, y)
                else:
                    break # Sai do loop se não houver mais locations   
   
    # Próxima Cidade
    def next_city():
        location_plane = ImageUtils.find_image("plane.png", 5)
        if location_plane:
            x, y = location_plane
            adb.press(x, y)
            
            capture_and_save_screenshot()
            
            time.sleep(2)
            
            location_fly = ImageUtils.find_image("fly.png", 5)
            
            if location_fly:
                x, y = location_fly
                adb.press(x, y)
                time.sleep(15)

        else:
            location_next_city = ImageUtils.find_image("build.png", 5)
                        
            if location_next_city:
                x, y = location_next_city
                adb.press(x, y)
                
                capture_and_save_screenshot()
                
                location_Open = ImageUtils.find_image("upgrade_city.png", 5)
                if location_Open:
                    x, y = location_Open
                    adb.press(x, y)
                    time.sleep(15)
                    
    def open_city():
        location_open = ImageUtils.find_image("open.png", 5)
                        
        if location_open:
            x, y = location_open
            adb.press(x, y)
            time.sleep(1)
            capture_and_save_screenshot()
            time.sleep(1)