"""FastMCP 服务器：把超星客户端能力暴露为 MCP 工具。"""

import functools
from typing import Any

from mcp.server.fastmcp import FastMCP

from . import materials, notice, session_store, study, work
from .client import ChaoxingClient, ChaoxingError
from .decode import QUESTION_TYPE_NAMES
from .study import MANAGER

mcp = FastMCP(
    "superstar-checker",
    instructions=(
        "超星学习通工具集。典型流程：login → get_courses → "
        "start_studying（后台按真实时长刷视频，study_status 查进度，stop_studying 停止）/"
        "study_chapter（文档打卡）/ (get_quizzes → get_quiz_questions → save_homework 测验) / "
        "get_inbox_folders + get_inbox_notices（收件箱作业通知整理）。"
        "答题规则：题目由你在对话中作答，作答后调用 save_homework 仅暂存。"
        "本系统【不支持自动提交】（风险控制，已禁用）：暂存后必须提醒用户在学习通"
        "App/网页中人工核对并手动提交。收件箱通知的 is_read 只代表通知已读，"
        "不代表作业已完成。"
    ),
)

_client: ChaoxingClient | None = None


def get_client() -> ChaoxingClient:
    global _client
    if _client is None:
        _client = ChaoxingClient()
    return _client


def _guard(fn):
    """把 ChaoxingError 转成 {'error': ...} 返回，避免 MCP 调用直接崩栈。"""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ChaoxingError as e:
            return {"error": str(e)}
        except Exception as e:
            # MCP 工具应返回结构化错误，避免解析器或接口格式变化直接打断会话。
            return {"error": f"工具执行失败: {e}"}
    return wrapper


# ---- 登录 ----

@mcp.tool()
@_guard
def login(phone: str, password: str) -> dict:
    """登录超星账号（手机号+密码）。成功后会话持久化，重启后免登录。"""
    client = get_client()
    result = client.login(phone, password)
    result["message"] = "登录成功，会话已保存"
    return result


@mcp.tool()
@_guard
def login_status() -> dict:
    """检查当前会话是否有效。未登录时返回 login_hint（让用户在终端执行的登录命令）。"""
    client = get_client()
    if not client.has_session():
        client._reload_from_store()
    if not client.has_session():
        return {"logged_in": False,
                "login_hint": "请在 cmd 中运行以下命令登录（密码在终端输入，有 * 回显，"
                              "不要把密码发到对话里）：",
                "command": client_login_hint(),
                "note": "登录成功后直接重试即可，无需重启 MCP"}
    return {"logged_in": client.check_login(), "uid": client.uid}


def client_login_hint(qr: bool = False, terminal: bool = False) -> str:
    from .client import login_hint
    return login_hint(qr, terminal)


@mcp.tool()
@_guard
def login_guide() -> dict:
    """返回引导用户自己在终端完成登录的命令（密码不经过对话，也可改用扫码）。
    当其他工具报"登录会话不存在或已失效"时，把本工具返回的 command 原样给用户执行。"""
    return {
        "command": client_login_hint(),
        "command_qr": client_login_hint(qr=True),
        "command_qr_terminal": client_login_hint(qr=True, terminal=True),
        "steps": ["打开一个 cmd 窗口", "粘贴运行上面任一命令（两条等价，任选其一）",
                  "密码方式：按提示输入手机号和密码（密码有 * 回显，可用 Backspace 删除、Esc 清空）",
                  "扫码方式：浏览器会显示二维码，用学习通 App「扫一扫」并在手机点确认；"
                  "也可使用 command_qr_terminal 打开原生像素二维码窗口（--half-block/--ascii 可切换终端回退）；"
                  "每张码约 150 秒，过期自动换下一张，最多 4 张",
                  "看到 \"status\": true 即登录成功",
                  "回到本对话直接重试之前的操作，无需重启 MCP"],
    }


@mcp.tool()
@_guard
def logout() -> dict:
    """清除登录会话（内存 cookie + 本地持久化文件）。"""
    get_client().clear_session()
    return {"status": True, "message": "已清除本地会话"}


# ---- 课程与刷课 ----

@mcp.tool()
@_guard
def get_courses() -> Any:
    """获取课程列表（course_id/class_id/cpi 用于后续所有课程级工具）。"""
    client = get_client()
    client.ensure_login()
    resp = client.follow("https://mooc1-api.chaoxing.com/mycourse/backclazzdata",
                         params={"view": "json", "rss": "1"})
    from .decode import parse_courses
    body = resp.json()
    if body.get("result") != 1:
        return {"error": f"课程列表接口异常: {body.get('msg', body)}"}
    return parse_courses(body)


@mcp.tool()
@_guard
def get_chapters(course_id: str, class_id: str, cpi: str) -> Any:
    """获取课程章节树，标注每个小节的任务数与完成状态。"""
    client = get_client()
    client.ensure_login()
    return study.fetch_chapters(client, course_id, class_id, cpi)


@mcp.tool()
@_guard
def study_chapter(course_id: str, class_id: str, cpi: str, knowledge_id: str,
                  include_videos: bool = False) -> dict:
    """完成一个章节小节（knowledge_id 来自 get_chapters）的任务点。
    默认只处理文档/阅读打卡（秒级完成）；视频按超星规则必须真实时长播放，
    include_videos=True 时同步等待刷完（一个 30 分钟视频要 30 分钟，慎用），
    建议改用 start_studying 后台刷。章节测验用作业工具处理。"""
    client = get_client()
    client.ensure_login()
    attachments, defaults = study.fetch_card_attachments(
        client, course_id, class_id, cpi, knowledge_id)
    if not attachments:
        return {"knowledge_id": knowledge_id, "results": [], "summary": "未发现任务点"}
    if not include_videos:
        attachments = [a for a in attachments
                       if study.attachment_kind(a) not in ("video", "audio", "media")]
    results = study.study_attachments(client, course_id, class_id, cpi,
                                      knowledge_id, attachments, defaults)
    passed = sum(1 for r in results if r["status"] in ("passed", "skipped"))
    return {"knowledge_id": knowledge_id, "results": results,
            "summary": f"{passed}/{len(results)} 个任务点完成"}


@mcp.tool()
@_guard
def start_studying(course_id: str, class_id: str, cpi: str) -> dict:
    """后台开始刷整门课：扫描未完成章节并按视频真实时长逐个播放（1x，
    不跳进度），文档/阅读即时打卡。立即返回；用 study_status 查进度，
    stop_studying 停止（进度保留，可续播）。同一时间只能跑一门课。"""
    client = get_client()
    client.ensure_login()
    return MANAGER.start(client, course_id, class_id, cpi)


@mcp.tool()
@_guard
def study_status() -> dict:
    """查询后台刷课任务的实时进度（当前章节/视频、已处理数量）。"""
    return MANAGER.status()


@mcp.tool()
@_guard
def stop_studying() -> dict:
    """停止后台刷课。当前视频进度保留在服务端，下次 start_studying 自动续播。"""
    return MANAGER.stop()


# ---- 作业 ----

@mcp.tool()
@_guard
def get_homeworks(course_id: str, class_id: str, cpi: str) -> Any:
    """获取课程的作业列表（work_id/title/状态/截止时间）。"""
    client = get_client()
    client.ensure_login()
    return work.list_homeworks(client, course_id, class_id, cpi)


@mcp.tool()
@_guard
def get_homework(work_id: str, detail_url: str = "") -> dict:
    """获取一次作业的题目列表（题干/选项/题型）。
    题型代码：{}。拿到题目后在对话中作答，再用 save_homework 暂存，
    最后在学习通 App/网页中人工核对并提交。""".format(
        QUESTION_TYPE_NAMES)
    client = get_client()
    client.ensure_login()
    return work.fetch_homework(client, work_id, detail_url)


@mcp.tool()
@_guard
def save_homework(work_id: str, answers: dict[str, str]) -> dict:
    """仅暂存作业/测验答案（answers 形如 {"题目id": "答案"}：
    单选 "A"；多选 "ABD"；判断 "true"/"false"；填空多空用 | 分隔；简答/论述给纯文本）。
    出于风险控制本系统不支持自动提交：暂存后请提醒用户在学习通 App/网页中
    人工核对答案并手动提交。调用前必须先 get_homework 或 get_quiz_questions。"""
    client = get_client()
    client.ensure_login()
    return work.save_homework(client, work_id, answers)


@mcp.tool()
@_guard
def get_quizzes(course_id: str, class_id: str, cpi: str,
                max_chapters: int = 5, rescan: bool = False) -> Any:
    """列出课程的章节测验（隐藏在章节任务点里的作业/小测）。
    增量扫描：每次调用扫 max_chapters 个章节（默认 5，防止客户端超时），
    标题带"测验/测试/练习"的章节优先。若需扫完整门课，请重复调用本工具
    直到返回的列表长度不再增加。"""
    client = get_client()
    client.ensure_login()
    return work.list_quizzes(client, course_id, class_id, cpi, max_chapters, rescan)


@mcp.tool()
@_guard
def get_quiz_questions(course_id: str, class_id: str, cpi: str,
                       knowledge_id: str, jobid: str) -> dict:
    """查看一次章节测验的题目（题干/选项/题型，只读）。
    knowledge_id/jobid 来自 get_quizzes。
    重要流程：拿到题目后由你在对话中作答，然后调用 save_homework 暂存。
    本系统不支持自动提交——暂存后提醒用户在学习通里人工核对并手动提交。"""
    client = get_client()
    client.ensure_login()
    return work.fetch_quiz_questions(client, course_id, class_id, cpi,
                                     knowledge_id, jobid)


@mcp.tool()
@_guard
def get_homework_score(course_id: str, class_id: str, cpi: str, work_id: str,
                       work_answer_id: str = "") -> dict:
    """查看已批阅作业的得分与正确答案。"""
    client = get_client()
    client.ensure_login()
    return work.get_homework_score(client, course_id, class_id, cpi,
                                   work_id, work_answer_id)


# ---- 资料 ----

@mcp.tool()
@_guard
def get_materials(course_id: str, class_id: str, cpi: str,
                  max_chapters: int = 30) -> Any:
    """列出章节附件（PPT/PDF/文档/视频）的下载直链。"""
    client = get_client()
    client.ensure_login()
    return materials.list_materials(client, course_id, class_id, cpi, max_chapters)


# ---- 收件箱 ----

@mcp.tool()
@_guard
def get_inbox_folders() -> list[dict]:
    """收件箱文件夹列表（用户自建文件夹，含每夹通知数/未读数）。"""
    client = get_client()
    client.ensure_login()
    return notice.list_folders(client)


@mcp.tool()
@_guard
def get_inbox_notices(folder_name: str = "", keyword: str = "", limit: int = 50,
                      unread_only: bool = False) -> list[dict]:
    """拉取收件箱通知并归一化。老师的作业/测验通知会解析为结构化条目：
    {type: work/exam, title 作业名, course_id, class_id, homework_id,
    publish_time, url 直达链接, is_read}；普通通知返回 sender + content 正文。
    folder_name 传文件夹名（如"大二"，来自 get_inbox_folders），留空查默认收件箱；
    keyword 按标题搜索。适合让 AI 汇总"未完成的作业"。"""
    client = get_client()
    client.ensure_login()
    folder_uuid = ""
    if folder_name:
        folders = notice.list_folders(client)
        match = next((f for f in folders if f["name"] == folder_name), None)
        if match is None:
            return {"error": f"没有叫 {folder_name!r} 的文件夹，现有: "
                             f"{[f['name'] for f in folders]}"}
        folder_uuid = match["uuid"]
    return notice.list_notices(client, folder_uuid=folder_uuid, keyword=keyword,
                               limit=limit, unread_only=unread_only)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
