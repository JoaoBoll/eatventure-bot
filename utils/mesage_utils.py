from globals import globals as g
from enums.identifiers import Identifiers

class MessageUtils:
    """
    Classe para gerenciar mensagens no console.
    """

    @staticmethod
    def update_console():
        """
        Atualiza o console com base nas variáveis globais armazenadas em g.log.
        """
        # Crie uma nova tabela com os dados em g.log
    

    @staticmethod
    def add_item_log(key, value):
        """
        Adiciona ou atualiza um item no dicionário global `g.log`.
        Atualiza o console em seguida.

        :param key: Chave para o novo item.
        :param value: Valor para o novo item.
        """
        # Adiciona ou atualiza o item em `g.log`
        g.log[key] = value
        # Atualiza o console com `update_console()`
        MessageUtils.update_console()

    @staticmethod
    def delete_item_log(key):
        """
        Exclui um item do dicionário global `g.log`.
        Atualiza o console em seguida.

        :param key: Chave do item a ser excluído.
        """
        # Exclui o item de `g.log`
        if key in g.log:
            del g.log[key]
        # Atualiza o console com `update_console()`
        MessageUtils.update_console()

    @staticmethod
    def show_log(message):
        print(message)