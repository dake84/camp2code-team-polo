import logging
import threading
import time
from typing import Optional
import cv2

from CameraCar import CameraData

import numpy as np

import ConfigReader
from CameraCar import CameraCar

from basisklassen_cam import Camera

def blur_img(img, blur_value = (10,10)):
    return cv2.blur(img,blur_value)

def kanten_erkennung(img, minmaxVal = (20,250)):
    return cv2.Canny(img,minmaxVal[0],minmaxVal[1])

def hough_transformation(img, rho = 1, angle = np.pi/180, min_threshold = 70):
    # rho = 1  # distance precision in pixel, i.e. 1 pixel
    # angle = np.pi / 180  # angular precision in radian, i.e. 1 degree
    # min_threshold = 10  # minimal of votes, Je geringer Min_threshold, dest mehr Geraden werden erkannt.
    #img_hough_trans = cv2.HoughLinesP(img, rho, angle, min_threshold, np.array([]), minLineLength=minLineLength, maxLineGap=maxLineGap)
    img_hough_trans = cv2.HoughLines(img, rho, angle, min_threshold)

    # if img_hough_trans is None:
    #     print('Keine Linien gefunden')
    # else:
    #     print(img_hough_trans.shape)

    return img_hough_trans

def put_lines_into_image(img, hough_parameter):

    img_with_lines = img.copy()
    img_with_lines = cv2.cvtColor(img_with_lines, cv2.COLOR_GRAY2RGB)

    data = []
    if hough_parameter is not None and len(hough_parameter) > 0:
        for line in hough_parameter:
            rho, theta = line[0]

            a = np.cos(theta)
            b = np.sin(theta)

            x0 = a * rho
            y0 = b * rho

            x1 = int(x0 + 1000 * (-b))
            y1 = int(y0 + 1000 * ( a))

            x2 = int(x0 - 1000 * (-b))
            y2 = int(y0 - 1000 * ( a))

            cv2.line(img_with_lines, (x1, y1), (x2, y2), (255, 255, 255), 2)

            data.append((x1, y1, x2, y2))

    return img_with_lines, data

def put_steering_lines_into_image(img, lanes = None):

    img_with_lines = img.copy()
    if img_with_lines.ndim == 2:
        img_with_lines = cv2.cvtColor(img_with_lines, cv2.COLOR_GRAY2RGB)

    w = img_with_lines.shape[1]
    if lanes is not None and len(lanes) > 0:
        for lane in lanes:
            if lane is None:
                continue
            m, b = lane

            x1 = 0
            x2 = w - 1
            y1 = int(np.clip(m * x1 + b, -1e6, 1e6))
            y2 = int(np.clip(m * x2 + b, -1e6, 1e6))

            cv2.line(img_with_lines, (x1, y1), (x2, y2), (0, 255, 0), 2)

    return img_with_lines

def detect_lanes_from_areas(mask, n_lanes=2, min_area=150, m_max=10, fit_frac=1.0, morph_kernel=(5, 5)):
    '''Fasst zusammenhaengende Pixel der Maske zu Flaechen zusammen, nimmt die n_lanes groessten
    und fittet durch deren Pixel je eine Gerade y = m*x + b. Rueckgabe: lanes (links -> rechts), sel_mask'''
    if mask is None or mask.size == 0:
        return [], None

    # Binaer machen + kleine Luecken schliessen, damit eine Linie eine Flaeche bleibt
    mask_bin = cv2.morphologyEx((mask > 0).astype(np.uint8) * 255, cv2.MORPH_CLOSE, np.ones(morph_kernel, np.uint8))

    # Zusammenhaengende Flaechen (Label 0 = Hintergrund)
    num, labels, stats, _ = cv2.connectedComponentsWithStats(mask_bin, connectivity=8)
    if num <= 1:
        return [], np.zeros_like(mask_bin)

    # n groesste Flaechen, zu kleine verwerfen
    order = np.argsort(stats[1:, cv2.CC_STAT_AREA])[::-1][:n_lanes] + 1
    order = order[stats[order, cv2.CC_STAT_AREA] >= min_area]

    h = mask_bin.shape[0]
    y_min = int((1.0 - fit_frac) * h)   # nur unteren Bildanteil fuer den Fit nutzen (fit_frac=1.0 -> ganzes Bild)
    lanes, x_bottom = [], []
    for lbl in order:
        ys, xs = np.nonzero(labels == lbl)
        keep = ys >= y_min
        ys, xs = ys[keep], xs[keep]
        if ys.size < 2 or np.ptp(ys) < 2:   # zu wenig Punkte oder horizontale Flaeche
            continue

        # Fit x = a*y + c (stabil auch bei fast senkrechten Linien)
        a, c = np.polyfit(ys, xs, 1)
        m = float(1.0 / a) if abs(a) > 1.0 / m_max else float(np.copysign(m_max, a))
        b = float(ys.mean() - m * xs.mean())   # Gerade geht durch den Schwerpunkt der Flaeche

        lanes.append((m, b))
        x_bottom.append(a * (h - 1) + c)

    lanes = [lanes[i] for i in np.argsort(x_bottom)]   # links -> rechts ueber x am unteren Bildrand
    sel_mask = np.isin(labels, order).astype(np.uint8) * 255
    return lanes, sel_mask


def cals_drive_parameter_from_lanes(img, lanes):

    img_with_steering_lane = img.copy()
    if img_with_steering_lane.ndim == 2:
        img_with_steering_lane = cv2.cvtColor(img_with_steering_lane, cv2.COLOR_GRAY2RGB)

    h, w = img_with_steering_lane.shape[:2]
    if lanes is not None and len(lanes) == 2 and all(lane is not None for lane in lanes):
        y_bottom = h - 1
        y_top = 0
        x_bottom_arr = np.zeros(2)
        x_top_arr = np.zeros(2)
        for i, lane in enumerate(lanes):
            m, b = lane

            x1 = 0
            x2 = w - 1
            y1 = int(np.clip(m * x1 + b, -1e6, 1e6))
            y2 = int(np.clip(m * x2 + b, -1e6, 1e6))

            cv2.line(img_with_steering_lane, (x1, y1), (x2, y2), (0, 255, 0), 2)

            m_safe = m if abs(m) > 1e-6 else 1e-6
            x_bottom_arr[i] = (y_bottom - b) / m_safe
            x_top_arr[i] = (y_top - b) / m_safe

        x_steer_start = int(np.clip(np.mean(x_bottom_arr), -1e6, 1e6))
        x_steer_end = int(np.clip(np.mean(x_top_arr), -1e6, 1e6))

        Delta_x = x_steer_end - x_steer_start
        Delta_y = y_top - y_bottom

        slope_degree = np.degrees(np.arctan2(Delta_y, Delta_x))

        text = f"{slope_degree:.1f}"
        org = (20, 20)  # Position (x, y) - untere linke Ecke des Textes
        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 0.5
        color = (255, 0, 0)  # Farbe im BGR-Format (Grün)
        thickness = 2

        # Text ins Bild einfügen
        cv2.putText(img_with_steering_lane, text, org, font, font_scale, color, thickness, cv2.LINE_AA)

        cv2.line(img_with_steering_lane, (x_steer_start, y_bottom), (x_steer_end, y_top), (0, 0, 255), 2)

    speed = 0
    steering_angle = 90

    return speed, steering_angle, img_with_steering_lane

class CameraSensor(Camera):

    def __init__(self, car:CameraCar, sensor_config:Optional[ConfigReader.ConfigReader]=None, logger:Optional[logging.Logger]=None) -> None:

        self._log = logger if logger is not None else logging.getLogger(self.__class__.__name__)
        self._cfg = sensor_config if sensor_config is not None else ConfigReader.ConfigReader('cam_sensor', logger=self._log)
        self._lock = threading.Lock()
        self._debug_mode = False
        self._car = car
        self._pack_processing_parameters = ((0,179),(0,255),(0,255),(0.1,0.9), 35) #hue/saturation/value/crop
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
                img_blur = blur_img(mask, blur_value = (3,3))

                # Kantenerkennung
                img_kanten = kanten_erkennung(img_blur, minmaxVal = (50,100))

                # Hough-Transformation
                hough_parameter = hough_transformation(img_kanten, rho = 1, angle = np.pi/180, min_threshold = int(self.processing_parameters[4]))

                # Plotte Lines in Image
                #img_with_lines, _ = put_lines_into_image(img_kanten, hough_parameter)

                # NEU: Flaechen der Maske -> zwei groesste Flaechen -> Geradengleichungen (m, b)
                lanes, img_areas = detect_lanes_from_areas(img_blur, n_lanes=2, min_area=150, fit_frac=1.0)

                # Plotte die Maxima Linien ins Bild
                img_with_lines = put_steering_lines_into_image(img_kanten, lanes)
                
                # Berechne Fahrparameter aus lanes
                speed, steering_angle, img_with_steering_lane = cals_drive_parameter_from_lanes(img_with_lines, lanes)

                # cfg -> save_image -> FUNKTION: Checke Ordner fig_raw ja, nein
                #FIXME
                if self.save_image:
                    cv2.imwrite('camera_test_processed.jpg', mask)

                # Bild bearbeitung -> Linienwerte
                # Linienwerte an Auto (CameraCar.py) übergeben
                timestamp = time.time_ns()
                cameradata = CameraData(raw_image=image,
                                            timestamp = timestamp,
                                            process_image = img_with_steering_lane)

                self._car.camera_data = cameradata

                time.sleep(1/self.scan_frequency)
        except Exception as e:
            raise e
        finally:
            self._log.debug('Exit Camera loop')

    # def: Bild zuschneiden (Horizont weghauen)

    # def: Bearbeitetes Bild einlsesen, Bild bearbeiten, Linienwerte weitergeben

if __name__ == '__main__':

    print('This is the main.')