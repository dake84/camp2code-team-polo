"""Mock-Prozessor zum Testen der Simulation (nur Simulation, nie im Fahrbetrieb).

Verarbeitet das Bild wie der Copilot-Prozessor und veraendert danach die
Linien gezielt und reproduzierbar:

* jede Linie wird um ``SHIFT_PX`` Pixel nach rechts verschoben,
* in jedem ``DROP_EVERY``-ten Aufruf fehlt die rechte Linie.

Damit unterscheiden sich "damals" und "jetzt" in der Simulation sichtbar.
Kennzeichnung: Stufe ``mock`` im Ergebnis und ``IS_MOCK = True``.
"""
from pipeline_processors import CopilotLaneProcessor, PipelineResult


class MockProcessor(CopilotLaneProcessor):
    IS_MOCK = True
    SHIFT_PX = 12.0
    DROP_EVERY = 7

    def __init__(self):
        super().__init__()
        self._calls = 0

    @staticmethod
    def shift_lane(lane, dx):
        """Verschiebt ``y = m*x + b`` um ``dx`` Pixel in x-Richtung."""
        m, b = lane
        return float(m), float(b - m * dx)

    def process(self, raw, params):
        result = self._process(raw, params, capture=True)
        self._calls += 1
        lanes = [self.shift_lane(lane, self.SHIFT_PX) for lane in result.lanes if lane is not None]
        if self._calls % self.DROP_EVERY == 0 and len(lanes) == 2:
            lanes = lanes[:1]
        stages = dict(result.stages)
        stages['mock'] = result.processed_image
        return PipelineResult(result.processed_image, tuple(lanes), stages)
