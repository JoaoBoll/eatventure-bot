import numpy as np
from adb_utils.adb_utils import capture_and_copy_screenshot
from utils.utils_test import UtilsTest
from automation.automation import Automation
from utils.utils import Utils
import time

'''

while True:
    capture_and_copy_screenshot()
    time.sleep(1.25)
    
    location_arrow = Utils.find_image_generic("images/food_upgrade_arrow.png", 5, False)
    
    if location_arrow:
        x, y = location_arrow
        Utils.click(x,y, "Abrindo upgrade de comida")                        
        capture_and_copy_screenshot()
        
        time.sleep(1)
        x, y = location_arrow
        y=y+50
        print (f'x {x} y {y}')
        Utils.click(x,y, "Fechando upgrade")
        
        capture_and_copy_screenshot()
utils.find_image("images/upgrade_food.png")


'''



time.sleep(1)
UtilsTest.find_image("images/investor_3.jpg")



'''

while True:
time.sleep(1)
    capture_and_copy_screenshot()
    time.sleep(2)
    Automation.up_food()
'''

