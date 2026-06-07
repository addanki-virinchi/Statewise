from ohio import run_scraper


if __name__ == "__main__":
    run_scraper(
        target_boards=["Nursing Board"],
        fixed_license_types=["Registered Nurse (RN)"],
        output_file="ohio_nursing_registered_nurse.csv",
    )
