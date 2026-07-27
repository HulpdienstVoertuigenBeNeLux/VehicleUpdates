import apk_check
import check_tenaamstelling_changes
from extra_scripts import check_bezetting

MAX_RDW_CHECKS_PER_RUN = 10


def main() -> None:
    total_budget = MAX_RDW_CHECKS_PER_RUN

    print(f"Start gecombineerde RDW pipeline met totaal budget: {total_budget}")
    apk_used = apk_check.run(max_checks=total_budget)
    remaining_budget = max(0, total_budget - max(0, apk_used))

    if remaining_budget <= 0:
        print("Geen resterend RDW budget voor datum_tenaamstelling_dt en bezetting controle.")
        return

    print(
        "Start controle datum_tenaamstelling_dt met budget: "
        f"{remaining_budget}..."
    )
    tenaamstelling_checked = 0
    try:
        tenaamstelling_checked = check_tenaamstelling_changes.run(max_checks=remaining_budget)
        print(
            "Tenaamstelling controle voltooid: "
            f"{tenaamstelling_checked} kentekens gecontroleerd."
        )
    except Exception as exc:
        print(f"Tenaamstelling controle kon niet worden uitgevoerd: {exc}")

    bezetting_budget = max(0, remaining_budget - max(0, tenaamstelling_checked))
    if bezetting_budget <= 0:
        print("Geen resterend RDW budget voor bezetting controle.")
        return

    print(f"Start bezetting controle met resterend RDW budget: {bezetting_budget}...")
    try:
        bezetting_checked = check_bezetting.run(max_checks=bezetting_budget)
        print(
            "Bezetting controle voltooid: "
            f"{bezetting_checked} RDW checks gebruikt."
        )
    except Exception as exc:
        print(f"Bezetting controle kon niet worden uitgevoerd: {exc}")


if __name__ == "__main__":
    main()