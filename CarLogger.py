
from BaseCar import BaseCar
from SensorCar import SensorCar


class CarLogger():

    def log_ir_sensor_values(self, car: SensorCar):
        ir_values = car.normierte_sensorwerte()
    
    def log_driving_values(self, car: BaseCar):
        lw = car.steering_angle
        v = car.speed

    

