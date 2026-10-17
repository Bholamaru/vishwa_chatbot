from datetime import datetime
from django.db.models import Max
from .models import ReworkProduction

def Create_Rework_No():
    current_year = datetime.now().year % 100
    next_year = (datetime.now().year + 1) % 100
    prefix = f"REWPRO-{current_year:02d}{next_year:02d}"

    # Get highest existing rework number for current financial year
    highest_no = (
        ReworkProduction.objects
        .filter(
            rework_no__startswith=prefix,
            rework_no__regex=r'^\d+$'
        )
        .aggregate(max_no=Max("rework_no"))
        ["max_no"]
    )

    if highest_no:
        next_number = str(highest_no) + 1
    else:
        next_number = str(prefix + "00001")

    return str(next_number).zfill(9)