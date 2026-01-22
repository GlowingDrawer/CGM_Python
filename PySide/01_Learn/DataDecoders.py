import struct
import json
import logging


# =================================================
# 抽象基类 / 接口协议 (Duck Typing)
# =================================================
class BaseDecoder:
    def feed(self, data: bytes, on_frame_decoded):
        """
        接收原始字节流，解析出数据后回调 on_frame_decoded(dict)
        """
        pass

    def reset(self):
        """清空缓存"""
        pass


# =================================================
# 1. 二进制协议解析器 (对应你的 C++ 结构体)
# =================================================
class BinaryFrameDecoder(BaseDecoder):
    """
    解析 C++ 定义的 DataPacket 结构:
    Head(1) + MS(4) + Uric(2) + Ascorbic(2) + Glucose(2) + Code12(2) + Sum(1) + Tail(1)
    Total: 15 Bytes, Little Endian (<)
    """

    def __init__(self):
        self.buffer = bytearray()
        self.FRAME_LEN = 15
        self.HEAD = 0xA5
        self.TAIL = 0x5A
        # struct 格式: < (小端), I (uint32), H (uint16) * 4
        # Payload 部分对应: ms, uric, ascorbic, glucose, code12
        self.PAYLOAD_FMT = "<IHHHH"

    def feed(self, data: bytes, on_frame_decoded):
        self.buffer.extend(data)

        while len(self.buffer) >= self.FRAME_LEN:
            # 1. 寻找帧头
            if self.buffer[0] != self.HEAD:
                # 如果第一个字节不是帧头，移除它，继续找
                self.buffer.pop(0)
                continue

            # 2. 检查帧尾 (优化：先看尾部对不对，不对就不用算校验和了)
            if self.buffer[self.FRAME_LEN - 1] != self.TAIL:
                # 帧头是对的，但长度够了尾巴不对，说明这可能不是一个完整的包
                # 或者碰巧数据里有个 0xA5。移除帧头，继续找
                self.buffer.pop(0)
                continue

            # 3. 提取候选帧
            frame = self.buffer[:self.FRAME_LEN]

            # 4. 校验和验证 (防错)
            # C++算法: sum += pData[i] (Payload部分)
            # Payload 在 frame 的索引是 1 到 12 (不含13)
            payload = frame[1:13]
            calc_sum = sum(payload) & 0xFF  # 确保是 uint8
            recv_sum = frame[13]

            if calc_sum == recv_sum:
                # === 校验通过，解析数据 ===
                try:
                    # 解包 Payload
                    ms, uric_raw, ascorbic_raw, glucose_raw, code12 = struct.unpack(self.PAYLOAD_FMT, payload)

                    # 转换 DAC 码值为电压 (假设 12位 DAC, 参考电压 3.3V，根据你实际情况调整)
                    # 如果单片机发的就是电压值，这里就不用除
                    voltage_v = (code12 / 4095.0) * 3.3

                    # 构造与之前 JSON 一致的字典，保证 UI 不用改
                    decoded_data = {
                        "t": ms,  # 对应 C++ 的 ms
                        "voltage": voltage_v,  # 对应 C++ 的 code12 换算
                        "uric": uric_raw,  # 对应 C++ 的 uric
                        "ascorbic": ascorbic_raw,
                        "glucose": glucose_raw
                    }

                    # 回调给上层
                    on_frame_decoded(decoded_data)

                except Exception as e:
                    print(f"解析异常: {e}")

                # 消费掉这个完整帧
                del self.buffer[:self.FRAME_LEN]

            else:
                # 校验失败
                print(f"校验失败: Calc={calc_sum:02X}, Recv={recv_sum:02X}")
                # 移除帧头，尝试重新对齐
                self.buffer.pop(0)

    def reset(self):
        self.buffer.clear()


# =================================================
# 2. JSON 协议解析器 (兼容旧代码)
# =================================================
class JsonFrameDecoder(BaseDecoder):
    """
    处理基于换行符 \n 或 {} 的 JSON 文本流
    """

    def __init__(self):
        self.buffer = ""

    def feed(self, data: bytes, on_frame_decoded):
        # 将字节流转为字符串
        try:
            text = data.decode('utf-8', errors='ignore')
        except:
            return

        self.buffer += text

        # 简单的粘包处理：按换行符切割，或者按 JSON 对象切割
        # 这里假设单片机发 JSON 也是带换行符的，或者我们用括号匹配
        while "}" in self.buffer:
            try:
                # 寻找第一个 { 和对应的 }
                start = self.buffer.find("{")
                if start == -1:
                    self.buffer = ""
                    break

                # 简单的括号计数法来找匹配的 } (应对嵌套)
                depth = 0
                end = -1
                for i in range(start, len(self.buffer)):
                    if self.buffer[i] == '{':
                        depth += 1
                    elif self.buffer[i] == '}':
                        depth -= 1

                    if depth == 0:
                        end = i
                        break

                if end != -1:
                    json_str = self.buffer[start:end + 1]
                    self.buffer = self.buffer[end + 1:]  # 移除已处理部分

                    try:
                        obj = json.loads(json_str)
                        # 确保字段名归一化（根据你之前的 AppConfig）
                        # 这里直接传出去，由 MainViewModel 进行字段映射
                        on_frame_decoded(obj)
                    except json.JSONDecodeError:
                        pass  # 丢弃坏包
                else:
                    # 还没收完完整的包
                    break
            except Exception:
                self.buffer = ""  # 出错重置
                break

    def reset(self):
        self.buffer = ""