import time
from utils.mesage_utils import MessageUtils as messages
from globals import globals
from functions.images_functions import ImagesFunctions as image
from adb_utils import adb_utils as adb

class Automation:
    
    screenshot_path='./screenshot.png'
    output_path='screenshot-circle.png'
    quantInvestSucces=0
    tentativasDeAchar=0
    
    @staticmethod
    def process_event():
        image.new_screenshot()
        time.sleep(0.5)
        image.open_box()
        image.up_upgrades()
        image.up_food(False)

    @staticmethod
    def process():
        globals.running_times = globals.running_times + 1
        image.new_screenshot()
        time.sleep(0.7)

        # Procura qualquer item para fechar na tela!
        image.find_close_x()

        # Convidar Helper (Só para tirar o "!")
        # image.invite_helper()

        # Procurar Investidores
        # image.find_investor()
        
        # Abrindo caixas
        image.open_box()
        
        # Aplica upgrades
        image.up_upgrades()
        
        # Evolui pratos
        image.up_food(True)
        
        # Proxima cidade
        image.next_city()

        if globals.running_times % 3 == 0 :
            if globals.swipe_count % 4 == 0:
                if globals.swipe_up:
                    globals.swipe_up = False
                else:
                    globals.swipe_up = True

            if globals.swipe_up:
                adb.swipe_up(500, 400, 500, 1250)
            else:
                adb.swipe_down(500, 1250, 500, 400)
                
            globals.swipe_count = globals.swipe_count + 1
        print ('-----------------------------')
        print (f'Rodou: {globals.running_times} vez(es)')
        print (f'Cidades: {globals.up_count} / Vôou: {globals.fly_count}')
        
        # Evolui o nível das comidas
        #Automation.up_food()
        
        # Registre o resultado da busca
        #if encontrado:
            #messages.add_item_log("find_close_x", "Encontrou item para fechar")
        #else:
            #messages.add_item_log("find_close_x", "Nenhum item para fechar encontrado")
        
        
'''
    @staticmethod
    def process():
        capture_and_save_screenshot()
        time.sleep(1)
        
        print ('-----------------------------')
        print ('Procurando investidor!')
        Automation.find_investor()
        print ('-----------------------------')
        
        print ('-----------------------------')
        print ('Fechando outras telas!')
        Automation.close_x()
        print ('-----------------------------')
        
        print ('-----------------------------')
        print ('Abrindo Box!')
        Automation.open_box()
        print ('-----------------------------')

        
        print ('-----------------------------')
        print ('Procurando icone para próxima cidade!')
        Automation.next_city()
        print ('-----------------------------')

        print ('-----------------------------')
        print ('Procurando icone up food!')
        Automation.up_food()
        print ('-----------------------------')

    @staticmethod
    def process_alt():
        capture_and_save_screenshot()
        time.sleep(1)
        location = None
        print ('-----------------------------')
        print ('Procurando investidor!')
        Automation.find_investor()
        print ('-----------------------------')
        
    @staticmethod
    def find_investor():
        while True:
            location = Utils.find_image("./images/investor.png", 5)
        
            if location:
                x, y = location
                Utils.click(x, y, "Abrindo investidor")
                time.sleep(1)
                capture_and_save_screenshot()
                time.sleep(1)
                
                Automation.claim_reward()
            else:
                location = Utils.find_image_with_threshold_and_save("./images/investor_2.jpg", 5, 0.8)
                if location:
                    x, y = location
                    Utils.click(x, y, "Abrindo investidor 2")
                    time.sleep(1)
                    capture_and_save_screenshot()
                    time.sleep(1)
                
                    Automation.claim_reward()
                break
            
    @staticmethod
    def next_city():
        location_plane = Utils.find_image("./images/plane.png", 5)
        if location_plane:
            x, y = location_plane
            Utils.click(x, y, "Abrindo tela para próxima cidade!")
            
            capture_and_save_screenshot()
            
            time.sleep(2)
            
            location_fly = Utils.find_image("./images/fly.png", 5)
            
            if location_fly:
                x, y = location_fly
                Utils.click(x, y, "Fly!")
                time.sleep(10)
        else:
            location_next_city = Utils.find_image("./images/build.png", 5)
                        
            if location_next_city:
                x, y = location_next_city
                Utils.click(x, y, "Abrindo tela para próxima cidade!")
                
                capture_and_save_screenshot()
                
                location_fly = Utils.find_image("./images/upgrade_city.png", 5)
                if location_fly:
                    x, y = location_fly
                    Utils.click(x, y, "Próxima cidade!!")
                    time.sleep(10)
                    Automation.open_city()
          
    def open_city():
        location_open = Utils.find_image("./images/open.png", 5)
                        
        if location_open:
            x, y = location_open
            Utils.click(x, y, "Abrindo tela para próxima cidade!")
            time.sleep(1)
            capture_and_save_screenshot()
            time.sleep(1)
'''