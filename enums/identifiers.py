from enum import Enum

class Identifiers(Enum):
    """
    Enumeração para definir identificadores para as linhas do console.
    """
    RUNNING_TIMES='RUNNING_TIMES'
    QUANT_INVEST_SUCCES= 'QUANT_INVEST_SUCCES'
    ACTION = '\rAção:'
    BOOST_GOLD = '\rBoost:'
    UP_FOOD = '\rFood:'
    INVESTOR = '\rInvestor:'
    REWARDS = '\rRewards:'

    def __str__(self):
        # Retorna o valor associado ao identificador quando convertido para string
        return self.value