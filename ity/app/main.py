from __future__ import annotations

import asyncio
import random
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from dateutil import parser as dtparse
from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import HTMLResponse, ORJSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from rapidfuzz import fuzz

app = FastAPI(title="ITY MVP", default_response_class=ORJSONResponse)

templates = Jinja2Templates(directory="app/templates")


# ----------------------------
# Domain models
# ----------------------------
class AppointmentSlot(BaseModel):
	provider: str
	provider_location_id: str
	provider_location_name: str
	stylist_name: Optional[str] = None
	service_name: str
	start_time: datetime
	duration_minutes: int
	price_cents: int
	currency: str = "USD"
	latitude: Optional[float] = None
	longitude: Optional[float] = None
	provider_url: Optional[str] = None
	provider_internal_id: Optional[str] = None
	score: Optional[float] = None


class SearchRequest(BaseModel):
	query: Optional[str] = None
	when: Optional[str] = None
	budget_max: Optional[float] = Field(None, description="Max price in USD")
	distance_miles_max: Optional[float] = None
	service: Optional[str] = None
	stylist: Optional[str] = None
	lat: Optional[float] = None
	lng: Optional[float] = None
	limit: int = 25


class BookingRequest(BaseModel):
	provider: str
	provider_internal_id: str
	customer_name: str
	customer_phone: str


# ----------------------------
# Mock connectors (Square, Vagaro)
# ----------------------------
async def connector_square_haircuts(search: SearchRequest) -> List[AppointmentSlot]:
	base_time = datetime.utcnow().replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
	slots: List[AppointmentSlot] = []
	for i in range(10):
		start = base_time + timedelta(hours=random.randint(0, 48))
		price = random.choice([2500, 3000, 3500, 4000, 4500])
		slot = AppointmentSlot(
			provider="square",
			provider_location_id=f"sq_loc_{random.randint(1,3)}",
			provider_location_name=random.choice(["Downtown Cuts", "Clip & Go", "Fade Factory"]),
			stylist_name=random.choice(["Alex", "Jamie", "Riley", None]),
			service_name=random.choice(["Men's Cut", "Women's Cut", "Kids Cut", "Fade + Beard"]),
			start_time=start,
			duration_minutes=random.choice([30, 45, 60]),
			price_cents=price,
			provider_url="https://example.com/square/booking",
			provider_internal_id=f"sq_{i}",
			latitude=(search.lat or 37.7749) + random.uniform(-0.02, 0.02),
			longitude=(search.lng or -122.4194) + random.uniform(-0.02, 0.02),
		)
		slots.append(slot)
	await asyncio.sleep(0.05)
	return slots


async def connector_vagaro_haircuts(search: SearchRequest) -> List[AppointmentSlot]:
	base_time = datetime.utcnow().replace(minute=0, second=0, microsecond=0) + timedelta(hours=2)
	slots: List[AppointmentSlot] = []
	for i in range(10):
		start = base_time + timedelta(hours=random.randint(0, 72))
		price = random.choice([2000, 2800, 3200, 3800, 5000])
		slot = AppointmentSlot(
			provider="vagaro",
			provider_location_id=f"vg_loc_{random.randint(1,3)}",
			provider_location_name=random.choice(["Shear Genius", "Salon Nova", "Urban Trim"]),
			stylist_name=random.choice(["Taylor", "Morgan", None]),
			service_name=random.choice(["Trim", "Full Cut", "Blowout + Cut"]),
			start_time=start,
			duration_minutes=random.choice([30, 45, 60]),
			price_cents=price,
			provider_url="https://example.com/vagaro/booking",
			provider_internal_id=f"vg_{i}",
			latitude=(search.lat or 37.7749) + random.uniform(-0.03, 0.03),
			longitude=(search.lng or -122.4194) + random.uniform(-0.03, 0.03),
		)
		slots.append(slot)
	await asyncio.sleep(0.05)
	return slots


# ----------------------------
# Normalization, ranking, simple NLP parsing
# ----------------------------
class NormalizedSlot(BaseModel):
	id: str
	provider: str
	location_name: str
	service_name: str
	stylist_name: Optional[str]
	start_time: datetime
	duration_minutes: int
	price_cents: int
	currency: str
	latitude: Optional[float]
	longitude: Optional[float]
	book_url: Optional[str]
	score: float


def parse_when_text(when_text: Optional[str]) -> Optional[datetime]:
	if not when_text:
		return None
	text = when_text.lower().strip()
	try:
		if text in {"now", "asap", "today"}:
			base = datetime.utcnow()
			return base
		if text in {"tomorrow"}:
			base = datetime.utcnow().replace(hour=9, minute=0, second=0, microsecond=0) + timedelta(days=1)
			return base
		# Try dateutil parsing for more formats like "friday evening", "2025-08-01 5pm"
		return dtparse.parse(when_text, fuzzy=True, default=datetime.utcnow())
	except Exception:
		return None


def compute_score(slot: AppointmentSlot, search: SearchRequest) -> float:
	# Lower price, earlier time, stylist match, service match, proximity (mocked via small coordinate deltas)
	price_score = max(0.0, 1.0 - (slot.price_cents / 100.0) / 100.0)  # $0->$100 scale
	early_bonus = 0.2 if slot.start_time < datetime.utcnow() + timedelta(hours=24) else 0.0
	service_match = fuzz.partial_ratio((search.service or "").lower(), slot.service_name.lower()) / 100.0 if search.service else 0.0
	stylist_match = 1.0 if (search.stylist and slot.stylist_name and search.stylist.lower() in slot.stylist_name.lower()) else 0.0
	proximity = 0.0
	if search.lat is not None and search.lng is not None and slot.latitude and slot.longitude:
		proximity = max(0.0, 1.0 - (abs(slot.latitude - search.lat) + abs(slot.longitude - search.lng)) / 0.1)
	return round(price_score + early_bonus + 0.5 * service_match + 0.3 * stylist_match + 0.5 * proximity, 4)


def normalize_slots(slots: List[AppointmentSlot], search: SearchRequest) -> List[NormalizedSlot]:
	normalized: List[NormalizedSlot] = []
	for s in slots:
		s.score = compute_score(s, search)
		normalized.append(
			NormalizedSlot(
				id=f"{s.provider}:{s.provider_internal_id}",
				provider=s.provider,
				location_name=s.provider_location_name,
				service_name=s.service_name,
				stylist_name=s.stylist_name,
				start_time=s.start_time,
				duration_minutes=s.duration_minutes,
				price_cents=s.price_cents,
				currency=s.currency,
				latitude=s.latitude,
				longitude=s.longitude,
				book_url=s.provider_url,
				score=s.score or 0.0,
			)
		)
	return normalized


def conversational_to_search(query: Optional[str]) -> SearchRequest:
	if not query:
		return SearchRequest()
	q = query.lower()
	budget = None
	service = None
	when = None
	stylist = None
	lat = None
	lng = None
	# Heuristics
	for token in q.replace("$", "").replace(",", "").split():
		if token.isdigit():
			value = int(token)
			if value < 200:  # treat as dollars budget
				budget = float(value)
	if "fade" in q:
		service = "fade"
	elif "women" in q:
		service = "Women's Cut"
	elif "men" in q:
		service = "Men's Cut"
	if "today" in q or "asap" in q:
		when = "today"
	elif "tomorrow" in q:
		when = "tomorrow"
	# very basic stylist extraction
	if "with " in q:
		stylist = q.split("with ", 1)[1].split()[0].strip(".,!")
	return SearchRequest(query=query, budget_max=budget, service=service, when=when, stylist=stylist, lat=lat, lng=lng)


# ----------------------------
# Dependencies
# ----------------------------
async def get_templates() -> Jinja2Templates:
	return templates


# ----------------------------
# Routes
# ----------------------------
@app.get("/", response_class=HTMLResponse)
async def home(request: Request, q: Optional[str] = Query(None)):
	if q:
		# redirect to search results template
		search_req = conversational_to_search(q)
		results = await search_haircuts_internal(search_req)
		return templates.TemplateResponse("results.html", {"request": request, "q": q, "results": results, "format_price": format_price})
	return templates.TemplateResponse("index.html", {"request": request})


def format_price(cents: int) -> str:
	return f"${cents/100:.2f}"


async def search_haircuts_internal(search: SearchRequest) -> List[NormalizedSlot]:
	# parse when
	parsed_when = parse_when_text(search.when)
	# Fetch from connectors concurrently
	connector_tasks = [
		asyncio.create_task(connector_square_haircuts(search)),
		asyncio.create_task(connector_vagaro_haircuts(search)),
	]
	raw_results_groups = await asyncio.gather(*connector_tasks)
	raw_results: List[AppointmentSlot] = [slot for group in raw_results_groups for slot in group]
	# Filter by budget and when if provided
	filtered: List[AppointmentSlot] = []
	for s in raw_results:
		if search.budget_max is not None and (s.price_cents / 100.0) > search.budget_max:
			continue
		if parsed_when is not None and s.start_time < parsed_when:
			continue
		filtered.append(s)
	# Normalize and rank
	normalized = normalize_slots(filtered, search)
	normalized.sort(key=lambda x: (-x.score, x.price_cents, x.start_time))
	return normalized[: search.limit]


@app.get("/api/search/haircuts")
async def api_search_haircuts(
	q: Optional[str] = Query(None, description="Conversational query"),
	when: Optional[str] = Query(None),
	budget_max: Optional[float] = Query(None),
	distance_miles_max: Optional[float] = Query(None),
	service: Optional[str] = Query(None),
	stylist: Optional[str] = Query(None),
	lat: Optional[float] = Query(None),
	lng: Optional[float] = Query(None),
	limit: int = Query(25),
):
	search = conversational_to_search(q) if q else SearchRequest(
		when=when, budget_max=budget_max, distance_miles_max=distance_miles_max, service=service, stylist=stylist, lat=lat, lng=lng, limit=limit
	)
	results = await search_haircuts_internal(search)
	return {"results": [r.model_dump() for r in results]}


@app.post("/api/book")
async def api_book(req: BookingRequest):
	# This is a mock booking endpoint
	confirmation = {
		"status": "confirmed",
		"provider": req.provider,
		"provider_internal_id": req.provider_internal_id,
		"confirmation_code": f"CONF-{random.randint(10000,99999)}",
	}
	return confirmation


@app.get("/healthz")
async def healthz():
	return {"ok": True, "time": datetime.utcnow().isoformat()}