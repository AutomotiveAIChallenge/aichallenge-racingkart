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
from dataclasses import dataclass, replace
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

# 終点が戻ってくる点を先頭から何 m まで探すか。ガレージからの引き込み
# （raceline_*_from_garage.csv）はここに収まる長さしかない。範囲を切らないと、
# 後半でコース自身に近づくだけの線を 1 周と誤認しかねない。
LEAD_IN_SEARCH_M = 30.0

# 横オフセットのマイター上限。鋭角のコーナーで外側の頂点が無限に伸びるのを防ぐ。
# 指定した横オフセットの 4 倍まで。
MITER_LIMIT = 4.0


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
    ``sample(length)`` lands back on ``sample(0.0)``. ``normals[i]`` is the
    left-hand miter normal at point ``i``: shifting every point by
    ``normals[i] * d`` gives a polyline parallel to this one at ``d`` metres,
    so a position can be moved off the line without recomputing the geometry
    every cycle.
    """

    points: Tuple[Tuple[float, float, float], ...]
    speeds: Tuple[float, ...]
    cumulative: Tuple[float, ...]
    normals: Tuple[Tuple[float, float], ...]
    length: float
    closed: bool
    # 周回の手前で切り落としたガレージ引き込みの点数（報告用）。
    lead_in_points: int = 0

    def sample(self, s_m: float, lateral_offset_m: float = 0.0) -> Tuple[float, float, float]:
        """Position at arc length ``s_m``, wrapping around a closed loop.

        ``lateral_offset_m`` moves the point sideways off the line: positive is
        left of the direction of travel, negative right. The normal is
        interpolated along the segment together with the position, so an
        offset object follows a continuous curve instead of stepping sideways
        at every point of the polyline.
        """
        index, ratio = self._locate(s_m)
        following = (index + 1) % len(self.points)
        start = self.points[index]
        end = self.points[following]
        point = (
            start[0] + (end[0] - start[0]) * ratio,
            start[1] + (end[1] - start[1]) * ratio,
            start[2] + (end[2] - start[2]) * ratio,
        )
        if not lateral_offset_m:
            return point
        # マイター法線を位置と同じ比で混ぜる。両端が区間から等距離にあるので、
        # 間の点もその区間と平行な線の上に乗る。
        start_normal = self.normals[index]
        end_normal = self.normals[following]
        normal_x = start_normal[0] + (end_normal[0] - start_normal[0]) * ratio
        normal_y = start_normal[1] + (end_normal[1] - start_normal[1]) * ratio
        return (
            point[0] + normal_x * lateral_offset_m,
            point[1] + normal_y * lateral_offset_m,
            point[2],
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


def _closure_point(points: Sequence[Tuple[float, float, float]]) -> Tuple[int, float]:
    """The point near the start that the line's end comes back to.

    Usually that is the first point. A raceline exported from the garage
    (``raceline_*_from_garage.csv``) instead begins with a lead-in that is not
    part of the lap, and its last point lands back on an interior point: the
    kashiwanoha line ends exactly on point 7, 7 m away from point 0. Only the
    first LEAD_IN_SEARCH_M are searched, so a line that merely passes close to
    itself later on is not taken for a lap.

    Returns the index and its distance from the end point; the caller decides
    whether that distance is close enough to call the line closed.
    """
    end = points[-1]
    best_index, best_gap = 0, math.dist(end[:2], points[0][:2])
    travelled = 0.0
    for index in range(1, len(points) - 1):
        travelled += math.dist(points[index][:2], points[index - 1][:2])
        if travelled > LEAD_IN_SEARCH_M:
            break
        gap = math.dist(end[:2], points[index][:2])
        if gap < best_gap:
            best_index, best_gap = index, gap
    return best_index, best_gap


def _unit_left_normal(
    start: Tuple[float, float, float], end: Tuple[float, float, float]
) -> Tuple[float, float]:
    """Unit normal pointing left of the direction ``start`` → ``end``.

    Left is +90 degrees from the heading, (dx, dy) → (-dy, dx), which is the
    left-hand side of the track in the map frame the karts share (R9.1).
    """
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    span = math.hypot(dx, dy)
    if span <= 0.0:
        return (0.0, 0.0)
    return (-dy / span, dx / span)


def _miter_normal(
    incoming: Tuple[float, float], outgoing: Tuple[float, float]
) -> Tuple[float, float]:
    """The left normal at a corner between two segments, as a miter vector.

    The bisector of the two unit normals, lengthened by 1 / cos(half angle) so
    that the corner of the offset line stays the requested distance from both
    segments — the plain bisector would cut the corner and pull the object back
    towards the raceline. Capped at MITER_LIMIT for a sharp corner, and at a
    180 degree turnaround, where the bisector vanishes, the incoming normal is
    kept.
    """
    x = incoming[0] + outgoing[0]
    y = incoming[1] + outgoing[1]
    span = math.hypot(x, y)
    if span <= 1e-9:
        return incoming
    bisector = (x / span, y / span)
    cosine = bisector[0] * outgoing[0] + bisector[1] * outgoing[1]
    scale = min(1.0 / cosine, MITER_LIMIT) if cosine > 1e-9 else MITER_LIMIT
    return (bisector[0] * scale, bisector[1] * scale)


def left_normals(
    points: Sequence[Tuple[float, float, float]], closed: bool
) -> Tuple[Tuple[float, float], ...]:
    """The left-hand miter normal at every point (see ``_miter_normal``).

    One normal per point rather than per segment is what keeps an offset
    object on a continuous curve: with a per-segment normal it would step
    sideways as it crossed each point. A line of fewer than two points has no
    direction, so its normals are zero and a lateral offset does nothing.
    """
    count = len(points)
    if count < 2:
        return ((0.0, 0.0),) * count
    segments = [
        _unit_left_normal(points[index], points[(index + 1) % count])
        for index in range(count if closed else count - 1)
    ]
    normals = []
    for index in range(count):
        if closed:
            incoming, outgoing = segments[index - 1], segments[index]
        else:
            incoming = segments[index - 1] if index > 0 else segments[0]
            outgoing = segments[index] if index < count - 1 else segments[-1]
        normals.append(_miter_normal(incoming, outgoing))
    return tuple(normals)


def parse_raceline(text: str, loop: Optional[bool] = None) -> Raceline:
    """Parse a simple_trajectory_generator raceline CSV.

    Columns ``x``, ``y`` are required; ``z`` and ``speed`` are optional and
    default to 0.0 and 0.0.

    With ``loop`` left at None the shape is decided from the geometry: the line
    is a lap when its end comes back within LOOP_CLOSURE_TOLERANCE_M of the
    start, or of a point inside the leading LEAD_IN_SEARCH_M — the garage
    lead-in of a ``from_garage`` CSV, which is then dropped so that the lap
    itself closes. Anything else is an open line, which an object walks to the
    end of and then stops on. ``loop=True`` closes the line whatever the gap
    (the object drives straight across it once a lap), ``loop=False`` keeps it
    open even when it visibly closes.
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
    lead_in_points = 0
    if len(points) > 2 and loop is not False:
        index, gap = _closure_point(points)
        if gap <= LOOP_CLOSURE_TOLERANCE_M:
            closed = True
            if index:
                # ガレージ引き込み。周回に入る点より前を落とすと、そのまま
                # 閉ループになる。弧長の原点も周回の入口へ移る。
                lead_in_points = index
                del points[:index]
                del speeds[:index]
            if gap < 1e-9:
                points.pop()
                speeds.pop()
        elif loop:
            # 明示的に周回させる。隙間はそのまま最終区間になる。
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
        normals=left_normals(points, closed),
        length=length,
        closed=closed,
        lead_in_points=lead_in_points,
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
    # レースラインからの横オフセット [m]。正が進行方向の左、負が右。路肩に
    # 停めたカートを置く、コース中央を外して並走させる、といった用途。
    lateral_offset: float = 0.0
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
    # レースラインを周回させるか。None は CSV の形から判断する（既定）。
    loop: Optional[bool] = None

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


def check_vehicle_id(vehicle_id: str, context: str) -> None:
    """Reject an id that cannot be a single MQTT topic level.

    ``v2x/vehicles/<id>/position`` breaks apart on ``/`` and matches a
    subscription pattern on ``+`` / ``#``, so the broker's ACL would no longer
    bind the id to its certificate.
    """
    if not vehicle_id:
        raise ScenarioError(f"{context}: id must not be empty")
    if "/" in vehicle_id or "+" in vehicle_id or "#" in vehicle_id:
        raise ScenarioError(f"{context}: id must not contain /, + or #")


def with_vehicle_ids(scenario: Scenario, vehicle_ids: Sequence[str]) -> Scenario:
    """The same scenario with its objects renamed, in order.

    Lets one scenario file be run under whichever ids have certificates on the
    day (``--ids d14,d15,d16``) without editing the YAML. The count must match:
    silently renaming a prefix of the objects would put a kart on the track
    under an id nobody expects.
    """
    if len(vehicle_ids) != len(scenario.objects):
        raise ScenarioError(
            f"{len(vehicle_ids)} id(s) given for {len(scenario.objects)} object(s) "
            f"({list(scenario.vehicle_ids)}); give one id per object, or narrow the "
            f"scenario with --only first"
        )
    seen = set()
    renamed = []
    for spawned, vehicle_id in zip(scenario.objects, vehicle_ids):
        check_vehicle_id(vehicle_id, f"id '{vehicle_id}'")
        if vehicle_id in seen:
            raise ScenarioError(f"id '{vehicle_id}': duplicate id")
        seen.add(vehicle_id)
        renamed.append(replace(spawned, vehicle_id=vehicle_id))
    return replace(scenario, objects=tuple(renamed))


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

    loop = document.get("loop")
    if loop is not None and not isinstance(loop, bool):
        raise ScenarioError("loop must be true or false")

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
        check_vehicle_id(vehicle_id, context)
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
        lateral_offset = _as_float(
            raw,
            "lateral_offset",
            context,
            _as_float(defaults, "lateral_offset", "defaults", 0.0),
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
            if has_xy and lateral_offset:
                raise ScenarioError(
                    f"{context}: lateral_offset is measured from the raceline, so it needs "
                    f"s_m; with an explicit x/y put the offset into the coordinates"
                )
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
                lateral_offset=lateral_offset,
                position=position,
                static_s_m=static_s_m,
                raceline=str(raceline) if raceline else None,
                speed_mps=speed_mps,
                speed_scale=speed_scale,
                start_s_m=start_s_m,
            )
        )

    return Scenario(broker=broker, objects=tuple(objects), rate_hz=rate_hz, loop=loop)


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
            base = raceline.sample(s_m, spawned.lateral_offset)
        return ObjectState(s_m=s_m, position=_with_offset(base, spawned.z_offset), speed_mps=0.0)

    if raceline is None:
        raise ScenarioError(f"object {spawned.vehicle_id}: raceline mode needs a loaded raceline")
    s_m = raceline.normalize(spawned.start_s_m)
    return ObjectState(
        s_m=s_m,
        position=_with_offset(raceline.sample(s_m, spawned.lateral_offset), spawned.z_offset),
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
        position=_with_offset(raceline.sample(s_m, spawned.lateral_offset), spawned.z_offset),
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
