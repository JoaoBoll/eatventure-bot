Requisitos:

ADB (Adicionar no PAH nas variáveis de ambiente caso windows): https://www.xda-developers.com/install-adb-windows-macos-linux/

Python 3.12.3 (Usada no desenvolvimento)

---

Windows:

Validar se python está instalado:

python3 --version

Criar ambiente virtual:

python -m venv eatventure-venv

usar ambiente:

.\eatventure-venv\Scripts\activate

(Pode ser necessário mudar as politicas de execução do windows:
    - No powershell: Set-ExecutionPolicy RemoteSigned -Scope CurrentUser)

Instalar requisitos:

pip install -r requirements.txt

Rodar:

python3 eatventure_bot.py


<!---
Node 18.18.0 (Usada no desenvolvimento)

---

NODE:

instalar: 
npm install -g appium

---

ADB (OBRIGATÓRIO, SEM ISSO, NADA FUNCIONA! Provável que sem os outros também...)
Link para instruções: https://developer.android.com/studio/command-line/adb?hl=pt-br

---

Windows

Python:https://www.python.org/downloads/

Criar ambiente virtual:

python -m venv eatventure-venv

Ativar o ambiente:

(Windows)
.\eatventure-venv\Scripts\activate

Download SWIG
https://www.swig.org/download.html