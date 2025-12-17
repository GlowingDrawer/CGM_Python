# REQUIREMENTS
# pip install pandas numpy scikit-learn

import pandas as pd
import numpy as np
from sklearn.model_selection import GroupKFold

# 1) 读取数据
df = pd.read_csv("uva_padova_surrogate_cgm_100p_30d.csv", parse_dates=["timestamp"])

# 2) 简单预处理：按 patient + timestamp 排序
df = df.sort_values(["patient_id", "timestamp"]).reset_index(drop=True)

# 3) 重索引 / 重采样（如果需要改变采样频率）
def resample_patient(df_patient, freq="5min"):
    dfp = df_patient.set_index("timestamp").resample(freq).first()
    dfp["patient_id"] = df_patient["patient_id"].iloc[0]
    # 插值短缺失
    dfp["cgm"] = dfp["cgm"].interpolate(limit=6, method="time")
    # 填充其他 metadata
    for col in ["baseline","age","weight_kg","insulin_sensitivity","carb_ratio"]:
        if col in df_patient.columns:
            dfp[col] = df_patient[col].iloc[0]
    # fill meal/insulin flags
    dfp["meal_flag"] = dfp["meal_flag"].fillna(0).astype(int)
    dfp["meal_carbs"] = dfp["meal_carbs"].fillna(0).astype(int)
    dfp["insulin_flag"] = dfp["insulin_flag"].fillna(0).astype(int)
    dfp["insulin_units"] = dfp["insulin_units"].fillna(0.0)
    return dfp.reset_index()

# Example: resample a single patient
p0 = df[df.patient_id == df.patient_id.unique()[0]]
p0_res = resample_patient(p0, "5min")

# 4) 特征工程（滑窗特征）
def add_features(dfp, windows=[3,6,12,36]): # windows in number of samples (5-min each)
    # windows e.g., 3->15min, 6->30min, etc.
    for w in windows:
        dfp[f"cgm_mean_{w}"] = dfp["cgm"].rolling(window=w, min_periods=1).mean()
        dfp[f"cgm_std_{w}"]  = dfp["cgm"].rolling(window=w, min_periods=1).std().fillna(0)
    dfp["cgm_diff_1"] = dfp["cgm"].diff().fillna(0)
    dfp["hour"] = dfp["timestamp"].dt.hour
    dfp["is_night"] = ((dfp["hour"] >= 22) | (dfp["hour"] < 6)).astype(int)
    # mask for missing
    dfp["mask"] = (~dfp["cgm"].isna()).astype(float)
    return dfp

p0_feat = add_features(p0_res)

# 5) 构造监督序列：滑窗输入 -> 多步输出
def make_sequences(series_df, input_len=60, pred_horizon=12, step=1, feature_cols=None, target_col="cgm"):
    # inputs: sliding windows of length input_len (samples)
    # target: next pred_horizon samples (multi-step)
    X, Y = [], []
    T = len(series_df)
    if feature_cols is None:
        feature_cols = [target_col]
    arrX = series_df[feature_cols].values
    arrY = series_df[target_col].values
    for i in range(0, T - input_len - pred_horizon + 1, step):
        x = arrX[i : i + input_len]
        y = arrY[i + input_len : i + input_len + pred_horizon]
        X.append(x)
        Y.append(y)
    return np.array(X), np.array(Y)

# Example usage
feature_cols = ["cgm","cgm_diff_1","cgm_mean_6","cgm_std_6","meal_flag","insulin_flag","is_night"]
X, Y = make_sequences(p0_feat, input_len=60, pred_horizon=12, feature_cols=feature_cols)

# 6) LOSO split for cross-validation
patients = df.patient_id.unique()
gkf = GroupKFold(n_splits=len(patients))  # LOSO: folds == n_patients
# but for speed you may choose fewer folds, or manual LOSO
# Example: single-train/test split
train_idx = df.patient_id != patients[0]
test_idx = df.patient_id == patients[0]
