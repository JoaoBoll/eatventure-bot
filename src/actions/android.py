import subprocess


class AndroidActions:

    def __init__(self):

        self.adb = "adb"

    # =====================================================
    # CLICK
    # =====================================================

    def click(self, x, y):

        subprocess.run(
            [
                self.adb,
                "shell",
                "input",
                "tap",
                str(int(x)),
                str(int(y)),
            ],
            check=True,
        )

    # =====================================================
    # PRESS / LONG PRESS
    # =====================================================

    def press(self, x, y, duration=4.0):

        duration_ms = int(
            duration * 1000
        )

        print(
            f"[ANDROID] Long press "
            f"({x}, {y}) → {duration}s"
        )

        subprocess.run(
            [
                "adb",
                "shell",
                "input",
                "swipe",
                str(x),
                str(y),
                str(x),
                str(y),
                str(duration_ms),
            ],
            check=True
        )

    # =====================================================
    # SWIPE
    # =====================================================

    def swipe(
        self,
        x1,
        y1,
        x2,
        y2,
        duration=300,
    ):

        subprocess.run(
            [
                self.adb,
                "shell",
                "input",
                "swipe",
                str(int(x1)),
                str(int(y1)),
                str(int(x2)),
                str(int(y2)),
                str(int(duration)),
            ],
            check=True,
        )

    # =====================================================
    # BACK
    # =====================================================

    def back(self):

        subprocess.run(
            [
                self.adb,
                "shell",
                "input",
                "keyevent",
                "4",
            ],
            check=True,
        )