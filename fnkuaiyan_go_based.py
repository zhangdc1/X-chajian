#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
飞鸟快验Python API - 基于Go SDK实现
完全按照官方Go SDK的实现方式重新编写
修复了所有加密和通讯问题
"""

import requests
import time
import psutil
import uuid
import socket
import random
from loguru import logger
import datetime
import hashlib
import base64
import json
from Crypto.Cipher import AES, PKCS1_v1_5
from Crypto.PublicKey import RSA
from Crypto.Random import get_random_bytes
from Crypto.Util.Padding import pad, unpad

class FnKuaiYanGoBasedAPI:
    """基于 SDK实现的飞鸟快验Python API"""

    def __init__(self, config=None):
        """
        初始化API
        参数配置格式:
        {
            "AppWeb": "https://demo.fnkuaiyan.cn/Api?AppId=10001",
            "CryptoKeyPublic": "-----BEGIN PUBLIC KEY-----\n...",
            "CryptoType": 3,
            "CryptoKeyAes": "24字节AES密钥"  # 可选，RSA模式会自动生成
        }
        """
        self.default_config = {"AppWeb": "http://47.119.180.33:5888/Api?AppId=10003",
                               "CryptoKeyPublic": "-----BEGIN PUBLIC KEY-----\nMIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCwdDkmltaK3kpL2bWrCQMFsXdN\nst91/ttAv9HDQvuPT1pPc+0vBipdj1SoWmY1MJf7SFosa7JQHQaOgALy4krFL7eM\nDKCiLOo6g50rdW9z0la8BzoLZEQ6XlrYKO54/IRLCqyKWjMEpPpb3iZXmguaTU2m\nUgVQ+QdLtnPI7Gh5NQIDAQAB\n-----END PUBLIC KEY-----\n",
                               "CryptoType": 3}

        self.config = config if config else self.default_config
        self.url = self.config["AppWeb"]
        self.crypto_type = self.config.get("CryptoType", 3)
        self.token = ""
        self.error_message = ""
        self.error_code = 0

        # 根据Go SDK的实现设置AES密钥
        if self.crypto_type == 3:
            # RSA模式：生成24字节随机AES密钥（对应Go: utils.W文本_取随机字符串(24)）
            self.aes_key = self._generate_random_string(24).encode('utf-8')

            # 加载RSA公钥
            try:
                self.rsa_public_key = RSA.import_key(self.config["CryptoKeyPublic"])
                print(f"✓ RSA公钥加载成功: {self.rsa_public_key.size_in_bits()}位")
            except Exception as e:
                print(f"✗ RSA公钥加载失败: {e}")
                self.rsa_public_key = None
        elif self.crypto_type == 2:
            # AES模式：使用配置的24字节AES密钥
            self.aes_key = self.config.get("CryptoKeyAes", "").encode('utf-8')[:24]
            if len(self.aes_key) != 24:
                raise ValueError("AES密钥长度必须为24字节")
        else:
            # 明文模式
            self.aes_key = b""

        # 强制RSA加密的接口列表（对应Go SDK的强制Rsa加密接口）
        self.force_rsa_apis = [
            "GetToken", "UserLogin", "UserReduceMoney",
            "UserReduceVipNumber", "UserReduceVipTime", "GetVipData",
            "SetUserConfig", "GetUserConfig", "SetAppUserKey"
        ]

        print(f"🔐 加密模式: Type={self.crypto_type}")
        print(f"📡 服务器: {self.url}")

    def _generate_random_string(self, length):
        """生成随机字符串（对应Go: utils.W文本_取随机字符串）"""
        chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890"
        result = ""
        for _ in range(length):
            result += random.choice(chars[:-1])  # 第一位不能是0
        if result[0] == '0':  # 第一位不能是0
            result = '9' + result[1:]
        return result

    def get_machine_code(self):
        """获取机器码"""
        try:
            # CPU信息
            cpu_info = psutil.cpu_count()

            # MAC地址
            mac = uuid.getnode()
            mac_addr = ':'.join(('%012X' % mac)[i:i + 2] for i in range(0, 12, 2))

            # 磁盘信息
            disk_usage = psutil.disk_usage('/')

            # 组合生成机器码
            machine_info = f"{cpu_info}_{mac_addr}_{disk_usage.total}"
            machine_code = hashlib.md5(machine_info.encode()).hexdigest().upper()

            return machine_code
        except Exception as e:
            print(f"获取机器码失败: {e}")
            return "DEFAULT_MACHINE_CODE"

    def _aes_encrypt_cbc192(self, data, aes_key):
        """AES-192-CBC加密（对应Go: utils.Aes加密_cbc192密匙字节数组）"""
        if len(aes_key) != 24:
            return b""

        # 使用全零IV（对应Go实现）
        iv = b'\x00' * 16

        # PKCS7 Padding
        data_bytes = data.encode('utf-8') if isinstance(data, str) else data
        padded_data = pad(data_bytes, AES.block_size)

        # AES-192-CBC加密
        cipher = AES.new(aes_key, AES.MODE_CBC, iv)
        encrypted = cipher.encrypt(padded_data)

        return encrypted

    def _aes_decrypt_cbc192(self, encrypted_data, aes_key):
        """AES-192-CBC解密（对应Go: utils.Aes解密_cbc192字节集）"""
        if len(aes_key) != 24 or len(encrypted_data) < 16:
            return ""

        # 使用全零IV
        iv = b'\x00' * 16

        # AES-192-CBC解密
        cipher = AES.new(aes_key, AES.MODE_CBC, iv)
        decrypted = cipher.decrypt(encrypted_data)

        # 去除PKCS7 Padding
        try:
            unpadded = unpad(decrypted, AES.block_size)
            return unpadded.decode('utf-8')
        except Exception as e:
            print(f"AES解密失败: {e}")
            return ""

    def _rsa_encrypt_pkcs1v15(self, data):
        """RSA-PKCS1v15加密（对应Go: k.rsa公钥加密）"""
        if not self.rsa_public_key:
            return ""

        try:
            cipher = PKCS1_v1_5.new(self.rsa_public_key)
            encrypted = cipher.encrypt(data)
            return base64.b64encode(encrypted).decode('utf-8')
        except Exception as e:
            print(f"RSA加密失败: {e}")
            return ""

    def _rsa_public_decrypt(self, encrypted_data):
        """RSA公钥解密（对应Go: k.RSA公钥解密和unLeftPad）
        这是一个特殊操作，用于解密服务器用私钥加密的数据
        """
        if not self.rsa_public_key:
            return b""

        try:
            # 将加密数据转换为大整数
            c = int.from_bytes(encrypted_data, 'big')

            # 使用公钥指数进行解密（实际是验证签名的逆过程）
            # m = c^e mod n
            e = self.rsa_public_key.e
            n = self.rsa_public_key.n
            m = pow(c, e, n)

            # 将结果转换回字节
            key_size = (self.rsa_public_key.size_in_bits() + 7) // 8
            decrypted_bytes = m.to_bytes(key_size, 'big')

            print(f"🔍 RSA解密原始数据: {decrypted_bytes.hex()[:50]}...")

            # 使用Go SDK的unLeftPad算法
            # func unLeftPad(input []byte) (out []byte)
            n = len(decrypted_bytes)
            if n < 3:
                return b""

            t = 2
            for i in range(2, n):
                if decrypted_bytes[i] == 0xff:
                    t = t + 1
                else:
                    if decrypted_bytes[i] == decrypted_bytes[0]:
                        t = t + int(decrypted_bytes[1])
                    break

            if t >= n:
                return b""

            # 返回去除填充后的数据
            result = decrypted_bytes[t:]
            print(f"🔍 unLeftPad结果: {result.hex()}")

            return result

        except Exception as e:
            print(f"RSA公钥解密失败: {e}")
            import traceback
            traceback.print_exc()
            return b""

    def _md5_hash(self, text):
        """MD5哈希（对应Go: utils.Md5String）"""
        return hashlib.md5(text.encode('utf-8')).hexdigest()

    def _encrypt_and_sign(self, post_json):
        """加密并签名（对应Go: k.加密并签名）"""
        # 添加公共变量
        post_json["Time"] = int(time.time())
        post_json["Status"] = random.randint(10000, 99999)

        api_name = post_json.get("Api", "")

        # 序列化JSON
        json_data = json.dumps(post_json, separators=(',', ':'), ensure_ascii=False)

        if self.crypto_type == 1:
            # 明文模式
            return json_data

        # 检查是否需要强制RSA加密
        use_rsa = (self.crypto_type == 3 and api_name in self.force_rsa_apis)

        # 显示加密选择逻辑
        if self.crypto_type == 3:
            if use_rsa:
                print(f"🔐 接口 {api_name} 使用强制RSA+AES混合加密")
            else:
                print(f"🔐 接口 {api_name} 使用普通AES加密（性能优化）")

        if use_rsa:
            # RSA模式：生成随机AES密钥
            random_aes_key = self._generate_random_string(24).encode('utf-8')

            # AES加密数据
            encrypted_bytes = self._aes_encrypt_cbc192(json_data, random_aes_key)
            encrypted_data = base64.b64encode(encrypted_bytes).decode('utf-8')

            # RSA加密AES密钥
            encrypted_key = self._rsa_encrypt_pkcs1v15(random_aes_key)

            # 返回加密格式（对应Go: fmt.Sprintf(`{"a":"%s","b":"%s"}`, 局_密文, 局_签名)）
            return json.dumps({"a": encrypted_data, "b": encrypted_key}, separators=(',', ':'))
        else:
            # 普通AES加密
            encrypted_bytes = self._aes_encrypt_cbc192(json_data, self.aes_key)
            encrypted_data = base64.b64encode(encrypted_bytes).decode('utf-8')

            # MD5签名（对应Go: utils.Md5String(局_密文 + string(k.J_CryptoKeyAes))）
            signature = self._md5_hash(encrypted_data + self.aes_key.decode('utf-8'))

            return json.dumps({"a": encrypted_data, "b": signature}, separators=(',', ':'))

    def _send_and_decrypt(self, post_data):
        """发送请求并解密响应（对应Go: k.发包并返回解密）"""
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Token": self.token,
            "User-Agent": "FnKuaiYan-Python-Client/1.0"
        }

        # 重试3次（对应Go实现）
        for i in range(3):
            try:
                response = requests.post(
                    self.url,
                    data=post_data,
                    headers=headers,
                    timeout=60
                )

                response_text = response.text
                if response_text:
                    break

                print(f"通讯异常容错计次: {i}")
                time.sleep(2)

            except Exception as e:
                print(f"请求异常 {i}: {e}")
                if i == 2:
                    self.error_message = "请求失败,可能电脑时间不准确或无网络连接"
                    self.error_code = 0
                    return ""
                time.sleep(2)
        else:
            self.error_message = "请求失败,可能电脑时间不准确或无网络连接"
            self.error_code = 0
            return ""

        print(f"📥 收到响应: {response_text[:200]}...")

        # 解析响应
        try:
            response_json = json.loads(response_text)
        except json.JSONDecodeError:
            self.error_message = response_text
            self.error_code = 0
            return ""

        # 检查是否是明文响应（对应Go: if 响应.GetInt("Time") > 0）
        if "Time" in response_json and response_json.get("Time", 0) > 0:
            print("📄 收到明文响应")
            return response_text

        # 检查是否返回错误信息
        if "a" not in response_json:
            # 可能是错误响应，直接返回
            print("⚠️ 非标准加密响应格式")
            return response_text

        # 加密响应解密
        encrypted_data = response_json.get("a", "")
        signature_or_key = response_json.get("b", "")

        print(f"🔓 解密加密响应: a长度={len(encrypted_data)}, b长度={len(signature_or_key)}")

        if self.crypto_type == 3:
            # RSA模式
            if len(signature_or_key) > 32:
                # 这是加密的AES密钥，使用RSA公钥解密
                try:
                    encrypted_key_bytes = base64.b64decode(signature_or_key)
                    decrypted_aes_key = self._rsa_public_decrypt(encrypted_key_bytes)

                    if len(decrypted_aes_key) == 24:
                        aes_key = decrypted_aes_key
                        print(f"✅ RSA公钥解密AES密钥成功: {len(aes_key)}字节")
                    else:
                        print(f"❌ RSA公钥解密失败: 密钥长度{len(decrypted_aes_key)}不正确")
                        self.error_message = "RSA解密AES密钥失败"
                        self.error_code = 0
                        return ""
                except Exception as e:
                    print(f"❌ RSA解密异常: {e}")
                    self.error_message = "RSA解密失败"
                    self.error_code = 0
                    return ""
            else:
                # 这是MD5签名
                expected_signature = self._md5_hash(encrypted_data + self.aes_key.decode('utf-8'))
                if expected_signature.upper() != signature_or_key.upper():
                    print(f"❌ 签名验证失败: 期望={expected_signature}, 实际={signature_or_key}")
                    self.error_message = "验签不通过"
                    self.error_code = 0
                    return ""
                aes_key = self.aes_key
        else:
            aes_key = self.aes_key

        # AES解密
        try:
            encrypted_bytes = base64.b64decode(encrypted_data)
            decrypted_text = self._aes_decrypt_cbc192(encrypted_bytes, aes_key)
            print(f"✅ 解密成功: {decrypted_text[:100]}...")
            return decrypted_text
        except Exception as e:
            print(f"❌ 解密失败: {e}")
            return ""

    def _communicate(self, post_json):
        """通讯方法（对应Go: k.通讯）"""
        encrypted_data = self._encrypt_and_sign(post_json)
        print(f"📤 发送{post_json.get('Api', 'Unknown')}请求")
        print(f"   加密数据长度: {len(encrypted_data)}")

        response_text = self._send_and_decrypt(encrypted_data)

        if not response_text:
            return None, False

        try:
            response_json = json.loads(response_text)

            # 验证响应（对应Go: k.X响应校验时间状态）
            if not self._validate_response(post_json, response_json):
                return None, False

            return response_json, True

        except json.JSONDecodeError as e:
            print(f"响应解析失败: {e}")
            self.error_message = "响应解析失败"
            self.error_code = 0
            return None, False

    def _validate_response(self, request_json, response_json):
        """验证响应时间和状态（对应Go: k.X响应校验时间状态）"""
        if request_json["Status"] != response_json.get("Status"):
            self.error_code = response_json.get("Status", 0)
            self.error_message = response_json.get("Msg", "")

            # 特殊处理Token已注销(109)的情况
            if self.error_code == 109:
                data = response_json.get("Data", {})
                logout_code = data.get("LogoutCode", -1)
                logout_reasons = {
                    0: "心跳超时自动注销",
                    1: "超过同时在线注销",
                    2: "管理员手动注销(含在线踢出,冻结用户注销)",
                    3: "用户自己注销",
                    4: "远程注销"
                }

                if logout_code in logout_reasons:
                    self.error_message = f"Token已注销: {logout_reasons[logout_code]}"
                else:
                    self.error_message = f"Token已注销: 注销原因未知({logout_code})"

            return False

        # 时间差验证（最多30分钟）
        time_diff = request_json["Time"] - response_json.get("Time", 0)
        if abs(time_diff) > 1800:
            if not response_json.get("Msg"):
                self.error_message = "封包时间异常"
                self.error_code = 107
            else:
                self.error_code = response_json.get("Status", 0)
                self.error_message = response_json.get("Msg", "")
            return False

        return True

    def get_token(self):
        """获取Token（对应Go: k.Q取Token）"""
        request_json = {"Api": "GetToken"}

        # RSA模式需要发送AES密钥
        if self.crypto_type == 3:
            request_json["Key"] = self.aes_key.decode('utf-8')

        self.token = ""
        response_json, success = self._communicate(request_json)

        if not success:
            return "获取Token失败"

        # 解析Token
        data = response_json.get("Data", {})
        self.token = data.get("Token", "")

        # 更新AES密钥（RSA模式）
        if self.crypto_type == 3:
            new_aes_key = data.get("CryptoKeyAes", "")
            if new_aes_key:
                self.aes_key = new_aes_key.encode('utf-8')

        if not self.token:
            self.error_message = "获取到Token错误"
            return "获取到Token错误"

        print(f"✅ Token获取成功: {self.token[:10]}...")
        return "ok"

    def card_login(self, card_number, app_version="1.0.0"):
        """卡密登录（对应Go: k.D登录_通用）"""
        machine_code = self.get_machine_code()

        request_json = {
            "Api": "UserLogin",
            "UserOrKa": card_number,
            "PassWord": "",  # 卡密模式密码为空
            "Key": machine_code,
            "Tab": f"Python-Client-{socket.gethostname()}",
            "AppVer": app_version  # 使用传递的版本号
        }

        response_json, success = self._communicate(request_json)

        if not success:
            return f"登录失败: {self.error_message}"

        # 解析登录结果
        data = response_json.get("Data", {})
        vip_time = data.get("VipTime", 0)

        if vip_time > 0:
            expiry_date = datetime.datetime.fromtimestamp(vip_time).strftime('%Y-%m-%d %H:%M:%S')
            return f"ok|{expiry_date}"
        else:
            return "登录失败: 无效的卡密或已过期"

    def get_announcement(self):
        """获取公告"""
        request_json = {"Api": "GetAppGongGao"}

        response_json, success = self._communicate(request_json)

        if not success:
            return f"获取公告失败: {self.error_message}"

        # 获取公告内容并处理换行符
        data = response_json.get("Data", {})
        announcement = data.get("AppGongGao", "暂无公告")

        # 将\n转换为实际换行符
        if isinstance(announcement, str):
            announcement = announcement.replace('\\n', '\n')

        return announcement

    def set_user_config(self, name, value):
        """设置用户云配置"""
        if not self.token:
            return "未登录，无法设置配置"

        request_json = {
            "Api": "SetUserConfig",
            "Name": name,
            "Value": value
        }

        response_json, success = self._communicate(request_json)

        if not success:
            return f"设置配置失败: {self.error_message}"

        return "ok"

    def get_user_config(self, name):
        """获取用户云配置"""
        if not self.token:
            return ""

        request_json = {
            "Api": "GetUserConfig",
            "Name": name
        }

        response_json, success = self._communicate(request_json)

        if not success:
            return ""

        # 获取配置值
        data = response_json.get("Data", {})
        return data.get(name, "")

    def set_new_binding(self, new_key, user="", password=""):
        """设置新的绑定信息（换绑）"""
        if not self.token:
            return "未登录，无法换绑"

        request_json = {
            "Api": "SetAppUserKey",
            "NewKey": new_key,
            "User": user,
            "PassWord": password
        }

        response_json, success = self._communicate(request_json)

        if not success:
            return f"换绑失败: {self.error_message}"

        # 获取扣除的时间或点数
        data = response_json.get("Data", {})
        reduce_time = data.get("ReduceVipTime", 0)
        return f"ok|{reduce_time}"

    def get_purchase_url(self):
        """获取购卡地址"""
        request_json = {"Api": "GetPublicData", "Key": "购卡地址"}

        response_json, success = self._communicate(request_json)

        if not success:
            return f"获取购卡地址失败: {self.error_message}"

        return response_json.get("Data", "")

    def heartbeat(self):
        """心跳检测（对应Go: k.X心跳）
        更新心跳,并获取当前状态,如果是Token已注销,可以看状态码,注销原因
        """
        # 检查Token是否有效
        if not self.token:
            return "心跳失败: 未获取Token，请先登录"

        try:
            request_json = {"Api": "HeartBeat"}

            response_json, success = self._communicate(request_json)

            if not success:
                # 检查是否是Token注销相关错误
                if self.error_code > 0:
                    # 根据官方状态码文档判断错误原因
                    error_reasons = {
                        100: "系统已关闭",
                        101: "App不存在",
                        102: "Api不存在",
                        103: "签名错误",
                        104: "参数错误",
                        105: "加解密失败",
                        106: "Token无效",
                        107: "封包超时",
                        108: "状态码错误",
                        109: "Token已注销",
                        110: "已停止运营",
                        111: "验证码错误",
                        200: "操作失败",
                        201: "SQL错误",
                        202: "登录失败,登录状态失效,TOKEN错误",
                        203: "版本不可用",
                        204: "VIP已到期",
                        205: "绑定信息验证失败",
                        206: "绑定信息已被其他用户使用",
                        207: "已冻结无法登录",
                        208: "同时在线超过最大值",
                        210: "未登录",
                        211: "黑名单",
                        212: "唯一标识重复",
                        213: "积分不足"
                    }

                    error_reason = error_reasons.get(self.error_code, f"未知错误({self.error_code})")

                    # 特殊处理Token已注销的情况
                    if self.error_code == 109:
                        # Token已注销，检查注销原因
                        logout_codes = {
                            0: "心跳超时自动注销",
                            1: "超过同时在线注销",
                            2: "管理员手动注销(含在线踢出,冻结用户注销)",
                            3: "用户自己注销",
                            4: "远程注销"
                        }
                        # 这里暂时无法获取LogoutCode，后续可以从响应Data中解析
                        return f"心跳失败: Token已注销 - {self.error_message}"

                    return f"心跳失败: {error_reason} - {self.error_message}"

                # 通用错误处理
                if "Token" in self.error_message or "token" in self.error_message.lower():
                    return "心跳失败: Token无效，请重新登录"

                return f"心跳失败: {self.error_message}"

            # 解析心跳结果
            data = response_json.get("Data", {})
            status = data.get("Status", 0)

            # 根据官方文档处理状态
            if status == 1:
                return "ok|正常状态"
            elif status == 3:
                return "ok|会员已到期(免费模式即使到期了也不会返回3)"
            else:
                return f"ok|未知状态({status})"

        except Exception as e:
            return f"心跳异常: {str(e)}"

    def user_logout(self):
        """用户登录注销"""
        if not self.token:
            return "未登录，无法注销"

        request_json = {
            "Api": "LogOut"
        }

        # 发送注销请求
        response_json, success = self._communicate(request_json)

        if not success:
            return f"注销失败: {self.error_message}"

        return "ok"


# 全局API实例（兼容原有代码）
api = None


def initialization(config=None):
    """初始化API"""
    global api
    try:
        api = FnKuaiYanGoBasedAPI(config)
        result = api.get_token()
        return result
    except Exception as e:
        return f"初始化失败: {str(e)}"


def login(card_number):
    """卡密登录"""
    if api is None:
        return "API未初始化"
    return api.card_login(card_number)


def pay(key):
    """获取公共数据"""
    if api is None:
        return "API未初始化"

    if key == "公告":
        return api.get_announcement()
    elif key == "购卡地址":
        return api.get_purchase_url()
    else:
        return f"不支持的键: {key}"


def HeartBeat():
    """心跳检测"""
    if api is None:
        return "API未初始化"
    return api.heartbeat()


if __name__ == "__main__":
    # 测试代码
    print("🚀 飞鸟快验Go SDK风格Python实现测试")

    # 初始化
    result = initialization()
    print(f"初始化结果: {result}")

    if result == "ok":
        # 测试获取公告
        announcement = pay("公告")
        print(f"公告: {announcement}")

        # 测试心跳
        heartbeat_result = HeartBeat()
        print(f"心跳: {heartbeat_result}")

        # 测试购卡地址
        purchase_url = pay("购卡地址")
        print(f"购卡地址: {purchase_url}")
