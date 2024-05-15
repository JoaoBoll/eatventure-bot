import subprocess
import os
from utils.mesage_utils import MessageUtils as message

# Nome do pacote do aplicativo que estamos controlando
eat_venture_package = 'com.hwqgrhhjfd.idlefastfood'

# Função para iniciar o aplicativo
def open_app():
    try:
        # Abrir o aplicativo usando o comando adb
        # cmd: adb shell monkey -p com.hwqgrhhjfd.idlefastfood -c android.intent.category.LAUNCHER 1
        with open(os.devnull, 'w') as null_file:
            subprocess.run(['adb', 'shell', 'monkey', '-p', eat_venture_package, '-c', 'android.intent.category.LAUNCHER', '1'], stdout=null_file, stderr=null_file)
        print(f'Eatventure foi aberto com sucesso.')

    except Exception as e:
        print(f"Erro ao abrir o aplicativo: {str(e)}")

#Função para validar se está aberto
def app_is_open():
    try:
        # Execute o comando adb para listar as atividades em execução
        # cmd(Filtrando o eatventure) adb shell dumpsys activity activities | findstr com.hwqgrhhjfd.idlefastfood
        result = subprocess.run(['adb', 'shell', 'dumpsys', 'activity', 'activities'], capture_output=True, text=True)

        # Verifique se o nome do pacote está na saída do comando
        if eat_venture_package in result.stdout:
            return True  # O aplicativo está aberto
        else:
            return False  # O aplicativo não está aberto

    except Exception as e:
        print(f"Erro ao verificar se o aplicativo já está aberto: {str(e)}")
        return False  # Em caso de erro, assumimos que o aplicativo não está aberto

# Função valida se a tela está em 1° plano.
def is_screen_in_focus():
    print("execute")
    try:
        # Execute o comando adb para obter informações sobre a atividade em primeiro plano
        result = subprocess.run(['adb', 'shell', 'dumpsys', 'activity', 'activities'], capture_output=True, text=True)

        # Procure a linha que contém a atividade em primeiro plano (foreground)
        for line in result.stdout.splitlines():
            if 'topActivity' in line and eat_venture_package in line:
                # A tela está em foco
                return True  
            
        # A tela não está em foco
        return False  

    except Exception as e:
        print(f"Erro ao verificar se tela está em 1° plano: {str(e)}")
        # Em caso de erro, assumimos que a tela não está em foco
        return False

# Traz para a frente caso não esteja
def bring_to_foreground():
    try:
        # Execute o comando adb para trazer a atividade principal do aplicativo "eatventure" para o primeiro plano
        subprocess.run(['adb', 'shell', 'am', 'start', '-n', 'com.hwqgrhhjfd.idlefastfood/.MainActivity'])

        print("A tela do aplicativo eatventure foi trazida para o primeiro plano com sucesso.")

    except Exception as e:
        print(f"Erro ao trazer a tela do aplicativo para o primeiro plano: {str(e)}")

def capture_and_save_screenshot(project_folder="./"):
    # Nome do arquivo de captura de tela no dispositivo
    screenshot_filename = "/sdcard/screenshot.png"

    # Comando ADB para capturar a tela
    adb_capture_command = f"adb shell screencap {screenshot_filename}"

    # Captura a tela no dispositivo
    try:
        subprocess.run(adb_capture_command, shell=False)
    except subprocess.CalledProcessError as e:
        print(f"Erro ao capturar a tela:\n{e}")
        return None

    # Caminho para a pasta do projeto onde deseja salvar a captura de tela


    # Comando ADB para copiar a captura de tela para a pasta do projeto
    adb_pull_command = f"adb pull {screenshot_filename} {project_folder}"

    # Copia a captura de tela para a pasta do projeto
    try:
        subprocess.run(adb_pull_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
        return project_folder
    except subprocess.CalledProcessError as e:
        print(f"Erro ao copiar a captura de tela: {e}")
        return None

def swipe_up(x1, y1, x2, y2, duration=500):
    """
    Simula um gesto de arrastar para cima na tela do dispositivo Android.

    Args:
        x1 (int): Coordenada x de início do gesto.
        y1 (int): Coordenada y de início do gesto.
        x2 (int): Coordenada x de fim do gesto.
        y2 (int): Coordenada y de fim do gesto.
        duration (int): Duração do gesto em milissegundos (padrão: 500).
    """
    adb_swipe_up_command = f"adb shell input swipe {x1} {y1} {x2} {y2} {duration}"
    subprocess.run(adb_swipe_up_command, shell=True)

def swipe_down(x1, y1, x2, y2, duration=500):
    """
    Simula um gesto de arrastar para baixo na tela do dispositivo Android.

    Args:
        x1 (int): Coordenada x de início do gesto.
        y1 (int): Coordenada y de início do gesto.
        x2 (int): Coordenada x de fim do gesto.
        y2 (int): Coordenada y de fim do gesto.
        duration (int): Duração do gesto em milissegundos (padrão: 500).
    """
    adb_swipe_down_command = f"adb shell input swipe {x1} {y1} {x2} {y2} {duration}"
    subprocess.run(adb_swipe_down_command, shell=True)

def press_and_hold(x, y, duration_seconds):
    try:
        # Comando adb para pressionar e segurar na tela
        adb_command = f"adb shell input swipe {x} {y} {x} {y} {int(duration_seconds * 1000)}"

        # Executar o comando adb
        subprocess.run(adb_command, shell=True, check=True)
        print(f'Pressionando e segurando em ({x}, {y}) por {duration_seconds} segundos.')

    except subprocess.CalledProcessError as e:
        print(f"Erro ao pressionar e segurar: {str(e)}")
    except Exception as e:
        print(f"Erro inesperado: {str(e)}")
        
def press(x, y):
    try:
        # Comando adb para pressionar e segurar na tela
        adb_command = f"adb shell input swipe {x} {y} {x} {y}"

        # Executar o comando adb
        subprocess.run(adb_command, shell=True, check=True)
        print(f'Pressionado em ({x}, {y})')

    except subprocess.CalledProcessError as e:
        print(f"Erro ao pressionar e segurar: {str(e)}")
    except Exception as e:
        print(f"Erro inesperado: {str(e)}")

import subprocess

def press_multiple_times(x, y, times):
    try:
        # Comando adb para pressionar um ponto específico na tela
        adb_command = f"adb shell input tap {x} {y}"

        for i in range(times):
            # Executar o comando adb para pressionar o ponto específico
            subprocess.run(adb_command, shell=True, check=True)

    except subprocess.CalledProcessError as e:
        print(f"Erro ao pressionar: {str(e)}")
    except Exception as e:
        print(f"Erro inesperado: {str(e)}")

# Exemplo de uso da função
# press_multiple_times(x, y, times)
