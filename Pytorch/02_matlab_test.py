"""
示例：使用 MATLAB Engine 调用 MATLAB 仿真函数生成血糖数据，
并在 Python 中实现简单的血糖监测算法（去噪 + 低血糖预警）。

运行前准备：
1. 已正确安装 MATLAB Engine for Python（在当前 conda 环境）。
2. MATLAB 中存在函数：
   [t_vec, cgm] = run_cgm_simulation(patient_id, ndays)
3. 修改 MATLAB_ROOT 与 SIMULATOR_PATH 为你本机的实际路径。
"""

import matlab.engine
import numpy as np
import pandas as pd
import time
from typing import Tuple, List


# ============== 1. 启动 MATLAB Engine ==============

def start_matlab_engine() -> matlab.engine.MatlabEngine:
    """以精简模式启动 MATLAB，引擎只启动一次反复使用。"""
    print("Starting MATLAB engine...")
    t0 = time.time()
    # 推荐精简启动参数，减少 GUI 开销
    eng = matlab.engine.start_matlab("-nodesktop -nosplash")
    t1 = time.time()
    print(f"MATLAB engine started in {t1 - t0:.2f} seconds.")
    return eng


# ============== 2. 添加 MATLAB 仿真器路径 ==============

def setup_matlab_paths(eng: matlab.engine.MatlabEngine,
                       simulator_path: str):
    """
    向 MATLAB 路径中添加仿真器/模型文件目录。
    simulator_path: MATLAB 仿真器所在目录（包含 run_cgm_simulation.m）
    """
    print(f"Adding simulator path to MATLAB: {simulator_path}")
    eng.addpath(simulator_path, nargout=0)


# ============== 3. 调 MATLAB 仿真函数生成 CGM 数据 ==============

def simulate_cgm_with_matlab(
        eng: matlab.engine.MatlabEngine,
        patient_id: int,
        ndays: int = 2
) -> pd.DataFrame:
    """
    调用 MATLAB 函数 run_cgm_simulation，返回 DataFrame:
    columns = ['time_min', 'cgm_mgdl']
    """
    print(f"Running MATLAB CGM simulation for patient {patient_id}, {ndays} day(s)...")
    # 调用 MATLAB 函数：[t_vec, cgm] = run_cgm_simulation(patient_id, ndays)
    # 注意：函数名与签名需要与你实际 MATLAB 代码一致
    t_vec_mat, cgm_mat = eng.run_cgm_simulation(
        float(patient_id),  # MATLAB 默认 double
        float(ndays),
        nargout=2
    )

    # matlab.double -> numpy array
    # t_vec_mat 是 matlab.double 嵌套列表，需转换
    t_vec = np.array(t_vec_mat).flatten()
    cgm = np.array(cgm_mat).flatten()

    # 打包成 DataFrame
    df = pd.DataFrame({
        "time_min": t_vec,
        "cgm_mgdl": cgm
    }).sort_values("time_min").reset_index(drop=True)

    print(f"Simulation done: {len(df)} points.")
    return df


# ============== 4. Python 侧血糖监测算法（示例） ==============

def smooth_cgm(df: pd.DataFrame,
               window: int = 5) -> pd.DataFrame:
    """
    使用简单移动平均对 CGM 去噪。
    window: 滑动窗口点数（与采样间隔有关）
    """
    df = df.copy()
    df["cgm_smooth"] = df["cgm_mgdl"].rolling(window=window,
                                              min_periods=1).mean()
    return df


def detect_hypoglycemia(
        df: pd.DataFrame,
        threshold: float = 70.0,
        min_duration_min: float = 15.0
) -> List[Tuple[float, float]]:
    """
    检测低血糖事件：
    - 使用平滑后的 cgm_smooth；
    - 连续低于阈值 threshold，持续时间 >= min_duration_min。

    返回 [(start_time_min, end_time_min), ...]
    """
    if "cgm_smooth" not in df.columns:
        raise ValueError("DataFrame must contain 'cgm_smooth' column.")

    df = df.copy()
    below = df["cgm_smooth"] < threshold

    events = []
    in_event = False
    start_time = None

    # 假设 time_min 单位为分钟，采样间隔为 delta_t
    if len(df) >= 2:
        delta_t = float(df["time_min"].iloc[1] - df["time_min"].iloc[0])
    else:
        delta_t = 5.0

    for i, is_low in enumerate(below):
        t = df["time_min"].iloc[i]
        if is_low and not in_event:
            # 进入低血糖区间
            in_event = True
            start_time = t
        elif not is_low and in_event:
            # 退出低血糖区间
            end_time = df["time_min"].iloc[i - 1]
            duration = end_time - start_time + delta_t
            if duration >= min_duration_min:
                events.append((start_time, end_time))
            in_event = False
            start_time = None

    # 如果最后仍在低血糖区间，闭合事件
    if in_event and start_time is not None:
        end_time = df["time_min"].iloc[-1]
        duration = end_time - start_time + delta_t
        if duration >= min_duration_min:
            events.append((start_time, end_time))

    return events


# ============== 5. 主流程：MATLAB 仿真 + Python 检测 ==============

def main():
    # 1. 启动 MATLAB Engine
    eng = start_matlab_engine()

    try:
        # 2. 配置 MATLAB 路径（改成你自己的 UVA–Padova/仿真器路径）
        SIMULATOR_PATH = r"D:\Your\UVA_Padova_Simulator"  # TODO: 修改
        setup_matlab_paths(eng, SIMULATOR_PATH)

        # 3. 生成一个患者 2 天的 CGM 数据
        patient_id = 1
        ndays = 2
        df_cgm = simulate_cgm_with_matlab(eng, patient_id, ndays)

        # 4. Python 侧做去噪 + 低血糖事件检测
        df_cgm = smooth_cgm(df_cgm, window=5)
        events = detect_hypoglycemia(df_cgm,
                                     threshold=70.0,
                                     min_duration_min=15.0)

        # 5. 输出结果
        print("\nDetected hypoglycemia events (threshold=70 mg/dL, duration>=15 min):")
        if not events:
            print("  No events detected.")
        else:
            for i, (t_start, t_end) in enumerate(events, 1):
                print(f"  Event {i}: {t_start:.1f} min → {t_end:.1f} min "
                      f"(duration ≈ {t_end - t_start:.1f} min)")

        # 如需可视化，可用 matplotlib 画图（可选）
        try:
            import matplotlib.pyplot as plt

            plt.figure()
            plt.plot(df_cgm["time_min"], df_cgm["cgm_mgdl"], label="Raw CGM", alpha=0.5)
            plt.plot(df_cgm["time_min"], df_cgm["cgm_smooth"], label="Smoothed CGM", linewidth=2)
            for (t_start, t_end) in events:
                plt.axvspan(t_start, t_end, alpha=0.2, label="Hypo event")
            plt.axhline(70.0, color="red", linestyle="--", label="Threshold 70 mg/dL")
            plt.xlabel("Time (min)")
            plt.ylabel("Glucose (mg/dL)")
            plt.legend()
            plt.title(f"Patient {patient_id} CGM Simulation + Hypoglycemia Detection")

