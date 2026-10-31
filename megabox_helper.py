"""영화 예매 도우미(메가박스·롯데시네마·CGV) — 영화·지점·날짜·시간·좌석까지 고르고 결제 직전에 멈춘다.

- 로그인은 처음 한 번 열린 창에서 직접 한다(비밀번호를 프로그램이 다루지 않음).
  로그인 상태는 profile\\ 폴더(CGV 는 profile_cgv\\)에 남아 다음부터는 다시 안 해도 된다.
- CGV 만 설치된 Chrome 창으로 연다(Cloudflare 가 Playwright Chromium·창 없는 실행을 막음, --dry 도 창이 뜸).
- 좌석 화면의 '다음'(로봇 확인 + 결제 창)은 프로그램이 누르지 않는다. 사람이 누른다.
- 좌석은 seats 의 1~10순위(인원 수만큼 좌석 이름)를 차례로 보고, 다 찼으면 rows 줄에서 자동으로 고른다.
- 예매 오픈 기다리기(wait_open)는 check_every_sec 간격(최소 30초)으로만 확인한다.

사용: python megabox_helper.py            (config.json 대로 실행)
      python megabox_helper.py --dry      (시간표에서 고를 회차만 보여 주고 멈춤, 로그인 불필요)
      python megabox_helper.py --config 다른설정.json
"""
import argparse
import asyncio
import json
import re
import sys
import time
import urllib.request
import winsound
from pathlib import Path

from playwright.async_api import async_playwright

import cgv
import lotte

HERE = Path(__file__).resolve().parent
URL = "https://www.megabox.co.kr/booking"


def log(msg):
    print(msg, flush=True)


def load_config(path):
    c = json.loads(Path(path).read_text(encoding="utf-8"))
    c["cinema"] = c.get("cinema", "megabox")
    if c["cinema"] not in ("megabox", "lotte", "cgv"):
        sys.exit("cinema 는 megabox, lotte, cgv 만 쓸 수 있어요.")
    c["date_data"] = c["date"].replace("-", ".")          # 2026.10.30
    for k in ("movie", "region", "branch", "format_keyword"):
        if re.search(r'["\\]', str(c.get(k, ""))):     # 화면 선택자에 들어가는 값
            sys.exit(f"{k} 에는 \" 와 \\ 를 쓸 수 없어요.")
    c["people"] = max(1, min(8, int(c.get("people", 1))))
    c["check_every_sec"] = max(30, int(c.get("check_every_sec", 60)))
    c["times"] = [t.replace(":", "") for t in c.get("times", [])]
    c["rows"] = [r.upper() for r in c.get("rows", [])]
    # 원하는 좌석 1~10순위: ["G7 G8", "H7 H8", ...] — 한 순위에 인원 수만큼 좌석 이름
    prefs = c.get("seats", [])
    if len(prefs) > 10:
        sys.exit("seats 는 10순위까지만 적을 수 있어요.")
    c["seats"] = []
    for i, p in enumerate(prefs, 1):
        names = [n.upper() for n in (p if isinstance(p, list) else re.split(r"[\s,]+", p)) if n]
        if len(names) != c["people"] or not all(re.fullmatch(r"[A-Z]+\d+", n) for n in names):
            sys.exit(f"seats {i}순위 '{p}' 는 좌석 이름(예: G7)을 인원 수({c['people']}개)만큼 적어 주세요.")
        # "G07" → "G7"(좌석표의 seatNo 는 앞의 0 이 없음)
        c["seats"].append([re.sub(r"(?<=[A-Z])0+(?=\d)", "", n) for n in names])
    return c


async def popup_text(frame):
    """보이는 알림창 글자(없으면 '')."""
    return await frame.evaluate(
        "()=>{const s=[...document.querySelectorAll('section.alert-popup')].find(e=>e.offsetParent);"
        "return s? s.innerText.trim() : ''}")


async def close_popup(frame, prefer="확인"):
    """보이는 알림창이 있으면 prefer 버튼(없으면 아무 버튼)을 누르고 글자를 돌려준다."""
    text = await popup_text(frame)
    if not text:
        return ""
    btns = frame.locator("section.alert-popup:visible .btn-group button")
    n = await btns.count()
    target = None
    for i in range(n):
        if (await btns.nth(i).inner_text()).strip() == prefer:
            target = btns.nth(i)
    if target is None and n:
        target = btns.nth(n - 1)
    if target is not None:
        await target.click()
        await asyncio.sleep(0.6)
    return text


async def is_logged_in(page):
    return await page.evaluate("()=>[...document.querySelectorAll('a')].some(a=>a.innerText.trim()==='로그아웃')")


async def pick_schedule(page, c):
    """날짜·지점·영화를 고르고 조건에 맞는 회차 버튼 정보를 돌려준다(없으면 None)."""
    f = page.frame(name="frameBokdMBooking")
    await f.wait_for_selector("#movieList button[movie-nm]", timeout=30000)

    d = f.locator(f'button[date-data="{c["date_data"]}"]')
    if not await d.count():
        log(f"  날짜 {c['date']} 가 날짜 줄에 아직 없어요.")
        return None
    if "disabled" in (await d.get_attribute("class") or ""):
        log(f"  {c['date']} 은 아직 예매가 열리지 않았어요.")
        return None
    await d.click()
    await asyncio.sleep(1.0)
    await close_popup(f, "취소")

    region = f.locator("#brchList > ul > li > button", has_text=c["region"])
    if not await region.count():
        sys.exit(f"지역 '{c['region']}' 을 못 찾았어요. 서울/경기/인천처럼 화면에 보이는 이름으로 적어 주세요.")
    await region.first.click()
    await asyncio.sleep(0.8)
    br = f.locator(f'#brchList button[brch-nm="{c["branch"]}"]')
    if not await br.count():
        sys.exit(f"지점 '{c['branch']}' 을 못 찾았어요(화면의 지점 이름 그대로, 예: 코엑스).")
    await br.first.click()
    await asyncio.sleep(1.5)
    if await close_popup(f, "취소"):
        log("  그 날짜에 이 지점 상영이 없어요.")
        return None

    mv = f.locator(f'#movieList button[movie-nm*="{c["movie"]}"]')
    if not await mv.count():
        log(f"  영화 '{c['movie']}' 가 목록에 아직 없어요.")
        return None
    await mv.first.click()
    await asyncio.sleep(2.0)
    if await close_popup(f, "취소"):          # '가장 빠른 상영일로 변경?' → 날짜 안 바꿈
        log("  그 날짜에 이 영화 상영이 없어요.")
        return None

    shows = await f.evaluate("""()=>[...document.querySelectorAll('#playScheduleList button[play-schdl-no]')].map(b=>({
        id:b.id, start:b.getAttribute('play-start-time'), rest:+b.getAttribute('rest-seat-cnt'),
        title:(b.querySelector('.title strong')||{}).title||'',
        text:b.innerText.replace(/\\s+/g,' ').trim()}))""")
    # 영화 버튼이 그날 비활성이면 선택이 안 되고 지점 전체 시간표가 보이므로 제목으로 한 번 더 거른다
    shows = [s for s in shows if c["movie"] in s["title"]]
    if not shows:
        log("  이 영화의 시간표가 없어요.")
        return None
    log(f"  회차 {len(shows)}개 찾음")
    return shows


def best_show(shows, c):
    """남은 좌석·상영 형식으로 거르고 원하는 시각에 가장 가까운 회차 하나(없으면 None)."""
    if not shows:
        return None
    ok = [s for s in shows if s["rest"] >= c["people"]
          and (not c.get("format_keyword") or c["format_keyword"] in s["text"])]
    if c["times"]:
        def gap(s):
            m = int(s["start"][:2]) * 60 + int(s["start"][2:])
            return min(abs(m - (int(t[:2]) * 60 + int(t[2:]))) for t in c["times"])
        ok.sort(key=gap)                       # 원하는 시각과 가까운 순
    if not ok:
        log("  조건(남은 좌석·상영 형식)에 맞는 회차가 없어요.")
        return None
    return ok[0]


async def choose_seats(page, c):
    """좌석 화면에서 인원 수를 맞추고 좌석을 고른다. 고른 좌석 이름 목록을 돌려준다."""
    seat = page.frame(name="frameBokdMSeat")
    await seat.wait_for_selector("#seatLayout button[rowNm]", timeout=30000)
    await asyncio.sleep(1.0)

    up = seat.locator(".seat-count .cell").first.locator("button.up")
    for _ in range(c["people"]):
        await up.click()
        await asyncio.sleep(0.4)
        await close_popup(seat, "확인")

    seats = await seat.evaluate("""()=>[...document.querySelectorAll('#seatLayout button[rowNm]')].map(b=>({
        row:b.getAttribute('rowNm'), no:b.getAttribute('seatNo'), uniq:b.getAttribute('seatUniqNo'),
        left:parseFloat(b.style.left), width:parseFloat(b.style.width),
        free:!/finish|impossible|disabled/.test(b.className) && !b.disabled}))""")
    return await pick_and_click(seats, c, lambda s: click_megabox_seat(seat, s))


async def click_megabox_seat(seat, s):
    await seat.locator(f'#seatLayout button[seatUniqNo="{s["uniq"]}"]').click()
    await asyncio.sleep(0.5)
    return await close_popup(seat, "확인")


async def open_show(page, show):
    f = page.frame(name="frameBokdMBooking")
    await f.click(f'#playScheduleList button[id="{show["id"]}"]')
    await asyncio.sleep(1.5)
    for _ in range(3):                     # 상영관 안내 등 알림창 닫기
        if not await close_popup(f, "확인"):
            break


async def pick_and_click(seats, c, click):
    """좌석표(seats)에서 고를 묶음을 정하고 click(좌석) 으로 누른다."""
    if seats is None:
        return [], "인원 수 버튼을 못 찾았어요"
    grp, how = plan_seats(seats, c)
    if not grp:
        return [], how
    picked = []
    for s in grp:
        msg = await click(s)
        if msg:
            log(f"  알림: {msg[:60]}")
        picked.append(f'{s["row"]}{s["no"]}')
    return picked, how


def plan_seats(seats, c):
    """고를 좌석 묶음과 설명을 돌려준다. 1~10순위 좌석 먼저, 다 막혔으면 rows 자동 고르기."""
    if not seats:
        return None, "좌석표를 못 읽었어요"
    by_name = {f'{s["row"]}{s["no"]}': s for s in seats}
    for i, names in enumerate(c["seats"], 1):
        grp = [by_name.get(n) for n in names]
        if all(g and g["free"] for g in grp):
            return grp, f"{i}순위"
    if c["seats"] and not c["rows"]:
        return None, "1~10순위 좌석이 모두 찼어요"
    xs = [s["left"] for s in seats]
    center = (min(xs) + max(xs)) / 2
    rows = c["rows"] or sorted({s["row"] for s in seats})
    best = None
    for rank, row in enumerate(rows):
        line = sorted([s for s in seats if s["row"] == row], key=lambda s: s["left"])
        # 붙어 있는 빈자리 묶음(옆 좌석과 간격이 좌석 폭의 1.5배 이하)
        for i in range(len(line) - c["people"] + 1):
            grp = line[i:i + c["people"]]
            if not all(s["free"] for s in grp):
                continue
            if any(grp[k + 1]["left"] - grp[k]["left"] > grp[k]["width"] * 1.5 for k in range(len(grp) - 1)):
                continue
            mid = (grp[0]["left"] + grp[-1]["left"]) / 2
            score = (rank, abs(mid - center))
            if best is None or score < best[0]:
                best = (score, grp)
        if best and best[0][0] == rank:
            break                               # 앞 순위 줄에서 찾았으면 끝
    pre = "순위 좌석이 모두 차서 " if c["seats"] else ""
    if not best:
        return None, pre + "원하는 줄에 붙은 빈자리가 없어요"
    return best[1], pre + "자동으로 고름"


class Megabox:
    NAME = "메가박스"
    URL = URL
    is_logged_in = staticmethod(is_logged_in)
    pick_schedule = staticmethod(pick_schedule)
    open_show = staticmethod(open_show)
    choose_seats = staticmethod(choose_seats)


class Lotte:
    NAME = lotte.NAME
    URL = lotte.URL
    is_logged_in = staticmethod(lotte.is_logged_in)
    pick_schedule = staticmethod(lotte.pick_schedule)
    open_show = staticmethod(lotte.open_show)

    @staticmethod
    async def choose_seats(page, c):
        seats = await lotte.read_seats(page, c)
        return await pick_and_click(seats, c, lambda s: lotte.click_seat(page, s))


class Cgv:
    NAME = cgv.NAME
    URL = cgv.URL
    LOGIN_URL = cgv.LOGIN_URL
    CHROME = cgv.CHROME
    is_logged_in = staticmethod(cgv.is_logged_in)
    pick_schedule = staticmethod(cgv.pick_schedule)
    open_show = staticmethod(cgv.open_show)

    @staticmethod
    async def choose_seats(page, c):
        seats = await cgv.read_seats(page, c)
        return await pick_and_click(seats, c, lambda s: cgv.click_seat(page, s))


SITES = {"megabox": Megabox, "lotte": Lotte, "cgv": Cgv}


def beep():
    for _ in range(3):
        winsound.Beep(1200, 250)


# 휴대폰 알림: 이 PC 에 HEO Works 워커 키(..\study-helper\sync.json)가 있을 때만 보낸다. 없으면 조용히 건너뜀
ALERT_API = "https://exam-api.dawn-sea-4052.workers.dev"
ALERT_KEY_FILE = HERE.parent / "study-helper" / "sync.json"


def phone_alert(text):
    try:
        key = json.loads(ALERT_KEY_FILE.read_text(encoding="utf-8"))["key"]
    except Exception:
        return
    try:
        for kind in ("movie", "dispatch"):        # 서버가 'movie' 종류를 아직 모르면 '디스패처' 제목으로(글이 [영화 예매] 로 시작해 구별됨)
            body = json.dumps({"action": "pcAlert", "key": key, "kind": kind, "text": ("[영화 예매] " + text)[:300]}).encode()
            req = urllib.request.Request(ALERT_API, data=body, headers={"User-Agent": "movie-helper", "Content-Type": "application/json"})
            r = json.loads(urllib.request.urlopen(req, timeout=20).read())
            if r.get("error") != "bad_kind":
                break
        log("휴대폰 알림: " + ("보냄" if r.get("ok") else f"실패({r.get('error')})"))
    except Exception as e:
        log(f"휴대폰 알림 실패({type(e).__name__})")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(HERE / "config.json"))
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    c = load_config(a.config)
    site = SITES[c["cinema"]]()
    log(f"[{site.NAME}] 찾는 것: {c['movie']} / {c['region']} {c['branch']} / {c['date']} / {c['people']}명")

    chrome = getattr(site, "CHROME", False)        # CGV: 설치된 Chrome 으로만(Cloudflare 가 Chromium 을 막음)
    async with async_playwright() as p:
        try:
            if a.dry:
                browser = await (p.chromium.launch(channel="chrome", headless=False) if chrome else p.chromium.launch())
                page = await browser.new_page(locale="ko-KR")
            else:
                ctx = await p.chromium.launch_persistent_context(
                    str(HERE / ("profile_cgv" if chrome else "profile")), channel="chrome" if chrome else None,
                    headless=False, locale="ko-KR", viewport=None, args=["--start-maximized"])
                page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        except Exception as e:
            if chrome:
                sys.exit(f"{site.NAME}는 Chrome 브라우저로만 열려요. Chrome 이 설치돼 있는지, 이 프로그램 창이 이미 켜져 있지 않은지 확인해 주세요.\n({str(e).splitlines()[0]})")
            raise

        async def check_login():
            try:
                return await site.is_logged_in(page)
            except Exception:              # 로그인 버튼을 눌러 화면이 바뀌는 중 등
                return False

        async def logged_in():
            # 화면이 덜 그려져 '로그아웃'으로 잘못 보는 일이 없게 두 번 확인
            if await check_login():
                return True
            await asyncio.sleep(4)
            return await check_login()

        alerts = []                        # 로그인 풀림 알림 보낸 시각(서버 하루 10건을 좌석 알림 몫으로 남김)

        async def relogin_alert():
            now = time.monotonic()
            if len(alerts) >= 3 or (alerts and now - alerts[-1] < 30 * 60):
                return
            alerts.append(now)
            await asyncio.to_thread(phone_alert, f"[{site.NAME}] 로그인이 풀렸어요. 원격으로 PC 창에서 다시 로그인하면 이어서 기다려요(그동안 예매 오픈 확인은 멈춤).")

        async def login(again=False):
            if again:      # 예매 오픈을 기다리는 사이 사이트 로그인이 풀림 → 폰으로 알리고 다시 로그인할 때까지 기다림
                log(f"\n{site.NAME} 로그인이 풀렸어요. 열린 창에서 다시 로그인하면 이어서 기다려요.")
                await relogin_alert()
            else:
                log(f"\n열린 창에서 {site.NAME}에 로그인해 주세요. 로그인이 끝나면 자동으로 계속해요.")
            if getattr(site, "LOGIN_URL", None):
                await page.goto(site.LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
            while not await check_login():
                await asyncio.sleep(2)
                if again:
                    await relogin_alert()  # 오래 안 하면 30분마다 다시(최대 3번)
            log("로그인 확인!")
            await page.goto(site.URL, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(6 if chrome else 2)

        await page.goto(site.URL, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(6 if chrome else 2)
        if not a.dry and not await logged_in():
            await login()

        while True:
            show = best_show(await site.pick_schedule(page, c), c)
            if show or not c.get("wait_open") or a.dry:
                break
            log(f"  {c['check_every_sec']}초 뒤 다시 확인해요. (끄려면 Ctrl+C)")
            await asyncio.sleep(c["check_every_sec"])
            await page.reload(wait_until="domcontentloaded")
            await asyncio.sleep(6 if chrome else 2)
            if not await logged_in():
                await login(again=True)

        if not show:
            log("조건에 맞는 회차를 못 찾았어요.")
            if not a.dry:
                await asyncio.to_thread(phone_alert, f"[{site.NAME}] {c['movie']} {c['date']} — 조건에 맞는 회차를 못 찾아 멈췄어요.")
            return
        log(f"고른 회차: {show['text']} (남은 좌석 {show['rest']})")
        if a.dry:
            log("--dry 라서 여기서 멈춰요.")
            await browser.close()
            return

        # 좌석 단계가 실패해도 창은 열어 두고 직접 고르게 한다(로그인 뒤 화면이라 미리 다 확인하지 못함)
        try:
            n_pages = len(page.context.pages)
            await site.open_show(page, show)
            if len(page.context.pages) > n_pages:  # 좌석 화면이 새 탭으로 열리면 그 탭에서
                page = page.context.pages[-1]
                await page.wait_for_load_state("domcontentloaded")
            picked, how = await site.choose_seats(page, c)
        except Exception as e:
            picked, how = [], f"좌석 화면을 자동으로 다루지 못했어요({type(e).__name__})"
        beep()
        if picked:
            msg = f"좌석 {', '.join(picked)} 을 골랐어요({how})."
        else:
            msg = f"{how}. 좌석은 직접 골라 주세요."
        log("\n" + msg)
        await asyncio.to_thread(phone_alert, f"[{site.NAME}] {show['text'][:60]} — {msg} 결제 직전에 멈췄어요.")
        log("창에서 좌석을 확인하고 다음 단계를 직접 눌러 결제하세요. (창을 닫으면 프로그램도 끝나요)")
        await page.wait_for_event("close", timeout=0)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("멈췄어요.")
