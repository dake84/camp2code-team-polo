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

import myplot as myplt

def Count_I_D(t, I, step_num=500, plot_bool=False):

    t = np.asarray(t, dtype=float).ravel()
    I = np.asarray(I, dtype=float).ravel()

    valid = np.isfinite(t) & np.isfinite(I)
    t = t[valid]
    I = I[valid]

    if I.size == 0:
        raise ValueError('Count_I_D: Das übergebene Stromarray ist leer.')

    step_num = max(2, int(step_num))

    Counts = np.zeros(step_num, dtype=np.int32)
    Cound_idxs = np.zeros((step_num, len(t)), dtype=np.int32)
    I_interval = np.zeros(step_num, dtype=float)

    I_min_global = np.min(I)
    I_max_global = np.max(I)

    if np.isclose(I_max_global, I_min_global):
        Counts[0] = len(I)
        I_interval[:] = I_min_global
        Cound_idxs[0, :] = np.arange(len(I), dtype=np.int32)

        return Counts, I_interval, Cound_idxs

    I_step = (I_max_global - I_min_global) / step_num

    edges = (
        I_min_global
        + np.arange(step_num + 1, dtype=float) * I_step
    )

    I_interval[:] = (
        I_min_global
        + (np.arange(step_num, dtype=float) + 0.5) * I_step
    )

    bin_idx = np.digitize(I, edges, right=False)

    pos_frac = (I - I_min_global) / I_step

    on_edge = np.isclose(
        pos_frac - np.floor(pos_frac),
        0.0,
        atol=1e-12,
        rtol=0.0,
    )

    valid = (
        (bin_idx >= 1)
        & (bin_idx <= step_num)
        & (~on_edge)
    )

    pos = np.flatnonzero(valid)
    bins0 = bin_idx[pos] - 1

    Counts[:] = np.bincount(
        bins0,
        minlength=step_num,
    )

    if pos.size:
        Cound_idxs[bins0, pos] = pos.astype(np.int32)

    if plot_bool:

        data = [
            I_interval,
            Counts,
            'Values',
            'blue',
            'y1',
            '-',
        ]

        plot = myplt.Plot()
        plot.fig_num = 1
        plot.x_axe_name = 'Interval'
        plot.y_axe_name = 'Counts'
        plot.log_x = True
        plot.use_meta_data(data)
        plot.plotting()

    return Counts, I_interval, Cound_idxs

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

def detect_lanes(hough_parameter, step_num=50, m_max=10, min_dist=1, plot_bool=True):
    ## Berechnet aus den Hough-Parametern die Steigung m = -cos(theta)/sin(theta), plottet die Häufigkeit
    ## und gibt für die zwei Maxima die Geradengleichung y = m*x + b zurück.
    if hough_parameter is None or len(hough_parameter) == 0:
        return None, None, []
    rho, theta = hough_parameter.reshape(-1, 2).T
    sin_t = np.maximum(np.sin(theta), 1e-6)
    m_lines = np.clip(-np.cos(theta) / sin_t, -0.999 * m_max, 0.999 * m_max)
    b_lines = rho / sin_t
    m = np.append(m_lines, [-m_max, m_max])   # feste Bin-Grenzen, Dummy-Punkte fallen in Count_I_D auf die Kante und werden nicht gezählt

    Counts, m_interval, _ = Count_I_D(np.arange(len(m)), m, step_num=step_num, plot_bool=False)

    # Zwei Maxima mit Mindestabstand von min_dist Bins -> Geradengleichung (m, b)
    Counts_tmp = Counts.copy()
    m_step = m_interval[1] - m_interval[0]
    lanes = []
    for _ in range(2):
        idx = int(np.argmax(Counts_tmp))
        if Counts_tmp[idx] == 0:
            break
        sel = np.abs(m_lines - m_interval[idx]) <= 1.5 * m_step   # Peak-Bin ±1 Nachbar-Bin
        lanes.append((float(np.mean(m_lines[sel])), float(np.mean(b_lines[sel]))))
        Counts_tmp[max(0, idx - min_dist):idx + min_dist + 1] = 0
    lanes.sort()   # links (m < 0) zuerst

    if plot_bool:
        data = [m_interval, Counts, 'Steigungen', 'blue', 'y1', '-']
        plot = myplt.Plot()
        plot.fig_num = 1
        plot.x_axe_name = 'Steigung $m$'
        plot.y_axe_name = 'Häufigkeit'
        plot.log_x = False
        plot.use_meta_data(data)
        plot.save_plotting = True
        plot.plotting()

    return Counts, m_interval, lanes

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
        self._pack_processing_parameters = ((0,179),(0,255),(0,255),(0.1,0.9)) #hue/saturation/value/crop
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
                hough_parameter = hough_transformation(img_kanten, rho = 1, angle = np.pi/180, min_threshold = 35)

                # Plotte Lines in Image
                img_with_lines, _ = put_lines_into_image(img_kanten, hough_parameter)

                # Steigungsverteilung der Hough-Linien
                counts, m_interval, lanes = detect_lanes(hough_parameter)

                # Plotte die Maxima Linien ins Bild
                img_with_lines = put_steering_lines_into_image(img_with_lines, lanes)
                
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
