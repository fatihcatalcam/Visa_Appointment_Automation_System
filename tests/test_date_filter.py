"""
Unit test for _filter_dates_by_max() — the date cutoff filtering logic.
Tests that appointment dates after the specified max date are correctly excluded.
"""
import pytest
from unittest.mock import MagicMock


class TestFilterDatesByMax:
    """Test the _filter_dates_by_max method of BLSScraper."""

    def _make_scraper(self):
        """Create a minimal BLSScraper instance with mocked dependencies."""
        from bot.scraper import BLSScraper
        
        # Mock everything so we don't need a real browser/DB
        scraper = object.__new__(BLSScraper)
        scraper.user_data = {}
        scraper._custom_log = None
        scraper.driver = None
        return scraper

    def test_dates_before_max_are_kept(self):
        scraper = self._make_scraper()
        dates = ["3 (May 2026)", "10 (May 2026)", "15 (May 2026)"]
        result = scraper._filter_dates_by_max(dates, "2026-05-20")
        assert result == ["3 (May 2026)", "10 (May 2026)", "15 (May 2026)"]

    def test_dates_after_max_are_removed(self):
        scraper = self._make_scraper()
        dates = ["3 (May 2026)", "10 (May 2026)", "15 (May 2026)"]
        result = scraper._filter_dates_by_max(dates, "2026-05-10")
        assert result == ["3 (May 2026)", "10 (May 2026)"]

    def test_exact_boundary_date_is_included(self):
        scraper = self._make_scraper()
        dates = ["3 (May 2026)"]
        result = scraper._filter_dates_by_max(dates, "2026-05-03")
        assert result == ["3 (May 2026)"]

    def test_all_dates_after_max_returns_empty(self):
        scraper = self._make_scraper()
        dates = ["15 (June 2026)", "20 (June 2026)"]
        result = scraper._filter_dates_by_max(dates, "2026-05-01")
        assert result == []

    def test_mixed_months(self):
        scraper = self._make_scraper()
        dates = ["28 (April 2026)", "3 (May 2026)", "15 (June 2026)"]
        result = scraper._filter_dates_by_max(dates, "2026-05-03")
        assert result == ["28 (April 2026)", "3 (May 2026)"]

    def test_invalid_max_date_returns_all(self):
        scraper = self._make_scraper()
        dates = ["3 (May 2026)", "10 (May 2026)"]
        result = scraper._filter_dates_by_max(dates, "not-a-date")
        assert result == ["3 (May 2026)", "10 (May 2026)"]

    def test_unparseable_date_is_kept(self):
        """Dates that can't be parsed should be kept (safety fallback)."""
        scraper = self._make_scraper()
        dates = ["3 (May 2026)", "unknown_date", "10 (May 2026)"]
        result = scraper._filter_dates_by_max(dates, "2026-05-05")
        assert "3 (May 2026)" in result
        assert "unknown_date" in result  # Unparseable → kept
        assert "10 (May 2026)" not in result  # After max

    def test_empty_dates_list(self):
        scraper = self._make_scraper()
        result = scraper._filter_dates_by_max([], "2026-05-03")
        assert result == []

    def test_day_only_format_is_kept(self):
        """Dates without month info (just a number) should be kept."""
        scraper = self._make_scraper()
        dates = ["5", "3 (May 2026)"]
        result = scraper._filter_dates_by_max(dates, "2026-05-01")
        assert "5" in result  # Can't parse month → kept
        assert "3 (May 2026)" not in result  # After April → removed
