"""登录密码加密。

超星 fanyalogin 服务端同时接受两种加密（与官方前端/安卓端一致）：
- AES-128-CBC：key = iv = "u2oh6Vu^HWe4_AES"，PKCS7，输出 Base64（当前官方前端使用）
- DES-ECB：key = "u2oh6Vu^"，PKCS7，输出小写 hex（旧版前端/安卓端）
"""

import base64

from Crypto.Cipher import AES, DES
from Crypto.Util.Padding import pad

AES_KEY = b"u2oh6Vu^HWe4_AES"
DES_KEY = b"u2oh6Vu^"


def encrypt_aes(plain: str) -> str:
    """AES-128-CBC + PKCS7，输出 Base64。"""
    cipher = AES.new(AES_KEY, AES.MODE_CBC, iv=AES_KEY)
    encrypted = cipher.encrypt(pad(plain.encode("utf-8"), AES.block_size))
    return base64.b64encode(encrypted).decode()


def encrypt_des(plain: str) -> str:
    """DES-ECB + PKCS7，输出小写 hex。"""
    cipher = DES.new(DES_KEY, DES.MODE_ECB)
    return cipher.encrypt(pad(plain.encode("utf-8"), DES.block_size)).hex()
