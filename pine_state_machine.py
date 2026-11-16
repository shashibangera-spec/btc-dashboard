"""Source-faithful Python port of the BTC AMT Observer v7.2.2 auction state machine.

Scope: the AMT TPO profile, prior-value lifecycle, ATR/significance inputs, and
auction-state transitions that feed auctionState in the Pine source. Display,
OI/PI, EMA diagnostics, poor-extreme annotations, and other independent layers
are intentionally excluded.

Reference: BTC_AMT_Observer_v7.2.2.pine, Pine v6.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from math import ceil, floor
from typing import Iterable, Optional


STATE_NAMES = {
    0: "BUILDING",
    1: "DEVELOPMENT",
    2: "DISCOVERY ABOVE",
    3: "DISCOVERY BELOW",
    4: "PRICE ACCEPTANCE ABOVE",
    5: "PRICE ACCEPTANCE BELOW",
    6: "POC BREAK ABOVE",
    7: "POC BREAK BELOW",
    8: "NEW AREA DEVELOPING UP",
    9: "NEW AREA DEVELOPING DOWN",
    10: "FAILED DISCOVERY UP",
    11: "FAILED DISCOVERY DOWN",
    12: "ACCEPTANCE FAILURE UP",
    13: "ACCEPTANCE FAILURE DOWN",
    14: "RE-ENTRY CONFIRMED",
    15: "DISCOVERY STALLED ABOVE",
    16: "DISCOVERY STALLED BELOW",
    17: "FAILED DISCOVERY UP — CANDIDATE",
    18: "FAILED DISCOVERY DOWN — CANDIDATE",
}


@dataclass(frozen=True)
class Candle:
    open_time: datetime
    open: float
    high: float
    low: float
    close: float

    def __post_init__(self) -> None:
        if self.open_time.tzinfo is None:
            raise ValueError("Candle.open_time must be timezone-aware")
        if not (self.low <= min(self.open, self.close) <= max(self.open, self.close) <= self.high):
            raise ValueError(f"Invalid OHLC range at {self.open_time.isoformat()}")
        for name in ("open", "high", "low", "close"):
            value = getattr(self, name)
            if value != value or value in (float("inf"), float("-inf")):
                raise ValueError(f"Non-finite {name} at {self.open_time.isoformat()}")

    @property
    def utc_time(self) -> datetime:
        return self.open_time.astimezone(timezone.utc)


@dataclass(frozen=True)
class PineConfig:
    # Pine v7.2.2 input defaults.
    row_size_mode: str = "Fixed $"
    row_size_fixed: float = 50.0
    row_size_pct: float = 0.07
    row_size_atr_mult: float = 0.25
    value_percent: float = 70.0
    development_min: int = 3
    use_midpoint_poc: bool = True
    cycle_hour_utc: int = 0
    cycle_minute_utc: int = 0
    accept_bars: int = 2
    use_adaptive_timeout: bool = True
    discovery_timeout: int = 6
    poc_persistence_bars: int = 2
    poc_delta_threshold_rows: float = 1.0
    development_stall_bars: int = 20
    failed_discovery_confirm_bars: int = 2
    reentry_persistence_bars: int = 3
    acceptance_pullback_bars: int = 2
    use_significance_gate: bool = True
    significance_range_mult: float = 1.5
    min_tick: float = 0.01
    atr_length: int = 20

    def __post_init__(self) -> None:
        if self.row_size_mode not in {"Fixed $", "Fixed %", "ATR-based"}:
            raise ValueError("row_size_mode must be Fixed $, Fixed %, or ATR-based")
        if self.row_size_fixed <= 0 or self.value_percent <= 0:
            raise ValueError("row size and value percent must be positive")
        if self.atr_length < 1:
            raise ValueError("atr_length must be >= 1")


@dataclass
class TransitionCounters:
    state8_bars: int = 0
    state9_bars: int = 0
    reentry_inside_bars: int = 0


@dataclass(frozen=True)
class TransitionInputs:
    bar_index: int
    discovery_start_bar: int
    effective_timeout: int
    close: float
    current_poc: Optional[float]
    prior_vah: float
    prior_val: float
    cycle_bars: int
    development_min: int
    inside_value_bars: int
    accept_bars: int
    failed_discovery_confirm_bars: int
    development_stall_bars: int
    reentry_persistence_bars: int
    price_acceptance_up: bool
    price_acceptance_down: bool
    value_migration_up: bool
    value_migration_down: bool
    new_value_developing_up: bool
    new_value_developing_down: bool


def advance_auction_state(
    state: int,
    discovery_start_bar: int,
    counters: TransitionCounters,
    x: TransitionInputs,
) -> tuple[int, int, bool, Optional[str]]:
    """Translate the Pine v7.2.2 auctionState if/else chain in source order."""
    development_stalled = False
    event: Optional[str] = None
    discovery_timed_out = (x.bar_index - discovery_start_bar) >= x.effective_timeout

    if state == 0:
        if x.close > x.prior_vah:
            state = 2
            discovery_start_bar = x.bar_index
        elif x.close < x.prior_val:
            state = 3
            discovery_start_bar = x.bar_index
        elif x.cycle_bars >= x.development_min:
            state = 1
    elif state == 1:
        if x.close > x.prior_vah:
            state = 2
            discovery_start_bar = x.bar_index
        elif x.close < x.prior_val:
            state = 3
            discovery_start_bar = x.bar_index
    elif state == 2:
        if x.price_acceptance_up:
            state = 4
        elif x.close < x.prior_val:
            state = 3
            discovery_start_bar = x.bar_index
        elif x.close <= x.prior_vah:
            state = 17
        elif discovery_timed_out:
            state = 15
    elif state == 3:
        if x.price_acceptance_down:
            state = 5
        elif x.close > x.prior_vah:
            state = 2
            discovery_start_bar = x.bar_index
        elif x.close >= x.prior_val:
            state = 18
        elif discovery_timed_out:
            state = 16
    elif state == 4:
        if x.value_migration_up:
            state = 6
        elif x.close <= x.prior_vah:
            state = 12
    elif state == 5:
        if x.value_migration_down:
            state = 7
        elif x.close >= x.prior_val:
            state = 13
    elif state == 6:
        if x.new_value_developing_up:
            state = 8
        elif x.close <= x.prior_vah:
            state = 12
    elif state == 7:
        if x.new_value_developing_down:
            state = 9
        elif x.close >= x.prior_val:
            state = 13
    elif state == 8:
        counters.state8_bars += 1
        if counters.state8_bars >= x.development_stall_bars and not x.new_value_developing_up:
            development_stalled = True
            state = 12
            event = "NEW AREA STALLED UP"
        elif x.current_poc is not None and x.close <= x.prior_vah and x.current_poc <= x.prior_vah:
            state = 12
    elif state == 9:
        counters.state9_bars += 1
        if counters.state9_bars >= x.development_stall_bars and not x.new_value_developing_down:
            development_stalled = True
            state = 13
            event = "NEW AREA STALLED DOWN"
        elif x.current_poc is not None and x.close >= x.prior_val and x.current_poc >= x.prior_val:
            state = 13
    elif state == 17:
        if x.close > x.prior_vah:
            state = 2
            discovery_start_bar = x.bar_index
        elif x.close < x.prior_val:
            state = 3
            discovery_start_bar = x.bar_index
        elif x.prior_val <= x.close <= x.prior_vah and x.inside_value_bars >= x.failed_discovery_confirm_bars:
            state = 10
    elif state == 18:
        if x.close < x.prior_val:
            state = 3
            discovery_start_bar = x.bar_index
        elif x.close > x.prior_vah:
            state = 2
            discovery_start_bar = x.bar_index
        elif x.prior_val <= x.close <= x.prior_vah and x.inside_value_bars >= x.failed_discovery_confirm_bars:
            state = 11
    elif state == 10:
        if x.close > x.prior_vah:
            state = 2
            discovery_start_bar = x.bar_index
        elif x.close < x.prior_val:
            state = 3
            discovery_start_bar = x.bar_index
        elif x.prior_val <= x.close <= x.prior_vah and x.inside_value_bars >= x.accept_bars:
            state = 14
    elif state == 11:
        if x.close < x.prior_val:
            state = 3
            discovery_start_bar = x.bar_index
        elif x.close > x.prior_vah:
            state = 2
            discovery_start_bar = x.bar_index
        elif x.prior_val <= x.close <= x.prior_vah and x.inside_value_bars >= x.accept_bars:
            state = 14
    elif state == 15:
        if x.price_acceptance_up:
            state = 4
        elif x.close < x.prior_val:
            state = 3
            discovery_start_bar = x.bar_index
        elif x.prior_val <= x.close <= x.prior_vah:
            state = 10
    elif state == 16:
        if x.price_acceptance_down:
            state = 5
        elif x.close > x.prior_vah:
            state = 2
            discovery_start_bar = x.bar_index
        elif x.prior_val <= x.close <= x.prior_vah:
            state = 11
    elif state == 12:
        if x.close > x.prior_vah:
            state = 2
            discovery_start_bar = x.bar_index
        elif x.close < x.prior_val:
            state = 3
            discovery_start_bar = x.bar_index
        elif x.inside_value_bars >= x.accept_bars:
            state = 14
    elif state == 13:
        if x.close < x.prior_val:
            state = 3
            discovery_start_bar = x.bar_index
        elif x.close > x.prior_vah:
            state = 2
            discovery_start_bar = x.bar_index
        elif x.inside_value_bars >= x.accept_bars:
            state = 14
    elif state == 14:
        if x.close > x.prior_vah:
            state = 2
            discovery_start_bar = x.bar_index
            counters.reentry_inside_bars = 0
        elif x.close < x.prior_val:
            state = 3
            discovery_start_bar = x.bar_index
            counters.reentry_inside_bars = 0
        else:
            counters.reentry_inside_bars += 1
            if counters.reentry_inside_bars >= x.reentry_persistence_bars and x.cycle_bars >= x.development_min:
                state = 1
                counters.reentry_inside_bars = 0

    if state != 8:
        counters.state8_bars = 0
    if state != 9:
        counters.state9_bars = 0
    if state != 14:
        counters.reentry_inside_bars = 0
    return state, discovery_start_bar, development_stalled, event


@dataclass(frozen=True)
class PineBarResult:
    open_time: datetime
    bar_index: int
    state_code: int
    state_name: str
    previous_state_code: int
    prior_vah: Optional[float]
    prior_val: Optional[float]
    prior_poc: Optional[float]
    developing_vah: Optional[float]
    developing_val: Optional[float]
    developing_poc: Optional[float]
    atr20: Optional[float]
    row_size: float
    cycle_bars: int
    transition_event: Optional[str]
    development_stalled: bool


class PineStateMachine:
    """Incremental replay engine for the v7.2.2 AMT auction state machine."""

    def __init__(self, config: PineConfig = PineConfig()) -> None:
        self.config = config
        self.bar_index = -1
        self.previous_close: Optional[float] = None
        self._tr_seed: list[float] = []
        self._atr: Optional[float] = None
        self.row_size = config.row_size_fixed
        self._tpo_base: Optional[int] = None
        self._tpo: list[int] = []
        self.prior_vah: Optional[float] = None
        self.prior_val: Optional[float] = None
        self.prior_poc: Optional[float] = None
        self.cycle_bars = 0
        self.above_value_bars = 0
        self.below_value_bars = 0
        self.inside_value_bars = 0
        self.auction_state = 0
        self.auction_state_prev = 0
        self.discovery_start_bar = 0
        self.poc_above_bars = 0
        self.poc_below_bars = 0
        self.state_counters = TransitionCounters()
        self.acceptance_pullback_bars_up = 0
        self.acceptance_pullback_bars_down = 0
        self.acceptance_confirmed_up = False
        self.acceptance_confirmed_down = False
        self._previous_developing_poc: Optional[float] = None

    @staticmethod
    def _round_pine(value: float) -> int:
        # Pine math.round rounds positive half values away from zero.
        return int(floor(value + 0.5))

    def _update_atr(self, candle: Candle) -> Optional[float]:
        if self.previous_close is None:
            tr = candle.high - candle.low
        else:
            tr = max(
                candle.high - candle.low,
                abs(candle.high - self.previous_close),
                abs(candle.low - self.previous_close),
            )
        length = self.config.atr_length
        if self._atr is None:
            self._tr_seed.append(tr)
            if len(self._tr_seed) == length:
                self._atr = sum(self._tr_seed) / length
        else:
            self._atr = ((self._atr * (length - 1)) + tr) / length
        self.previous_close = candle.close
        return self._atr

    def _bucket(self, price: float) -> int:
        return int(floor(price / self.row_size))

    def _row_price(self, bucket: int) -> float:
        return bucket * self.row_size + self.row_size * 0.5

    def _add_profile_bar(self, candle: Candle) -> None:
        low_bucket = self._bucket(candle.low)
        high_bucket = self._bucket(candle.high)
        if self._tpo_base is None:
            self._tpo_base = low_bucket
            self._tpo = [0] * (high_bucket - low_bucket + 1)
        if low_bucket < self._tpo_base:
            prepend = self._tpo_base - low_bucket
            self._tpo = [0] * prepend + self._tpo
            self._tpo_base = low_bucket
        current_top = self._tpo_base + len(self._tpo) - 1
        if high_bucket > current_top:
            self._tpo.extend([0] * (high_bucket - current_top))
        first_idx = low_bucket - self._tpo_base
        last_idx = high_bucket - self._tpo_base
        for idx in range(first_idx, last_idx + 1):
            self._tpo[idx] += 1

    def _profile_stats(self) -> tuple[Optional[float], Optional[float], Optional[float]]:
        size = len(self._tpo)
        if size == 0 or self._tpo_base is None:
            return None, None, None
        max_count = max(self._tpo)
        tied = [i for i, count in enumerate(self._tpo) if count == max_count]
        poc_idx = tied[0]
        if self.config.use_midpoint_poc and len(tied) > 1:
            profile_mid = (size - 1) / 2.0
            best_distance = float("inf")
            for idx in tied:
                distance = abs(idx - profile_mid)
                if distance < best_distance:
                    best_distance = distance
                    poc_idx = idx
        target = int(ceil(sum(self._tpo) * self.config.value_percent / 100.0))
        covered = self._tpo[poc_idx]
        lo = hi = poc_idx
        while covered < target:
            can_down = lo > 0
            can_up = hi < size - 1
            if not can_down and not can_up:
                break
            down_count = self._tpo[lo - 1] if can_down else -1
            up_count = self._tpo[hi + 1] if can_up else -1
            if up_count > down_count:
                hi += 1
                covered += self._tpo[hi]
            elif down_count > up_count:
                lo -= 1
                covered += self._tpo[lo]
            else:
                if can_down:
                    lo -= 1
                    covered += self._tpo[lo]
                if covered < target and can_up:
                    hi += 1
                    covered += self._tpo[hi]
        return (
            self._row_price(self._tpo_base + hi),
            self._row_price(self._tpo_base + lo),
            self._row_price(self._tpo_base + poc_idx),
        )

    def _save_previous_cycle_profile(self) -> None:
        vah, val, poc = self._profile_stats()
        if vah is not None and val is not None and poc is not None:
            self.prior_vah, self.prior_val, self.prior_poc = vah, val, poc

    def _reset_cycle(self, candle: Candle, atr: Optional[float]) -> None:
        self._save_previous_cycle_profile()
        cfg = self.config
        if cfg.row_size_mode == "Fixed $":
            row_size = cfg.row_size_fixed
        elif cfg.row_size_mode == "Fixed %":
            row_size = candle.close * cfg.row_size_pct / 100.0
        else:
            row_size = cfg.row_size_fixed if atr is None else atr * cfg.row_size_atr_mult
        self.row_size = max(row_size, cfg.min_tick)
        self._tpo_base = None
        self._tpo = []
        self.cycle_bars = 0
        self.above_value_bars = 0
        self.below_value_bars = 0
        self.inside_value_bars = 0
        self.auction_state = 0
        self.auction_state_prev = 0
        self.discovery_start_bar = 0
        self.poc_above_bars = 0
        self.poc_below_bars = 0
        self.state_counters = TransitionCounters()
        self.acceptance_pullback_bars_up = 0
        self.acceptance_pullback_bars_down = 0
        self.acceptance_confirmed_up = False
        self.acceptance_confirmed_down = False

    def process_bar(self, candle: Candle) -> PineBarResult:
        self.bar_index += 1
        atr = self._update_atr(candle)
        utc = candle.utc_time
        is_new_cycle = (
            utc.hour == self.config.cycle_hour_utc
            and utc.minute == self.config.cycle_minute_utc
        )
        if is_new_cycle:
            self._reset_cycle(candle, atr)

        self.cycle_bars += 1
        self._add_profile_bar(candle)
        current_vah, current_val, current_poc = self._profile_stats()

        prior_vah = self.prior_vah
        prior_val = self.prior_val
        if prior_vah is not None and prior_val is not None:
            if candle.close > prior_vah:
                self.above_value_bars += 1
                self.below_value_bars = 0
                self.inside_value_bars = 0
            elif candle.close < prior_val:
                self.below_value_bars += 1
                self.above_value_bars = 0
                self.inside_value_bars = 0
            else:
                self.above_value_bars = 0
                self.below_value_bars = 0
                self.inside_value_bars += 1

        bar_range = candle.high - candle.low
        wide_bar = atr is not None and bar_range > atr * self.config.significance_range_mult
        significant_bar = wide_bar if self.config.use_significance_gate else True

        if prior_vah is not None and prior_val is not None:
            if self.above_value_bars >= self.config.accept_bars:
                if candle.low <= prior_vah and candle.close > prior_vah and significant_bar:
                    self.acceptance_pullback_bars_up += 1
                    if self.acceptance_pullback_bars_up >= self.config.acceptance_pullback_bars:
                        self.acceptance_confirmed_up = True
                elif candle.close < prior_vah:
                    self.acceptance_pullback_bars_up = 0
                    self.acceptance_confirmed_up = False
            else:
                self.acceptance_pullback_bars_up = 0
                self.acceptance_confirmed_up = False

            if self.below_value_bars >= self.config.accept_bars:
                if candle.high >= prior_val and candle.close < prior_val and significant_bar:
                    self.acceptance_pullback_bars_down += 1
                    if self.acceptance_pullback_bars_down >= self.config.acceptance_pullback_bars:
                        self.acceptance_confirmed_down = True
                elif candle.close > prior_val:
                    self.acceptance_pullback_bars_down = 0
                    self.acceptance_confirmed_down = False
            else:
                self.acceptance_pullback_bars_down = 0
                self.acceptance_confirmed_down = False

        valid_current_poc = current_poc is not None
        valid_prior_developing_poc = self._previous_developing_poc is not None
        va_shift_up = (
            current_vah is not None and current_val is not None
            and prior_vah is not None and prior_val is not None
            and current_vah > prior_vah and current_val > prior_val
        )
        va_shift_down = (
            current_vah is not None and current_val is not None
            and prior_vah is not None and prior_val is not None
            and current_vah < prior_vah and current_val < prior_val
        )
        poc_threshold = self.row_size * self.config.poc_delta_threshold_rows
        poc_shift_up = (
            valid_current_poc and valid_prior_developing_poc
            and current_poc - self._previous_developing_poc >= poc_threshold
        )
        poc_shift_down = (
            valid_current_poc and valid_prior_developing_poc
            and self._previous_developing_poc - current_poc >= poc_threshold
        )
        value_migration_up = va_shift_up and poc_shift_up
        value_migration_down = va_shift_down and poc_shift_down

        if valid_current_poc and prior_vah is not None and prior_val is not None:
            if current_poc > prior_vah:
                self.poc_above_bars += 1
                self.poc_below_bars = 0
            elif current_poc < prior_val:
                self.poc_below_bars += 1
                self.poc_above_bars = 0
            else:
                self.poc_above_bars = 0
                self.poc_below_bars = 0

        new_value_developing_up = (
            self.poc_above_bars >= self.config.poc_persistence_bars
            and self.cycle_bars >= self.config.development_min
        )
        new_value_developing_down = (
            self.poc_below_bars >= self.config.poc_persistence_bars
            and self.cycle_bars >= self.config.development_min
        )

        effective_timeout = self.config.discovery_timeout
        if self.config.use_adaptive_timeout and atr is not None:
            effective_timeout = max(
                3,
                min(20, self._round_pine(self.config.discovery_timeout * (atr / 250.0))),
            )

        previous_state = self.auction_state
        transition_event = None
        development_stalled = False
        if prior_vah is not None and prior_val is not None and self.cycle_bars > 0:
            inputs = TransitionInputs(
                bar_index=self.bar_index,
                discovery_start_bar=self.discovery_start_bar,
                effective_timeout=effective_timeout,
                close=candle.close,
                current_poc=current_poc,
                prior_vah=prior_vah,
                prior_val=prior_val,
                cycle_bars=self.cycle_bars,
                development_min=self.config.development_min,
                inside_value_bars=self.inside_value_bars,
                accept_bars=self.config.accept_bars,
                failed_discovery_confirm_bars=self.config.failed_discovery_confirm_bars,
                development_stall_bars=self.config.development_stall_bars,
                reentry_persistence_bars=self.config.reentry_persistence_bars,
                price_acceptance_up=self.acceptance_confirmed_up,
                price_acceptance_down=self.acceptance_confirmed_down,
                value_migration_up=value_migration_up,
                value_migration_down=value_migration_down,
                new_value_developing_up=new_value_developing_up,
                new_value_developing_down=new_value_developing_down,
            )
            (
                self.auction_state,
                self.discovery_start_bar,
                development_stalled,
                transition_event,
            ) = advance_auction_state(
                self.auction_state,
                self.discovery_start_bar,
                self.state_counters,
                inputs,
            )

        # Pine currentPOCReference[1] is the previous chart bar, including
        # across the 00:00 UTC cycle boundary; do not clear this on reset.
        self._previous_developing_poc = current_poc
        return PineBarResult(
            open_time=candle.open_time,
            bar_index=self.bar_index,
            state_code=self.auction_state,
            state_name=STATE_NAMES[self.auction_state],
            previous_state_code=previous_state,
            prior_vah=prior_vah,
            prior_val=prior_val,
            prior_poc=self.prior_poc,
            developing_vah=current_vah,
            developing_val=current_val,
            developing_poc=current_poc,
            atr20=atr,
            row_size=self.row_size,
            cycle_bars=self.cycle_bars,
            transition_event=transition_event,
            development_stalled=development_stalled,
        )

    def replay(self, candles: Iterable[Candle]) -> list[PineBarResult]:
        results: list[PineBarResult] = []
        previous_time: Optional[datetime] = None
        for candle in candles:
            if previous_time is not None:
                delta_seconds = (candle.open_time - previous_time).total_seconds()
                if delta_seconds <= 0:
                    raise ValueError("Candles must be strictly increasing and unique")
            results.append(self.process_bar(candle))
            previous_time = candle.open_time
        return results
