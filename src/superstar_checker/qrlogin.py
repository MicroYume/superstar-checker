"""超星扫码登录：纯 HTTP 走完 passport2 的二维码流程，会话交给 ChaoxingClient 落盘。

协议（2026-09-28 真机逐接口验证，超星无公开文档，改版可能失效）：

  GET  /login?newversion=true     页面隐藏域里带 uuid + enc
  GET  /createqr?uuid=..&fid=-1   取二维码图片；这一步同时"激活"该 uuid —— 漏掉它，
                                  下一步永远返回 type:2 二维码已失效
  POST /getauthstatus/v2          3 秒轮一次，官方前端上限 50 次（约 150 秒）。
                                  status:true 那次响应的 Set-Cookie 就是完整会话，
                                  不需要再跟随 space/index 跳转
  POST /refreshQRCode             换新码；换过之后仍要再 GET 一次 /createqr

二维码显示走本地 HTML（浏览器渲染比 cmd 字符可靠），由本模块反复重写该文件、
页面自带 2 秒 meta 刷新 —— file:// 页面自己去 fetch passport2 会被 CORS 挡，
所以轮询必须留在 Python 侧。
"""

import base64
import os
import re
import struct
import sys
import time
import webbrowser
import zlib

from bs4 import BeautifulSoup

from . import session_store
from .client import ChaoxingError

BASE = "https://passport2.chaoxing.com"
QR_IMAGE = f"{BASE}/createqr"
POLL_STATUS = f"{BASE}/getauthstatus/v2"
REFRESH = f"{BASE}/refreshQRCode"

POLL_INTERVAL = 3.0        # 与官方前端一致
POLLS_PER_CODE = 50        # 3s x 50 ≈ 150s，即一张码的寿命
MAX_CODES = 4              # 过期自动换码，最多 4 张（约 10 分钟窗口）

PAGE_FILE = session_store.STORE_DIR / "qr_login.html"

WAITING, SCANNED = "等待扫码", "已扫码 — 请在手机上点击确认"
DEAD_TYPE_TEXT = {"2": "二维码已失效", "6": "手机上取消了登录", "7": "登录被拒绝"}
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

_STYLE = """
body{background:#12141a;color:#e8eaf0;font:15px/1.6 "Segoe UI",system-ui,sans-serif;
     margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:30px}
.wrap{text-align:center}h1{font-size:18px;font-weight:600;margin:0 0 6px}
.pill{display:inline-block;padding:5px 14px;border-radius:99px;font-size:14px;
      background:#2a2f3c;color:#b9c1d4;margin:10px 0 18px}
.pill.waiting{background:#1d3a5f;color:#8ec5ff}.pill.scanned{background:#3d3413;color:#ffd479}
.pill.success{background:#153d24;color:#7ef0a5}.pill.dead{background:#4a1d22;color:#ff9aa2}
.qr{background:#fff;padding:20px;border-radius:12px;display:inline-block;line-height:0}
.qr img{image-rendering:pixelated;width:360px;height:360px;display:block}
.hint{color:#6f7a90;font-size:13px;margin-top:14px}
"""


def _paint(png: bytes | None, text: str, tone: str, hint: str) -> None:
    """重写二维码页面。先写 .tmp 再 os.replace（避免页面 2s 自刷新时读到半个文件）。

    坑：浏览器带着 meta refresh 打开该文件时，Windows 可能拒绝 rename —— 那属于显示层
    问题，绝不能把登录流程打断，所以退化成原地覆盖写，再失败就这次不更新。
    """
    session_store.STORE_DIR.mkdir(parents=True, exist_ok=True)
    box = (f'<img src="data:image/png;base64,{base64.b64encode(png).decode()}" alt="二维码">'
           if png else '<div style="width:360px;height:360px;display:flex;align-items:'
                       'center;justify-content:center;color:#666">—</div>')
    html = ('<!doctype html><html lang="zh"><head><meta charset="utf-8">'
            '<meta http-equiv="refresh" content="2">'
            f'<title>超星扫码登录</title><style>{_STYLE}</style></head><body>'
            '<div class="wrap"><h1>用学习通 App「扫一扫」登录</h1>'
            f'<div class="pill {tone}">{text}</div><div class="qr">{box}</div>'
            f'<div class="hint">{hint}</div></div></body></html>')
    tmp = PAGE_FILE.with_name("qr_login.tmp")
    try:
        tmp.write_text(html, encoding="utf-8")
        os.replace(tmp, PAGE_FILE)
    except OSError:
        try:
            PAGE_FILE.write_text(html, encoding="utf-8")
        except OSError:
            pass


def _new_code(client, uuid: str) -> bytes:
    """取二维码图片 —— 同时也是激活这个 uuid 的唯一途径。"""
    resp = client.request("GET", QR_IMAGE, params={"uuid": uuid, "fid": "-1"})
    if resp.status_code != 200 or not resp.content:
        raise ChaoxingError("二维码接口没返回图片，稍后重试或改用密码登录")
    if not resp.content.startswith(PNG_SIGNATURE):
        raise ChaoxingError("二维码接口返回了错误信息，不是有效图片；请稍后重试")
    return resp.content


def _poll(client, uuid: str, enc: str) -> dict:
    resp = client.request("POST", POLL_STATUS,
                          data={"enc": enc, "uuid": uuid, "doubleFactorLogin": "0",
                                "forbidotherlogin": "0"},
                          headers={"X-Requested-With": "XMLHttpRequest",
                                   "Referer": client.LOGIN_PAGE})
    if resp.status_code != 200:
        raise ChaoxingError(f"扫码状态接口异常（HTTP {resp.status_code}）")
    try:
        return resp.json()
    except ValueError:
        raise ChaoxingError(f"扫码状态接口返回非 JSON（HTTP {resp.status_code}）") from None


def _png_to_grid(png: bytes) -> list[list[bool]]:
    """解码超星返回的无隔行 PNG，返回 True=黑色模块的二维网格。"""
    if not png.startswith(PNG_SIGNATURE):
        raise ChaoxingError("二维码不是有效 PNG 图片")
    pos, width, height, bit_depth, color_type, interlace = 8, 0, 0, 0, 0, 0
    compressed = bytearray()
    while pos + 8 <= len(png):
        length = struct.unpack(">I", png[pos:pos + 4])[0]
        kind = png[pos + 4:pos + 8]
        chunk = png[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            width, height, bit_depth, color_type, _, _, interlace = struct.unpack(
                ">IIBBBBB", chunk)
        elif kind == b"IDAT":
            compressed.extend(chunk)
        elif kind == b"IEND":
            break
    if not width or not height or interlace:
        raise ChaoxingError("二维码 PNG 格式不受支持")
    channels = {0: 1, 2: 3, 4: 2, 6: 4}.get(color_type)
    if channels is None or bit_depth not in (1, 2, 4, 8):
        raise ChaoxingError("二维码 PNG 色彩格式不受支持")
    row_bytes = (width * channels * bit_depth + 7) // 8
    bpp = max(1, (channels * bit_depth + 7) // 8)
    raw = zlib.decompress(bytes(compressed))
    expected = height * (row_bytes + 1)
    if len(raw) < expected:
        raise ChaoxingError("二维码 PNG 数据不完整")
    rows, offset = [], 0
    for _ in range(height):
        filter_type = raw[offset]
        source = raw[offset + 1:offset + 1 + row_bytes]
        offset += row_bytes + 1
        row = bytearray(row_bytes)
        for i, value in enumerate(source):
            left = row[i - bpp] if i >= bpp else 0
            up = rows[-1][i] if rows else 0
            up_left = rows[-1][i - bpp] if rows and i >= bpp else 0
            if filter_type == 0:
                row[i] = value
            elif filter_type == 1:
                row[i] = (value + left) & 0xff
            elif filter_type == 2:
                row[i] = (value + up) & 0xff
            elif filter_type == 3:
                row[i] = (value + ((left + up) // 2)) & 0xff
            elif filter_type == 4:
                estimate = left + up - up_left
                distances = (abs(estimate - left), abs(estimate - up),
                             abs(estimate - up_left))
                predictor = (left, up, up_left)[distances.index(min(distances))]
                row[i] = (value + predictor) & 0xff
            else:
                raise ChaoxingError(f"二维码 PNG 使用未知滤镜 {filter_type}")
        rows.append(row)

    grid = []
    mask = (1 << bit_depth) - 1
    for row in rows:
        pixels = []
        if color_type == 0:
            for x in range(width):
                shift = 8 - bit_depth - (x % (8 // bit_depth)) * bit_depth
                sample = (row[x // (8 // bit_depth)] >> shift) & mask
                pixels.append(sample < mask / 2)
        elif color_type == 4:
            for x in range(width):
                gray = row[x * 2]
                pixels.append(gray < 128)
        else:
            step = channels
            for x in range(width):
                pixels.append(sum(row[x * step:x * step + 3]) / 3 < 128)
        grid.append(pixels)
    # 超星的 270px 图片通常是 53x53 个模块、每个模块 5px，先压缩成真正的
    # QR 模块网格，否则终端会画出 270 列，既慢又难扫。
    points = [(x, y) for y, row in enumerate(grid) for x, value in enumerate(row) if value]
    if points:
        min_x, max_x = min(x for x, _ in points), max(x for x, _ in points)
        min_y, max_y = min(y for _, y in points), max(y for _, y in points)
        box_w, box_h = max_x - min_x + 1, max_y - min_y + 1
        if box_w == box_h:
            # Prefer the coarsest valid scale.  A 105px image is both a
            # valid 105-module grid and a 21-module QR enlarged 5x; the
            # latter is what the server actually returns.
            for module_size in range(min(20, box_w), 0, -1):
                module_count = box_w // module_size
                if (box_w % module_size == 0 and 21 <= module_count <= 177
                        and module_count % 4 == 1):
                    collapsed = []
                    for y in range(module_count):
                        row = []
                        for x in range(module_count):
                            cells = [grid[min_y + y * module_size + yy]
                                     [min_x + x * module_size + xx]
                                     for yy in range(module_size)
                                     for xx in range(module_size)]
                            row.append(sum(cells) * 2 >= len(cells))
                        collapsed.append(row)
                    return collapsed
    return grid


def render_terminal_qr(png: bytes, stream=None, half_block: bool = True) -> None:
    """在终端绘制二维码回退；half_block=False 时使用 ASCII。"""
    stream = stream or sys.stdout
    grid = _png_to_grid(png)
    if grid:
        quiet = 4
        blank = [False] * (len(grid[0]) + quiet * 2)
        grid = ([blank[:] for _ in range(quiet)] +
                [[False] * quiet + row + [False] * quiet for row in grid] +
                [blank[:] for _ in range(quiet)])
    if not half_block:
        for row in grid:
            stream.write("".join("##" if value else "  " for value in row) + "\n")
        stream.flush()
        return
    # 半块字符把两行模块压成一个字符，终端中接近方形模块。
    for y in range(0, len(grid), 2):
        line = []
        for x in range(len(grid[y])):
            upper = grid[y][x]
            lower = grid[y + 1][x] if y + 1 < len(grid) else False
            if upper and lower:
                line.append("\x1b[30;40m ")
            elif upper:
                line.append("\x1b[30;47m▀")
            elif lower:
                line.append("\x1b[37;40m▄")
            else:
                line.append("\x1b[37;47m ")
        stream.write("".join(line) + "\x1b[0m\n")
    stream.flush()


def _terminal_state(png: bytes | None, text: str, hint: str,
                    half_block: bool = False, native_window=None) -> None:
    if native_window is not None:
        native_window.update(png, text, hint)
        return
    if png:
        render_terminal_qr(png, half_block=half_block)
    print(f"\n{text}\n{hint}\n")


def _clear_page(text: str = "扫码登录已结束") -> None:
    try:
        _paint(None, text, "success" if "成功" in text else "dead", "")
    except OSError:
        pass


def _remove_page() -> None:
    try:
        PAGE_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def login_with_qr(client, log=print, rounds: int = MAX_CODES,
                  polls_per_code: int = POLLS_PER_CODE,
                  open_browser: bool = True, terminal: bool = False,
                  half_block: bool | None = None) -> dict:
    """扫码登录直到成功或换码次数用尽。成功走 finalize_login，与密码登录同一收尾。"""
    native_window = None
    try:
        page = client.reset_for_login()
        soup = BeautifulSoup(page.text, "html.parser")
        uuid = (soup.find("input", id="uuid") or {}).get("value", "")
        enc = (soup.find("input", id="enc") or {}).get("value", "")
        if not (re.fullmatch(r"[0-9a-fA-F]{32}", uuid or "") and
                re.fullmatch(r"[0-9a-fA-F]{32}", enc or "")):
            raise ChaoxingError("登录页结构变了，取不到 uuid/enc —— 请改用密码登录")

        for rnd in range(1, rounds + 1):
            png = _new_code(client, uuid)
            hint = f"第 {rnd}/{rounds} 张码 · 每张约 {int(polls_per_code * POLL_INTERVAL)}s，过期自动换下一张"
            log(f"[码 {rnd}/{rounds}] " +
                ("打开原生二维码窗口" if terminal and half_block is None
                 else f"二维码页面：{PAGE_FILE}"))
            if terminal:
                if half_block is None:
                    try:
                        from .qrwindow import NativeQrWindow
                        if native_window is None:
                            native_window = NativeQrWindow(png, WAITING, hint)
                        else:
                            native_window.update(png, WAITING, hint)
                    except (ImportError, OSError) as exc:
                        raise ChaoxingError(
                            f"无法打开原生二维码窗口：{exc}。请改用 login --qr 浏览器模式") from exc
                else:
                    _terminal_state(png, WAITING, hint, half_block)
            else:
                _paint(png, WAITING, "waiting", hint)
            if rnd == 1 and open_browser and not terminal:
                webbrowser.open(PAGE_FILE.as_uri())
                log("             已尝试在浏览器打开，请对屏幕扫码并在手机上点确认")

            terminal_status = WAITING
            for i in range(1, polls_per_code + 1):
                if native_window is not None:
                    if native_window.closed.wait(POLL_INTERVAL):
                        raise ChaoxingError("二维码窗口已关闭，扫码登录已取消")
                else:
                    time.sleep(POLL_INTERVAL)
                left = f"{hint} · 本码约剩 {int((polls_per_code - i) * POLL_INTERVAL)}s"
                if native_window is not None:
                    native_window.update(None, terminal_status, left)
                body = _poll(client, uuid, enc)
                code = str(body.get("type", ""))
                if body.get("status") is True or str(body.get("status", "")).lower() == "true":
                    if body.get("containTwoFactorLogin"):
                        if terminal:
                            _terminal_state(None, "该账号需要二次验证", "请改用密码登录",
                                            half_block is True, native_window)
                        else:
                            _paint(None, "该账号需要二次验证", "dead", "本工具不支持双因子，请用密码登录")
                        raise ChaoxingError("这个账号开了双因子验证，扫码走不通，请用密码登录")
                    if not terminal:
                        _paint(None, "登录成功，可以关掉这个页面了", "success", "")
                    log(f"  ← 第 {i} 次轮询：验证通过")
                    result = client.finalize_login("QR")
                    if native_window is not None:
                        native_window.update(None, "登录成功", "会话已保存，可以关闭此窗口")
                        native_window.close()
                        native_window = None
                    _remove_page()
                    return result
                if code == "4":
                    if terminal:
                        log(f"  ← 第 {i} 次轮询：已扫码，等手机确认")
                        if native_window is not None:
                            terminal_status = SCANNED
                            native_window.update(None, SCANNED, left)
                    else:
                        _paint(png, SCANNED, "scanned", left)
                    log(f"  ← 第 {i} 次轮询：已扫码，等手机确认（{body.get('nickname', '')}）")
                elif code in DEAD_TYPE_TEXT:
                    if native_window is not None:
                        native_window.update(None, DEAD_TYPE_TEXT[code], hint)
                    if not terminal:
                        _paint(None, DEAD_TYPE_TEXT[code], "dead", hint)
                    log(f"  ← 第 {i} 次轮询：{DEAD_TYPE_TEXT[code]}")
                    break
                elif code not in ("", "3"):
                    raise ChaoxingError(f"扫码状态接口返回未知状态 type={code}")
                else:
                    terminal_status = WAITING
                    if native_window is not None:
                        native_window.update(None, WAITING, left)
                    elif not terminal:
                        _paint(png, WAITING, "waiting", left)
            else:
                if terminal:
                    log(f"  ← 第 {rnd} 张码已过期，正在换码")
                    if native_window is not None:
                        native_window.update(None, "二维码已过期", "正在获取新二维码")
                else:
                    _paint(None, "二维码已过期 — 正在换新码", "dead", hint)
                log(f"  ← 第 {rnd} 张码 {int(polls_per_code * POLL_INTERVAL)}s 内没人扫，自动换码")

            if rnd < rounds:
                fresh = client.request("POST", REFRESH, headers={"Referer": client.LOGIN_PAGE})
                if fresh.status_code != 200:
                    raise ChaoxingError(f"换新二维码接口异常（HTTP {fresh.status_code}）")
                try:
                    data = fresh.json()
                    uuid, enc = data["uuid"], data["enc"]
                except (ValueError, KeyError, TypeError):
                    raise ChaoxingError("换新二维码接口异常，请重新运行一次") from None

        raise ChaoxingError("扫码未在有效期内完成，本地会话未改动")
    except Exception:
        if native_window is not None:
            native_window.close()
        client.restore_login_state()
        if not terminal:
            _clear_page("扫码登录失败，会话已恢复")
        _remove_page()
        raise
