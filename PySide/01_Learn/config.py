# config.py
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional
import json
# 改成


# --------------------------- Enums ---------------------------

class FilterType(str, Enum):
    NONE = "无滤波"
    MOVING_AVG = "滑动平均"
    MEDIAN = "中值滤波"
    KALMAN = "卡尔曼滤波"


# --------------------------- Dataclasses ---------------------------

@dataclass
class SerialConfig:
    baudrate: int = 115200
    databits: int = 8
    stopbits: str = "1"        # "1" / "1.5" / "2"
    parity: str = "None"       # "None" / "Odd" / "Even" / "Mark" / "Space"
    auto_reconnect: bool = False

    # 你现在代码里会“蓝牙强制9600”，建议改成配置项，默认关闭
    bluetooth_force_9600: bool = False
    bluetooth_baudrate: int = 9600

    # 轮询/线程读取相关（如果后续想统一也可用）
    is_bluetooth_hint: bool = True
    poll_interval_bluetooth_ms: int = 20
    poll_interval_uart_ms: int = 5


@dataclass
class ProtocolConfig:
    # STM32 输出字段名（你现在的最终版：Ms / Code12）
    field_ms: str = "Ms"
    field_uric: str = "Uric"
    field_ascorbic: str = "Ascorbic"
    field_glucose: str = "Glucose"
    field_code12: str = "Code12"

    # 行协议（STM32 每行一条 JSON + '\n'）
    line_delimiter: str = "\n"


@dataclass
class CalibrationConfig:
    # 你的工程换算参数集中化
    adc_value_per_volt: float = 1240.9091
    ref_volt: float = 1.5
    time_gain: float = 1000.0

    uric_gain: float = 20400 / 1_000_000
    ascorbic_gain: float = 4700 / 1_000_000
    glucose_gain: float = 200 / 1000


@dataclass
class FilterConfig:
    filter_type: FilterType = FilterType.MOVING_AVG
    window_size: int = 5
    kalman_Q: float = 0.01
    kalman_R: float = 0.1


@dataclass
class SaveConfig:
    auto_save: bool = False
    save_interval_ms: int = 1000
    save_path: str = "./serial_data"


@dataclass
class UIConfig:
    max_table_rows: int = 2000
    ui_update_interval_ms: int = 50


@dataclass
class AppConfig:
    serial: SerialConfig = field(default_factory=SerialConfig)
    protocol: ProtocolConfig = field(default_factory=ProtocolConfig)
    calib: CalibrationConfig = field(default_factory=CalibrationConfig)
    filt: FilterConfig = field(default_factory=FilterConfig)
    save: SaveConfig = field(default_factory=SaveConfig)
    ui: UIConfig = field(default_factory=UIConfig)

    @staticmethod
    def default_path() -> Path:
        return Path("./app_config.json")

    # --------------------- Public API ---------------------

    @classmethod
    def load(cls, path: Optional[str | Path] = None) -> "AppConfig":
        """从 JSON 加载配置；文件不存在则返回默认配置；缺字段自动填默认值。"""
        p = Path(path) if path is not None else cls.default_path()
        if not p.exists():
            return cls()

        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return cls()
        except Exception:
            # 配置损坏时：直接回默认（也可选择备份后重建）
            return cls()

        cfg = cls()
        cfg._merge_inplace(raw)
        return cfg

    def save_to(self, path: Optional[str | Path] = None) -> None:
        """保存到 JSON；Enum 自动转字符串。"""
        p = Path(path) if path is not None else self.default_path()
        d = asdict(self)

        # Enum -> value
        d["filt"]["filter_type"] = self.filt.filter_type.value

        p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

    # --------------------- Internal helpers ---------------------

    def _merge_inplace(self, raw: Dict[str, Any]) -> None:
        """将 raw dict 合并到当前对象，忽略未知字段，保留默认值。"""
        self.serial = _safe_dataclass_update(self.serial, raw.get("serial", {}), SerialConfig)
        self.protocol = _safe_dataclass_update(self.protocol, raw.get("protocol", {}), ProtocolConfig)
        self.calib = _safe_dataclass_update(self.calib, raw.get("calib", {}), CalibrationConfig)

        # filt 需要处理 Enum
        filt_raw = raw.get("filt", {})
        if isinstance(filt_raw, dict):
            filt_raw = dict(filt_raw)
            ft = filt_raw.get("filter_type", None)
            if isinstance(ft, str):
                try:
                    filt_raw["filter_type"] = FilterType(ft)
                except Exception:
                    # 兼容旧值/非法值：回默认
                    filt_raw["filter_type"] = self.filt.filter_type
        self.filt = _safe_dataclass_update(self.filt, filt_raw, FilterConfig)

        self.save = _safe_dataclass_update(self.save, raw.get("save", {}), SaveConfig)
        self.ui = _safe_dataclass_update(self.ui, raw.get("ui", {}), UIConfig)


def _safe_dataclass_update(current_obj, patch: Any, cls_type):
    """
    只从 patch dict 里取 cls_type 支持的字段更新；
    patch 非 dict 时直接返回 current_obj。
    """
    if not isinstance(patch, dict):
        return current_obj

    # 允许 patch 中有未知字段：忽略
    allowed = {f.name for f in cls_type.__dataclass_fields__.values()}
    clean = {}
    for k, v in patch.items():
        if k in allowed:
            clean[k] = v

    # 用 current 的值作默认，patch 覆盖
    base = asdict(current_obj)
    base.update(clean)

    try:
        return cls_type(**base)
    except Exception:
        # patch 数据类型不对等，回退为 current_obj，保证程序可用
        return current_obj
