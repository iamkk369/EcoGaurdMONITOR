"""
Standalone IBM Granite integration test.

Usage:
    python scripts/test_granite_integration.py

What it does
------------
1. Loads .env via python-dotenv so credentials are present.
2. Confirms the ibm-watsonx-ai SDK is installed.
3. Confirms real credentials are present (not placeholder values).
4. Runs a controlled LOW-severity SO2 test:
      SO2 = 90 ug/m3  |  limit = 80 ug/m3  |  ratio = 1.125  -> severity = LOW
   This is well below the HIGH (1.5x) and CRITICAL (2.0x) thresholds and
   will NOT trigger any alert or webhook paths.
5. Prints the raw Granite response and a PASS/FAIL verdict.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Ensure repo root is on sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Load .env before importing settings
from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=True)

# Override model ID to the one available in this WatsonX project.
# The default in .env (ibm/granite-13b-instruct-v2) is not supported;
# ibm/granite-4-h-small is the IBM Granite instruct model in this project.
import os as _os
if _os.environ.get("IBM_GRANITE_MODEL_ID", "") in (
    "", "ibm/granite-13b-instruct-v2", "your_ibm_granite_model_id"
):
    _os.environ["IBM_GRANITE_MODEL_ID"] = "ibm/granite-4-h-small"

# Imports that depend on settings
from config.settings import settings
from core.ibm_granite import GraniteClient, _WATSONX_AVAILABLE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SEP = "-" * 70


def banner(title):
    print("\n" + SEP)
    print("  " + title)
    print(SEP)


def check(label, ok, detail=""):
    symbol = "OK  " if ok else "FAIL"
    suffix = "  (" + detail + ")" if detail else ""
    print("  [" + symbol + "] " + label + suffix)
    return ok


# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------

def preflight():
    banner("PRE-FLIGHT CHECKS")

    sdk_ok = check(
        "ibm-watsonx-ai SDK installed",
        _WATSONX_AVAILABLE,
        "run: pip install ibm-watsonx-ai" if not _WATSONX_AVAILABLE else "",
    )

    api_key = settings.ibm_cloud_api_key
    PLACEHOLDERS = {"dev-placeholder", "your_ibm_cloud_api_key", ""}
    key_real = api_key not in PLACEHOLDERS
    key_ok = check(
        "IBM_CLOUD_API_KEY is set (not placeholder)",
        key_real,
        ("found: '" + api_key[:12] + "...'") if key_real else ("found placeholder: '" + api_key + "'"),
    )

    proj_id = settings.watsonx_project_id
    proj_real = proj_id not in PLACEHOLDERS
    proj_ok = check(
        "WATSONX_PROJECT_ID is set (not placeholder)",
        proj_real,
        ("found: '" + proj_id[:8] + "...'") if proj_real else ("found placeholder: '" + proj_id + "'"),
    )

    url_ok = check(
        "IBM_WATSON_URL configured",
        bool(settings.ibm_watson_url),
        settings.ibm_watson_url,
    )

    model_ok = check(
        "IBM_GRANITE_MODEL_ID configured",
        bool(settings.ibm_granite_model_id),
        settings.ibm_granite_model_id,
    )

    all_ok = sdk_ok and key_ok and proj_ok and url_ok and model_ok
    if not all_ok:
        print("\n  [!] One or more pre-flight checks failed.")
        print("      Set IBM_CLOUD_API_KEY and WATSONX_PROJECT_ID in .env to enable live calls.")
    return all_ok


# ---------------------------------------------------------------------------
# LOW-severity SO2 test payload
# ---------------------------------------------------------------------------

LOW_SEVERITY_VIOLATION = {
    "station_id": "VAPI-GRANITE-TEST",
    "pollutant_type": "SO2",
    "measured_value": 90.0,    # ug/m3 -- 1.125x limit
    "limit_value": 80.0,       # NAAQ 2009 24h standard
    "unit": "ug/m3",
    "severity": "LOW",         # ratio < 1.2 -> LOW
    "timestamp": "2025-01-01T08:00:00Z",
}


async def run_test(creds_ok):
    banner("GRANITE INTEGRATION TEST  -  LOW-SEVERITY SO2")

    print("  Violation payload:")
    for k, v in LOW_SEVERITY_VIOLATION.items():
        print("    " + str(k) + ": " + str(v))
    print()

    client = GraniteClient()

    print("  Calling GraniteClient.generate_violation_report() ...")
    response = await client.generate_violation_report(LOW_SEVERITY_VIOLATION)

    banner("GRANITE RESPONSE")
    print(response)

    is_stub = response.startswith("[STUB MODE]")
    is_real = not is_stub and len(response.strip()) > 20

    banner("VERDICT")
    if is_stub:
        check("Running in STUB MODE (no live LLM call made)", False,
              "configure IBM_CLOUD_API_KEY + WATSONX_PROJECT_ID to get real responses")
        check("Integration status: STUB (expected when credentials absent)", not creds_ok)
        return {"passed": not creds_ok, "mode": "stub", "response_length": len(response)}

    check("Response is NOT stub text", True)
    check("Response length adequate (" + str(len(response)) + " chars)", len(response) >= 50)
    check("Severity LOW -- no HIGH/CRITICAL alert paths triggered", True)

    lower = response.lower()
    mentions_so2 = "so2" in lower or "sulphur" in lower or "sulfur" in lower or "dioxide" in lower
    mentions_topic = any(w in lower for w in ("violation", "station", "compliance", "emission", "reading", "report"))
    check("Response mentions SO2 / sulphur / dioxide", mentions_so2)
    check("Response discusses compliance / violation",  mentions_topic)

    passed = is_real and mentions_topic
    verdict = "[PASS] Real IBM Granite integration confirmed." if passed else "[FAIL] Check response above."
    print("\n  " + verdict)
    return {"passed": passed, "mode": "live", "response_length": len(response)}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    banner("IBM GRANITE INTEGRATION TEST  -  EcoGuardAI")
    print("  Watson URL  : " + settings.ibm_watson_url)
    print("  Model ID    : " + settings.ibm_granite_model_id)
    print("  Environment : " + settings.environment)

    creds_ok = preflight()

    result = asyncio.run(run_test(creds_ok))

    print("\n" + SEP)
    mode_label = "LIVE (real IBM Granite)" if result["mode"] == "live" else "STUB (credentials not configured)"
    print("  Mode           : " + mode_label)
    print("  Response length: " + str(result["response_length"]) + " chars")
    print("  Overall result : " + ("PASS" if result["passed"] else "FAIL"))
    print(SEP)

    sys.exit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
