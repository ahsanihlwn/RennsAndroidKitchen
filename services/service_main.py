# SPDX-License-Identifier: MPL-2.0
import os
import sys
import asyncio
from multiprocessing import freeze_support


def main():

    # mark proses ini sebagai service mode (optional, untuk debugging)
    os.environ["RAK_SERVICE_MODE"] = "1"

    # pastiin path project root ada di sys.path (di frozen exe ini biasanya udah,
    # tapi kalau dev mode lewat python -m, butuh fallback)
    here = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(here)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    # import setelah path siap
    from services.config_engine_service import run_server

    try:
        asyncio.run(run_server())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        # log ke stderr — parent bisa baca via pipe atau abaikan
        print(f"[service_main] crashed: {e}", file=sys.stderr, flush=True)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    freeze_support()
    main()
