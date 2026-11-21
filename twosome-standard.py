"""투썸 원본 매장 목록(twosome/latest.json)을 표준 형식(twosome/stores.json)으로 바꾼다.

표준 형식은 스타벅스 starbucks/stores.json 과 같다.
stores.json 은 직접 관리하는 파일이므로 이미 있으면 덮어쓰지 않으며,
원본이 표준 형식이 아닐 때(처음 변환)나 --force 로 실행할 때만 만든다.

실행: python twosome-standard.py [--force]
"""

from datetime import datetime
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from zoneinfo import ZoneInfo
import json
import re
import sys

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "twosome"

_spec = spec_from_file_location("starbucks_stores", ROOT / "starbucks-stores.py")
common = module_from_spec(_spec)
_spec.loader.exec_module(common)

# services 에서 뺄 항목: 주차는 parking, 드라이브스루는 type 에 따로 넣고,
# 하트·와이파이·투썸오더·디카페인은 거의 모든 매장에 있어 구분 의미가 없다.
SERVICE_EXCLUDE = {"주차", "드라이브스루", "하트", "와이파이", "투썸오더", "디카페인"}


def format_date(value):
    value = str(value or "").strip()
    if re.fullmatch(r"\d{8}", value):
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return value if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) else None


def build_store(row):
    name = row.get("name") or ""
    services = row.get("services") or []
    is_dt = "드라이브스루" in services or bool(re.search(r"DT(?:점)?$", name))
    store = {
        "id": str(row["id"]),
        "name": name,
        "address": (row.get("address") or "").strip(),
        "directions": common.clean_text(row.get("directions")),
        "description": None,
        "lat": common.to_float(row.get("lat")),
        "lng": common.to_float(row.get("lng")),
        "type": ["DT"] if is_dt else [],
        "opened": format_date(row.get("opened")),
        "view": None,
        "seats": None,
        "smoking": "금지",
        "pet": "금지",
        "study": None,
        "phone": (row.get("phone") or "").strip() or None,
        "services": [label for label in services if label not in SERVICE_EXCLUDE],
        "parking": common.parse_simple_parking(row.get("parking_raw") or "", icon="주차" in services),
        "images": list(dict.fromkeys(row.get("images") or [])),
    }
    return common.normalize_starbucks(store)


def is_standard(data):
    items = (data or {}).get("item") or []
    return bool(items) and isinstance(items[0].get("parking"), dict)


def main():
    output = DATA_DIR / "stores.json"
    if output.exists() and "--force" not in sys.argv:
        current = json.loads(output.read_text(encoding="utf-8"))
        if is_standard(current):
            print("twosome/stores.json 이 이미 표준 형식입니다. 직접 수정한 내용이 덮어써지므로 실행하지 않습니다.\n"
                  "전체를 다시 만들려면: python twosome-standard.py --force", flush=True)
            return
    source = json.loads((DATA_DIR / "latest.json").read_text(encoding="utf-8"))
    items = [build_store(row) for row in source["item"]]
    data = {
        "kind": "Korea Twosome Place Stores",
        "date": datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d"),
        "source": source.get("source"),
        "count": len(items),
        "item": items,
    }
    with output.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=4)
        file.write("\n")
    print(f"투썸 매장 {len(items)}개 → twosome/stores.json", flush=True)


if __name__ == "__main__":
    main()
