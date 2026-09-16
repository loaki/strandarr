from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from strandarr.analysis import Float

Path = list[list[float]]


@dataclass(frozen=True)
class SegmentIndex:
    ids: tuple[int, ...]
    lats: Float
    lons: Float
    lengths: Float
    orientations: Float
    paths: tuple[Path | None, ...]
    position: Mapping[int, int]

    @classmethod
    def of(cls, rows: Sequence[Any]) -> "SegmentIndex":
        return cls(
            ids=tuple(row.id for row in rows),
            lats=np.array([row.center_lat for row in rows], dtype=np.float64),
            lons=np.array([row.center_lon for row in rows], dtype=np.float64),
            lengths=np.array([row.length_km or 0.0 for row in rows], dtype=np.float64),
            orientations=np.array(
                [row.coastline_orientation_deg or 0.0 for row in rows],
                dtype=np.float64,
            ),
            paths=tuple(row.path for row in rows),
            position={row.id: position for position, row in enumerate(rows)},
        )

    def __len__(self) -> int:
        return len(self.ids)

    @property
    def divisors(self) -> Float:
        return np.maximum(np.where(self.lengths > 0.0, self.lengths, 1.0), 0.1)

    def blank(self) -> Float:
        return np.zeros(len(self), dtype=np.float64)

    def geometry(self, position: int) -> Path:
        return self.paths[position] or [
            [float(self.lons[position]), float(self.lats[position])]
        ]

    def vector(self, by_id: Mapping[int, float]) -> Float:
        row = self.blank()
        for segment_id, value in by_id.items():
            position = self.position.get(segment_id)
            if position is not None:
                row[position] = value
        return row
