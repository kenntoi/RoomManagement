# E:\RAMMS\website\create_fake_data.py

from . import db  # This assumes db is initialized in __init__.py
from .models import Usage  # Assuming Usage is defined in models.py
from datetime import datetime, timedelta
import random

def create_fake_usage_data():
    # Start with today and go back a week
    today = datetime.today()
    for i in range(7):
        usage_date = today - timedelta(days=i)
        kwh_used = round(random.uniform(5, 20), 2)  # Random kWh usage
        usage = Usage(usage_date=usage_date.date(), room_id=1, kwh_used=kwh_used)  # room_id as needed
        db.session.add(usage)
    db.session.commit()
    print("Fake usage data created for a week!")

if __name__ == '__main__':
    create_fake_usage_data()
