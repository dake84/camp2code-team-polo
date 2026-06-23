from typing import Optional

from ConfigReader import ConfigReader


class Configurable():
    
    def __init__(self, config_reader:Optional[ConfigReader]=None):
        self._cfg = config_reader if config_reader is not None else ConfigReader("ir_sensors", logger=self._log)

