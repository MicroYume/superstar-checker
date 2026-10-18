"""超星页面/JSON 响应解析。

超星大量接口返回 HTML 或"HTML 里内嵌 JS 对象"，这里统一负责解析成结构化数据。
解析规则基于 Samueli924/chaoxing、chaoxing-homework-checker/API.md 等开源实现交叉验证。
"""

import json
import re
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup

from . import secret_font

# 题型代码（页面 data 属性原始值）：0单选 1多选 2简答(问答) 3判断 4填空 5名词解释
# 6论述 7计算 8其他 9分录 10资料 11连线 14完形填空 15阅读理解
QUESTION_TYPE_NAMES = {
    "0": "单选", "1": "多选", "2": "简答", "3": "判断", "4": "填空",
    "5": "名词解释", "6": "论述", "7": "计算", "8": "其他", "9": "分录",
    "10": "资料", "11": "连线", "14": "完形填空", "15": "阅读理解",
}


# ---- 课程 ----

def parse_courses(clazz_json: dict) -> list[dict]:
    """解析 mycourse/backclazzdata 的 JSON，返回课程列表。"""
    courses = []
    for channel in clazz_json.get("channelList") or []:
        content = channel.get("content") or {}
        course_data = (content.get("course") or {}).get("data") or []
        if not course_data:
            continue  # 群聊频道等无课程条目
        course = course_data[0]
        courses.append({
            "course_id": course.get("id"),
            "name": course.get("name", ""),
            "teacher": course.get("teacherfactor", ""),
            "class_id": content.get("id"),
            "cpi": content.get("cpi"),
            "isretire": content.get("isretire", 0),
            "end_date": content.get("endDate", ""),
            "active": content.get("isretire", 0) == 0,
        })
    return courses


# ---- 章节 ----

def parse_chapters(html: str) -> list[dict]:
    """解析 mooc2-ans/mycourse/studentcourse 页面，返回章节树（拍平）。"""
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for unit in soup.select("div.chapter_unit"):
        chapter_title = ""
        chapter_head = unit.find(["h2", "h3", "div"], class_=re.compile("chapter"))
        if chapter_head:
            chapter_title = chapter_head.get_text(" ", strip=True)
        for li in unit.find_all("li"):
            knowledge_id = None
            for div in li.find_all("div", id=re.compile(r"^cur\d+$")):
                knowledge_id = re.match(r"^cur(\d+)$", div.get("id", "")).group(1)
                break
            if not knowledge_id:
                continue
            title_el = li.select_one("a.clicktitle") or li.find("a")
            title = title_el.get_text(" ", strip=True) if title_el else ""
            # 去掉前缀序号中的纯空白
            job_input = li.find("input", class_="knowledgeJobCount")
            job_count = job_input.get("value", "0") if job_input else "0"
            tips = li.select_one("span.bntHoverTips")
            tips_text = tips.get_text(strip=True) if tips else ""
            items.append({
                "knowledge_id": knowledge_id,
                "title": re.sub(r"\s+", " ", title),
                "chapter": re.sub(r"\s+", " ", chapter_title),
                "job_count": int(job_count or 0),
                "finished": "已完成" in tips_text,
            })
    return items


# ---- 章节卡片（任务点） ----

def parse_marg(html: str) -> dict | None:
    """从 knowledge/cards 页面提取内嵌的 mArg JSON 对象。"""
    match = re.search(r"mArg\s*=\s*\{(.*?)\};", html, re.S)
    if not match:
        return None
    # 不要删除整段文本中的空格：字段值（课件名、题目、URL）可能包含空格。
    raw = "{" + match.group(1) + "}"
    try:
        return json.loads(raw)
    except ValueError:
        # 宽松处理：去掉尾逗号再试
        raw = re.sub(r",\}", "}", re.sub(r",\]", "]", raw))
        try:
            return json.loads(raw)
        except ValueError:
            return None


def attachment_kind(att: dict) -> str:
    """把 attachment 映射为 video/audio/document/work/read/other。"""
    if att.get("card_type") == "workid" or att.get("type") == "workid" or att.get("workid"):
        return "work"
    prop = att.get("property") or {}
    if att.get("type") in ("video",) or (isinstance(prop, dict) and prop.get("type") == "video"):
        return "video"
    if att.get("type") in ("audio",) or (isinstance(prop, dict) and prop.get("type") == "audio"):
        return "audio"
    if att.get("type") in ("document", "doc") or "document" in str(att.get("card_type", "")):
        return "document"
    if att.get("type") == "read" or att.get("card_type") == "read":
        return "read"
    # 兜底：有 objectId 且带 job 的当视频/音频处理（dtype 由 ananas status 判断）
    if att.get("objectId") and att.get("job"):
        return "media"
    return "other"


def otherinfo_node_id(att: dict) -> str:
    """从 otherInfo 中提取 knowledgeid（nodeId_xxx-cpi_yyy）。"""
    info = att.get("otherInfo") or att.get("otherinfo") or ""
    match = re.search(r"nodeId_(\d+)", str(info))
    return match.group(1) if match else ""


def trim_otherinfo(att: dict) -> str:
    """上报时 otherInfo 截断到第一个 & 之前。"""
    info = str(att.get("otherInfo") or att.get("otherinfo") or "")
    return info.split("&", 1)[0]


# ---- 作业 ----

# 学生端表示"还没有有效提交"的状态文案（"待批阅"属于已提交，不在其中）
_UNDONE_STATUSES = ("未交", "未做", "未完成", "缺交", "待重做", "未开始")


def parse_work_list(html: str) -> list[dict]:
    """解析 mooc2/work/list 页面，返回作业列表。

    兼容两种条目结构：
    - 旧版/API.md：li 带 data 属性（详情页 URL，含 workId/answerId）
    - 新版：.bottomList ul li，详情 URL 在 a[href] 或 onclick 里
    """
    soup = BeautifulSoup(html, "html.parser")
    works, seen = [], set()

    def add_from_url(url: str, scope) -> None:
        query = parse_qs(urlparse(url).query)
        work_id = (query.get("workId") or [""])[0]
        if not work_id or work_id in seen:
            return
        seen.add(work_id)
        title_el = scope.select_one(".overHidden2") or scope.select_one(".titTxt a")
        status_el = scope.select_one(".status") or scope.select_one("p.status")
        time_el = scope.select_one(".time") or scope.select_one(".pt5")
        status_text = status_el.get_text(strip=True) if status_el else ""
        works.append({
            "work_id": work_id,
            "answer_id": (query.get("answerId") or [""])[0],
            "detail_url": url,
            "title": title_el.get_text(" ", strip=True) if title_el else "",
            "status": status_text,
            "deadline": time_el.get_text(" ", strip=True) if time_el else "",
            # .tag 图标只区分是否过期，判断已交只能靠状态文案
            "done": bool(status_text)
                    and not any(s in status_text for s in _UNDONE_STATUSES),
            "is_reviewed": "已批阅" in status_text or "已批改" in status_text,
        })

    # 结构一：li[data]（旧版）
    for li in soup.find_all("li", attrs={"data": re.compile(r"workId")}):
        add_from_url(li.get("data", ""), li)
    # 结构二：任意 li，其内链接/onclick 带 workId（新版 .bottomList 等）
    if not works:
        for li in soup.find_all("li"):
            url = ""
            a = li.find("a", href=re.compile(r"workId="))
            if a:
                url = a.get("href", "")
            else:
                onclick = li.find(attrs={"onclick": re.compile(r"workId")})
                if onclick:
                    m = re.search(r"['\"]([^'\"]*workId[^'\"]*)['\"]",
                                  onclick.get("onclick", ""))
                    if m and m.group(1).startswith("/"):
                        url = m.group(1)
            if url:
                add_from_url(url if url.startswith("http") else urljoin("https://mooc1.chaoxing.com", url), li)
    return works


def parse_work_page(html: str) -> dict:
    """解析作业作答页：表单隐藏字段 + action + 题目列表（自动解字体反爬）。"""
    soup = BeautifulSoup(html, "html.parser")
    dst_fontmap = None
    if secret_font.is_encrypted_page(html):
        embedded = secret_font.extract_embedded_font(html)
        if embedded:
            try:
                dst_fontmap = secret_font._font_to_map(embedded)
            except Exception:
                dst_fontmap = None

    def _clean(text: str) -> str:
        text = re.sub(r"\s+", " ", text).strip()
        if dst_fontmap:
            text = secret_font.decrypt(dst_fontmap, text)
        return text

    # 选真正的作答提交表单：页面上可能有"题目纠错"等无关表单，
    # 以 action 含 addStudentWorkNew / 表单内含答案输入为准，都未命中再退回首表单
    form = None
    for f in soup.find_all("form"):
        if "addStudentWorkNew" in (f.get("action", "") or ""):
            form = f
            break
    if form is None:
        for f in soup.find_all("form"):
            if f.find("input", id="answerwqbid") or \
                    f.find("input", attrs={"name": re.compile(r"^answertype")}):
                form = f
                break
    if form is None:
        form = soup.find("form")
    action = form.get("action", "") if form else ""
    hidden = {}
    if form:
        for inp in form.find_all("input", attrs={"type": "hidden"}):
            name = inp.get("name") or inp.get("id")
            if name:
                hidden[name] = inp.get("value", "")

    questions = []
    for block in soup.select("div.singleQuesId") or soup.select("div.questionLi"):
        outer_data = block.get("data", "")  # 题目 id
        timu = block.select_one("div.TiMu") or block.select_one("div.Cy_TItle")
        qtype = ""
        if timu and timu.get("data"):
            qtype = str(timu.get("data")).split(",")[0].strip()
        title_el = block.select_one("div.Zy_TItle") or block.select_one("div.Cy_TItle") or timu
        title = _clean(title_el.get_text(" ", strip=True) if title_el
                       else block.get_text(" ", strip=True))
        options = []
        for li in block.select("ul > li") or block.select(".answerBg"):
            text = li.get("aria-label") or li.get_text(" ", strip=True)
            text = _clean(text)
            # 页面 UI 拼在选项后面的冗余字样
            text = re.sub(r"(选择|正确答案)$", "", text).strip()
            if text:
                options.append(text)
        answer_input = block.find("input", id=re.compile(r"^answertype"))
        if answer_input and not qtype:
            qtype = answer_input.get("value", "")
        questions.append({
            "id": outer_data,
            "type": qtype,
            "type_name": QUESTION_TYPE_NAMES.get(qtype, f"未知({qtype})"),
            "title": title,
            "options": options,
        })
    return {"action": action, "hidden": hidden, "questions": questions}


def parse_work_score(html: str) -> dict:
    """解析已批阅作业详情页：总分 + 每题我的答案/正确答案（尽力解析）。"""
    soup = BeautifulSoup(html, "html.parser")
    result = {"total_score": "", "questions": []}
    total = soup.select_one("span.resultNum") or soup.select_one(".score,.finalScore")
    if total:
        result["total_score"] = total.get_text(strip=True)
    for timu in soup.select("div.TiMu"):
        q = {"title": re.sub(r"\s+", " ", timu.get_text(" ", strip=True))[:200]}
        green = timu.find_parent().select_one(".colorGreen") if timu.find_parent() else None
        deep = timu.find_parent().select_one(".colorDeep") if timu.find_parent() else None
        if green:
            q["correct_answer"] = re.sub(r"\s+", " ", green.get_text(" ", strip=True))
        if deep:
            q["my_answer"] = re.sub(r"\s+", " ", deep.get_text(" ", strip=True))
        result["questions"].append(q)
    # 页面无 TiMu 时的兜底：整页抽取"正确答案"附近文本
    if not result["questions"]:
        text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
        result["raw_excerpt"] = text[:3000]
    return result
