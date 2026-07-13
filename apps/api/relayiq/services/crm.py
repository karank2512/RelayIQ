"""CRM adapters: a shared interface, the built-in simulator (default), and a HubSpot
adapter implemented against the v3 CRM API shape.

The HubSpot adapter is fully implemented and unit-tested against recorded-shape fixtures
(respx), but LIVE synchronization has NOT been verified — no credentials in this build.
Set HUBSPOT_ACCESS_TOKEN and create a CrmConnection(system='hubspot', mode='live') to use it.
"""

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from relayiq.config import get_settings
from relayiq.models import CrmSimRecord

# Canonical field -> HubSpot property (v3 default property names)
HUBSPOT_COMPANY_PROPERTIES = {
    "name": "name",
    "root_domain": "domain",
    "website": "website",
    "industry": "industry",
    "employee_count": "numberofemployees",
    "annual_revenue_usd": "annualrevenue",
    "hq_city": "city",
    "hq_state": "state",
    "hq_country": "country",
    "founded_year": "founded_year",
    "linkedin_url": "linkedin_company_page",
}
HUBSPOT_CONTACT_PROPERTIES = {
    "first_name": "firstname",
    "last_name": "lastname",
    "work_email": "email",
    "job_title": "jobtitle",
    "seniority": "hs_seniority",
    "country": "country",
    "linkedin_url": "hs_linkedin_url",
    "company_name": "company",
}


@dataclass
class CrmReadResult:
    found: bool
    external_id: str | None = None
    properties: dict | None = None
    property_updated_at: dict | None = None


@dataclass
class CrmWriteResult:
    ok: bool
    external_id: str | None = None
    error: str | None = None
    retryable: bool = False
    status_code: int | None = None


class CrmAdapter(ABC):
    system: str = "base"

    @abstractmethod
    def read(self, session: Session, tenant_id: str, object_type: str,
             external_id: str | None, lookup: dict) -> CrmReadResult: ...

    @abstractmethod
    def write(self, session: Session, tenant_id: str, object_type: str,
              external_id: str | None, properties: dict) -> CrmWriteResult: ...


class SimulatorCrmAdapter(CrmAdapter):
    """Writes into crm_sim_records so 'the CRM' is inspectable in the UI and tests."""

    system = "simulator"

    def _find(self, session: Session, tenant_id: str, object_type: str,
              external_id: str | None, lookup: dict) -> CrmSimRecord | None:
        if external_id:
            row = session.execute(
                select(CrmSimRecord).where(
                    CrmSimRecord.tenant_id == tenant_id,
                    CrmSimRecord.object_type == object_type,
                    CrmSimRecord.external_id == external_id,
                )
            ).scalar_one_or_none()
            if row:
                return row
        # Fall back to natural-key lookup (domain / email), mirroring CRM search-by-property
        key = lookup.get("domain") or lookup.get("email")
        if not key:
            return None
        prop = "domain" if "domain" in lookup else "email"
        rows = session.execute(
            select(CrmSimRecord).where(
                CrmSimRecord.tenant_id == tenant_id, CrmSimRecord.object_type == object_type
            )
        ).scalars().all()
        return next((r for r in rows if (r.properties or {}).get(prop, "").lower() == key.lower()), None)

    def read(self, session, tenant_id, object_type, external_id, lookup) -> CrmReadResult:
        row = self._find(session, tenant_id, object_type, external_id, lookup)
        if row is None:
            return CrmReadResult(found=False)
        return CrmReadResult(
            found=True, external_id=row.external_id,
            properties=dict(row.properties or {}),
            property_updated_at=dict(row.property_updated_at or {}),
        )

    def write(self, session, tenant_id, object_type, external_id, properties) -> CrmWriteResult:
        row = self._find(session, tenant_id, object_type, external_id, properties_lookup(properties))
        now_iso = datetime.now(UTC).isoformat()
        if row is None:
            # Random id, never derived from property values — identical property sets on
            # different records must not collide on the (tenant, type, external_id) key.
            ext = external_id or f"sim-{object_type}-{uuid.uuid4().hex[:12]}"
            row = CrmSimRecord(
                tenant_id=tenant_id, object_type=object_type, external_id=ext,
                properties={}, property_updated_at={},
            )
            session.add(row)
            session.flush()
        props = dict(row.properties or {})
        stamps = dict(row.property_updated_at or {})
        for k, v in properties.items():
            props[k] = v
            stamps[k] = now_iso
        row.properties = props
        row.property_updated_at = stamps
        session.flush()
        return CrmWriteResult(ok=True, external_id=row.external_id)


def properties_lookup(properties: dict) -> dict:
    out = {}
    if properties.get("domain"):
        out["domain"] = properties["domain"]
    if properties.get("email"):
        out["email"] = properties["email"]
    return out


class HubSpotCrmAdapter(CrmAdapter):
    """HubSpot CRM v3 objects API. Rate-limit aware (429 → retryable), idempotent upsert
    via search-then-create/update on domain/email natural keys."""

    system = "hubspot"

    def __init__(self, access_token: str | None = None, base_url: str | None = None,
                 client: httpx.Client | None = None):
        settings = get_settings()
        self.base_url = (base_url or settings.hubspot_base_url).rstrip("/")
        token = access_token or settings.hubspot_access_token
        self._client = client or httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {token}"} if token else {},
            timeout=10.0,
        )

    @staticmethod
    def _object_path(object_type: str) -> str:
        return "companies" if object_type == "company" else "contacts"

    def read(self, session, tenant_id, object_type, external_id, lookup) -> CrmReadResult:
        path = self._object_path(object_type)
        try:
            if external_id:
                resp = self._client.get(f"/crm/v3/objects/{path}/{external_id}")
                if resp.status_code == 200:
                    doc = resp.json()
                    return CrmReadResult(True, doc.get("id"), doc.get("properties", {}), {})
                if resp.status_code != 404:
                    resp.raise_for_status()
            key = lookup.get("domain") or lookup.get("email")
            if key:
                prop = "domain" if "domain" in lookup else "email"
                resp = self._client.post(
                    f"/crm/v3/objects/{path}/search",
                    json={"filterGroups": [{"filters": [
                        {"propertyName": prop, "operator": "EQ", "value": key}]}], "limit": 1},
                )
                resp.raise_for_status()
                results = resp.json().get("results", [])
                if results:
                    doc = results[0]
                    return CrmReadResult(True, doc.get("id"), doc.get("properties", {}), {})
            return CrmReadResult(found=False)
        except httpx.HTTPError:
            return CrmReadResult(found=False)

    def write(self, session, tenant_id, object_type, external_id, properties) -> CrmWriteResult:
        path = self._object_path(object_type)
        try:
            if external_id:
                resp = self._client.patch(
                    f"/crm/v3/objects/{path}/{external_id}", json={"properties": properties}
                )
            else:
                resp = self._client.post(f"/crm/v3/objects/{path}", json={"properties": properties})
            if resp.status_code == 429:
                return CrmWriteResult(False, error="rate limited", retryable=True, status_code=429)
            if resp.status_code >= 500:
                return CrmWriteResult(False, error=f"server error {resp.status_code}",
                                      retryable=True, status_code=resp.status_code)
            if resp.status_code >= 400:
                return CrmWriteResult(False, error=f"client error {resp.status_code}",
                                      retryable=False, status_code=resp.status_code)
            doc = resp.json()
            return CrmWriteResult(True, doc.get("id"), status_code=resp.status_code)
        except httpx.TimeoutException:
            return CrmWriteResult(False, error="timeout", retryable=True)
        except httpx.HTTPError as exc:
            return CrmWriteResult(False, error=f"transport error: {type(exc).__name__}", retryable=True)


def get_adapter(system: str, mode: str = "simulator") -> CrmAdapter:
    if system == "hubspot" and mode == "live":
        return HubSpotCrmAdapter()
    return SimulatorCrmAdapter()


def map_properties(entity_type: str, system: str, canonical: dict) -> dict:
    """Map canonical field values to CRM property names (unknown fields dropped)."""
    if system == "hubspot":
        table = HUBSPOT_CONTACT_PROPERTIES if entity_type == "contact" else HUBSPOT_COMPANY_PROPERTIES
    else:
        # Simulator uses canonical names with domain/email natural keys
        table = {f: f for f in canonical}
        if entity_type == "account":
            table["root_domain"] = "domain"
        else:
            table["work_email"] = "email"
    return {table[f]: v for f, v in canonical.items() if f in table and v is not None}
