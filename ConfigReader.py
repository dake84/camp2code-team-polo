import json
import threading
import logging
from typing import Any, Optional

class ConfigReader():

    #DEFAULT_NAMESPACES = {
    #    BaseCar.BaseCar: "car",
    #    InfraredSensor.InfraredSensor: "ir_sensors"
    #}

    def __init__(self, cfg_namespace:str, config_file="config.json", logger:Optional[logging.Logger]=None) -> None:
        self._config_file = config_file
        self._cfg_namespace = cfg_namespace
        self._lock = threading.Lock()
        self._log = logger if logger is not None else logging.getLogger(self.__class__.__name__)
        self._load_config_file()
        pass

    def _load_config_file(self):
        self._log.info(f"Lese Config-File {self._config_file}")
        try:
            with open(self._config_file, "r") as f:
                self._json_config = json.load(f)
                self._log.debug(f"Config-File gelesen {self._config_file}")
        except OSError as e:
            error = AttributeError(name=f"Datei {self._config_file} nicht verfügbar")
            self._log.error(error)
            raise error
        except Exception as e:
            self._log.error(e)
            raise e

    def get_config(self, force_update = False) -> dict:
        with self._lock:
            if (force_update or self._json_config is None):
                self._load_config_file()
            return self._json_config.get(self._cfg_namespace, {})
    
    def set_config(self, attribut:str, values=any, save_config=False) -> bool:
        with self._lock:
            if (self._json_config is None):
                self._load_config_file()
            self._json_config.get(self._cfg_namespace, {})[attribut] = values
            return True if (not save_config) else self._save_config()
    
    def get(self, attribut:str, dv=None) -> None|Any:
        return self.get_config().get(attribut, dv)

    def get_list(self, attribut:str, default:Optional[list|None]=None) -> list:
        dv = default if default is not None else {}
        return self.get_config().get(attribut, dv)

    def get_int(self, attribut:str, dv:int) -> int:
        return int(self.get_config().get(attribut, dv))

    def _save_config(self) -> bool:
        try:
            with open(self._config_file, "w", encoding="utf-8") as jf:
                json.dump(self._json_config, jf, indent=4, ensure_ascii=True, sort_keys=True)
            return True
        except OSError as e:
            error = AttributeError(name=f"Datei {self._config_file} nicht verfügbar")
            self._log.error(error)
            raise error
        except Exception as e:
            self._log.error(e)
            raise e        
     