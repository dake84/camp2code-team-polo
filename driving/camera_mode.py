"""Kamerabasierter Fahrmodus (Standardmodus des CameraCar).

Datenvertrag:
    * Eingang ist ausschliesslich ``CameraCar.camera_data`` (eine Bildverarbeitung
      je Frame im ``CameraSensor``). Der Regler erkennt keine Linien selbst.
    * Jede Fahrentscheidung wird als ``DrivingState`` mit derselben ``frame_id``
      veroeffentlicht und im Ringbuffer dem Kameraframe zugeordnet.

Aenderungen gegenueber V5.5 (siehe CHANGELOG):
    * Ein Kameraframe wird genau einmal ausgewertet. Vorher wurde derselbe Frame
      bei 500 Hz Abtastrate bis zu ~30-mal neu berechnet und an die Hardware
      gesendet.
    * Die Schutzfunktion ``LANE_TIMEOUT`` bleibt unveraendert (gleiche Uhr, gleiche
      Schwelle) und wird auch ohne neuen Frame geprueft.
    * Neu: ``CAMERA_STALE`` stoppt, wenn laenger als ``lost_line_timeout`` kein
      neuer Kameraframe eintrifft (vorher fuhr das Fahrzeug mit dem letzten
      Winkel weiter, falls der Kamerathread ausfiel).
    * Neu: Ohne gueltigen Kameraframe (``frame_id < 0``, Platzhalter beim Start)
      wird nicht gefahren. Vorher fuhr das Fahrzeug mit 90 Grad und Speed 30 los,
      obwohl noch kein Bild vorlag. Nach ``lost_line_timeout`` -> ``NO_CAMERA_DATA``.
    * ``CameraDataProcessed`` wird nicht mehr erzeugt (keine Leser vorhanden).
    * Unveraendert: Winkelberechnung, ``steering_gain``, Speed-Steuerwert 30.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Optional

import ConfigReader
from LenkwinkelKalkulator import steering_angle_from_lanes
from models import DrivingState
from speed_policy import SpeedPolicy  # PAKET-G

from .base import DrivingMode
from mode_registry import register

if TYPE_CHECKING:
    from CameraCar import CameraCar

@register('camera', 'CameraMode (autonom)', order=10, kind='auto', short='CameraMode')
class CameraMode(DrivingMode):
    # Speed-Steuerwert des CameraMode. Wurde bewusst von 20 auf 30 erhoeht,
    # weil das Fahrzeug bei 20 nicht anfaehrt. Nicht ohne Absprache aendern;
    # tests/test_camera_mode.py sichert den Wert ab.
    SPEED_CONTROL_VALUE = 30
    # PAKET-E: Regler-Abtastung, falls cameracar_controller.scan_frequency fehlt (vorher 500 Hz)
    DEFAULT_POLL_HZ = 100

    def __init__(self, name='CameraModus', car: Optional[CameraCar] = None,
                 cfg: Optional[ConfigReader.ConfigReader] = None):
        if car is None:
            # Lazy-Import: Hardware nur bei Bedarf initialisieren.
            from CameraCar import CameraCar
            car = CameraCar()
        # PAKET-F: austauschbare monotone Uhr (ns). Live = time.monotonic_ns; die Simulation
        # setzt die aufgezeichneten Zeitstempel ein, damit LANE_TIMEOUT wie damals wirkt.
        self._clock = time.monotonic_ns
        self._cameracar = car
        cfg = cfg if cfg is not None else ConfigReader.ConfigReader("cameracar_controller")
        self._last_steering_angle = 90
        self._lost_since = None
        self._last_frame_id = None
        self._started_ns = self._clock()
        self.last_stop_reason = None
        self.last_diagnostic = None
        self._lost_timeout = cfg.get_float('lost_line_timeout', 1)
        logger = logging.getLogger(self.__class__.__name__)
        super().__init__(name, car, logger, cfg)
        if cfg.get_int('scan_frequency', 0) <= 0:  # PAKET-E
            self._frequency = self.DEFAULT_POLL_HZ
        # Driving-Mode-Kennung fuer DrivingState/Recorder.
        self.mode_key = 'camera'
        # PAKET-G: Geschwindigkeitsstrategie (Standard: fest 30 = bisheriges Verhalten).
        # CarApplication.set_speed_policy() ersetzt das Objekt im Stillstand.
        self.speed_policy = SpeedPolicy(fixed_speed=self.SPEED_CONTROL_VALUE)

    @property
    def poll_frequency(self) -> int:
        """Abtastrate der Reglerschleife in Hz (nicht die Entscheidungsrate)."""
        return self._frequency

    def _pre_run(self) -> bool:
        self._last_steering_angle = 90
        self._lost_since = None
        self._last_frame_id = None
        self.last_stop_reason = None
        self.last_diagnostic = None
        self._started_ns = self._clock()
        self._car.drive(0, 90)
        return True

    def _check_without_new_frame(self, camera, now) -> bool:
        """Prueft die Schutzfunktionen, wenn kein neuer Kameraframe vorliegt."""
        timeout_ns = self._lost_timeout * 1e9
        if self._lost_since is not None and now - self._lost_since > timeout_ns:
            self._stop_with('LANE_TIMEOUT', camera.frame_id)
            return False
        published = getattr(camera, 'monotonic_ns', 0) or 0
        if published and now - published > timeout_ns:
            self._stop_with('CAMERA_STALE', camera.frame_id)
            return False
        return True

    def _stop_with(self, reason, frame_id):
        self.last_stop_reason = reason
        if self.last_diagnostic is not None:
            self.last_diagnostic['stop_reason'] = reason
        self._log.warning('CameraMode stoppt: %s, Frame %s, letzte Lenkung %s',
                          reason, frame_id, self._last_steering_angle)

    def _run(self) -> bool:
        camera_data_transfer = self._cameracar.camera_data
        if camera_data_transfer is None:
            self.last_stop_reason = 'NO_CAMERA_DATA'
            self._log.error('CameraMode beendet: kein Kameradatenpaket')
            return False
        try:
            now = self._clock()
            if camera_data_transfer.frame_id < 0:
                # Platzhalter ohne Kamerabild: nicht fahren, nur Wartezeit pruefen.
                if now - self._started_ns > self._lost_timeout * 1e9:
                    self._stop_with('NO_CAMERA_DATA', camera_data_transfer.frame_id)
                    return False
                return True
            if camera_data_transfer.frame_id == self._last_frame_id:
                # Kein neuer Frame: keine neue Entscheidung, keine Hardwarebefehle.
                return self._check_without_new_frame(camera_data_transfer, now)
            self._last_frame_id = camera_data_transfer.frame_id

            # Der LaneDetector hat die Linien bereits fuer diesen Frame ermittelt.
            # Hier wird nur noch der Lenkwinkel aus den Linien berechnet.
            lanes = list(camera_data_transfer.lanes)
            process_image = camera_data_transfer.process_image
            if process_image is None or process_image.size == 0:
                # Verhalten wie bisher: ohne Bild liefert der Kalkulator 90 Grad.
                steering_angle_raw = 90.0
            else:
                height, width = process_image.shape[:2]
                steering_angle_raw = steering_angle_from_lanes(lanes, width, height)

            # Diagnose wird unabhaengig von der Fahrentscheidung veroeffentlicht.
            diagnostic = {
                'frame_id': camera_data_transfer.frame_id,
                'lanes': tuple(lanes),
                'raw_angle': steering_angle_raw,
                'held_previous_angle': steering_angle_raw is None,
                'previous_angle': self._last_steering_angle,
                'stop_reason': None,
                'decided_monotonic_ns': now,
            }
            self.last_diagnostic = diagnostic
            if steering_angle_raw is None:
                if self._lost_since is None:
                    self._lost_since = now
                if now - self._lost_since > self._lost_timeout * 1e9:
                    self._stop_with('LANE_TIMEOUT', camera_data_transfer.frame_id)
                    self._log.info(f'Seit {self._lost_timeout}s keine zwei Linien - {self._name} wird beendet')
                    return False
                steering_angle_raw = self._last_steering_angle
            else:
                self._lost_since = None
                self._last_steering_angle = steering_angle_raw

            driving_parameter = self.driving_parameter(steering_angle_raw)
            steering_gain = self._cfg.get_float('steering_gain', 2.0)
            steering_angle = int(max(45, min(135, 90 + (driving_parameter[0] - 90) * steering_gain)))
            speed = self.speed_policy.speed_for(  # PAKET-G: fest oder adaptiv
                steering_angle, diagnostic['held_previous_angle'])
            self._cameracar.drive(speed, steering_angle)
            actual_angle = self._car.steering_angle
            actual_speed = self._car.speed
            diagnostic.update(requested_angle=steering_angle, actual_angle=actual_angle,
                              speed_control=actual_speed)

            # Die Fahrentscheidung referenziert genau das verarbeitete Bild.
            self._cameracar.driving_state = DrivingState(
                frame_id=camera_data_transfer.frame_id,
                timestamp_ns=time.time_ns(),
                steering_angle=steering_angle_raw,
                speed=actual_speed,
                lanes=lanes,
                steering_angle_car=actual_angle,
                driving_mode=self.mode_key,
                requested_angle=steering_angle,
                monotonic_ns=self._clock(),
            )
        except Exception:
            self.last_stop_reason = 'CONTROLLER_EXCEPTION'
            self._log.exception('Fehler in CameraMode._run()')
            return False
        return True

    def _run_condition(self) -> bool:
        return True

    # def: Linienwerte bekommen, aus den Linien Lenkwinkel bearbeiten
    def driving_parameter(self, steering_angle_raw: int) -> tuple[int, int]:
        steering_angle = steering_angle_raw  # Replace with actual calculation
        speed = self.SPEED_CONTROL_VALUE     # bewusst 30, siehe Klassenkonstante
        return steering_angle, speed