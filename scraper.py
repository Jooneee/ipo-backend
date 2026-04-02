import requests
import re
import time
from threading import Lock
from concurrent.futures import ThreadPoolExecutor
from bs4 import BeautifulSoup
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_URL = 'http://www.38.co.kr'
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0',
    'Accept-Language': 'ko-KR,ko;q=0.9',
    'Referer': 'http://www.38.co.kr/',
}

_cache = {}
_cache_lock = Lock()
CACHE_TTL = 3600


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
    r = requests.get(url, headers=HEADERS, timeout=15, verify=False)
    r.encoding = 'euc-kr'
    return r


def _parse_date(s):
    """단일 날짜 문자열 → YYYY.M.D 포맷 (예: '2026.05.11' → '2026.5.11')"""
    s = s.strip().replace('\xa0', '').replace(' ', '')
    if not s or s == '-':
        return ''
    try:
        if '.' in s:
            parts = s.split('.')
            if len(parts) == 3:          # YYYY.MM.DD
                return f"{int(parts[0])}.{int(parts[1])}.{int(parts[2])}"
            elif len(parts) == 2:        # MM.DD (연도 없음)
                return f"{int(parts[0])}.{int(parts[1])}"
        if '/' in s:
            parts = s.split('/')
            if len(parts) == 3:          # YYYY/MM/DD
                return f"{int(parts[0])}.{int(parts[1])}.{int(parts[2])}"
    except (ValueError, IndexError):
        pass
    return ''


def _parse_date_range(s):
    """날짜 범위 → YYYY.M.D~YYYY.M.D 포맷 (예: '2026.05.11~05.12' → '2026.5.11~2026.5.12')"""
    s = s.strip().replace('\xa0', '').replace(' ', '')
    if not s:
        return ''
    if '~' in s:
        parts = s.split('~')
        start = _parse_date(parts[0])
        if not start:
            return ''
        end_raw = parts[1].strip().replace('\xa0', '').replace(' ', '') if len(parts) > 1 else ''
        # end_raw가 MM.DD 형식(연도 없음)인 경우 start에서 연도 추출
        end_parts = end_raw.split('.')
        if len(end_parts) == 2:
            start_parts = start.split('.')
            if len(start_parts) == 3:
                end = f"{start_parts[0]}.{int(end_parts[0])}.{int(end_parts[1])}"
            else:
                end = _parse_date(end_raw)
        else:
            end = _parse_date(end_raw)
        if start and end:
            return f"{start}~{end}"
        return start
    return _parse_date(s)


def _get_detail_cached(no):
    cached = _get_cache(f'detail:{no}')
    if cached is not None:
        return cached
    return get_ipo_detail(no)


def get_ipo_list():
    cached = _get_cache('list')
    if cached:
        return cached

    result = []

    for page in range(1, 6):
        url = f'{BASE_URL}/html/fund/index.htm?o=k&page={page}'
        try:
            r = _fetch(url)
            soup = BeautifulSoup(r.text, 'html.parser')
        except Exception:
            break

        table = soup.find('table', {'summary': '공모주 청약일정'})
        if not table:
            break

        tbody = table.find('tbody')
        rows = tbody.find_all('tr') if tbody else table.find_all('tr')[1:]
        if not rows:
            break

        # 1단계: 목록 파싱
        raw_items = []
        for row in rows:
            cells = row.find_all('td')
            if len(cells) < 6:
                continue
            link = cells[0].find('a')
            if not link:
                continue
            href = link.get('href', '')
            no_match = re.search(r'no=(\d+)', href)
            if not no_match:
                continue
            no = no_match.group(1)
            font_tag = link.find('font')
            color = (font_tag.get('color', '') if font_tag else '').upper()
            raw_items.append({
                'no': no,
                'name': link.get_text(strip=True),
                'is_completed': (color == '#333333'),
                'subscription_date': _parse_date_range(cells[1].get_text(strip=True)),
                'ipo_price': re.sub(r'[^\d,]', '', cells[2].get_text(strip=True)),
                'target_price': cells[3].get_text(strip=True).strip('-').strip(),
                'competition': cells[4].get_text(strip=True),
                'underwriter': cells[5].get_text(strip=True),
            })

        if not raw_items:
            break

        # 2단계: 상세 페이지 병렬 fetch (상장일/환불일)
        nos = [item['no'] for item in raw_items]
        with ThreadPoolExecutor(max_workers=8) as executor:
            detail_map = dict(zip(nos, executor.map(_get_detail_cached, nos)))

        for item in raw_items:
            no = item['no']
            detail = detail_map.get(no, {})
            detail_status = detail.get('status', '')
            status = detail_status if detail_status else ('청약완료' if item['is_completed'] else '')
            result.append({
                'code': no,
                'status': status,
                'subscription_date': item['subscription_date'],
                'name': item['name'],
                'target_price': item['target_price'],
                'ipo_price': item['ipo_price'] or detail.get('ipo_price', ''),
                'amount': detail.get('ipo_amount', ''),
                'refund_date': detail.get('refund_date', ''),
                'listing_date': detail.get('listing_date', ''),
                'competition': item['competition'] or detail.get('competition', ''),
                'underwriter': item['underwriter'],
            })
        found = len(raw_items)

    _set_cache('list', result)
    return result


def get_ipo_detail(no):
    cached = _get_cache(f'detail:{no}')
    if cached is not None:
        return cached

    url = f'{BASE_URL}/html/fund/index.htm?o=v&no={no}'
    try:
        r = _fetch(url)
        soup = BeautifulSoup(r.text, 'html.parser')
    except Exception:
        result = {}
        _set_cache(f'detail:{no}', result)
        return result

    detail = {'code': no}

    field_map = {
        '공모청약일': 'subscription_date',
        '수요예측일': 'demand_forecast_date',
        '납입일': 'payment_date',
        '환불일': 'refund_date',
        '상장일': 'listing_date',
        '확정공모가': 'ipo_price',
        '희망공모가': 'target_price',
        '공모금액': 'ipo_amount',
        '청약경쟁률': 'competition',
        '주간사': 'underwriter',
        '액면가': 'par_value',
        '업종': 'sector',
        '진행상황': '_status_raw',
    }
    date_fields = {'subscription_date', 'listing_date', 'refund_date', 'payment_date', 'demand_forecast_date'}

    for table in soup.find_all('table'):
        for row in table.find_all('tr'):
            cells = row.find_all('td')
            for i, cell in enumerate(cells):
                label = cell.get_text(strip=True).replace('\xa0', '')
                for korean, key in field_map.items():
                    if korean == label and i + 1 < len(cells) and key not in detail:
                        val = cells[i + 1].get_text(strip=True).replace('\xa0', '').replace(' ', '')
                        if val and val != '-':
                            if key in date_fields:
                                detail[key] = _parse_date_range(val)
                            else:
                                detail[key] = val

    # 진행상황 → status 변환
    status_raw = detail.pop('_status_raw', '')
    if '신규상장' in status_raw:
        detail['status'] = '상장완료'
    elif '공모철회' in status_raw:
        detail['status'] = '공모철회'
    else:
        detail['status'] = ''

    # IPO 페이지 여부 검증: 핵심 IPO 필드가 하나도 없으면 잘못된 페이지
    ipo_keys = {'subscription_date', 'listing_date', 'refund_date', 'ipo_price', 'target_price', 'competition'}
    if not any(k in detail for k in ipo_keys):
        detail['shares_info'] = {}
        _set_cache(f'detail:{no}', detail)
        return detail

    # 종목명: <title> 우선, 없으면 fund 관련 heading에서 추출
    title_tag = soup.find('title')
    if title_tag:
        title_text = title_tag.get_text(strip=True)
        # 38.co.kr title 형식: "종목명 - ..." 또는 "종목명공모주..."
        for sep in [' - ', ' | ', '공모주', '(']:
            if sep in title_text:
                candidate = title_text.split(sep)[0].strip()
                if candidate and len(candidate) < 30 and '38' not in candidate:
                    detail.setdefault('name', candidate)
                    break
    if 'name' not in detail:
        # 폴백: fund 페이지 특화 table에서 종목명 찾기
        for table in soup.find_all('table'):
            for row in table.find_all('tr'):
                cells = row.find_all('td')
                texts = [c.get_text(strip=True) for c in cells]
                if '종목명' in texts:
                    idx = texts.index('종목명')
                    if idx + 1 < len(texts) and texts[idx + 1]:
                        detail['name'] = texts[idx + 1]
                        break
            if 'name' in detail:
                break
    if 'name' not in detail:
        for tag in soup.find_all(['h2', 'h3', 'h4']):
            text = tag.get_text(strip=True)
            if text and len(text) < 30 and '38' not in text and 'EPS' not in text:
                detail['name'] = text
                break

    # 공모주식수 / 배정 비율: '일반청약자' 레이블이 있는 테이블에서만 추출
    shares_info = {}
    for table in soup.find_all('table'):
        for row in table.find_all('tr'):
            cells = row.find_all('td')
            texts = [c.get_text(strip=True) for c in cells]
            if '일반청약자' in texts:
                idx = texts.index('일반청약자')
                if idx + 2 < len(texts):
                    # 숫자 포함된 값만 신뢰
                    shares_val = texts[idx + 1]
                    ratio_val = texts[idx + 2]
                    if any(c.isdigit() for c in shares_val):
                        shares_info['retail_shares'] = shares_val
                        shares_info['retail_ratio'] = ratio_val
        if shares_info:
            break
    detail['shares_info'] = shares_info

    _set_cache(f'detail:{no}', detail)
    return detail
