import json
import threading
from typing import Any, Optional

from BaseCar import BaseCar
from InfraredSensor import InfraredSensors

class ConfigReader():

    DEFAULT_NAMESPACES = {
        BaseCar: "car",
        InfraredSensors: "ir_sensors"
    }

    def __init__(self, cfg_namespace:str|type, config_file="new_config.json") -> None:
        self._config_file = config_file
        self._cfg_namespace = self.DEFAULT_NAMESPACES[type] if isinstance(cfg_namespace, type) else cfg_namespace 
        self._lock = threading.Lock()
        pass

    def _load_config_file(self):
        try:
            with open(self._config_file, "r") as f:
                self._json_config = json.load(f)
        except:
            print("Keine geeignete Datei config.json gefunden!")
            raise AttributeError(name=f"Datei {self._config_file} nicht verfügbar")            

    def get_config(self, force_update = False) -> dict:
        with self._lock:
            if (force_update or self._json_config is None):
                self._load_config_file()     
            return self._json_config.get(self._cfg_namespace, {})
    
    def set_config(self, attribut:str, values=any, save_config=False) -> bool:
        with self._lock:
            if (self._json_config is None):
                self._load_config_file()
            self._json_config.get(self._cfg_namespace, {}).set(attribut, values)
            return True if (not save_config) else self._save_config()
    
    def get(self, attribut:str, dv=None) -> None|Any:
        return self.get_config().get(attribut, dv)

    def get_list(self, attribut:str, default:Optional[list|None]=None) -> list:
        dv = default if default is not None else {}
        return self.get_config().get(attribut, dv)

    def _save_config(self) -> bool:
        try:
            with open(self._config_file, "w", encoding="utf-8") as jf:
                json.dump(self._json_config, jf, indent=4, ensure_ascii=True)
            return True
        except OSError as e:
            print(f"Fehler beim Schreiben der Config-File: {e}")
            return False
     