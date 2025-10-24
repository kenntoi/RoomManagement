# views.py
import random
from flask import Blueprint, render_template
from .models import Usage, User
from . import db  # Import db here
from datetime import datetime, date, time, timedelta


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

@views.route('/insert_fake_usage_data')
def insert_fake_usage_data():
    try:
        for i in range(31):  # Create data for the past 31 days
            usage_date = (datetime.now() - timedelta(days=i)).date()
            kwh_used = random.uniform(5, 20)  # Generate random kWh between 5 and 20
            
            # Create a new Usage instance
            new_usage = Usage(usage_date=usage_date, room_id=1, kwh_used=kwh_used)
            
            # Add to the session
            db.session.add(new_usage)
        
        db.session.commit()  # Commit the changes
        return "Fake usage data inserted successfully!"
    except Exception as e:
        return f"Failed to insert fake usage data: {e}"
