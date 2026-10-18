"""命令行自测入口：逐个功能独立验证（不经过 MCP）。

用法：
  .venv\\Scripts\\superstar-checker.exe login            # 输入手机号+密码
  .venv\\Scripts\\superstar-checker.exe login --qr       # 浏览器扫码登录
  .venv\\Scripts\\superstar-checker.exe login --qr --terminal  # Win32 原生像素窗口扫码
  .venv\\Scripts\\superstar-checker.exe login --qr --terminal --half-block  # 半块字符回退
  .venv\\Scripts\\superstar-checker.exe login --qr --terminal --ascii  # ASCII 兼容模式
  superstar-checker courses
  superstar-checker chapters <course_id> <class_id> <cpi>
  superstar-checker study <course_id> <class_id> <cpi> [max_chapters]   # 仅文档
  superstar-checker study-start <course_id> <class_id> <cpi>            # 后台刷课
  superstar-checker study-status / study-stop
  superstar-checker homeworks <course_id> <class_id> <cpi>
  superstar-checker homework <work_id>
  superstar-checker materials <course_id> <class_id> <cpi>
"""

import argparse
import getpass
import json
import sys


def _print(data) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def _input_masked(prompt: str = "密码: ") -> str:
    """逐字符回显 * 的密码输入（Windows 用 msvcrt，其他平台退回 getpass）。"""
    print(prompt, end="", flush=True)
    try:
        import msvcrt
    except ImportError:
        return getpass.getpass("")
    chars = []
    while True:
        ch = msvcrt.getwch()
        if ch in ("\r", "\n"):
            print()
            return "".join(chars)
        if ch in ("\x08", "\x7f"):  # Backspace
            if chars:
                chars.pop()
                print("\b \b", end="", flush=True)
        elif ch == "\x03":  # Ctrl+C
            raise KeyboardInterrupt
        elif ch == "\x1b":  # Esc 清空重来
            old = len(chars)
            chars = []
            print("\r" + prompt + " " * old + "\r" + prompt, end="", flush=True)
        elif not ch.startswith("\x00") and not ch.startswith("\xe0"):
            chars.append(ch)
            print("*", end="", flush=True)


def _confirm_account_switch(client, ask=None, write=None) -> bool:
    """登录前确认是否替换当前会话；取消时不触碰登录流程。"""
    if not client.has_session():
        return True

    if ask is None:
        ask = input
    if write is None:
        write = print

    uid = client.uid or "未知账号"
    detail = f"当前账号已登录（uid: {uid}）。"
    try:
        from .study import MANAGER
        if MANAGER.status().get("running"):
            detail += "当前正在刷课，切换账号会先停止刷课。"
    except (ImportError, AttributeError):
        pass
    write(detail)
    write("继续将切换到新账号，是否切换？[y/N]: ", end="")
    answer = ask("").strip().lower()
    if answer in {"y", "yes", "是", "切换", "确认"}:
        write("已确认切换账号。")
        return True
    write("已取消登录，当前账号保持不变。")
    return False


def main() -> None:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="superstar-checker", description="超星学习通自测 CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("login", help="登录：默认交互式输入手机号+密码，--qr 改用扫码")
    p.add_argument("--qr", action="store_true",
                   help="扫码登录：二维码显示在浏览器里，全程不输入密码")
    p.add_argument("--terminal", "--cmd", dest="terminal", action="store_true",
                   help="与 --qr 一起使用：打开 Win32 原生像素二维码窗口")
    p.add_argument("--half-block", action="store_true",
                   help="与 --terminal 一起使用：改用半块字符显示二维码")
    p.add_argument("--ascii", action="store_true",
                   help="与 --terminal 一起使用：改用纯 ASCII 显示二维码")
    sub.add_parser("logout", help="清除本地会话")
    sub.add_parser("status", help="会话状态")

    def add_course_args(p):
        p.add_argument("course_id")
        p.add_argument("class_id")
        p.add_argument("cpi")

    p = sub.add_parser("courses", help="课程列表")
    p = sub.add_parser("chapters", help="章节树")
    add_course_args(p)
    p = sub.add_parser("study", help="刷章节的文档/阅读任务（视频请用 study-start）")
    add_course_args(p)
    p.add_argument("max_chapters", nargs="?", type=int, default=2)
    p = sub.add_parser("study-start", help="后台开始刷整门课（视频真实时长播放）")
    add_course_args(p)
    sub.add_parser("study-status", help="后台刷课进度")
    sub.add_parser("study-stop", help="停止后台刷课（进度保留）")
    p = sub.add_parser("homeworks", help="作业列表")
    add_course_args(p)
    p = sub.add_parser("quizzes", help="扫描章节测验列表")
    add_course_args(p)
    p.add_argument("--max-chapters", type=int, default=30)
    p = sub.add_parser("quiz", help="查看章节测验题目（只读）")
    add_course_args(p)
    p.add_argument("knowledge_id")
    p.add_argument("jobid")
    sub.add_parser("inbox-folders", help="收件箱文件夹列表")
    p = sub.add_parser("inbox", help="收件箱通知（作业通知为结构化输出）")
    p.add_argument("--folder", default="", help="文件夹名，如 大二")
    p.add_argument("--kw", default="", help="标题搜索")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--unread", action="store_true")
    p = sub.add_parser("materials", help="资料直链")
    add_course_args(p)
    p.add_argument("--max-chapters", type=int, default=10)
    p = sub.add_parser("homework", help="抓取题目")
    p.add_argument("work_id")
    p.add_argument("detail_url", nargs="?", default="")

    args = parser.parse_args()

    from .client import ChaoxingClient, ChaoxingError
    client = ChaoxingClient()
    try:
        if args.cmd == "login":
            if (args.terminal or args.half_block or args.ascii) and not args.qr:
                parser.error("--terminal/--cmd/--half-block/--ascii 只能与 --qr 一起使用")
            if (args.half_block or args.ascii) and not args.terminal:
                parser.error("--half-block/--ascii 只能与 --terminal 一起使用")
            if not _confirm_account_switch(client):
                return
            if args.qr:
                from .qrlogin import login_with_qr
                _print(login_with_qr(client, terminal=args.terminal,
                                     open_browser=not args.terminal,
                                     half_block=(True if args.half_block else
                                                 False if args.ascii else None)))
            else:
                phone = input("手机号: ").strip()
                password = _input_masked("密码: ")
                _print(client.login(phone, password))
        elif args.cmd == "logout":
            client.clear_session()
            print("已清除本地会话")
        elif args.cmd == "status":
            _print({"has_session": client.has_session(),
                    "valid": client.check_login() if client.has_session() else False,
                    "uid": client.uid})
        elif args.cmd == "courses":
            from .decode import parse_courses
            resp = client.follow("https://mooc1-api.chaoxing.com/mycourse/backclazzdata",
                                 params={"view": "json", "rss": "1"})
            _print(parse_courses(resp.json()))
        elif args.cmd == "chapters":
            from .study import fetch_chapters
            _print(fetch_chapters(client, args.course_id, args.class_id, args.cpi))
        elif args.cmd == "study":
            from .decode import attachment_kind
            from .study import fetch_card_attachments, fetch_chapters, study_attachments
            chapters = fetch_chapters(client, args.course_id, args.class_id, args.cpi)
            pending = [c for c in chapters if not c["finished"] and c["job_count"] > 0]
            for ch in pending[:args.max_chapters]:
                atts, defaults = fetch_card_attachments(
                    client, args.course_id, args.class_id, args.cpi, ch["knowledge_id"])
                docs = [a for a in atts
                        if attachment_kind(a) not in ("video", "audio", "media")]
                _print({"chapter": ch["title"],
                        "results": study_attachments(
                            client, args.course_id, args.class_id, args.cpi,
                            ch["knowledge_id"], docs, defaults)})
        elif args.cmd == "study-start":
            import time as _t
            from .study import MANAGER, FileStopEvent
            FileStopEvent().clear()
            _print(MANAGER.start(client, args.course_id, args.class_id, args.cpi))
            try:
                while True:
                    st = MANAGER.status()
                    _print(st)
                    if not st.get("running"):
                        break
                    _t.sleep(10)
            except KeyboardInterrupt:
                MANAGER.stop()
                print("收到 Ctrl+C，正在停止（当前上报周期结束后退出，进度保留）…")
        elif args.cmd == "study-status":
            from .study import load_state
            st = load_state()
            if not st:
                print("没有刷课任务状态（从未运行过或文件缺失）")
            else:
                _print({"running": st.get("running"),
                        "current": st.get("current"),
                        "chapter_index": st.get("chapter_index"),
                        "video_progress": st.get("video_progress"),
                        "processed": len(st.get("results", []))})
        elif args.cmd == "study-stop":
            from .study import FileStopEvent
            FileStopEvent().set()
            _print({"status": "stopping",
                    "detail": "已写入停止标志，刷课进程将在当前上报周期后暂停"})
        elif args.cmd == "homeworks":
            from .work import list_homeworks
            _print(list_homeworks(client, args.course_id, args.class_id, args.cpi))
        elif args.cmd == "quizzes":
            from .work import list_quizzes
            _print(list_quizzes(client, args.course_id, args.class_id, args.cpi,
                                args.max_chapters))
        elif args.cmd == "quiz":
            from .work import fetch_quiz_questions
            _print(fetch_quiz_questions(client, args.course_id, args.class_id,
                                        args.cpi, args.knowledge_id, args.jobid))
        elif args.cmd == "inbox-folders":
            from .notice import list_folders
            _print(list_folders(client))
        elif args.cmd == "inbox":
            from .notice import list_folders, list_notices
            folder_uuid = ""
            if args.folder:
                folders = {f["name"]: f for f in list_folders(client)}
                if args.folder not in folders:
                    print(f"没有叫 {args.folder!r} 的文件夹，现有: {list(folders)}",
                          file=sys.stderr)
                    sys.exit(1)
                folder_uuid = folders[args.folder]["uuid"]
            _print(list_notices(client, folder_uuid=folder_uuid, keyword=args.kw,
                                limit=args.limit, unread_only=args.unread))
        elif args.cmd == "homework":
            from .work import fetch_homework
            _print(fetch_homework(client, args.work_id, args.detail_url))
        elif args.cmd == "materials":
            from .materials import list_materials
            _print(list_materials(client, args.course_id, args.class_id, args.cpi,
                                  args.max_chapters))
    except ChaoxingError as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
