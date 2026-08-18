import threading
import time


class VisionWorker:

    def __init__(
        self,
        detector,
        interval=0.1,
    ):

        self.detector = detector

        # 0.1 = aproximadamente 10 análises/segundo
        self.interval = interval

        self.running = False
        self.thread = None

        self.latest_frame = None
        self.latest_detections = []

        self.frame_lock = threading.Lock()
        self.detection_lock = threading.Lock()

    # =====================================================
    # START
    # =====================================================

    def start(self):

        if self.running:
            return

        self.running = True

        self.thread = threading.Thread(
            target=self._run,
            daemon=True,
        )

        self.thread.start()

    # =====================================================
    # SET FRAME
    # =====================================================

    def set_frame(self, frame):

        with self.frame_lock:

            self.latest_frame = frame

    # =====================================================
    # GET DETECTIONS
    # =====================================================

    def get_detections(self):

        with self.detection_lock:

            return list(
                self.latest_detections
            )

    # =====================================================
    # WORKER
    # =====================================================

    def _run(self):

        while self.running:

            # ---------------------------------------------
            # Pega somente o frame mais recente e descarta
            # os atrasados para não ficar com backlog.
            # ---------------------------------------------

            with self.frame_lock:

                frame = self.latest_frame
                self.latest_frame = None

            if frame is None:

                time.sleep(0.01)

                continue

            frame = frame.copy()

            # ---------------------------------------------
            # Detector
            # ---------------------------------------------

            try:

                detections = self.detector.detect(
                    frame
                )

                with self.detection_lock:

                    self.latest_detections = detections

            except Exception as e:

                print(
                    f"Erro no detector: {e}"
                )

            # ---------------------------------------------
            # Intervalo entre análises
            # ---------------------------------------------

            time.sleep(
                self.interval
            )

    # =====================================================
    # STOP
    # =====================================================

    def stop(self):

        self.running = False

        if self.thread:

            self.thread.join(
                timeout=2
            )

            self.thread = None