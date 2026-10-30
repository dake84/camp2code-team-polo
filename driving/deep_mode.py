from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Optional

import ConfigReader
from LenkwinkelKalkulator import steering_angle_from_lanes
from models import DrivingState
from speed_policy import SpeedPolicy  # PAKET-G

import numpy as np
import cv2

from .base import DrivingMode
from mode_registry import register
import tensorflow as tf

if TYPE_CHECKING:
    from CameraCar import CameraCar

@register('cnn', 'CNN (autonom, tflite)', order=10, kind='auto', short='CNN')

class DeepCarMode(DrivingMode):

    def __init__(self,
                 name='DeepCarModus',
                 car: Optional[CameraCar] = None,
                 cfg: Optional[ConfigReader.ConfigReader] = None, model_path='deepcar.tflite'):

        car = car if car is not None else CameraCar()
        self._cameracar = car
        logger = logging.getLogger(self.__class__.__name__)
    

        cfg = cfg if cfg is not None else ConfigReader.ConfigReader('deep_controller')

        self._steer_cnn = 90.0
        self._speed_cnn = 0.0

        # TFLite-Modell laden
        self._interpreter = tf.lite.Interpreter(model_path=model_path)
        self._interpreter.allocate_tensors()

        # Informationen über Input und Output
        self._input_details = self._interpreter.get_input_details()
        self._output_details = self._interpreter.get_output_details()

        # Erwartete Shape dynamisch auslesen [Batch, Height, Width, Channels]
        input_shape = self._input_details[0]['shape']
        self._input_height = int(input_shape[1])
        self._input_width = int(input_shape[2])

        logger.info(
            f'TFLite geladen. Erwartete Eingabe: {self._input_width}x{self._input_height} (BxH)'
        )
        print(f'Modell-Input-Shape: {input_shape} -> Erwarte BxH: {self._input_width}x{self._input_height}')


        super().__init__(name, car, logger, cfg)

    def _pre_run(self) -> bool:
        self._car.drive(0, 90)
        return True

    def _run(self) -> bool:

        try:
            # Geschwindigkeit und Lenkwinkel vom Netzwerk
            speed, steering_angle_raw = self.predict(
                self._cameracar.camera_data.raw_image
            )

            if not np.isfinite([speed, steering_angle_raw]).all():
                raise ValueError('Ungültige Netzwerkvorhersage.')

            steering_angle = int(max(45, min(135, steering_angle_raw)))
            speed = 30 # int(max(0, min(30, speed)))

            print(f'Speed: {speed:.0f} \nLenkwinkel: {steering_angle:.0f}\n----------')

            self._cameracar.drive(speed, steering_angle)

        except Exception:
            self._car.drive(0, 90)
            self._log.exception('Fehler in DeepCarMode._run()')
            return False

        return True
    
    def data_transformation(self, image):
            # 1. Kein Farbraumwechsel mehr nötig! Kamera ist BGR, Modell wurde mit BGR trainiert.
            
            # 2. Falls das Kamerabild nicht exakt 320x240 ist:
            if (image.shape[1], image.shape[0]) != (self._input_width, self._input_height):
                image = cv2.resize(image, (self._input_width, self._input_height))

            # 3. Typ float32 (Rescaling und Cropping macht das TFLite-Modell selbst)
            image = image.astype(np.float32)

            # 4. Batch-Dimension: (H, W, 3) -> (1, H, W, 3)
            return np.expand_dims(image, axis=0)

    def predict(self, image):

        image = self.data_transformation(image)

        # Bild an das Netzwerk übergeben
        self._interpreter.set_tensor(self._input_details[0]['index'], image)

        # CNN ausführen
        self._interpreter.invoke()

        # Ergebnis holen
        prediction = self._interpreter.get_tensor(
            self._output_details[0]['index']
        )

        # prediction = [[speed, steering_angle]]
        self._speed_cnn = float(prediction[0][0])
        self._steer_cnn = float(prediction[0][1])

        return self._speed_cnn, self._steer_cnn    

    def _run_condition(self) -> bool:
        return True

 