from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any

Row = Any
Props = Mapping[str, Callable[[Any], Any]]

SEGMENT_FIELDS = ("segment_id", "lat", "lon", "length_km")


def pick(rows: Iterable[Row], fields: Sequence[str]) -> list[dict[str, Any]]:
    return [{field: getattr(row, field) for field in fields} for row in rows]


def _feature(row: Row, props: Props) -> dict[str, Any]:
    segment_id, lat, lon, length_km, path = row[:5]
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": path or [[lon, lat]]},
        "properties": {
            **dict(zip(SEGMENT_FIELDS, (segment_id, lat, lon, length_km), strict=True)),
            **{
                name: cast(value)
                for (name, cast), value in zip(props.items(), row[5:], strict=True)
            },
        },
    }


def collection(
    rows: Sequence[Row],
    props: Props,
    peak_of: str,
    relative_of: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    features = [_feature(row, props) for row in rows]
    peak = max(
        (float(feature["properties"][peak_of]) for feature in features), default=0.0
    )
    if relative_of is not None:
        for feature in features:
            value = float(feature["properties"][relative_of])
            feature["properties"]["relative_index"] = (
                100.0 * value / peak if peak else 0.0
            )
    return {"type": "FeatureCollection", "features": features, "peak": peak, **extra}
