import logging
import threading
import time
from typing import Optional
import cv2

from CameraCar import CameraData, CameraDataProcessed

import numpy as np

import ConfigReader
from CameraCar import CameraCar

from basisklassen_cam import Camera

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
        self._pack_processing_parameters = ((110,179), # hue
                                            (0,255), # saturation
                                            (0,255), # value
                                            (0.1,0.9), # crop
                                            3, # blue
                                            5 # morph kernel
                                            )
        print(self._cfg.get('colormode'))
        super().__init__ (colorspace=str(self._cfg.get_str('colormode','gray')),
                          height=self._cfg.get_int('height',480),
                          width=self._cfg.get_int('width',640))

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

                processed_image = image
                h = processed_image.shape[0]
                cropped = processed_image[int(h*(1-self.processing_parameters[3][1])):int(h*(1-self.processing_parameters[3][0])),:]
                hsv = cv2.cvtColor(cropped, cv2.COLOR_BGR2HSV)
                lower_boundary = np.array([self.processing_parameters[0][0],self.processing_parameters[1][0],self.processing_parameters[2][0]])
                upper_boundary = np.array([self.processing_parameters[0][1],self.processing_parameters[1][1],self.processing_parameters[2][1]])
                mask = cv2.inRange(hsv, lower_boundary, upper_boundary)

                # evtl noch blur zB mask_blur = cv2.blur(image_gray,(4,4))
                blur = max(1, int(self.processing_parameters[4]))
                img_blur = blur_img(mask, blur_value = (blur,blur))

                morph_kernel_parameter = ((self.processing_parameters[5],self.processing_parameters[5]))
                img_morph_kernel = morph_kernel_filter(img_blur, morph_kernel=morph_kernel_parameter)

                # Kantenerkennung
                #img_kanten = kanten_erkennung(img_morph_kernel, minmaxVal = (50,100))

                # cfg -> save_image -> FUNKTION: Checke Ordner fig_raw ja, nein
                #FIXME
                if self.save_image:
                    cv2.imwrite('camera_test_processed.jpg', mask)

                # Bild bearbeitung -> Linienwerte
                # Linienwerte an Auto (CameraCar.py) übergeben
                timestamp = time.time_ns()

                cameradata = CameraData(
                    raw_image=image,
                    timestamp=timestamp,
                    process_image=img_morph_kernel
                )
                self._car.camera_data = cameradata

                time.sleep(1/self.scan_frequency)
        except Exception as e:
            raise e
        finally:
            self._log.debug('Exit Camera loop')

if __name__ == '__main__':

    print('This is the main.')