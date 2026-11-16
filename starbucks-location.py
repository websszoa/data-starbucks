"""스타벅스 지역별 매장 위치 데이터 수집기."""

from datetime import datetime
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import argparse


def load_collector():
    module_path = Path(__file__).with_name("starbucks-details.py")
    spec = spec_from_file_location("starbucks_details", module_path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_args(region_slugs):
    parser = argparse.ArgumentParser(description="스타벅스 지역별 매장 위치 정보 수집")
    parser.add_argument(
        "regions", nargs="*", metavar="REGION",
        help="수집할 지역 slug. 생략하면 전체 지역을 수집합니다.",
    )
    args = parser.parse_args()
    invalid = [region for region in args.regions if region not in region_slugs]
    if invalid:
        parser.error(
            f"지원하지 않는 지역: {', '.join(invalid)}\n"
            f"사용 가능한 지역: {', '.join(sorted(region_slugs))}"
        )
    return args


def main():
    collector = load_collector()
    args = parse_args(collector.REGIONS)
    selected = args.regions or list(collector.REGIONS)
    current_date = datetime.now().strftime("%Y-%m-%d")
    failures = []

    for position, slug in enumerate(selected, start=1):
        region = collector.REGIONS[slug]
        print(f"\n위치 수집 시작: {region['name']} ({position}/{len(selected)})", flush=True)
        try:
            stores = collector.get_region_stores(region)
            collector.save_location_data(slug, region, stores, current_date)
        except Exception as error:
            failures.append((slug, str(error)))
            print(f"위치 수집 실패: {region['name']} - {error}", flush=True)

    if failures:
        summary = "; ".join(f"{slug}: {error}" for slug, error in failures)
        raise RuntimeError(f"{len(failures)}개 지역 위치 수집 실패: {summary}")


if __name__ == "__main__":
    main()
