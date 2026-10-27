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
        with self._lock:
            if self.is_running(): return
            if self._thread.is_alive(): self._thread.join(timeout=2)  # alten Lauf sauber auslaufen lassen
            self._thread = threading.Thread(target=self._drive, daemon=True, args=[self._update_cfg], name=self._name)
            self._thread.start()

    def _drive(self, update_cfg:bool=False):
        with self._lock:
            if self.is_running():
                raise RuntimeError("Mode is already running")

        try:
            if (update_cfg):
                self._log.warning(f"Reloading config files. This will cost some performance and is not advised in production environments")
            
            with self._lock:
                self._stop_event = threading.Event()

            self._log.info(f"Starte {self._name}")

            if (self._pre_run()):
                self._log.debug("Pre-Run actions absolved")
            else:
                raise RuntimeError("Pre-Run actions failed. Aborting.")

            i = 0
            running = True
            
            period = 1.0 / self._frequency
            while (not self._stop_event.is_set() and self._run_condition() and running):
                start_time = time.monotonic()
                if (update_cfg):
                    self._cfg._load_config_file()
                    self._car._update_config()

                running = self._run()

                elapsed_time = time.monotonic() - start_time

                # self._stop_event.wait(
                #     max(0.0, period - elapsed_time)
                # )

                time.sleep(max(0, period - elapsed_time))
                i+=1
                if (i%self._frequency == 5):
                    self._log.debug(f"Running {self._name} in iteration #{i}")
                
        except Exception as e:
            self._log.exception(e)
        finally:
            self.stop()
            if (self._post_run()):
                self._log.debug("Post-Run actions absolved")
            else:
                raise RuntimeError("Post-Run actions failed. Aborting.")                
            with self._lock:
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
        return self._stop_event is not None

    def stop(self, reason:StopReason|int=0) -> bool:
        if (self._stop_event is not None):
            self._log.info(f"{self._name} beendet")
            self._stop_event.set()
            self._stop_car(reason)
            return True
        return False

    def _stop_car(self, reason:StopReason|int=0):
        """_summary_

        Args:
            reason (StopReason | int, optional): _description_. Defaults to 0.
        """
        self._log.info(f"Car stopped for {reason}")
        self._car.stop()    



