from utils.image_utils import ImageUtils
from adb_utils.adb_utils import capture_and_save_screenshot
import time
from globals import globals as g
from adb_utils import adb_utils as adb
import sys

class ImagesFunctions:
    
    # Novo print
    def new_screenshot():
        adb.open_app()
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

    # Clica no Invite Helper
    def invite_helper():
        location_helper = ImageUtils.find_image('invite_helper.png', 5)
        if location_helper:
            x, y = location_helper
            adb.press(x+50,y+50)

            time.sleep(1)
            capture_and_save_screenshot()
            time.sleep(1)

            location = ImageUtils.find_image('x.png', 5)
            if location:
                x, y = location
                adb.press(x, y)

                time.sleep(1)
                capture_and_save_screenshot()
                time.sleep(1)

    # Upgrades da cidade
    def up_upgrades():
        location_arrow = ImageUtils.find_image_with_threshold('upgrades_arrow.png', 5, 0.95)
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
    def up_food(isNormal:bool):
        if isNormal:
            if True:
                location_arrow = ImageUtils.find_image('food_upgrade_arrow.png', 5)
                if location_arrow:
                    ImagesFunctions.location_arrow(location_arrow)
                    return
                
                location_arrow_2 = ImageUtils.find_image('food_upgrade_arrow_2.png', 5)
                if location_arrow_2:
                    ImagesFunctions.location_arrow(location_arrow_2)

                location_arrow_3 = ImageUtils.find_image('food_upgrade_arrow_3.png',5)
                if location_arrow_3:
                    ImagesFunctions.location_arrow(location_arrow_3)

                location_arrow_4 = ImageUtils.find_image('food_upgrade_arrow_4.png', 5)
                if location_arrow_4:
                    ImagesFunctions.location_arrow(location_arrow_4)

                location_arrow_5 = ImageUtils.find_image('food_upgrade_arrow_5.png', 5)
                if location_arrow_5:
                    ImagesFunctions.location_arrow(location_arrow_5)

                adb.press(10,300)
            else:
                ImagesFunctions.up_button_food()
        else:
            location_upgrade = ImageUtils.find_image_with_threshold('upgrade_food_event.png',5, 0.80)
            if location_upgrade:
                x_lup, y_lup = location_upgrade
                adb.press_and_hold(x_lup,y_lup+30, 3)    
    
    def location_arrow(location_arrow):
        x, y = location_arrow
        adb.press(x+10 ,y +40)

        time.sleep(0.5)
        capture_and_save_screenshot()
        time.sleep(0.5)

        print(x)
        print(y)

        ImagesFunctions.up_button_food()

        y_close = y + 45
        adb.press(x+40, y_close)
        capture_and_save_screenshot()
        time.sleep(0.5)
         
    def up_button_food():
        unlock = ImageUtils.find_image('unlock.png',5)
        location_upgrade = ImageUtils.find_image('upgrade_food_gold.png',5)

        if unlock and location_upgrade:
            x_lup, y_lup = location_upgrade
            adb.press(x_lup,y_lup+30)
        elif location_upgrade:
            x_lup, y_lup = location_upgrade
            adb.press_and_hold(x_lup,y_lup+30, 3)
        
       

    # Abre as caixas de cozinheiros/comida/etc
    @staticmethod
    def open_box():
        for i in range(5):
            icon_path = f"box_{i}.png"
            location_x = ImageUtils.find_image(icon_path, 5)
    
            if location_x:
                x, y = location_x
                adb.press(x, y)
            elif i == 5:
                break
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
        location_next_city = ImageUtils.find_image_with_threshold("build.png", 5, 0.95)
        if location_next_city:
            print("build?")

            x, y = location_next_city
            adb.press(x, y)
            
            time.sleep(0.7)
            capture_and_save_screenshot()
            time.sleep(0.7)
            
            location_Open = ImageUtils.find_image("renovate_city_coin.png", 5)
            
            if location_Open:
                g.up_count = g.up_count + 1
                x, y = location_Open
                adb.press(x + 20, y)
                time.sleep(10)
                ImagesFunctions.reset_globals_new_city()

        else:
            location_plane = ImageUtils.find_image_with_threshold("plane.png", 5, 0.95)
            if location_plane:
                print("Achou plane")
                x, y = location_plane
                adb.press(x, y +20)

                time.sleep(0.7)
                capture_and_save_screenshot()
                time.sleep(0.7)

                print("AAA")

                location_fly = ImageUtils.find_image("fly.png", 5)
                if location_fly:
                    g.fly_count = g.fly_count + 1
                    x, y = location_fly
                    adb.press(x, y+40)
                    time.sleep(10)
                    ImagesFunctions.reset_globals_new_city()
            else:
                print("Não achou")

    def reset_globals_new_city():
        g.running_times = 0
        g.swipe_count = 0  
        g.swipe_up = False
                            
    def open_city():
        location_open = ImageUtils.find_image("open.png", 5)
                        
        if location_open:
            x, y = location_open
            adb.press(x, y)
            time.sleep(1)
            capture_and_save_screenshot()
            time.sleep(1)