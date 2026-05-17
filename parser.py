import aiohttp
import logging
from config import DELIVERY_REGIONS

logger = logging.getLogger(__name__)

BASE_URL = "https://ows.goszakup.gov.kz"
ANNOUNCE_URL = f"{BASE_URL}/trd-buy/all"
LOTS_URL = f"{BASE_URL}/lots"


class GoszakupParser:
    def __init__(self):
        self.headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (compatible; GoszakupBot/1.0)"
        }

    async def get_new_announcements(self, limit: int = 50) -> list:
        params = {
            "limit": limit,
            "ref_buy_status_id": 220,  # активные объявления
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    ANNOUNCE_URL,
                    params=params,
                    headers=self.headers,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status != 200:
                        logger.error(f"API вернул статус {resp.status}")
                        return await self._scrape_fallback(session)

                    data = await resp.json(content_type=None)
                    items = data.get("items", [])
                    logger.info(f"Получено {len(items)} объявлений из API")
                    filtered = await self._filter_by_region(session, items)
                    return filtered

        except Exception as e:
            logger.error(f"Ошибка: {e}")
            return []

    async def _filter_by_region(self, session, items):
        filtered = []
        for item in items:
            ann_id = item.get("id")
            if not ann_id:
                continue
            try:
                lots = await self._get_lots(session, ann_id)
                if self._lots_match_region(lots):
                    item["_lots"] = lots
                    filtered.append(item)
            except Exception as e:
                logger.warning(f"Ошибка лотов #{ann_id}: {e}")
        logger.info(f"После фильтра: {len(filtered)} объявлений")
        return filtered

    async def _get_lots(self, session, ann_id):
        url = f"{LOTS_URL}/number-anno/{ann_id}"
        async with session.get(url, headers=self.headers,
                               timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                return []
            data = await resp.json(content_type=None)
            if isinstance(data, list):
                return data
            return data.get("items", [])

    def _lots_match_region(self, lots):
        for lot in lots:
            kato = str(lot.get("ref_kato_code") or "")
            # КАТО Абайской области начинается с 63
            if kato.startswith("63"):
                return True
            combined = " ".join([
                lot.get("delivery_place_name_ru") or "",
                lot.get("delivery_place_name_kz") or "",
                lot.get("full_delivery_place_name_ru") or "",
                lot.get("full_delivery_place_name_kz") or "",
            ]).lower()
            for region in DELIVERY_REGIONS:
                if region.lower() in combined:
                    return True
        return False

    async def _scrape_fallback(self, session):
        logger.info("Резервный парсинг HTML...")
        import re
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
                return [{"id": int(i), "name_ru": f"Объявление #{i}", "_from_html": True} for i in ids[:20]]
        except Exception as e:
            logger.error(f"Ошибка резервного парсинга: {e}")
            return []

    def format_delivery(self, ann):
        lots = ann.get("_lots", [])
        if not lots:
            return "Область Абай / Район Мақаншы"
        places = set()
        for lot in lots[:3]:
            place = (lot.get("full_delivery_place_name_ru") or
                     lot.get("delivery_place_name_ru") or "").strip()
            if place:
                places.add(place)
        return " | ".join(places) if places else "Область Абай / Район Мақаншы"
