import os

import azure.functions as func

from .main import run_once

app = func.FunctionApp()


def _build_schedule(minutes: int) -> str:
    if minutes <= 0:
        minutes = 15

    if minutes == 60:
        return "0 0 * * * *"

    if minutes < 60 and 60 % minutes == 0:
        return f"0 */{minutes} * * * *"

    raise ValueError("SYNC_INTERVAL_MINUTES must be 1-59 and divide 60, or be exactly 60")


SYNC_SCHEDULE = _build_schedule(int(os.getenv("SYNC_INTERVAL_MINUTES", "15")))


@app.function_name(name="emu_scim_sync_timer")
@app.schedule(schedule=SYNC_SCHEDULE, arg_name="timer", run_on_startup=False, use_monitor=True)
def emu_scim_sync_timer(timer: func.TimerRequest) -> None:
    run_once()
