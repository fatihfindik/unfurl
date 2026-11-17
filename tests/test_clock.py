"""Dates and times for people (D-071): UTC in the database, Vienna on the screen."""

import datetime as dt

from freezegun import freeze_time

from app.services import clock

UTC = dt.UTC


@freeze_time("2026-11-16 23:30:00")  # UTC; in Vienna (UTC+1 in winter) it is 00:30 on the 17th
def test_today_is_the_users_day_not_utcs():
    assert clock.today() == dt.date(2026, 11, 17)


def test_a_typed_time_is_stored_as_utc():
    assert clock.to_utc(dt.date(2026, 11, 18), dt.time(10, 0)) == dt.datetime(
        2026, 11, 18, 9, 0, tzinfo=UTC
    )  # winter: UTC+1
    assert clock.to_utc(dt.date(2026, 7, 1), dt.time(10, 0)) == dt.datetime(
        2026, 7, 1, 8, 0, tzinfo=UTC
    )  # summer: UTC+2


def test_a_date_without_a_time_is_the_start_of_that_day():
    stored = clock.to_utc(dt.date(2026, 11, 18))

    assert stored == dt.datetime(2026, 11, 17, 23, 0, tzinfo=UTC)
    assert clock.is_start_of_day(stored)
    assert clock.local(stored).date() == dt.date(2026, 11, 18)
    assert not clock.is_start_of_day(clock.to_utc(dt.date(2026, 11, 18), dt.time(9, 30)))


def test_a_stored_time_comes_back_as_typed():
    stored = clock.to_utc(dt.date(2026, 11, 18), dt.time(14, 15))

    assert clock.local(stored).time() == dt.time(14, 15)
    naive = stored.replace(tzinfo=None)  # SQLite gives the time back without a zone
    assert clock.local(naive).time() == dt.time(14, 15)


def test_a_time_that_does_not_exist_in_march_moves_an_hour_later():
    # 29 March 2026: in Vienna the clock jumps from 02:00 to 03:00.
    stored = clock.to_utc(dt.date(2026, 3, 29), dt.time(2, 30))

    assert clock.local(stored).time() == dt.time(3, 30)


def test_a_time_that_happens_twice_in_october_is_the_first_one():
    # 25 October 2026: in Vienna 02:00–03:00 happens twice; the first is still summer time.
    stored = clock.to_utc(dt.date(2026, 10, 25), dt.time(2, 30))

    assert stored == dt.datetime(2026, 10, 25, 0, 30, tzinfo=UTC)  # UTC+2


def test_the_time_zone_comes_from_the_settings():
    assert clock.tz().key == "Europe/Vienna"
