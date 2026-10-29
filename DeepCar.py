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

        # Erwartete Shape dynamisch auslesen [Batch, Height, Width, Channels]
        input_shape = self._input_details[0]['shape']
        self._input_height = int(input_shape[1])
        self._input_width = int(input_shape[2])

        self.__log.info(
            f'TFLite geladen. Erwartete Eingabe: {self._input_width}x{self._input_height} (BxH)'
        )
        print(f'Modell-Input-Shape: {input_shape} -> Erwarte BxH: {self._input_width}x{self._input_height}')

    def data_transformation(self, image):

        # 1. Farbraum von BGR (OpenCV) auf RGB (Keras-Training) konvertieren
        if len(image.shape) == 3 and image.shape[2] == 3:
            image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

        # 2. Auf exakte Modellgröße bringen (Breite, Höhe beachten!)
        if (image.shape[1], image.shape[0]) != (self._input_width, self._input_height):
            image = cv2.resize(image, (self._input_width, self._input_height))

        # 3. Datentyp float32 (Bereich 0..255 bleibt erhalten, Rescaling übernimmt das Modell)
        image = image.astype(np.float32)

        # 4. Batch-Dimension hinzufügen: (H, W, 3) -> (1, H, W, 3)
        image = np.expand_dims(image, axis=0)

        return image

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