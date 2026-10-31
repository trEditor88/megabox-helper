"""CGV — 지점·날짜·영화·회차 고르기와 좌석 고르기(megabox_helper.py 가 부른다).

CGV 는 Cloudflare 가 Playwright 의 Chromium·창 없는 실행을 막아서(403) 설치된 Chrome 창으로만 연다(CHROME).
화면 이름표(class)는 'screenInfo_timeLink__45VfR' 처럼 뒤에 바뀌는 꼬리가 붙어 앞부분([class*=...])으로만 찾는다.
"""
import asyncio
from datetime import date

URL = "https://cgv.co.kr/cnm/movieBook/cinema"
LOGIN_URL = "https://cgv.co.kr/mem/login?returnUrl=%2Fcnm%2FmovieBook%2Fcinema"
NAME = "CGV"
CHROME = True


def log(msg):
    print(msg, flush=True)


async def is_logged_in(page):
    """아래쪽 링크에 '로그인' 이 없으면 로그인된 것(로그인 화면처럼 아래쪽 링크가 없으면 아직 아님)."""
    try:
        return await page.evaluate("""()=>{const f=[...document.querySelectorAll('.cgv-footer-link')];
            return f.length>0 && !f.some(b=>b.innerText.trim()==='로그인')}""")
    except Exception as e:                # 로그인 버튼을 눌러 화면이 바뀌는 중
        if "context was destroyed" in str(e) or "navigat" in str(e):
            return False
        raise


async def close_ads(page):
    """첫 화면 광고 창(닫기·오늘은 그만 보기)만 닫는다. '확인' 은 누르지 않음(로그인 안내 등)."""
    await page.evaluate("""()=>document.querySelectorAll('.cgv-modal.active button').forEach(b=>{
        if(/^(닫기|오늘은 그만 보기)$/.test(b.innerText.trim())) b.click()})""")


async def modal_text(page):
    return await page.evaluate("""()=>{const m=[...document.querySelectorAll('.modal-container')].find(e=>e.offsetParent);
        return m? m.innerText.replace(/\\s+/g,' ').trim() : ''}""")


async def click_modal(page, label="확인"):
    """보이는 안내 창의 label 버튼을 누르고 글자를 돌려준다."""
    return await page.evaluate("""(label)=>{const m=[...document.querySelectorAll('.modal-container')].find(e=>e.offsetParent);
        if(!m) return ''; const b=[...m.querySelectorAll('button')].find(x=>x.innerText.trim()===label);
        if(!b) return ''; const t=m.innerText.replace(/\\s+/g,' ').trim().slice(0,80); b.click(); return t}""", label)


async def pick_schedule(page, c):
    await page.wait_for_selector('button[class*="dayScroll_scrollItem"]', timeout=30000)
    await asyncio.sleep(2)
    await close_ads(page)

    # 극장: 고른 극장 탭에 있으면 그걸 누르고, 아니면 지역 → 지점 창에서 고른다
    tab = await page.evaluate("""(br)=>{const t=[...document.querySelectorAll('button[class*="roundtab_tabTitle"]')]
        .find(b=>b.innerText.trim()===br); if(t){t.click(); return true} return false}""", c["branch"])
    if not tab:
        sheet = page.locator('[class*="bottom_region"]').locator("visible=true")
        if not await sheet.count():
            await page.evaluate("""()=>{const b=[...document.querySelectorAll('button')].find(x=>x.textContent.trim()==='자주가는 CGV 목록 수정');
                if(b) b.click()}""")
        await page.wait_for_selector('[class*="bottom_region"] li > button', timeout=20000)
        ok = await page.evaluate("""(rg)=>{const b=[...document.querySelectorAll('[class*="bottom_region"] li > button')]
            .filter(x=>/\\(\\d+\\)$/.test(x.innerText.trim()))
            .find(x=>{const t=x.innerText.trim(); return t.startsWith(rg) || t.split('(')[0].split('/').includes(rg)});
            if(b){b.click(); return true} return false}""", c["region"])
        if not ok:
            raise SystemExit(f"지역 '{c['region']}' 을 못 찾았어요. CGV 화면의 지역 이름(서울, 경기, 대전 등)으로 적어 주세요.")
        await asyncio.sleep(0.8)
        ok = await page.evaluate("""(br)=>{const b=[...document.querySelectorAll('[class*="bottom_listCon"] li button')]
            .find(x=>x.offsetParent && x.innerText.trim()===br); if(b){b.click(); return true} return false}""", c["branch"])
        if not ok:
            raise SystemExit(f"지점 '{c['branch']}' 을 못 찾았어요(CGV 화면의 지점 이름 그대로, 예: 용산아이파크몰).")
    await asyncio.sleep(3)

    # 날짜 줄은 오늘부터 하루씩 — 오늘에서 며칠 뒤인지로 칸을 고르고 숫자로 한 번 더 확인
    want = date.fromisoformat(c["date"])
    idx = (want - date.today()).days
    st = await page.evaluate("""([i,day])=>{const d=[...document.querySelectorAll('button[class*="dayScroll_scrollItem"]')][i];
        if(!d) return 'none';
        const n=((d.querySelector('[class*="dayScroll_number"]')||d).innerText.trim().split('.').pop());
        if(+n!==day) return 'none';
        if(d.disabled || /dayScroll_disabled/.test(d.className)) return 'closed';
        d.click(); return 'ok'}""", [idx, want.day])
    if idx < 0 or st == "none":
        log(f"  날짜 {c['date']} 가 날짜 줄에 아직 없어요.")
        return None
    if st == "closed":
        log(f"  {c['date']} 은 아직 예매가 열리지 않았거나 이 지점 상영이 없어요.")
        return None
    await asyncio.sleep(3)

    shows = await page.evaluate("""(movie)=>{
        const out=[];
        document.querySelectorAll('[class*="accordion_container"]').forEach(g=>{
          const title=((g.querySelector('[class*="screenInfo_cinemaMovieWrap"] .title2')||{}).innerText||'').trim();
          if(!title.includes(movie)) return;
          let hall='';
          g.querySelectorAll('[class*="screenInfo_contentWrap"] > *').forEach(e=>{
            if(e.tagName==='H3'){hall=e.innerText.replace(/\\s+/g,' ').trim(); return}
            e.querySelectorAll('button[class*="screenInfo_timeLink"]').forEach(b=>{
              const t=((b.querySelector('[class*="screenInfo_start"]')||{}).innerText||'').trim();
              const rest=parseInt(((b.querySelector('[class*="screenInfo_status"] .c-blue')||b.querySelector('[class*="screenInfo_status"]')||{}).innerText||'0'));
              const id='mbx'+out.length; b.setAttribute('data-mbx',id);
              out.push({id, start:t.replace(':',''), rest, title,
                        text:(t+' '+title+' '+hall+' '+b.innerText.replace(/\\s+/g,' ').replace(/^\\S+\\s*-\\s*\\S+/,'')).replace(/\\s+/g,' ').trim(),
                        disabled:b.getAttribute('aria-disabled')==='true'||b.disabled});
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
    await page.evaluate("(id)=>document.querySelector(`button[data-mbx=\"${id}\"]`).click()", show["id"])
    await asyncio.sleep(3)
    for _ in range(3):                       # 관람 등급·상영관 안내 같은 창
        t = await modal_text(page)
        if not t:
            break
        if "로그인" in t:
            raise RuntimeError("로그인이 풀렸어요")
        if not await click_modal(page, "확인"):
            break
        await asyncio.sleep(1.5)


async def read_seats(page, c):
    """인원 수를 맞추고 좌석표를 읽는다. 로그인 뒤 화면이라 구조를 확인하지 못해 여러 모양을 시도한다."""
    await asyncio.sleep(2)
    # 인원: '일반'(또는 '성인') 줄의 + 버튼
    for _ in range(c["people"]):
        ok = await page.evaluate("""()=>{
          const rows=[...document.querySelectorAll('li,div,dl')].filter(e=>e.offsetParent && /^(일반|성인)/.test(e.innerText.trim()) && e.querySelector('button'));
          const r=rows.sort((a,b)=>a.innerText.length-b.innerText.length)[0]; if(!r) return false;
          const b=[...r.querySelectorAll('button')].filter(x=>!x.disabled).find(x=>/증가|추가|더하기|plus/i.test((x.title||'')+(x.getAttribute('aria-label')||'')+x.textContent+x.className)
            || String(x.className).split(/\\s+/).includes('up') || x.innerText.trim()==='+');
          if(!b) return false; b.click(); return true}""")
        if not ok:
            return None
        await asyncio.sleep(0.6)
        await click_modal(page, "확인")
    # 인원 화면과 좌석 화면이 나뉘어 있으면 '좌석 선택' 으로 넘어감(결제 단계 버튼은 누르지 않음)
    nxt = await page.evaluate("""()=>{const b=[...document.querySelectorAll('button,a')].find(x=>x.offsetParent && !x.disabled
          && /^좌석\\s?선택$/.test(x.innerText.trim())); if(b){b.click(); return true} return false}""")
    if nxt:
        await asyncio.sleep(3)
        await click_modal(page, "확인")
    return await page.evaluate("""()=>[...document.querySelectorAll('[class*="seat"] button, button[class*="seat"], [class*="Seat"] button, button[class*="Seat"]')]
        .filter(b=>b.offsetParent).map((b,i)=>{
          const name=(b.getAttribute('title')||b.getAttribute('aria-label')||b.dataset.seat||b.textContent||'').trim().toUpperCase();
          // 좌석 이름 모양(G7, G열 7번 좌석)만 — 'TOTAL 8,000' 같은 다른 버튼이 섞이지 않게
          const m=name.length<=20 && name.match(/^([A-Z]{1,2})\\s*열?\\s*(\\d{1,3})\\s*번?(\\s*좌석)?$/);
          if(!m) return null;
          b.setAttribute('data-mbxseat','s'+i);
          const r=b.getBoundingClientRect();
          return {row:m[1], no:String(parseInt(m[2])), uniq:'s'+i, left:r.left, width:r.width,
                  free:!b.disabled && b.getAttribute('aria-disabled')!=='true'
                       && !/disabled|sold|finish|impossible|complete|reserved|block/i.test(b.className)}
        }).filter(Boolean)""")


async def click_seat(page, s):
    await page.evaluate("(u)=>document.querySelector(`[data-mbxseat=\"${u}\"]`).click()", s["uniq"])
    await asyncio.sleep(0.6)
    return await click_modal(page, "확인")
