"""Open-Meteo weather. Two HTTP calls leave the device:
  1. Geocoding API with `name=<location_text>` only.
  2. Forecast API with the resolved lat/lon only.
No microphone audio, no home logs, no precise device coordinates."""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.schemas import PlannerOutput, ToolResult
from app.settings import Settings
from app.tool_registry import ToolRegistry

log = logging.getLogger(__name__)

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"


WEATHER_CODES = {
    0: "clear sky",
    1: "mainly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "fog",
    48: "depositing rime fog",
    51: "light drizzle",
    53: "moderate drizzle",
    55: "dense drizzle",
    61: "slight rain",
    63: "moderate rain",
    65: "heavy rain",
    71: "slight snow",
    73: "moderate snow",
    75: "heavy snow",
    80: "rain showers",
    81: "moderate rain showers",
    82: "violent rain showers",
    95: "thunderstorm",
}


async def _geocode(location: str) -> dict[str, Any] | None:
    # 12 s tolerates occasional open-meteo CDN cold edges.
    async with httpx.AsyncClient(timeout=12.0) as c:
        r = await c.get(GEOCODE_URL, params={"name": location, "count": 1})
        r.raise_for_status()
        results = r.json().get("results") or []
        return results[0] if results else None


async def _forecast(lat: float, lon: float, action: str) -> dict[str, Any]:
    daily = "weather_code,precipitation_sum,precipitation_probability_max,temperature_2m_max,temperature_2m_min"
    hourly = "precipitation_probability"
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,weather_code,precipitation",
        "daily": daily,
        "hourly": hourly,
        "timezone": "auto",
        "forecast_days": 1,
    }
    # Forecast endpoint sometimes takes 5-12 s; one retry on TimeoutException
    # avoids surfacing transient slowness as "couldn't reach weather service".
    last_err: Exception | None = None
    for attempt in (1, 2):
        try:
            async with httpx.AsyncClient(timeout=15.0) as c:
                r = await c.get(FORECAST_URL, params=params)
                r.raise_for_status()
                return r.json()
        except (httpx.TimeoutException, httpx.ReadError) as e:
            last_err = e
            log.warning("forecast attempt %d/2 slow: %s", attempt, type(e).__name__)
            if attempt == 2:
                raise
    raise last_err if last_err else RuntimeError("forecast retry loop exited unexpectedly")


def register(registry: ToolRegistry, settings: Settings) -> None:
    async def handler(plan: PlannerOutput) -> ToolResult:
        location = (
            plan.arguments.get("location_text")
            or plan.arguments.get("location")
            or ""
        ).strip()
        if not location:
            return ToolResult(
                ok=False,
                error="No location supplied",
                speak="Which location should I check?",
            )
        try:
            geo = await _geocode(location)
            if geo is None:
                return ToolResult(
                    ok=False,
                    error="No geocode match",
                    speak=f"I couldn't find {location} on the map.",
                )
            data = await _forecast(geo["latitude"], geo["longitude"], plan.action)
            current = data.get("current", {})
            daily = data.get("daily", {})
            code = current.get("weather_code")
            condition = WEATHER_CODES.get(code, f"weather code {code}")
            temp = current.get("temperature_2m")
            rain_prob = (daily.get("precipitation_probability_max") or [None])[0]
            high = (daily.get("temperature_2m_max") or [None])[0]
            low = (daily.get("temperature_2m_min") or [None])[0]
            place = geo.get("name") or location

            if plan.action == "rain_probability":
                speak = (
                    f"In {place}, the chance of rain today is "
                    f"{rain_prob}%." if rain_prob is not None
                    else f"In {place}, I couldn't get rain probability."
                )
            elif plan.action == "current":
                speak = f"In {place} it's {temp} degrees with {condition}."
            else:
                speak = (
                    f"In {place} expect {condition}, high {high} and low {low}, "
                    f"chance of rain {rain_prob}%."
                )
            return ToolResult(
                ok=True,
                data={
                    "location": place,
                    "country": geo.get("country"),
                    "latitude": geo["latitude"],
                    "longitude": geo["longitude"],
                    "temperature_c": temp,
                    "weather_code": code,
                    "rain_probability_percent": rain_prob,
                },
                speak=speak,
            )
        except httpx.HTTPError as e:
            # ReadTimeout stringifies as "" — capture the type for actionable logs.
            err = f"{type(e).__name__}: {e}".rstrip(": ")
            log.warning("weather fetch failed: %s", err)
            return ToolResult(
                ok=False, error=err, speak="I had trouble reaching the weather service."
            )

    registry.register("weather.live", handler)
