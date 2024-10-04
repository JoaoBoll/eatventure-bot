from utils.image_utils import capture_and_save_screenshot
from globals.variables_start import GlobalsValues
from enums.cellphone_models import CellphoneModels
from functions.images_functions import ImagesFunctions
from adb_utils import adb_utils as adb
import time
from utils.image_utils import ImageUtils
import sys

#GlobalsValues.start_items(CellphoneModels.XIAOMI_POCO_X5_PRO)
capture_and_save_screenshot() 
GlobalsValues.start_items(CellphoneModels.XIAOMI_POCO_X5_PRO)
GlobalsValues.set_test_true()
time.sleep(1)
location_next_city = ImageUtils.find_image("build.png", 5)
if location_next_city:
    x, y = location_next_city
    adb.press(x, y)
    
    #capture_and_save_screenshot()
    
    time.sleep(2)
    
    location_Open = ImageUtils.find_image("upgrade_city.png", 5)
    
    if location_Open:
        x, y = location_Open
        adb.press(x, y)
        time.sleep(15)
        ImagesFunctions.reset_globals_new_city()

else:
    location_plane = ImageUtils.find_image_with_threshold("plane.png", 5, 0.95)
                
    if location_plane:
        x, y = location_plane
        adb.press(x, y)
        
        #capture_and_save_screenshot()
        
        location_fly = ImageUtils.find_image("fly.png", 3)
        if location_fly:
            x, y = location_fly
            adb.press(x, y)
            time.sleep(15)
            ImagesFunctions.reset_globals_new_city()

