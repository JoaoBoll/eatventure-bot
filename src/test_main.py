import cv2

from capture.screen import ScreenCapture


WINDOW_NAME = "AI Raw"


capture = ScreenCapture()


try:

    capture.start()

    cv2.namedWindow(
        WINDOW_NAME,
        cv2.WINDOW_NORMAL
    )

    cv2.resizeWindow(
        WINDOW_NAME,
        500,
        900
    )

    while True:

        frame = capture.get_frame()

        if frame is None:
            continue

        cv2.imshow(
            WINDOW_NAME,
            frame
        )

        if cv2.waitKey(1) & 0xFF == 27:
            break


except KeyboardInterrupt:

    print(
        "\nPrograma interrompido pelo usuário."
    )


finally:

    capture.stop()

    cv2.destroyAllWindows()