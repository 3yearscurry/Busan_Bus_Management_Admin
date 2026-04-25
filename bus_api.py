import hashlib
import hmac
import re
import threading
import time

import requests


BUS_TYPE = {
    "normal": {"name": "일반", "color": "#3d74db", "bg": "#e8f0ff"},
    "express": {"name": "급행", "color": "#e03030", "bg": "#ffebeb"},
    "village": {"name": "마을", "color": "#5aad46", "bg": "#edf7eb"},
    "night": {"name": "심야", "color": "#8b4ebe", "bg": "#f3ebff"},
    "airport": {"name": "공항", "color": "#8b4ebe", "bg": "#f3ebff"},
}

_CLOUD_RUN_PATHS = {
    "busInfo": "/bus/routes",
    "busStopList": "/bus/stops",
    "busInfoByRouteId": "/bus/route-stations",
    "bitArrByArsno": "/bus/arrivals",
}


class BusanBusAPI:
    DEFAULT_MAIN_ROWS = 18
    DEFAULT_MAIN_STOP = "부산시청"

    def __init__(self, config=None):
        if isinstance(config, dict):
            self.proxy_base_url = str(config.get("proxy_base_url") or config.get("base_url") or "").strip().rstrip("/")
            self.hmac_key = str(config.get("hmac_key") or "").strip()
        else:
            self.proxy_base_url = ""
            self.hmac_key = ""

        if not self.proxy_base_url:
            raise ValueError("서버에 연결할 수 없습니다. 인터넷 연결을 확인해 주세요.")
        self._session = requests.Session()
        self._cache_lock = threading.RLock()
        self._route_cache = {}
        self._search_routes_cache = {}
        self._route_stations_cache = {}
        self._arrivals_cache = {}

    def _hmac_headers(self, path: str) -> dict:
        if not self.hmac_key:
            return {}
        ts = str(int(time.time()))
        msg = f"{ts}:{path}".encode()
        sig = hmac.new(self.hmac_key.encode(), msg, hashlib.sha256).hexdigest()
        return {"X-Timestamp": ts, "X-Signature": sig}

    def _get_cached(self, cache, key, ttl):
        with self._cache_lock:
            item = cache.get(key)
            if not item:
                return None
            cached_at, value = item
            if time.time() - cached_at > ttl:
                cache.pop(key, None)
                return None
            return value

    def _set_cached(self, cache, key, value):
        with self._cache_lock:
            cache[key] = (time.time(), value)

    def _get(self, path, params=None):
        cloud_path = _CLOUD_RUN_PATHS.get(path)
        if not cloud_path:
            raise RuntimeError(f"알 수 없는 API 경로: {path}")
        return self._get_proxy(cloud_path, params or {})

    def _extract_items_from_json(self, payload):
        if isinstance(payload, list):
            return payload
        if not isinstance(payload, dict):
            return []

        candidates = [
            payload.get("items"),
            payload.get("data"),
            payload.get("results"),
            payload.get("response", {}).get("items") if isinstance(payload.get("response"), dict) else None,
            payload.get("response", {}).get("body", {}).get("items") if isinstance(payload.get("response"), dict) else None,
        ]
        for candidate in candidates:
            if isinstance(candidate, list):
                return candidate
            if isinstance(candidate, dict):
                for key in ("item", "items"):
                    value = candidate.get(key)
                    if isinstance(value, list):
                        return value
        return []

    def _get_proxy(self, cloud_path, params):
        query = {
            k: v for k, v in {
                "arsno": params.get("arsno") or None,
                "lineid": params.get("lineid") or None,
                "lineno": params.get("lineno") or None,
                "bstopnm": params.get("bstopnm") or None,
                "page_no": params.get("pageNo", 1),
                "num_of_rows": params.get("numOfRows", 100),
            }.items() if v is not None
        }
        url = f"{self.proxy_base_url}{cloud_path}"
        try:
            response = self._session.get(
                url,
                params=query,
                headers=self._hmac_headers(cloud_path),
                timeout=10,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"버스 API 요청 실패: {exc.__class__.__name__}") from exc

        if not response.ok:
            raise RuntimeError(f"버스 API 요청 실패: HTTP {response.status_code}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError("버스 API 응답을 JSON으로 해석할 수 없습니다.") from exc

        return [
            item if isinstance(item, dict) else {"value": item}
            for item in self._extract_items_from_json(payload)
        ]

    def _item_to_dict(self, item):
        return {child.tag: (child.text or "").strip() for child in list(item)}

    # ── 메인 보드 ─────────────────────────────────────────────
    def get_main_board(self):
        items = self._get("busInfo", {"pageNo": "1", "numOfRows": str(self.DEFAULT_MAIN_ROWS)})
        return [self._fmt_route(i) for i in items[: self.DEFAULT_MAIN_ROWS]]

    # ── 노선 검색 ─────────────────────────────────────────────
    def search_routes(self, query):
        query_key = str(query or "").strip()
        cached = self._get_cached(self._search_routes_cache, query_key, ttl=300)
        if cached is not None:
            return [dict(item) for item in cached]

        params = {"pageNo": "1", "numOfRows": "100"}
        if query_key:
            params["lineno"] = query_key
        items = self._get("busInfo", params)
        routes = [self._fmt_route(i) for i in items]
        self._set_cached(self._search_routes_cache, query_key, [dict(item) for item in routes])
        return routes

    # ── 정류소 검색 ───────────────────────────────────────────
    def search_stations(self, query):
        q = str(query or "").strip()
        params = {"pageNo": "1", "numOfRows": "100"}
        if q.isdigit():
            params["arsno"] = q
        else:
            params["bstopnm"] = q
        items = self._get("busStopList", params)
        return [self._fmt_station(i) for i in items]

    # ── 정류소 도착 정보 ──────────────────────────────────────
    def get_arrivals(self, station):
        ars_id = self._station_ars_id(station)
        if not ars_id:
            return []
        cached = self._get_cached(self._arrivals_cache, ars_id, ttl=8)
        if cached is not None:
            return [dict(item) for item in cached]
        items = self._get("bitArrByArsno", {"arsno": ars_id, "numOfRows": "100"})
        arrivals = [self._fmt_arrival(i) for i in items]
        arrivals = sorted(arrivals, key=lambda x: x.get("arrival1") if x.get("arrival1") is not None else 9999)
        self._set_cached(self._arrivals_cache, ars_id, [dict(item) for item in arrivals])
        return arrivals

    def get_station_direction_summary(self, station, arrivals=None):
        arrivals = arrivals if arrivals is not None else self.get_arrivals(station)
        endpoints = []
        for arrival in arrivals:
            endpoint = self._route_endpoint_for_arrival(arrival)
            if endpoint and endpoint not in endpoints:
                endpoints.append(endpoint)
            if len(endpoints) >= 2:
                break

        if not endpoints:
            return "방면 정보 없음"
        if len(endpoints) == 1:
            return f"{endpoints[0]} 방면"
        return f"{', '.join(endpoints)} 방면"

    # ── 노선 경유 정류장/실시간 위치 ─────────────────────────
    def get_route_stations(self, route_id):
        route_key = str(route_id or "")
        cached = self._get_cached(self._route_stations_cache, route_key, ttl=300)
        if cached is not None:
            return [dict(item) for item in cached]
        items = self._get("busInfoByRouteId", {"lineid": route_id, "numOfRows": "1000"})
        stations = [self._fmt_route_station(i) for i in items]
        stations = sorted(stations, key=lambda x: (x.get("seq", 0), x.get("name", "")))
        self._set_cached(self._route_stations_cache, route_key, [dict(item) for item in stations])
        return stations

    def get_route_station_arrivals(self, route_id, route_number, stations, focus_station=None):
        items = self._get("busInfoByRouteId", {"lineid": route_id, "numOfRows": "1000"})
        by_ars = {str(item.get("arsno", "")): item for item in items if item.get("arsno")}
        by_name = {str(item.get("bstopnm", "")): item for item in items if item.get("bstopnm")}

        mapped = {}
        if focus_station:
            focus_arrival = self._pick_route_arrival(self.get_arrivals(focus_station), route_id, route_number)
            if focus_arrival:
                for key in [focus_station.get("id"), focus_station.get("arsId")]:
                    if key:
                        mapped[key] = focus_arrival

        for station in stations:
            item = by_ars.get(str(station.get("arsId", ""))) or by_name.get(str(station.get("name", "")))
            if not item:
                continue

            car_no = item.get("carno", "")
            gps_time = item.get("gpsym", "")
            avg_sec = self._to_int(item.get("avgym"))
            if not car_no and not gps_time:
                continue

            payload = {
                "arrival1": None,
                "arrival2": self._seconds_to_min(avg_sec) if avg_sec is not None else None,
                "arrmsg1": "",
                "arrmsg2": "",
                "vehicle_no": car_no,
                "gps_time": gps_time,
                "position_only": True,
            }
            for key in [station.get("id"), station.get("arsId")]:
                if key and key not in mapped:
                    mapped[key] = payload
        self._fill_estimated_arrivals(mapped, stations, route_number)
        return mapped

    def _fill_estimated_arrivals(self, mapped, stations, route_number):
        route_stops = sorted(stations, key=lambda x: x.get("seq", 0))
        position_indices = []
        for idx, station in enumerate(route_stops):
            info = self._mapped_for_station(mapped, station)
            if info and info.get("position_only"):
                position_indices.append(idx)

        for target_idx, station in enumerate(route_stops):
            existing = self._mapped_for_station(mapped, station)
            if existing and not existing.get("position_only"):
                continue

            candidates = []
            for pos_idx in position_indices:
                if pos_idx > target_idx:
                    continue
                minutes = 0 if pos_idx == target_idx else self._estimated_minutes_between(route_stops, pos_idx, target_idx)
                vehicle = self._mapped_for_station(mapped, route_stops[pos_idx]).get("vehicle_no", "")
                candidates.append({
                    "arrival1": minutes,
                    "arrival1_stations": target_idx - pos_idx,
                    "vehicle_no": vehicle,
                })

            if not candidates:
                continue

            candidates.sort(key=lambda x: x["arrival1"])
            first = candidates[0]
            second = candidates[1] if len(candidates) > 1 else None
            payload = {
                "arrival1": first["arrival1"],
                "arrival2": second["arrival1"] if second else None,
                "arrival1_stations": first["arrival1_stations"],
                "arrival2_stations": second["arrival1_stations"] if second else None,
                "vehicle_no": first.get("vehicle_no", ""),
                "route_number": route_number,
                "estimated": True,
            }
            if existing and existing.get("position_only"):
                existing.update({
                    "arrival2": second["arrival1"] if second else None,
                    "arrival2_stations": second["arrival1_stations"] if second else None,
                })
                continue
            for key in [station.get("id"), station.get("arsId")]:
                if key and key not in mapped:
                    mapped[key] = payload

    def _mapped_for_station(self, mapped, station):
        for key in [station.get("id"), station.get("arsId")]:
            if key and key in mapped:
                return mapped[key]
        return None

    def _estimated_minutes_between(self, route_stops, start_idx, target_idx):
        total_seconds = 0
        for idx in range(start_idx + 1, target_idx + 1):
            seconds = self._to_int(route_stops[idx].get("avg_seconds"))
            if seconds is None:
                return None
            total_seconds += seconds
        return self._seconds_to_min(total_seconds)

    def _pick_route_arrival(self, arrivals, route_id, route_number, require_time=False):
        for arrival in arrivals:
            if require_time and arrival.get("arrival1") is None and arrival.get("arrival2") is None:
                continue
            if route_id and arrival.get("route_id") == route_id:
                return arrival
            if route_number and str(arrival.get("number")) == str(route_number):
                return arrival
        return None

    def _route_endpoint_for_arrival(self, arrival):
        route_id = arrival.get("route_id", "")
        route_number = arrival.get("number", "")
        if route_id in self._route_cache:
            return self._route_cache[route_id].get("end", "")

        routes = self.search_routes(route_number)
        for route in routes:
            if route.get("id") == route_id:
                self._route_cache[route_id] = route
                return route.get("end", "")
        return ""

    def get_bus_positions(self, route_id):
        items = self._get("busInfoByRouteId", {"lineid": route_id, "numOfRows": "1000"})
        return [self._fmt_bus_position(i) for i in items if i.get("carno") or i.get("gpsym")]

    # ── 포맷 헬퍼 ────────────────────────────────────────────
    def _fmt_route(self, item):
        route_type = self._route_type(item.get("bustype", ""), item.get("buslinenum", ""))
        info = BUS_TYPE.get(route_type, BUS_TYPE["normal"])
        term = item.get("headwaynorm") or item.get("headwaypeak") or item.get("headwayholi") or "?"
        return {
            "id": item.get("lineid", ""),
            "number": item.get("buslinenum", ""),
            "start": item.get("startpoint", ""),
            "end": item.get("endpoint", ""),
            "type": route_type,
            "type_name": info["name"],
            "color": info["color"],
            "bg": info["bg"],
            "term": term,
            "arrival1": None,
            "arrival2": None,
        }

    def _fmt_station(self, item):
        return {
            "id": item.get("bstopid", ""),
            "arsId": item.get("arsno", ""),
            "name": item.get("bstopnm", ""),
            "direction": item.get("stoptype", ""),
            "gpsx": item.get("gpsx", ""),
            "gpsy": item.get("gpsy", ""),
        }

    def _fmt_arrival(self, item):
        route_type = self._route_type(item.get("bustype", ""), item.get("lineno", ""))
        info = BUS_TYPE.get(route_type, BUS_TYPE["normal"])
        min1 = self._parse_minute_field(item.get("min1"))
        min2 = self._parse_minute_field(item.get("min2"))
        station1 = item.get("station1", "")
        station2 = item.get("station2", "")
        msg1 = self._arrival_message(min1, station1, item.get("carno1", ""))
        msg2 = self._arrival_message(min2, station2, item.get("carno2", ""))
        return {
            "id": item.get("lineid", ""),
            "route_id": item.get("lineid", ""),
            "number": item.get("lineno") or item.get("buslinenum", ""),
            "type": route_type,
            "type_name": info["name"],
            "color": info["color"],
            "bg": info["bg"],
            "destination": item.get("direction", ""),
            "start": item.get("nodenm", ""),
            "end": "도착 예정",
            "arrival1": min1,
            "arrival2": min2,
            "arrival1_stations": self._to_int(station1),
            "arrival2_stations": self._to_int(station2),
            "arrmsg1": msg1,
            "arrmsg2": msg2,
        }

    def _fmt_route_station(self, item):
        seq = self._to_int(item.get("bstopidx")) or 0
        return {
            "id": item.get("nodeid", ""),
            "seq": seq,
            "name": item.get("bstopnm", ""),
            "arsId": item.get("arsno", ""),
            "direction": item.get("direction", ""),
            "avg_seconds": item.get("avgym", ""),
            "car_no": item.get("carno", ""),
            "gps_time": item.get("gpsym", ""),
        }

    def _fmt_bus_position(self, item):
        return {
            "route_id": item.get("lineid", ""),
            "vehicle_id": item.get("carno", ""),
            "plain_no": item.get("carno", ""),
            "section_order": item.get("bstopidx", ""),
            "stop_order": item.get("bstopidx", ""),
            "station_id": item.get("nodeid", ""),
            "station_name": item.get("bstopnm", ""),
            "gps_time": item.get("gpsym", ""),
        }

    def _station_ars_id(self, station):
        if isinstance(station, dict):
            return station.get("arsId") or station.get("ars_id") or ""
        station_text = str(station or "").strip()
        if len(station_text) == 5 and station_text.isdigit():
            return station_text
        return ""

    def _route_type(self, bustype, number):
        text = f"{bustype} {number}"
        if "심야" in text:
            return "night"
        if "급행" in text:
            return "express"
        if "마을" in text:
            return "village"
        if "공항" in text:
            return "airport"
        return "normal"

    def _parse_min(self, msg):
        if msg is None:
            return None
        text = str(msg).strip()
        if not text or text in ["운행종료", "출발대기", "-", "정보없음"]:
            return None
        if "곧" in text or "도착" in text:
            return 0
        minute_match = re.search(r"(\d+)\s*분", text)
        if minute_match:
            return int(minute_match.group(1))
        second_match = re.search(r"(\d+)\s*초", text)
        if second_match:
            return 0
        if text.isdigit():
            return self._seconds_to_min(int(text))
        return None

    def _parse_minute_field(self, value):
        try:
            minute = int(str(value).strip())
        except (TypeError, ValueError):
            return None
        return minute if minute >= 0 else None

    def _arrival_message(self, minute, stations_away, car_no):
        if minute is None:
            return ""
        base = "곧 도착" if minute <= 1 else f"{minute}분 후"
        details = []
        if stations_away:
            details.append(f"{stations_away}정류장 전")
        if car_no:
            details.append(str(car_no))
        return f"{base} ({', '.join(details)})" if details else base

    def _seconds_to_min(self, seconds):
        if seconds is None:
            return None
        return max(0, round(seconds / 60))

    def _to_int(self, value):
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return None
