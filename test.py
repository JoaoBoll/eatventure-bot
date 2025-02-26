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
#adb.swipe_up(500, 400, 500, 1250)

location_next_city = ImageUtils.find_image("renovate_city_coin.png", 5)

if location_next_city:
    print("build?")

    x, y = location_next_city
    adb.press(x + 50, y)
    
    time.sleep(0.7)
    capture_and_save_screenshot()
    time.sleep(0.7)