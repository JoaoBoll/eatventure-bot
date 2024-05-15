import subprocess
import os
from adb_utils import adb_utils
from automation.automation import Automation

# Nome do pacote do aplicativo que estamos controlando
eat_venture_package = 'com.hwqgrhhjfd.idlefastfood'

def capture_and_save_multiple_screenshots(path):
    
    for i in range(30):
        # Nome do arquivo de captura de tela no dispositivo
        screenshot_filename = f"/sdcard/screenshot{i+1}.png"

        # Comando ADB para capturar a tela
        adb_capture_command = f"adb shell screencap {screenshot_filename}"

        # Captura a tela no dispositivo
        try:
            subprocess.run(adb_capture_command, shell=False)
        except subprocess.CalledProcessError as e:
            print(f"Erro ao capturar a tela: {e}")
            return None

        # Caminho para a pasta do projeto onde deseja salvar a captura de tela


        # Comando ADB para copiar a captura de tela para a pasta path caso não nula.
        adb_pull_command = f"adb pull {screenshot_filename} {path}"

        # Copia a captura de tela para a pasta do projeto
        try:
            subprocess.run(adb_pull_command, shell=True)
        except subprocess.CalledProcessError as e:
            print(f"Erro ao copiar a captura de tela: {e}")

def testUpgrade():
    return None

def testCloseX():
    Automation.find_close_x()
    
