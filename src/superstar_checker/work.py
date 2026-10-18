"""作业模块：列表、抓题、提交、批阅成绩。

提交采用 new_xxt 验证过的做法：先 GET 作答页收集 <form> 全量隐藏字段
（standardEnc / enc_work / token 等），再把答案回填 POST 到 form action。
"""

import re
from urllib.parse import urljoin

from .client import ChaoxingClient, ChaoxingError
from .decode import parse_work_list, parse_work_page, parse_work_score

MOOC1 = "https://mooc1.chaoxing.com"

# MCP server 进程内缓存：work_id -> 作答页状态
_WORK_CACHE: dict[str, dict] = {}
# work_id -> 详情页 URL（来自列表）
_LIST_CACHE: dict[str, str] = {}
# (course_id, class_id) -> 已扫描的章节测验列表（增量缓存）
_QUIZ_CACHE: dict[tuple, list[dict]] = {}


def clear_caches() -> None:
    """清除与当前登录账号绑定的作业、通知和测验缓存。"""
    _WORK_CACHE.clear()
    _LIST_CACHE.clear()
    _QUIZ_CACHE.clear()


def get_work_enc(client: ChaoxingClient, course_id, clazz_id, cpi) -> str:
    """课程中间页 302 链后，从最终页面提取 workEnc。"""
    resp = client.follow(f"{MOOC1}/visit/stucoursemiddle", params={
        "courseid": course_id, "clazzid": clazz_id, "cpi": cpi,
        "ismooc2": "1", "v": "2",
    }, headers={"Referer": "https://mooc2-ans.chaoxing.com/mooc2-ans/visit/interaction"})
    match = re.search(r'id="workEnc"[^>]*value="([^"]+)"', resp.text) or \
        re.search(r"value=\"([0-9a-f]{32})\"[^>]*id=\"workEnc\"", resp.text)
    if not match:
        # 兜底：302 目标 URL 里的 enc= 签名
        match = re.search(r"enc=([0-9a-f]{32})", str(resp.url))
    if not match:
        raise ChaoxingError("未能从课程页提取 workEnc（页面结构可能变化）")
    return match.group(1)


def list_homeworks(client: ChaoxingClient, course_id, clazz_id, cpi) -> list[dict]:
    enc = get_work_enc(client, course_id, clazz_id, cpi)
    resp = client.follow(f"{MOOC1}/mooc2/work/list", params={
        "courseId": course_id, "classId": clazz_id, "cpi": cpi,
        "ut": "s", "enc": enc, "pageNum": "1",
    }, headers={"Referer": "https://mooc2-ans.chaoxing.com/mooc2-ans/mycourse/stu"})
    works = parse_work_list(resp.text)
    for w in works:
        if w["work_id"] and w["detail_url"]:
            _LIST_CACHE[w["work_id"]] = w["detail_url"]
    return works


def fetch_homework(client: ChaoxingClient, work_id: str,
                   detail_url: str = "") -> dict:
    """抓取题目并缓存作答页表单状态（提交时回填用）。"""
    url = detail_url or _LIST_CACHE.get(work_id)
    if not url:
        raise ChaoxingError(
            f"没有作业 {work_id} 的详情页地址。请先调用 get_homeworks 或 "
            f"get_inbox_notices（收件箱通知里已自动登记链接），再重试；"
            f"或把通知里的作业直达链接作为 detail_url 传入")
    page_url = url if url.startswith("http") else urljoin(MOOC1, url)
    resp = client.follow(page_url, headers={"Referer": f"{MOOC1}/mooc2/work/list"})
    parsed = parse_work_page(resp.text)
    if not parsed["questions"]:
        raise ChaoxingError("作业页未解析到题目（可能已提交/已关闭，或页面结构变化）")
    _WORK_CACHE[work_id] = {
        "action": parsed["action"],
        "hidden": parsed["hidden"],
        "questions": parsed["questions"],
        "page_url": str(resp.url),
    }
    return {"work_id": work_id, "questions": parsed["questions"]}


_TRUE_WORDS = {"true", "对", "正确", "t", "√", "a", "yes", "1", "是"}
_FALSE_WORDS = {"false", "错", "错误", "f", "×", "x", "b", "no", "0", "否"}


def _format_answer(qtype: str, raw) -> str:
    if isinstance(raw, (list, tuple)):
        raw = "".join(str(x) for x in raw)
    text = str(raw).strip()
    if qtype in ("0", "1"):  # 单选/多选：只保留选项字母
        return "".join(re.findall(r"[A-Za-z]", text)).upper()
    if qtype == "3":  # 判断：true/false
        low = text.lower()
        if low in _TRUE_WORDS or low.startswith("t") or low.startswith("对"):
            return "true"
        if low in _FALSE_WORDS or low.startswith("f") or low.startswith("错"):
            return "false"
        raise ChaoxingError(f"判断题答案无法识别：{text!r}（请给 true/false）")
    if qtype in ("4",):  # 填空：多空用 #分隔 传入时用 | 分隔
        parts = [p.strip() for p in text.split("|")]
        return "<p>" + "</p><p>".join(parts) + "</p>"
    # 简答/论述/其他：包一层 HTML
    return "<p>" + text + "</p><br/>"


def save_homework(client: ChaoxingClient, work_id: str,
                  answers: dict[str, str]) -> dict:
    """仅暂存作业/测验答案（pyFlag=1）。

    出于风险控制，本工具【不支持自动提交】：暂存后请在学习通 App/网页中
    人工核对答案并手动提交。
    """
    state = _WORK_CACHE.get(work_id)
    if not state:
        raise ChaoxingError(
            f"内存中没有作业 {work_id} 的作答状态，请先调用 get_homework/get_quiz_questions")

    data = dict(state["hidden"])
    qtypes = {q["id"]: q["type"] for q in state["questions"]}
    unknown = [qid for qid in answers if qid not in qtypes]
    if unknown:
        raise ChaoxingError(f"以下题目 id 不在本次作业中: {unknown}")

    missing = [q["id"] for q in state["questions"] if q["id"] not in answers]
    if missing:
        raise ChaoxingError(f"以下题目未提供答案: {missing}")

    for qid, raw in answers.items():
        qtype = qtypes.get(qid, "")
        data[f"answertype{qid}"] = qtype
        data[f"answer{qid}"] = _format_answer(qtype, raw)
    data["answerwqbid"] = ",".join(q["id"] for q in state["questions"]) + ","
    data["pyFlag"] = "1"  # 永远仅暂存；自动提交已在服务端禁用

    action = state["action"] or "/work/addStudentWorkNewWeb"
    post_url = action if action.startswith("http") else urljoin(state["page_url"], action)
    resp = client.request("POST", post_url, data=data, headers={
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": state["page_url"],
    })
    body_text = resp.text
    if "验证码" in body_text:
        raise ChaoxingError("保存触发风控验证码，请稍后再试")
    try:
        body = resp.json()
        ok = body.get("status") is True
        return {"status": ok, "saved_only": True, "response": body}
    except ValueError:
        snippet = re.sub(r"\s+", " ", body_text)[:500]
        return {"status": resp.status_code == 200, "saved_only": True,
                "http": resp.status_code, "response_excerpt": snippet}


def list_quizzes(client: ChaoxingClient, course_id, clazz_id, cpi,
                 max_chapters: int = 5, rescan: bool = False) -> list[dict]:
    """扫描章节任务点，列出章节测验（work 类附件）。

    结果按课程缓存，增量扫描；标题带"测验/测试/练习"的章节优先。
    单次调用扫描 max_chapters 个章节（默认 5，避免 MCP 客户端 30s 超时），
    可重复调用直到返回数量不再增加（即扫描完毕）。
    """
    from .study import fetch_card_attachments, fetch_chapters
    from .decode import attachment_kind

    # cpi 和 uid 都是上下文的一部分，避免跨学期或切换账号后复用旧结果。
    key = (str(client.uid), str(course_id), str(clazz_id), str(cpi))
    cached = _QUIZ_CACHE.setdefault(key, [])
    scanned_kids = {q["knowledge_id"] for q in cached}
    if rescan:
        cached.clear()
        scanned_kids.clear()

    chapters = fetch_chapters(client, course_id, clazz_id, cpi)
    todo_all = [c for c in chapters
                if c["knowledge_id"] not in scanned_kids and c["job_count"] > 0]
    # 标题带测验关键词的章节优先扫描（命中率最高，小批量也能先出结果）
    kw_rank = ("测验", "测试", "练习", "作业", "quiz", "test")
    todo_all.sort(key=lambda c: 0 if any(k in c["title"].lower() for k in kw_rank) else 1)
    todo = todo_all[: max(1, min(max_chapters, 200))]
    for ch in todo:
        try:
            atts, _ = fetch_card_attachments(client, course_id, clazz_id,
                                             cpi, ch["knowledge_id"])
        except Exception:
            continue
        for att in atts:
            if attachment_kind(att) == "work" or str(att.get("jobid", "")).startswith("work-"):
                prop = att.get("property") or {}
                cached.append({
                    "knowledge_id": ch["knowledge_id"],
                    "chapter": ch["title"],
                    "name": prop.get("name") or prop.get("title") or "",
                    "jobid": att.get("jobid"),
                    "enc": att.get("enc"),
                    "isPassed": att.get("isPassed"),
                })
        scanned_kids.add(ch["knowledge_id"])
    return cached


def fetch_quiz_questions(client: ChaoxingClient, course_id, clazz_id, cpi,
                         knowledge_id: str, jobid: str) -> dict:
    """抓取章节测验的题目（只读，不提交）。"""
    from .study import fetch_card_attachments
    from urllib.parse import urljoin as _urljoin

    atts, defaults = fetch_card_attachments(client, course_id, clazz_id,
                                            cpi, knowledge_id)
    att = next((a for a in atts if a.get("jobid") == jobid), None)
    if att is None:
        raise ChaoxingError(f"章节 {knowledge_id} 下没有任务点 {jobid}")
    work_id = jobid.replace("work-", "")
    resp = client.follow(f"{MOOC1}/mooc-ans/api/work", params={
        "api": "1", "workId": work_id, "jobid": jobid, "originJobId": jobid,
        "needRedirect": "true", "skipHeader": "true",
        "knowledgeid": knowledge_id, "ktoken": defaults.get("ktoken", ""),
        "cpi": cpi, "ut": "s", "clazzId": clazz_id, "type": "",
        "enc": att.get("enc", ""), "mooc2": "1", "courseid": course_id,
    }, headers={"Referer": f"{MOOC1}/mooc-ans/mycourse/studentstudy"})
    parsed = parse_work_page(resp.text)
    if not parsed["questions"]:
        raise ChaoxingError("未解析到题目（可能已作答完毕或页面结构变化）")
    _WORK_CACHE[jobid] = {
        "action": parsed["action"], "hidden": parsed["hidden"],
        "questions": parsed["questions"], "page_url": str(resp.url),
        "kind": "quiz",
    }
    return {"knowledge_id": knowledge_id, "jobid": jobid,
            "name": (att.get("property") or {}).get("name") or "",
            "questions": parsed["questions"]}


def get_homework_score(client: ChaoxingClient, course_id, clazz_id, cpi,
                       work_id: str, work_answer_id: str = "") -> dict:
    """查已批阅/已提交作业的得分与正确答案。

    流程：record-list（校验+次数）→ record-detail（AJAX 接口，含每题对错与正确答案）。
    work_answer_id 缺省时会自动从缓存的作答状态或详情页提取。
    """
    if not work_answer_id:
        state = _WORK_CACHE.get(work_id) or _WORK_CACHE.get(f"work-{work_id}")
        if state:
            work_answer_id = state["hidden"].get("workAnswerId", "")
    if not work_answer_id:
        url = _LIST_CACHE.get(work_id)
        if url:
            page = url if url.startswith("http") else urljoin(MOOC1, url)
            resp = client.follow(page, headers={"Referer": f"{MOOC1}/mooc2/work/list"})
            m = re.search(r'id="workAnswerId"[^>]*value="([^"]+)"', resp.text)
            if m and m.group(1):
                work_answer_id = m.group(1)
            else:
                # 已提交待批阅的"查看页"里字段名叫 answerId
                m2 = re.search(r'id="answerId"[^>]*value="([^"]+)"', resp.text)
                if m2:
                    work_answer_id = m2.group(1)
    if not work_answer_id:
        return {"error": "缺少 workAnswerId：请先对该作业调用一次 get_homework，"
                         "或从收件箱通知链接进入后重试"}

    resp = client.follow(f"{MOOC1}/mooc-ans/work/record-list", params={
        "courseId": course_id, "classId": clazz_id, "workId": work_id,
        "workAnswerId": work_answer_id, "cpi": cpi,
        "api": "1", "mooc2": "1", "ut": "s",
    }, headers={"Referer": f"{MOOC1}/mooc2/work/list"})
    if "作业记录已被删除" in resp.text:
        return {"error": "作答记录不存在（可能未提交过，或参数不匹配）"}

    detail = client.follow(f"{MOOC1}/mooc-ans/work/record-detail", params={
        "courseId": course_id, "classId": clazz_id, "workId": work_id,
        "workAnswerId": work_answer_id, "times": "0", "cpi": cpi,
        "ut": "s", "isdisplaytable": "0", "firstHeader": "2",
        "isWork": "false", "workSystem": "0", "api": "1",
        "archive": "false", "mooc2": "1",
    }, headers={"Referer": str(resp.url)})
    if detail.status_code != 200:
        return {"error": f"批阅详情获取失败（HTTP {detail.status_code}）"}
    result = parse_work_score(detail.text)
    # 从列表页补充总分（record-detail 页可能没有汇总分数）
    scores = re.findall(r'viewScore">([\d.]+)分', resp.text)
    result["attempt_scores"] = scores
    return result
