"""예매 오픈을 기다리다 로그인이 풀리면 폰 알림 → 다시 로그인 기다림 → 이어서 찾기 (가짜 브라우저로 시험)."""
import asyncio
import json
import sys
import tempfile
from pathlib import Path

import megabox_helper as m

ok = 0


def check(name, got, want):
    global ok
    good = got == want
    ok += good
    print(("통과 " if good else "실패 ") + name, got if not good else "")


class Page:
    def __init__(self):
        self.context = self
        self.pages = [self]
        self.gotos = []

    async def goto(self, url, **k):
        self.gotos.append(url)

    async def reload(self, **k):
        pass

    async def wait_for_event(self, *a, **k):
        pass


class Ctx:
    def __init__(self, page):
        self.pages = [page]


class PW:
    def __init__(self, page):
        self.chromium = self
        self.page = page

    async def launch_persistent_context(self, *a, **k):
        return Ctx(self.page)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass


def run(logins, schedules, login_url=True):
    """logins: is_logged_in 이 차례로 돌려줄 값, schedules: pick_schedule 이 차례로 돌려줄 값."""
    page, alerts, calls = Page(), [], {"login": 0}
    logins, schedules = list(logins), list(schedules)

    class Site:
        NAME = "가짜"
        URL = "https://example/booking"
        LOGIN_URL = "https://example/login" if login_url else None

        @staticmethod
        async def is_logged_in(p):
            calls["login"] += 1
            v = logins.pop(0) if logins else True
            if v == "err":
                raise RuntimeError("Execution context was destroyed")
            return v

        @staticmethod
        async def pick_schedule(p, c):
            return schedules.pop(0) if schedules else None

        @staticmethod
        async def open_show(p, s):
            pass

        @staticmethod
        async def choose_seats(p, c):
            return ["G7", "G8"], "1순위"

    cfg = Path(tempfile.mkdtemp()) / "c.json"
    cfg.write_text(json.dumps({"cinema": "megabox", "movie": "a", "region": "서울", "branch": "b", "date": "2026-11-02",
                               "people": 2, "wait_open": True}), encoding="utf-8")
    real_sleep = asyncio.sleep
    m.SITES["megabox"] = Site
    m.async_playwright = lambda: PW(page)
    m.phone_alert = alerts.append
    m.beep = lambda: None
    m.asyncio.sleep = lambda s: real_sleep(0)
    sys.argv = ["x", "--config", str(cfg)]
    try:
        asyncio.run(m.main())
    finally:
        m.asyncio.sleep = real_sleep
    return page.gotos, alerts


SHOW = [{"id": "1", "start": "1800", "rest": 10, "title": "a", "text": "18:00 a"}]

# 처음 로그인됨 → 1번째 확인 회차 없음 → 새로고침 뒤 로그인 풀림(두 번 확인 모두 False) → 로그인 기다림(False 한 번 뒤 True) → 회차 찾음
gotos, alerts = run([True, False, False, False, True], [None, SHOW])
check("풀리면 로그인 화면으로", gotos.count("https://example/login"), 1)
check("폰 알림 1번(풀림) + 1번(좌석)", (len(alerts), "로그인이 풀렸어요" in alerts[0]), (2, True))
check("다시 로그인 뒤 예매 화면으로", gotos[-1], "https://example/booking")

# 한 번만 False(화면이 덜 그려짐) → 두 번째 확인에서 True → 로그인 화면으로 안 감
gotos, alerts = run([True, False, True], [None, SHOW])
check("잠깐 False 는 무시", gotos.count("https://example/login"), 0)
check("그때 알림은 좌석만", len(alerts), 1)

# 판정이 흔들림(풀림↔로그인 반복) → 풀림 알림은 30분 안에 1번만(서버 하루 10건을 좌석 알림 몫으로 남김)
gotos, alerts = run([True] + [False, False, True] * 4, [None] * 4 + [SHOW])
check("흔들려도 풀림 알림 1번", sum("로그인이 풀렸어요" in a for a in alerts), 1)

# 로그인 버튼으로 화면이 바뀌는 중 오류 → 죽지 않고 '아직'으로 봄, LOGIN_URL 없는 사이트(메가박스·롯데)는 그 화면에서 기다림
gotos, alerts = run([True, "err", "err", "err", True], [None, SHOW], login_url=False)
check("이동 중 오류에도 이어감", len(alerts), 2)
check("로그인 주소 없으면 그대로", [g for g in gotos if "login" in g], [])

print(f"{ok}/8 통과"); sys.exit(0 if ok == 8 else 1)
