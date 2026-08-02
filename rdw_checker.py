import apk_check
import check_tenaamstelling_changes
import time
from extra_scripts import check_bezetting
from extra_scripts import cleanup

MAX_RDW_CHECKS_PER_RUN = 500


def _format_duration(seconds: float) -> str:
    minutes = int(seconds // 60)
    remaining_seconds = int(seconds % 60)
    return f"{minutes}m {remaining_seconds}s"


def main() -> None:
    pipeline_started = time.perf_counter()
    total_budget = MAX_RDW_CHECKS_PER_RUN
    try:
        print(f"Start gecombineerde RDW pipeline met totaal budget: {total_budget}")

        apk_started = time.perf_counter()
        apk_used = apk_check.run(max_checks=total_budget)
        apk_duration = time.perf_counter() - apk_started
        print(f"APK stap duur: {_format_duration(apk_duration)}")

        remaining_budget = max(0, total_budget - max(0, apk_used))
        if remaining_budget <= 0:
            print("Geen resterend RDW budget voor datum_tenaamstelling_dt en bezetting controle.")
            return

        print(
            "Start controle datum_tenaamstelling_dt met budget: "
            f"{remaining_budget}..."
        )
        tenaamstelling_checked = 0
        tenaamstelling_started = time.perf_counter()
        try:
            tenaamstelling_checked = check_tenaamstelling_changes.run(max_checks=remaining_budget)
            print(
                "Tenaamstelling controle voltooid: "
                f"{tenaamstelling_checked} kentekens gecontroleerd."
            )
        except Exception as exc:
            print(f"Tenaamstelling controle kon niet worden uitgevoerd: {exc}")
        finally:
            tenaamstelling_duration = time.perf_counter() - tenaamstelling_started
            print(f"Tenaamstelling stap duur: {_format_duration(tenaamstelling_duration)}")

        bezetting_budget = max(0, remaining_budget - max(0, tenaamstelling_checked))
        if bezetting_budget <= 0:
            print("Geen resterend RDW budget voor bezetting controle.")
            return

        print(f"Start bezetting controle met resterend RDW budget: {bezetting_budget}...")
        bezetting_started = time.perf_counter()
        try:
            bezetting_checked = check_bezetting.run(max_checks=bezetting_budget)
            print(
                "Bezetting controle voltooid: "
                f"{bezetting_checked} RDW checks gebruikt."
            )
        except Exception as exc:
            print(f"Bezetting controle kon niet worden uitgevoerd: {exc}")
        finally:
            bezetting_duration = time.perf_counter() - bezetting_started
            print(f"Bezetting stap duur: {_format_duration(bezetting_duration)}")
    finally:
        total_duration = time.perf_counter() - pipeline_started
        print(f"Totale pipeline duur: {_format_duration(total_duration)}")

    print("Start cleanup...")
    cleanup.main()


if __name__ == "__main__":
    main()