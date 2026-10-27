"""Uebergabe eines Replay-Frames an das Pipeline Studio (A/B: damals vs. jetzt).

Es wird nichts neu erkannt: Das gespeicherte Rohbild und die damals
verwendeten Verarbeitungsparameter werden unveraendert an PipelineAB gegeben.
"""
import json
from pathlib import Path

import cv2


def parameters_from_record(value):
    """Wandelt gespeicherte Parameter (Tupel, Liste oder JSON-Text) in das Prozessorformat.

    Format: (hue, sat, val, crop, blur, morph[, canny[, enabled]]).
    """
    if value is None or value == '':
        raise ValueError('Frame enthält keine Verarbeitungsparameter (Session v1?)')
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError as exc:
            raise ValueError('Verarbeitungsparameter nicht lesbar') from exc
    if not isinstance(value, (list, tuple)) or len(value) < 6:
        raise ValueError('Verarbeitungsparameter unvollständig')
    result = []
    for index, item in enumerate(value):
        if index == 7:
            if not isinstance(item, dict):
                raise ValueError('Schalter der Pipeline-Stufen fehlen')
            result.append({str(k): bool(v) for k, v in item.items()})
        elif isinstance(item, (list, tuple)):
            if len(item) != 2:
                raise ValueError(f'Parameter {index} ist kein Wertepaar')
            result.append(tuple(item))
        else:
            result.append(item)
    return tuple(result)


def processor_key(processor_name):
    """Klassenname aus der Aufzeichnung -> Schluessel fuer PipelineAB."""
    return 'legacy' if processor_name in ('LaneProcessor', 'legacy') else 'copilot'


def load_session_image(path, row):
    """Laedt das unveraenderte Rohbild eines Session-Frames (nur innerhalb der Session)."""
    base = Path(path).resolve()
    filename = (base / str(row.get('image_filename') or '')).resolve()
    if not filename.is_relative_to(base) or not filename.is_file():
        raise ValueError('Bild fehlt')
    image = cv2.imread(str(filename))
    if image is None or image.size == 0:
        raise ValueError('Bild nicht lesbar')
    return image
