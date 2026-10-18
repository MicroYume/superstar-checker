"""超星字体反爬（cxsecret）解码。

方案来自 SocialSisterYi / Samueli924 的开源实现（Apache-2.0）：
题目页内嵌 base64 TTF 子集字体（font-family: font-cxsecret），题干/选项里的字符
被替换为生僻码点，需按"字形轮廓哈希 -> 标准字符"映射表还原。
"""

import base64
import hashlib
import json
from io import BytesIO
from pathlib import Path

from fontTools.ttLib.ttFont import TTFont

_DATA_FILE = Path(__file__).parent / "data" / "font_map_table.json"

# 康熙部首替换表（U+2F00 区部首字形 -> 常用汉字）
KX_RADICALS_TAB = str.maketrans(
    "⼀⼁⼂⼃⼄⼅⼆⼇⼈⼉⼊⼋⼌⼍⼎⼏⼐⼑⼒⼓⼔⼕⼖⼗⼘⼙⼚⼛⼜⼝⼞⼟⼠⼡⼢⼣⼤⼥⼦⼧⼨⼩⼪⼫⼬⼭⼮⼯⼰⼱⼲⼳⼴⼵⼶⼷⼸⼹⼺⼻⼼⼽⼾⼿⽀⽁⽂⽃⽄⽅⽆⽇⽈⽉⽊⽋⽌⽍⽎⽏⽐⽑⽒⽓⽔⽕⽖⽗⽘⽙⽚⽛⽜⽝⽞⽟⽠⽡⽢⽣⽤⽥⽦⽧⽨⽩⽪⽫⽬⽭⽮⽯⽰⽱⽲⽳⽴⽵⽶⽷⽸⽹⽺⽻⽼⽽⽾⽿⾀⾁⾂⾃⾄⾅⾆⾇⾈⾉⾊⾋⾌⾍⾎⾏⾐⾑⾒⾓⾔⾕⾖⾗⾘⾙⾚⾛⾜⾝⾞⾟⾠⾡⾢⾣⾤⾥⾦⾧⾨⾩⾪⾫⾬⾭⾮⾯⾰⾱⾲⾳⾴⾵⾶⾷⾸⾹⾺⾻⾼髙⾽⾾⾿⿀⿁⿂⿃⿄⿅⿆⿇⿈⿉⿊⿋⿌⿍⿎⿏⿐⿑⿒⿓⿔⿕⺠⻬⻩⻢⻜⻅⺟⻓",
    "一丨丶丿乙亅二亠人儿入八冂冖冫几凵刀力勹匕匚匸十卜卩厂厶又口囗土士夂夊夕大女子宀寸小尢尸屮山巛工己巾干幺广廴廾弋弓彐彡彳心戈戶手支攴文斗斤方无日曰月木欠止歹殳毋比毛氏气水火爪父爻爿片牙牛犬玄玉瓜瓦甘生用田疋疒癶白皮皿目矛矢石示禸禾穴立竹米糸缶网羊羽老而耒耳聿肉臣自至臼舌舛舟艮色艸虍虫血行衣襾見角言谷豆豕豸貝赤走足身車辛辰辵邑酉采里金長門阜隶隹雨青非面革韋韭音頁風飛食首香馬骨高高髟鬥鬯鬲鬼魚鳥鹵鹿麥麻黃黍黑黹黽鼎鼓鼠鼻齊齒龍龜龠民齐黄马飞见母长",
)

# hash -> "uniXXXX"
_hash_map: dict[str, str] | None = None


def _load_hash_map() -> dict[str, str]:
    global _hash_map
    if _hash_map is None:
        try:
            char_map = json.loads(_DATA_FILE.read_text(encoding="utf-8"))
            _hash_map = {h: c for c, h in char_map.items()}
        except (OSError, ValueError):
            _hash_map = {}
    return _hash_map


def _hash_glyph(glyph) -> str:
    if glyph.numberOfContours <= 0:
        return ""
    pos_data = []
    last_index = 0
    for i in range(glyph.numberOfContours):
        end_point = glyph.endPtsOfContours[i]
        for j in range(last_index, end_point + 1):
            x, y = glyph.coordinates[j]
            flag = glyph.flags[j] & 0x01
            pos_data.append(f"{x}{y}{flag}")
        last_index = end_point + 1
    return hashlib.md5("".join(pos_data).encode()).hexdigest()


def _font_to_map(font_data) -> dict[str, str]:
    """内嵌 base64 字体 -> {字形名("uniXXXX"): 轮廓哈希}。"""
    if isinstance(font_data, str):
        font_data = BytesIO(base64.b64decode(font_data.split("base64,", 1)[1]))
    hashmap = {}
    with TTFont(font_data, lazy=False) as font_file:
        table = font_file["glyf"]
        for name in table.glyphOrder:
            if name.startswith("uni"):
                glyph_hash = _hash_glyph(table.glyphs[name])
                if glyph_hash:
                    hashmap[name] = glyph_hash
    return hashmap


def decrypt(dst_fontmap: dict[str, str], encrypted_text: str) -> str:
    """按字形哈希还原加密文本；查不到的字符原样保留。"""
    hash_map = _load_hash_map()
    result = []
    for char in encrypted_text:
        char_code = f"uni{ord(char):X}"
        dst_hash = dst_fontmap.get(char_code)
        original = hash_map.get(dst_hash) if dst_hash else None
        if original:
            try:
                result.append(chr(int(original[3:], 16)))
                continue
            except (ValueError, IndexError):
                pass
        result.append(char)
    return "".join(result).translate(KX_RADICALS_TAB)


def is_encrypted_page(html: str) -> bool:
    return "font-cxsecret" in html


def extract_embedded_font(html: str) -> str | None:
    """提取页面 @font-face 里的 base64 字体数据。"""
    import re
    match = re.search(r"src:\s*url\('(data:application/font-ttf[^']+)'\)", html)
    return match.group(1) if match else None
