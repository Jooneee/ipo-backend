import requests
from bs4 import BeautifulSoup
import re
import time
from threading import Lock

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0',
    'Accept-Language': 'ko-KR,ko;q=0.9',
    'Referer': 'http://ipostock.co.kr/',
}

# 단순 메모리 캐시
_cache = {}
_cache_lock = Lock()
CACHE_TTL = 3600  # 1시간


def _get_cache(key):
    with _cache_lock:
        entry = _cache.get(key)
        if entry and (time.time() - entry['ts']) < CACHE_TTL:
            return entry['data']
    return None


def _set_cache(key, data):
    with _cache_lock:
        _cache[key] = {'data': data, 'ts': time.time()}


def _fetch(url):
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.encoding = 'utf-8'
    return r


def get_ipo_list():
    cached = _get_cache('list')
    if cached:
        return cached

    r = _fetch('http://ipostock.co.kr/sub03/ipo04.asp')
    soup = BeautifulSoup(r.text, 'html.parser')

    # 중첩 테이블이 없으면서 view_ 링크가 있는 가장 안쪽 테이블 찾기
    target_table = None
    for table in soup.find_all('table'):
        nested = table.find('table')
        links = table.find_all('a', href=re.compile(r'view_\d+\.asp\?code='))
        if links and not nested:
            target_table = table
            break

    if not target_table:
        return []

    result = []
    for row in target_table.find_all('tr'):
        link = row.find('a', href=re.compile(r'view_\d+\.asp\?code='))
        if not link:
            continue

        href = link['href']
        m = re.search(r'code=([A-Z0-9]+)', href)
        if not m:
            continue
        code = m.group(1)

        # view_04 or view_01 구분
        view_type = 'view_04' if 'view_04' in href else 'view_01'

        cells = row.find_all('td')
        # 전체 셀 텍스트 (빈 것도 포함, 위치 유지)
        all_texts = [c.get_text(strip=True).replace('\xa0', ' ') for c in cells]

        # 데이터 행: 최소 9개 셀
        if len(all_texts) < 9:
            continue

        # 컬럼 구조: [추천, sub_date_or_status, name, target_price, ipo_price, amount, refund_date, listing_date, competition, underwriter]
        def g(i): return all_texts[i] if i < len(all_texts) else ''

        status = g(1) if any(kw in g(1) for kw in ['공모철회', '환불완료', '상장완료']) else ''
        sub_date = '' if status else g(1)

        result.append({
            'code': code,
            'view_type': view_type,
            'status': status,
            'subscription_date': sub_date,
            'name': g(2),
            'target_price': g(3),
            'ipo_price': g(4),
            'amount': g(5),
            'refund_date': g(6),
            'listing_date': g(7),
            'competition': g(8),
            'underwriter': g(9),
        })

    _set_cache('list', result)
    return result


def get_ipo_detail(code):
    cached = _get_cache(f'detail:{code}')
    if cached:
        return cached

    detail = {'code': code}

    # view_04 먼저 시도, 실패 시 view_01
    for view in ['view_04', 'view_01']:
        url = f'http://ipostock.co.kr/view_pg/{view}.asp?code={code}&schk=2'
        try:
            r = _fetch(url)
            if r.status_code == 200 and len(r.text) > 1000:
                break
        except Exception:
            continue

    soup = BeautifulSoup(r.text, 'html.parser')

    # 종목명
    for tag in soup.find_all(['h1', 'h2', 'h3', 'strong']):
        text = tag.get_text(strip=True)
        if text and not any(kw in text for kw in ['IPOSTOCK', '아이피오', 'IPO']):
            detail.setdefault('name', text)
            break

    # key-value 방식으로 모든 테이블 스캔
    field_map = {
        '공모청약일': 'subscription_date',
        '상장일': 'listing_date',
        '환불일': 'refund_date',
        '납일일': 'payment_date',
        '수요예측일': 'demand_forecast_date',
        '(확정)공모가격': 'ipo_price',
        '(희망)공모가격': 'target_price',
        '(확정)공모금액': 'ipo_amount',
        '(희망)공모금액': 'target_amount',
        '청약경쟁률': 'competition',
        '청약증거금율': 'deposit_rate',
        '주간사': 'underwriter',
        '공모가': 'ipo_price_short',
        '공모일': 'subscription_date_short',
        '업종': 'sector',
        '액면가': 'par_value',
    }

    for table in soup.find_all('table'):
        rows = table.find_all('tr')
        for row in rows:
            cells = row.find_all('td')
            for i, cell in enumerate(cells):
                text = cell.get_text(strip=True)
                for label, key in field_map.items():
                    if text == label and i + 1 < len(cells):
                        val = cells[i + 1].get_text(strip=True)
                        if val and key not in detail:
                            detail[key] = val

    # 공모주식수 & 배정비율
    shares_info = {}
    for table in soup.find_all('table'):
        for row in table.find_all('tr'):
            cells = row.find_all('td')
            texts = [c.get_text(strip=True) for c in cells]
            if '일반청약자' in texts:
                idx = texts.index('일반청약자')
                if idx + 2 < len(texts):
                    shares_info['retail_shares'] = texts[idx + 1]
                    shares_info['retail_ratio'] = texts[idx + 2]
            if '공모주식수' in texts[0] if texts else False:
                shares_info['total_shares'] = texts[1] if len(texts) > 1 else ''

    detail['shares_info'] = shares_info

    _set_cache(f'detail:{code}', detail)
    return detail
