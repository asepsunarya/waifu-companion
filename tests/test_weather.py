import http.client
import http.server
import io
import json
import threading
import urllib.error
import urllib.parse
import urllib.request

import pytest

from waifu.weather import Weather, WeatherClient, WeatherError, get_json

FORECAST_HOST = "api.open-meteo.com"
GEOCODING_HOST = "geocoding-api.open-meteo.com"

# A geocoding result shaped like Open-Meteo's documented reply (empty fields are left out).
BANDUNG = {
    "id": 1650357,
    "name": "Bandung",
    "latitude": -6.90389,
    "longitude": 107.61861,
    "elevation": 768.0,
    "feature_code": "PPLA",
    "country_code": "ID",
    "admin1_id": 1642672,
    "timezone": "Asia/Jakarta",
    "population": 1699719,
    "country_id": 1643084,
    "country": "Indonesia",
    "admin1": "Jawa Barat",
}


def geocoding_reply(*results: dict) -> dict:
    return {"results": list(results), "generationtime_ms": 0.62}


def forecast_reply(
    *,
    today: str = "2026-10-13",
    tomorrow: str = "2026-10-14",
    current_code: int = 2,
    tomorrow_morning_code: int = 61,
) -> dict:
    """A forecast shaped like Open-Meteo's reply to the SPEC's request (timezone=auto, 2 days)."""
    hours = [f"{day}T{hour:02d}:00" for day in (today, tomorrow) for hour in range(24)]
    codes = [3] * 48
    codes[7] = 0  # today 07:00, which tomorrow_morning must not read
    codes[24 + 6] = 45
    codes[24 + 7] = tomorrow_morning_code
    codes[24 + 8] = 80
    return {
        "latitude": -6.9,
        "longitude": 107.625,
        "generationtime_ms": 0.08,
        "utc_offset_seconds": 25200,
        "timezone": "Asia/Jakarta",
        "timezone_abbreviation": "GMT+7",
        "elevation": 768.0,
        "current_units": {
            "time": "iso8601",
            "interval": "seconds",
            "temperature_2m": "°C",
            "weather_code": "wmo code",
        },
        "current": {
            "time": f"{today}T07:15",
            "interval": 900,
            "temperature_2m": 21.4,
            "weather_code": current_code,
        },
        "hourly_units": {"time": "iso8601", "weather_code": "wmo code"},
        "hourly": {"time": hours, "weather_code": codes},
        "daily_units": {
            "time": "iso8601",
            "weather_code": "wmo code",
            "temperature_2m_max": "°C",
            "temperature_2m_min": "°C",
            "precipitation_probability_max": "%",
        },
        "daily": {
            "time": [today, tomorrow],
            "weather_code": [95, 3],
            "temperature_2m_max": [27.9, 28.3],
            "temperature_2m_min": [18.6, 18.9],
            "precipitation_probability_max": [85, 40],
        },
    }


class FakeClock:
    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class FakeOpenMeteo:
    """The fetch function: records every URL and timeout and answers by host; never touches
    the network. With a `clock`, each request takes `took_s` on it."""

    def __init__(
        self,
        *,
        forecast: object = None,
        geocoding: object = None,
        clock: FakeClock | None = None,
        took_s: float = 0.0,
    ) -> None:
        self.replies = {
            FORECAST_HOST: forecast if forecast is not None else forecast_reply(),
            GEOCODING_HOST: geocoding if geocoding is not None else geocoding_reply(BANDUNG),
        }
        self.clock = clock
        self.took_s = took_s
        self.urls: list[str] = []
        self.timeouts: list[float] = []

    def __call__(self, url: str, timeout_s: float) -> object:
        self.urls.append(url)
        self.timeouts.append(timeout_s)
        if self.clock is not None:
            self.clock.now += self.took_s
        reply = self.replies[urllib.parse.urlsplit(url).hostname]
        if isinstance(reply, BaseException):
            raise reply
        return reply

    def requests_to(self, host: str) -> list[dict[str, list[str]]]:
        return [
            urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            for url in self.urls
            if urllib.parse.urlsplit(url).hostname == host
        ]


def test_today_reads_the_weather_at_the_configured_coordinates():
    fetch = FakeOpenMeteo()

    weather = WeatherClient(latitude=-6.9147, longitude=107.6098, fetch=fetch).today()

    assert weather == Weather(
        place="-6.9147, 107.6098",
        condition="berawan",
        temperature_c=21.4,
        min_c=18.6,
        max_c=27.9,
        rain_chance=85,
        tomorrow_morning="hujan",
    )


def test_the_forecast_request_asks_for_the_spec_parameters():
    fetch = FakeOpenMeteo()

    WeatherClient(latitude=-6.9147, longitude=107.6098, fetch=fetch).today()

    assert len(fetch.urls) == 1
    url = urllib.parse.urlsplit(fetch.urls[0])
    assert (url.scheme, url.hostname, url.path) == ("https", FORECAST_HOST, "/v1/forecast")
    assert urllib.parse.parse_qs(url.query) == {
        "latitude": ["-6.9147"],
        "longitude": ["107.6098"],
        "current": ["temperature_2m,weather_code"],
        "daily": [
            "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max"
        ],
        "hourly": ["weather_code"],
        "timezone": ["auto"],
        "forecast_days": ["2"],
    }


def test_a_city_is_geocoded_once_in_indonesian_and_its_coordinates_forecast():
    fetch = FakeOpenMeteo()
    client = WeatherClient(city="Bandung", fetch=fetch)

    client.today()
    client.today()

    geocoding = fetch.requests_to(GEOCODING_HOST)
    assert geocoding == [{"name": ["Bandung"], "count": ["1"], "language": ["id"]}]
    assert urllib.parse.urlsplit(fetch.urls[0]).path == "/v1/search"
    forecasts = fetch.requests_to(FORECAST_HOST)
    assert len(forecasts) == 2
    assert {key: forecasts[0][key] for key in ("latitude", "longitude")} == {
        "latitude": ["-6.90389"],
        "longitude": ["107.61861"],
    }


def test_a_city_name_with_spaces_is_percent_encoded():
    fetch = FakeOpenMeteo()

    WeatherClient(city="Tanjung Pinang", fetch=fetch).today()

    assert "name=Tanjung%20Pinang&" in fetch.urls[0]


def test_the_place_is_the_geocoded_name_with_its_province():
    fetch = FakeOpenMeteo()

    weather = WeatherClient(city="bandung", fetch=fetch).today()

    assert weather.place == "Bandung, Jawa Barat"


@pytest.mark.parametrize(
    "result",
    [
        {**BANDUNG, "name": "Jawa Barat"},
        {key: value for key, value in BANDUNG.items() if key != "admin1"},
    ],
    ids=["province with the same name", "no province"],
)
def test_the_place_is_only_the_name_when_the_province_adds_nothing(result):
    fetch = FakeOpenMeteo(geocoding=geocoding_reply(result))

    weather = WeatherClient(city="Bandung", fetch=fetch).today()

    assert weather.place == result["name"]


def test_coordinates_win_over_the_city_which_still_names_the_place():
    # She says "Surabaya", never "minus tujuh koma dua lima".
    fetch = FakeOpenMeteo()

    weather = WeatherClient(latitude=-7.25, longitude=112.75, city="Surabaya", fetch=fetch).today()

    assert fetch.requests_to(GEOCODING_HOST) == []
    [forecast] = fetch.requests_to(FORECAST_HOST)
    assert (forecast["latitude"], forecast["longitude"]) == (["-7.25"], ["112.75"])
    assert weather.place == "Surabaya"


def test_no_location_is_a_weather_error_saying_how_to_set_the_city():
    fetch = FakeOpenMeteo()

    with pytest.raises(WeatherError, match="briefing.city"):
        WeatherClient(fetch=fetch).today()

    assert fetch.urls == []


def test_a_latitude_without_a_longitude_is_refused():
    with pytest.raises(ValueError):
        WeatherClient(latitude=-6.9, fetch=FakeOpenMeteo())


@pytest.mark.parametrize(
    "reply", [{"generationtime_ms": 0.3}, {"results": [], "generationtime_ms": 0.3}]
)
def test_a_city_open_meteo_does_not_know_is_a_weather_error_naming_it(reply):
    fetch = FakeOpenMeteo(geocoding=reply)

    with pytest.raises(WeatherError, match="Kota Bandungg tidak ditemukan"):
        WeatherClient(city="Bandungg", fetch=fetch).today()

    assert fetch.requests_to(FORECAST_HOST) == []


def test_a_failed_geocoding_is_tried_again_on_the_next_call():
    fetch = FakeOpenMeteo(geocoding=urllib.error.URLError("no route"))
    client = WeatherClient(city="Bandung", fetch=fetch)
    with pytest.raises(WeatherError):
        client.today()
    fetch.replies[GEOCODING_HOST] = geocoding_reply(BANDUNG)

    weather = client.today()

    assert weather.place == "Bandung, Jawa Barat"
    assert len(fetch.requests_to(GEOCODING_HOST)) == 2


@pytest.mark.parametrize(
    ("code", "condition"),
    [
        (0, "cerah"),
        (1, "cerah berawan"),
        (2, "berawan"),
        (3, "mendung"),
        (45, "berkabut"),
        (48, "berkabut"),
        (51, "gerimis"),
        (55, "gerimis"),
        (57, "gerimis"),
        (61, "hujan"),
        (63, "hujan"),
        (65, "hujan deras"),
        (66, "hujan"),
        (67, "hujan deras"),
        (80, "hujan lokal"),
        (81, "hujan lokal"),
        (82, "hujan lokal deras"),
        (95, "badai petir"),
        (96, "badai petir"),
        (99, "badai petir"),
        (71, "salju"),
        (77, "salju"),
        (85, "salju"),
        (86, "salju"),
        (4, "tidak diketahui"),
        (50, "tidak diketahui"),
        (79, "tidak diketahui"),
        (100, "tidak diketahui"),
    ],
)
def test_wmo_weather_codes_become_indonesian_conditions(code, condition):
    fetch = FakeOpenMeteo(forecast=forecast_reply(current_code=code, tomorrow_morning_code=code))

    weather = WeatherClient(latitude=-6.9, longitude=107.6, fetch=fetch).today()

    assert (weather.condition, weather.tomorrow_morning) == (condition, condition)


def test_tomorrow_morning_is_07_00_of_the_day_after_today_across_a_month_end():
    reply = forecast_reply(today="2026-10-31", tomorrow="2026-11-01", tomorrow_morning_code=95)
    fetch = FakeOpenMeteo(forecast=reply)

    weather = WeatherClient(latitude=-6.9, longitude=107.6, fetch=fetch).today()

    assert weather.tomorrow_morning == "badai petir"


def test_a_whole_number_rain_chance_sent_as_a_float_is_an_int():
    reply = forecast_reply()
    reply["daily"]["precipitation_probability_max"] = [85.0, 40.0]
    fetch = FakeOpenMeteo(forecast=reply)

    weather = WeatherClient(latitude=-6.9, longitude=107.6, fetch=fetch).today()

    assert weather.rain_chance == 85
    assert isinstance(weather.rain_chance, int)


MALFORMED_FORECASTS = {
    "no current block": lambda reply: reply.pop("current"),
    "no temperature now": lambda reply: reply["current"].pop("temperature_2m"),
    "temperature is null": lambda reply: reply["current"].update(temperature_2m=None),
    "temperature is text": lambda reply: reply["current"].update(temperature_2m="21.4"),
    "weather code is a boolean": lambda reply: reply["current"].update(weather_code=True),
    "no daily block": lambda reply: reply.pop("daily"),
    "no daily dates": lambda reply: reply["daily"].update(time=[]),
    "daily date is not a date": lambda reply: reply["daily"].update(time=["besok", "lusa"]),
    "no minimum": lambda reply: reply["daily"].update(temperature_2m_min=[]),
    "no maximum": lambda reply: reply["daily"].pop("temperature_2m_max"),
    "no rain chance": lambda reply: reply["daily"].pop("precipitation_probability_max"),
    "rain chance is null": lambda reply: reply["daily"].update(
        precipitation_probability_max=[None, 40]
    ),
    "no hourly block": lambda reply: reply.pop("hourly"),
    "hourly lacks tomorrow 07:00": lambda reply: reply["hourly"].update(
        time=reply["hourly"]["time"][:24]
    ),
    "hourly codes end early": lambda reply: reply["hourly"].update(
        weather_code=reply["hourly"]["weather_code"][:30]
    ),
    "hourly times are not a list": lambda reply: reply["hourly"].update(time={"0": "07:00"}),
}


@pytest.mark.parametrize("damage", MALFORMED_FORECASTS.values(), ids=MALFORMED_FORECASTS.keys())
def test_a_forecast_with_missing_or_malformed_fields_is_a_weather_error(damage):
    reply = forecast_reply()
    damage(reply)
    fetch = FakeOpenMeteo(forecast=reply)

    with pytest.raises(WeatherError, match="tidak bisa dibaca"):
        WeatherClient(latitude=-6.9, longitude=107.6, fetch=fetch).today()


@pytest.mark.parametrize("reply", [[], None, "ok"], ids=["list", "null", "text"])
def test_a_forecast_reply_that_is_not_an_object_is_a_weather_error(reply):
    fetch = FakeOpenMeteo()
    fetch.replies[FORECAST_HOST] = reply

    with pytest.raises(WeatherError, match="tidak bisa dibaca"):
        WeatherClient(latitude=-6.9, longitude=107.6, fetch=fetch).today()


@pytest.mark.parametrize(
    "reply",
    [
        [],
        {"results": [{"name": "Bandung"}]},
        {"results": [{"latitude": -6.9, "longitude": 107.6}]},
        {"results": [{**BANDUNG, "name": ""}]},
        {"results": [{**BANDUNG, "latitude": "-6.9"}]},
        {"results": "Bandung"},
    ],
    ids=["not an object", "no coordinates", "no name", "empty name", "text latitude", "text"],
)
def test_a_malformed_geocoding_reply_is_a_weather_error(reply):
    fetch = FakeOpenMeteo(geocoding=reply)

    with pytest.raises(WeatherError, match="tidak bisa dibaca"):
        WeatherClient(city="Bandung", fetch=fetch).today()

    assert fetch.requests_to(FORECAST_HOST) == []


@pytest.mark.parametrize(
    "error",
    [
        urllib.error.URLError("getaddrinfo failed"),
        TimeoutError("timed out"),
        ConnectionResetError("reset"),
        http.client.IncompleteRead(b"{"),
    ],
    ids=["url error", "timeout", "connection reset", "incomplete read"],
)
def test_an_unreachable_server_is_a_weather_error_about_the_connection(error):
    fetch = FakeOpenMeteo(forecast=error)

    with pytest.raises(WeatherError, match="koneksi internet"):
        WeatherClient(latitude=-6.9, longitude=107.6, fetch=fetch).today()


def test_an_http_error_is_a_weather_error_naming_the_status():
    error = urllib.error.HTTPError(
        "https://api.open-meteo.com/v1/forecast", 503, "Service Unavailable", None, None
    )
    fetch = FakeOpenMeteo(geocoding=error)

    with pytest.raises(WeatherError, match="HTTP 503"):
        WeatherClient(city="Bandung", fetch=fetch).today()


@pytest.mark.parametrize(
    "error",
    [
        json.JSONDecodeError("Expecting value", "<html>", 0),
        UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"),
    ],
    ids=["not json", "not utf-8"],
)
def test_a_reply_that_is_not_json_is_a_weather_error(error):
    fetch = FakeOpenMeteo(forecast=error)

    with pytest.raises(WeatherError, match="tidak bisa dibaca"):
        WeatherClient(latitude=-6.9, longitude=107.6, fetch=fetch).today()


@pytest.mark.parametrize(
    ("took_s", "timeouts"), [(0.0, [8.0, 8.0]), (3.0, [8.0, 5.0]), (7.0, [8.0, 1.0])]
)
def test_the_city_lookup_and_the_forecast_share_one_8_s_limit(took_s, timeouts):
    # She is silent while the weather is fetched: a slow Open-Meteo must not hold her for
    # a full timeout per request.
    clock = FakeClock()
    fetch = FakeOpenMeteo(clock=clock, took_s=took_s)

    WeatherClient(city="Bandung", fetch=fetch, clock=clock).today()

    assert fetch.timeouts == timeouts


def test_the_forecast_is_not_asked_with_less_than_a_second_left_and_the_city_is_kept():
    clock = FakeClock()
    fetch = FakeOpenMeteo(clock=clock, took_s=7.5)  # the city lookup alone took 7.5 s
    client = WeatherClient(city="Bandung", fetch=fetch, clock=clock)

    with pytest.raises(WeatherError, match="koneksi internet"):
        client.today()
    skipped = fetch.requests_to(FORECAST_HOST)
    fetch.took_s = 0.0
    weather = client.today()

    assert skipped == []
    assert weather.place == "Bandung, Jawa Barat"
    assert len(fetch.requests_to(GEOCODING_HOST)) == 1
    assert fetch.timeouts == [8.0, 8.0]  # the next call asks only the forecast, with 8 s


def test_the_default_fetch_is_a_urllib_get_with_the_time_left_and_our_user_agent(monkeypatch):
    seen: list[tuple[urllib.request.Request, float]] = []

    def fake_urlopen(request: urllib.request.Request, *, timeout: float) -> io.BytesIO:
        seen.append((request, timeout))
        return io.BytesIO(json.dumps(forecast_reply()).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    weather = WeatherClient(latitude=-6.9, longitude=107.6, clock=FakeClock()).today()

    assert weather.condition == "berawan"
    request, timeout = seen[0]
    assert urllib.parse.urlsplit(request.full_url).hostname == FORECAST_HOST
    assert request.get_header("User-agent").startswith("waifu-companion")
    assert timeout == 8.0


class _ForecastHandler(http.server.BaseHTTPRequestHandler):
    seen: list[tuple[str, str]] = []

    def do_GET(self):  # noqa: N802 - http.server naming
        self.seen.append((self.path, self.headers["User-Agent"]))
        body = json.dumps({"timezone": "Asia/Jakarta", "note": "cerah"}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def test_get_json_reads_utf8_json_over_http_with_our_user_agent():
    server = http.server.HTTPServer(("127.0.0.1", 0), _ForecastHandler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    try:
        data = get_json(f"http://127.0.0.1:{server.server_port}/v1/forecast?timezone=auto", 5.0)
    finally:
        thread.join(5)
        server.server_close()

    assert data == {"timezone": "Asia/Jakarta", "note": "cerah"}
    path, user_agent = _ForecastHandler.seen[-1]
    assert path == "/v1/forecast?timezone=auto"
    assert user_agent.startswith("waifu-companion")
