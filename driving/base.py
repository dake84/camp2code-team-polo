from __future__ import annotations

# Gemeinsame Bibliotheken aus der bisherigen Driving.py.
# Die Fahrlogik bleibt bei dieser reinen Modulaufteilung unveraendert.
import abc
import curses
import random
import threading
import time
from datetime import datetime
from typing import Optional, Tuple
import logging
import cv2
import numpy as np
from BaseCar import BaseCar
from CameraCar import CameraCar, CameraDataProcessed, CameraData
from CarLogger import Loggable
import ConfigReader
from SensorCar import SensorCar
from SonicCar import SonicCar
from LenkwinkelKalkulator import lenkwinkel_from_image
import select
from evdev import InputDevice, ecodes

from .stop_reason import StopReason

class DrivingMode(abc.ABC):

    def __init__(self, name:str, car:BaseCar, logger:logging.Logger, config:ConfigReader.ConfigReader, update_cfg:bool=False, frequency:int=500) -> None:
        super().__init__()
        self._stop_event = None
        self._name = name
        self._car = car
        self._log = logger
        self._cfg = config
        self._frequency = self._cfg.get_int("scan_frequency", frequency)
        self._lock = threading.RLock()
        self._update_cfg = update_cfg
        self._thread = threading.Thread(target=self._drive, daemon=True, args=[update_cfg])

    def start(self):
        """Startet höchstens einen Worker und setzt dessen Stop-Ereignis vorher."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event = threading.Event()
            self._thread = threading.Thread(target=self._drive, daemon=True,
                                            args=[self._update_cfg], name=self._name)
            self._thread.start()

    def _drive(self, update_cfg: bool = False):
        event = self._stop_event
        try:
            if update_cfg:
                self._log.warning("Konfiguration wird während der Fahrt neu geladen")
            if not self._pre_run():
                self._log.error("Vorbereitung des Fahrmodus fehlgeschlagen")
                return
            period = 1.0 / max(1, self._frequency)
            while not event.is_set() and self._run_condition():
                started = time.monotonic()
                if update_cfg:
                    self._cfg._load_config_file()
                    self._car._update_config()
                if not self._run():
                    break
                event.wait(max(0.0, period - (time.monotonic() - started)))
        except Exception:
            self._log.exception("Fehler im Fahrmodus %s", self._name)
        finally:
            event.set()
            try:
                self._post_run()
            except Exception:
                self._log.exception("Fehler beim Beenden des Fahrmodus")
            with self._lock:
                if self._stop_event is event:
                    self._stop_event = None

    @abc.abstractmethod
    def _run_condition(self) -> bool:
        pass

    def _pre_run(self) -> bool:
        self._car.drive(0,90)
        return True

    @abc.abstractmethod
    def _run(self) -> bool:
        pass

    def _post_run(self) -> bool:
        self._car.drive(0,90)
        return True

    def is_running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive() and self._stop_event is not None and not self._stop_event.is_set()

    def stop(self, reason: StopReason | int = 0) -> bool:
        with self._lock:
            event = self._stop_event
            thread = self._thread
            if event is None:
                return False
            event.set()
        self._stop_car(reason)
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)
        return True

    def _stop_car(self, reason:StopReason|int=0):
        """_summary_

        Args:
            reason (StopReason | int, optional): _description_. Defaults to 0.
        """
        self._log.info(f"Car stopped for {reason}")
        self._car.stop()    



