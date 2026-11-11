#!/usr/bin/env python3
"""
research_engine.py
==================
Independent BTC Auction Market Theory Research State Engine.
Includes the external fixture runner for independent T01-T15 execution.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import unittest

from collections import deque
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

ENGINE_VERSION = "1.0.0"

IN_VALUE = "IN_VALUE"
DISCOVERY_CANDIDATE_BELOW = "DISCOVERY_CANDIDATE_BELOW"
DISCOVERY_CANDIDATE_ABOVE = "DISCOVERY_CANDIDATE_ABOVE"
DISCOVERY_CONFIRMED_BELOW = "DISCOVERY_CONFIRMED_BELOW"
DISCOVERY_CONFIRMED_ABOVE = "DISCOVERY_CONFIRMED_ABOVE"
REENTRY_CONFIRMED = "REENTRY_CONFIRMED"
BALANCE_CONFIRMED = "BALANCE_CONFIRMED"
DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
PIN_HYPOTHESIS = "PIN_HYPOTHESIS"

class InputError(ValueError):
    pass

def number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool): return None
    try: result = float(value)
    except (TypeError, ValueError): return None
    if not math.isfinite(result): return None
    return result

def timestamp(value: Any) -> str:
    if value is None: return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return datetime.fromtimestamp(float(value), timezone.utc).isoformat()
    raw = str(value).strip()
    if not raw: return ""
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except ValueError: return raw

def first(data: dict, *keys: str, default=None):
    for key in keys:
        if key in data: return data[key]
    return default

@dataclass(frozen=True)
class Parameters:
    version: str = "v1.0"
    atr_period: int = 21
    atr_multiplier: float = 0.50
    minimum_buffer: float = 50.0
    discovery_closes: int = 2
    reentry_closes: int = 1
    balance_crossings: int = 2
    balance_window: int = 5

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "Parameters":
        data = data or {}
        result = cls(
            version=str(first(data, "version", "parameter_version", default="v1.0")),
            atr_period=int(first(data, "atr_period", default=21)),
            atr_multiplier=float(first(data, "atr_multiplier", "K", default=0.50)),
            minimum_buffer=float(first(data, "minimum_buffer", "min_buffer", default=50)),
            discovery_closes=int(first(data, "discovery_closes", "discovery_confirmation_closes", default=2)),
            reentry_closes=int(first(data, "reentry_closes", "reentry_confirmation_closes", default=1)),
            balance_crossings=int(first(data, "balance_crossings", "balance_confirmation_crossings", default=2)),
            balance_window=int(first(data, "balance_window", "balance_rolling_window", default=5)),
        )
        return result

@dataclass(frozen=True)
class Profile:
    profile_id: str
    vah: float
    poc: float
    val: float
    frozen: bool = True

    @classmethod
    def from_dict(cls, data: dict, fallback_id: Optional[str] = None) -> "Profile":
        profile_id = first(data, "profile_id", "id", default=fallback_id)
        vah = number(first(data, "vah", "VAH"))
        poc = number(first(data, "poc", "POC"))
        val = number(first(data, "val", "VAL"))
        if profile_id is None: raise InputError("Profile ID is missing.")
        if vah is None or poc is None or val is None: raise InputError("Profile requires numeric VAH, POC and VAL.")
        if not val <= poc <= vah: raise InputError("Profile must satisfy VAL <= POC <= VAH.")
        frozen = bool(first(data, "frozen", "is_frozen", default=True))
        return cls(profile_id=str(profile_id), vah=vah, poc=poc, val=val, frozen=frozen)

class ResearchStateEngine:
    def __init__(self, configuration: Optional[dict] = None, initial_state: str = IN_VALUE):
        self.parameters = Parameters.from_dict(configuration)
        self.confirmed_state = initial_state
        self.current_profile_id = None
        self.current_parameter_version = self.parameters.version
        self.discovery_direction = None
        self.discovery_count = 0
        self.last_non_neutral_poc_side = None
        self.poc_crossings = deque()
        self.valid_candle_index = 0
        self.seen_candle_keys = set()
        self.results = []
        self.events = []

    @staticmethod
    def candle_key(candle: dict) -> tuple:
        return (
            str(first(candle, "exchange", default="BINANCE")).upper(),
            str(first(candle, "symbol", "instrument", default="BTCUSDT")).upper(),
            str(first(candle, "timeframe", "interval", default="30")).lower(),
            timestamp(first(candle, "open_time_utc", "open_time", "timestamp")),
        )

    def resolve_profile(self, candle: dict, document: dict) -> Optional[Profile]:
        raw = first(candle, "profile", "frozen_profile", "profile_reference")
        if isinstance(raw, dict): return Profile.from_dict(raw)
        profile_id = first(candle, "profile_id")
        if profile_id is not None:
            profiles = document.get("profiles", {})
            if str(profile_id) in profiles:
                return Profile.from_dict(profiles[str(profile_id)], str(profile_id))
            if "default_profile" in document:
                default = document["default_profile"]
                if str(first(default, "profile_id", "id", default="")) == str(profile_id):
                    return Profile.from_dict(default)
            flat = {
                "profile_id": profile_id,
                "vah": first(candle, "vah", "VAH"),
                "poc": first(candle, "poc", "POC"),
                "val": first(candle, "val", "VAL"),
                "frozen": first(candle, "profile_frozen", "frozen", default=True),
            }
            if all(flat[k] is not None for k in ("vah", "poc", "val")):
                return Profile.from_dict(flat)
        if "default_profile" in document:
            return Profile.from_dict(document["default_profile"])
        return None

    def resolve_parameters(self, candle: dict, document: dict) -> Parameters:
        inline = first(candle, "parameters", "configuration")
        if isinstance(inline, dict): return Parameters.from_dict(inline)
        version = first(candle, "parameter_version", "config_version")
        parameter_sets = document.get("parameter_sets", document.get("parameter_versions", {}))
        if version is not None and str(version) in parameter_sets:
            return Parameters.from_dict(parameter_sets[str(version)])
        return self.parameters

    @staticmethod
    def candle_is_invalid(candle: dict) -> tuple[bool, str]:
        status = str(first(candle, "data_status", "status", "data_quality", default="VALID")).upper()
        if status in {"DATA_UNAVAILABLE", "INVALID", "STALE", "MISSING", "ERROR"}:
            return True, status
        if candle.get("valid") is False: return True, "VALID_FLAG_FALSE"
        if candle.get("is_valid") is False: return True, "IS_VALID_FLAG_FALSE"
        for field in ("open", "high", "low", "close"):
            value = number(first(candle, field, field.upper()))
            if value is None: return True, f"INVALID_{field.upper()}"
        high = number(first(candle, "high", "HIGH"))
        low = number(first(candle, "low", "LOW"))
        open_price = number(first(candle, "open", "OPEN"))
        close = number(first(candle, "close", "CLOSE"))
        if high < low: return True, "HIGH_BELOW_LOW"
        if high < max(open_price, close): return True, "HIGH_INCONSISTENT_WITH_OHLC"
        if low > min(open_price, close): return True, "LOW_INCONSISTENT_WITH_OHLC"
        return False, "VALID"

    def emit(self, event_type: str, key: tuple, old_state=None, new_state=None, details=None) -> dict:
        event = {
            "event_type": event_type, "exchange": key[0], "symbol": key[1], "timeframe": key[2],
            "open_time_utc": key[3], "old_state": old_state, "new_state": new_state,
            "details": details or {}, "parameter_version": self.current_parameter_version, "profile_id": self.current_profile_id,
        }
        self.events.append(event)
        return event

    def transition(self, new_state: str, key: tuple, details=None) -> Optional[dict]:
        old_state = self.confirmed_state
        if old_state == new_state: return None
        self.confirmed_state = new_state
        return self.emit("STATE_TRANSITION", key, old_state=old_state, new_state=new_state, details=details)

    def apply_profile(self, profile: Profile, key: tuple):
        old_profile_id = self.current_profile_id
        if old_profile_id is None:
            self.current_profile_id = profile.profile_id
            return
        if old_profile_id == profile.profile_id: return
        self.current_profile_id = profile.profile_id
        self.discovery_direction = None
        self.discovery_count = 0
        self.poc_crossings.clear()
        self.last_non_neutral_poc_side = None
        self.emit("PROFILE_ROLLOVER", key, details={"old_profile_id": old_profile_id, "new_profile_id": profile.profile_id, "reset": ["discovery_counter", "poc_crossing_window", "poc_side_memory"]})

    def unavailable_result(self, key: tuple, reason: str, event_start: int) -> dict:
        result = {
            "candle_key": list(key), "ignored": False, "completed": True,
            "developing_state": DATA_UNAVAILABLE, "confirmed_state": self.confirmed_state,
            "discovery_counter": self.discovery_count, "discovery_direction": self.discovery_direction,
            "poc_crossing_count": len(self.poc_crossings), "data_status": DATA_UNAVAILABLE, "reason": reason, "events": self.events[event_start:],
        }
        self.results.append(result)
        return result

    def process_discovery(self, close: float, profile: Profile, params: Parameters, buffer: float, key: tuple):
        lower_threshold = profile.val - buffer
        upper_threshold = profile.vah + buffer
        direction = None
        if close < lower_threshold: direction = "BELOW"
        elif close > upper_threshold: direction = "ABOVE"
        transition_event = None
        if direction is not None:
            if self.discovery_direction == direction: self.discovery_count += 1
            else:
                self.discovery_direction = direction
                self.discovery_count = 1
            developing_state = DISCOVERY_CANDIDATE_BELOW if direction == "BELOW" else DISCOVERY_CANDIDATE_ABOVE
            if self.discovery_count >= params.discovery_closes:
                if direction == "BELOW": target = DISCOVERY_CONFIRMED_BELOW; threshold = lower_threshold
                else: target = DISCOVERY_CONFIRMED_ABOVE; threshold = upper_threshold
                transition_event = self.transition(target, key, {"close": close, "threshold": threshold, "buffer": buffer, "qualifying_closes": self.discovery_count, "profile_id": profile.profile_id, "parameter_version": params.version})
                developing_state = target
        else:
            self.discovery_direction = None
            self.discovery_count = 0
            inside_value = profile.val <= close <= profile.vah
            if inside_value and self.confirmed_state in {DISCOVERY_CONFIRMED_BELOW, DISCOVERY_CONFIRMED_ABOVE}:
                transition_event = self.transition(REENTRY_CONFIRMED, key, {"close": close, "profile_id": profile.profile_id})
                developing_state = REENTRY_CONFIRMED
            elif self.confirmed_state == BALANCE_CONFIRMED: developing_state = BALANCE_CONFIRMED
            elif self.confirmed_state == REENTRY_CONFIRMED: developing_state = REENTRY_CONFIRMED
            elif self.confirmed_state in {DISCOVERY_CONFIRMED_BELOW, DISCOVERY_CONFIRMED_ABOVE}: developing_state = self.confirmed_state
            elif inside_value: developing_state = IN_VALUE
            else: developing_state = "VALUE_EXCURSION"
        return developing_state, transition_event, lower_threshold, upper_threshold

    def process_poc(self, close: float, profile: Profile, params: Parameters, key: tuple):
        crossing = None
        if close < profile.poc: current_side = "BELOW"
        elif close > profile.poc: current_side = "ABOVE"
        else: current_side = "NEUTRAL"
        if current_side != "NEUTRAL":
            previous_side = self.last_non_neutral_poc_side
            if previous_side is not None and previous_side != current_side:
                crossing = f"{previous_side}_TO_{current_side}"
                self.poc_crossings.append({"valid_index": self.valid_candle_index, "timestamp": key[3], "direction": crossing, "close": close})
                self.emit("POC_CROSSING", key, details={"direction": crossing, "poc": profile.poc, "close": close})
            self.last_non_neutral_poc_side = current_side
        earliest_valid_index = self.valid_candle_index - params.balance_window + 1
        while self.poc_crossings and self.poc_crossings[0]["valid_index"] < earliest_valid_index:
            self.poc_crossings.popleft()
        return crossing

    def process_candle(self, candle: dict, document: dict) -> dict:
        key = self.candle_key(candle)
        if not key[3]: raise InputError("Candle is missing open_time_utc/open_time/timestamp.")
        if key in self.seen_candle_keys:
            return {"candle_key": list(key), "ignored": True, "reason": "DUPLICATE_CANDLE", "confirmed_state": self.confirmed_state, "events": []}
        completed = first(candle, "completed", "is_closed", default=True)
        if completed is False:
            return {"candle_key": list(key), "ignored": False, "completed": False, "developing_state": "PROVISIONAL_OBSERVATION", "confirmed_state": self.confirmed_state, "discovery_counter": self.discovery_count, "events": [], "reason": "INTRABAR_NOT_ELIGIBLE_FOR_CONFIRMATION"}
        self.seen_candle_keys.add(key)
        event_start = len(self.events)
        profile = self.resolve_profile(candle, document)
        if profile is None: return self.unavailable_result(key, "MISSING_PROFILE", event_start)
        self.apply_profile(profile, key)
        params = self.resolve_parameters(candle, document)
        if params.version != self.current_parameter_version:
            old_version = self.current_parameter_version
            self.current_parameter_version = params.version
            self.emit("PARAMETER_VERSION_OBSERVED", key, details={"old_version": old_version, "new_version": params.version})
        invalid, invalid_reason = self.candle_is_invalid(candle)
        if invalid: return self.unavailable_result(key, invalid_reason, event_start)
        atr = number(first(candle, "atr", "ATR"))
        if atr is None or atr <= 0: return self.unavailable_result(key, "INVALID_OR_MISSING_ATR", event_start)
        if not profile.frozen: return self.unavailable_result(key, "PROFILE_NOT_FROZEN", event_start)
        close = number(first(candle, "close", "CLOSE"))
        if close is None: return self.unavailable_result(key, "INVALID_CLOSE", event_start)
        self.valid_candle_index += 1
        buffer = max(params.minimum_buffer, atr * params.atr_multiplier)
        developing_state, transition_event, lower_threshold, upper_threshold = self.process_discovery(close, profile, params, buffer, key)
        crossing = self.process_poc(close, profile, params, key)
        balance_transition = None
        if len(self.poc_crossings) >= params.balance_crossings:
            balance_transition = self.transition(BALANCE_CONFIRMED, key, {"poc_crossings_in_window": len(self.poc_crossings), "window_valid_candles": params.balance_window, "poc": profile.poc, "profile_id": profile.profile_id})
            developing_state = BALANCE_CONFIRMED
        elif self.confirmed_state == BALANCE_CONFIRMED: developing_state = BALANCE_CONFIRMED
        options = first(candle, "options_hypothesis", "options_context")
        options_hypothesis = None
        if isinstance(options, str): options_hypothesis = options
        elif isinstance(options, dict): options_hypothesis = first(options, "hypothesis", "state")
        result = {
            "candle_key": list(key), "ignored": False, "completed": True, "data_status": "VALID",
            "developing_state": developing_state, "confirmed_state": self.confirmed_state,
            "discovery_counter": self.discovery_count, "discovery_direction": self.discovery_direction,
            "buffer": buffer, "lower_discovery_threshold": lower_threshold, "upper_discovery_threshold": upper_threshold,
            "poc_crossing": crossing, "poc_crossing_count": len(self.poc_crossings),
            "profile_id": profile.profile_id, "parameter_version": params.version, "options_hypothesis": options_hypothesis,
            "transition_event": (balance_transition or transition_event), "events": self.events[event_start:],
        }
        self.results.append(result)
        return result

    def process(self, candles: list, document: dict, sort_chronologically: bool = True) -> dict:
        rows = list(candles)
        if sort_chronologically: rows.sort(key=lambda row: self.candle_key(row)[3])
        for candle in rows: self.process_candle(candle, document)
        return self.output()

    def output(self) -> dict:
        transitions = [event for event in self.events if event["event_type"] == "STATE_TRANSITION"]
        return {
            "document": "BTC AMT Research Module — State Engine Output", "engine_version": ENGINE_VERSION,
            "parameter_version": self.current_parameter_version, "final_confirmed_state": self.confirmed_state,
            "results": self.results, "transition_events": transitions, "all_events": self.events,
        }

# =============================================================
# EXTERNAL FIXTURE RUNNER — INDEPENDENT T01 TO T15 EXECUTION
# =============================================================

def _fixture_candle_defaults(candle: dict, document: dict, test_index: int, candle_index: int) -> dict:
    row = dict(candle)
    time_fields = ("open_time_utc", "open_time", "timestamp")
    if not any(row.get(field) is not None for field in time_fields):
        minute = candle_index * 30
        hour = minute // 60
        minute = minute % 60
        row["open_time_utc"] = f"2026-01-{test_index + 1:02d}T{hour:02d}:{minute:02d}:00Z"
    row.setdefault("exchange", document.get("exchange", "BINANCE"))
    row.setdefault("symbol", document.get("symbol", "BTCUSDT"))
    row.setdefault("timeframe", document.get("timeframe", "30"))
    if row.get("completed", row.get("is_closed", True)) is False:
        live_price = number(first(row, "live_price", "price"))
        if live_price is not None:
            row.setdefault("open", live_price); row.setdefault("high", live_price); row.setdefault("low", live_price); row.setdefault("close", live_price)
        return row
    close_present = "close" in row or "CLOSE" in row
    close_value = first(row, "close", "CLOSE")
    if close_present and close_value is not None:
        for field in ("open", "high", "low"):
            upper = field.upper()
            if field not in row and upper not in row: row[field] = close_value
    if "atr" not in row and "ATR" not in row:
        fixture_defaults = document.get("fixture_defaults", {})
        row["atr"] = fixture_defaults.get("atr", 100)
    return row

def _fixture_profile_override(candle: dict, document: dict) -> dict:
    row = dict(candle)
    frozen_key_present = "profile_frozen" in row or "frozen" in row
    if not frozen_key_present: return row
    profile_id = first(row, "profile_id")
    profiles = document.get("profiles", {})
    raw_profile = None
    if profile_id is not None: raw_profile = profiles.get(str(profile_id))
    if raw_profile is None: raw_profile = document.get("default_profile")
    if isinstance(raw_profile, dict):
        profile = dict(raw_profile)
        profile["frozen"] = bool(first(row, "profile_frozen", "frozen", default=True))
        row["profile"] = profile
    return row

def _expected_value(expected: dict, *keys):
    for key in keys:
        if key in expected: return True, expected[key]
    return False, None

def _compare_expected(expected: dict, output: dict, processed_rows: list) -> list:
    failures = []
    if not isinstance(expected, dict): return ["Expected result must be a JSON object."]
    final_state = output["final_confirmed_state"]
    transition_events = output["transition_events"]
    transition_occurred = len(transition_events) > 0
    final_result = processed_rows[-1] if processed_rows else {}
    checks = [
        (("final_state", "final_confirmed_state", "confirmed_state"), final_state, "final state"),
        (("transition", "transition_occurred", "has_transition"), transition_occurred, "transition boolean"),
        (("transition_count", "state_transition_count"), len(transition_events), "transition count"),
        (("discovery_counter", "discovery_count"), final_result.get("discovery_counter", 0), "discovery counter"),
        (("discovery_direction",), final_result.get("discovery_direction"), "discovery direction"),
        (("poc_crossing_count", "crossing_count"), final_result.get("poc_crossing_count", 0), "POC crossing count"),
    ]
    for aliases, actual, label in checks:
        present, wanted = _expected_value(expected, *aliases)
        if present and actual != wanted: failures.append(f"{label}: expected {wanted!r}, got {actual!r}")
    present, wanted = _expected_value(expected, "transition_to", "transition_state", "expected_transition_state")
    if present:
        actual = transition_events[-1]["new_state"] if transition_events else None
        if actual != wanted: failures.append(f"transition target: expected {wanted!r}, got {actual!r}")
    present, wanted = _expected_value(expected, "event_type", "expected_event")
    if present:
        event_types = [event["event_type"] for event in output["all_events"]]
        if wanted not in event_types: failures.append(f"expected event {wanted!r} not found; actual events={event_types}")
    expected_candles = expected.get("candles", expected.get("candle_results"))
    if isinstance(expected_candles, list):
        if len(expected_candles) != len(processed_rows): failures.append(f"per-candle result count: expected {len(expected_candles)}, got {len(processed_rows)}")
        for index, candle_expected in enumerate(expected_candles):
            if index >= len(processed_rows): break
            actual_row = processed_rows[index]
            if not isinstance(candle_expected, dict): continue
            candle_checks = [
                (("confirmed_state", "final_state", "state"), actual_row.get("confirmed_state"), "confirmed_state"),
                (("discovery_counter", "discovery_count"), actual_row.get("discovery_counter"), "discovery_counter"),
                (("poc_crossing_count", "crossing_count"), actual_row.get("poc_crossing_count"), "poc_crossing_count"),
                (("ignored",), actual_row.get("ignored"), "ignored"),
                (("developing_state",), actual_row.get("developing_state"), "developing_state"),
            ]
            for aliases, actual, label in candle_checks:
                present, wanted = _expected_value(candle_expected, *aliases)
                if present and actual != wanted: failures.append(f"candle {index + 1} {label}: expected {wanted!r}, got {actual!r}")
    return failures

def run_external_fixture(document: dict) -> dict:
    test_cases = document.get("test_cases")
    if not isinstance(test_cases, list): raise InputError("External fixture must contain a test_cases array.")
    if not test_cases: raise InputError("External fixture contains no test cases.")
    
    print("=" * 72)
    print("BTC AMT RESEARCH ENGINE — EXTERNAL FIXTURE VALIDATION")
    print("=" * 72)
    print(f"Engine version: {ENGINE_VERSION}")
    print(f"Test cases:     {len(test_cases)}")
    print("-" * 72)
    
    test_reports = []
    for test_index, test_case in enumerate(test_cases):
        test_id = str(first(test_case, "test_id", "id", default=f"T{test_index + 1:02d}"))
        description = str(first(test_case, "description", "name", default=""))
        
        # Handle the special T12 "runs" structure
        if "runs" in test_case:
            all_runs_passed = True
            run_failures = []
            for run in test_case["runs"]:
                run_config = dict(document.get("configuration", {}))
                if "parameter_version" in run:
                    parameter_sets = document.get("parameter_sets", document.get("parameter_versions", {}))
                    if str(run["parameter_version"]) in parameter_sets:
                        run_config.update(parameter_sets[str(run["parameter_version"])])
                        
                run_engine = ResearchStateEngine(configuration=run_config, initial_state=run.get("initial_state", IN_VALUE))
                run_candles = run.get("candles", [])
                actual_rows = []
                
                for candle_index, raw_candle in enumerate(run_candles):
                    if not isinstance(raw_candle, dict): continue
                    row = _fixture_candle_defaults(raw_candle, document, test_index, candle_index)
                    if "profile_id" not in row and "profile_id" in test_case:
                        row["profile_id"] = test_case["profile_id"]
                    row = _fixture_profile_override(row, document)
                    actual_rows.append(run_engine.process_candle(row, document))
                    
                output = run_engine.output()
                failures = _compare_expected(run.get("expected", {}), output, actual_rows)
                if failures:
                    all_runs_passed = False
                    run_failures.extend([f"{run['run_id']}: {f}" for f in failures])
                    
            passed = all_runs_passed
            report = {
                "test_id": test_id, "description": description, "status": "PASS" if passed else "FAIL",
                "failures": run_failures
            }
            test_reports.append(report)
            
            label = "PASS" if passed else "FAIL"
            print(f"{test_id:<8} {label:<6} {description}")
            for f in run_failures: print(f"         - {f}")
            continue

        initial_state = str(test_case.get("initial_state", IN_VALUE))
        candles = test_case.get("candles", test_case.get("observations", []))
        expected = test_case.get("expected", {})
        if not isinstance(candles, list): candles = []
        
        configuration = dict(document.get("configuration", {}))
        test_configuration = test_case.get("configuration", test_case.get("parameters"))
        if isinstance(test_configuration, dict): configuration.update(test_configuration)
        
        parameter_version = test_case.get("parameter_version")
        if parameter_version is not None:
            parameter_sets = document.get("parameter_sets", document.get("parameter_versions", {}))
            if str(parameter_version) in parameter_sets: configuration.update(parameter_sets[str(parameter_version)])
            
        engine = ResearchStateEngine(configuration=configuration, initial_state=initial_state)
        normalized_rows = []
        for candle_index, raw_candle in enumerate(candles):
            if not isinstance(raw_candle, dict): continue
            row = _fixture_candle_defaults(raw_candle, document, test_index, candle_index)
            if "profile_id" not in row and "profile_id" in test_case:
                row["profile_id"] = test_case["profile_id"]
            row = _fixture_profile_override(row, document)
            normalized_rows.append(row)
            
        actual_rows = []
        for row in normalized_rows: actual_rows.append(engine.process_candle(row, document))
        output = engine.output()
        failures = _compare_expected(expected, output, actual_rows)
        passed = len(failures) == 0
        
        report = {
            "test_id": test_id, "description": description, "status": "PASS" if passed else "FAIL",
            "initial_state": initial_state, "final_state": output["final_confirmed_state"],
            "transition_count": len(output["transition_events"]),
            "discovery_counter": (actual_rows[-1].get("discovery_counter", 0) if actual_rows else engine.discovery_count),
            "candles_processed": len(actual_rows), "failures": failures, "output": output,
        }
        test_reports.append(report)
        
        label = "PASS" if passed else "FAIL"
        print(f"{test_id:<8} {label:<6} {description}")
        for failure in failures: print(f"         - {failure}")
        
    passed_count = sum(report["status"] == "PASS" for report in test_reports)
    failed_count = len(test_reports) - passed_count
    
    print("-" * 72)
    print(f"RESULT: {passed_count} PASSED / {len(test_reports)} TOTAL")
    print(f"FAILURES: {failed_count}")
    if failed_count == 0: print("EXTERNAL FIXTURE ACCEPTANCE: PASS")
    else: print("EXTERNAL FIXTURE ACCEPTANCE: FAIL")
    
    return {
        "document": "BTC AMT Research Module — External Fixture Results", "engine_version": ENGINE_VERSION,
        "fixture_version": document.get("fixture_version"), "total_tests": len(test_reports),
        "passed": passed_count, "failed": failed_count, "acceptance": "PASS" if failed_count == 0 else "FAIL",
        "test_reports": test_reports,
    }

# =============================================================
# JSON INTERFACE & COMMAND-LINE ENTRY POINT
# =============================================================

def load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as file: document = json.load(file)
    if isinstance(document, list): document = {"candles": document}
    if not isinstance(document, dict): raise InputError("Input must be a JSON object or candle array.")
    if "test_cases" in document:
        if not isinstance(document["test_cases"], list): raise InputError("test_cases must be an array.")
        return document
    if not isinstance(document.get("candles"), list):
        raise InputError("JSON document must contain either candles or test_cases.")
    return document

def run_document(document: dict) -> dict:
    if "test_cases" in document: return run_external_fixture(document)
    configuration = document.get("configuration", {})
    engine = ResearchStateEngine(configuration)
    return engine.process(candles=document["candles"], document=document, sort_chronologically=True)

class ResearchEngineAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.profile = {"profile_id": "P1", "vah": 83000, "poc": 82500, "val": 82000, "frozen": True}
        self.document = {
            "default_profile": self.profile,
            "configuration": {"version": "v1.0", "atr_period": 21, "atr_multiplier": 0.50, "minimum_buffer": 50, "discovery_closes": 2, "reentry_closes": 1, "balance_crossings": 2, "balance_window": 5},
            "candles": [],
        }
    def candle(self, index, close, profile=None, **extra):
        minute = index * 30; hour = minute // 60; minute = minute % 60
        row = {
            "exchange": "BINANCE", "symbol": "BTCUSDT", "timeframe": "30",
            "open_time_utc": f"2026-01-01T{hour:02d}:{minute:02d}:00Z",
            "open": close, "high": close + 10, "low": close - 10, "close": close,
            "atr": 100, "profile": profile or self.profile, "completed": True, "data_status": "VALID",
        }
        row.update(extra); return row
    def engine(self, initial=IN_VALUE): return ResearchStateEngine(self.document["configuration"], initial_state=initial)
    def run_rows(self, engine, rows): return engine.process(rows, self.document)
    def test_T01_marginal_breach(self):
        e = self.engine(); out = self.run_rows(e, [self.candle(0, 81999)])
        self.assertEqual(out["final_confirmed_state"], IN_VALUE); self.assertEqual(out["results"][0]["discovery_counter"], 0)
    def test_T02_breach_inside_buffer(self):
        e = self.engine(); out = self.run_rows(e, [self.candle(0, 81970)])
        self.assertEqual(out["results"][0]["developing_state"], "VALUE_EXCURSION"); self.assertEqual(out["final_confirmed_state"], IN_VALUE)
    def test_T03_failed_consecutive_close(self):
        e = self.engine(); out = self.run_rows(e, [self.candle(0, 81940), self.candle(1, 82000)])
        self.assertEqual(out["results"][0]["discovery_counter"], 1); self.assertEqual(out["results"][1]["discovery_counter"], 0); self.assertEqual(out["final_confirmed_state"], IN_VALUE)
    def test_T04_discovery_below(self):
        e = self.engine(); out = self.run_rows(e, [self.candle(0, 81940), self.candle(1, 81930)])
        self.assertEqual(out["final_confirmed_state"], DISCOVERY_CONFIRMED_BELOW)
    def test_T05_discovery_above(self):
        e = self.engine(); out = self.run_rows(e, [self.candle(0, 83060), self.candle(1, 83070)])
        self.assertEqual(out["final_confirmed_state"], DISCOVERY_CONFIRMED_ABOVE)
    def test_T06_reentry_not_balance(self):
        e = self.engine(initial=DISCOVERY_CONFIRMED_BELOW); out = self.run_rows(e, [self.candle(0, 82500)])
        self.assertEqual(out["final_confirmed_state"], REENTRY_CONFIRMED); self.assertNotEqual(out["final_confirmed_state"], BALANCE_CONFIRMED)
    def test_T07_no_poc_crossings(self):
        e = self.engine(initial=REENTRY_CONFIRMED); out = self.run_rows(e, [self.candle(0, 82100), self.candle(1, 82200), self.candle(2, 82300), self.candle(3, 82400), self.candle(4, 82450)])
        self.assertEqual(out["final_confirmed_state"], REENTRY_CONFIRMED); self.assertEqual(len(out["transition_events"]), 0)
    def test_T08_one_poc_crossing(self):
        e = self.engine(initial=REENTRY_CONFIRMED); out = self.run_rows(e, [self.candle(0, 82400), self.candle(1, 82600), self.candle(2, 82700), self.candle(3, 82650), self.candle(4, 82550)])
        self.assertEqual(out["final_confirmed_state"], REENTRY_CONFIRMED); self.assertEqual(len(out["transition_events"]), 0)
    def test_T09_two_poc_crossings(self):
        e = self.engine(initial=REENTRY_CONFIRMED); out = self.run_rows(e, [self.candle(0, 82400), self.candle(1, 82600), self.candle(2, 82700), self.candle(3, 82480), self.candle(4, 82490)])
        self.assertEqual(out["results"][3]["poc_crossing_count"], 2); self.assertEqual(out["results"][3]["confirmed_state"], BALANCE_CONFIRMED); self.assertEqual(out["results"][4]["confirmed_state"], BALANCE_CONFIRMED); self.assertEqual(len(out["transition_events"]), 1)
    def test_T10_intrabar_breach(self):
        e = self.engine(); intrabar = self.candle(0, 81900, completed=False); intrabar["low"] = 81800
        result = e.process_candle(intrabar, self.document)
        self.assertEqual(result["confirmed_state"], IN_VALUE); self.assertEqual(e.discovery_count, 0)
    def test_T11_options_hypothesis_separate(self):
        e = self.engine(initial=REENTRY_CONFIRMED); row = self.candle(0, 82500, options_hypothesis=PIN_HYPOTHESIS); out = self.run_rows(e, [row])
        self.assertEqual(out["final_confirmed_state"], REENTRY_CONFIRMED); self.assertEqual(out["results"][0]["options_hypothesis"], PIN_HYPOTHESIS); self.assertFalse(any(event["new_state"] == PIN_HYPOTHESIS for event in out["transition_events"]))
    def test_T12_parameter_version_immutability(self):
        v1_document = {**self.document, "configuration": {**self.document["configuration"], "version": "v1.0", "minimum_buffer": 50}}
        v1 = ResearchStateEngine(v1_document["configuration"]); v1_out = v1.process([self.candle(0, 81940), self.candle(1, 81930)], v1_document)
        original_events = json.dumps(v1_out["transition_events"], sort_keys=True)
        v2_document = {**self.document, "configuration": {**self.document["configuration"], "version": "v1.1", "minimum_buffer": 65}}
        v2 = ResearchStateEngine(v2_document["configuration"]); v2_out = v2.process([self.candle(0, 81940), self.candle(1, 81930)], v2_document)
        self.assertEqual(v2_out["results"][0]["discovery_counter"], 0); self.assertEqual(v2_out["results"][1]["discovery_counter"], 1)
        self.assertEqual(original_events, json.dumps(v1_out["transition_events"], sort_keys=True)); self.assertEqual(v1_out["final_confirmed_state"], DISCOVERY_CONFIRMED_BELOW)
    def test_T13_profile_rollover(self):
        p2 = {"profile_id": "P2", "vah": 83500, "poc": 82750, "val": 82000, "frozen": True}
        e = self.engine(); out = self.run_rows(e, [self.candle(0, 81940, profile=self.profile), self.candle(1, 82040, profile=p2), self.candle(2, 81970, profile=p2)])
        self.assertEqual(out["results"][0]["discovery_counter"], 1); self.assertEqual(out["results"][1]["profile_id"], "P2"); self.assertEqual(out["results"][1]["discovery_counter"], 0); self.assertEqual(out["results"][2]["discovery_counter"], 0)
        self.assertTrue(any(event["event_type"] == "PROFILE_ROLLOVER" for event in out["all_events"]))
    def test_T14_duplicate_idempotency(self):
        e = self.engine(); row = self.candle(0, 81940); first_result = e.process_candle(row, self.document); count_after_first = e.discovery_count
        duplicate_result = e.process_candle(row, self.document)
        self.assertEqual(count_after_first, 1); self.assertTrue(duplicate_result["ignored"]); self.assertEqual(e.discovery_count, 1); self.assertEqual(len(e.results), 1)
    def test_T15_invalid_data(self):
        invalid_rows = [self.candle(0, 81940, open=None), self.candle(1, 81940, atr=None), self.candle(2, 81940, data_status="STALE")]
        e = self.engine(); out = self.run_rows(e, invalid_rows)
        self.assertEqual(out["final_confirmed_state"], IN_VALUE); self.assertEqual(e.discovery_count, 0); self.assertEqual(len(out["transition_events"]), 0)
        for result in out["results"]: self.assertEqual(result["developing_state"], DATA_UNAVAILABLE)
        e2 = self.engine(); missing_profile = self.candle(0, 81940); missing_profile.pop("profile"); missing_profile.pop("profile_id", None)
        doc_without_profile = {"configuration": self.document["configuration"], "candles": []}
        result = e2.process_candle(missing_profile, doc_without_profile)
        self.assertEqual(result["developing_state"], DATA_UNAVAILABLE)
    def test_POC_exact_close_is_neutral(self):
        e = self.engine(initial=REENTRY_CONFIRMED); out = self.run_rows(e, [self.candle(0, 82600), self.candle(1, 82500), self.candle(2, 82400)])
        self.assertIsNone(out["results"][1]["poc_crossing"]); self.assertEqual(out["results"][2]["poc_crossing"], "ABOVE_TO_BELOW")
    def test_invalid_candle_pauses_discovery_counter(self):
        e = self.engine(); out = self.run_rows(e, [self.candle(0, 81940), self.candle(1, 81930, data_status="DATA_UNAVAILABLE"), self.candle(2, 81930)])
        self.assertEqual(out["results"][0]["discovery_counter"], 1); self.assertEqual(out["results"][1]["discovery_counter"], 1); self.assertEqual(out["final_confirmed_state"], DISCOVERY_CONFIRMED_BELOW)

def run_self_tests():
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ResearchEngineAcceptanceTests)
    total = suite.countTestCases()
    print("=" * 64); print("BTC AMT RESEARCH ENGINE — ACCEPTANCE TEST SUITE"); print("=" * 64)
    print(f"Engine version: {ENGINE_VERSION}"); print(f"Tests scheduled: {total}"); print("-" * 64)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print("-" * 64); print(f"RESULT: {result.testsRun - len(result.failures) - len(result.errors)} PASSED / {total} TOTAL")
    print(f"FAILURES: {len(result.failures)}"); print(f"ERRORS:   {len(result.errors)}")
    if result.wasSuccessful(): print("ACCEPTANCE: PASS"); return 0
    print("ACCEPTANCE: FAIL"); return 1

def main():
    parser = argparse.ArgumentParser(description="Independent BTC AMT Research State Engine")
    parser.add_argument("input", nargs="?", help="Input JSON file or external test fixture")
    parser.add_argument("--output", help="Output JSON file; defaults to stdout")
    parser.add_argument("--self-test", action="store_true", help="Run the built-in acceptance tests")
    args = parser.parse_args()
    if args.self_test: return run_self_tests()
    if not args.input: parser.error("Provide an input JSON file or use --self-test.")
    try:
        document = load_json(args.input); result = run_document(document)
        rendered = json.dumps(result, indent=2, ensure_ascii=False)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as file: file.write(rendered + "\n")
            print(f"Output written to: {args.output}")
        else: print(rendered)
        if result.get("acceptance") == "FAIL": return 1
        return 0
    except (OSError, json.JSONDecodeError, InputError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr); return 2

if __name__ == "__main__":
    raise SystemExit(main())