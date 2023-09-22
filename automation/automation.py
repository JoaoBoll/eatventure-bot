import subprocess
import cv2
import time
from uiautomator import Device
from adb_utils.adb_utils import capture_and_copy_screenshot
from utils.utils import Utils
from utils.utils_test import UtilsTest

class Automation:
    
    screenshot_path='./screenshot.png'
    output_path='screenshot-circle.png'
    
    @staticmethod
    def process():
        capture_and_copy_screenshot()
        time.sleep(1)
        
        print ('-----------------------------')
        print ('Procurando investidor!')
        Automation.clain_investor()
        print ('-----------------------------')
        
        print ('-----------------------------')
        print ('Fechando outras telas!')
        Automation.close_x()
        print ('-----------------------------')
        
        print ('-----------------------------')
        print ('Abrindo Box!')
        Automation.open_box()
        print ('-----------------------------')

        '''
        print ('-----------------------------')
        print ('Procurando icone para próxima cidade!')
        Automation.next_city()
        print ('-----------------------------')
        '''        
        
        print ('-----------------------------')
        print ('Aplicando upgrades!')
        Automation.up_upgrades()
        print ('-----------------------------')
        
        print ('-----------------------------')
        print ('Procurando icone up food!')
        Automation.up_food()
        print ('-----------------------------')
         
         
         
         
         
         
         
         
    
    """OK"""
    @staticmethod
    def open_box():
        while True:
            location_x = Utils.find_image("./images/box.png", 5)
        
            if location_x:
                x, y = location_x
                Utils.click(x, y, "Fechando tela!")
                
                capture_and_copy_screenshot()

                time.sleep(1)
                
            else:
                location_x = Utils.find_image("./images/box_2.png", 5)
        
                if location_x:
                    x, y = location_x
                    Utils.click(x, y, "Fechando tela!")
                    
                    capture_and_copy_screenshot()

                    time.sleep(1)
                else:
                    break # Sai do loop se não houver mais locations
                
    """OK"""
    @staticmethod
    def up_food():
        location_arrow = Utils.find_image('./images/food_upgrade_arrow.png', 5)
        if location_arrow:
            x, y = location_arrow
            x= x+20
            Utils.click(x,y, "Abrindo upgrade de comida")
            time.sleep(1)
            capture_and_copy_screenshot()
            time.sleep(1)

            Automation.click_up_food()
            
            time.sleep(2)
            x, y = location_arrow
            y=y+50
            print (f'x {x} y {y}')
            Utils.click(x,y, "Fechando upgrade")
    
    """OK"""
    @staticmethod  
    def click_up_food():

        print ('Chega aqui')
        location_upgrade = Utils.find_image('./images/upgrade_food.png', 5)

        if location_upgrade:
            x, y = location_upgrade
            
            seconds = 3
            Utils.click_and_hold(x, y, seconds, "Evoluindo")
        else:
            print('Não foi encontrado nenhuma comida para evoluir.')            

    """OK"""
    @staticmethod
    def up_upgrades():
        location_arrow = Utils.find_image('./images/upgrades_arrow.png', 5)
        if location_arrow:
            x, y = location_arrow
            Utils.click(x,y, "Abrindo upgrade")
            time.sleep(2)
                                    
            capture_and_copy_screenshot()
            time.sleep(1)
            
            Automation.click_up_upgrades()
            time.sleep(1)
            
            x, y = location_arrow
            Utils.click(x,y, "Fechando upgrade")
    
    """OK"""
    @staticmethod
    def click_up_upgrades():
        while True:
            location_upgrade = Utils.find_image('./images/upgrade_upgrades.png', 5)

            if location_upgrade:
                x, y = location_upgrade
                Utils.click(x, y, "Evoluindo")
            else:
                location_upgrade = Utils.find_image('./images/upgrade_upgrades2.png', 5)
                if location_upgrade:
                    x, y = location_upgrade
                    Utils.click(x, y, "Evoluindo")
                else:
                    break  # Sai do loop se não houver mais location_upgrade
            
            capture_and_copy_screenshot()
            time.sleep(2)
            Automation.close_x()

    
    """OK"""
    @staticmethod
    def next_city():
        location_plane = Utils.find_image("./images/plane.png", 5)
        if location_plane:
            x, y = location_plane
            Utils.click(x, y, "Abrindo tela para próxima cidade!")
            
            capture_and_copy_screenshot()
            
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
                
                capture_and_copy_screenshot()
                
                location_fly = Utils.find_image("./images/upgrade_city.png", 5)
                if location_fly:
                    x, y = location_fly
                    Utils.click(x, y, "Próxima cidade!!")
                    time.sleep(10)
                    Automation.open_city()
          
    """OK"""      
    def open_city():
        location_open = Utils.find_image("./images/open.png", 5)
                        
        if location_open:
            x, y = location_open
            Utils.click(x, y, "Abrindo tela para próxima cidade!")
            time.sleep(1)
            capture_and_copy_screenshot()
            time.sleep(1)
            
    """OK"""
    @staticmethod
    def close_x():
        while True:
            location_x = Utils.find_image("./images/x.png", 5)
        
            if location_x:
                x, y = location_x
                Utils.click(x, y, "Fechando tela!")
                
                capture_and_copy_screenshot()

                time.sleep(2)
                
            else:
                location_shops = Utils.find_image("./images/shops_x.png", 5)
                
                if location_shops:
                    x, y = location_shops
                    Utils.click(x, y, "Fechando tela!")
                    
                    capture_and_copy_screenshot()

                    time.sleep(2)
                else :
                    break # Sai do loop se não houver mais locations
                
    @staticmethod
    def clain_investor():
        while True:
            location_x = Utils.find_image("./images/investor.png", 5)
        
            if location_x:
                x, y = location_x
                Utils.click(x, y, "Abrindo investidor")
                time.sleep(1)
                
                capture_and_copy_screenshot()
                time.sleep(2)
                
                Automation.claim_reward()
            else:
                break
    
    def claim_reward():
        location_open = Utils.find_image("./images/claim.png", 5)
                        
        if location_open:
            x, y = location_open
            Utils.click(x, y, "Recebendo recompensas")
            time.sleep(1)
            capture_and_copy_screenshot()
            time.sleep(1)