from utils.image_utils import capture_and_save_screenshot
from globals.variables_start import GlobalsValues
from enums.cellphone_models import CellphoneModels
from functions.images_functions import ImagesFunctions as img
from adb_utils import adb_utils as adb
import time
from utils.image_utils import ImageUtils
import sys

#GlobalsValues.start_items(CellphoneModels.XIAOMI_POCO_X5_PRO)
capture_and_save_screenshot() 
GlobalsValues.start_items(CellphoneModels.XIAOMI_POCO_X5_PRO)
GlobalsValues.set_test_true()
time.sleep(1)
img.open_box()