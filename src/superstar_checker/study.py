"""刷课引擎：视频/音频心跳上报、文档/阅读打卡、后台刷课任务。

协议要点（2026-09 用真实浏览器抓包对照验证）：
- 心跳参数与官方播放器一致：isdrag=3（播放中）、courseEngineInfo=false、
  videoFaceCaptureEnc/attDuration/attDurationEnc（附件有就必传）、rt 取自 otherInfo
- 必须按真实时间 1x 节奏推进 playingTime，高频灌进度服务端不判完成
- 播完（playingTime==duration）后发一次 isdrag=4，服务端返回 isPassed=true
- 不做"跳终点"秒过：视频该刷多久刷多久
"""

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path

from .client import CaptchaRequired, ChaoxingError, ChaoxingClient
from .decode import (attachment_kind, otherinfo_node_id, parse_chapters,
                     parse_marg, trim_otherinfo)

MAGIC = "d_yHJ!$pdA~5"
PLAYER_REFERER = "https://mooc1.chaoxing.com/ananas/modules/video/index.html?v=2026-0901-1154"

MOOC1 = "https://mooc1.chaoxing.com"
MOOC2_ANS = "https://mooc2-ans.chaoxing.com"


def video_enc(clazz_id, userid, jobid, object_id, playing_time_s, duration_s) -> str:
    plain = (f"[{clazz_id}][{userid}][{jobid}][{object_id}]"
             f"[{int(playing_time_s * 1000)}][{MAGIC}][{int(duration_s * 1000)}][0_{int(duration_s)}]")
    return hashlib.md5(plain.encode()).hexdigest()


def _rt_of(att: dict) -> str:
    prop = att.get("property") or {}
    if isinstance(prop, dict) and prop.get("rt"):
        return str(prop["rt"])
    match = re.search(r"-rt_([1d])", str(att.get("otherInfo") or att.get("otherinfo") or ""))
    if match:
        return "0.9" if match.group(1) == "d" else "1"
    return "0.9"


def fetch_chapters(client: ChaoxingClient, course_id, clazz_id, cpi) -> list[dict]:
    """抓取并解析课程章节树。"""
    resp = client.follow(f"{MOOC2_ANS}/mooc2-ans/mycourse/studentcourse", params={
        "courseid": course_id, "clazzid": clazz_id, "cpi": cpi, "ut": "s",
    }, headers={"Referer": f"{MOOC2_ANS}/mooc2-ans/visit/interaction"})
    return parse_chapters(resp.text)


def fetch_card_attachments(client: ChaoxingClient, course_id, clazz_id, cpi,
                           knowledge_id) -> tuple[list[dict], dict]:
    """遍历 num=0..6 抓取知识点的全部任务卡片。"""
    attachments, seen = [], set()
    defaults = {}
    for num in range(7):
        resp = client.follow(f"{MOOC1}/mooc-ans/knowledge/cards", params={
            "clazzid": clazz_id, "courseid": course_id, "knowledgeid": knowledge_id,
            "ut": "s", "cpi": cpi, "num": num, "mooc2": "1",
            "v": "2025-0424-1038-3",
        }, headers={"Referer": f"{MOOC1}/mooc-ans/mycourse/studentstudy"})
        m_arg = parse_marg(resp.text)
        if not m_arg:
            break
        defaults.update(m_arg.get("defaults") or {})
        for att in m_arg.get("attachments") or []:
            key = (att.get("jobid"), att.get("objectId"), att.get("cardid"))
            if key in seen:
                continue
            seen.add(key)
            attachments.append(att)
    return attachments, defaults


def _video_meta(client: ChaoxingClient, object_id: str, dtype: str) -> dict:
    module = "video" if dtype == "Video" else "audio"
    resp = client.follow(f"{MOOC1}/ananas/status/{object_id}", params={
        "k": client.fid, "flag": "normal",
    }, headers={"Referer": PLAYER_REFERER})
    if resp.status_code != 200:
        raise ChaoxingError(f"获取媒体信息失败（HTTP {resp.status_code}）")
    body = resp.json()
    if body.get("status") != "success":
        raise ChaoxingError(f"获取媒体信息异常: {body}")
    return body


def _build_report_ctx(ctx: dict, att: dict, meta: dict, duration: int) -> dict:
    """构造与官方播放器一致的上报参数（courseEngineInfo=false 必带）。"""
    return {
        "clazzId": ctx["clazz_id"],
        "duration": duration,
        "clipTime": f"0_{duration}",
        "objectId": att.get("objectId", ""),
        "otherInfo": trim_otherinfo(att),
        "courseId": ctx["course_id"],
        "jobid": att.get("jobid", ""),
        "userid": ctx["userid"],
        "view": "pc",
        "dtype": "Video" if ctx.get("dtype") != "Audio" else "Audio",
        "rt": _rt_of(att),
        "videoFaceCaptureEnc": att.get("videoFaceCaptureEnc", ""),
        "attDuration": att.get("attDuration", duration),
        "attDurationEnc": att.get("attDurationEnc", ""),
        "courseEngineInfo": "false",
    }


def _report_log(client: ChaoxingClient, ctx: dict, att: dict, meta: dict,
                playing_time: int, isdrag: int) -> dict:
    duration = int(meta.get("duration", 0))
    params = _build_report_ctx(ctx, att, meta, duration)
    params["playingTime"] = int(playing_time)
    params["isdrag"] = isdrag
    params["enc"] = video_enc(ctx["clazz_id"], ctx["userid"], att.get("jobid", ""),
                              att.get("objectId", ""), playing_time, duration)
    params["_t"] = int(time.time() * 1000)
    report_url = ctx.get("report_url") or \
        f"{MOOC1}/mooc-ans/multimedia/log/a/{ctx['cpi']}"
    dtoken = meta.get("dtoken", "")
    if dtoken and not report_url.rstrip("/").endswith(dtoken):
        report_url = report_url.rstrip("/") + "/" + dtoken
    resp = client.follow(report_url, params=params, headers={"Referer": PLAYER_REFERER})
    if resp.status_code == 403:
        raise CaptchaRequired("视频上报返回 403")
    if "验证码" in resp.text:
        raise CaptchaRequired()
    try:
        return resp.json()
    except ValueError:
        return {"isPassed": False, "raw": resp.text[:200]}


def _study_media(client: ChaoxingClient, ctx: dict, att: dict, name: str,
                 stop_event: threading.Event | None = None,
                 progress_cb=None) -> dict:
    """按真实时间 1x 节奏刷完一个视频/音频。

    从服务端记录的进度续播；每 ~58s（reportTimeInterval）上报一次 isdrag=3，
    到达时长后发 isdrag=4 收尾。不跳进度。
    """
    prop = att.get("property") or {}
    ctx["dtype"] = "Audio" if attachment_kind(att) == "audio" else "Video"
    meta = _video_meta(client, att.get("objectId", ""), ctx["dtype"])
    duration = int(meta.get("duration", 0))
    if duration <= 0:
        return {"name": name, "status": "failed", "detail": "无法获取媒体时长"}

    start = int(att.get("playTime") or 0) // 1000
    if start >= duration:
        # 进度已在结尾但任务未判通过：服务端记录了全程却缺少会话真实观看时长，
        # 只能从头按真实节奏重刷（否则永远无法判过）
        start = 0
        if progress_cb:
            progress_cb(name, 0, duration)

    interval = 58
    defaults = ctx.get("defaults") or {}
    if isinstance(defaults.get("reportTimeInterval"), int) and \
            20 <= defaults["reportTimeInterval"] <= 120:
        interval = defaults["reportTimeInterval"]

    t0 = time.time()
    playing = start
    next_report_at = playing + interval
    while playing < duration:
        if stop_event is not None and stop_event.is_set():
            return {"name": name, "status": "stopped",
                    "detail": f"进度 {playing}/{duration}s（已保留，下次续播）"}
        playing = min(start + int(time.time() - t0), duration)
        if playing >= next_report_at or playing >= duration:
            body = _report_log(client, ctx, att, meta, playing, 3)
            if body.get("isPassed"):
                return {"name": name, "status": "passed",
                        "detail": f"真实播放完成（{duration}s）"}
            next_report_at = playing + interval
            if progress_cb:
                progress_cb(name, playing, duration)
        time.sleep(1.0)

    body = _report_log(client, ctx, att, meta, duration, 4)
    for _ in range(2):
        if body.get("isPassed"):
            break
        time.sleep(3)
        body = _report_log(client, ctx, att, meta, duration, 4)
    if progress_cb:
        progress_cb(name, duration, duration)
    return {"name": name,
            "status": "passed" if body.get("isPassed") else "failed",
            "detail": f"真实播放完成（{duration}s）" if body.get("isPassed")
            else "播完上报未判通过，稍后重试"}


def _study_document(client: ChaoxingClient, ctx: dict, att: dict, name: str) -> dict:
    resp = client.follow(f"{MOOC1}/ananas/job/document", params={
        "jobid": att.get("jobid", ""),
        "knowledgeid": otherinfo_node_id(att) or ctx["knowledge_id"],
        "courseid": ctx["course_id"],
        "clazzid": ctx["clazz_id"],
        "jtoken": att.get("jtoken", ""),
        "_dc": int(time.time() * 1000),
    }, headers={"Referer": f"{MOOC1}/mooc-ans/knowledge/cards"})
    if _job_response_ok(resp):
        return {"name": name, "status": "passed", "detail": "文档打卡完成"}
    return {"name": name, "status": "failed",
            "detail": f"文档打卡未确认成功（HTTP {resp.status_code}）"}


def _study_read(client: ChaoxingClient, ctx: dict, att: dict, name: str) -> dict:
    resp = client.follow(f"{MOOC1}/ananas/job/readv2", params={
        "jobid": att.get("jobid", ""),
        "knowledgeid": otherinfo_node_id(att) or ctx["knowledge_id"],
        "jtoken": att.get("jtoken", ""),
        "courseid": ctx["course_id"],
        "clazzid": ctx["clazz_id"],
    }, headers={"Referer": f"{MOOC1}/mooc-ans/knowledge/cards"})
    if _job_response_ok(resp):
        return {"name": name, "status": "passed", "detail": "阅读打卡完成"}
    return {"name": name, "status": "failed",
            "detail": f"阅读打卡未确认成功（HTTP {resp.status_code}）"}


def _job_response_ok(resp) -> bool:
    """判断文档/阅读接口的业务结果，而不是只看 HTTP 状态码。"""
    if resp.status_code != 200:
        return False
    text = (resp.text or "").strip()
    if not text:
        return True
    try:
        body = resp.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        for key in ("status", "success", "result", "isPassed"):
            if key in body:
                value = body[key]
                return value is True or value == 1 or str(value).lower() in {
                    "true", "1", "success", "ok", "passed"
                }
        message = " ".join(str(body.get(k, "")) for k in ("msg", "message", "error"))
        if message:
            return not any(word in message.lower()
                           for word in ("失败", "错误", "失效", "登录", "error", "fail"))
    normalized = text.lower()
    if any(word in normalized for word in ("失败", "错误", "失效", "登录", "error", "fail")):
        return False
    return any(word in normalized for word in ("成功", "完成", "success", "ok", "passed"))


def _attachment_name(att: dict) -> str:
    prop = att.get("property") or {}
    if isinstance(prop, dict):
        return prop.get("name") or prop.get("title") or att.get("objectId") or "未命名任务点"
    return "任务点"


def study_attachments(client: ChaoxingClient, course_id, clazz_id, cpi,
                      knowledge_id, attachments: list[dict],
                      defaults: dict | None = None,
                      stop_event: threading.Event | None = None,
                      progress_cb=None) -> list[dict]:
    """处理一组任务点附件（视频按真实节奏）。"""
    ctx = {"course_id": course_id, "clazz_id": clazz_id, "cpi": cpi,
           "knowledge_id": knowledge_id, "userid": client.uid,
           "defaults": defaults or {}}
    if isinstance(defaults, dict) and defaults.get("reportUrl"):
        ctx["report_url"] = defaults["reportUrl"]

    results = []
    for att in attachments:
        name = _attachment_name(att)
        kind = attachment_kind(att)
        if att.get("isPassed") is True or att.get("isPassed") == "true":
            results.append({"name": name, "kind": kind, "status": "skipped", "detail": "已完成"})
            continue
        if kind in ("video", "audio", "media"):
            res = _study_media(client, ctx, att, name, stop_event, progress_cb)
        elif kind == "document":
            res = _study_document(client, ctx, att, name)
        elif kind == "read":
            res = _study_read(client, ctx, att, name)
        elif kind == "work":
            res = {"name": name, "kind": kind, "status": "manual",
                   "detail": "章节测验请用 get_quizzes/get_quiz_questions 取题，save_homework 暂存后人工提交"}
        else:
            res = {"name": name, "kind": kind, "status": "unsupported",
                   "detail": f"暂不支持的任务类型: {att.get('type') or att.get('card_type')}"}
        res.setdefault("kind", kind)
        results.append(res)
    return results


def study_knowledge(client: ChaoxingClient, course_id, clazz_id, cpi,
                    knowledge_id, stop_event: threading.Event | None = None,
                    progress_cb=None) -> dict:
    """完成一个知识点（章节小节）的全部任务点（视频真实节奏，耗时=剩余时长）。"""
    attachments, defaults = fetch_card_attachments(client, course_id, clazz_id,
                                                   cpi, knowledge_id)
    if not attachments:
        return {"knowledge_id": knowledge_id, "results": [],
                "summary": "未发现任务点（可能已全部完成或该节无任务）"}
    results = study_attachments(client, course_id, clazz_id, cpi, knowledge_id,
                                attachments, defaults, stop_event, progress_cb)
    passed = sum(1 for r in results if r["status"] in ("passed", "skipped"))
    return {"knowledge_id": knowledge_id, "results": results,
            "summary": f"{passed}/{len(results)} 个任务点完成"}


class FileStopEvent:
    """跨进程停止信号：标志文件存在即停止（供 CLI 独立进程使用）。"""

    def __init__(self, ttl: float = 1.0):
        self._ttl = ttl
        self._last_check = 0.0
        self._cached = False

    @property
    def _flag(self) -> Path:
        return Path.home() / ".superstar-checker" / "study_stop.flag"

    def is_set(self) -> bool:
        now = time.time()
        if now - self._last_check > self._ttl:
            self._cached = self._flag.exists()
            self._last_check = now
        return self._cached

    def set(self) -> None:
        self._flag.parent.mkdir(parents=True, exist_ok=True)
        self._flag.write_text("stop", encoding="utf-8")
        self._cached = True
        self._last_check = time.time()

    def clear(self) -> None:
        try:
            self._flag.unlink()
        except OSError:
            pass
        self._cached = False
        self._last_check = 0.0


def _state_file() -> Path:
    return Path.home() / ".superstar-checker" / "study_state.json"


def _save_state(state: dict) -> None:
    temp_path = None
    try:
        target = _state_file()
        target.parent.mkdir(parents=True, exist_ok=True)
        state["updated_at"] = time.time()
        temp_path = target.with_name(f"{target.name}.{os.getpid()}.tmp")
        payload = json.dumps(state, ensure_ascii=False, default=str)
        with temp_path.open("w", encoding="utf-8", newline="") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, target)
    except OSError:
        pass
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


def load_state() -> dict | None:
    try:
        return json.loads(_state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _process_alive(pid) -> bool:
    """判断落盘状态所属进程是否仍存在。"""
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


class StudyManager:
    """后台刷课任务：单线程顺序刷一门课的全部未完成章节。

    start() 立即返回，study_status() 查询进度，stop() 优雅停止
    （当前上报周期结束后保留进度，下次续播）。状态同步落盘，
    供 CLI 的 study-status / study-stop 跨进程使用。
    """

    def __init__(self):
        self._thread: threading.Thread | None = None
        # 用文件标志做停止信号：MCP 进程与 CLI 进程（study-stop）都能停
        self._stop = FileStopEvent()
        self._lock = threading.Lock()
        self._state: dict = {"running": False}
        saved = load_state()
        if saved:
            if saved.get("running") and not _process_alive(saved.get("pid")):
                saved["running"] = False
                saved["current"] = "异常停止"
                saved["error"] = "刷课进程已退出，任务未完成"
                _save_state(saved)
            self._state = saved

    def start(self, client: ChaoxingClient, course_id, clazz_id, cpi) -> dict:
        with self._lock:
            disk = load_state()
            if disk and disk.get("running") and _process_alive(disk.get("pid")):
                self._state = disk
            if self._state.get("running"):
                return {"status": "already_running",
                        "detail": self._state.get("current", "")}
            self._stop.clear()
            self._state = {"running": True, "course_id": course_id,
                           "class_id": clazz_id, "cpi": str(cpi),
                           "uid": str(client.uid), "results": [],
                           "current": "扫描章节中…", "started_at": time.time(),
                           "pid": os.getpid()}
            _save_state(self._state)
            t = threading.Thread(target=self._run, daemon=True,
                                 args=(client, course_id, clazz_id, cpi))
            self._thread = t
            t.start()
            return {"status": "started"}

    def _run(self, client, course_id, clazz_id, cpi):
        run_error = ""
        try:
            try:
                chapters = fetch_chapters(client, course_id, clazz_id, cpi)
            except Exception as exc:
                run_error = f"扫描章节失败: {exc}"
                return
            pending = [c for c in chapters if not c["finished"] and c["job_count"] > 0]
            with self._lock:
                self._state["pending_total"] = len(pending)
                self._state["pending"] = [c["title"] for c in pending]
                _save_state(self._state)
            for idx, ch in enumerate(pending, 1):
                if self._stop.is_set():
                    break
                with self._lock:
                    self._state["current"] = ch["title"]
                    self._state["chapter_index"] = f"{idx}/{len(pending)}"
                    _save_state(self._state)

                def cb(name, playing, duration):
                    with self._lock:
                        self._state["video_progress"] = {
                            "chapter": ch["title"], "name": name,
                            "playing": playing, "duration": duration}
                    _save_state(self._state)

                try:
                    res = study_knowledge(client, course_id, clazz_id, cpi,
                                          ch["knowledge_id"],
                                          stop_event=self._stop, progress_cb=cb)
                except ChaoxingError as e:
                    res = {"knowledge_id": ch["knowledge_id"], "results":
                           [{"name": ch["title"], "status": "failed", "detail": str(e)}],
                           "summary": f"出错: {e}"}
                except Exception as e:
                    res = {"knowledge_id": ch["knowledge_id"], "results":
                           [{"name": ch["title"], "status": "failed", "detail": str(e)}],
                           "summary": f"未处理异常: {e}"}
                with self._lock:
                    self._state["results"].append({"chapter": ch["title"],
                                                   "summary": res["summary"],
                                                   "results": res["results"]})
                    _save_state(self._state)
        except Exception as e:
            run_error = f"后台刷课异常: {e}"
        finally:
            with self._lock:
                self._state["running"] = False
                if run_error:
                    self._state["current"] = "异常停止"
                    self._state["error"] = run_error
                else:
                    self._state["current"] = "已停止" if self._stop.is_set() else "全部完成"
                _save_state(self._state)

    def stop(self) -> dict:
        self._stop.set()
        _save_state(self._state)
        return {"status": "stopping", "detail": "当前上报周期结束后暂停，进度已保留"}

    def status(self) -> dict:
        with self._lock:
            st = dict(self._state)
        if not (self._thread and self._thread.is_alive()):
            disk = load_state()
            if disk:
                st = disk
        if not st.get("running"):
            recent = st.get("results", [])[-5:]
            return {"running": False, "state": st.get("current"),
                    "error": st.get("error", ""),
                    "processed": len(st.get("results", [])), "recent": recent}
        return {"running": True, "current_chapter": st.get("current"),
                "video_progress": st.get("video_progress"),
                "pending_total": st.get("pending_total"),
                "processed": len(st.get("results", [])),
                "recent": st.get("results", [])[-3:],
                "error": st.get("error", "")}


MANAGER = StudyManager()
