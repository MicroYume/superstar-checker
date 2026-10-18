from pathlib import Path
"""单元自测：加密、enc 公式、HTML 解析器。"""

from superstar_checker.crypto import encrypt_aes, encrypt_des
from superstar_checker.decode import parse_chapters, parse_marg, parse_work_list
from superstar_checker.study import video_enc


def test_enc_formula():
    e = video_enc("123", "u1", "j1", "o1", 100, 200)
    assert len(e) == 32
    import hashlib
    plain = "[123][u1][j1][o1][100000][d_yHJ!$pdA~5][200000][0_200]"
    assert e == hashlib.md5(plain.encode()).hexdigest(), "enc 公式与参考实现不一致"


def test_crypto():
    assert len(encrypt_aes("pass123")) > 0
    assert len(encrypt_des("pass123")) == 16


def test_chapters():
    html = ('<div class="chapter_unit"><li><div id="cur999"></div>'
            '<a class="clicktitle">1.1 测试</a>'
            '<input class="knowledgeJobCount" value="3">'
            '<span class="bntHoverTips">已完成</span></li></div>')
    chs = parse_chapters(html)
    assert chs and chs[0]["knowledge_id"] == "999"
    assert chs[0]["job_count"] == 3 and chs[0]["finished"] is True


def test_marg():
    html = '<script>mArg = {"attachments":[{"jobid":"j","objectId":"o"}],"defaults":{"reportUrl":"u"}};</script>'
    marg = parse_marg(html)
    assert marg and marg["attachments"][0]["jobid"] == "j"


def test_marg_preserves_spaces():
    html = ('<script>mArg = {"attachments":[{"property":{"name":"第一章 课件"}}]};'
            '</script>')
    marg = parse_marg(html)
    assert marg["attachments"][0]["property"]["name"] == "第一章 课件"


def test_notice_string_read_flag():
    from superstar_checker.notice import _parse_notice
    assert _parse_notice({"isread": "0"})["is_read"] is False
    assert _parse_notice({"isread": "1"})["is_read"] is True


def test_job_response_business_status():
    from types import SimpleNamespace
    from superstar_checker.study import _job_response_ok
    assert _job_response_ok(SimpleNamespace(status_code=200,
                                            text='{"status": true}',
                                            json=lambda: {"status": True}))
    assert not _job_response_ok(SimpleNamespace(status_code=200,
                                                text='{"status": false}',
                                                json=lambda: {"status": False}))


def test_work_caches_clear():
    from superstar_checker import work
    work._WORK_CACHE["x"] = {}
    work._LIST_CACHE["x"] = "url"
    work._QUIZ_CACHE[("x",)] = []
    work.clear_caches()
    assert not work._WORK_CACHE and not work._LIST_CACHE and not work._QUIZ_CACHE


def _test_png_1bit(rows):
    import struct
    import zlib
    width, height = len(rows[0]), len(rows)
    packed = []
    for row in rows:
        values = bytearray((len(row) + 7) // 8)
        for x, bit in enumerate(row):
            values[x // 8] |= (0 if bit else 1) << (7 - x % 8)
        packed.append(bytes([0]) + values)
    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data +
                struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff))
    return (b"\x89PNG\r\n\x1a\n" +
            chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 1, 0, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(b"".join(packed))) + chunk(b"IEND", b""))


def test_qr_png_terminal_grid():
    from io import StringIO
    from superstar_checker.qrlogin import _png_to_grid, render_terminal_qr
    png = _test_png_1bit([[True, False, True, False],
                          [False, True, True, False]])
    assert _png_to_grid(png) == [[True, False, True, False],
                                 [False, True, True, False]]
    out = StringIO()
    render_terminal_qr(png, out, half_block=False)
    lines = out.getvalue().splitlines()
    assert len(lines) == 10 and all(len(line) == 24 for line in lines)
    assert lines[:4] == [" " * 24] * 4
    assert lines[4] == " " * 8 + "##  ##  " + " " * 8
    assert lines[5] == " " * 8 + "  ####  " + " " * 8
    assert lines[-4:] == [" " * 24] * 4


def test_terminal_qr_compact_default_has_quiet_zone():
    from io import StringIO
    import re
    from superstar_checker.qrlogin import render_terminal_qr

    png = _test_png_1bit([[True] * 21 for _ in range(21)])
    out = StringIO()
    render_terminal_qr(png, out)
    lines = [re.sub(r"\x1b\[[0-9;]*m", "", line)
             for line in out.getvalue().splitlines()]

    assert len(lines) == 15
    assert all(len(line) == 29 for line in lines)
    assert all(set(line) == {" "} for line in lines[:2] + lines[-2:])
    assert all(line.startswith("    ") and line.endswith("    ") for line in lines)
    assert any("▀" in line or "▄" in line for line in lines)


def test_terminal_qr_compact_preserves_each_module():
    from io import StringIO
    import re
    from superstar_checker.qrlogin import render_terminal_qr

    modules = [[(x * 3 + y * 5) % 7 < 3 for x in range(21)]
               for y in range(21)]
    png = _test_png_1bit(modules)
    out = StringIO()
    render_terminal_qr(png, out)

    expected = [[False] * 29 for _ in range(4)]
    expected += [[False] * 4 + row + [False] * 4 for row in modules]
    expected += [[False] * 29 for _ in range(4)]
    rendered = [[False] * 29 for _ in range(30)]
    colors = {
        ("30;40", " "): (True, True),
        ("30;47", "▀"): (True, False),
        ("37;40", "▄"): (False, True),
        ("37;47", " "): (False, False),
    }
    for y, line in enumerate(out.getvalue().splitlines()):
        cells = re.findall(r"\x1b\[([0-9;]+)m(.)", line)
        assert len(cells) == 29
        for x, cell in enumerate(cells):
            upper, lower = colors[cell]
            rendered[y * 2][x] = upper
            rendered[y * 2 + 1][x] = lower
    assert rendered[:29] == expected


def test_terminal_qr_ascii_fallback_keeps_quiet_zone():
    from io import StringIO
    from superstar_checker.qrlogin import render_terminal_qr

    png = _test_png_1bit([[True] * 21 for _ in range(21)])
    out = StringIO()
    render_terminal_qr(png, out, half_block=False)
    lines = out.getvalue().splitlines()

    assert len(lines) == 29
    assert all(len(line) == 58 for line in lines)
    assert lines[0] == " " * 58 and lines[-1] == " " * 58


def test_qr_scaled_png_collapses_to_modules():
    """服务端的 5px/module PNG 应压缩成真实 QR 模块，而不是满屏黑块。"""
    from superstar_checker.qrlogin import _png_to_grid

    modules = [[(x == 0 or y == 0 or x == 20 or y == 20 or x == y)
                for x in range(21)] for y in range(21)]
    pixels = [[modules[y // 5][x // 5] for x in range(105)]
              for y in range(105)]
    assert _png_to_grid(_test_png_1bit(pixels)) == modules


def test_native_qr_raster_is_square_and_preserves_modules():
    from superstar_checker.qrwindow import rasterize_qr

    modules = [[(x * 3 + y * 5) % 7 < 3 for x in range(21)]
               for y in range(21)]
    width, height, pixels = rasterize_qr(_test_png_1bit(modules), module_pixels=2)
    assert (width, height) == (58, 58)
    assert len(pixels) == width * height * 4

    expected = [[False] * 29 for _ in range(4)]
    expected += [[False] * 4 + row + [False] * 4 for row in modules]
    expected += [[False] * 29 for _ in range(4)]
    for y, row in enumerate(expected):
        for x, dark in enumerate(row):
            expected_rgb = b"\x00" * 3 if dark else b"\xff" * 3
            for dy in range(2):
                for dx in range(2):
                    offset = (((y * 2 + dy) * width) + x * 2 + dx) * 4
                    assert pixels[offset:offset + 3] == expected_rgb
    assert pixels[:width * 4] == b"\xff" * (width * 4)


def test_qr_rejects_json_as_image():
    from types import SimpleNamespace
    from superstar_checker.qrlogin import _new_code
    class FakeClient:
        def request(self, *args, **kwargs):
            return SimpleNamespace(status_code=200, content=b'{"type":2}')
    try:
        _new_code(FakeClient(), "uuid")
    except Exception as exc:
        assert "有效图片" in str(exc)
    else:
        raise AssertionError("二维码错误 JSON 不应被当成图片")


def test_stop_event_clear_resets_cache():
    from superstar_checker.study import FileStopEvent
    event = FileStopEvent(ttl=3600)
    event.clear()
    event.set()
    assert event.is_set() is True
    event.clear()
    assert event.is_set() is False


def test_study_state_has_context():
    from superstar_checker import study
    old_fetch = study.fetch_chapters
    old_file = study._state_file
    state_path = Path.cwd() / "test-study-state.json"
    class FakeClient:
        uid = "test-user"
    study._state_file = lambda: state_path
    study.fetch_chapters = lambda *args, **kwargs: []
    manager = study.StudyManager()
    try:
        result = manager.start(FakeClient(), "course", "class", "cpi")
        assert result["status"] == "started"
        manager._thread.join(5)
        state = study.load_state()
        assert state["uid"] == "test-user" and state["cpi"] == "cpi"
    finally:
        study.fetch_chapters = old_fetch
        study._state_file = old_file
        state_path.unlink(missing_ok=True)


def test_work_list():
    html = ('<li data="/mooc2/work/dowork?workId=111&amp;answerId=222">'
            '<div class="overHidden2">第一次作业</div><div class="status">未交</div>'
            '<div class="time notOver">截止：2026-12-31</div><span class="tag icon-zy"></span></li>')
    works = parse_work_list(html)
    assert works and works[0]["work_id"] == "111"
    assert works[0]["answer_id"] == "222" and works[0]["title"] == "第一次作业"


def test_cookie_conflict():
    """回归：多域同名 cookie（JSESSIONID）不得抛 CookieConflict。"""
    from httpx import Cookies

    from superstar_checker.client import ChaoxingClient
    client = ChaoxingClient()
    jar = client._client.cookies.jar
    for domain in ("i.chaoxing.com", "notice.chaoxing.com", "mooc1.chaoxing.com"):
        c = Cookies()
        c.set("JSESSIONID", f"sess-{domain}", domain=domain, path="/")
        for cookie in c.jar:
            jar.set_cookie(cookie)
    # 修复前：Cookies.items() 抛 "Multiple cookies exist with name=JSESSIONID"
    cookies = client.cookies()
    assert cookies["JSESSIONID"].startswith("sess-")
    client._uid = "123"
    assert isinstance(client.has_session(), bool)


def test_session_reload():
    """回归：用户在终端 CLI 登录后，运行中的 MCP 进程应能从磁盘恢复会话。"""
    import tempfile
    from superstar_checker import session_store
    from superstar_checker.client import ChaoxingClient
    old_dir, old_file = session_store.STORE_DIR, session_store.SESSION_FILE
    temp_dir = Path(tempfile.mkdtemp(prefix="superstar-session-test-"))
    session_store.STORE_DIR = temp_dir
    session_store.SESSION_FILE = temp_dir / "session.json"
    try:
        session_store.save({"_uid": "1", "vc3": "x", "_d": "y", "fid": "z"}, "1")
        # 模拟已在运行的 MCP 进程：内存无会话，但磁盘有
        client = ChaoxingClient()
        client._client.cookies.clear()
        client._uid = ""
        assert not client.has_session()
        client._reload_from_store()
        assert client.has_session() and client.uid == "1"
    finally:
        session_store.STORE_DIR = old_dir
        session_store.SESSION_FILE = old_file
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_login_switch_confirmation():
    """已有会话时先确认切换，取消不得进入密码/二维码流程。"""
    from superstar_checker.cli import _confirm_account_switch

    class FakeClient:
        uid = "current-user"

        def has_session(self):
            return True

    output = []
    answers = iter([""])
    assert not _confirm_account_switch(FakeClient(), ask=lambda _: next(answers),
                                       write=lambda text, **kwargs: output.append(text))
    assert any("当前账号已登录" in text for text in output)

    answers = iter(["y"])
    assert _confirm_account_switch(FakeClient(), ask=lambda _: next(answers),
                                   write=lambda text, **kwargs: None)


def test_login_switch_confirmation_skips_when_logged_out():
    from superstar_checker.cli import _confirm_account_switch

    class FakeClient:
        uid = ""

        def has_session(self):
            return False

    called = []
    assert _confirm_account_switch(FakeClient(), ask=lambda _: called.append(True))
    assert not called


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(name, "ok")
    print("ALL UNIT OK")
