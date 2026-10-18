"""Calendar months, the period every spending figure is reported over."""

import re
from dataclasses import dataclass
from datetime import date

_PATTERN = re.compile(r"(\d{4})-(\d{2})")


@dataclass(frozen=True, slots=True, order=True)
class Month:
    year: int
    month: int

    @classmethod
    def of(cls, day: date) -> Month:
        return cls(day.year, day.month)

    @classmethod
    def parse(cls, text: str) -> Month:
        match = _PATTERN.fullmatch(text)
        if not match or not 1 <= int(match[2]) <= 12 or int(match[1]) < 1:
            raise ValueError(f"not a month: {text!r}")
        return cls(int(match[1]), int(match[2]))

    def contains(self, day: date) -> bool:
        return (day.year, day.month) == (self.year, self.month)

    def previous(self) -> Month:
        return Month(self.year - 1, 12) if self.month == 1 else Month(self.year, self.month - 1)

    def following(self) -> Month:
        return Month(self.year + 1, 1) if self.month == 12 else Month(self.year, self.month + 1)

    @property
    def label(self) -> str:
        return date(self.year, self.month, 1).strftime("%B %Y")

    def __str__(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"
