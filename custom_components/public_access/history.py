"""History for the public page, read-only and limited to the published view.

Some cards (ApexCharts among them) fetch their history through Home Assistant's
REST API rather than the websocket. On the public page that request would go to
Home Assistant with no credentials and fail, so it is steered here instead:
same path shape, same parameters, same answer format as
``GET /api/history/period/<start>`` — with one difference that is the whole
point: only entities the published view refers to are ever returned, whatever
the visitor asks for.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

ONE_DAY = timedelta(days=1)
# The recorder answers with one row per state change; a visitor asking for a
# year of a chatty sensor would tie up the database for everyone.
MAX_SPAN = timedelta(days=31)


@dataclass(frozen=True)
class HistoryQuery:
    start: datetime
    end: datetime
    entity_ids: list[str]
    include_start_time_state: bool
    significant_changes_only: bool
    minimal_response: bool
    no_attributes: bool


def parse_query(
    start_text: str | None,
    query: dict[str, str],
    allowed: set[str],
    now: datetime | None = None,
) -> HistoryQuery | str:
    """Home Assistant's own rules for the history endpoint, plus the allowlist.

    Returns the parsed query, or an error message. Entities outside the
    allowlist are dropped silently rather than refused: the card then gets an
    empty series for them, exactly as the websocket proxy behaves, and cannot
    tell whether they exist.
    """
    now = now or dt_util.utcnow()
    if start_text:
        start = dt_util.parse_datetime(start_text)
        if start is None:
            return "Invalid datetime"
        start = dt_util.as_utc(start)
    else:
        start = now - ONE_DAY

    wanted = [e for e in (query.get("filter_entity_id") or "").strip().lower().split(",") if e]
    if not wanted:
        return "filter_entity_id is missing"
    entity_ids = [e for e in wanted if e in allowed]

    if end_text := query.get("end_time"):
        end = dt_util.parse_datetime(end_text)
        if end is None:
            return "Invalid end_time"
        end = dt_util.as_utc(end)
    else:
        end = start + ONE_DAY
    if end - start > MAX_SPAN:
        start = end - MAX_SPAN

    return HistoryQuery(
        start=start,
        end=end,
        entity_ids=entity_ids,
        include_start_time_state="skip_initial_state" not in query,
        significant_changes_only=query.get("significant_changes_only", "1") != "0",
        minimal_response="minimal_response" in query,
        no_attributes="no_attributes" in query,
    )


async def async_fetch(hass: HomeAssistant, query: HistoryQuery) -> list:
    """The recorder's answer, in the REST endpoint's shape: one list per entity."""
    if not query.entity_ids or query.start > dt_util.utcnow():
        return []
    from homeassistant.components.recorder import get_instance, history

    states = await get_instance(hass).async_add_executor_job(
        history.get_significant_states,
        hass,
        query.start,
        query.end,
        query.entity_ids,
        None,
        query.include_start_time_state,
        query.significant_changes_only,
        query.minimal_response,
        query.no_attributes,
    )
    return list(states.values())
