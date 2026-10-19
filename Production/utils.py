from datetime import datetime
from django.db.models import Max
from .models import ReworkProduction


def Create_Rework_No():
    current_year = datetime.now().year % 100
    next_year = (datetime.now().year + 1) % 100
    prefix = f"REWPRO-{current_year:02d}{next_year:02d}"

    # Get all numbers for the current financial year
    existing_numbers = (
        ReworkProduction.objects
        .filter(rework_no__startswith=prefix)
        .values_list("rework_no", flat=True)
    )

    highest_number = 0

    for number in existing_numbers:
        suffix = number[len(prefix):]

        if suffix.isdigit():
            highest_number = max(highest_number, int(suffix))

    next_number = highest_number + 1

    return f"{prefix}{next_number:05d}"