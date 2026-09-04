#!/usr/bin/env python3
"""Pure logic for the V2X virtual object spawner.

Scenario validation, raceline geometry and payload encoding for
vehicle/v2x_virtual_objects.py. Deliberately free of MQTT, sockets, sleeping
and filesystem access: a raceline arrives as CSV text and a scenario as a
plain dict, so every rule here can be tested without a broker or a track.

The payload format is V2XVehiclePositionJson (aichallenge-v2x/docs/
SPECIFICATION.md §6.2): no vehicle_id inside the document, the id travels in
the MQTT topic name alone (R4.2), and covariance is a standard deviation in
metres, not a variance (R10.2.1).
"""
from __future__ import annotations

import csv
import io
import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional, Sequence, Tuple

# --- 既定値 -----------------------------------------------------------------
DEFAULT_FRAME_ID = "map"
DEFAULT_RATE_HZ = 20.0
# 実車の GNSS が出す標準偏差の代表値 [m]。usage.md §5.2 の例と同じ。
DEFAULT_COVARIANCE = (0.08, 0.08, 0.15)
DEFAULT_BROKER_PORT_TLS = 8883
DEFAULT_BROKER_PORT_PLAIN = 1883

# --- 動作モード -------------------------------------------------------------
MODE_STATIC = "static"
MODE_RACELINE = "raceline"
MODES = (MODE_STATIC, MODE_RACELINE)

# レースラインが閉ループとみなせる始点・終点間の距離 [m]。
LOOP_CLOSURE_TOLERANCE_M = 5.0


class ScenarioError(ValueError):
    """Raised when a scenario document cannot be turned into a valid run."""


# --- ペイロード -------------------------------------------------------------
def to_rfc3339_utc(moment: datetime) -> str:
    """Format a datetime as RFC 3339 UTC with millisecond resolution (R6.2.2)."""
    utc = moment.astimezone(timezone.utc)
    return f"{utc.strftime('%Y-%m-%dT%H:%M:%S')}.{utc.microsecond // 1000:03d}Z"


def build_payload(
    position: Tuple[float, float, float],
    covariance: Tuple[float, float, float] = DEFAULT_COVARIANCE,
    frame_id: str = DEFAULT_FRAME_ID,
    stamp: Optional[datetime] = None,
) -> str:
    """Build one V2XVehiclePositionJson document.

    Args:
        position: x, y, z in ``frame_id`` (MGRS map coordinates on the kart).
        covariance: per-axis positional standard deviation [m].
        frame_id: shared reference frame; every kart must agree on it (R9.1).
        stamp: observation time; defaults to now.
    """
    moment = stamp if stamp is not None else datetime.now(timezone.utc)
    document = {
        "stamp": to_rfc3339_utc(moment),
        "frame_id": frame_id,
        "position": {"x": position[0], "y": position[1], "z": position[2]},
        "covariance": {"x": covariance[0], "y": covariance[1], "z": covariance[2]},
    }
    # separators を固定して 1 行にする。mosquitto_pub -l は改行区切りで読むため、
    # ペイロードに改行が入ってはいけない。
    return json.dumps(document, separators=(",", ":"))


def position_topic(vehicle_id: str) -> str:
    """MQTT topic a kart publishes its own position to (§6.1)."""
    return f"v2x/vehicles/{vehicle_id}/position"


# --- レースライン -----------------------------------------------------------
@dataclass(frozen=True)
class Raceline:
    """A polyline in the map frame, parameterised by arc length.

    ``cumulative[i]`` is the distance from the first point to point ``i``;
    ``length`` includes the closing segment when the line is a loop, so that
    ``sample(length)`` lands back on ``sample(0.0)``.
    """

    points: Tuple[Tuple[float, float, float], ...]
    speeds: Tuple[float, ...]
    cumulative: Tuple[float, ...]
    length: float
    closed: bool

    def sample(self, s_m: float) -> Tuple[float, float, float]:
        """Position at arc length ``s_m``, wrapping around a closed loop."""
        index, ratio = self._locate(s_m)
        start = self.points[index]
        end = self.points[(index + 1) % len(self.points)]
        return (
            start[0] + (end[0] - start[0]) * ratio,
            start[1] + (end[1] - start[1]) * ratio,
            start[2] + (end[2] - start[2]) * ratio,
        )

    def speed_at(self, s_m: float) -> float:
        """Raceline speed [m/s] at arc length ``s_m`` (no interpolation)."""
        index, _ = self._locate(s_m)
        return self.speeds[index]

    def normalize(self, s_m: float) -> float:
        """Fold an arc length into [0, length), or clamp it on an open line."""
        if self.length <= 0.0:
            return 0.0
        if self.closed:
            return s_m % self.length
        return min(max(s_m, 0.0), self.length)

    def _locate(self, s_m: float) -> Tuple[int, float]:
        """Return the segment index and the ratio within it for ``s_m``."""
        if len(self.points) == 1:
            return 0, 0.0
        target = self.normalize(s_m)
        # 点数は数百なので線形探索で足りる (20 Hz × 数台)。
        index = 0
        for candidate in range(len(self.cumulative) - 1):
            if self.cumulative[candidate + 1] > target:
                index = candidate
                break
        else:
            index = len(self.cumulative) - 1
        segment_start = self.cumulative[index]
        segment_end = (
            self.cumulative[index + 1] if index + 1 < len(self.cumulative) else self.length
        )
        span = segment_end - segment_start
        ratio = 0.0 if span <= 0.0 else (target - segment_start) / span
        return index, min(max(ratio, 0.0), 1.0)


def parse_raceline(text: str) -> Raceline:
    """Parse a simple_trajectory_generator raceline CSV.

    Columns ``x``, ``y`` are required; ``z`` and ``speed`` are optional and
    default to 0.0 and 0.0. The line is treated as a loop when its first and
    last points are within LOOP_CLOSURE_TOLERANCE_M of each other, which is how
    the track racelines in aichallenge_submit are shaped.
    """
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise ScenarioError("raceline CSV is empty")
    missing = {"x", "y"} - {name.strip() for name in reader.fieldnames}
    if missing:
        raise ScenarioError(f"raceline CSV lacks the column(s) {sorted(missing)}")

    points = []
    speeds = []
    for line_number, row in enumerate(reader, start=2):
        try:
            point = (
                float(row["x"]),
                float(row["y"]),
                float(row.get("z") or 0.0),
            )
            speed = float(row.get("speed") or 0.0)
        except (TypeError, ValueError) as error:
            raise ScenarioError(f"raceline CSV line {line_number}: {error}") from error
        # 重複点は弧長 0 の区間を作り、速度参照を壊すので落とす。
        if points and math.dist(point[:2], points[-1][:2]) < 1e-9:
            continue
        points.append(point)
        speeds.append(speed)

    if not points:
        raise ScenarioError("raceline CSV has no usable points")

    # 閉ループかどうかを先に決める。終点が始点と完全に一致する CSV
    # (raceline_cctb_30km.csv) はその終点を落とす。落としてから距離で判定すると、
    # 最終区間の長さを「開いた線の隙間」と取り違えてしまう。
    closed = False
    if len(points) > 2:
        gap = math.dist(points[-1][:2], points[0][:2])
        if gap < 1e-9:
            closed = True
            points.pop()
            speeds.pop()
        elif gap <= LOOP_CLOSURE_TOLERANCE_M:
            # 柏の葉のレースラインのように 1 m ほど開いている CSV。1 周とみなす。
            closed = True

    cumulative = [0.0]
    for index in range(len(points) - 1):
        cumulative.append(cumulative[-1] + math.dist(points[index][:2], points[index + 1][:2]))

    closing = math.dist(points[-1][:2], points[0][:2]) if closed else 0.0
    length = cumulative[-1] + closing

    return Raceline(
        points=tuple(points),
        speeds=tuple(speeds),
        cumulative=tuple(cumulative),
        length=length,
        closed=closed,
    )


# --- シナリオ ---------------------------------------------------------------
@dataclass(frozen=True)
class BrokerConfig:
    """Where the virtual objects publish, and how they authenticate."""

    host: str
    port: int
    tls: bool
    certs_dir: Optional[str] = None
    qos: int = 0

    def certificate_paths(self, vehicle_id: str) -> Tuple[str, str, str]:
        """ca.crt / kart.crt / kart.key of ``vehicle_id`` in ``certs_dir``.

        The layout is the one issue-kart-cert.sh writes:
        ``<certs_dir>/<vehicle_id>/{ca.crt,kart.crt,kart.key}``.
        """
        if not self.certs_dir:
            raise ScenarioError("broker.certs_dir is required when tls is enabled")
        base = os.path.join(self.certs_dir, vehicle_id)
        return (
            os.path.join(base, "ca.crt"),
            os.path.join(base, "kart.crt"),
            os.path.join(base, "kart.key"),
        )


@dataclass(frozen=True)
class VirtualObject:
    """One pseudo kart on the broker."""

    vehicle_id: str
    mode: str
    frame_id: str = DEFAULT_FRAME_ID
    covariance: Tuple[float, float, float] = DEFAULT_COVARIANCE
    z_offset: float = 0.0
    # static
    position: Optional[Tuple[float, float, float]] = None
    static_s_m: Optional[float] = None
    # raceline
    raceline: Optional[str] = None
    speed_mps: Optional[float] = None
    speed_scale: float = 1.0
    start_s_m: float = 0.0

    @property
    def topic(self) -> str:
        return position_topic(self.vehicle_id)


@dataclass(frozen=True)
class Scenario:
    """A validated run: the broker, the rate and the objects to spawn."""

    broker: BrokerConfig
    objects: Tuple[VirtualObject, ...]
    rate_hz: float = DEFAULT_RATE_HZ

    @property
    def raceline_paths(self) -> Tuple[str, ...]:
        """Every distinct raceline the scenario refers to, in first-use order."""
        seen: Dict[str, None] = {}
        for spawned in self.objects:
            if spawned.raceline:
                seen.setdefault(spawned.raceline, None)
        return tuple(seen)

    @property
    def vehicle_ids(self) -> Tuple[str, ...]:
        return tuple(spawned.vehicle_id for spawned in self.objects)


def _as_float(container: dict, key: str, context: str, default=None):
    if key not in container or container[key] is None:
        return default
    try:
        return float(container[key])
    except (TypeError, ValueError) as error:
        raise ScenarioError(f"{context}: {key} must be a number") from error


def _as_covariance(value, context: str) -> Tuple[float, float, float]:
    if value is None:
        return DEFAULT_COVARIANCE
    if isinstance(value, dict):
        try:
            return (float(value["x"]), float(value["y"]), float(value["z"]))
        except (KeyError, TypeError, ValueError) as error:
            raise ScenarioError(f"{context}: covariance needs numeric x, y, z") from error
    if isinstance(value, (list, tuple)) and len(value) == 3:
        try:
            return (float(value[0]), float(value[1]), float(value[2]))
        except (TypeError, ValueError) as error:
            raise ScenarioError(f"{context}: covariance needs three numbers") from error
    raise ScenarioError(f"{context}: covariance must be [x, y, z] or {{x, y, z}}")


def parse_scenario(document: dict) -> Scenario:
    """Validate a scenario document and turn it into a Scenario.

    ``defaults`` supplies frame_id / covariance / z_offset / raceline /
    speed_mps / speed_scale to every object that does not set them itself.
    Nothing here touches the filesystem: raceline values stay strings, and the
    caller loads and resolves them.
    """
    if not isinstance(document, dict):
        raise ScenarioError("scenario must be a mapping")

    defaults = document.get("defaults") or {}
    if not isinstance(defaults, dict):
        raise ScenarioError("defaults must be a mapping")

    broker_document = document.get("broker") or {}
    if not isinstance(broker_document, dict):
        raise ScenarioError("broker must be a mapping")
    tls = bool(broker_document.get("tls", True))
    port = broker_document.get("port")
    broker = BrokerConfig(
        host=str(broker_document.get("host", "127.0.0.1")),
        port=int(port) if port is not None else (
            DEFAULT_BROKER_PORT_TLS if tls else DEFAULT_BROKER_PORT_PLAIN
        ),
        tls=tls,
        certs_dir=broker_document.get("certs_dir"),
        qos=int(broker_document.get("qos", 0)),
    )
    if broker.qos not in (0, 1, 2):
        raise ScenarioError("broker.qos must be 0, 1 or 2")

    rate_hz = _as_float(document, "rate_hz", "scenario", DEFAULT_RATE_HZ)
    if rate_hz <= 0.0:
        raise ScenarioError("rate_hz must be greater than zero")

    raw_objects = document.get("objects")
    if not isinstance(raw_objects, list) or not raw_objects:
        raise ScenarioError("scenario needs a non-empty objects list")

    objects = []
    seen_ids = set()
    for index, raw in enumerate(raw_objects):
        if not isinstance(raw, dict):
            raise ScenarioError(f"objects[{index}] must be a mapping")
        vehicle_id = str(raw.get("id") or "").strip()
        context = f"object {vehicle_id or index}"
        if not vehicle_id:
            raise ScenarioError(f"objects[{index}] needs an id")
        if "/" in vehicle_id or "+" in vehicle_id or "#" in vehicle_id:
            raise ScenarioError(f"{context}: id must not contain /, + or #")
        if vehicle_id in seen_ids:
            raise ScenarioError(f"{context}: duplicate id")
        seen_ids.add(vehicle_id)

        mode = str(raw.get("mode") or MODE_STATIC)
        if mode not in MODES:
            raise ScenarioError(f"{context}: mode must be one of {list(MODES)}")

        raceline = raw.get("raceline", defaults.get("raceline"))
        z_offset = _as_float(
            raw, "z_offset", context, _as_float(defaults, "z_offset", "defaults", 0.0)
        )
        covariance = _as_covariance(
            raw.get("covariance", defaults.get("covariance")), context
        )
        frame_id = str(raw.get("frame_id", defaults.get("frame_id", DEFAULT_FRAME_ID)))

        position = None
        static_s_m = None
        speed_mps = None
        speed_scale = 1.0
        start_s_m = 0.0

        if mode == MODE_STATIC:
            static_s_m = _as_float(raw, "s_m", context)
            has_xy = raw.get("x") is not None and raw.get("y") is not None
            if has_xy:
                position = (
                    _as_float(raw, "x", context),
                    _as_float(raw, "y", context),
                    _as_float(raw, "z", context, 0.0),
                )
            elif static_s_m is None:
                raise ScenarioError(f"{context}: a static object needs x and y, or s_m")
            if has_xy and static_s_m is not None:
                raise ScenarioError(f"{context}: give x/y or s_m, not both")
            if static_s_m is not None and not raceline:
                raise ScenarioError(f"{context}: s_m needs a raceline")
        else:
            if not raceline:
                raise ScenarioError(f"{context}: a raceline object needs a raceline path")
            speed_mps = _as_float(
                raw, "speed_mps", context, _as_float(defaults, "speed_mps", "defaults")
            )
            speed_scale = _as_float(
                raw, "speed_scale", context, _as_float(defaults, "speed_scale", "defaults", 1.0)
            )
            start_s_m = _as_float(raw, "start_s_m", context, 0.0)
            if speed_mps is not None and speed_mps < 0.0:
                raise ScenarioError(f"{context}: speed_mps must not be negative")
            if speed_scale < 0.0:
                raise ScenarioError(f"{context}: speed_scale must not be negative")

        objects.append(
            VirtualObject(
                vehicle_id=vehicle_id,
                mode=mode,
                frame_id=frame_id,
                covariance=covariance,
                z_offset=z_offset,
                position=position,
                static_s_m=static_s_m,
                raceline=str(raceline) if raceline else None,
                speed_mps=speed_mps,
                speed_scale=speed_scale,
                start_s_m=start_s_m,
            )
        )

    return Scenario(broker=broker, objects=tuple(objects), rate_hz=rate_hz)


# --- 走行状態 ---------------------------------------------------------------
@dataclass(frozen=True)
class ObjectState:
    """Where one virtual object currently is."""

    s_m: float = 0.0
    position: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    speed_mps: float = 0.0


def initial_state(spawned: VirtualObject, raceline: Optional[Raceline]) -> ObjectState:
    """State at t = 0, resolving a static s_m against the raceline."""
    if spawned.mode == MODE_STATIC:
        if spawned.position is not None:
            base = spawned.position
            s_m = 0.0
        else:
            if raceline is None:
                raise ScenarioError(f"object {spawned.vehicle_id}: s_m needs a loaded raceline")
            s_m = raceline.normalize(spawned.static_s_m or 0.0)
            base = raceline.sample(s_m)
        return ObjectState(s_m=s_m, position=_with_offset(base, spawned.z_offset), speed_mps=0.0)

    if raceline is None:
        raise ScenarioError(f"object {spawned.vehicle_id}: raceline mode needs a loaded raceline")
    s_m = raceline.normalize(spawned.start_s_m)
    return ObjectState(
        s_m=s_m,
        position=_with_offset(raceline.sample(s_m), spawned.z_offset),
        speed_mps=current_speed(spawned, raceline, s_m),
    )


def advance(
    spawned: VirtualObject,
    raceline: Optional[Raceline],
    state: ObjectState,
    dt_s: float,
) -> ObjectState:
    """State after ``dt_s`` seconds.

    A static object never moves. A raceline object walks the polyline at
    ``speed_mps`` when it is given, otherwise at the CSV speed scaled by
    ``speed_scale`` — which is why the walk is integrated step by step rather
    than computed from the elapsed time.
    """
    if spawned.mode == MODE_STATIC or raceline is None:
        return state
    speed = current_speed(spawned, raceline, state.s_m)
    s_m = raceline.normalize(state.s_m + speed * dt_s)
    return ObjectState(
        s_m=s_m,
        position=_with_offset(raceline.sample(s_m), spawned.z_offset),
        speed_mps=speed,
    )


def current_speed(spawned: VirtualObject, raceline: Raceline, s_m: float) -> float:
    """Speed [m/s] the object travels at right now."""
    if spawned.speed_mps is not None:
        return spawned.speed_mps
    return raceline.speed_at(s_m) * spawned.speed_scale


def _with_offset(
    position: Tuple[float, float, float], z_offset: float
) -> Tuple[float, float, float]:
    return (position[0], position[1], position[2] + z_offset)


# --- 証明書 -----------------------------------------------------------------
def common_name_of(subject_line: str) -> Optional[str]:
    """Extract the CN from an ``openssl x509 -noout -subject`` line.

    Handles both spellings openssl emits: ``subject=CN = d5`` (1.1+) and
    ``subject= /CN=d5`` (older). The CN matters because the broker sets
    ``use_identity_as_username`` and the strict ACL only lets a certificate
    publish under ``v2x/vehicles/<CN>/position``.
    """
    for field in subject_line.replace("subject=", "", 1).split(","):
        for part in field.split("/"):
            key, separator, value = part.partition("=")
            if separator and key.strip().upper() == "CN":
                return value.strip()
    return None


def missing_files(paths: Sequence[str]) -> Tuple[str, ...]:
    """Which of ``paths`` do not exist. The one filesystem touch here."""
    return tuple(path for path in paths if not os.path.isfile(path))
