"""롯데시네마 — 지점·날짜·영화·회차 고르기와 좌석 고르기(megabox_helper.py 가 부른다)."""
import asyncio

URL = "https://www.lottecinema.co.kr/NLCHS/Ticketing"
NAME = "롯데시네마"
HEADLESS_OK = True


def log(msg):
    print(msg, flush=True)


async def is_logged_in(page):
    return await page.evaluate(
        "()=>[...document.querySelectorAll('a,button')].some(a=>a.offsetParent&&a.innerText.trim()==='로그아웃')")


async def close_layers(page):
    """보이는 안내 창(레이어)의 확인/닫기 버튼을 누른다. 누른 글자를 돌려준다."""
    return await page.evaluate("""()=>{
        const L=[...document.querySelectorAll('.layer_wrap, .layer_pop, [class*="layer"][class*="active"], [role="dialog"]')]
          .filter(e=>e.offsetParent && getComputedStyle(e).display!=='none');
        for(const l of L){
          const b=[...l.querySelectorAll('button,a')].find(x=>x.offsetParent && /^(확인|닫기)$/.test(x.innerText.trim()));
          if(b){const t=l.innerText.trim().slice(0,80); b.click(); return t}
        }
        return ''}""")


async def pick_schedule(page, c):
    await page.wait_for_selector(".cinema_select_wrap li.depth1 > a", timeout=30000)
    await close_layers(page)

    # 지역: '경기/인천(41)' 처럼 나오므로 앞부분으로 맞춘다
    regions = page.locator(".cinema_select_wrap.basicCinema li.depth1 > a")
    n = await regions.count()
    hit = None
    for i in range(n):
        t = (await regions.nth(i).inner_text()).strip()
        if t.startswith(c["region"]) or c["region"] in t.split("(")[0].split("/"):
            hit = regions.nth(i)
            break
    if hit is None:
        raise SystemExit(f"지역 '{c['region']}' 을 못 찾았어요. 롯데시네마 화면의 지역 이름(서울, 경기 등)으로 적어 주세요.")
    await hit.click()
    await asyncio.sleep(0.8)

    br = page.locator(".cinema_select_wrap.basicCinema li.depth1.active .depth2 a")
    target = None
    for i in range(await br.count()):
        if (await br.nth(i).inner_text()).strip() == c["branch"]:
            target = br.nth(i)
            break
    if target is None:
        raise SystemExit(f"지점 '{c['branch']}' 을 못 찾았어요(롯데시네마 화면의 지점 이름 그대로, 예: 월드타워).")
    await target.click()
    await asyncio.sleep(1.5)
    await close_layers(page)

    d = page.locator(f'input[data-playdate="{c["date"]}"]')
    if not await d.count():
        log(f"  날짜 {c['date']} 가 날짜 줄에 아직 없어요.")
        return None
    if await d.first.get_attribute("data-isplaydate") != "Y":
        log(f"  {c['date']} 은 아직 예매가 열리지 않았거나 이 지점 상영이 없어요.")
        return None
    await d.first.evaluate("e=>e.closest('a').click()")
    await asyncio.sleep(1.5)
    await close_layers(page)

    mv = page.locator(".movie_select_wrap li:not(.disabled) a, .movieSelect li:not(.disabled) a").filter(
        has=page.locator("strong.tit", has_text=c["movie"]))
    if await mv.count():
        await mv.first.click()
        await asyncio.sleep(1.5)
        await close_layers(page)

    shows = await page.evaluate("""(movie)=>{
        const out=[];
        document.querySelectorAll('.group_time_select').forEach(g=>{
          const title=(g.querySelector('.time_select_tit strong')||{}).innerText||'';
          if(!title.includes(movie)) return;
          g.querySelectorAll('.time_select_wrap').forEach(w=>{
            const hall=[...w.querySelectorAll('.list_hall li')].map(x=>x.innerText.trim()).join(' ');
            w.querySelectorAll('button.schedule_box').forEach((b,i)=>{
              const t=(b.querySelector('.schedule_box--time strong')||{}).innerText||'';
              const rest=parseInt((b.querySelector('.schedule_box--seat strong')||{}).innerText||'0');
              const id='mbx'+out.length; b.setAttribute('data-mbx',id);
              out.push({id, start:t.replace(':',''), rest, title, text:(t+' '+title+' '+hall).trim(), disabled:b.disabled});
            });
          });
        });
        return out}""", c["movie"])
    shows = [s for s in shows if not s["disabled"] and len(s["start"]) == 4]
    if not shows:
        log("  이 영화의 시간표가 없어요.")
        return None
    log(f"  회차 {len(shows)}개 찾음")
    return shows


async def open_show(page, show):
    await page.click(f'button[data-mbx="{show["id"]}"]')
    await asyncio.sleep(1.5)
    for _ in range(3):
        if not await close_layers(page):
            break
        await asyncio.sleep(0.5)


async def read_seats(page, c):
    """인원 수를 맞추고 좌석표를 읽는다. 로그인 뒤 화면이라 구조를 확인하지 못해 여러 모양을 시도한다."""
    await asyncio.sleep(1.5)
    # 인원: '성인' 줄의 + 버튼
    for _ in range(c["people"]):
        ok = await page.evaluate("""()=>{
          const rows=[...document.querySelectorAll('li,div,dl')].filter(e=>e.offsetParent && /^성인/.test(e.innerText.trim()) && e.querySelector('button'));
          const r=rows.sort((a,b)=>a.innerText.length-b.innerText.length)[0]; if(!r) return false;
          const b=[...r.querySelectorAll('button')].find(x=>/증가|더하기|plus/.test((x.title||'')+(x.getAttribute('aria-label')||'')+x.className)
            || String(x.className).split(/\\s+/).includes('up') || x.innerText.trim()==='+');
          if(!b) return false; b.click(); return true}""")
        if not ok:
            return None
        await asyncio.sleep(0.5)
        await close_layers(page)
    return await page.evaluate("""()=>[...document.querySelectorAll('[class*="seat"] button, button[class*="seat"], a[class*="seat"]')]
        .filter(b=>b.offsetParent).map((b,i)=>{
          const name=(b.getAttribute('title')||b.getAttribute('aria-label')||b.dataset.seat||'').toUpperCase();
          const m=name.match(/([A-Z]+)\\s*열?\\s*(\\d+)/) || (b.closest('[data-row]')&&[0,b.closest('[data-row]').dataset.row,b.innerText.trim()]);
          if(!m) return null;
          b.setAttribute('data-mbxseat','s'+i);
          const r=b.getBoundingClientRect();
          return {row:String(m[1]).toUpperCase(), no:String(parseInt(m[2])), uniq:'s'+i, left:r.left, width:r.width,
                  free:!b.disabled && !/disabled|sold|finish|impossible|completed/.test(b.className)}
        }).filter(Boolean)""")


async def click_seat(page, s):
    await page.click(f'[data-mbxseat="{s["uniq"]}"]')
    await asyncio.sleep(0.5)
    return await close_layers(page)
