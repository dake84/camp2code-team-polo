import logging
import threading
import time
from typing import Optional
import cv2

from models import CameraData, CameraDataProcessed

import numpy as np

import ConfigReader
from CameraCar import CameraCar

from basisklassen_cam import Camera
from pipeline_processors import LaneProcessor, CopilotLaneProcessor

def blur_img(img, blur_value = (10,10)):
    return cv2.blur(img,blur_value)

def kanten_erkennung(img, minmaxVal = (20,250)):
    return cv2.Canny(img,minmaxVal[0],minmaxVal[1])

def morph_kernel_filter(img, morph_kernel=(5, 5)):

    # Falls ein BGR-Bild übergeben wird, in Graustufen umwandeln
    if img.ndim == 3:
        mask_gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    else:
        mask_gray = img

    mask_bin = (mask_gray > 0).astype(np.uint8) * 255

    kernel = np.ones(morph_kernel, dtype=np.uint8)

    img_morph_kernel = cv2.morphologyEx(
        mask_bin,
        cv2.MORPH_CLOSE,
        kernel
    )

    return img_morph_kernel


class CameraSensor(Camera):

    def __init__(self, car:CameraCar, sensor_config:Optional[ConfigReader.ConfigReader]=None, logger:Optional[logging.Logger]=None) -> None:

        self._log = logger if logger is not None else logging.getLogger(self.__class__.__name__)
        self._cfg = sensor_config if sensor_config is not None else ConfigReader.ConfigReader('cam_sensor', logger=self._log)
        self._lock = threading.Lock()
        self._debug_mode = False
        self._car = car
        # Der Prozessor ist austauschbar; die restliche Architektur bleibt gleich.
        self._processor = CopilotLaneProcessor()
        # Laufende Nummer fuer die Zuordnung von Bild und Fahrentscheidung.
        self._frame_id = 0
        # Gespeicherte Studio-Konfiguration auch ohne offenen Browser nutzen.
        from pipeline_config import load as load_pipeline_config
        settings = load_pipeline_config()
        self._pack_processing_parameters = (
            tuple(settings['hue']), tuple(settings['sat']), tuple(settings['val']),
            tuple(settings['crop']), int(settings['blur']), int(settings['morph']),
            tuple(settings['canny']), dict(settings['enabled']),
        )
        print(self._cfg.get('colormode'))
        super().__init__ (colorspace=str(self._cfg.get_str('colormode','gray')),
                          height=self._cfg.get_int('height',480),
                          width=self._cfg.get_int('width',640))
        
        self.set_camera_controls({
            "AeEnable": True,
            "ExposureValue": 1.0,
        })

    @property
    def scan_frequency(self) -> int:
        with self._lock:
            return self._cfg.get_int('scan_frequency', 25)

    @property
    def save_image(self) -> bool: # Mit Zeitstempel. -- In Dateiname: Zeitstempel; Lenkwinkel, Speed
        # Hier einstellen (Auflösung, Pixel etc.)
        with self._lock:
            return self._cfg.get_bool('save_image', False)

    @property
    def processing_parameters(self):
        with self._lock:
            return self._pack_processing_parameters

    @processing_parameters.setter
    def processing_parameters(self,pack_processing_parameters):
        with self._lock:
            self._pack_processing_parameters = pack_processing_parameters

    def set_processor(self, name: str):
        """Wechselt zwischen Legacy und Diagnose-Prozessor.

        Der Wechsel wird unter dem bestehenden Sensor-Lock vorgenommen.
        """
        if name not in ('legacy', 'copilot'):
            raise ValueError('Unbekannter Prozessor')
        with self._lock:
            self._processor = LaneProcessor() if name == 'legacy' else CopilotLaneProcessor()

    @property
    def processor_name(self) -> str:
        with self._lock:
            return 'copilot' if isinstance(self._processor, CopilotLaneProcessor) else 'legacy'

    def read_loop(self, stop_event:threading.Event):
        try:
            self._log.debug('Entering Camera-Sensor loop')
            while (not stop_event.is_set()):
                if (self._debug_mode):
                    self._cfg._load_config_file()
                #v = self.sensor_values
                self._log.debug(f'Read from camera-sensor')

                image = self.get_frame()
                # cfg -> save_image -> FUNKTION: Checke Ordner fig_raw ja, nein
                #FIXME
                if self.save_image:
                    cv2.imwrite('camera_test.jpg', image)

                # Parameter fuer diesen Frame einmalig lesen.
                parameters = self.processing_parameters
                # Genau eine Verarbeitung je Frame, mit optionalen Diagnosebildern.
                with self._lock:
                    processor = self._processor
                result = processor.process(image, parameters)
                self._frame_id += 1
                cameradata = CameraData(
                    raw_image=image,
                    timestamp=time.time_ns(),
                    frame_id=self._frame_id,
                    monotonic_ns=time.monotonic_ns(),
                    crop=(tuple(parameters[3]) if len(parameters) < 8 or
                          parameters[7].get("roi", True) else (0.0, 1.0)),
                    lanes=result.lanes,
                    process_image=result.processed_image,
                    stages=result.stages,
                )
                self._car.camera_data = cameradata

                time.sleep(1/self.scan_frequency)
        except Exception as e:
            raise e
        finally:
            self._log.debug('Exit Camera loop')

if __name__ == '__main__':

    print('This is the main.')
