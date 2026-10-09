"""Government data-source adapters.

STATUS: no government feed is integrated.

Each candidate source below was investigated (see README "Data sources"). None offers a
documented public API, bulk download or incremental feed that this project is authorised
to use, and from the build environment several portals did not resolve at all. They are
therefore registered as ``not_configured`` with the reason, so the UI reports them
honestly instead of implying live government data. Nothing here scrapes or bypasses
access controls.

To integrate a source later, implement :class:`GovernmentAdapter` (fetch -> validate ->
upsert into a ``government_industries_*`` table) and flip ``enabled`` once records have
actually been obtained and validated. The existing batch tooling in
``src/government`` (CSV schema normalisation) and ``src/matching`` (entity matching)
is the intended downstream path.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class GovernmentSourceInfo:
    source: str
    name: str
    url: str
    cadence: str
    reason: str
    access: str            # what was established about access
    incremental: bool = False

    def describe(self) -> dict:
        return asdict(self)


def government_sources() -> list[GovernmentSourceInfo]:
    return [
        GovernmentSourceInfo(
            source="gov_india_industrial_land_bank",
            name="India Industrial Land Bank (IILB)",
            url="https://iilb.gov.in",
            cadence="unknown (no documented feed)",
            reason="Not configured: no documented public API or bulk download; host did not resolve "
                   "from the build environment (checked 2026-10-09).",
            access="Interactive portal only (as far as verified). No authorised export established.",
        ),
        GovernmentSourceInfo(
            source="gov_industrial_corridor_land",
            name="Industrial Corridor Land Platform",
            url="https://iclp.gov.in",
            cadence="unknown (no documented feed)",
            reason="Not configured: host did not resolve from the build environment (checked "
                   "2026-10-09); no authorised export established.",
            access="Unverified.",
        ),
        GovernmentSourceInfo(
            source="gov_ncog_up_industrial_parks",
            name="NCoG / Uttar Pradesh Industrial Parks & Land Bank GIS",
            url="https://ncog.gov.in",
            cadence="unknown (no documented feed)",
            reason="Not configured: connection to host timed out from the build environment "
                   "(checked 2026-10-09); GIS service terms/endpoints not established.",
            access="Unverified.",
        ),
    ]
