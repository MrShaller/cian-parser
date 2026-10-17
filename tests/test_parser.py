"""Тесты парсера на локально сохранённой копии страницы объявления ЦИАН.

Файл `test_data/cian_308497101.html` — сохранённая браузером копия
https://spb.cian.ru/sale/flat/308497101/. Ожидаемые значения выверены по
встроенному состоянию страницы и её DOM; при добавлении новых сохранённых
страниц создавайте аналогичные тест-классы рядом.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from my_actor.parser import ParseError, parse_offer_html

HTML_PATH = Path(__file__).resolve().parents[1] / "test_data" / "cian_308497101.html"

DESCRIPTION = (
    "просторная видовая новая квартира в полностью заселенном жк бизнес класса Променад.\n"
    "метро, школы и вся инфраструктура в пешей 5-мин доступности.\n"
    "прямая продажа от собственника."
)


@unittest.skipUnless(HTML_PATH.exists(), f"нет файла {HTML_PATH}")
class ParseSavedOfferTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.result = parse_offer_html(HTML_PATH.read_text(encoding="utf-8", errors="replace"))

    def test_main_fields(self) -> None:
        result = self.result
        self.assertEqual(result["url"], "https://spb.cian.ru/sale/flat/308497101/")
        self.assertEqual(result["offer_id"], 308497101)
        self.assertEqual(result["title"], "Продается 3-комн. квартира, 79,6 м²")
        self.assertEqual(result["price"], 33000000)
        self.assertEqual(result["price_currency"], "rur")
        self.assertEqual(result["price_per_meter"], 414572)
        self.assertEqual(result["residential_complex"], "Promenade (Променад)")
        self.assertEqual(result["coordinates"], {"lat": 59.905849, "lng": 30.312392})

    def test_address_parts_not_mixed(self) -> None:
        address = self.result["address"]
        self.assertEqual(address["full"], "Санкт-Петербург, район Адмиралтейский, "
                                          "Измайловское, улица Парфеновская, 6к1")
        self.assertEqual(address["city"], "Санкт-Петербург")
        self.assertEqual(address["district"], "Адмиралтейский")
        self.assertEqual(address["okrug"], "Измайловское")
        self.assertEqual(address["street"], "Парфеновская")
        self.assertEqual(address["house_number"], "6к1")

    def test_flat_features(self) -> None:
        flat = self.result["flat"]
        self.assertEqual(flat["housing_type"], "Вторичка")
        self.assertEqual(flat["total_area"], 79.6)
        self.assertEqual(flat["kitchen_area"], 25.4)
        self.assertEqual(flat["floor"], 14)
        self.assertEqual(flat["floors_total"], 17)
        self.assertEqual(flat["ceiling_height"], 2.75)
        self.assertEqual(flat["bathroom"], "2 раздельных")
        self.assertEqual(flat["balcony"], "1 лоджия")
        self.assertEqual(flat["windows_view"], "На улицу и двор")
        self.assertEqual(flat["renovation"], "Дизайнерский")
        self.assertEqual(flat["rooms_count"], 3)
        self.assertFalse(flat["is_apartments"])

    def test_living_area_absent_not_invented(self) -> None:
        # В этом объявлении жилая площадь не указана ни в «О квартире»,
        # ни в areaParts — парсер не должен её выдумывать.
        self.assertIsNone(self.result["flat"]["living_area"])

    def test_building_features(self) -> None:
        building = self.result["building"]
        self.assertEqual(building["build_year"], 2022)
        self.assertEqual(building["floors_count"], 17)
        self.assertEqual(building["parking"], "Подземная")
        # На странице эти характеристики показаны как «Нет информации».
        self.assertEqual(building["overlap_type"], "Нет информации")
        self.assertEqual(building["heating"], "Нет информации")
        self.assertEqual(building["emergency"], "Нет")

    def test_about_sections_cover_all_displayed_fields(self) -> None:
        # Требование «остальные доступные поля раздела "О доме"»: сырые словари
        # содержат каждую пару «название → значение» со страницы, разделы не смешаны.
        self.assertEqual(
            self.result["about_flat_raw"],
            {
                "Тип жилья": "Вторичка",
                "Общая площадь": "79,6 м²",
                "Площадь кухни": "25,4 м²",
                "Высота потолков": "2,75 м",
                "Санузел": "2 раздельных",
                "Балкон/лоджия": "1 лоджия",
                "Вид из окон": "На улицу и двор",
                "Ремонт": "Дизайнерский",
            },
        )
        self.assertEqual(
            self.result["about_building_raw"],
            {
                "Год постройки": "2022",
                "Тип перекрытий": "Нет информации",
                "Парковка": "Подземная",
                "Отопление": "Нет информации",
                "Аварийность": "Нет",
            },
        )

    def test_description_extracted_whole(self) -> None:
        self.assertEqual(self.result["description"], DESCRIPTION)

    def test_photos(self) -> None:
        photo_urls = self.result["photo_urls"]
        self.assertEqual(len(photo_urls), 49)
        self.assertEqual(len(set(photo_urls)), 49, "URL фотографий не должны повторяться")
        for url in photo_urls:
            self.assertTrue(url.startswith("https://images.cdn-cian.ru/"), url)

    def test_floor_plan_not_invented(self) -> None:
        # На странице планировки подгружаются динамически и в сохранённом HTML
        # отсутствуют (cianLayouts: null) — планировкой нельзя назначать фото.
        self.assertIsNone(self.result["floor_plan_url"])

    def test_metro(self) -> None:
        self.assertEqual(
            self.result["metro"],
            [
                {"name": "Фрунзенская", "travel_time_min": 4, "travel_type": "пешком"},
                {"name": "Балтийская", "travel_time_min": 13, "travel_type": "пешком"},
                {"name": "Технологический институт", "travel_time_min": 18, "travel_type": "пешком"},
            ],
        )

    def test_parsed_at_iso_utc(self) -> None:
        parsed_at = self.result["parsed_at"]
        self.assertRegex(parsed_at, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class HelpersTest(unittest.TestCase):
    def test_url_fallback_from_offer_id(self) -> None:
        html = (
            '<html><head><title>x</title></head><body><script>window._cianConfig = {};'
            "window._cianConfig['frontend-offer-card'] = ([].concat([{"
            '"key":"defaultState","value":{"offerData":{"offer":{'
            '"id":123,"dealType":"sale","offerType":"flat","description":"d"}}}}]));'
            "</script></body></html>"
        )
        result = parse_offer_html(html)
        self.assertEqual(result["url"], "https://www.cian.ru/sale/flat/123/")
        self.assertEqual(result["offer_id"], 123)
        self.assertEqual(result["description"], "d")
        # фото отсутствуют, а не выдуманы
        self.assertEqual(result["photo_urls"], [])

    def test_parse_error_on_foreign_html(self) -> None:
        with self.assertRaises(ParseError):
            parse_offer_html("<html><body>page without cian state</body></html>")

    def test_as_number_parsing(self) -> None:
        from my_actor.parser import _as_int, _as_number

        self.assertEqual(_as_number("79,6 м²"), 79.6)
        self.assertEqual(_as_number("2.75"), 2.75)
        self.assertEqual(_as_number(None), None)
        self.assertEqual(_as_number("Нет информации"), None)
        self.assertEqual(_as_int("17"), 17)


if __name__ == "__main__":
    unittest.main()
