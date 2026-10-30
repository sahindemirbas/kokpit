"""Approved publishing through the container API: create container, wait until FINISHED, then media_publish.

Only ``publish_slot`` publishes, and only when the module and the ``publish`` switch are on, a token exists and the slot
is ready or failed (the Approve action). Nothing here runs from a timer. A failure stores the platform's message on the
slot and marks it failed; there is no automatic retry. The token is never part of a message.
"""
from __future__ import annotations

import json
import math
import sqlite3
import time
from datetime import datetime, timezone

from ...config import Config
from . import plan
from .client import ApiError, Client
from .token import resolve


class PublishError(Exception):
    """A container or limit problem with a message that is safe to store and show."""


def _clean(client: Client, text) -> str:
    return client.clean(str(text))[:300]


def check_limit(client: Client) -> None:
    resp = client.get("me/content_publishing_limit", fields="quota_usage,config")
    item = (resp.get("data") or [{}])[0] or {}
    used, total = item.get("quota_usage"), (item.get("config") or {}).get("quota_total")
    if not isinstance(used, int) or not isinstance(total, int):
        raise PublishError("could not read the publishing limit, nothing was published")
    if used >= total:
        raise PublishError(f"publishing limit reached ({used} of {total} used in the current window); try again later")


def wait_finished(client: Client, container_id: str, interval: int, timeout: int, sleep) -> None:
    for _ in range(max(1, math.ceil(timeout / interval))):
        st = client.get(container_id, fields="status_code,status")
        code = st.get("status_code")
        if code == "FINISHED":
            return
        if code in ("ERROR", "EXPIRED"):
            raise PublishError(f"container {str(code).lower()}: {_clean(client, st.get('status') or 'no detail from the platform')}")
        sleep(interval)
    raise PublishError(f"timed out after {timeout} s waiting for the container; nothing was published, check the account before you retry")


def _create(client: Client, **params) -> str:
    cid = str(client.post("me/media", **params).get("id") or "")
    if not cid:
        raise PublishError("the platform answered without a container id")
    return cid


def build_container(client: Client, slot: dict, interval: int, timeout: int, sleep) -> str:
    urls, caption = slot["media_urls"], slot["caption"]
    extra = {"collaborators": json.dumps(slot["collabs"])} if slot["collabs"] else {}
    if not urls:
        raise PublishError("the slot has no media URL")
    kind = slot["kind"]
    if kind == "carousel":
        kids = []
        for u in urls:
            cid = _create(client, is_carousel_item="true", **({"media_type": "VIDEO", "video_url": u} if plan.is_video(u) else {"image_url": u}))
            wait_finished(client, cid, interval, timeout, sleep)
            kids.append(cid)
        return _create(client, media_type="CAROUSEL", children=",".join(kids), **({"caption": caption} if caption else {}), **extra)
    u = urls[0]
    if kind == "story":
        return _create(client, media_type="STORIES", **({"video_url": u} if plan.is_video(u) else {"image_url": u}))
    cap = {"caption": caption} if caption else {}
    if kind == "reel":
        return _create(client, media_type="REELS", video_url=u, share_to_feed="true", **cap, **extra)
    return _create(client, image_url=u, **cap, **extra)


def publish_slot(cfg: Config, conn: sqlite3.Connection, slot_id: int, opener=None, now: datetime | None = None, sleep=time.sleep) -> dict:
    """Approve and publish one slot. Raises PermissionError / LookupError / ValueError before any change; once the slot is
    claimed every platform problem ends as status failed with the message, never as an exception."""
    ig = cfg.instagram
    if not cfg.modules.get("instagram", False) or not ig["publish"]:
        raise PermissionError("publishing is off: set instagram = true under [modules] and publish = true under [instagram] in kokpit.toml")
    token = resolve(cfg)
    if not token:
        raise PermissionError("no token: set KOKPIT_INSTAGRAM_TOKEN or [instagram] token (permission instagram_business_content_publish is needed)")
    slot = plan.get(conn, slot_id)
    if slot is None:
        raise LookupError("no such slot")
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if not plan.claim(conn, slot_id, now):
        raise ValueError(f"only a ready or failed slot can be approved; this one is {slot['status']}")
    client = Client(token, opener)
    try:
        check_limit(client)
        container = build_container(client, slot, ig["poll_seconds"], ig["poll_timeout"], sleep)
        wait_finished(client, container, ig["poll_seconds"], ig["poll_timeout"], sleep)
        media_id = str(client.post("me/media_publish", creation_id=container).get("id") or "")
        if not media_id:
            raise PublishError("the platform answered without a media id; check the account before you retry")
        permalink = None
        try:
            permalink = client.get(media_id, fields="permalink").get("permalink")
        except ApiError:
            pass
        plan.mark_published(conn, slot_id, media_id, permalink, now)
    except (ApiError, PublishError) as exc:
        plan.mark_failed(conn, slot_id, _clean(client, exc), now)
    except Exception as exc:  # unexpected: still end in a visible state
        plan.mark_failed(conn, slot_id, _clean(client, f"unexpected {type(exc).__name__}"), now)
    return plan.get(conn, slot_id)
