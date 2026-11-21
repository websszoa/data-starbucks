"""할리스 상세 데이터(hollys/details/latest.json)로 매장 정리 데이터(hollys/stores.json)를 처음 생성한다.

stores.json 은 직접 관리하는 파일이므로 자동 실행하지 않는다.
이미 파일이 있으면 덮어쓰지 않으며, 전체를 다시 만들 때만 --force 로 실행한다.
(신규·폐점 매장 확인은 hollys-daily.py → hollys/changes/latest.json)

항목 구조는 스타벅스 starbucks/stores.json 과 같다.

실행: python hollys-stores.py --force
"""

from datetime import datetime
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from zoneinfo import ZoneInfo
import json
import sys

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "hollys"

# 층·주차 파서와 공통 형식은 스타벅스 정리 스크립트의 것을 그대로 쓴다.
_spec = spec_from_file_location("starbucks_stores", ROOT / "starbucks-stores.py")
common = module_from_spec(_spec)
_spec.loader.exec_module(common)


# services 에서 뺄 항목: 주차는 parking, 흡연시설은 smoking, DT 매장은 type 에 따로 넣는다.
SERVICE_EXCLUDE = {"주차", "흡연시설", "DT 매장"}


def build_store(detail):
    """스타벅스 starbucks/stores.json 과 같은 형식으로 매장 1개를 만든다."""
    name = detail.get("name") or ""
    services = detail.get("services", [])
    directions = common.clean_text(detail.get("directions"))
    store = {
        "id": detail["id"],
        "name": name,
        "address": (detail.get("address") or "").strip(),
        "directions": directions if directions and len(directions) > 1 else None,  # "." 만 적힌 경우 제외
        "description": None,
        "lat": common.to_float(detail.get("latitude")),
        "lng": common.to_float(detail.get("longitude")),
        "type": ["DT"] if "DT 매장" in services or name.endswith(("DT점", "DT")) else [],
        "opened": None,
        "view": None,
        "seats": None,
        "smoking": "흡연시설" if "흡연시설" in services else "금지",
        "pet": "금지",
        "study": None,
        "phone": (detail.get("phone") or "").strip() or None,
        "services": [label for label in services if label not in SERVICE_EXCLUDE],
        "parking": common.parse_simple_parking(detail.get("parking", ""), icon="주차" in services),
        "images": list(dict.fromkeys(detail.get("images") or [])),
    }
    return common.normalize_starbucks(store)


def main():
    output = DATA_DIR / "stores.json"
    if output.exists() and "--force" not in sys.argv:
        print("hollys/stores.json 이 이미 있습니다. 직접 수정한 내용이 덮어써지므로 실행하지 않습니다.\n"
              "전체를 다시 생성하려면: python hollys-stores.py --force", flush=True)
        return
    details = json.loads((DATA_DIR / "details" / "latest.json").read_text(encoding="utf-8"))
    items = [build_store(detail) for detail in details["item"]]
    data = {
        "kind": "Korea Hollys Stores",
        "date": datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d"),
        "count": len(items),
        "item": items,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=4)
        file.write("\n")
    print(f"할리스 매장 {len(items)}개 → hollys/stores.json", flush=True)


if __name__ == "__main__":
    main()
