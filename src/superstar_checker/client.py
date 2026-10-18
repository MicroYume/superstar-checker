"""超星 HTTP 客户端：会话管理、登录、限速、会话失效/风控检测。"""

import sys
import threading
import time
from pathlib import Path
from urllib.parse import urljoin

import httpx

from . import session_store
from .crypto import encrypt_aes, encrypt_des

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

DEFAULT_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
}


class ChaoxingError(Exception):
    """可恢复的业务/网络错误，message 直接展示给用户。"""


def _stop_study_manager() -> None:
    """会话切换前请求停止后台刷课，避免旧会话继续上报。"""
    try:
        from .study import MANAGER
        if MANAGER.status().get("running"):
            MANAGER.stop()
    except (ImportError, AttributeError):
        pass


def _clear_work_caches() -> None:
    """会话切换时清除依赖账号的作业/测验缓存。"""
    try:
        from . import work
        work.clear_caches()
    except ImportError:
        # client.py 在模块初始化期间可能被 work.py 反向导入，此时无需清理。
        pass


def login_hint(qr: bool = False, terminal: bool = False) -> str:
    """生成终端登录命令；终端扫码默认打开 Win32 原生像素窗口。"""
    suffix = (" --qr" if qr else "") + (" --terminal" if terminal else "")
    try:
        scripts = Path(sys.executable).parent          # ...\Superstar_Checker\.venv\Scripts
        login_exe = scripts / "superstar-checker.exe"
        root = scripts.parent.parent
        if login_exe.exists():
            return f'cd /d "{root}" && .venv\\Scripts\\superstar-checker.exe login{suffix}'
        if scripts.name.lower() == "runtime":
            portable_root = scripts.parent
            return f'cd /d "{portable_root}" && runtime\\python.exe -m superstar_checker.cli login{suffix}'
    except Exception:
        pass
    return f"superstar-checker login{suffix}   （需先 pip install -e .）"


class SessionExpired(ChaoxingError):
    def __init__(self):
        super().__init__(
            "登录会话不存在或已失效。请在 cmd 中运行以下命令登录"
            "（密码在终端输入、有 * 回显，不要把密码发到对话里）：\n  "
            + login_hint()
            + "\n登录成功后直接重试本操作即可，无需重启 MCP。")


class CaptchaRequired(ChaoxingError):
    def __init__(self, detail: str = ""):
        super().__init__(
            "触发超星风控验证码，请稍后再试或降低频率（可先在手机/网页端登录一次再试）"
            + (f"：{detail}" if detail else ""))


class ChaoxingClient:
    def __init__(self, min_interval: float = 1.0):
        self._client = httpx.Client(
            headers=DEFAULT_HEADERS, follow_redirects=False, timeout=30.0)
        self._min_interval = min_interval
        self._last_request = 0.0
        self._io_lock = threading.RLock()
        self._uid = ""
        self._fid = ""
        self._session_stamp = ("", None)  # (uid, saved_at)：与磁盘会话比对
        self._login_backup = None
        saved = session_store.load()
        if saved:
            self._client.cookies.update(saved["cookies"])
            self._uid = saved.get("uid", "")
            self._fid = saved["cookies"].get("fid", "")
            self._session_stamp = (self._uid, saved.get("saved_at"))

    # ---- 底层请求 ----

    def request(self, method: str, url: str, **kw) -> httpx.Response:
        """带全局限速的单次请求。"""
        with self._io_lock:
            wait = self._min_interval - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()
            try:
                return self._client.request(method, url, **kw)
            except httpx.HTTPError as e:
                raise ChaoxingError(f"网络请求失败: {e}") from e

    def follow(self, url: str, **kw) -> httpx.Response:
        """GET 并手动跟随 3xx；一旦从业务域名跳向登录页视为会话失效。

        注意：后续跳转请求不能复用调用方的 params，否则会覆盖
        302 Location 自带的 query（如 stu 页面 URL 里的 enc 签名）。
        """
        hop_kw = dict(kw)
        resp = self.request("GET", url, **hop_kw)
        for _ in range(6):
            if resp.status_code not in (301, 302, 303, 307, 308):
                break
            loc = resp.headers.get("location", "")
            if not loc:
                break
            nxt = urljoin(str(resp.url), loc)
            # 只有"从业务域名被踢回登录页"才算会话失效；
            # passport2 自身的 302/301（登录页内部跳转）是正常流程
            if ("passport2.chaoxing.com" in nxt
                    and "passport2.chaoxing.com" not in str(resp.url)):
                raise SessionExpired()
            hop_kw.pop("params", None)
            resp = self.request("GET", nxt, **hop_kw)
        if resp.status_code == 403:
            raise CaptchaRequired("HTTP 403")
        return resp

    # ---- 属性 ----

    @property
    def uid(self) -> str:
        return self._uid

    @property
    def fid(self) -> str:
        return self._fid

    def cookies(self) -> dict:
        # 直接遍历 jar：Cookies.items() 按名字取值，同名跨域 cookie（如各子域
        # 各自下发的 JSESSIONID）会抛 CookieConflict
        return {c.name: c.value for c in self._client.cookies.jar}

    def has_session(self) -> bool:
        if not self._uid:
            return False
        jar_names = {c.name for c in self._client.cookies.jar}
        return bool(jar_names & {"vc3", "_d"})

    def clear_session(self) -> None:
        """清空内存 cookie 与持久化文件（logout 用）。"""
        with self._io_lock:
            _stop_study_manager()
            self._client.cookies.clear()
            self._uid = ""
            self._fid = ""
            self._session_stamp = ("", None)
            session_store.clear()
            _clear_work_caches()

    # ---- 登录 ----

    # ---- 登录 ----

    LOGIN_PAGE = "https://passport2.chaoxing.com/login?newversion=true"

    def reset_for_login(self) -> httpx.Response:
        """登录前清场：停后台刷课、清空旧 cookie，访问登录页拿初始 cookie。"""
        with self._io_lock:
            import copy
            self._login_backup = (
                [copy.copy(cookie) for cookie in self._client.cookies.jar],
                self._uid, self._fid, self._session_stamp,
            )
            _stop_study_manager()
            _clear_work_caches()
            self._client.cookies.clear()
            self._uid = ""
            self._fid = ""
            try:
                return self.follow(self.LOGIN_PAGE)
            except Exception:
                self.restore_login_state()
                raise

    def restore_login_state(self) -> None:
        """登录流程失败时恢复 reset_for_login() 前的内存会话。"""
        with self._io_lock:
            if self._login_backup is None:
                return
            cookies, uid, fid, stamp = self._login_backup
            self._client.cookies.clear()
            for cookie in cookies:
                self._client.cookies.jar.set_cookie(cookie)
            self._uid, self._fid, self._session_stamp = uid, fid, stamp
            self._login_backup = None

    def discard_login_backup(self) -> None:
        """登录成功后丢弃旧会话备份。"""
        self._login_backup = None

    def finalize_login(self, via: str) -> dict:
        """把当前 jar 里的会话落盘。密码登录和扫码登录共用这一步。"""
        cookies = self.cookies()
        self._uid = cookies.get("_uid") or cookies.get("UID") or ""
        self._fid = cookies.get("fid", "")
        if not self._uid:
            raise ChaoxingError("登录成功但未取到 uid cookie，请重试")
        saved_at = session_store.save(cookies, self._uid)
        self._session_stamp = (self._uid, saved_at)
        self.discard_login_backup()
        _clear_work_caches()
        return {"status": True, "uid": self._uid, "via": via}

    def login(self, uname: str, password: str) -> dict:
        with self._io_lock:
            try:
                return self._login(uname, password)
            except Exception:
                self.restore_login_state()
                raise

    def _login(self, uname: str, password: str) -> dict:
        """账号密码登录。AES 加密优先，失败自动回退 DES；成功后 cookie 落盘。"""
        self.reset_for_login()

        attempts = [
            ("AES", encrypt_aes(password), uname),
            ("DES", encrypt_des(password), uname),
            ("AES+AES", encrypt_aes(password), encrypt_aes(uname)),
        ]
        last_msg = ""
        for tag, enc_pwd, enc_uname in attempts:
            resp = self.request("POST", "https://passport2.chaoxing.com/fanyalogin", data={
                "fid": "-1",
                "uname": enc_uname,
                "password": enc_pwd,
                "refer": "https://i.chaoxing.com",
                "t": "true",
                "forbidotherlogin": "0",
                "validate": "",
                "doubleFactorLogin": "0",
                "independentId": "0",
            }, headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                        "X-Requested-With": "XMLHttpRequest",
                        "Referer": "https://passport2.chaoxing.com/login?newversion=true"})
            try:
                body = resp.json()
            except ValueError:
                last_msg = f"登录接口返回非 JSON（HTTP {resp.status_code}）"
                continue
            if body.get("status"):
                return self.finalize_login(tag)
            last_msg = body.get("msg2") or body.get("msg") or "未知原因"
            if "验证" in last_msg or "码" in last_msg:
                raise CaptchaRequired(last_msg)
        raise ChaoxingError(f"登录失败：{last_msg}")

    def check_login(self) -> bool:
        """访问个人中心验证会话是否仍有效。"""
        try:
            resp = self.follow("https://i.chaoxing.com/")
        except SessionExpired:
            return False
        return resp.status_code == 200

    def _reload_from_store(self) -> None:
        """从磁盘会话文件恢复（用户在终端/开关 bat 登录后，MCP 进程无需重启）。"""
        with self._io_lock:
            saved = session_store.load()
            if saved:
                self._client.cookies.clear()
                self._client.cookies.update(saved["cookies"])
                self._uid = saved.get("uid", "")
                self._fid = saved["cookies"].get("fid", "")
                self._session_stamp = (self._uid, saved.get("saved_at"))
                _clear_work_caches()
            else:
                self._client.cookies.clear()
                self._uid = ""
                self._fid = ""
                self._session_stamp = ("", None)
                _clear_work_caches()

    def ensure_login(self) -> None:
        # 磁盘会话与内存不一致（被"退出登录.bat"清掉或换了账号）→ 以磁盘为准
        saved = session_store.load()
        disk_stamp = ((saved or {}).get("uid", ""), (saved or {}).get("saved_at"))
        if disk_stamp != self._session_stamp:
            self._reload_from_store()
        if not self.has_session():
            # 登录尝试失败后，磁盘时间戳可能没有变化，但内存 Cookie 已被清场。
            # 仍应从磁盘恢复旧会话。
            self._reload_from_store()
        if not self.has_session():
            raise SessionExpired()
        # vc3 失效时页面会跳登录页，靠 follow 里的检测兜底
