"""超星收件箱（notice.chaoxing.com 站内通知）。

入口：直接访问 https://notice.chaoxing.com/pc/notice/myNotice 建立该域会话
接口（同源）：
- POST /pc/notice/getNoticeFolders  文件夹列表
- POST /pc/notice/getNoticeList     消息列表（响应在 notices.list，游标 notices.lastGetId）

老师发的作业/测验通知带结构化 attachment（attachmentType=25）：
{examOrWorkId, examOrWork: "work"/"exam", clazzId, courseId, title, url, content(时间)}
普通文本通知：content 正文、createrName 发件人、isread 已读、imgs 图片。
"""

import json

from .client import ChaoxingError, ChaoxingClient

NOTICE_HOST = "https://notice.chaoxing.com"


def _as_bool(value) -> bool:
    """兼容接口返回 0/1、布尔值或字符串形式的状态字段。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _ensure_session(client: ChaoxingClient) -> None:
    """确保已持有 notice.chaoxing.com 域的会话 cookie（冷启动走一遍入口）。

    个人门户不再内嵌带 ?s= 签名的收件箱链接（菜单改由 JS 异步加载），
    直接访问 myNotice 页面即可在 notice 域建立会话；未登录时会被跳回
    passport2，由 ChaoxingClient.follow 抛出 SessionExpired。
    """
    jar = client._client.cookies.jar
    if not any(getattr(c, "domain", "").endswith("notice.chaoxing.com") for c in jar):
        client.follow(f"{NOTICE_HOST}/pc/notice/myNotice",
                      headers={"Referer": "https://i.chaoxing.com/"})


def _raw_notices(client: ChaoxingClient, folder_uuid: str, keyword: str,
                 last_value: str, notice_type: str = "") -> dict:
    resp = client.request("POST", f"{NOTICE_HOST}/pc/notice/getNoticeList", data={
        "type": "2", "notice_type": notice_type, "lastValue": last_value,
        "sort": "", "folderUUID": folder_uuid, "kw": keyword,
        "startTime": "", "endTime": "", "gKw": "", "gName": "", "year": "",
        "tag": "", "fidsCode": "", "queryFolderNoticePrevYear": "",
        "filterSenderPuids": "",
    }, headers={"Content-Type": "application/x-www-form-urlencoded",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": f"{NOTICE_HOST}/pc/notice/myNotice"})
    body = resp.json()
    if not body.get("status"):
        raise ChaoxingError(f"获取消息列表失败: {body.get('msg')}")
    return body


def list_folders(client: ChaoxingClient) -> list[dict]:
    """收件箱文件夹列表（含用户自建文件夹与未读/总数）。"""
    _ensure_session(client)
    resp = client.request("POST", f"{NOTICE_HOST}/pc/notice/getNoticeFolders",
                          headers={"X-Requested-With": "XMLHttpRequest",
                                   "Referer": f"{NOTICE_HOST}/pc/notice/myNotice"})
    body = resp.json()
    if not body.get("status"):
        raise ChaoxingError(f"获取文件夹失败: {body.get('msg')}")
    return [{"name": f.get("folderName"), "uuid": f.get("uuid"),
             "notice_count": f.get("noticeCount", 0),
             "unread": f.get("noReadCount", 0)}
            for f in (body.get("msg") or [])]


def _parse_notice(item: dict) -> dict:
    """把原始通知归一化：作业类通知提取作业直达信息，文本类提取正文。"""
    base = {
        "id": item.get("idCode"),
        "sender": item.get("createrName"),
        "time": item.get("completeTime"),
        "is_read": _as_bool(item.get("isread", 0)),
        "type": "text",
    }
    raw_att = item.get("attachment")
    if raw_att:
        try:
            atts = json.loads(raw_att)
            att = next((a for a in atts if a.get("att_web")), None)
            if att:
                web = att["att_web"]
                base.update({
                    "type": web.get("examOrWork", "work"),  # work=作业 exam=考试
                    "title": web.get("title"),
                    "homework_id": web.get("examOrWorkId"),
                    "course_id": web.get("courseId"),
                    "class_id": web.get("clazzId"),
                    "publish_time": web.get("content"),
                    "url": web.get("url"),
                })
                # 登记 作业id -> 详情链接，get_homework(work_id) 可直接命中
                if web.get("examOrWorkId") and web.get("url"):
                    from . import work as _work
                    _work._LIST_CACHE.setdefault(str(web["examOrWorkId"]),
                                                 web["url"])
                return base
        except (ValueError, TypeError, KeyError):
            pass
    base["content"] = (item.get("content") or "")[:500]
    imgs = item.get("imgs") or []
    if imgs:
        base["images"] = [i.get("imgUrl") for i in imgs if i.get("imgUrl")]
    return base


def list_notices(client: ChaoxingClient, folder_uuid: str = "", keyword: str = "",
                 limit: int = 50, unread_only: bool = False) -> list[dict]:
    """拉取通知列表（自动翻页到 limit 条），归一化输出。"""
    _ensure_session(client)
    out, last_value = [], ""
    for _ in range(20):  # 翻页保护
        body = _raw_notices(client, folder_uuid, keyword, last_value)
        notices = body.get("notices") or {}
        items = notices.get("list") or []
        for item in items:
            parsed = _parse_notice(item)
            if unread_only and parsed.get("is_read"):
                continue
            out.append(parsed)
        if len(out) >= limit:
            out = out[:limit]
            break
        last_value = notices.get("lastGetId") or ""
        if notices.get("lastPage") or not last_value or not items:
            break
    return out
