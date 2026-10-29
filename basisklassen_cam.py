#!/usr/bin/env python3
# Information: Module mit Basisklasse fuer Camera fuer Projekt RPiCar
# File name: basisklassen_cam.py
# Author (Original): Robert Heise (FIDA)
# Date created: 11/10/2021
# Date last modified: 29/10/2026 (Picamera2 / Debian 13, Dual-Stream: PAKET-H3)
# Usage: U4I/FIDA "Autonomes Fahren" Sunfounder PiCar-S
#
# PAKET-H3: Optionaler zweiter Stream (Dual-Stream).
#   * "main"  = grosses Bild (RGB888) fuer Aufnahme und Anzeige,
#   * "lores" = kleines Bild (YUV420) fuer die Bildverarbeitung,
#   beide aus DERSELBEN Aufnahme (capture_arrays). Ohne die neuen Argumente (small_size, small_mode, frame_rate)
#   verhalten sich Konstruktor und alle bisherigen Methoden exakt wie vorher.
import logging
import math

import cv2
import numpy as np

_log = logging.getLogger("Camera")

# Kamera-Metadaten (Picamera2/libcamera) -> normalisierte Namen
_METADATA_KEYS = (
    ("exposure_us", "ExposureTime"),          # Belichtungszeit in Mikrosekunden
    ("gain", "AnalogueGain"),                 # analoge Verstaerkung
    ("digital_gain", "DigitalGain"),
    ("frame_duration_us", "FrameDuration"),   # Bilddauer in Mikrosekunden (1e6 / Dauer = Kamera-fps)
    ("lux", "Lux"),                           # geschaetzte Beleuchtungsstaerke
    ("sensor_timestamp_ns", "SensorTimestamp"),
)


def normalize_metadata(metadata):
    """Metadaten einer Aufnahme als dict mit festen Schluesseln; fehlende oder unbrauchbare Werte sind ``None``."""
    result = dict.fromkeys(name for name, _ in _METADATA_KEYS)
    if isinstance(metadata, dict):
        for name, source in _METADATA_KEYS:
            value = metadata.get(source)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                result[name] = value
    return result


def yuv420_to_bgr(yuv, width, height):
    """Wandelt ein YUV420-Array (I420) der Groesse ``width`` x ``height`` nach BGR um.

    Picamera2 liefert YUV420 als Array der Form ``(hoehe * 3 // 2, stride)``; der Zeilen-Stride kann groesser
    als die Breite sein (Zeilen sind aufgefuellt). Es wird mit voller Stride-Breite umgewandelt und danach auf
    ``width`` zugeschnitten. Ungueltige Formen ergeben ``ValueError``.
    """
    if yuv is None or getattr(yuv, "ndim", 0) != 2:
        raise ValueError("YUV420-Array erwartet (2 Dimensionen)")
    rows, stride = yuv.shape
    if height % 2 or rows != height * 3 // 2:
        raise ValueError(f"YUV420-Zeilen {rows} passen nicht zur Hoehe {height}")
    if stride < width:
        raise ValueError(f"YUV420-Stride {stride} kleiner als die Breite {width}")
    bgr = cv2.cvtColor(yuv, cv2.COLOR_YUV2BGR_I420)
    return np.ascontiguousarray(bgr[:height, :width])


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
        small_size: tuple = None,
        small_mode: str = "auto",
        frame_rate: float = None,
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
            small_size (tuple): PAKET-H3: (Breite, Hoehe) des kleinen Bildes fuer die Bildverarbeitung.
                None = kein kleines Bild (Verhalten wie bisher). Groesser oder gleich gross wie das Hauptbild
                wird ignoriert (kein Hochskalieren).
            small_mode (str): PAKET-H3: 'auto' oder 'camera' = zweiter Stream der Kamera (lores, YUV420), wenn
                die Kamera ihn akzeptiert; sonst (Konfiguration abgelehnt, OpenCV-/USB-Backend) Software-
                Verkleinern mit Hinweis im Log. 'software' = immer Software-Verkleinern.
            frame_rate (float): PAKET-H3: feste Bildrate der KAMERA in fps (FrameDurationLimits, nur Picamera2).
                None oder 0 = Automatik. Nicht zu verwechseln mit dem Schleifentakt des CameraSensor
                (``cam_sensor.scan_frequency``).
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
        if small_mode not in ("auto", "camera", "software"):
            raise ValueError("small_mode muss 'auto', 'camera' oder 'software' sein")
        self.__devicenumber = devicenumber
        self.__flip = flip
        self.__colorspace = (colorspace or "bgr").lower()
        self.__skip_frame = int(skip_frame)
        self.__VideoCapture = None
        self.__backend = None
        self.__is_active = False
        self.__imgsize = (0, 0)
        # PAKET-H3: kleines Bild, Metadaten, Bildrate
        self.__small_requested = self.__valid_size(small_size)
        self.__small_mode = small_mode
        self.__small_size = None          # tatsaechliche Groesse des kleinen Bildes (None = keins)
        self.__small_source = None        # 'camera' | 'software' | None
        self.__lores_active = False
        self.__lores_stride = None
        self.__dual_note = None           # Grund, falls der Dual-Stream nicht genutzt wird
        self.__frame_rate = None          # eingestellte Kamera-fps (None = Automatik)
        self.__last_meta = normalize_metadata(None)
        self.__lores_warned = False
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
                        # PAKET-H3: zweiter Stream (lores) nur, wenn ein kleineres Bild angefordert ist.
                        small = self.__smaller_than(self.__small_requested, config["main"]["size"])
                        configured = False
                        if small is not None and self.__small_mode != "software":
                            configured = self.__configure_dual(main, small, buffer_count)
                        if not configured:
                            self.__VideoCapture.configure(config)
                        self.__imgsize = tuple(self.__VideoCapture.camera_configuration()["main"]["size"])
                        self.__settle_small(small, configured)
                        self.__VideoCapture.start()
                        self.__is_active = True
                        if frame_rate:
                            self.set_frame_rate(frame_rate)
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
            # PAKET-H3: das OpenCV-/USB-Backend hat keinen zweiten Stream -> Software-Verkleinern.
            small = self.__smaller_than(self.__small_requested, self.__imgsize)
            if small is not None and self.__small_mode != "software":
                self.__dual_note = "OpenCV-/USB-Backend hat keinen zweiten Stream"
                _log.warning("Dual-Stream nicht verfuegbar (%s) - Software-Verkleinern auf %sx%s",
                             self.__dual_note, small[0], small[1])
            self.__settle_small(small, False)

    # ------------------------------------------------------------------ PAKET-H3: Hilfen (privat)
    @staticmethod
    def __valid_size(size):
        try:
            width, height = size
            width, height = int(width), int(height)
        except (TypeError, ValueError):
            return None
        return (width, height) if width > 0 and height > 0 else None

    @staticmethod
    def __smaller_than(small, main):
        """``small``, wenn es in beiden Richtungen nicht groesser und nicht identisch mit ``main`` ist, sonst None."""
        if small is None:
            return None
        main_width, main_height = int(main[0]), int(main[1])
        if small[0] > main_width or small[1] > main_height:
            _log.warning("Kleines Bild %sx%s groesser als das Hauptbild %sx%s - wird ignoriert",
                         small[0], small[1], main_width, main_height)
            return None
        if tuple(small) == (main_width, main_height):
            return None
        return tuple(small)

    def __configure_dual(self, main, small, buffer_count):
        """Konfiguriert main + lores (YUV420). ``False`` bei Ablehnung (Software-Verkleinern, Hinweis im Log)."""
        camera = self.__VideoCapture
        try:
            config = camera.create_preview_configuration(
                main=main, lores={"format": "YUV420", "size": small}, buffer_count=buffer_count, queue=False
            )
            camera.configure(config)
            if not (camera.camera_configuration() or {}).get("lores"):
                raise RuntimeError("Kamera meldet keinen lores-Stream")
            return True
        except Exception as exc:  # Konfiguration abgelehnt: kein Dual-Stream, Start scheitert nicht
            self.__dual_note = f"Dual-Stream abgelehnt: {exc}"
            _log.warning("Dual-Stream nicht verfuegbar (%s) - Software-Verkleinern auf %sx%s", exc, small[0], small[1])
            return False

    def __settle_small(self, small, dual):
        """Legt Groesse und Herkunft des kleinen Bildes fest (nach dem Konfigurieren, tatsaechliche Groesse)."""
        if small is None:
            self.__small_size = self.__small_source = None
            self.__lores_active = False
            return
        if dual:
            lores = self.__VideoCapture.camera_configuration()["lores"]
            self.__small_size = tuple(lores["size"])
            self.__lores_stride = lores.get("stride")
            self.__small_source = "camera"
            self.__lores_active = True
            if self.__small_size != tuple(small):
                _log.info("Kamera hat das kleine Bild von %sx%s auf %sx%s angepasst", small[0], small[1],
                          self.__small_size[0], self.__small_size[1])
            return
        self.__small_size = tuple(small)
        self.__small_source = "software"
        self.__lores_active = False
        if self.__small_mode != "software":
            _log.info("Kleines Bild %sx%s per Software verkleinert (%s)", small[0], small[1],
                      self.__dual_note or "kein Dual-Stream")

    def __finish(self, frame):
        """Drehen und Farbraum wie bisher (gilt fuer das grosse und das kleine Bild gleichermassen)."""
        if self.__flip:
            frame = cv2.flip(frame, -1)  # bewusst beide Achsen / 180 Grad
        if self.__colorspace == "rgb":
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        elif self.__colorspace == "gray":
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        return frame

    # ------------------------------------------------------------------ bisherige Methoden
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
        return self.__finish(frame)

    # ------------------------------------------------------------------ PAKET-H3: kleines Bild, Metadaten
    def get_frames(self) -> tuple:
        """PAKET-H3: ``(grosses Bild, kleines Bild)`` aus DERSELBEN Aufnahme.

        Das kleine Bild ist ``None``, wenn kein kleineres Bild angefordert wurde. Beide Bilder sind wie bei
        ``get_frame`` gedreht und im gewaehlten Farbraum. Mit Dual-Stream kommt das kleine Bild aus dem zweiten
        Stream der Kamera (YUV420 -> BGR, Zeilen-Stride beachtet), sonst per Software (INTER_AREA) aus dem
        grossen Bild. Die Metadaten dieser Aufnahme liefert danach ``get_metadata()``.

        Raises:
            RuntimeError: Camera is not accessible or frame capture failed.
        """
        if not self.check():
            raise RuntimeError("Camera is not accessible or was already released")
        yuv = None
        metadata = None
        try:
            if self.__backend == "picamera2":
                for _ in range(self.__skip_frame):
                    self.__VideoCapture.capture_array("main")
                names = ["main", "lores"] if self.__lores_active else ["main"]
                arrays, metadata = self.__VideoCapture.capture_arrays(names)
                frame = arrays[0]
                if self.__lores_active:
                    yuv = arrays[1]
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
        self.__last_meta = normalize_metadata(metadata)
        small = None
        if yuv is not None:
            try:
                small = self.__finish(yuv420_to_bgr(yuv, *self.__small_size))
            except (ValueError, cv2.error) as exc:
                if not self.__lores_warned:   # einmalig; danach Software-Verkleinern fuer diesen Frame
                    self.__lores_warned = True
                    _log.warning("lores-Bild nicht umwandelbar (%s) - Software-Verkleinern", exc)
        frame = self.__finish(frame)
        if self.__small_size is not None and small is None:
            small = cv2.resize(frame, self.__small_size, interpolation=cv2.INTER_AREA)
        return frame, small

    def get_metadata(self) -> dict:
        """PAKET-H3: Metadaten der letzten mit ``get_frames`` gelesenen Aufnahme (Kopie).

        Schluessel: exposure_us (Belichtungszeit), gain (analoge Verstaerkung), digital_gain,
        frame_duration_us (Bilddauer), lux, sensor_timestamp_ns. Nicht verfuegbare Werte sind ``None``.
        """
        return dict(self.__last_meta)

    def get_small_size(self):
        """PAKET-H3: tatsaechliche Groesse ``(breite, hoehe)`` des kleinen Bildes oder ``None``."""
        return self.__small_size

    @property
    def small_source(self):
        """PAKET-H3: Herkunft des kleinen Bildes: ``'camera'`` (zweiter Stream), ``'software'`` oder ``None``."""
        return self.__small_source

    def get_sizes(self) -> dict:
        """PAKET-H3: Groessen beider Bilder: ``{'main': (b, h), 'small': (b, h) | None, 'small_source': ...}``."""
        return {"main": self.__imgsize, "small": self.__small_size, "small_source": self.__small_source}

    def get_stream_info(self) -> dict:
        """PAKET-H3: Zustand der Streams fuer Diagnose (``scripts/cam_check.py``) und Log."""
        return {"backend": self.__backend, "main_size": self.__imgsize, "small_requested": self.__small_requested,
                "small_mode": self.__small_mode, "small_size": self.__small_size,
                "small_source": self.__small_source, "lores_stride": self.__lores_stride,
                "dual_note": self.__dual_note, "frame_rate_set": self.__frame_rate}

    def sensor_modes(self) -> list:
        """PAKET-H3: Sensor-Modi, wie sie Picamera2 meldet (nichts ist festgeschrieben); leer ohne Picamera2."""
        if self.__backend != "picamera2" or self.__VideoCapture is None:
            return []
        modes = []
        try:
            for mode in self.__VideoCapture.sensor_modes:
                modes.append({"size": tuple(mode.get("size", ())), "fps": mode.get("fps"),
                              "bit_depth": mode.get("bit_depth"), "format": str(mode.get("format")),
                              "crop_limits": tuple(mode.get("crop_limits", ()))})
        except Exception as exc:
            _log.warning("Sensor-Modi nicht lesbar: %s", exc)
        return modes

    def set_frame_rate(self, fps) -> bool:
        """PAKET-H3: feste Bildrate der KAMERA (FrameDurationLimits); ``None``/0 = Automatik.

        Gemeint ist die Rate, mit der der Sensor Bilder liefert - nicht der Schleifentakt des CameraSensor
        (``scan_frequency``). Die Kamera kann nicht schneller, als Belichtung und Sensor-Modus erlauben.
        Gibt ``True`` zurueck, wenn die Steuerung gesetzt wurde (nur Picamera2).
        """
        if self.__backend != "picamera2" or self.__VideoCapture is None:
            _log.info("Bildrate: Backend %s unterstuetzt FrameDurationLimits nicht", self.__backend)
            return False
        try:
            if fps:
                fps = float(fps)
                if not math.isfinite(fps) or fps <= 0:
                    raise ValueError(f"ungueltige Bildrate {fps!r}")
                duration = int(round(1_000_000 / fps))
                limits = (duration, duration)
            else:
                # Automatik: den vollen Bereich freigeben, den die Kamera meldet.
                control = (getattr(self.__VideoCapture, "camera_controls", {}) or {}).get("FrameDurationLimits")
                if not control:
                    self.__frame_rate = None
                    return False
                limits = (int(control[0]), int(control[1]))
            self.__VideoCapture.set_controls({"FrameDurationLimits": limits})
            self.__frame_rate = float(fps) if fps else None
            return True
        except Exception as exc:
            _log.warning("Bildrate nicht gesetzt: %s", exc)
            return False

    def get_frame_rate_setting(self):
        """PAKET-H3: eingestellte Kamera-Bildrate in fps; ``None`` = Automatik."""
        return self.__frame_rate

    def set_exposure_value(self, value) -> bool:
        """PAKET-H3: Belichtungskorrektur (ExposureValue); ``False``, wenn das Backend sie nicht kennt."""
        try:
            self.set_camera_controls({"ExposureValue": float(value)})
            return True
        except Exception as exc:
            _log.warning("Belichtungskorrektur nicht gesetzt: %s", exc)
            return False

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

    def set_camera_controls(self, controls: dict) -> None:
        """Setzt Kameraeinstellungen über das aktive Kamerabackend."""
        self.__VideoCapture.set_controls(controls)


if __name__ == "__main__":
    # Funktioniert auch ohne Desktop/Display (z. B. bei SSH-Zugriff).
    with Camera() as cam:
        img = cam.get_frame()
        print("Image size:", cam.get_size())
        cv2.imwrite("camera_test.jpg", img)
        print("Bild gespeichert: camera_test.jpg")
