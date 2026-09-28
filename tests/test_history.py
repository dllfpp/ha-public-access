"""The public history endpoint answers only for the published view's entities."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("homeassistant")

from custom_components.public_access.history import MAX_SPAN, parse_query  # noqa: E402

NOW = datetime(2026, 9, 28, 20, 0, tzinfo=timezone.utc)
ALLOWED = {"sensor.solar_power", "weather.home"}


def test_entities_outside_the_view_are_dropped_silently():
    q = parse_query(
        "2026-09-24T20:00:00+02:00",
        {"filter_entity_id": "sensor.solar_power,person.owner,LOCK.Front_Door", "end_time": "2026-09-28T20:00:00+02:00"},
        ALLOWED, now=NOW,
    )
    assert q.entity_ids == ["sensor.solar_power"]
    # ISO datetimes are normalised to UTC, as Home Assistant does.
    assert q.start == datetime(2026, 9, 24, 18, 0, tzinfo=timezone.utc)
    assert q.end == datetime(2026, 9, 28, 18, 0, tzinfo=timezone.utc)


def test_parameters_follow_home_assistants_rest_endpoint():
    q = parse_query(None, {"filter_entity_id": "weather.home", "minimal_response": "", "no_attributes": "",
                           "significant_changes_only": "0", "skip_initial_state": ""}, ALLOWED, now=NOW)
    assert q.start == NOW - timedelta(days=1) and q.end == NOW
    assert q.minimal_response and q.no_attributes
    assert not q.significant_changes_only and not q.include_start_time_state


def test_bad_input_is_an_error_message():
    assert parse_query("not-a-date", {"filter_entity_id": "weather.home"}, ALLOWED, now=NOW) == "Invalid datetime"
    assert parse_query(None, {}, ALLOWED, now=NOW) == "filter_entity_id is missing"
    assert parse_query(None, {"filter_entity_id": "weather.home", "end_time": "x"}, ALLOWED, now=NOW) == "Invalid end_time"


def test_a_huge_range_is_capped():
    q = parse_query("2020-01-01T00:00:00+00:00", {"filter_entity_id": "weather.home", "end_time": "2026-09-28T00:00:00+00:00"}, ALLOWED, now=NOW)
    assert q.end - q.start == MAX_SPAN
