"""课程资料（章节附件）下载直链获取。"""

from .client import ChaoxingClient
from .decode import attachment_kind
from .study import MOOC1, fetch_card_attachments, fetch_chapters


def _object_status(client: ChaoxingClient, object_id: str) -> dict:
    resp = client.follow(f"{MOOC1}/ananas/status/{object_id}", params={
        "k": client.fid, "flag": "normal",
    }, headers={"Referer": f"{MOOC1}/ananas/modules/document/index.html"})
    if resp.status_code != 200:
        return {}
    try:
        return resp.json()
    except ValueError:
        return {}


def list_materials(client: ChaoxingClient, course_id, clazz_id, cpi,
                   max_chapters: int = 30, max_items: int = 100) -> list[dict]:
    """遍历章节卡片，收集附件的下载直链（带会话 cookie 访问防 403）。"""
    chapters = fetch_chapters(client, course_id, clazz_id, cpi)
    seen, results = set(), []
    for ch in chapters[:max_chapters]:
        if len(results) >= max_items:
            break
        try:
            attachments, _ = fetch_card_attachments(
                client, course_id, clazz_id, cpi, ch["knowledge_id"])
        except Exception:
            continue
        for att in attachments:
            if len(results) >= max_items:
                break
            kind = attachment_kind(att)
            if kind not in ("document", "media", "video", "audio", "other"):
                continue
            prop = att.get("property") or {}
            object_id = att.get("objectId") or (prop.get("objectid") if isinstance(prop, dict) else "")
            if not object_id or object_id in seen:
                continue
            seen.add(object_id)
            name = (prop.get("name") or prop.get("title") or object_id) if isinstance(prop, dict) else object_id
            meta = _object_status(client, object_id)
            url = meta.get("download") or meta.get("pdf") or meta.get("preview") or ""
            results.append({
                "name": name,
                "kind": kind,
                "chapter": ch["title"],
                "url": url,
                "filesize": meta.get("filesize", ""),
                "downloadable": bool(meta.get("download") or meta.get("pdf")),
            })
    return results
