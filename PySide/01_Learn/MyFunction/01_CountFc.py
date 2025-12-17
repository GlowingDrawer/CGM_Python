import math

k = 1000
uF = 1e-6
nF = 1e-9

Res_Value = 4.7 * k
Cap_Value = 10 * nF


def count_fc(res:float, cap:float):
    FcValue = 1 / (2 * math.pi * res * cap)
    print(f"截止频率为: {FcValue:.2f}Hz")


def count_capacity(res:float, fc:float):
    CapValue = 1 / (2 * math.pi * res * fc) * 1e9
    sign = "nF"
    if CapValue > 1e9:
        sign = "F"
        CapValue *= 1e-9
    elif CapValue > 1e6:
        sign = "mF"
        CapValue = 1e-6
    elif CapValue > 1e3:
        sign = "uF"
        CapValue *= 1e-3
    print(f"电容值为: {CapValue:.2f}{sign}")


def count_resist(cap:float, fc:float):
    ResValue = 1 / (2 * math.pi * cap * fc)
    print(f"电阻值为{ResValue:.2f}Ω")


if __name__ == '__main__':
    count_fc(Res_Value, Cap_Value)