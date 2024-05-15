from enum import Enum

class CellphoneModels(Enum):
    """
    Modelos de Celulares:
    """
    
    XIAOMI_POCO_X5_PRO = 'xiaomi_poco_x5_pro'
    
    def __str__(self):
        # Retorna o valor associado ao identificador quando convertido para string
        return self.value