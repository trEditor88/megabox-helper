"""좌석 순위 고르기 시험(사이트 없이). python test_seats.py"""
import json, sys, tempfile
from pathlib import Path
import megabox_helper as m

def layout(taken=()):
    return [{"row": r, "no": str(n), "uniq": f"{r}{n}", "left": n * 20, "width": 20,
             "free": f"{r}{n}" not in taken} for r in "EFGHJ" for n in range(1, 15)]

def cfg(**kw):
    base = {"movie": "x", "region": "서울", "branch": "코엑스", "date": "2026-10-30", "people": 2}
    base.update(kw)
    p = Path(tempfile.mkdtemp()) / "c.json"
    p.write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8")
    return m.load_config(p)

def names(g): return [f'{s["row"]}{s["no"]}' for s in g] if g else None
ok = 0
def check(label, got, want):
    global ok
    print(("통과 " if got == want else "실패 ") + label, got)
    ok += got == want

c = cfg(seats=["G7 G8", "H7,H8", ["F7", "F8"]], rows=["J"])
check("1순위 비어 있음", (names(m.plan_seats(layout(), c)[0]), m.plan_seats(layout(), c)[1]), (["G7", "G8"], "1순위"))
check("1순위 한 자리 참→2순위", names(m.plan_seats(layout({"G8"}), c)[0]), ["H7", "H8"])
check("1·2순위 참→3순위(목록형)", names(m.plan_seats(layout({"G7", "H8"}), c)[0]), ["F7", "F8"])
g, how = m.plan_seats(layout({"G7", "H8", "F7"}), c)
check("모두 참→rows 자동", (names(g), how), (["J7", "J8"], "순위 좌석이 모두 차서 자동으로 고름"))
c2 = cfg(seats=["G7 G8"])
check("모두 참+rows 없음→직접", m.plan_seats(layout({"G7"}), c2), (None, "1~10순위 좌석이 모두 찼어요"))
check("없는 좌석 이름→다음 순위", names(m.plan_seats(layout(), cfg(seats=["Z1 Z2", "E1 E2"]))[0]), ["E1", "E2"])
check("순위 없으면 예전처럼 자동", names(m.plan_seats(layout(), cfg(rows=["G"]))[0]), ["G7", "G8"])
for bad in (["G7"], ["G7 G8"] * 11, ["G7 가8"]):
    try: cfg(seats=bad); check(f"잘못된 입력 {bad[:2]} 막기", "안 막힘", "막힘")
    except SystemExit as e: check(f"잘못된 입력 {bad[:2]} 막기", "막힘", "막힘")
for bad in ({"movie": 'a"b'}, {"branch": r"a\b"}):
    try: cfg(**bad); check(f"따옴표·역슬래시 {bad} 막기", "안 막힘", "막힘")
    except SystemExit: check(f"따옴표·역슬래시 {bad} 막기", "막힘", "막힘")
check("G07 → G7 로 맞춤", names(m.plan_seats(layout(), cfg(seats=["G07 G08"]))[0]), ["G7", "G8"])
check("영화관 안 적으면 메가박스", cfg()["cinema"], "megabox")
check("롯데시네마 받음", cfg(cinema="lotte")["cinema"], "lotte")
check("CGV 받음", cfg(cinema="cgv")["cinema"], "cgv")
try: cfg(cinema="cineq"); check("모르는 영화관 막기", "안 막힘", "막힘")
except SystemExit: check("모르는 영화관 막기", "막힘", "막힘")
print(f"{ok}/17 통과"); sys.exit(0 if ok == 17 else 1)
