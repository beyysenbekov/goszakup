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

# Колонки таблицы лотов (порядок фиксирован на сайте)
COL_LOT_NUMBER   = 1   # "86813699-ЗЦП1"
COL_CUSTOMER     = 2   # Заказчик
COL_NAME         = 3   # Наименование
COL_DESCRIPTION  = 4   # Дополнительная характеристика
COL_PRICE_UNIT   = 5   # Цена за ед.
COL_QTY          = 6   # Кол-во
COL_UNIT         = 7   # Ед. изм.
COL_TOTAL        = 8   # Плановая сумма  ← берём отсюда
COL_STATUS       = 12  # Статус лота


class GoszakupParser:

    async def get_new_announcements(self, limit: int = 20) -> list:
        """
        Парсит страницу поиска объявлений по региону Абай,
        затем для каждого объявления подгружает лоты со вкладки ?tab=lots.
        """
        search_url = f"{BASE}/ru/search/announce"
        params = {
            "filter[del_region]": "630000000",  # Область Абай
            "filter[status]": "1",              # Идёт приём заявок
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
                logger.info(f"Найдено {len(ann_ids)} объявлений на странице поиска")

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
        """
        Загружает главную страницу объявления + вкладку лотов.
        Возвращает dict с полями id, number, end_date, publish_date, lots.
        """
        # ── Главная страница (сумма закупки, срок подачи) ──────
        main_url = f"{BASE}/ru/announce/index/{ann_id}"
        async with session.get(main_url, headers=HEADERS,
                               timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                return None
            main_html = await resp.text()

        # Номер объявления
        number_match = re.search(r'Номер объявления[^<]*</[^>]+>\s*<[^>]+>\s*([^\s<]+)', main_html)
        number = number_match.group(1) if number_match else ann_id

        # Срок подачи заявок
        end_match = re.search(
            r'(?:Срок подачи|Дата окончания)[^<]*</[^>]+>\s*<[^>]+>\s*([\d.:\s]+)',
            main_html
        )
        end_date = end_match.group(1).strip() if end_match else ""

        # Дата публикации
        pub_match = re.search(
            r'(?:Дата публикации|Опубликовано)[^<]*</[^>]+>\s*<[^>]+>\s*([\d.:\s]+)',
            main_html
        )
        publish_date = pub_match.group(1).strip() if pub_match else ""

        # ── Вкладка лотов ──────────────────────────────────────
        lots_url = f"{BASE}/ru/announce/index/{ann_id}?tab=lots"
        async with session.get(lots_url, headers=HEADERS,
                               timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                return None
            lots_html = await resp.text()

        lots = self._parse_lots_table(lots_html, ann_id)

        # Фильтруем лоты по региону
        matched = [l for l in lots if self._lot_matches_region(l)]
        if not matched:
            return None

        return {
            "id": ann_id,
            "number": number,
            "end_date": end_date,
            "publish_date": publish_date,
            "lots": matched,
        }

    def _parse_lots_table(self, html: str, ann_id: str) -> list:
        """
        Парсит таблицу лотов.
        Заголовки: №п/п | Номер лота | Заказчик | Наименование |
                   Доп.характ. | Цена за ед. | Кол-во | Ед.изм. |
                   Плановая сумма | Сумма 1 год | Сумма 2 год |
                   Сумма 3 год | Статус лота | Пред. план
        Данные начинаются после 14 заголовочных ячеек.
        """
        tables = re.findall(r'<table[^>]*>(.*?)</table>', html, re.DOTALL)
        lots = []

        for table in tables:
            cells_raw = re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', table, re.DOTALL)
            cells = [re.sub(r'<[^>]+>', '', c).strip() for c in cells_raw]
            cells = [c for c in cells if c]

            # Ищем таблицу с колонкой "Наименование"
            if "Наименование" not in cells:
                continue

            # Находим индекс конца заголовков
            try:
                header_end = cells.index("Пред. план") + 1
            except ValueError:
                # Попробуем найти по "Статус лота"
                try:
                    header_end = cells.index("Статус лота") + 1
                except ValueError:
                    header_end = 14  # fallback

            data = cells[header_end:]

            # Каждая строка данных = 13 ячеек (до "Пред.план" включительно)
            # Но последняя ячейка может отсутствовать, берём по 13
            row_size = 13
            for i in range(0, len(data), row_size):
                row = data[i:i + row_size]
                if len(row) < 9:
                    continue

                lot = {
                    "lot_number":   row[0] if len(row) > 0 else "",
                    "customer":     row[1] if len(row) > 1 else "",
                    "name":         row[2] if len(row) > 2 else "Без названия",
                    "description":  row[3] if len(row) > 3 else "",
                    "price_unit":   row[4] if len(row) > 4 else "",
                    "qty":          row[5] if len(row) > 5 else "",
                    "unit":         row[6] if len(row) > 6 else "",
                    "total_sum":    row[7] if len(row) > 7 else "0",
                    "status":       row[11] if len(row) > 11 else "",
                    "ann_id":       ann_id,
                    # Место доставки берём из адреса заказчика позже,
                    # регион определяем через поиск — все объявления уже
                    # отфильтрованы по КАТО 630000000 (Область Абай)
                    "delivery":     "Область Абай",
                }
                lots.append(lot)

        return lots

    def _lot_matches_region(self, lot: dict) -> bool:
        """
        Все объявления уже пришли с фильтром del_region=630000000 (Абай),
        поэтому дополнительно проверяем только Мақаншы если нужно.
        Сейчас пропускаем все лоты из Абайской области.
        """
        # Если хотите сузить только до Мақаншы — раскомментируйте:
        # combined = (lot.get("customer") or "").lower()
        # return any(r.lower() in combined for r in DELIVERY_REGIONS)
        return True  # все из Абайской области

    def format_lots_info(self, ann: dict) -> list:
        result = []
        for lot in ann.get("lots", []):
            name = lot.get("name") or "Без названия"
            desc = lot.get("description") or ""
            customer = lot.get("customer") or ""

            raw_sum = lot.get("total_sum", "0").replace(" ", "").replace(",", ".")
            try:
                amount_str = f"{float(raw_sum):,.0f} ₸".replace(",", " ")
            except (ValueError, TypeError):
                amount_str = lot.get("total_sum", "не указана")

            qty = lot.get("qty", "")
            unit = lot.get("unit", "")
            qty_str = f"{qty} {unit}".strip() if qty else ""

            result.append({
                "name": name,
                "description": desc,
                "amount": amount_str,
                "qty": qty_str,
                "customer": customer,
                "delivery": lot.get("delivery", "Область Абай"),
            })
        return result
