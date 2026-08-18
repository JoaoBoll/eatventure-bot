import time


class StateMachine:

    # =====================================================
    # ESTADOS
    # =====================================================

    NORMAL = "NORMAL"
    RENOVATE = "RENOVATE"
    FOOD = "FOOD"
    NEW_POINT = "NEW_POINT"
    UPGRADE = "UPGRADE"

    def __init__(self, action_manager):

        self.action_manager = action_manager

        # Estado inicial do bot
        self.state = self.NORMAL

        # Momento da última ação executada
        self.last_action_time = 0

        # Intervalo mínimo entre ações
        self.action_cooldown = 0.5

        # Controle da espera após o long press
        self.up_food_wait_start = None

        # =================================================
        # EXPLORAÇÃO DA TELA
        # =================================================

        # Quanto tempo ficar sem encontrar nada
        # antes de fazer um swipe.
        self.exploration_delay = 5.0

        # Momento em que começou a ficar sem encontrar nada.
        self.last_detection_time = time.monotonic()

        # Direção atual
        self.swipe_direction = "up"

        # Quantos swipes já foram feitos nessa direção
        self.swipe_count = 0

        # Máximo de swipes consecutivos
        self.max_swipes = 5

    # =====================================================
    # UPDATE
    # =====================================================

    def update(self, detections):

        # A StateMachine sempre começa verificando
        # em qual estado ela está.

        if self.state == self.RENOVATE:

            self._renovate(
                detections
            )

        elif self.state == self.NEW_POINT:

            self._new_point(
                detections
            )

        elif self.state == self.FOOD:

            self._food(
                detections
            )

        elif self.state == self.UPGRADE:

            self._upgrade(
                detections
            )

        else:
            self._normal(
                detections
            )

    # =====================================================
    # NORMAL
    # =====================================================

    def _normal(self, detections):

        # =================================================
        #
        # !!!!!!!!!!!!!!!!!!!!!!!!!!! 
        #
        # FECHAR QUALQUER COISA QUE 
        # NÃO DEVERIA ESTAR ABERTA 
        #
        # !!!!!!!!!!!!!!!!!!!!!!!!!!!
        #
        # =================================================

        self._close_any_wrong_tab(detections)

        # =================================================
        # PRIORIDADE 1 → PLANE
        # =================================================

        plane = self._find(
            detections,
            "plane"
        )

        if plane is not None:

            self._reset_exploration()

            if not self._can_act():
                return

            print(
                "[ACTION] Plane encontrado."
            )

            self.action_manager.execute(
                "plane",
                plane
            )

            self.state = self.RENOVATE

            self.last_action_time = (
                time.monotonic()
            )

            return

        # =================================================
        # PRIORIDADE 2 → BUILD
        # =================================================

        build = self._find(
            detections,
            "build"
        )

        if build is not None:

            self._reset_exploration()

            if not self._can_act():
                return

            print(
                "[ACTION] Renovate encontrado."
            )

            self.action_manager.execute(
                "click",
                build
            )

            self.state = self.RENOVATE

            self.last_action_time = (
                time.monotonic()
            )

            return

        # =================================================
        # PRIORIDADE 3 → UPGRADE
        # =================================================
        #
        # Se encontrar o botão de upgrade,
        # ele tem prioridade sobre todo o resto.
        #
        # Exemplo:
        #
        # upgrade + food + plane
        #
        #        ↓
        #
        # somente upgrade será executado.
        
        upgrade = self._find(
            detections,
            "upgrade"
        )

        if upgrade is not None:

            if not self._can_act():
                return

            print(
                "[STATE] NORMAL → UPGRADE"
            )

            print(
                "[ACTION] Abrindo upgrade."
            )

            self.action_manager.execute(
                "upgrade",
                upgrade
            )

            # Depois de clicar em upgrade,
            # mudamos de estado.
            #
            # A partir daqui o bot NÃO deve mais
            # procurar ações normais.
            #
            # Agora ele deve procurar os itens
            # dentro da tela de upgrade.

            self.state = self.UPGRADE

            self.last_action_time = (
                time.monotonic()
            )

            return

        # =================================================
        # PRIORIDADE 4 → NEW POINT
        # =================================================

        new_point = self._find(
            detections,
            "new_point"
        )

        if new_point is not None:

            self._reset_exploration()
            
            if not self._can_act():
                return

            print(
                "[ACTION] New Point encontrado."
            )

            self.state = self.NEW_POINT

            self.action_manager.execute(
                "new_point",
                new_point
            )

            self.last_action_time = (
                time.monotonic()
            )

            return

        # =================================================
        # PRIORIDADE 5 → BOX
        # =================================================

        box = self._find(
            detections,
            "box"
        )

        if box is not None:

            self._reset_exploration()

            if not self._can_act():
                return

            print(
                "[ACTION] Box encontrado."
            )

            self.action_manager.execute(
                "open_box",
                box
            )

            self.last_action_time = (
                time.monotonic()
            )

            return

        # =================================================
        # PRIORIDADE 6 → FOOD
        # =================================================

        food = self._find(
            detections,
            "food",
            0.90
        )

        if food is not None:

            self._reset_exploration()

            if not self._can_act():
                return

            print(
                "[ACTION] Food encontrado."
            )

            self.state = self.FOOD

            self.action_manager.execute(
                "food",
                food
            )

            self.last_action_time = (
                time.monotonic()
            )

            return

        self._explore_screen();

    # =====================================================
    # FECHAR QUALQUER COISA QUE ESTEJA ABERTA.
    # =====================================================

    def _close_any_wrong_tab(self, detections):

        self._open_store(detections)

        self._close_any_x(detections)

        self._close_gray_max(detections)

        return

    # =================================================
    # CLOSE ANY "X"
    # =================================================
    #
    # Vai apertar em OPEN ao começar novo restaurante.

    def _close_any_x(self, detections):

        close_x = self._find(
            detections,
            "close"
        )

        if close_x is not None:
    
            self._reset_exploration()

            if not self._can_act():
                return

            print(
                "[ACTION] Fechando Abas."
            )

            self.action_manager.execute(
                "close",
                close_x
            )

            self.last_action_time = (
                time.monotonic()
            )

            return

        return

    # =================================================
    # OPEN NEW STORE
    # =================================================
    #
    # Vai apertar em OPEN ao começar novo restaurante.

    def _open_store(self, detections):
        
        open_store = self._find(
            detections,
            "open_store"
        )

        if open_store is not None:

            self._reset_exploration()

            if not self._can_act():
                return

            print(
                "[ACTION] Up Upgrade encontrado."
            )

            self.action_manager.execute(
                "open_store_click",
                open_store
            )

            self.last_action_time = (
                time.monotonic()
            )

            return


    def _close_gray_max(self, detections):
        gray_max = self._find(
            detections,
            "gray_max"
        )

        if gray_max is not None:

            self._reset_exploration()

            if not self._can_act():
                return

            print(
                "[ACTION] Close trash upgrade"
            )

            self.action_manager.execute(
                "gray_max",
                gray_max
            )

            self.last_action_time = (
                time.monotonic()
            )

            if self.FOOD == self.state:
                self.state = self.NORMAL


        return

    # =====================================================
    # RENOVATE
    # =====================================================

    def _renovate(self, detections):

        # =================================================
        # RENOVATE/PLANE
        # =================================================
        #
        # Vai fazer o renovate a partir do momento que encontrar o icone da moeda.

        renovate = self._find(
            detections,
            "renovate_coin"
        )

        if renovate is not None:

            if not self._can_act():
                return

            print(
                "[ACTION] Up Upgrade encontrado."
            )

            self.action_manager.execute(
                "renovate_click",
                renovate
            )

            self.state = self.NORMAL

            self.last_action_time = (
                time.monotonic()
            )

            return

        fly = self._find(
            detections,
            "fly"
        )

        if fly is not None:

            if not self._can_act():
                return

            print(
                "[ACTION] Up Upgrade encontrado."
            )

            self.action_manager.execute(
                "renovate_click",
                fly
            )

            self.state = self.NORMAL

            self.last_action_time = (
                time.monotonic()
            )

            return

        return

    # =====================================================
    # UPGRADE
    # =====================================================

    def _upgrade(self, detections):

        # =================================================
        # PRIORIDADE 1 → UP UPGRADE
        # =================================================
        #
        # Estamos dentro da tela de upgrade.
        #
        # Agora procuramos especificamente pelos
        # itens que podem ser evoluídos.
        #
        # IMPORTANTE:
        #
        # Depois de clicar, continuamos no estado UPGRADE.
        #
        # No próximo frame vamos procurar novamente.
        #
        # Isso permite:
        #
        # up_upgrade
        #      ↓
        #    CLICK
        #      ↓
        # próximo frame
        #      ↓
        # up_upgrade
        #      ↓
        #    CLICK
        #

        up_upgrade = self._find(
            detections,
            "up_upgrade"
        )

        if up_upgrade is not None:

            if not self._can_act():
                return

            print(
                "[ACTION] Up Upgrade encontrado."
            )

            self.action_manager.execute(
                "upgrade_item",
                up_upgrade
            )

            self.last_action_time = (
                time.monotonic()
            )

            # NÃO mudamos o estado.
            #
            # Continuamos em UPGRADE para que,
            # no próximo frame, possamos procurar
            # outro up_upgrade.

            return

        else: 

            # =================================================
            # PRIORIDADE 2 → CLOSE
            # =================================================
            #
            # Se não encontramos nenhum up_upgrade,
            # verificamos se existe o botão de fechar.
            #
            # Isso significa que provavelmente não há
            # mais upgrades para fazer.
            #

            close = self._find(
                detections,
                "close"
            )

            if close is not None:

                if not self._can_act():
                    return

                print(
                    "[ACTION] Close encontrado."
                )

                self.action_manager.execute(
                    "close",
                    close
                )

                self.last_action_time = (
                    time.monotonic()
                )

                # Voltamos para o estado NORMAL.
                #
                # No próximo frame o bot volta a procurar:
                #
                # upgrade
                # plane
                # food
                # new_point

                self.state = self.NORMAL

                print(
                    "[STATE] UPGRADE → NORMAL"
                )

                return

        # =================================================
        # NENHUMA AÇÃO
        # =================================================
        #
        # Se chegou aqui:
        #
        # - não encontrou up_upgrade
        # - não encontrou close
        #
        # Então simplesmente espera o próximo frame.
        #

        return

    # =====================================================
    # NEW POINT
    # =====================================================

    def _new_point(self, detections):
    
        # =================================================
        # UP FOOD - New Point
        # =================================================
        #
        # Estamos no estado NEW_POINT
        #
        # Procuramos pelo botão que permite fazer
        # o upgrade da comida, porém diferente do FOOD,
        # este apenas da um click para liberar.
        #

        unlock_food = self._find(
            detections,
            "up_food"
        )

        # =================================================
        # ENCONTROU UNLOCK FOOD
        # =================================================

        if unlock_food is not None:

            if not self._can_act():
                return

            print(
                "[ACTION] Up Food encontrado."
            )

            # Segura o botão por 4 segundos.
            self.action_manager.execute(
                "new_point_click",
                unlock_food
            )

            # Marca o momento em que terminamos
            # o upgrade.
            self.up_food_wait_start = (
                time.monotonic()
            )

            return

        # =================================================
        # NÃO ENCONTROU UP FOOD
        # =================================================
        #
        # Pode ser que o jogo ainda esteja atualizando
        # a tela depois do long press.
        #
        # Então esperamos até 2 segundos.
        #

        if self.up_food_wait_start is not None:

            elapsed = (
                time.monotonic()
                - self.up_food_wait_start
            )

            # Ainda estamos dentro dos 2 segundos
            # de espera.

            if elapsed < 2.0:

                return

            # =================================================
            # PASSOU 2 SEGUNDOS SEM UP FOOD
            # =================================================

            print(
                "[STATE] Nenhum Up Food apareceu "
                "em 2 segundos."
            )

            print(
                "[STATE] UP_FOOD → NORMAL"
            )

            self.state = self.NORMAL

            self.up_food_wait_start = None

            return

        # =================================================
        # PRIMEIRA VEZ NO ESTADO
        # =================================================

        return

    # =====================================================
    # FOOD
    # =====================================================

    def _food(self, detections):
    
        # =================================================
        # UP FOOD
        # =================================================
        #
        # Estamos no estado UP_FOOD.
        #
        # Procuramos pelo botão que permite fazer
        # o upgrade da comida.
        #

        up_food = self._find(
            detections,
            "up_food"
        )

        # =================================================
        # ENCONTROU UP FOOD
        # =================================================

        if up_food is not None:

            if not self._can_act():
                return

            print(
                "[ACTION] Up Food encontrado."
            )

            # Segura o botão por 4 segundos.
            self.action_manager.execute(
                "upgrade_food",
                up_food
            )

            self.last_action_time = (
                time.monotonic()
            )

            # Marca o momento em que terminamos
            # o upgrade.
            self.up_food_wait_start = (
                time.monotonic()
            )

            print(
                "[STATE] Aguardando próximo Up Food..."
            )

            return

        else:
            self._close_gray_max(detections)
            

        # =================================================
        # NÃO ENCONTROU UP FOOD
        # =================================================
        #
        # Pode ser que o jogo ainda esteja atualizando
        # a tela depois do long press.
        #
        # Então esperamos até 2 segundos.
        #

        if self.up_food_wait_start is not None:

            elapsed = (
                time.monotonic()
                - self.up_food_wait_start
            )

            # Ainda estamos dentro dos 2 segundos
            # de espera.

            if elapsed < 2.0:

                return

            # =================================================
            # PASSOU 2 SEGUNDOS SEM UP FOOD
            # =================================================

            print(
                "[STATE] Nenhum Up Food apareceu "
                "em 2 segundos."
            )

            print(
                "[STATE] UP_FOOD → NORMAL"
            )

            self.state = self.NORMAL

            self.up_food_wait_start = None

            return

        # =================================================
        # PRIMEIRA VEZ NO ESTADO
        # =================================================

        return

    
    # =====================================================
    # FIND
    # =====================================================

    def _find(
        self,
        detections,
        category,
        min_confidence=0.80
    ):

        for detection in detections:

            # Categoria precisa ser a correta
            if detection["category"] != category:
                continue

            # Confiança precisa atingir o mínimo
            if detection["confidence"] < min_confidence:
                continue

            return detection

        return None

    # =====================================================
    # COOLDOWN
    # =====================================================

    def _can_act(self):

        now = time.monotonic()

        # Só permite uma nova ação depois que
        # o cooldown tiver passado.

        return (
            now - self.last_action_time
            >= self.action_cooldown
        )

    # =====================================================
    # RESET
    # =====================================================

    def reset(self):

        self.state = self.NORMAL

        self.last_action_time = 0

        print(
            "[STATE] Reset → NORMAL"
        )

    # =====================================================
    # SCROLL
    # =====================================================

    def _reset_exploration(self):
    
        self.last_detection_time = time.monotonic()

        self.swipe_count = 0

    def _explore_screen(self):
    
        now = time.monotonic()

        # Ainda não passou tempo suficiente.
        if (
            now - self.last_detection_time
            < self.exploration_delay
        ):
            return

        # =================================================
        # FAZ SWIPE
        # =================================================

        print(
            f"[EXPLORE] Swipe "
            f"{self.swipe_direction.upper()} "
            f"{self.swipe_count + 1}/"
            f"{self.max_swipes}"
        )

        self.action_manager.swipe(
            self.swipe_direction
        )

        self.swipe_count += 1

        # Reinicia o contador de espera.
        self.last_detection_time = now

        # =================================================
        # CHEGOU NO LIMITE?
        # =================================================

        if self.swipe_count >= self.max_swipes:

            # Inverte a direção
            if self.swipe_direction == "up":

                self.swipe_direction = "down"

            else:

                self.swipe_direction = "up"

            # Começa novamente do 1
            self.swipe_count = 0

    def swipe(self, direction):
        
        if direction == "up":

            print("[ACTION] SWIPE UP")

            self.android.swipe(
                540,   # x inicial
                1800,  # y inicial
                540,   # x final
                600,   # y final
                500    # duração em ms
            )

        elif direction == "down":

            print("[ACTION] SWIPE DOWN")

            self.android.swipe(
                540,
                600,
                540,
                1800,
                500
            )