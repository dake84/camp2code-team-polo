import logging
from typing import Optional

from CameraCar import CameraCar, CameraData
from ConfigReader import ConfigReader

import numpy as np
import cv2
import tensorflow as tf


class DeepCar(CameraCar):

    def __init__(self, config: Optional[ConfigReader] = None, model_path='deepcar.tflite'):

        super().__init__(config=config)

        self._camera_data = CameraData(
            process_image=np.empty(0),
            raw_image=np.empty(0)
        )

        self.__log = logging.getLogger(DeepCar.__name__)

        self._steer_cnn = 90.0
        self._speed_cnn = 0.0

        # TFLite-Modell laden
        self._interpreter = tf.lite.Interpreter(model_path=model_path)
        self._interpreter.allocate_tensors()

        # Informationen über Input und Output
        self._input_details = self._interpreter.get_input_details()
        self._output_details = self._interpreter.get_output_details()

        print(self._input_details)
        print(self._output_details)

    def data_transformation(self, image):

        # Auf gleiche Bildgröße wie beim Training bringen
        image = cv2.resize(image,(320, 240))

        # Datentyp wie beim Training
        image = image.astype(np.float32)

        # Batch-Dimension hinzufügen:
        # (120, 160, 3) -> (1, 120, 160, 3)
        image = np.expand_dims(image, axis=0)

        return image


    def predict(self, image):

        image = self.data_transformation(image)

        # Bild an das Netzwerk übergeben
        self._interpreter.set_tensor(self._input_details[0]['index'],image)

        # CNN ausführen
        self._interpreter.invoke()

        # Ergebnis holen
        prediction = self._interpreter.get_tensor(
            self._output_details[0]['index']
        )

        # Erwartet:
        # prediction = [[speed, steering_angle]]
        self._speed_cnn = float(prediction[0][0])
        self._steer_cnn = float(prediction[0][1])

        return self._speed_cnn, self._steer_cnn