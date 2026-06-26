import json
import threading
import logging
from typing import Any, Optional

class ConfigReader:
    """
    Thread-sicherer JSON-Konfigurationsleser für Namespaced-Konfigurationen.

    Diese Klasse lädt eine JSON-Datei, liest Werte aus einem angegebenen
    Namespace und stellt Hilfsmethoden für verschiedene Datentypen bereit.
    """

    def __init__(self, cfg_namespace: str, config_file: str = "config.json", logger:Optional[logging.Logger]=None) -> None:
        """
        Initialisiert den ConfigReader und lädt die Konfigurationsdatei.

        Args:
            cfg_namespace (str): Der Namespace innerhalb der JSON-Datei,
                aus dem Werte gelesen werden sollen.
            config_file (str, optional): Pfad zur JSON-Konfigurationsdatei.
                Defaults to "new_config.json".

        Raises:
            AttributeError: Wenn die Datei nicht geladen werden kann.
        """
        self._config_file = config_file
        self._cfg_namespace = cfg_namespace
        self._lock = threading.Lock()
        self._log = logger if logger is not None else logging.getLogger(self.__class__.__name__)
        self._load_config_file()

    def _load_config_file(self):
        """
        Lädt die JSON-Konfigurationsdatei in den Speicher.

        Raises:
            AttributeError: Wenn die Datei nicht existiert oder nicht lesbar ist.
        """
        self._log.debug(f"Lese Config-File {self._config_file}")
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

    def get_config(self, force_update: bool = False) -> dict:
        """
        Gibt den Namespace-Abschnitt der Konfiguration zurück.

        Args:
            force_update (bool, optional): Wenn True, wird die Datei neu geladen.
                Defaults to False.

        Returns:
            dict: Der Konfigurationsabschnitt des angegebenen Namespaces.
        """
        with self._lock:
            if (force_update or self._json_config is None):
                self._load_config_file()
            return self._json_config.get(self._cfg_namespace, {})
    
    def set_config(self, attribut:str, values=any, save_config=False) -> bool:
        """
        Setzt einen Wert im Namespace und speichert optional die Datei.

        Args:
            attribut (str): Name des Konfigurationsparameters.
            values (Any): Neuer Wert, der gesetzt werden soll.
            save_config (bool, optional): Wenn True, wird die Datei gespeichert.
                Defaults to False.

        Returns:
            bool: True bei Erfolg, False bei Fehlern beim Speichern.
        """
        with self._lock:
            if (self._json_config is None):
                self._load_config_file()
            self._json_config.get(self._cfg_namespace, {})[attribut] = values
            return True if (not save_config) else self._save_config()
    
    def get(self, attribut:str, dv=None) -> None|Any:
        """
        Gibt einen beliebigen Wert aus der Konfiguration zurück.

        Args:
            attribut (str): Name des Parameters.
            dv (Any, optional): Default-Wert, falls der Parameter nicht existiert.
                Defaults to None.

        Returns:
            Any: Der gelesene Wert oder der Default-Wert.
        """      
        return self.get_config().get(attribut, dv)

    def get_list(self, attribut: str, default: Optional[list] = None) -> list:
        """
        Gibt eine Liste aus der Konfiguration zurück.

        Args:
            attribut (str): Name des Parameters.
            default (list, optional): Default-Liste, falls der Parameter nicht existiert.
                Defaults to None.

        Returns:
            list: Die gelesene Liste oder der Default-Wert.
        """
        dv = default if default is not None else []
        return self.get_config().get(attribut, dv)

    def get_int(self, attribut: str, dv: int) -> int:
        """
        Gibt einen Integer-Wert aus der Konfiguration zurück.

        Args:
            attribut (str): Name des Parameters.
            dv (int): Default-Wert, falls der Parameter nicht existiert.

        Returns:
            int: Der gelesene Integer-Wert.
        """
        return int(self.get_config().get(attribut, dv))

    def get_float(self, attribut: str, dv: float) -> float:
        """
        Gibt einen Float-Wert aus der Konfiguration zurück.

        Args:
            attribut (str): Name des Parameters.
            dv (float): Default-Wert, falls der Parameter nicht existiert.

        Returns:
            float: Der gelesene Float-Wert.
        """
        return float(self.get_config().get(attribut, dv))

    def _save_config(self) -> bool:
        """
        Speichert die aktuelle Konfiguration zurück in die JSON-Datei.

        Returns:
            bool: True bei Erfolg, False bei Fehlern beim Schreiben.
        """
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


class Configurable():
    
    def __init__(self, config_reader:Optional[ConfigReader]=None):
        self._cfg = config_reader if config_reader is not None else ConfigReader("ir_sensors")
