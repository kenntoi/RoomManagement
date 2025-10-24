# views.py
import random
from flask import Blueprint, render_template
from .models import Usage, User
from . import db  # Import db here
from datetime import datetime, date, time, timedelta
import random


views = Blueprint('views', __name__)

@views.route('/')
def home():
    return render_template("login.html")

@views.route('/add_user')
def add_user():
    try:
        new_user = User(username='admin', role='user', password='admin123', name='admin')
        db.session.add(new_user)
        db.session.commit()
        return "New user added successfully!"
    except Exception as e:
        return f"Failed to add user: {e}"

@views.route('/test_db')
def test_db():
    try:
        users = User.query.all()
        return f"Connected to the database! Found {len(users)} users."
    except Exception as e:
        return f"Database connection failed: {e}"

@views.route('/insert_fake_minute_usage_data')
def insert_fake_hourly_usage_data():
    try:
        usage_date = datetime(2024, 11, 13).date()  # Set the date to November 13, 2024
        for hour in range(24):  
            usage_datetime = datetime.combine(usage_date, datetime.min.time()) + timedelta(hours=hour)
            usage_time = usage_datetime.time()
            kwh_used = random.uniform(0.5, 1.5)  # Generate random kWh between 0.5 and 1.5 for hourly intervals
            new_usage = Usage(usage_date=usage_date, usage_time=usage_time, room_id=1, kwh_used=kwh_used)
            db.session.add(new_usage)
        db.session.commit()
        return "Hourly fake usage data for November 13, 2024, inserted successfully!"
    except Exception as e:
        db.session.rollback()
        return f"Failed to insert fake usage data: {e}"
