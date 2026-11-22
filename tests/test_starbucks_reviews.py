"""starbucks-reviews.py 시험. 네이버 API 키 없이 저장된 응답(fixture)으로 파싱·필터·출력 형식을 확인한다.

실행: python3 tests/test_starbucks_reviews.py   (저장소 루트에서)
"""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import contextlib
import io
import json
import os
import shutil
import tempfile

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "naver"
SEOUL_IDS = ["9861", "9236"]  # 가로수길, 광화문


def load_module():
    spec = spec_from_file_location("starbucks_reviews", ROOT / "starbucks-reviews.py")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(module, argv):
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = module.main(argv)
    return code, buffer.getvalue()


def read(path):
    with Path(path).open(encoding="utf-8") as file:
        return json.load(file)


def check(label, condition, detail=""):
    if not condition:
        raise AssertionError(f"{label} 실패 {detail}")
    print(f"  OK  {label}{(' — ' + detail) if detail else ''}")


def test_helpers(module):
    print("[1] 파싱 도우미")
    check("<b> 제거", module.clean_text("<b>가로수길</b> 스타벅스") == "가로수길 스타벅스")
    check("HTML 엔티티 해제", module.clean_text("커피 &amp; 디저트") == "커피 & 디저트")
    check("접미사 제거(DT)", module.core_name("안양호계DT") == "안양호계", module.core_name("안양호계DT"))
    check("접미사 제거(B1F)", module.core_name("KT광화문웨스트B1F") == "KT광화문웨스트")
    check("리저브 R 제거", module.core_name("잠실R") == "잠실")
    check("매장명 키", "가로수길" in module.match_keys("가로수길"), str(module.match_keys("가로수길")))
    check("공백 무시 키", ["63skypicnic", "sky", "picnic"] == module.match_keys("63 Sky Picnic"), str(module.match_keys("63 Sky Picnic")))
    check("숫자만 토큰 제외", "63" not in module.match_keys("63 Sky Picnic"))
    check("구 추출", module.district("서울특별시 종로구 세종대로 167 (세종로)") == "종로구")
    check("짧은 이름은 질의 2회", len(module.build_queries({"name": "광화문", "address": "서울특별시 종로구 세종대로 167"})) == 2,
          str(module.build_queries({"name": "광화문", "address": "서울특별시 종로구 세종대로 167"})))
    check("긴 이름은 질의 1회", len(module.build_queries({"name": "가로수길", "address": "서울특별시 강남구 논현로175길 94"})) == 1)
    check("네이버 블로그 사이트 키", module.site_key("https://blog.naver.com/spam9/2234567901") == "blog.naver.com/spam9")
    check("날짜 변환", module.to_iso_date("20261120") == "2026-11-20")


def test_first_run(module, workdir):
    print("[2] 첫 수집 (2026-11-23)")
    out = workdir / "out"
    argv = ["--store-id", SEOUL_IDS[0], "--store-id", SEOUL_IDS[1],
            "--fixture", str(FIXTURES), "--out-dir", str(out), "--date", "2026-11-23"]
    code, output = run(module, argv)
    print("    " + "\n    ".join(output.strip().splitlines()))
    check("종료 코드 0", code == 0)
    data = read(out / "2026-11-23.json")
    check("톱레벨 키", list(data.keys()) == ["kind", "date", "count", "item"], str(list(data.keys())))
    check("kind/date/count", (data["kind"], data["date"], data["count"]) == ("Korea Starbucks Reviews Raw", "2026-11-23", 2),
          f'{data["kind"]} / {data["date"]} / {data["count"]}')
    check("latest.json 동일", read(out / "latest.json") == data)

    store = data["item"]["9861"]
    check("매장 키", list(store.keys()) == ["name", "address", "query", "post_count", "new_count", "posts"], str(list(store.keys())))
    check("매장 name/query", store["name"] == "가로수길" and "스타벅스 가로수길" in store["query"], store["query"])
    check("<b> 태그가 남지 않음", not any("<b>" in post["title"] or "<b>" in post["description"] for post in store["posts"]))
    check("엔티티 해제", any("&" in post["description"] for post in store["posts"]))
    check("중복 링크 1건으로", len({post["link"] for post in store["posts"]}) == len(store["posts"]) == store["post_count"] == 8,
          f'post_count={store["post_count"]}')
    links = {post["link"] for post in store["posts"]}
    check("매장명 없는 글 제외", "https://blog.naver.com/food3/2234567892" not in links)
    check("새 글 new=true (첫 수집)", store["new_count"] == 8)
    check("광고 표시 1건(버리지 않음)",
          [post["link"] for post in store["posts"] if "광고" in post["flag"]] == ["https://blog.naver.com/ad4/2234567893"])
    check("도배 표시 4건(버리지 않음)",
          sorted(post["link"] for post in store["posts"] if "도배" in post["flag"]) ==
          [f"https://blog.naver.com/spam9/223456790{n}" for n in range(1, 5)])
    check("글 키", all(list(post.keys()) == ["title", "link", "description", "bloggername", "postdate", "flag", "new"]
                       for post in store["posts"]))
    check("postdate 형식", all(len(post["postdate"]) == 10 and post["postdate"][4] == "-" for post in store["posts"]))
    check("최신 글 먼저", store["posts"][0]["postdate"] == "2026-11-20", store["posts"][0]["postdate"])

    gw = data["item"]["9236"]
    check("질의 2회여도 중복 제거", gw["post_count"] == gw["new_count"] == 2, f'post_count={gw["post_count"]}')
    check("질의 2회 기록", gw["query"] == "스타벅스 광화문 / 스타벅스 광화문 종로구", gw["query"])
    check("다른 지역 글 제외", all("busan" not in post["link"] for post in gw["posts"]))
    return out


def test_second_run(module, workdir, out):
    print("[3] 다음 날 수집 (2026-11-24) — 새 글 판별")
    fixtures = workdir / "fixtures"
    shutil.copytree(FIXTURES, fixtures)
    data = read(fixtures / "9861.json")
    data["items"].append({
        "title": "가로수길 스타벅스 신메뉴",
        "link": "https://blog.naver.com/coffee1/2234567899",
        "description": "가로수길 스타벅스에서 새로 나온 음료.",
        "bloggername": "커피러버",
        "bloggerlink": "https://blog.naver.com/coffee1",
        "postdate": "20261123",
    })
    (fixtures / "9861.json").write_text(json.dumps(data, ensure_ascii=False, indent=4), encoding="utf-8")

    argv = ["--store-id", SEOUL_IDS[0], "--fixture", str(fixtures), "--out-dir", str(out), "--date", "2026-11-24"]
    code, output = run(module, argv)
    print("    " + "\n    ".join(output.strip().splitlines()))
    check("종료 코드 0", code == 0)
    second = read(out / "2026-11-24.json")
    store = second["item"]["9861"]
    check("글 9건", store["post_count"] == 9, f'post_count={store["post_count"]}')
    check("새 글 1건", store["new_count"] == 1, f'new_count={store["new_count"]}')
    check("새 글 표시", [post["link"] for post in store["posts"] if post["new"]] == ["https://blog.naver.com/coffee1/2234567899"])
    check("지난 파일 보존", (out / "2026-11-23.json").exists())


def test_missing_key(module, workdir):
    print("[4] 키가 없을 때 (수집 생략, 종료 코드 0)")
    saved = {name: os.environ.pop(name, None) for name in ("NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET")}
    try:
        out = workdir / "nokey"
        code, output = run(module, ["--out-dir", str(out), "--date", "2026-11-23"])
        print("    " + output.strip())
        check("종료 코드 0", code == 0)
        check("수집 생략 메시지", "수집을 생략" in output)
        check("파일을 만들지 않음", not out.exists() or not list(out.glob("*.json")))
    finally:
        for name, value in saved.items():
            if value is not None:
                os.environ[name] = value


def test_selection(module):
    print("[5] 대상 매장 선정 (서울 매장 · 상한 회전)")
    stores = (read(ROOT / "starbucks" / "stores.json")).get("item") or []
    seoul = [store for store in stores if (store.get("address") or "").startswith("서울")]
    limit = 600
    print(f"    서울 매장 {len(seoul)}곳 (전체 {len(stores)}곳), 상한 {limit}곳")
    check("서울 매장이 있음", len(seoul) > 0, f"{len(seoul)}곳")

    class Args:
        store_id = None
        offset = None

    Args.limit = limit
    first, _ = module.select_stores(seoul, Args, "2026-11-23")
    second, _ = module.select_stores(seoul, Args, "2026-11-24")
    check("상한까지만 고름", len(first) == min(limit, len(seoul)), f"{len(first)}곳")
    if len(seoul) > limit:
        ids_first = {store["id"] for store in first}
        ids_second = {store["id"] for store in second}
        check("날마다 다른 구간", ids_first != ids_second)
        check("이틀 안에 서울 전체를 훑음", len(ids_first | ids_second) == len(seoul), f"합집합 {len(ids_first | ids_second)}곳")


def main():
    module = load_module()
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        test_helpers(module)
        out = test_first_run(module, workdir)
        test_second_run(module, workdir, out)
        test_missing_key(module, workdir)
    test_selection(module)
    print("\n모든 시험 통과")


if __name__ == "__main__":
    main()
