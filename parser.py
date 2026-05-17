import aiohttp
import logging
from config import DELIVERY_REGIONS

logger = logging.getLogger(__name__)

BASE_URL = "https://ows.goszakup.gov.kz"
ANNOUNCE_URL = f"{BASE_URL}/trd-buy/all"
LOTS_URL = f"{BASE_URL}/lots/number-anno"


class GoszakupParser:
    def __init__(self):
        self.headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (compatible; GoszakupBot/1.0)"
        }

    async def get_new_announcements(self, limit: int = 50) -> list:
        params = {"limit": limit, "ref_buy_status_id": 220}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    ANNOUNCE_URL, params=params,
                    headers=self.headers,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status != 200:
                        logger.error(f"API статус {resp.status}")
                        return await self._scrape_fallback(session)

                    data = await resp.json(content_type=None)
                    items = data.get("items", [])
                    logger.info(f"Получено {len(items)} объявлений")

                    # Для каждого объявления подгружаем лоты
                    enriched = await self._enrich_with_lots(session, items)
                    return enriched

        except Exception as e:
            logger.error(f"Ошибка: {e}")
            return []

    async def _enrich_with_lots(self, session, items):
        """
        Подгружает лоты для каждого объявления.
        Фильтрует по региону. Сохраняет лоты нужного региона в item['_lots'].
        """
        result = []
        for item in items:
            ann_id = item.get("id")
            if not ann_id:
                continue
            try:
                lots = await self._get_lots(session, ann_id)
                matched_lots = [l for l in lots if self._lot_matches_region(l)]
                if matched_lots:
                    item["_lots"] = matched_lots
                    result.append(item)
            except Exception as e:
                logger.warning(f"Ошибка лотов #{ann_id}: {e}")

        logger.info(f"После фильтра по региону: {len(result)} объявлений")
        return result

    async def _get_lots(self, session, ann_id):
        url = f"{LOTS_URL}/{ann_id}"
        async with session.get(
            url, headers=self.headers,
            timeout=aiohttp.ClientTimeout(total=15)
        ) as resp:
            if resp.status != 200:
                return []
            data = await resp.json(content_type=None)
            if isinstance(data, list):
                return data
            return data.get("items", [])

    def _lot_matches_region(self, lot):
        # Проверка по КАТО-коду (надёжнее всего)
        kato = str(lot.get("ref_kato_code") or "")
        if kato.startswith("63"):
            return True
        # Запасная проверка по тексту
        combined = " ".join([
            lot.get("delivery_place_name_ru") or "",
            lot.get("delivery_place_name_kz") or "",
            lot.get("full_delivery_place_name_ru") or "",
            lot.get("full_delivery_place_name_kz") or "",
        ]).lower()
        return any(r.lower() in combined for r in DELIVERY_REGIONS)

    async def _scrape_fallback(self, session):
        import re
        logger.info("Резервный HTML-парсинг...")
        url = "https://goszakup.gov.kz/ru/search/announce"
        params = {"filter[del_region]": "630000000", "filter[status]": "1", "per-page": "20"}
        try:
            async with session.get(url, params=params,
                                   headers={"User-Agent": "Mozilla/5.0"},
                                   timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    return []
                html = await resp.text()
                ids = list(set(re.findall(r'/ru/announce/index/(\d+)', html)))
                return [{"id": int(i), "_lots": []} for i in ids[:20]]
        except Exception as e:
            logger.error(f"Ошибка HTML-парсинга: {e}")
            return []

    def format_lots_info(self, ann: dict) -> list[dict]:
        """
        Возвращает список словарей с данными каждого лота:
        name, amount, delivery
        """
        lots = ann.get("_lots", [])
        result = []
        for lot in lots:
            name = (
                lot.get("name_ru") or
                lot.get("name_kz") or
                lot.get("lot_name_ru") or
                lot.get("lot_name_kz") or
                "Без названия"
            )
            # Сумма лота
            amount = (
                lot.get("amount") or
                lot.get("sum") or
                lot.get("lot_sum") or
                lot.get("total_sum") or
                0
            )
            try:
                amount_str = f"{float(amount):,.0f} ₸".replace(",", " ")
            except (ValueError, TypeError):
                amount_str = "не указана"

            # Место доставки
            delivery = (
                lot.get("full_delivery_place_name_ru") or
                lot.get("delivery_place_name_ru") or
                lot.get("full_delivery_place_name_kz") or
                "Область Абай / Район Мақаншы"
            ).strip()

            result.append({
                "name": name,
                "amount": amount_str,
                "delivery": delivery,
            })
        return result
