# Variáveis global
import json
import os
from enums.identifiers import Identifiers
from globals import globals

class GlobalsValues:
        
    def start_items(config_name: Identifiers=None):
        
        # Inicialize a classe
        globals.log = {}
        globals.log["QUANT_INVEST_SUCCES"] = 1
        globals.log["ACTION"] = 'testando'
        globals.log["BOOST_GOLD"] = 3
        globals.log["INVESTOR"] = 4
        globals.log["REWARDS"] = 5
        
        if config_name:
            globals.config_name = config_name.value            
        """
            Configura variáveis globais com base no arquivo de configuração e no nome de configuração passado.
            :param config_name_str: Nome do arquivo de configuração a ser carregado.
        """
               
        try:
            globals.config_name_str = config_name.value
            # Construir o caminho para o arquivo de configuração
            config_path = os.path.join('./configs', globals.config_name_str + '.json')

            # Carregar o arquivo de configuração
            with open(config_path, 'r') as file:
                config_data = json.load(file)

            # Definir variáveis globais com base nas configurações
            # globals.valor_global_1 = config_data.get('', 42)
            globals.claim_reward_x = config_data.get('claim_reward_x', None)
            globals.claim_reward_y = config_data.get('claim_reward_y', None)
            globals.up_upgrade_x = config_data.get('up_upgrade_x', None)
            globals.up_upgrade_y = config_data.get('up_upgrade_y', None)
            globals.close_upgrades_x = config_data.get('close_upgrades_x', None)
            globals.close_upgrades_y = config_data.get('close_upgrades_y', None)


        except FileNotFoundError:
            print(f"Arquivo de configuração '{config_path}' não encontrado.")
        except json.JSONDecodeError:
            print(f"Erro ao analisar arquivo JSON '{config_path}'.")
        except Exception as e:
            print(f"Ocorreu um erro ao carregar '{config_path}': {e}")
    
    def set_test_true():
        globals.is_test.__setattr__(True)


    
    #Classe para armazenar e gerenciar variáveis globais.
    #Implementa o padrão Singleton para garantir que apenas uma instância exista.

'''
    # Variável de classe para armazenar a instância única
    _instance = None
    log = {}
    config_name_str = None

    def __new__(cls, *args, **kwargs):
        # Verifica se a instância única já existe
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @staticmethod
    def get_instance():
        """
        Retorna a instância única de GlobalsValues.
        """
        if GlobalsValues._instance is None:
            raise RuntimeError("GlobalsValues ainda não foi inicializado.")
        return GlobalsValues._instance
        
    def __init__(globals, config_name: Identifiers=None):
        # Inicializa variáveis globais com valores padrão e configurações baseadas no arquivo
        if hasattr(globals, '_initialized') and globals._initialized:
            return
        
        globals.log = {}
        
        if config_name:
            globals.config_name = config_name.value
            # Configurar variáveis globais com base no arquivo de configuração
            globals.start_items(config_name.value + '.json')
        
        globals._initialized = True  # Marcar a instância como inicializada

    @staticmethod
    def start_items(globals, config_name_str):
        """
        Configura variáveis globais com base no arquivo de configuração e no nome de configuração passado.

        :param config_name_str: Nome do arquivo de configuração a ser carregado.
        """
        if (globals.log != None):
            globals.log = {}
        
        try:
            globals.config_name_str = config_name_str
            # Construir o caminho para o arquivo de configuração
            config_path = os.path.join('./configs', config_name_str)

            # Carregar o arquivo de configuração
            with open(config_path, 'r') as file:
                config_data = json.load(file)

            # Definir variáveis globais com base nas configurações
            # globals.valor_global_1 = config_data.get('', 42)
            globals.valor_global_2 = config_data.get('valor_global_2', 'Outro valor global')
            globals.screenResolution = config_data.get('screenResolution', [1920, 1080])

        except FileNotFoundError:
            print(f"Arquivo de configuração '{config_path}' não encontrado.")
        except json.JSONDecodeError:
            print(f"Erro ao analisar arquivo JSON '{config_path}'.")
        except Exception as e:
            print(f"Ocorreu um erro ao carregar '{config_path}': {e}")
'''    