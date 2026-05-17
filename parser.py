import aiohttp
import re
import logging
from config import DELIVERY_REGIONS

logger = logging.getLogger(__name__)

BASE = "https://goszakup.gov.kz"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Language": "ru-RU,ru;q=0.9",
}

# Индексы колонок в строке данных (14 ячеек, строка 0 — заголовок)
C_NUM        = 0   # № п/п
C_LOT_NUM    = 1   # Номер лота
C_CUSTOMER   = 2   # Заказчик
C_NAME       = 3   # Наименование
C_DESC       = 4   # Дополнительная характеристика
C_PRICE_UNIT = 5   # Цена за ед.
C_QTY        = 6   # Кол-во
C_UNIT       = 7   # Ед. изм.
C_TOTAL      = 8   # Плановая сумма  ← главное поле
C_STATUS     = 12  # Статус лота


class GoszakupParser:

    async def get_new_announcements(self, limit: int = 20) -> list:
        """
        1. Ищем объявления по региону Абай (КАТО 630000000).
        2. Для каждого проверяем место доставки — нужен Район Мақаншы.
        3. Парсим лоты через ?tab=lots построчно.
        """
        search_url = f"{BASE}/ru/search/announce"
        params = {
            "filter[del_region]": "630000000",
            "filter[status]": "1",
            "per-page": str(limit),
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(search_url, params=params,
                                       headers=HEADERS,
                                       timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    if resp.status != 200:
                        logger.error(f"Поиск вернул {resp.status}")
                        return []
                    html = await resp.text()

                ann_ids = list(dict.fromkeys(
                    re.findall(r'/ru/announce/index/(\d+)', html)
                ))
                logger.info(f"Найдено объявлений на странице поиска: {len(ann_ids)}")

                results = []
                for ann_id in ann_ids:
                    try:
                        ann = await self._parse_announcement(session, ann_id)
                        if ann:
                            results.append(ann)
                    except Exception as e:
                        logger.warning(f"Ошибка парсинга #{ann_id}: {e}")
                return results

        except Exception as e:
            logger.error(f"Ошибка get_new_announcements: {e}")
            return []

    async def _parse_announcement(self, session, ann_id: str) -> dict | None:
        # ── Главная страница ────────────────────────────────────
        main_url = f"{BASE}/ru/announce/index/{ann_id}"
        async with session.get(main_url, headers=HEADERS,
                               timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                return None
            main_html = await resp.text()

        # Проверяем место доставки — нужен Район Мақаншы
        if not self._html_matches_region(main_html):
            return None

        # Срок подачи заявок
        end_date = self._extract_field(main_html, [
            r'Срок подачи заявок.*?<td[^>]*>(.*?)</td>',
            r'Дата окончания.*?<td[^>]*>(.*?)</td>',
        ])

        # Дата публикации
        publish_date = self._extract_field(main_html, [
            r'Дата публикации.*?<td[^>]*>(.*?)</td>',
        ])

        # Номер объявления
        number_match = re.search(r'/ru/announce/index/\d+[^>]*>(\d{8,})', main_html)
        number = number_match.group(1) if number_match else ann_id

        # ── Вкладка лотов ──────────────────────────────────────
        lots_url = f"{BASE}/ru/announce/index/{ann_id}?tab=lots"
        async with session.get(lots_url, headers=HEADERS,
                               timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                return None
            lots_html = await resp.text()

        lots = self._parse_lots_table(lots_html, ann_id)
        if not lots:
            return None

        return {
            "id": ann_id,
            "number": ann_id,  # используем ID как номер — он виден в URL
            "end_date": end_date,
            "publish_date": publish_date,
            "lots": lots,
        }

    def _html_matches_region(self, html: str) -> bool:
        """Проверяет есть ли в HTML страницы упоминание Мақаншы / нужного района"""
        text = html.lower()
        for region in DELIVERY_REGIONS:
            if region.lower() in text:
                return True
        return False

    def _extract_field(self, html: str, patterns: list) -> str:
        for pattern in patterns:
            m = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
            if m:
                val = re.sub(r'<[^>]+>', '', m.group(1)).strip()
                val = re.sub(r'\s+', ' ', val)
                if val:
                    return val
        return ""

    def _parse_lots_table(self, html: str, ann_id: str) -> list:
        """
        Парсит таблицу лотов построчно через <tr>.
        Каждая строка данных = 14 ячеек (строка 0 — заголовок, пропускаем).
        """
        tables = re.findall(r'<table[^>]*>(.*?)</table>', html, re.DOTALL)
        lots = []

        for table in tables:
            rows = re.findall(r'<tr[^>]*>(.*?)</tr>', table, re.DOTALL)
            if not rows:
                continue

            # Проверяем что это таблица лотов (есть заголовок "Наименование")
            header_cells = re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', rows[0], re.DOTALL)
            header_text = " ".join(re.sub(r'<[^>]+>', '', c) for c in header_cells)
            if "Наименование" not in header_text:
                continue

            # Парсим строки данных (пропускаем строку 0 — заголовок)
            for row in rows[1:]:
                tds = re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', row, re.DOTALL)
                cells = [re.sub(r'<[^>]+>', '', td).strip() for td in tds]
                cells = [re.sub(r'\s+', ' ', c) for c in cells]

                if len(cells) < 9:
                    continue

                # Форматируем сумму
                raw_sum = cells[C_TOTAL].replace(" ", "").replace(",", ".")
                try:
                    amount_str = f"{float(raw_sum):,.0f} ₸".replace(",", " ")
                except (ValueError, TypeError):
                    amount_str = cells[C_TOTAL] or "не указана"

                lots.append({
                    "lot_number": cells[C_LOT_NUM],
                    "customer":   cells[C_CUSTOMER],
                    "name":       cells[C_NAME] or "Без названия",
                    "description":cells[C_DESC],
                    "price_unit": cells[C_PRICE_UNIT],
                    "qty":        cells[C_QTY],
                    "unit":       cells[C_UNIT],
                    "amount":     amount_str,
                    "status":     cells[C_STATUS] if len(cells) > C_STATUS else "",
                    "ann_id":     ann_id,
                })

        return lots

    def format_lots_info(self, ann: dict) -> list:
        return ann.get("lots", [])
