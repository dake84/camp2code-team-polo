#!/usr/bin/env python3

# Information: Module mit Basisklasse fuer Camera fuer Projekt RPiCar

# File name: basisklassen_cam.py

# Author (Original): Robert Heise (FIDA)

# Date created: 11/10/2021

# Date last modified: 18/10/2026 (Picamera2 / Debian 13)

# Usage: U4I/FIDA "Autonomes Fahren" Sunfounder PiCar-S

import cv2

import numpy as np


class Camera:

    def __init__(

        self,

        devicenumber: int = 0,

        buffersize: int = 1,

        skip_frame: int = 0,

        height: int = None,

        width: int = None,

        flip: bool = True,

        colorspace: str = "bgr",

        backend: str = "auto",

    ) -> None:

        """Initializes Camera.

        Args:

            devicenumber (int): Kameranummer (je nach Backend). Defaults to 0.

            buffersize (int): OpenCV-Puffergroesse bzw. mindestens zwei

                Aufnahmebuffer bei Picamera2. Defaults to 1.

            skip_frame (int): Anzahl zu verwerfender Frames. Defaults to 0.

            height (int): Bildhoehe. None verwendet die Kameravorgabe.

            width (int): Bildbreite. None verwendet die Kameravorgabe.

            flip (bool): Dreht das Bild um 180 Grad. Defaults to True.

            colorspace (str): 'bgr', 'rgb' oder 'gray'. Defaults to 'bgr'.

            backend (str): 'auto', 'picamera2' oder 'opencv'.

                'auto' bevorzugt Picamera2 fuer CSI-Kameras und verwendet

                OpenCV fuer USB-/V4L2-Kameras.

        """

        if backend not in ("auto", "picamera2", "opencv"):

            raise ValueError("backend muss 'auto', 'picamera2' oder 'opencv' sein")

        if (colorspace or "bgr").lower() not in ("bgr", "rgb", "gray"):

            raise ValueError("colorspace muss 'bgr', 'rgb' oder 'gray' sein")

        if buffersize < 1 or skip_frame < 0:

            raise ValueError("buffersize muss >= 1 und skip_frame muss >= 0 sein")

        if width is not None and width <= 0:

            raise ValueError("width muss positiv sein")

        if height is not None and height <= 0:

            raise ValueError("height muss positiv sein")

        if devicenumber < 0:

            raise ValueError("devicenumber muss >= 0 sein")

        self.__devicenumber = devicenumber

        self.__flip = flip

        self.__colorspace = (colorspace or "bgr").lower()

        self.__skip_frame = int(skip_frame)

        self.__VideoCapture = None

        self.__backend = None

        self.__is_active = False

        self.__imgsize = (0, 0)

        # Fuer CSI-Kameras auf aktuellen Raspberry-Pi-Systemen wird

        # Picamera2/libcamera statt cv2.VideoCapture verwendet.

        if backend in ("auto", "picamera2"):

            try:

                from picamera2 import Picamera2

            except ImportError as exc:

                if backend == "picamera2":

                    raise RuntimeError(

                        "Picamera2/libcamera kann nicht importiert werden. "

                        "Auf Raspberry Pi OS: sudo apt install python3-picamera2"

                    ) from exc

            else:

                use_picamera2 = backend == "picamera2"

                if backend == "auto":

                    cameras = Picamera2.global_camera_info()

                    if devicenumber < len(cameras):

                        # USB-Kameras weiterhin ueber OpenCV ansprechen.

                        camera_id = str(cameras[devicenumber].get("Id", ""))

                        use_picamera2 = "usb" not in camera_id.lower()

                if use_picamera2:

                    self.__backend = "picamera2"

                    try:

                        self.__VideoCapture = Picamera2(devicenumber)

                        # RGB888 ergibt bei Picamera2 ein BGR-NumPy-Array,

                        # also dieselbe Kanalreihenfolge wie cv2.VideoCapture.

                        main = {"format": "RGB888"}

                        buffer_count = max(2, int(buffersize))

                        config = self.__VideoCapture.create_preview_configuration(

                            main=main, buffer_count=buffer_count, queue=False

                        )

                        if width is not None or height is not None:

                            default_width, default_height = config["main"]["size"]

                            main["size"] = (

                                width if width is not None else default_width,

                                height if height is not None else default_height,

                            )

                            config = self.__VideoCapture.create_preview_configuration(

                                main=main, buffer_count=buffer_count, queue=False

                            )

                        self.__VideoCapture.configure(config)

                        self.__imgsize = tuple(self.__VideoCapture.camera_configuration()["main"]["size"])

                        self.__VideoCapture.start()

                        self.__is_active = True

                    except Exception as exc:

                        self.release()

                        raise RuntimeError(f"Picamera2-Kamera {devicenumber} konnte nicht gestartet werden") from exc

        # OpenCV bleibt fuer USB-Webcams und andere V4L2-Geraete verfuegbar.

        if self.__VideoCapture is None:

            self.__backend = "opencv"

            self.__VideoCapture = cv2.VideoCapture(devicenumber)

            if not self.__VideoCapture.isOpened():

                self.release()

                raise RuntimeError(

                    f"OpenCV-Kamera {devicenumber} konnte nicht geoeffnet werden. "

                    "Fuer eine CSI-Kamera ggf. backend='picamera2' verwenden."

                )

            if width is not None:

                self.__VideoCapture.set(cv2.CAP_PROP_FRAME_WIDTH, width)

            if height is not None:

                self.__VideoCapture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)

            self.__VideoCapture.set(cv2.CAP_PROP_BUFFERSIZE, buffersize)

            self.__imgsize = (

                int(self.__VideoCapture.get(cv2.CAP_PROP_FRAME_WIDTH)),

                int(self.__VideoCapture.get(cv2.CAP_PROP_FRAME_HEIGHT)),

            )

            self.__is_active = True

    def get_size(self) -> tuple:

        """Returns (width, height) of the configured camera image."""

        return self.__imgsize

    def get_frame(self) -> np.ndarray:

        """Returns the current frame as NumPy array with transformations applied.

        Raises:

            RuntimeError: Camera is not accessible or frame capture failed.

        """

        if not self.check():

            raise RuntimeError("Camera is not accessible or was already released")

        try:

            if self.__backend == "picamera2":

                for _ in range(self.__skip_frame):

                    self.__VideoCapture.capture_array("main")

                frame = self.__VideoCapture.capture_array("main")

            else:

                for _ in range(self.__skip_frame):

                    if not self.__VideoCapture.grab():

                        raise RuntimeError("Could not skip video frame")

                success, frame = self.__VideoCapture.read()

                if not success:

                    raise RuntimeError("Could not read video frame")

        except Exception as exc:

            raise RuntimeError("Could not capture video frame") from exc

        if frame is None or frame.size == 0:

            raise RuntimeError("Camera returned an empty frame")

        if self.__flip:

            frame = cv2.flip(frame, -1)  # bewusst beide Achsen / 180 Grad

        if self.__colorspace == "rgb":

            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        elif self.__colorspace == "gray":

            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        return frame

    def check(self) -> bool:

        """Returns True if the camera is initialized and not released."""

        if self.__VideoCapture is None or not self.__is_active:

            return False

        if self.__backend == "opencv":

            return self.__VideoCapture.isOpened()

        return True

    def release(self) -> None:

        """Releases camera so that other processes can access it."""

        camera = self.__VideoCapture

        if camera is None:

            return

        was_active = self.__is_active

        self.__VideoCapture = None

        self.__is_active = False

        if self.__backend == "picamera2":

            try:

                if was_active:

                    camera.stop()

            finally:

                camera.close()

        else:

            camera.release()

    def __enter__(self):

        """Allows use of Camera as context manager."""

        return self

    def __exit__(self, exc_type, exc_value, traceback):

        self.release()


if __name__ == "__main__":

    # Funktioniert auch ohne Desktop/Display (z. B. bei SSH-Zugriff).

    with Camera() as cam:

        img = cam.get_frame()

        print("Image size:", cam.get_size())

        cv2.imwrite("camera_test.jpg", img)

        print("Bild gespeichert: camera_test.jpg")

 
 