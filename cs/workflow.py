"""Orchestration: provider -> pipeline -> export -> case record. Used by the UI and the tests."""
from __future__ import annotations
from .config import EncroachParams, FloodParams, Settings, encroach_params_for
from .db import CaseStore
from .encroach import run_encroachment
from .exports import export_result
from .flood import run_flood
from .imagery import get_provider
from .models import Result


def _register(settings: Settings, result: Result) -> tuple[Result, str]:
    store = CaseStore(settings.db_path)
    case_id = store.next_id(result.pipeline.value)
    exports = export_result(result, case_id, settings.output_dir)
    store.create_case(case_id, result, exports)
    return result, case_id


def execute_flood(settings: Settings, scenario: str, params: FloodParams | None = None, study_area: str = "solapur_bhima"):
    provider = get_provider(settings)
    grid, scenes = provider.flood_scenes(scenario, study_area=study_area)
    supp = None
    try:
        _, supp = provider.s2_scenes("persistent_loss" if scenario == "flood_event" else "seasonal_only", study_area=study_area)
    except Exception:
        supp = None
    return _register(settings, run_flood(scenes, grid, params or FloodParams(), supplementary_scenes=supp))


def execute_encroachment(settings: Settings, scenario: str, params: EncroachParams | None = None, study_area: str = "solapur_bhima"):
    provider = get_provider(settings)
    grid, scenes = provider.s2_scenes(scenario, study_area=study_area)
    supp = None
    try:
        _, supp = provider.flood_scenes("no_flood" if scenario == "persistent_loss" else "flood_event", study_area=study_area)
    except Exception:
        supp = None
    # Use study-area-specific defaults when no explicit params provided.
    effective_params = params if params is not None else encroach_params_for(study_area)
    return _register(settings, run_encroachment(scenes, grid, effective_params, supplementary_scenes=supp))
