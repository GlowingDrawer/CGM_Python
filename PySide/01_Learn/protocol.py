# protocol.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict

from config import AppConfig


@dataclass(frozen=True)
class Frame:
    ms: int
    uric: int
    ascorbic: int
    glucose: int
    code12: int


def _to_int(x: Any, default: int = 0) -> int:
    try:
        return int(x)
    except Exception:
        return default


def parse_frame(obj: Dict[str, Any], cfg: AppConfig) -> Frame:
    p = cfg.protocol
    return Frame(
        ms=_to_int(obj.get(p.field_ms, 0)),
        uric=_to_int(obj.get(p.field_uric, 0)),
        ascorbic=_to_int(obj.get(p.field_ascorbic, 0)),
        glucose=_to_int(obj.get(p.field_glucose, 0)),
        code12=_to_int(obj.get(p.field_code12, 0)),
    )


def adc_code_to_voltage(code12: int, cfg: AppConfig) -> float:
    c = cfg.calib
    return float(c.ref_volt) - float(code12) / float(c.adc_value_per_volt)


def adc_to_current(adc_value: int, gain: float, cfg: AppConfig) -> float:
    c = cfg.calib
    v = (float(adc_value) - float(c.ref_volt) * float(c.adc_value_per_volt)) / float(c.adc_value_per_volt)
    return v / float(gain)


def frame_to_engineering(frame: Frame, cfg: AppConfig):
    c = cfg.calib
    t_s = float(frame.ms) / float(c.time_gain)
    voltage = adc_code_to_voltage(frame.code12, cfg)
    uric_uA = adc_to_current(frame.uric, c.uric_gain, cfg)
    ascorbic_uA = adc_to_current(frame.ascorbic, c.ascorbic_gain, cfg)
    glucose_mA = adc_to_current(frame.glucose, c.glucose_gain, cfg)
    return t_s, voltage, uric_uA, ascorbic_uA, glucose_mA
