import time
import random

from actions.android import AndroidActions

class ActionManager:

    def __init__(self):

        self.android = AndroidActions()

    # =====================================================
    # EXECUTE
    # =====================================================

    def execute(
        self,
        action,
        detection
    ):

        if detection is None:
            return

        x = detection["x"]
        y = detection["y"]

        width = detection["width"]
        height = detection["height"]

        # =================================================
        # CENTRO DO OBJETO
        # =================================================

        click_x = (
            x + width // 2
        )

        click_y = (
            y + height // 2
        )

        # =================================================
        # ACTION
        # =================================================

        if action == "click":
            self._click(
                click_x,
                click_y,
                "open_renovate"
            )

        elif action == "food":

            self._food(
                click_x,
                click_y
            )

        elif action == "open_renovate":
            self._click(
                click_x,
                click_y,
                "open_renovate"
            )

        elif action == "renovate_click":
            self._click(
                click_x,
                click_y,
                "renovate_click"
            )

        elif action == "open_store_click":
            self._click(
                click_x,
                click_y,
                "open_store_click"
            )

        elif action == "upgrade":

            self._upgrade(
                click_x,
                click_y
            )

        elif action == "plane":

            self._plane(
                click_x,
                click_y
            )

        elif action == "new_point":

            self._click(
                click_x,
                click_y,
                "new_point"
            )

            
        elif action == "new_point_click":

            self._click(
                click_x,
                click_y,
                "new_point_click"
            )

        elif action == "upgrade_item":

            self._upgrade_item(
                click_x,
                click_y
            )

        elif action == "close":
    
            self._close(
                click_x,
                click_y
            )

        elif action == "upgrade_food":
            self._upgrade_food(
                click_x,
                click_y
            )

        elif action == "open_box":

            self._click(
                click_x,
                click_y, 
                "open_box"
            )

        elif action == "gray_max":
            self._click(
                10,
                2200, 
                "gray_max"
            )
        else:

            print(
                f"[ACTION] Ação desconhecida: "
                f"{action}"
            )

    # =====================================================
    # SIMPLE CLICK
    # =====================================================
    
    def _click(
        self, x, y, action
    ):
        print(
            f"[ACTION] {action} → "
            f"click ({x}, {y})"
        )

        self.android.click(
            x,
            y
        )

    # =====================================================
    # FOOD
    # =====================================================

    def _food(
        self,
        x,
        y
    ):

        print(
            f"[ACTION] FOOD → "
            f"click ({x}, {y})"
        )

        self.android.click(
            x,
            y
        )

    # =====================================================
    # UPGRADE
    # =====================================================

    def _upgrade(
        self,
        x,
        y
    ):

        print(
            f"[ACTION] UPGRADE → "
            f"click ({x}, {y})"
        )

        self.android.click(
            x,
            y
        )

    # =====================================================
    # UPGRADE ITEM
    # =====================================================

    def _upgrade_item(self, x, y):
    
        print(
            f"[ACTION] UPGRADE ITEM → "
            f"CLICK ({x}, {y})"
        )

        for _ in range(5):
    
            self.android.click(
                x,
                y
            )

            # Intervalo aleatório entre os cliques
            delay = random.uniform(
                0.08,
                0.18
            )

            time.sleep(delay)
    # =====================================================
    # PLANE
    # =====================================================

    def _plane(
        self,
        x,
        y
    ):

        print(
            f"[ACTION] PLANE → "
            f"click ({x}, {y})"
        )

        self.android.click(
            x,
            y
        )

    # =====================================================
    # NEW POINT
    # =====================================================

    def _new_point(self, x, y):
    
        print(
            f"[ACTION] NEW POINT → "
            f"click ({x}, {y})"
        )

        self.android.click(
            x,
            y
        )
        
    # =====================================================
    # CLOSE
    # =====================================================

    def _close(self, x, y):
    
        print(
            f"[ACTION] CLOSE → "
            f"click ({x}, {y})"
        )

        self.android.click(
            x,
            y
        )
    
    # =====================================================
    # UP FOOD
    # =====================================================

    def _upgrade_food(self, x, y):
    
        print(
            f"[ACTION] UP FOOD → "
            f"press ({x}, {y}) por 4 segundos"
        )

        self.android.press(
            x,
            y,
            duration=4.0
        )

        
    # =====================================================
    # OPEN BOX
    # =====================================================

    def _open_box(self, x, y):
    
        print(
            f"[ACTION] CLOSE → "
            f"click ({x}, {y})"
        )

        self.android.click(
            x,
            y
        )

    # =====================================================
    # SWIPE
    # =====================================================

    def swipe(self, direction):
    
        x = 540
        y = 1200
        distance = 500
        duration = 500

        if direction == "up":

            print("[ACTION] SWIPE UP")

            self.android.swipe(
                x,
                y,
                x,
                y - distance,
                duration
            )

        elif direction == "down":

            print("[ACTION] SWIPE DOWN")

            self.android.swipe(
                x,
                y,
                x,
                y + distance,
                duration
            )